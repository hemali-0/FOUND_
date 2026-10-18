const $ = s => document.querySelector(s);
let me = null, reportKind = 'lost', kindFilter = '', photoData = null, camStream = null;

/* ---------- helpers ---------- */
async function api(url, method = 'GET', body) {
  const r = await fetch(url, {
    method, credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || 'Something went wrong');
  return d;
}
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
function toast(msg, bad) {
  const t = $('#toast');
  t.textContent = msg; t.className = 'show' + (bad ? ' bad' : '');
  setTimeout(() => (t.className = ''), 2600);
}
const run = fn => async (...a) => { try { await fn(...a); } catch (e) { toast(e.message, true); } };

/* ---------- views ---------- */
function show(view) {
  document.querySelectorAll('.view').forEach(v => (v.hidden = true));
  const id = view === 'lost' || view === 'found' ? 'report' : view;
  $('#v-' + id).hidden = false;
  document.querySelectorAll('nav button[data-view]').forEach(b => b.classList.toggle('active', b.dataset.view === view));
  if (view === 'dash') loadDash();
  if (view === 'lost' || view === 'found') openReport(view);
  if (view === 'track') loadTrack();
  if (view === 'admin') loadAdmin();
}

function setUser(u) {
  me = u;
  $('#nav').hidden = !u;
  $('#nav-admin').hidden = !(u && u.is_admin);
  show(u ? 'dash' : 'auth');
}

/* ---------- auth ---------- */
const creds = () => ({ username: $('#a-user').value, password: $('#a-pass').value });
$('#btn-login').onclick = run(async () => setUser(await api('/api/login', 'POST', creds())));
$('#btn-signup').onclick = run(async () => setUser(await api('/api/signup', 'POST', creds())));
$('#btn-guest').onclick = run(async () => setUser(await api('/api/guest', 'POST')));
$('#logout').onclick = run(async () => { await api('/api/logout', 'POST'); setUser(null); });
document.querySelectorAll('nav button[data-view]').forEach(b => (b.onclick = () => show(b.dataset.view)));

/* ---------- item card ---------- */
function card(i, mode = 'browse') {
  const acts = [];
  if (mode === 'browse' && i.kind === 'found' && i.status === 'open' && !i.mine)
    acts.push(`<button class="primary" data-claim="${i.id}" data-q="${esc(i.vq)}">Claim</button>`);
  if (mode !== 'browse' && i.status !== 'returned')
    acts.push(`<button data-resolve="${i.id}">&#10003; Mark as resolved</button>`);
  if (mode === 'track' && i.kind === 'lost') acts.push(`<button data-match="${i.id}">Find matches</button>`);
  if (mode !== 'browse') acts.push(`<button class="danger" data-del="${i.id}">Delete</button>`);
  const contact = i.contact
    ? `<div class="contact">Contact: ${esc(i.contact)}</div>`
    : (i.kind === 'found' ? '<div class="locked">&#128274; Contact shown after claim is verified</div>' : '');
  const pic = i.image ? `<img class="pic" src="${esc(i.image)}" alt="${esc(i.name)}" loading="lazy">` : '';
  return `<div class="item ${i.kind}" id="item-${i.id}">
    ${pic}<div class="top"><span class="tag ${i.kind}">${i.kind}</span><span class="status">${i.status === 'returned' ? 'resolved' : i.status}</span></div>
    <h3>${esc(i.name)}</h3>
    <p class="desc">${esc(i.description || i.category || '')}</p>
    <div class="meta">
      <div class="loc">&#128655; ${esc(i.stop)}${i.spot ? ' &middot; ' + esc(i.spot) : ''}</div>
      ${contact}
      <div class="date">${esc(i.date || '')}</div>
    </div>
    <div class="actions">${acts.join('')}</div>
    <div class="matches"></div>
  </div>`;
}

/* ---------- dashboard ---------- */
async function loadDash() {
  const p = new URLSearchParams({ q: $('#f-q').value, kind: kindFilter, status: $('#f-status').value });
  const [items, s] = await Promise.all([api('/api/items?' + p), api('/api/summary')]);
  $('#stats').innerHTML = [['Open lost', s.open_lost], ['Open found', s.open_found], ['Resolved', s.resolved]]
    .map(([l, v]) => `<div class="stat"><b>${v}</b>${l}</div>`).join('');
  $('#list').innerHTML = items.map(i => card(i)).join('') || '<p class="muted">Nothing here yet.</p>';
}
let debounce;   // wait until typing pauses, so we don't hit the server on every key
$('#f-q').oninput = () => { clearTimeout(debounce); debounce = setTimeout(run(loadDash), 300); };
$('#f-status').onchange = run(loadDash);
document.querySelectorAll('.pill').forEach(p => (p.onclick = run(async () => {
  kindFilter = p.dataset.kind;
  document.querySelectorAll('.pill').forEach(x => x.classList.toggle('active', x === p));
  await loadDash();
})));

/* ---------- photo: upload (lost / admin) or live camera (found / admin) ---------- */
function setPhoto(data) {
  photoData = data;
  $('#r-prev').src = data || '';
  $('#photo-prev').hidden = !data;
}
// shrink to max 800px JPEG so photos stay small and fast
function shrink(src) {
  const sw = src.videoWidth || src.naturalWidth, sh = src.videoHeight || src.naturalHeight;
  if (!sw || !sh) throw new Error('Could not read the image');
  const k = Math.min(1, 800 / Math.max(sw, sh));
  const c = document.createElement('canvas');
  c.width = Math.round(sw * k); c.height = Math.round(sh * k);
  c.getContext('2d').drawImage(src, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.75);
}
$('#btn-upload').onclick = () => $('#r-file').click();
$('#r-file').onchange = e => {
  const f = e.target.files[0];
  if (!f) return;
  const img = new Image();
  img.onload = () => { try { setPhoto(shrink(img)); } catch (er) { toast(er.message, true); } URL.revokeObjectURL(img.src); };
  img.onerror = () => toast('Could not read that image', true);
  img.src = URL.createObjectURL(f);
  e.target.value = '';
};
$('#btn-cam').onclick = run(async () => {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('Camera not available here (needs https or localhost).');
  try { camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } }); }
  catch { throw new Error('Camera blocked or not found. Allow camera access and try again.'); }
  $('#cam-v').srcObject = camStream;
  $('#cam').hidden = false;
});
function closeCam() {
  if (camStream) camStream.getTracks().forEach(t => t.stop());
  camStream = null;
  $('#cam').hidden = true;
}
$('#cam-snap').onclick = run(async () => { setPhoto(shrink($('#cam-v'))); closeCam(); });
$('#cam-close').onclick = closeCam;
$('#photo-rm').onclick = () => setPhoto(null);

