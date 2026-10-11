#!/usr/bin/env python3
"""Stay-An0n: small self-hosted anonymous imageboard using only Python's standard library."""
import os, json, sqlite3, secrets, hashlib, mimetypes, re, time, threading, shutil, ipaddress, io, zipfile
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, unquote
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('STAYANON_DATA', ROOT / 'data'))
UPLOADS = Path(os.getenv('STAYANON_UPLOADS', ROOT / 'uploads'))
STATIC = ROOT / 'static'
DATA.mkdir(parents=True, exist_ok=True); UPLOADS.mkdir(parents=True, exist_ok=True)
DB = DATA / 'stayanon.sqlite3'
PORT = int(os.getenv('PORT', '8080'))
HOST = os.getenv('HOST', '0.0.0.0')
MAX_UPLOAD = int(os.getenv('MAX_UPLOAD_BYTES', str(8 * 1024 * 1024)))
ADMIN_FILE = DATA / 'admin-token.txt'
if not ADMIN_FILE.exists():
    ADMIN_FILE.write_text(secrets.token_urlsafe(24) + '\n', encoding='utf8')
    try: ADMIN_FILE.chmod(0o600)
    except OSError: pass
ADMIN_TOKEN = os.getenv('STAYANON_ADMIN_TOKEN') or ADMIN_FILE.read_text(encoding='utf8').strip()

DEFAULT_SETTINGS = {'max_threads': 150, 'max_posts': 1200, 'grace_hours': 24, 'min_posts': 30,
                    'prune_batch': 5, 'max_text_chars': 8000, 'rate_seconds': 3, 'bump_limit': 300}
BOARDS = [('g','General','General discussion'), ('tech','Technology','Computers, software, and technology'),
          ('a','Anime','Anime and manga'), ('photo','Photography','Photography and equipment')]

SCHEMA = '''
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS boards (id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
 max_threads INTEGER NOT NULL DEFAULT 150, max_posts INTEGER NOT NULL DEFAULT 1200, grace_hours INTEGER NOT NULL DEFAULT 24,
 min_posts INTEGER NOT NULL DEFAULT 30, bump_limit INTEGER NOT NULL DEFAULT 300, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS threads (id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, board_id TEXT NOT NULL REFERENCES boards(id),
 subject TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, last_activity INTEGER NOT NULL, locked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, public_id TEXT NOT NULL UNIQUE, board_id TEXT NOT NULL REFERENCES boards(id),
 thread_id INTEGER NOT NULL REFERENCES threads(id) ON DELETE CASCADE, is_op INTEGER NOT NULL DEFAULT 0, name TEXT NOT NULL DEFAULT 'Anonymous',
 subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, interaction_score INTEGER NOT NULL DEFAULT 0, ip_address TEXT,
 attachment_id INTEGER, deleted_marker INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS idx_threads_board_activity ON threads(board_id,last_activity DESC);
CREATE INDEX IF NOT EXISTS idx_posts_thread ON posts(thread_id,created_at);
CREATE INDEX IF NOT EXISTS idx_posts_board_time ON posts(board_id,created_at);
CREATE TABLE IF NOT EXISTS attachments (id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER UNIQUE REFERENCES posts(id) ON DELETE CASCADE,
 filename TEXT NOT NULL, original_name TEXT NOT NULL, mime TEXT NOT NULL, size INTEGER NOT NULL, width INTEGER, height INTEGER, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS post_refs (source_post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
 target_public_id TEXT NOT NULL, target_post_id INTEGER REFERENCES posts(id) ON DELETE SET NULL, PRIMARY KEY(source_post_id,target_public_id));
CREATE INDEX IF NOT EXISTS idx_refs_target ON post_refs(target_post_id);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL, board_id TEXT, count INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL, details TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS rate_limits (key TEXT PRIMARY KEY, last_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS ip_bans (ip TEXT PRIMARY KEY, reason TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, expires_at INTEGER, created_by TEXT NOT NULL DEFAULT 'admin');
CREATE INDEX IF NOT EXISTS idx_bans_expiry ON ip_bans(expires_at);
'''
def now(): return int(time.time())
def db():
    c = sqlite3.connect(DB, timeout=10); c.row_factory = sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=10000'); return c

def init_db():
    c=db(); c.executescript(SCHEMA)
    # Forward-compatible migration for databases created before IP moderation existed.
    post_columns={row['name'] for row in c.execute('PRAGMA table_info(posts)')}
    if 'ip_address' not in post_columns: c.execute('ALTER TABLE posts ADD COLUMN ip_address TEXT')
    for key,val in DEFAULT_SETTINGS.items(): c.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(key,str(val)))
    for bid,title,desc in BOARDS:
        c.execute('INSERT OR IGNORE INTO boards(id,title,description,created_at) VALUES(?,?,?,?)',(bid,title,desc,now()))
    c.commit(); c.close()

