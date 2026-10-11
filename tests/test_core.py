import importlib.util, tempfile, unittest, time, threading, json, urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

SPEC=importlib.util.spec_from_file_location('stayanon',Path(__file__).parents[1]/'server.py')
app=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(app)

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); root=Path(self.tmp.name)
        app.DB=root/'test.sqlite3'; app.DATA=root; app.UPLOADS=root/'uploads'; app.UPLOADS.mkdir()
        app.ADMIN_FILE=root/'admin-token.txt'; app.ADMIN_TOKEN='test-token'; app.init_db()
    def tearDown(self): self.tmp.cleanup()
    def test_seed_boards_and_settings(self):
        c=app.db(); self.assertEqual(c.execute('select count(*) from boards').fetchone()[0],4); self.assertIn('grace_hours',app.settings(c)); c.close()
    def test_image_signature_validation(self):
        self.assertEqual(app.detect_image(b'\xff\xd8\xfffake'),('image/jpeg','.jpg'))
        self.assertEqual(app.detect_image(b'GIF89a'),('image/gif','.gif'))
        self.assertEqual(app.detect_image(b'not an image'),(None,None))
    def test_multipart_image_upload_roundtrip(self):
        # Exercise the same multipart path used by the browser's FormData upload.
        boundary = '----StayAnonUploadBoundary7MA4YWxkTrZu0gW'
        image = b'\x89PNG\r\n\x1a\n' + b'fake-image-payload'
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="subject"\r\n\r\nUpload test\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="body"\r\n\r\nImage attached\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="sample.png"\r\n'
            'Content-Type: image/png\r\n\r\n'
        ).encode('ascii') + image + f'\r\n--{boundary}--\r\n'.encode('ascii')
        server = ThreadingHTTPServer(('127.0.0.1', 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = urllib.request.Request(
                f'http://127.0.0.1:{server.server_port}/api/boards/g/threads',
                data=body, method='POST', headers={
                    'Content-Type': f'multipart/form-data; boundary={boundary}',
                    'Content-Length': str(len(body)),
                })
            with urllib.request.urlopen(request, timeout=5) as response:
                created = json.loads(response.read())
                self.assertEqual(response.status, 201)
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/threads/{created["thread_id"]}', timeout=5) as response:
                thread_data = json.loads(response.read())
            filename = thread_data['posts'][0]['filename']
            self.assertTrue(filename.endswith('.png'))
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/uploads/{filename}', timeout=5) as response:
                self.assertEqual(response.read(), image)
            c = app.db()
            self.assertEqual(c.execute('SELECT COUNT(*) FROM attachments').fetchone()[0], 1)
            c.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_pruning_respects_grace_and_retention_floor(self):
        c=app.db(); c.execute("UPDATE boards SET max_threads=1,max_posts=100,min_posts=0,grace_hours=0 WHERE id='g'")
        # Keep the global floor low too. Create 2 old threads with OPs and one reply each.
        c.execute("UPDATE settings SET value='0' WHERE key='min_posts'")
        for i in range(2):
            pub=app.public_id(); cur=c.execute('INSERT INTO threads(public_id,board_id,subject,created_at,last_activity) VALUES(?,?,?,?,?)',(pub,'g','',app.now()-10000+i,app.now()-10000+i)); tid=cur.lastrowid
            c.execute('INSERT INTO posts(public_id,board_id,thread_id,is_op,body,created_at) VALUES(?,?,?,?,?,?)',(app.public_id(),'g',tid,1,'op',app.now()-10000+i))
        c.commit(); c.execute('BEGIN IMMEDIATE'); n=app.prune_board(c,'g'); c.commit()
        self.assertEqual(n,1); self.assertEqual(c.execute("SELECT count(*) FROM threads WHERE board_id='g'").fetchone()[0],1); c.close()
    def test_post_reference_resolution(self):
        c=app.db(); tid=c.execute("INSERT INTO threads(public_id,board_id,created_at,last_activity) VALUES('AAAAAAAAAA','g',1,1)").lastrowid
        pid=c.execute("INSERT INTO posts(public_id,board_id,thread_id,is_op,body,created_at) VALUES('BBBBBBBBBB','g',?,1,'hello',1)",(tid,)).lastrowid
        c.execute("INSERT INTO post_refs(source_post_id,target_public_id,target_post_id) VALUES(?,?,?)",(pid,'BBBBBBBBBB',pid)); c.commit()
        self.assertEqual(c.execute('SELECT COUNT(*) FROM post_refs WHERE target_post_id=?',(pid,)).fetchone()[0],1); c.close()

class AdminConsoleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); root=Path(self.tmp.name)
        app.DB=root/'admin-test.sqlite3'; app.DATA=root; app.UPLOADS=root/'uploads'; app.UPLOADS.mkdir()
        app.ADMIN_FILE=root/'admin-token.txt'; app.ADMIN_TOKEN='test-token'; app.init_db()
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True); self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.tmp.cleanup()
    def call(self, path, method='GET', payload=None, token='test-token', raw=False):
        data=None if payload is None else json.dumps(payload).encode()
        headers={}
        if token: headers['X-Admin-Token']=token
        if data is not None: headers['Content-Type']='application/json'
        req=urllib.request.Request(self.base+path,data=data,method=method,headers=headers)
        try:
            with urllib.request.urlopen(req,timeout=5) as response:
                content=response.read()
                return response.status, content if raw else (json.loads(content) if content else {})
        except urllib.error.HTTPError as exc:
            content=exc.read()
            try: response_data=json.loads(content)
            except Exception: response_data=content
            return exc.code, response_data
    def test_admin_console_route_and_status_are_protected(self):
        status, _=self.call('/api/admin/status',token='wrong-token')
        self.assertEqual(status,401)
        status, result=self.call('/api/admin/status')
        self.assertEqual(status,200)
        self.assertEqual(result['stats']['boards'],4)
        status, raw=self.call('/admin',token=None,raw=True)
        self.assertEqual(status,200)
        self.assertIn(b'Admin access',raw)
    def test_board_create_rename_and_delete_cleans_content(self):
        status,_=self.call('/api/admin/boards','POST',{'id':'tools','title':'Tools','description':'Local tools'})
        self.assertEqual(status,201)
        status,_=self.call('/api/admin/boards/tools','PATCH',{'title':'Software Tools'})
        self.assertEqual(status,200)
        status,created=self.call('/api/boards/tools/threads','POST',{'subject':'Test','body':'test body'},token=None)
        self.assertEqual(status,201)
        status,posts=self.call('/api/admin/posts')
        self.assertEqual(status,200)
        target=next(p for p in posts['posts'] if p['public_id']==created['post_id'])
        self.assertEqual(target['ip_address'],'127.0.0.1')
        self.assertEqual(target['board_title'],'Software Tools')
        status,deleted=self.call('/api/admin/boards/tools','DELETE')
        self.assertEqual(status,200)
        self.assertEqual(deleted['threads_removed'],1)
        c=app.db(); self.assertEqual(c.execute("SELECT COUNT(*) FROM posts WHERE board_id='tools'").fetchone()[0],0); c.close()
    def test_ip_ban_blocks_posting_and_unban_restores_it(self):
        status,result=self.call('/api/admin/bans','POST',{'ip':'127.0.0.1','hours':'24','reason':'test ban'})
        self.assertEqual(status,200)
        status,_=self.call('/api/boards/g/threads','POST',{'body':'should be blocked'},token=None)
        self.assertEqual(status,403)
        status,_=self.call('/api/admin/bans/remove','POST',{'ip':'127.0.0.1'})
        self.assertEqual(status,200)
        c=app.db(); c.execute("UPDATE settings SET value='0' WHERE key='rate_seconds'"); c.commit(); c.close()
        status,_=self.call('/api/boards/g/threads','POST',{'body':'allowed now'},token=None)
        self.assertEqual(status,201)
    def test_locked_thread_rejects_replies(self):
        c=app.db(); c.execute("UPDATE settings SET value='0' WHERE key='rate_seconds'"); c.commit(); c.close()
        status,created=self.call('/api/boards/g/threads','POST',{'body':'open a thread'},token=None)
        self.assertEqual(status,201)
        status,result=self.call(f"/api/admin/threads/{created['thread_id']}",'PATCH',{'locked':True})
        self.assertEqual(status,200); self.assertTrue(result['locked'])
        status,_=self.call(f"/api/threads/{created['thread_id']}/replies",'POST',{'body':'this should fail'},token=None)
        self.assertEqual(status,403)
    def test_backup_is_a_zip_with_database(self):
        status,content=self.call('/api/admin/backup',raw=True)
        self.assertEqual(status,200)
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            self.assertIn('stayanon.sqlite3',archive.namelist())
            self.assertTrue(archive.testzip() is None)


    def test_bump_limit_stops_thread_activity_updates(self):
        c=app.db(); c.execute("UPDATE settings SET value='0' WHERE key='rate_seconds'"); c.execute("UPDATE boards SET bump_limit=0 WHERE id='g'"); c.commit(); c.close()
        status,created=self.call('/api/boards/g/threads','POST',{'body':'thread should stop bumping'},token=None)
        self.assertEqual(status,201)
        c=app.db(); old=app.now()-600; c.execute('UPDATE threads SET last_activity=? WHERE public_id=?',(old,created['thread_id'])); c.commit(); c.close()
        status,_=self.call(f"/api/threads/{created['thread_id']}/replies",'POST',{'body':'a reply'},token=None)
        self.assertEqual(status,201)
        c=app.db(); saved=c.execute('SELECT last_activity FROM threads WHERE public_id=?',(created['thread_id'],)).fetchone()[0]; c.close()
        self.assertEqual(saved,old)


if __name__=='__main__': unittest.main()
