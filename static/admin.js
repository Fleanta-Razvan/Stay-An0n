'use strict';
const $ = (selector, root=document) => root.querySelector(selector);
const $$ = (selector, root=document) => [...root.querySelectorAll(selector)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
const date = stamp => stamp ? new Date(Number(stamp) * 1000).toLocaleString() : '—';
let token = '';
let dashboardData = null;
let moderationOffset = 0;
const moderationLimit = 40;
let moderationHasMore = false;

try { token = sessionStorage.getItem('stay-an0n-admin-token') || ''; } catch { /* Private browsing may disable session storage. */ }
function notice(message='', kind='') {
  const el = $('#admin-notice');
  el.textContent = message;
  el.className = kind;
}
function storeToken(value) {
  token = value;
  try { if (token) sessionStorage.setItem('stay-an0n-admin-token', token); else sessionStorage.removeItem('stay-an0n-admin-token'); } catch {}
}
function lockConsole(showMessage=false) {
  storeToken('');
  dashboardData = null;
  $('#dashboard').hidden = true;
  $('#login-panel').hidden = false;
  $('#logout').hidden = true;
  $('#admin-password').value = '';
  if (showMessage) notice('Admin session was rejected. Enter the current password/token again.', 'error');
  else notice();
}
async function request(url, options={}, authToken=token) {
  const headers = new Headers(options.headers || {});
  if (authToken) headers.set('X-Admin-Token', authToken);
  if (options.body && typeof options.body === 'string' && !headers.has('Content-Type')) headers.set('Content-Type','application/json');
  const response = await fetch(url, {...options, headers});
  let data = {};
  try { data = await response.json(); } catch { /* Binary responses use a separate path. */ }
  if (!response.ok) {
    if (response.status === 401 && token) lockConsole(true);
    throw new Error(data.error || `Request failed (${response.status})`);
  }
  return data;
}
const sendJSON = (url, method, data={}) => request(url, {method, body:JSON.stringify(data)});
function showTab(name) {
  $$('.admin-tabs [data-tab]').forEach(button => {
    const active = button.dataset.tab === name;
    button.classList.toggle('active', active);
    button.setAttribute('aria-selected', String(active));
  });
  $$('.admin-panel[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== name; });
  if (name === 'moderation') loadModeration().catch(error => notice(error.message, 'error'));
  if (name === 'bans') loadBans().catch(error => notice(error.message, 'error'));
  if (name === 'audit') renderAudit(dashboardData?.audit || []);
}
function renderStats(stats={}) {
  const values = [
    ['Active boards',stats.boards ?? 0],['Threads',stats.threads ?? 0],['Posts',stats.posts ?? 0],
    ['Images',stats.images ?? 0],['Active IP bans',stats.bans ?? 0]
  ];
  $('#stats').innerHTML = values.map(([label,value]) => `<div class="stat-card"><div class="stat-label">${esc(label)}</div><div class="stat-value">${Number(value).toLocaleString()}</div></div>`).join('');
}
function renderOverview(data) {
  const boards = data.boards || [];
  $('#overview-boards').innerHTML = boards.length ? boards.map(board => `<div class="compact-row"><strong>/${esc(board.id)}/ — ${esc(board.title)}</strong><span class="minor ${board.enabled ? 'state-on' : 'state-off'}">${board.enabled ? 'ENABLED' : 'DISABLED'}<br>${Number(board.thread_count || 0)} threads</span></div>`).join('') : '<div class="empty-state">No boards configured.</div>';
  const audit = (data.audit || []).slice(0,8);
  $('#overview-audit').innerHTML = audit.length ? audit.map(item => `<div class="compact-row"><strong>${esc(item.event)}</strong><span class="minor">${esc(date(item.created_at))}</span></div>`).join('') : '<div class="empty-state">No administrative events yet.</div>';
}
function renderBoards(boards) {
  const root = $('#board-management');
  if (!boards.length) { root.innerHTML = '<div class="empty-state">There are no boards. Create one above.</div>'; return; }
  root.innerHTML = boards.map(b => `<form class="board-admin-card" data-board-id="${esc(b.id)}">
    <div class="board-admin-head"><div><code>/${esc(b.id)}/</code> <strong>${esc(b.title)}</strong></div><span class="status ${b.enabled ? 'state-on' : 'state-off'}">${b.enabled ? 'ENABLED' : 'DISABLED'}</span></div>
    <div class="board-admin-fields">
      <label class="wide">Display name <input data-field="title" required maxlength="80" value="${esc(b.title)}"></label>
      <label class="wide">Description <input data-field="description" maxlength="300" value="${esc(b.description)}"></label>
      <label>Max threads <input data-field="max_threads" type="number" min="1" max="10000" value="${Number(b.max_threads)}" required></label>
      <label>Max posts <input data-field="max_posts" type="number" min="1" max="100000" value="${Number(b.max_posts)}" required></label>
      <label>Retention grace (hours) <input data-field="grace_hours" type="number" min="0" max="8760" value="${Number(b.grace_hours)}" required></label>
      <label>Minimum posts retained <input data-field="min_posts" type="number" min="0" max="100000" value="${Number(b.min_posts)}" required></label>
      <label>Bump limit <input data-field="bump_limit" type="number" min="0" max="100000" value="${Number(b.bump_limit)}" required></label>
      <label>Board visibility <select data-field="enabled"><option value="1" ${b.enabled ? 'selected' : ''}>Enabled</option><option value="0" ${!b.enabled ? 'selected' : ''}>Disabled</option></select></label>
    </div>
    <div class="compact-row"><span class="minor">${Number(b.thread_count || 0)} threads · ${Number(b.post_count || 0)} posts</span><span class="minor">ID is stable; title can be renamed</span></div>
    <div class="board-admin-actions"><button type="submit" class="primary-button">SAVE BOARD</button><button type="button" class="danger-button board-delete">DELETE BOARD + CONTENT</button></div>
  </form>`).join('');
  $$('.board-admin-card').forEach(form => {
    form.addEventListener('submit', async event => {
      event.preventDefault();
      const id = form.dataset.boardId;
      const data = {};
      $$('[data-field]', form).forEach(input => data[input.dataset.field] = input.type === 'number' || input.tagName === 'SELECT' ? Number(input.value) : input.value.trim());
      try {
        await sendJSON(`/api/admin/boards/${encodeURIComponent(id)}`, 'PATCH', data);
        notice(`Board /${id}/ saved.`, 'success'); await loadDashboard();
      } catch (error) { notice(error.message, 'error'); }
    });
    $('.board-delete',form).addEventListener('click', async () => {
      const id = form.dataset.boardId;
      const b = (dashboardData?.boards || []).find(row => row.id === id);
      const threads = Number(b?.thread_count || 0), posts = Number(b?.post_count || 0);
      if (!confirm(`Permanently delete /${id}/ and all its ${threads} thread(s), ${posts} post(s), and attached images? This cannot be undone. Download a backup first if needed.`)) return;
      try {
        const result = await request(`/api/admin/boards/${encodeURIComponent(id)}`, {method:'DELETE'});
        notice(`Board /${id}/ removed (${result.threads_removed} threads, ${result.posts_removed} posts).`, 'success'); await loadDashboard();
      } catch (error) { notice(error.message, 'error'); }
    });
  });
}
function renderAudit(entries) {
  const root = $('#audit-list');
  if (!entries.length) { root.innerHTML = '<div class="empty-state">No admin activity has been recorded yet.</div>'; return; }
  root.innerHTML = entries.map(item => {
    let details = item.details || '{}';
    try { details = JSON.stringify(JSON.parse(details), null, 2); } catch {}
    const board = item.board_id ? ` · /${esc(item.board_id)}/` : '';
    return `<article class="audit-entry"><div class="audit-head"><span class="audit-event">${esc(item.event)}${board}</span><span>${esc(date(item.created_at))} · ${Number(item.count || 0)} item(s)</span></div><div class="audit-details">${esc(details)}</div></article>`;
  }).join('');
}
async function loadDashboard() {
  const data = await request('/api/admin/status');
  dashboardData = data;
  $('#login-panel').hidden = true; $('#dashboard').hidden = false; $('#logout').hidden = false;
  renderStats(data.stats); renderOverview(data); renderBoards(data.boards || []); renderAudit(data.audit || []);
  const boardFilter = $('#moderation-board');
  const previous = boardFilter.value;
  boardFilter.innerHTML = '<option value="">All boards</option>' + (data.boards || []).map(board => `<option value="${esc(board.id)}">/${esc(board.id)}/ — ${esc(board.title)}</option>`).join('');
  if ([...boardFilter.options].some(option => option.value === previous)) boardFilter.value = previous;
  const settings = data.settings || {};
  for (const name of ['min_posts','max_text_chars','rate_seconds','prune_batch']) {
    const input = $(`#settings-form [name="${name}"]`);
    if (input && settings[name] !== undefined) input.value = Number(settings[name]);
  }
}
async function loadBans() {
  const data = await request('/api/admin/bans');
  const root = $('#ban-list');
  const rows = data.bans || [];
  if (!rows.length) { root.innerHTML = '<div class="empty-state">No IP ban records.</div>'; return; }
  root.innerHTML = rows.map(b => {
    const active = b.expires_at === null || Number(b.expires_at) > Number(data.now);
    const expiryLabel = b.expires_at ? (active ? `Expires ${date(b.expires_at)}` : `Expired ${date(b.expires_at)}`) : 'Permanent';
    return `<div class="ban-row"><div class="ban-row-main"><code>${esc(b.ip)}</code> <span class="${active ? 'state-off' : 'minor'}">${active ? 'ACTIVE' : 'EXPIRED'}</span><div class="ban-details">${esc(b.reason || 'No reason supplied')} · ${esc(expiryLabel)} · Added ${esc(date(b.created_at))}</div></div><button class="secondary-danger" type="button" data-unban="${esc(b.ip)}">${active ? 'REMOVE BAN' : 'CLEAR ENTRY'}</button></div>`;
  }).join('');
  $$('[data-unban]',root).forEach(button => button.addEventListener('click', async () => {
    const ip = button.dataset.unban;
    if (!confirm(`Remove the IP ban for ${ip}?`)) return;
    try { await sendJSON('/api/admin/bans/remove','POST',{ip}); notice(`Ban removed for ${ip}.`,'success'); await loadBans(); await loadDashboard(); }
    catch (error) { notice(error.message,'error'); }
  }));
}
async function loadModeration() {
  const q = $('#moderation-search [name="q"]').value.trim();
  const board = $('#moderation-board').value;
  const params = new URLSearchParams({limit:String(moderationLimit),offset:String(moderationOffset)});
  if (q) params.set('q',q); if (board) params.set('board',board);
  $('#moderation-results').innerHTML = '<p class="loading-card">Loading posts…</p>';
  const data = await request(`/api/admin/posts?${params}`);
  moderationHasMore = data.posts.length === moderationLimit;
  $('#mod-newer').disabled = moderationOffset <= 0;
  $('#mod-older').disabled = !moderationHasMore;
  $('#moderation-count').textContent = `${data.posts.length ? moderationOffset + 1 : 0}–${moderationOffset + data.posts.length} displayed · newest first`;
  if (!data.posts.length) { $('#moderation-results').innerHTML = '<div class="empty-state">No matching posts.</div>'; return; }
  $('#moderation-results').innerHTML = data.posts.map(p => {
    const type = p.is_op ? 'THREAD OP' : 'REPLY';
    const body = (p.body || '').trim() || (p.filename ? '[Image-only post]' : '[Empty post]');
    const state = p.locked ? 'LOCKED' : 'OPEN';
    return `<article class="mod-post" data-post-id="${esc(p.public_id)}" data-thread-id="${esc(p.thread_public_id)}">
      <div class="mod-post-head"><span><strong>${type}</strong> · <a href="/#/thread/${encodeURIComponent(p.thread_public_id)}" target="_blank" rel="noopener">/${esc(p.board_id)}/ · ${esc(p.public_id)}</a></span><span class="${p.locked ? 'state-off' : 'state-on'}">THREAD ${state}</span></div>
      ${p.subject ? `<div class="mod-post-body"><strong>${esc(p.subject)}</strong><br>${esc(body.slice(0,1600))}${body.length>1600?'…':''}</div>` : `<div class="mod-post-body">${esc(body.slice(0,1600))}${body.length>1600?'…':''}</div>`}
      <div class="mod-meta"><span>Poster: <code>${esc(p.name || 'Anonymous')}</code></span><span>Created: ${esc(date(p.created_at))}</span><span>Thread: <code>${esc(p.thread_public_id)}</code></span><span>Client IP: <code>${esc(p.ip_address || 'not recorded on older post')}</code></span><span>Board: /${esc(p.board_id)}/ ${esc(p.board_title)}</span><span>${p.filename ? `Image: ${esc(p.original_name || p.filename)}` : 'No attachment'}</span></div>
      ${p.filename ? `<a class="mod-image" href="/uploads/${encodeURIComponent(p.filename)}" target="_blank" rel="noopener">[ OPEN ATTACHMENT ↗ ]</a>` : ''}
      <div class="moderation-actions"><button class="secondary-button thread-lock" type="button" data-locked="${p.locked ? '1':'0'}">${p.locked ? 'UNLOCK THREAD' : 'LOCK THREAD'}</button><button class="secondary-button ban-poster" type="button" ${p.ip_address ? '' : 'disabled'}>BAN THIS IP</button><button class="danger-button delete-post" type="button">${p.is_op ? 'DELETE ENTIRE THREAD' : 'DELETE REPLY'}</button></div>
    </article>`;
  }).join('');
  $$('.mod-post').forEach(card => {
    $('.thread-lock',card).addEventListener('click', async () => {
      const button = $('.thread-lock',card), id = card.dataset.threadId, locked = button.dataset.locked !== '1';
      try { await sendJSON(`/api/admin/threads/${encodeURIComponent(id)}`,'PATCH',{locked}); notice(`Thread ${id} ${locked ? 'locked' : 'unlocked'}.`,'success'); await loadDashboard(); await loadModeration(); }
      catch (error) { notice(error.message,'error'); }
    });
    $('.ban-poster',card).addEventListener('click', () => {
      const ip = $('.mod-meta code',card);
      const all = [...$$('.mod-meta span',card)];
      const ipLabel = all.find(span => span.textContent.startsWith('Client IP:'));
      const value = ipLabel?.querySelector('code')?.textContent || '';
      if (!value || value.startsWith('not recorded')) return;
      $('#ban-ip').value = value;
      showTab('bans');
      $('#ban-ip').focus();
      notice(`IP ${value} loaded into the ban form. Select a duration and apply it.`, 'success');
    });
    $('.delete-post',card).addEventListener('click', async () => {
      const id = card.dataset.postId;
      const op = $('.delete-post',card).textContent.includes('ENTIRE THREAD');
      if (!confirm(op ? `Delete thread ${card.dataset.threadId}, including every reply and image? This cannot be undone.` : `Delete reply ${id} and its attachment? This cannot be undone.`)) return;
      try {
        const result = await request(`/api/admin/posts/${encodeURIComponent(id)}`,{method:'DELETE'});
        notice(`Content removed (${result.removed_posts ?? 1} post(s)).`,'success'); await loadDashboard(); await loadModeration();
      } catch (error) { notice(error.message,'error'); }
    });
  });
}

$('#login-form').addEventListener('submit', async event => {
  event.preventDefault();
  const input = $('#admin-password'); const attempt = input.value;
  if (!attempt) return notice('Enter the admin password/token.', 'error');
  const button = $('button[type="submit"]',event.currentTarget); button.disabled = true; button.textContent = 'CHECKING…';
  try {
    await request('/api/admin/status',{},attempt);
    storeToken(attempt);
    input.value = '';
    notice('Access granted.','success');
    await loadDashboard();
  } catch (error) { notice(error.message === 'Admin token required' ? 'Incorrect admin password/token.' : error.message,'error'); }
  finally { button.disabled = false; button.textContent = 'UNLOCK CONSOLE ↗'; }
});
$('#logout').addEventListener('click', () => lockConsole(false));
$('#refresh-dashboard').addEventListener('click', async () => { try { await loadDashboard(); notice('Dashboard refreshed.','success'); } catch (error) { notice(error.message,'error'); } });
$$('.admin-tabs [data-tab]').forEach(button => button.addEventListener('click', () => showTab(button.dataset.tab)));
$('#create-board-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form).entries());
  data.id = String(data.id || '').trim(); data.title = String(data.title || '').trim(); data.description = String(data.description || '').trim();
  try { await sendJSON('/api/admin/boards','POST',data); form.reset(); notice(`Board /${data.id}/ created.`,'success'); await loadDashboard(); }
  catch (error) { notice(error.message,'error'); }
});
$('#moderation-search').addEventListener('submit', async event => { event.preventDefault(); moderationOffset=0; try { await loadModeration(); } catch (error) { notice(error.message,'error'); } });
$('#clear-search').addEventListener('click', () => { $('#moderation-search').reset(); moderationOffset=0; loadModeration().catch(error => notice(error.message,'error')); });
$('#mod-newer').addEventListener('click', () => { moderationOffset=Math.max(0,moderationOffset-moderationLimit); loadModeration().catch(error => notice(error.message,'error')); });
$('#mod-older').addEventListener('click', () => { if (moderationHasMore) { moderationOffset+=moderationLimit; loadModeration().catch(error => notice(error.message,'error')); } });
$('#ban-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const data = Object.fromEntries(new FormData(form).entries());
  try { await sendJSON('/api/admin/bans','POST',data); notice(`IP ban applied to ${data.ip}.`,'success'); form.reset(); await loadBans(); await loadDashboard(); }
  catch (error) { notice(error.message,'error'); }
});
$('#settings-form').addEventListener('submit', async event => {
  event.preventDefault(); const data = Object.fromEntries(new FormData(event.currentTarget).entries());
  for (const k of Object.keys(data)) data[k]=Number(data[k]);
  try { await sendJSON('/api/admin/settings','PATCH',data); notice('Posting settings saved.','success'); await loadDashboard(); }
  catch (error) { notice(error.message,'error'); }
});
$('#run-prune').addEventListener('click', async () => {
  if (!confirm('Run the current automatic-pruning policy now? Eligible old posts may be permanently removed.')) return;
  try { const data = await request('/api/admin/prune',{method:'POST'}); notice(`Pruner completed. ${data.removed} post(s) removed.`, 'success'); await loadDashboard(); }
  catch (error) { notice(error.message,'error'); }
});
$('#download-backup').addEventListener('click', async () => {
  const button = $('#download-backup'); button.disabled = true; button.textContent = 'BUILDING BACKUP…';
  try {
    const response = await fetch('/api/admin/backup',{headers:{'X-Admin-Token':token}});
    if (!response.ok) { let payload={}; try { payload=await response.json(); } catch {} throw new Error(payload.error || `Backup failed (${response.status})`); }
    const blob = await response.blob(); const url = URL.createObjectURL(blob); const link=document.createElement('a');
    link.href=url; link.download='stay-an0n-backup.zip'; document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
    notice('Backup download prepared. Keep the ZIP private because it contains posts and moderation data.','success');
  } catch (error) { notice(error.message,'error'); }
  finally { button.disabled=false; button.textContent='↓ DOWNLOAD BACKUP ZIP'; }
});

if (token) {
  loadDashboard().then(() => notice()).catch(() => lockConsole(false));
}