def public_id(): return secrets.token_hex(5).upper()
def safe_board(bid):
    if not re.fullmatch(r'[a-z0-9_-]{1,24}', bid or ''): raise ValueError('Invalid board identifier')
    return bid

def settings(c): return {r['key']:r['value'] for r in c.execute('SELECT key,value FROM settings')}
def board_row(c,bid):
    r=c.execute('SELECT * FROM boards WHERE id=? AND enabled=1',(bid,)).fetchone()
    if not r: raise LookupError('Board not found or disabled')
    return r

def detect_image(data):
    if data.startswith(b'\xff\xd8\xff'): return 'image/jpeg','.jpg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'): return 'image/png','.png'
    if data.startswith((b'GIF87a',b'GIF89a')): return 'image/gif','.gif'
    if len(data)>=12 and data[:4]==b'RIFF' and data[8:12]==b'WEBP': return 'image/webp','.webp'
    return None,None

def prune_board(c,bid):
    """Prune a configurable batch of low-interaction posts without orphaning replies.

    If an OP is selected, its complete thread is removed. Eligible replies can be
    removed individually. A minimum-retention floor is enforced before each deletion.
    """
    b=c.execute('SELECT * FROM boards WHERE id=?',(bid,)).fetchone()
    if not b: return 0
    s=settings(c)
    post_count=c.execute('SELECT COUNT(*) n FROM posts WHERE board_id=?',(bid,)).fetchone()['n']
    thread_count=c.execute('SELECT COUNT(*) n FROM threads WHERE board_id=?',(bid,)).fetchone()['n']
    if post_count<=b['max_posts'] and thread_count<=b['max_threads']: return 0
    floor=max(int(b['min_posts']), int(s.get('min_posts',30)))
    eligible_before=now()-int(b['grace_hours'])*3600
    removed=0; considered=set()
    batch_size=max(1,min(50,int(s.get('prune_batch',5))))
    for _ in range(batch_size):
        post_count=c.execute('SELECT COUNT(*) n FROM posts WHERE board_id=?',(bid,)).fetchone()['n']
        thread_count=c.execute('SELECT COUNT(*) n FROM threads WHERE board_id=?',(bid,)).fetchone()['n']
        if post_count<=int(b['max_posts']) and thread_count<=int(b['max_threads']): break
        if post_count<=floor: break
        candidates=c.execute("""SELECT p.id,p.public_id,p.thread_id,p.is_op,p.created_at,
          p.interaction_score + (SELECT COUNT(*) FROM post_refs r WHERE r.target_post_id=p.id) score
          FROM posts p JOIN threads t ON t.id=p.thread_id
          WHERE p.board_id=? AND p.created_at<? ORDER BY score ASC,p.created_at ASC,p.id ASC""",(bid,eligible_before)).fetchall()
        victim=next((x for x in candidates if x['id'] not in considered),None)
        if victim is None: break
        considered.add(victim['id'])
        if victim['is_op']:
            count=c.execute('SELECT COUNT(*) FROM posts WHERE thread_id=?',(victim['thread_id'],)).fetchone()[0]
            if post_count-count<floor: continue
            files=[r['filename'] for r in c.execute('SELECT a.filename FROM attachments a JOIN posts p ON p.id=a.post_id WHERE p.thread_id=?',(victim['thread_id'],))]
            c.execute('DELETE FROM threads WHERE id=?',(victim['thread_id'],))
            removed+=count
        else:
            files=[r['filename'] for r in c.execute('SELECT filename FROM attachments WHERE post_id=?',(victim['id'],))]
            c.execute('DELETE FROM posts WHERE id=?',(victim['id'],))
            removed+=1
        for fn in files:
            try: (UPLOADS/fn).unlink(missing_ok=True)
            except OSError: pass
    if removed:
        c.execute('INSERT INTO audit_log(event,board_id,count,created_at,details) VALUES(?,?,?,?,?)',('prune',bid,removed,now(),json.dumps({'policy':'lowest interaction score; up to five candidates; whole-thread deletion when OP selected'})))
    return removed

def run_pruner():
    while True:
        try:
            c=db(); c.execute('BEGIN IMMEDIATE')
            for r in c.execute('SELECT id FROM boards WHERE enabled=1').fetchall(): prune_board(c,r['id'])
            c.commit(); c.close()
        except Exception as e: print('Pruner error:',e,flush=True)
        time.sleep(300)

