'use strict';
const $ = s => document.querySelector(s);
let currentBoard = null, currentThread = null, page = 1, pages = 1, refreshTimer = null, routeVersion = 0;
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;
const ALLOWED_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function notice(message = '', kind = '') { const el = $('#notice'); el.textContent = message; el.className = kind; }
async function api(url, opts = {}) {
  const response = await fetch(url, opts);
  let data = {};
  try { data = await response.json(); } catch { /* A non-JSON error is reported below. */ }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}
function time(ts) { return new Date(Number(ts) * 1000).toLocaleString(); }
function formatSize(bytes) { return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(0)} KB` : `${(bytes / (1024 * 1024)).toFixed(2)} MB`; }
function imageHTML(post, cls = 'thumb', showEmpty = false) {
  const filename = post?.filename || post?.attachment?.filename || '';
  // Filenames are generated server-side from hex characters plus a fixed image extension.
  const safeName = String(filename).match(/^[a-f0-9]{32}\.(?:jpg|png|gif|webp)$/i)?.[0];
  if (!safeName) {
    if (!showEmpty) return '';
    return `<div class="image-attachment catalog-image-attachment no-image"><div class="image-box image-box-empty"><div class="image-box-placeholder"><span class="image-placeholder-icon">▧</span><strong>NO IMAGE ATTACHED</strong></div></div></div>`;
  }
  const src = `/uploads/${safeName}`;
  return `<figure class="image-attachment ${cls === 'thumb' ? 'catalog-image-attachment' : ''}"><a class="image-open" href="${src}" target="_blank" rel="noopener noreferrer" aria-label="Open full-size image"><span class="image-box"><img class="${cls}" loading="lazy" src="${src}" alt="Uploaded image" onerror="this.hidden=true;this.closest('.image-box').classList.add('image-error')"><span class="image-box-placeholder"><span class="image-placeholder-icon">▧</span><strong>IMAGE COULD NOT LOAD</strong></span></span></a><figcaption><a href="${src}" target="_blank" rel="noopener noreferrer">[ OPEN ATTACHMENT: ${safeName} ]</a><span class="image-load-error">Upload file missing — check the server's uploads directory.</span></figcaption></figure>`;
}
function bodyHTML(value) {
  return esc(value).replace(/&gt;&gt;([A-F0-9]{10})/g, (_, id) => `<a href="#p-${id}" data-ref="${id}">&gt;&gt;${id}</a>`).replace(/\n/g, '<br>');
}
function postHTML(post) {
  return `<article class="post ${post.is_op ? 'op' : ''}" id="p-${esc(post.public_id)}"><div class="post-head"><span class="anon">${esc(post.name || 'Anonymous')}</span>${post.subject ? `<strong>${esc(post.subject)}</strong>` : ''}<span class="post-id">${time(post.created_at)} · No.${esc(post.public_id)}</span><a href="#p-${esc(post.public_id)}">[link]</a><a href="#" data-quote="${esc(post.public_id)}">[quote]</a></div>${imageHTML(post, 'attachment')}<div class="post-body">${bodyHTML(post.body)}</div>${post.backlinks?.length ? `<div class="reply-list">Referenced by: ${post.backlinks.map(id => `<a href="#p-${esc(id)}" data-ref="${esc(id)}">&gt;&gt;${esc(id)}</a>`).join(' ')}</div>` : ''}</article>`;
}
async function loadBoards() {
  const data = await api('/api/boards');
  $('#boards').innerHTML = data.boards.map(board => `<article class="board"><div class="board-top"><span class="board-code">/${esc(board.id)}/</span><span class="board-arrow">↗</span></div><a href="#/${encodeURIComponent(board.id)}">${esc(board.title)}</a><p>${esc(board.description)}</p><span class="board-count">${Number(board.thread_count) || 0} THREADS</span></article>`).join('') || '<div class="loading-card">No boards available. // check node configuration</div>';
}
async function openBoard(boardId, pageNumber = 1) {
  currentBoard = boardId; currentThread = null; page = pageNumber;
  show('board-view');
  const data = await api(`/api/boards/${encodeURIComponent(boardId)}/catalog?page=${page}`);
  $('#board-title').textContent = `/${data.board.id}/`;
  $('#catalog-heading').textContent = data.board.title;
  $('#board-description').textContent = data.board.description;
  pages = Math.max(1, Number(data.pages) || 1);
  $('#page-label').textContent = `PAGE ${page} OF ${pages}`;
  $('#prev-page').disabled = page <= 1; $('#next-page').disabled = page >= pages;
  $('#catalog-count').textContent = `${data.threads.length} THREAD${data.threads.length === 1 ? '' : 'S'}`;
  $('#catalog').innerHTML = data.threads.map(thread => `<article class="thread-card"><a class="thread-link" href="#/thread/${encodeURIComponent(thread.public_id)}">THREAD / No.${esc(thread.op_public_id)}</a><div class="meta">${time(thread.created_at)} · ${Number(thread.reply_count) || 0} replies</div>${imageHTML(thread, 'thumb', true)}${(thread.op_subject || thread.subject) ? `<div class="subject">${esc(thread.op_subject || thread.subject)}</div>` : ''}<div class="preview">${bodyHTML(thread.op_body || '')}</div>${thread.replies.map(reply => `<div class="reply-list"><a href="#/thread/${encodeURIComponent(thread.public_id)}#p-${esc(reply.public_id)}">No.${esc(reply.public_id)}</a>${reply.filename ? imageHTML(reply, 'thumb') : ''}<div class="reply-preview">${bodyHTML(String(reply.body || '').slice(0, 120))}</div></div>`).join('')}<a class="open-thread" href="#/thread/${encodeURIComponent(thread.public_id)}">Open discussion ↗</a></article>`).join('') || '<div class="loading-card">Nothing here yet. Start the first thread.</div>';
  $('#thread-form').hidden = false;
}
function show(id) { for (const section of ['boards-view', 'board-view', 'thread-view']) $('#' + section).hidden = section !== id; }
async function openThread(id) {
  currentThread = id; show('thread-view'); $('#thread-posts').innerHTML = '<div class="loading-card">Loading thread…</div>';
  const data = await api(`/api/threads/${encodeURIComponent(id)}`);
  currentBoard = data.thread.board_id;
  $('#thread-board-link').href = `#/${encodeURIComponent(currentBoard)}`;
  $('#thread-posts').innerHTML = data.posts.map(postHTML).join('') || '<div class="loading-card">This thread has no posts.</div>';
  $('#reply-form').hidden = !!data.thread.locked;
  if (location.hash.includes('#p-')) setTimeout(() => document.querySelector(location.hash)?.scrollIntoView({block:'center'}), 60);
}
async function route() {
  const version = ++routeVersion;
  clearInterval(refreshTimer); notice();
  const hash = location.hash || '#boards';
  try {
    if (hash.startsWith('#/thread/')) {
      const id = decodeURIComponent(hash.slice('#/thread/'.length).split('#')[0]);
      await openThread(id); if (version !== routeVersion) return;
      refreshTimer = setInterval(() => { if (currentThread === id) openThread(id).catch(err => notice(err.message, 'error')); }, 30000);
    } else if (hash.startsWith('#/')) {
      const boardId = decodeURIComponent(hash.slice(2).split('/')[0]); await openBoard(boardId);
    } else { currentBoard = null; currentThread = null; show('boards-view'); await loadBoards(); }
  } catch (error) { notice(error.message, 'error'); if (version === routeVersion) show(hash.startsWith('#/') && !hash.startsWith('#/thread/') ? 'board-view' : 'boards-view'); }
}
function validateImage(file) {
  if (!file || file.size === 0) return 'That file is empty. Choose a different image.';
  if (!ALLOWED_TYPES.has(file.type)) return 'Unsupported image type. Choose a JPEG, PNG, GIF, or WebP image.';
  if (file.size > MAX_IMAGE_BYTES) return `Image is ${formatSize(file.size)}. The limit is 8 MiB.`;
  return '';
}
function clearPreview(input, preview) {
  if (input) input.value = '';
  preview.hidden = true;
  preview.replaceChildren();
  const placeholder = input?.closest('.upload-field')?.querySelector('[data-upload-placeholder]');
  if (placeholder) placeholder.hidden = false;
}
function showFilePreview(input, file) {
  const field = input.closest('.upload-field');
  const preview = field.querySelector('[data-preview]');
  const placeholder = field.querySelector('[data-upload-placeholder]');
  // Clear only the previous preview here. Clearing input.value also clears the
  // selected file, which meant uploads silently submitted without an attachment.
  preview.hidden = true;
  preview.replaceChildren();
  if (placeholder) placeholder.hidden = false;
  if (!file) return;
  const error = validateImage(file);
  if (error) { input.value = ''; notice(error, 'error'); return; }
  notice();
  if (placeholder) placeholder.hidden = true;
  preview.hidden = false;
  const image = document.createElement('img');
  image.alt = 'Selected image preview';
  image.src = URL.createObjectURL(file);
  image.onerror = () => { image.alt = 'Preview unavailable'; image.classList.add('preview-error'); };
  image.onload = () => URL.revokeObjectURL(image.src);
  const meta = document.createElement('span'); meta.className = 'file-preview-meta';
  const name = document.createElement('span'); name.className = 'file-preview-name'; name.textContent = file.name;
  const size = document.createElement('span'); size.className = 'file-preview-size'; size.textContent = `${formatSize(file.size)} · ${file.type.replace('image/','').toUpperCase()}`;
  meta.append(name, size);
  const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'remove-file'; remove.textContent = 'Remove';
  remove.addEventListener('click', event => {
    event.preventDefault(); event.stopPropagation();
    clearPreview(input, preview); notice('Image removed.');
  });
  preview.append(image, meta, remove);
}
function setupUploads() {
  document.querySelectorAll('input[type="file"][name="image"]').forEach(input => {
    input.addEventListener('change', () => showFilePreview(input, input.files?.[0] || null));
    const zone = input.closest('[data-dropzone]');
    for (const eventName of ['dragenter', 'dragover']) zone.addEventListener(eventName, event => { event.preventDefault(); zone.classList.add('dragover'); });
    for (const eventName of ['dragleave', 'drop']) zone.addEventListener(eventName, event => { event.preventDefault(); zone.classList.remove('dragover'); });
    zone.addEventListener('drop', event => {
      const file = event.dataTransfer?.files?.[0]; if (!file) return;
      const transfer = new DataTransfer(); transfer.items.add(file); input.files = transfer.files; showFilePreview(input, file);
    });
  });
}
function formData(form) {
  const fd = new FormData(form); const file = fd.get('image');
  if (!(file instanceof File) || !file.size) fd.delete('image');
  else { const error = validateImage(file); if (error) throw new Error(error); }
  return fd;
}
async function submitPost(form, url, successMessage, after) {
  const submit = form.querySelector('button[type="submit"]');
  try {
    const data = await api(url, {method:'POST', body:formData(form)});
    form.reset(); form.querySelectorAll('[data-preview]').forEach(preview => { preview.hidden = true; preview.replaceChildren(); }); form.querySelectorAll('[data-upload-placeholder]').forEach(placeholder => { placeholder.hidden = false; });
    notice(successMessage, 'success'); if (after) await after(data);
  } catch (error) { notice(error.message, 'error'); }
  finally { if (submit) { submit.disabled = false; submit.innerHTML = submit.dataset.originalLabel || submit.innerHTML; } }
}
$('#thread-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!currentBoard) return notice('Choose a board before starting a thread.', 'error');
  const button = event.target.querySelector('button[type="submit"]'); button.dataset.originalLabel ||= button.innerHTML; button.disabled = true; button.textContent = 'Publishing…';
  await submitPost(event.target, `/api/boards/${encodeURIComponent(currentBoard)}/threads`, 'Thread published successfully.', data => { location.hash = `#/thread/${data.thread_id}`; });
});
$('#reply-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!currentThread) return notice('Open a thread before replying.', 'error');
  const button = event.target.querySelector('button[type="submit"]'); button.dataset.originalLabel ||= button.innerHTML; button.disabled = true; button.textContent = 'Posting…';
  await submitPost(event.target, `/api/threads/${encodeURIComponent(currentThread)}/replies`, 'Reply posted successfully.', () => openThread(currentThread));
});
$('#new-thread').addEventListener('click', () => { $('#thread-form').scrollIntoView({behavior:'smooth',block:'start'}); $('#thread-form').querySelector('textarea[name=body]').focus({preventScroll:true}); });
$('#cancel-thread').addEventListener('click', () => { $('#thread-form').reset(); $('#thread-form').querySelectorAll('[data-preview]').forEach(p => {p.hidden=true;p.replaceChildren();}); $('#thread-form').querySelectorAll('[data-upload-placeholder]').forEach(p => p.hidden = false); notice('Draft cleared.'); });
$('#prev-page').addEventListener('click', () => { if (page > 1) openBoard(currentBoard, page - 1).catch(e => notice(e.message, 'error')); });
$('#next-page').addEventListener('click', () => { if (page < pages) openBoard(currentBoard, page + 1).catch(e => notice(e.message, 'error')); });
$('#refresh-thread').addEventListener('click', () => currentThread && openThread(currentThread).catch(e => notice(e.message, 'error')));
function openImageViewer(src, filename = 'IMAGE ATTACHMENT') {
  const viewer = $('#image-viewer');
  const image = $('#image-viewer-image');
  const error = $('#image-viewer-error');
  $('#image-viewer-name').textContent = filename;
  $('#image-viewer-open-original').href = src;
  error.hidden = true;
  image.hidden = false;
  image.onload = () => { image.hidden = false; error.hidden = true; };
  image.onerror = () => { image.hidden = true; error.hidden = false; };
  image.src = src;
  viewer.hidden = false;
  document.body.classList.add('viewer-open');
  $('#image-viewer-close').focus();
}
function closeImageViewer() {
  const viewer = $('#image-viewer');
  if (!viewer || viewer.hidden) return;
  viewer.hidden = true;
  document.body.classList.remove('viewer-open');
  $('#image-viewer-image').removeAttribute('src');
}
$('#image-viewer-close').addEventListener('click', closeImageViewer);
$('#image-viewer').addEventListener('click', event => { if (event.target === $('#image-viewer')) closeImageViewer(); });
document.addEventListener('keydown', event => { if (event.key === 'Escape') closeImageViewer(); });
document.addEventListener('click', event => {
  // Handle attachments in the catalog, the thread starter (OP), and all replies.
  const imageLink = event.target.closest('.image-open');
  if (imageLink) {
    event.preventDefault();
    const image = imageLink.querySelector('img');
    const filename = imageLink.closest('.image-attachment')?.querySelector('figcaption a')?.textContent?.replace(/^\[ OPEN ATTACHMENT: | \]$/g, '') || 'IMAGE ATTACHMENT';
    openImageViewer(imageLink.href, filename);
    return;
  }
  const quote = event.target.closest('[data-quote]');
  if (quote) { event.preventDefault(); const box = $('#reply-form textarea'); box.value += (box.value ? '\n' : '') + `>>${quote.dataset.quote} `; box.focus(); box.scrollIntoView({behavior:'smooth',block:'center'}); }
  const ref = event.target.closest('[data-ref]');
  if (ref) { const target = document.querySelector(`#p-${CSS.escape(ref.dataset.ref)}`); if (target) { event.preventDefault(); target.scrollIntoView({behavior:'smooth',block:'center'}); target.classList.add('highlight'); setTimeout(() => target.classList.remove('highlight'), 1400); } }
});
$('#theme').addEventListener('click', () => { document.body.classList.toggle('light'); try { localStorage.setItem('stay-theme', document.body.classList.contains('light') ? 'light' : 'dark'); } catch {} });
try { if (localStorage.getItem('stay-theme') === 'light') document.body.classList.add('light'); } catch {}
setupUploads(); window.addEventListener('hashchange', route); route();