/* ---------- report ---------- */
function openReport(kind) {
  reportKind = kind;
  setPhoto(null);
  const adm = me && me.is_admin;               // admin can use both options
  $('#btn-upload').hidden = !(kind === 'lost' || adm);
  $('#btn-cam').hidden = !(kind === 'found' || adm);
  $('#photo-hint').textContent = kind === 'lost'
    ? 'Upload a photo of the item you lost'
    : 'Take a live photo of the item you found';
  $('#r-title').textContent = kind === 'lost' ? 'Report a lost item' : 'Report a found item';
  $('#r-verify').hidden = kind === 'lost';
  $('#r-date').value = new Date().toISOString().slice(0, 10);
}
$('#r-submit').onclick = run(async () => {
  await api('/api/items', 'POST', {
    kind: reportKind, name: $('#r-name').value, category: $('#r-category').value,
    stop: $('#r-stop').value, spot: $('#r-spot').value, date: $('#r-date').value,
    description: $('#r-desc').value, contact: $('#r-contact').value,
    vq: $('#r-vq').value, va: $('#r-va').value, image: photoData
  });
  ['name', 'stop', 'spot', 'desc', 'contact', 'vq', 'va'].forEach(k => ($('#r-' + k).value = ''));
  setPhoto(null);
  toast('Report posted!');
  show('dash');
});

/* ---------- track ---------- */
async function loadTrack() {
  const items = await api('/api/mine');
  $('#my-list').innerHTML = items.map(i => card(i, 'track')).join('') || '<p class="muted">You have no reports yet.</p>';
}

/* ---------- admin ---------- */
async function loadAdmin() {
  const s = await api('/api/admin/stats');
  $('#admin-stats').innerHTML = Object.entries(s)
    .map(([k, v]) => `<div class="stat"><b>${v}</b>${k}</div>`).join('');
  const items = await api('/api/items?limit=200');
  $('#admin-list').innerHTML = items.map(i => card(i, 'admin')).join('');
}

/* ---------- card actions (event delegation) ---------- */
document.addEventListener('click', run(async e => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.dataset.claim) {
    const ans = prompt('Verification question:\n' + b.dataset.q);
    if (ans === null) return;
    const r = await api(`/api/items/${b.dataset.claim}/claim`, 'POST', { answer: ans });
    toast('Verified! Contact: ' + r.contact);
    loadDash();
  }
  if (b.dataset.del && confirm('Delete this report?')) {
    await api(`/api/items/${b.dataset.del}`, 'DELETE');
    show(document.querySelector('nav .active')?.dataset.view || 'dash');
  }
  if (b.dataset.resolve) {
    await api(`/api/items/${b.dataset.resolve}/status`, 'PATCH', { status: 'returned' });
    toast('Marked as resolved');
    show(document.querySelector('nav .active')?.dataset.view || 'dash');
  }
  if (b.dataset.match) {
    const m = await api(`/api/items/${b.dataset.match}/matches`);
    $(`#item-${b.dataset.match} .matches`).innerHTML = m.length
      ? 'Possible matches: ' + m.map(x => `${esc(x.name)} @ ${esc(x.stop)}`).join(', ')
      : 'No matches yet.';
  }
}));
/* ---------- boot ---------- */
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
api('/api/me').then(setUser).catch(() => setUser(null));