class Handler(BaseHTTPRequestHandler):
    server_version='Stay-An0n/1.0'
    def log_message(self, fmt,*args): print('%s - %s'%(self.client_address[0],fmt%args),flush=True)
    def send_json(self,code,obj):
        raw=json.dumps(obj,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(raw)
    def body(self):
        try:
            n = int(self.headers.get('Content-Length', '0'))
        except (TypeError, ValueError):
            raise ValueError('Invalid Content-Length header')
        # Allow enough space for multipart boundaries and field headers, while
        # still rejecting oversized requests before reading them into memory.
        if n < 0 or n > MAX_UPLOAD + 256 * 1024:
            raise ValueError('Request too large; image uploads are limited to 8 MiB')
        raw = self.rfile.read(n)
        if len(raw) != n:
            raise ValueError('Incomplete request body; please try again')
        ct = self.headers.get('Content-Type', '')
        if 'application/json' in ct:
            return json.loads(raw or b'{}'), None
        if 'multipart/form-data' in ct:
            # The stdlib email parser handles quoted boundaries and binary
            # payloads without treating image bytes as text.
            from email.parser import BytesParser
            from email.policy import default
            envelope = ('Content-Type: ' + ct + '\r\nMIME-Version: 1.0\r\n\r\n').encode('ascii', 'replace')
            msg = BytesParser(policy=default).parsebytes(envelope + raw)
            if not msg.is_multipart():
                raise ValueError('Could not read the upload form. Refresh the page and try again.')
            fields, upload = {}, None
            for part in msg.iter_parts():
                disposition = part.get_content_disposition()
                if disposition != 'form-data':
                    continue
                name = part.get_param('name', header='content-disposition')
                filename = part.get_filename()
                payload = part.get_payload(decode=True) or b''
                if name == 'image' and filename:
                    if upload is not None:
                        raise ValueError('Please attach only one image per post')
                    upload = (str(filename), payload)
                elif name:
                    fields[str(name)] = payload.decode('utf-8', 'replace')
            return fields, upload
        return {}, None
    def admin(self):
        supplied=self.headers.get('X-Admin-Token','')
        return bool(supplied) and secrets.compare_digest(supplied,ADMIN_TOKEN)
    def send_file(self, path, content_type):
        data=path.read_bytes(); self.send_response(200); self.send_header('Content-Type',content_type); self.send_header('Content-Length',str(len(data))); self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)
    def audit(self, c, event, board_id=None, count=1, details=None):
        c.execute('INSERT INTO audit_log(event,board_id,count,created_at,details) VALUES(?,?,?,?,?)',
                  (event,board_id,count,now(),json.dumps(details or {},ensure_ascii=False)))
    def do_GET(self):
        u=urlparse(self.path); path=u.path; q=parse_qs(u.query)
        try:
            if path=='/api/health': return self.send_json(200,{'ok':True,'app':'Stay-An0n'})
            if path=='/api/boards':
                c=db(); rows=c.execute('SELECT b.*, (SELECT COUNT(*) FROM threads t WHERE t.board_id=b.id) thread_count FROM boards b WHERE enabled=1 ORDER BY id').fetchall(); c.close()
                return self.send_json(200,{'boards':[dict(r) for r in rows]})
            m=re.fullmatch(r'/api/boards/([^/]+)/catalog',path)
            if m:
                bid=safe_board(m[1]); c=db(); board=board_row(c,bid); page=max(1,int(q.get('page',['1'])[0])); limit=min(100,max(1,int(q.get('limit',['30'])[0])))
                ts=c.execute('SELECT t.*,p.public_id op_public_id,p.body op_body,p.name op_name,p.subject op_subject,p.created_at op_created_at,a.filename,a.mime,a.original_name,a.size FROM threads t JOIN posts p ON p.thread_id=t.id AND p.is_op=1 LEFT JOIN attachments a ON a.post_id=p.id WHERE t.board_id=? ORDER BY t.last_activity DESC LIMIT ? OFFSET ?',(bid,limit,(page-1)*limit)).fetchall()
                out=[]
                for t in ts:
                    d=dict(t); d['reply_count']=c.execute('SELECT COUNT(*) FROM posts WHERE thread_id=? AND is_op=0',(t['id'],)).fetchone()[0]; d['replies']=[dict(x) for x in c.execute('SELECT p.public_id,p.body,p.created_at,p.name,a.filename,a.mime FROM posts p LEFT JOIN attachments a ON a.post_id=p.id WHERE p.thread_id=? AND p.is_op=0 ORDER BY p.created_at DESC LIMIT 3',(t['id'],))][::-1]; out.append(d)
                total=c.execute('SELECT COUNT(*) FROM threads WHERE board_id=?',(bid,)).fetchone()[0]; c.close(); return self.send_json(200,{'board':dict(board),'threads':out,'page':page,'pages':max(1,(total+limit-1)//limit)})
            m=re.fullmatch(r'/api/threads/([A-F0-9]+)/?',path)
            if m:
                c=db(); t=c.execute('SELECT * FROM threads WHERE public_id=?',(m[1],)).fetchone()
                if not t: c.close(); return self.send_json(404,{'error':'Thread not found'})
                posts=[]
                for p in c.execute('SELECT p.*,a.filename,a.mime,a.original_name,a.size FROM posts p LEFT JOIN attachments a ON a.post_id=p.id WHERE p.thread_id=? ORDER BY p.created_at,p.id',(t['id'],)):
                    d=dict(p); d['references']=[dict(r) for r in c.execute('SELECT target_public_id,target_post_id FROM post_refs WHERE source_post_id=?',(p['id'],))]; d['backlinks']=[r['public_id'] for r in c.execute('SELECT p.public_id FROM post_refs r JOIN posts p ON p.id=r.source_post_id WHERE r.target_post_id=?',(p['id'],))]; posts.append(d)
                c.close(); return self.send_json(200,{'thread':dict(t),'posts':posts})
            m=re.fullmatch(r'/uploads/([a-f0-9]{32}\.(?:jpg|png|gif|webp))',path)
            if m:
                f=UPLOADS/m[1]
                if not f.is_file(): return self.send_json(404,{'error':'File not found'})
                data=f.read_bytes(); mime,_=detect_image(data); self.send_response(200); self.send_header('Content-Type',mime); self.send_header('Content-Length',str(len(data))); self.send_header('X-Content-Type-Options','nosniff'); self.send_header('Cache-Control','public, max-age=86400'); self.end_headers(); self.wfile.write(data); return
            if path in ('/admin','/admin/','/admin.html'):
                return self.send_file(STATIC/'admin.html','text/html; charset=utf-8')
            if path=='/admin.css': return self.send_file(STATIC/'admin.css','text/css; charset=utf-8')
            if path=='/admin.js': return self.send_file(STATIC/'admin.js','text/javascript; charset=utf-8')
            if path=='/api/admin/status':
                if not self.admin(): return self.send_json(401,{'error':'Admin token required'})
                c=db(); out={'settings':settings(c),
                    'boards':[dict(x) for x in c.execute('SELECT b.*, (SELECT COUNT(*) FROM threads t WHERE t.board_id=b.id) thread_count, (SELECT COUNT(*) FROM posts p WHERE p.board_id=b.id) post_count FROM boards b ORDER BY id')],
                    'audit':[dict(x) for x in c.execute('SELECT * FROM audit_log ORDER BY id DESC LIMIT 100')],
                    'stats':{'boards':c.execute('SELECT COUNT(*) FROM boards WHERE enabled=1').fetchone()[0],
                        'all_boards':c.execute('SELECT COUNT(*) FROM boards').fetchone()[0],
                        'threads':c.execute('SELECT COUNT(*) FROM threads').fetchone()[0],
                        'posts':c.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
                        'images':c.execute('SELECT COUNT(*) FROM attachments').fetchone()[0],
                        'bans':c.execute('SELECT COUNT(*) FROM ip_bans WHERE expires_at IS NULL OR expires_at>?',(now(),)).fetchone()[0]},
                    'effective_settings':{k:v for k,v in settings(c).items() if k in ('min_posts','max_text_chars','rate_seconds','prune_batch')}}; c.close(); return self.send_json(200,out)
            if path=='/api/admin/posts':
                if not self.admin(): return self.send_json(401,{'error':'Admin token required'})
                query=(q.get('q',[''])[0] or '').strip()[:160]; bid=(q.get('board',[''])[0] or '').strip()
                limit=min(100,max(1,int(q.get('limit',['50'])[0]))); offset=max(0,int(q.get('offset',['0'])[0]))
                sql='''SELECT p.id,p.public_id,p.board_id,p.thread_id,p.is_op,p.name,p.subject,p.body,p.created_at,p.ip_address,
                    a.filename,a.mime,a.original_name,t.public_id thread_public_id,t.locked,b.title board_title
                    FROM posts p JOIN threads t ON t.id=p.thread_id JOIN boards b ON b.id=p.board_id
                    LEFT JOIN attachments a ON a.post_id=p.id WHERE 1=1'''
                args=[]
                if bid: sql+=' AND p.board_id=?'; args.append(bid)
                if query:
                    like='%'+query+'%'; sql+=' AND (p.public_id LIKE ? OR p.name LIKE ? OR p.subject LIKE ? OR p.body LIKE ?)'; args.extend([like]*4)
                sql+=' ORDER BY p.created_at DESC,p.id DESC LIMIT ? OFFSET ?'; args.extend([limit,offset])
                c=db(); rows=[dict(r) for r in c.execute(sql,args)]; c.close(); return self.send_json(200,{'posts':rows,'limit':limit,'offset':offset})
            if path=='/api/admin/bans':
                if not self.admin(): return self.send_json(401,{'error':'Admin token required'})
                c=db(); rows=[dict(r) for r in c.execute('SELECT ip,reason,created_at,expires_at,created_by FROM ip_bans ORDER BY created_at DESC')]; c.close(); return self.send_json(200,{'bans':rows,'now':now()})
            if path=='/api/admin/backup':
                if not self.admin(): return self.send_json(401,{'error':'Admin token required'})
                c=db(); self.audit(c,'backup-download',None,1,{'contents':'database and uploads'}); c.commit()
                snapshot=sqlite3.connect(':memory:'); c.backup(snapshot); db_bytes=snapshot.serialize(); snapshot.close(); c.close()
                stream=io.BytesIO()
                with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as zf:
                    zf.writestr('stayanon.sqlite3',db_bytes)
                    if UPLOADS.exists():
                        for f in UPLOADS.iterdir():
                            if f.is_file() and re.fullmatch(r'[a-f0-9]{32}\.(?:jpg|png|gif|webp)',f.name): zf.write(f,'uploads/'+f.name)
                payload=stream.getvalue(); self.send_response(200); self.send_header('Content-Type','application/zip'); self.send_header('Content-Disposition','attachment; filename=stay-an0n-backup.zip'); self.send_header('Content-Length',str(len(payload))); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(payload); return
            if path=='/' or path=='/index.html':
                data=(STATIC/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            if path=='/style.css' or path=='/app.js':
                f=STATIC/path.lstrip('/'); data=f.read_bytes(); self.send_response(200); self.send_header('Content-Type','text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            return self.send_json(404,{'error':'Not found'})
        except LookupError as e: return self.send_json(404,{'error':str(e)})
        except (ValueError,KeyError,json.JSONDecodeError) as e: return self.send_json(400,{'error':str(e)})
        except Exception as e: print('GET error:',repr(e),flush=True); return self.send_json(500,{'error':'Internal server error'})
    def do_POST(self):
        path=urlparse(self.path).path
        stored_upload = None
        try:
            fields,upload=self.body(); c=db(); ip=self.client_address[0]
            if path=='/api/admin/boards':
                if not self.admin(): c.close(); return self.send_json(401,{'error':'Admin token required'})
                bid=safe_board(str(fields.get('id',''))); title=str(fields.get('title','')).strip()[:80]; desc=str(fields.get('description','')).strip()[:300]
                if not title: c.close(); return self.send_json(400,{'error':'Title required'})
                c.execute('INSERT INTO boards(id,title,description,created_at) VALUES(?,?,?,?)',(bid,title,desc,now()))
                self.audit(c,'board-create',bid,1,{'title':title}); c.commit(); c.close(); return self.send_json(201,{'ok':True})
            if path=='/api/admin/bans':
                if not self.admin(): c.close(); return self.send_json(401,{'error':'Admin token required'})
                try: ban_ip=str(ipaddress.ip_address(str(fields.get('ip','')).strip()))
                except ValueError: c.close(); return self.send_json(400,{'error':'Enter a valid IPv4 or IPv6 address'})
                reason=str(fields.get('reason','')).strip()[:500]
                hours=fields.get('hours','0')
                try: hours=int(hours or 0)
                except (ValueError,TypeError): c.close(); return self.send_json(400,{'error':'Ban duration is invalid'})
                if hours not in (0,1,24,168,720): c.close(); return self.send_json(400,{'error':'Choose permanent, 1 hour, 24 hours, 7 days, or 30 days'})
                expires=now()+hours*3600 if hours else None
                c.execute('INSERT INTO ip_bans(ip,reason,created_at,expires_at,created_by) VALUES(?,?,?,?,?) ON CONFLICT(ip) DO UPDATE SET reason=excluded.reason,created_at=excluded.created_at,expires_at=excluded.expires_at,created_by=excluded.created_by',(ban_ip,reason,now(),expires,'admin'))
                self.audit(c,'ip-ban',None,1,{'ip':ban_ip,'reason':reason,'expires_at':expires}); c.commit(); c.close(); return self.send_json(200,{'ok':True,'ip':ban_ip,'expires_at':expires})
            if path=='/api/admin/bans/remove':
                if not self.admin(): c.close(); return self.send_json(401,{'error':'Admin token required'})
                try: ban_ip=str(ipaddress.ip_address(str(fields.get('ip','')).strip()))
                except ValueError: c.close(); return self.send_json(400,{'error':'Enter a valid IPv4 or IPv6 address'})
                cur=c.execute('DELETE FROM ip_bans WHERE ip=?',(ban_ip,))
                self.audit(c,'ip-unban',None,cur.rowcount,{'ip':ban_ip}); c.commit(); c.close(); return self.send_json(200,{'ok':True})
            if path=='/api/admin/prune':
                if not self.admin(): c.close(); return self.send_json(401,{'error':'Admin token required'})
                c.execute('BEGIN IMMEDIATE'); n=sum(prune_board(c,r['id']) for r in c.execute('SELECT id FROM boards').fetchall()); c.commit(); c.close(); return self.send_json(200,{'removed':n})
            m=re.fullmatch(r'/api/boards/([^/]+)/threads',path); reply=re.fullmatch(r'/api/threads/([A-F0-9]+)/replies',path)
            if not m and not reply: c.close(); return self.send_json(404,{'error':'Not found'})
            banned=c.execute('SELECT reason,expires_at FROM ip_bans WHERE ip=? AND (expires_at IS NULL OR expires_at>?)',(ip,now())).fetchone()
            if banned:
                c.close(); message='Posting from this IP address is currently blocked.'
                if banned['reason']: message+=' Reason: '+banned['reason']
                if banned['expires_at']: message+=' Ban expires at '+datetime.fromtimestamp(banned['expires_at'],timezone.utc).isoformat()+'.'
                return self.send_json(403,{'error':message})
            bid=safe_board(m[1]) if m else None
            if reply:
                t=c.execute('SELECT * FROM threads WHERE public_id=?',(reply[1],)).fetchone()
                if not t: c.close(); return self.send_json(404,{'error':'Thread not found'})
                bid=t['board_id']; thread_id=t['id']
                if t['locked']: c.close(); return self.send_json(403,{'error':'Thread is locked'})
            b=board_row(c,bid); rl=c.execute('SELECT last_at FROM rate_limits WHERE key=?',(ip,)).fetchone()
            if rl and now()-rl['last_at']<int(settings(c).get('rate_seconds',3)): c.close(); return self.send_json(429,{'error':'Please wait a few seconds before posting again'})
            body=(fields.get('body','') or '').strip(); subject=(fields.get('subject','') or '').strip()[:120]
            if len(body)>int(settings(c).get('max_text_chars',8000)): c.close(); return self.send_json(400,{'error':'Post text is too long'})
            if not body and not upload: c.close(); return self.send_json(400,{'error':'A post needs text or an image'})
            if upload and len(upload[1])>MAX_UPLOAD: c.close(); return self.send_json(413,{'error':'Image exceeds upload size limit'})
            mime=ext=None; filename=None
            if upload:
                mime,ext=detect_image(upload[1])
                if not mime: c.close(); return self.send_json(415,{'error':'Only valid JPEG, PNG, GIF, and WebP images are accepted'})
                filename=secrets.token_hex(16)+ext
            c.execute('BEGIN IMMEDIATE')
            if m:
                thread_pub=public_id(); cur=c.execute('INSERT INTO threads(public_id,board_id,subject,created_at,last_activity) VALUES(?,?,?,?,?)',(thread_pub,bid,subject,now(),now())); thread_id=cur.lastrowid; is_op=1
            else: is_op=0
            post_pub=public_id(); cur=c.execute('INSERT INTO posts(public_id,board_id,thread_id,is_op,subject,body,created_at,ip_address) VALUES(?,?,?,?,?,?,?,?)',(post_pub,bid,thread_id,is_op,subject,body,now(),ip)); post_id=cur.lastrowid
            if upload:
                # Write to a temporary file and atomically rename it into place.
                # Keep uploads and the DB row consistent if a later step fails.
                temp_file = UPLOADS / (filename + '.tmp')
                final_file = UPLOADS / filename
                temp_file.write_bytes(upload[1])
                os.replace(temp_file, final_file)
                stored_upload = final_file
                c.execute('INSERT INTO attachments(post_id,filename,original_name,mime,size,created_at) VALUES(?,?,?,?,?,?)',(post_id,filename,os.path.basename(upload[0])[:160],mime,len(upload[1]),now()))
            # Resolve public post references after inserting, and keep unresolved references only as plain text.
            refs=set(re.findall(r'>>(?:NO)?([A-F0-9]{10})',body.upper()))
            for refid in refs:
                target=c.execute('SELECT id FROM posts WHERE public_id=? AND board_id=?',(refid,bid)).fetchone()
                c.execute('INSERT OR IGNORE INTO post_refs(source_post_id,target_public_id,target_post_id) VALUES(?,?,?)',(post_id,refid,target['id'] if target else None))
                if target: c.execute('UPDATE posts SET interaction_score=interaction_score+1 WHERE id=?',(target['id'],))
            if not is_op:
                reply_count=c.execute('SELECT COUNT(*) FROM posts WHERE thread_id=? AND is_op=0',(thread_id,)).fetchone()[0]
                # Threads stop being bumped once they exceed their board's configured reply limit.
                if int(b['bump_limit'])<=0 or reply_count>int(b['bump_limit']):
                    pass
                else:
                    c.execute('UPDATE threads SET last_activity=? WHERE id=?',(now(),thread_id))
            c.execute('INSERT INTO rate_limits(key,last_at) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET last_at=excluded.last_at',(ip,now()))
            prune_board(c,bid); c.commit(); c.close()
            stored_upload = None  # Database and file are now committed together.
            return self.send_json(201,{'ok':True,'post_id':post_pub,'thread_id':thread_pub if m else t['public_id']})
        except sqlite3.IntegrityError as e:
            try: c.rollback(); c.close()
            except Exception: pass
            if stored_upload:
                try: stored_upload.unlink(missing_ok=True)
                except OSError: pass
            return self.send_json(409,{'error':'That board or identifier already exists'})
        except LookupError as e:
            try: c.close()
            except Exception: pass
            if stored_upload:
                try: stored_upload.unlink(missing_ok=True)
                except OSError: pass
            return self.send_json(404,{'error':str(e)})
        except (ValueError,KeyError,json.JSONDecodeError) as e:
            try: c.close()
            except Exception: pass
            if stored_upload:
                try: stored_upload.unlink(missing_ok=True)
                except OSError: pass
            return self.send_json(400,{'error':str(e)})
        except Exception as e:
            print('POST error:',repr(e),flush=True)
            try: c.rollback(); c.close()
            except Exception: pass
            if stored_upload:
                try: stored_upload.unlink(missing_ok=True)
                except OSError: pass
            return self.send_json(500,{'error':'Internal server error'})
    def do_PATCH(self):
        if not self.admin(): return self.send_json(401,{'error':'Admin token required'})
        try:
            fields,_=self.body(); path=urlparse(self.path).path; c=db()
            m=re.fullmatch(r'/api/admin/boards/([^/]+)',path)
            if m:
                bid=safe_board(m[1]); existing=c.execute('SELECT * FROM boards WHERE id=?',(bid,)).fetchone()
                if not existing: c.close(); return self.send_json(404,{'error':'Board not found'})
                allowed_text={'title','description'}; allowed_int={'enabled','max_threads','max_posts','grace_hours','min_posts','bump_limit'}; vals={}
                for k,v in fields.items():
                    if k in allowed_text:
                        text_value=str(v).strip()
                        if k=='title':
                            if not text_value: c.close(); return self.send_json(400,{'error':'Board title cannot be empty'})
                            vals[k]=text_value[:80]
                        else: vals[k]=text_value[:300]
                    elif k in allowed_int:
                        try: num=int(v)
                        except (ValueError,TypeError): c.close(); return self.send_json(400,{'error':f'{k} must be an integer'})
                        limits={'enabled':(0,1),'max_threads':(1,10000),'max_posts':(1,100000),'grace_hours':(0,8760),'min_posts':(0,100000),'bump_limit':(0,100000)}
                        lo,hi=limits[k]
                        if not lo<=num<=hi: c.close(); return self.send_json(400,{'error':f'{k} must be between {lo} and {hi}'})
                        vals[k]=num
                if not vals: c.close(); return self.send_json(400,{'error':'No editable fields supplied'})
                sql=', '.join(k+'=?' for k in vals); c.execute('UPDATE boards SET '+sql+' WHERE id=?',list(vals.values())+[bid])
                self.audit(c,'board-update',bid,1,vals); c.commit(); c.close(); return self.send_json(200,{'ok':True})
            m=re.fullmatch(r'/api/admin/threads/([A-F0-9]{10})',path)
            if m:
                locked=fields.get('locked')
                if locked in (True,1,'1','true','True'): locked_value=1
                elif locked in (False,0,'0','false','False'): locked_value=0
                else: c.close(); return self.send_json(400,{'error':'locked must be true or false'})
                t=c.execute('SELECT public_id,board_id,locked FROM threads WHERE public_id=?',(m[1],)).fetchone()
                if not t: c.close(); return self.send_json(404,{'error':'Thread not found'})
                c.execute('UPDATE threads SET locked=? WHERE public_id=?',(locked_value,m[1]))
                self.audit(c,'thread-lock' if locked_value else 'thread-unlock',t['board_id'],1,{'thread_id':m[1]})
                c.commit(); c.close(); return self.send_json(200,{'ok':True,'locked':bool(locked_value)})
            if path=='/api/admin/settings':
                limits={'min_posts':(0,100000),'max_text_chars':(1,50000),'rate_seconds':(0,3600),'prune_batch':(1,50)}
                updated={}
                for k,v in fields.items():
                    if k not in limits: continue
                    try: num=int(v)
                    except (ValueError,TypeError): c.close(); return self.send_json(400,{'error':f'{k} must be an integer'})
                    lo,hi=limits[k]
                    if not lo<=num<=hi: c.close(); return self.send_json(400,{'error':f'{k} must be between {lo} and {hi}'})
                    updated[k]=num
                if not updated: c.close(); return self.send_json(400,{'error':'No supported settings supplied'})
                for k,v in updated.items(): c.execute('INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(k,str(v)))
                self.audit(c,'settings-update',None,len(updated),updated); c.commit(); c.close(); return self.send_json(200,{'ok':True})
            c.close(); return self.send_json(404,{'error':'Not found'})
        except (ValueError,KeyError,json.JSONDecodeError) as e:
            try: c.close()
            except Exception: pass
            return self.send_json(400,{'error':str(e)})
        except Exception as e:
            try: c.close()
            except Exception: pass
            print('PATCH error:',repr(e),flush=True); return self.send_json(500,{'error':'Internal server error'})
    def do_DELETE(self):
        if not self.admin(): return self.send_json(401,{'error':'Admin token required'})
        path=urlparse(self.path).path
        board_match=re.fullmatch(r'/api/admin/boards/([^/]+)',path)
        post_match=re.fullmatch(r'/api/admin/posts/([A-F0-9]{10})',path)
        if not board_match and not post_match: return self.send_json(404,{'error':'Not found'})
        c=db()
        if board_match:
            try: bid=safe_board(board_match[1])
            except ValueError as e: c.close(); return self.send_json(400,{'error':str(e)})
            b=c.execute('SELECT id,title FROM boards WHERE id=?',(bid,)).fetchone()
            if not b: c.close(); return self.send_json(404,{'error':'Board not found'})
            files=[r['filename'] for r in c.execute('SELECT a.filename FROM attachments a JOIN posts p ON p.id=a.post_id WHERE p.board_id=?',(bid,))]
            thread_count=c.execute('SELECT COUNT(*) FROM threads WHERE board_id=?',(bid,)).fetchone()[0]
            post_count=c.execute('SELECT COUNT(*) FROM posts WHERE board_id=?',(bid,)).fetchone()[0]
            c.execute('DELETE FROM threads WHERE board_id=?',(bid,))
            c.execute('DELETE FROM boards WHERE id=?',(bid,))
            self.audit(c,'board-delete',bid,1,{'title':b['title'],'threads_removed':thread_count,'posts_removed':post_count})
            c.commit(); c.close()
            for fn in files:
                try: (UPLOADS/fn).unlink(missing_ok=True)
                except OSError: pass
            return self.send_json(200,{'ok':True,'threads_removed':thread_count,'posts_removed':post_count})
        p=c.execute('SELECT * FROM posts WHERE public_id=?',(post_match[1],)).fetchone()
        if not p: c.close(); return self.send_json(404,{'error':'Post not found'})
        if p['is_op']:
            files=[r['filename'] for r in c.execute('SELECT a.filename FROM attachments a JOIN posts p ON p.id=a.post_id WHERE p.thread_id=?',(p['thread_id'],))]
            removed_count=c.execute('SELECT COUNT(*) FROM posts WHERE thread_id=?',(p['thread_id'],)).fetchone()[0]
            c.execute('DELETE FROM threads WHERE id=?',(p['thread_id'],))
            event='thread-delete'
        else:
            files=[r['filename'] for r in c.execute('SELECT filename FROM attachments WHERE post_id=?',(p['id'],))]
            removed_count=1; c.execute('DELETE FROM posts WHERE id=?',(p['id'],)); event='reply-delete'
        self.audit(c,event,p['board_id'],removed_count,{'post_id':p['public_id']})
        c.commit(); c.close()
        for fn in files:
            try: (UPLOADS/fn).unlink(missing_ok=True)
            except OSError: pass
        return self.send_json(200,{'ok':True,'removed_posts':removed_count})

def main():
    init_db(); threading.Thread(target=run_pruner,daemon=True).start()
    print('Stay-An0n is starting.',flush=True); print(f'Local URL: http://localhost:{PORT}',flush=True); print(f'Admin token file: {ADMIN_FILE}',flush=True)
    try:
        import socket
        print('LAN addresses: '+', '.join('http://%s:%d'%(x,PORT) for x in sorted({ai[4][0] for ai in socket.getaddrinfo(socket.gethostname(),None) if ai[0]==socket.AF_INET and not ai[4][0].startswith('127.')})),flush=True)
    except Exception: pass
    ThreadingHTTPServer((HOST,PORT),Handler).serve_forever()
if __name__=='__main__': main()
