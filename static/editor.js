// ── CSRF shim ─────────────────────────────────────────────────────────────
// security_patch.install_security() returns a per-session token in the
// X-AA-CSRF response header and verifies it when supplied on state-changing
// /api/* calls; Host and Origin/Referer protect browser access. Wrap window.fetch once so all existing call sites get it
// transparently. GETs are exempt — they only need the Origin check.
let AA_CSRF = null;
const _origFetch = window.fetch;
// Resolve failures as an explicit failed response: legacy inline handlers and
// fire-and-forget MIDI callers must not leak rejected request promises.
window.fetch = async (url, opts = {}) => {
  const failure = message => {
    toast(`Request failed (${url}): ${message}`, true);
    return {ok: false, status: 0, headers: new Headers(),
            json: async () => ({ok: false, error: message})};
  };
  try {
    const headers = new Headers(opts.headers || {});
    const method = (opts.method || 'GET').toUpperCase();
    if (AA_CSRF && typeof url === 'string' && /^\/api\//.test(url) && method !== 'GET')
      headers.set('X-AA-CSRF', AA_CSRF);
    const response = await _origFetch(url, {...opts, headers});
    const token = response.headers.get('X-AA-CSRF');
    if (token) AA_CSRF = token;
    // Read a clone so ignored MIDI responses still report server failures.
    let body;
    try { body = await response.clone().json(); }
    catch { return failure(`Invalid server response (HTTP ${response.status})`); }
    if (!response.ok || body?.ok === false) {
      const message = body?.error || `HTTP ${response.status}`;
      toast(`Request failed (${url}): ${message}`, true);
      return {ok: false, status: response.status, headers: response.headers,
              json: async () => ({...body, ok: false, error: message})};
    }
    // JSON is already decoded, including for callers that do not inspect it.
    return {ok: true, status: response.status, headers: response.headers,
            json: async () => body};
  } catch (error) { return failure(error.message || String(error)); }
};
// Prime the token with a GET (any /api/ route returns the header).
fetch('/api/dump/status').catch(()=>{});

// ── state ─────────────────────────────────────────────────────────────────
let socket = io();
let state = { presets: [], channel_names: [], switch_names: {pedals:[],switches:[]},
              pc_map: [], songs: [], sets: [], globals: null,
              global_config: null, selectedPreset: null, bank: 0, second: false };

const $ = id => document.getElementById(id);

function toast(msg, isErr) {
  const t = $('toast'); t.textContent = msg;
  t.style.background = isErr ? '#b22' : '#333';
  t.style.display = 'block';
  clearTimeout(toast._t);
  toast._t = setTimeout(()=>t.style.display='none', 2200);
}

// ── tab switching ─────────────────────────────────────────────────────────
// Only buttons with a data-tab attribute are section tabs; toolbar
// buttons (.tab-tool) live in the same nav band but have their own
// onclick handlers and shouldn't trigger a tab change.
document.querySelectorAll('nav.tabs button[data-tab]').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('nav.tabs button[data-tab]').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(s=>s.classList.remove('active'));
    $('tab-' + btn.dataset.tab).classList.add('active');
  });
});

// ── MIDI port dropdowns + connection LEDs ───────────────────────────────
// Lightweight replacement for the old prompt() based picker. Two
// <select>s in the header: pick a port, the change handler opens it
// on the server, the LED next to it goes green when something is
// actually selected. Dropdowns auto-refresh every 4s so hot-plugged
// USB MIDI interfaces appear without a page reload.
let _portsCache = {in: [], out: []};

async function refreshPortDropdowns() {
  const r = await fetch('/api/midi/ports').then(r=>r.json());
  if (r.error) return;
  _portsCache = r;
  fillSelect('midi-in-select',  r.in,  r.selected_in);
  fillSelect('midi-out-select', r.out, r.selected_out);
  refreshMidiStatus(r);
}
function fillSelect(id, ports, selected) {
  const el = document.getElementById(id);
  if (!el) return;
  // Don't redraw if user is mid-interaction with the menu open
  if (document.activeElement === el) return;
  // De-duplicate visible names but keep their original index for the
  // backend to disambiguate (e.g. two ports both called "SysEx Librarian").
  const seen = new Map();
  ports.forEach((p, i) => {
    const key = p + '\u0001' + i;  // unique value
    seen.set(key, p);
  });
  el.innerHTML = '<option value="">— select —</option>'
    + Array.from(seen.entries()).map(([k, label]) =>
        `<option value="${escapeHtml(label)}"${label===selected?' selected':''}>${escapeHtml(label)}</option>`
      ).join('');
}

async function onPortSelectChange() {
  const inP  = document.getElementById('midi-in-select').value;
  const outP = document.getElementById('midi-out-select').value;
  const res = await fetch('/api/midi/open', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({in_port: inP || null, out_port: outP || null}),
  }).then(r=>r.json());
  if (!res.ok) return;
  refreshMidiStatus(res.ports);
  toast(`MIDI ports: in=${inP || '—'}  out=${outP || '—'}`);
}

function refreshMidiStatus(p) {
  // Hidden compat targets
  const sIn  = document.getElementById('midi-in-status');
  const sOut = document.getElementById('midi-out-status');
  if (sIn)  sIn.textContent  = p.selected_in  || '—';
  if (sOut) sOut.textContent = p.selected_out || '—';
  // Connection LEDs
  document.getElementById('led-in') ?.classList.toggle('on', !!p.selected_in);
  document.getElementById('led-out')?.classList.toggle('on', !!p.selected_out);
}
// Keep dropdowns + LEDs fresh
setInterval(refreshPortDropdowns, 4000);

// ── dump load / send ──────────────────────────────────────────────────────
async function loadDumpDialog() {
  const inp = document.createElement('input');
  inp.type = 'file'; inp.accept = '.syx';
  inp.onchange = async e => {
    const file = e.target.files[0];
    if (!file) return;
    const buf = await file.arrayBuffer();
    const hex = [...new Uint8Array(buf)].map(b=>b.toString(16).padStart(2,'0')).join('');
    await sendDumpLoad({hex, filename:file.name});
  };
  inp.click();
}

async function sendDumpLoad(body) {
  const r = await fetch('/api/dump/load', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  }).then(r=>r.json());
  if (!r.ok) { toast('Load failed: ' + (r.error||'unknown'), true); return; }
  hydrateDump(r, {resetSelection: true});
  toast('Loaded ' + r.loaded_from);
}

function hydrateDump(r, {resetSelection = false, selectedPreset = state.selectedPreset} = {}) {
  const hasDump = r.has_dump !== false && Array.isArray(r.presets);
  Object.assign(state, {
    presets: hasDump ? r.presets : [],
    channel_names: hasDump ? (r.channel_names || []) : [],
    switch_names: hasDump ? (r.switch_names || {pedals: [], switches: []}) : {pedals: [], switches: []},
    pc_map: hasDump ? (r.pc_map || []) : [],
    songs: hasDump ? (r.songs || []) : [],
    sets: hasDump ? (r.sets || []) : [],
    globals: hasDump ? (r.globals || null) : null,
    write: r.write || {enabled: false, busy: false},
    dumpComplete: hasDump && r.complete !== false,
    selectedPreset: null, activePreset: null, bank: 0, second: false,
  });
  clearAllLeds();
  $('dump-status').textContent = hasDump
    ? `${state.presets.length} presets (${r.frame_count} frames)` : 'No dump loaded';
  applyWriteStatus(state.write);
  $('btn-save-dump').disabled = !hasDump;
  $('btn-read-preset').disabled = !hasDump;
  $('btn-write-preset').disabled = true;
  $('preset-detail').textContent = 'Select a preset to view / edit.';
  $('preset-hex').textContent = '—';
  renderPresetList();
  $('g-bank-mode').value = '';
  $('g-bank-size').value = String(state.global_config?.bank_size || 5);
  $('g-rx-ch').value = state.global_config?.midi_rx_channel || 1;
  $('g-remote-title').value = '';
  $('g-pc-status').value = '';
  $('g-rx-omni').checked = false;
  $('g-rx-ch').disabled = false;
  $('switch-type-grid').innerHTML = '';
  applyDumpGlobalsToUI();
  populateSongPicker(); populateSetPicker(); renderPCMap();
  renderChannelNames();
  applyPartialDumpBanner();
  if (!resetSelection && state.presets.some(p => p.num === selectedPreset)) selectPreset(selectedPreset);
}

async function restoreDump(options) {
  try {
    const response = await fetch('/api/dump/status');
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    hydrateDump(await response.json(), options);
  } catch (error) { toast('State restore failed: ' + error.message, true); }
}

// Server marks the dump as `complete: false` when it isn't the full 145-frame
// multiset. A partial capture is fine to inspect/edit but must NOT be written
// to the device — Write All would overwrite valid presets with empty frames.
function applyPartialDumpBanner() {
  let bar = $('partial-dump-banner');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'partial-dump-banner';
    bar.style.cssText = 'background:#5a3300;border:1px solid #ffb000;'
      + 'color:#ffd28a;padding:8px 12px;margin:8px auto;max-width:1240px;'
      + 'border-radius:4px;font-size:12px;display:none;';
    document.body.insertBefore(bar, document.body.firstChild);
  }
  if (state.presets.length && state.dumpComplete === false) {
    bar.textContent = 'Partial / incomplete dump — Write All is disabled. '
      + 'Re-capture a full dump (145 frames) before pushing to the device.';
    bar.style.display = 'block';
    const w = $('btn-write-all'); if (w) w.disabled = true;
  } else {
    bar.style.display = 'none';
  }
}

function applyDumpGlobalsToUI() {
  if (!state.globals) return;
  const g = state.globals;
  $('g-bank-mode').value = g.operating_mode || '';
  $('g-bank-mode').disabled = true;
  $('g-bank-size').value = g.bank_size != null ? String(g.bank_size) : '';
  $('g-bank-style').value = '';
  // bank_style is always null (location unknown) — dropdown stays disabled.
  renderSwitchTypes(g.switch_types);
  if (g.midi_rx_channel === 'OMNI') {
    $('g-rx-omni').checked = true;
    $('g-rx-ch').disabled = true;
  } else if (g.midi_rx_channel != null) {
    $('g-rx-ch').value = g.midi_rx_channel;
    $('g-rx-omni').checked = false;
    $('g-rx-ch').disabled = false;
  } else {
    $('g-rx-ch').value = '';
    $('g-rx-omni').checked = false;
    $('g-rx-ch').disabled = false;
  }
  if (g.remote_title != null && $('g-remote-title')) $('g-remote-title').value = g.remote_title;
  if (g.pc_status && $('g-pc-status')) $('g-pc-status').value = g.pc_status;
  populateSongPicker(); populateSetPicker(); renderPCMap();
}

// ── Songs ──────────────────────────────────────────────────────────────
function populateSongPicker() {
  const sel = $('songs-pick'); if (!sel) return;
  const cur = sel.value || '1';
  sel.innerHTML = '';
  for (let i = 1; i <= 150; i++) {
    sel.innerHTML += `<option value="${i}">SG${String(i).padStart(3,'0')}</option>`;
  }
  sel.value = cur;
  renderSong();
}

function presetOptionList(selectedNum, preserveUnknown = false) {
  let html = preserveUnknown
    ? `<option value="" disabled ${selectedNum == null ? 'selected' : ''}>Unknown — preserve stored value</option>`
    : `<option value="">— off —</option>`;
  state.presets.forEach(p => {
    html += `<option value="${p.num}" ${p.num === selectedNum ? 'selected' : ''}>PR${String(p.num).padStart(3,'0')} ${escapeHtml((p.name||'').trim())}</option>`;
  });
  return html;
}

function renderSong() {
  const sel = $('songs-pick'); if (!sel) return;
  const num = parseInt(sel.value || '1', 10);
  const song = (state.songs || []).find(s => s.idx === num);
  const tbl = $('song-slots');
  if (!song) { tbl.innerHTML = '<tr><td>No song data</td></tr>'; return; }
  // A song stores 15 preset slots (SW1-SW15); only the first Bank-Size are
  // "active" on the device, but all 15 are stored, so show them all.
  const active = (state.globals && state.globals.bank_size) || 15;
  let html = '';
  for (let i = 0; i < 15; i++) {
    const v = song.slots[i];
    const presetNum = v == null ? null : v + 1;
    const dim = i >= active ? ' style="opacity:.5"' : '';
    const note = i >= active ? ' <span class="hint" style="font-size:9px">(inactive at Bank Size '+active+')</span>' : '';
    html += `<tr${dim}><th style="width:80px">SW${i+1}</th><td>
      <select data-song-slot="${i}">${presetOptionList(presetNum, true)}</select>${note}
    </td></tr>`;
  }
  tbl.innerHTML = html;
}

async function saveSong() {
  const num = parseInt($('songs-pick').value, 10);
  const slots = [];
  document.querySelectorAll('#song-slots [data-song-slot]').forEach(sel => {
    const v = sel.value === '' ? null : parseInt(sel.value, 10);
    slots.push(v);
  });
  const r = await fetch(`/api/song/${num}`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slots}),
  }).then(r=>r.json());
  if (!r.ok) { toast('Song save failed: ' + (r.error||''), true); return; }
  // Replace just this song in state
  const i = state.songs.findIndex(s => s.idx === num);
  if (i >= 0) state.songs[i] = r.song;
  toast(`SONG${String(num).padStart(3,'0')} saved`);
}

// ── Sets ───────────────────────────────────────────────────────────────
function populateSetPicker() {
  const sel = $('sets-pick'); if (!sel) return;
  const cur = sel.value || '1';
  sel.innerHTML = '';
  for (let i = 1; i <= 10; i++) sel.innerHTML += `<option value="${i}">SET${i}</option>`;
  sel.value = cur;
  renderSet();
}

function songOptionList(selectedNum) {
  let html = `<option value="" disabled ${selectedNum == null ? 'selected' : ''}>Unknown — preserve stored value</option>`;
  for (let i = 1; i <= 150; i++) {
    html += `<option value="${i}" ${i === selectedNum ? 'selected' : ''}>SG${String(i).padStart(3,'0')}</option>`;
  }
  return html;
}

function renderSet() {
  const sel = $('sets-pick'); if (!sel) return;
  const num = parseInt(sel.value || '1', 10);
  const setObj = (state.sets || []).find(s => s.idx === num);
  const grid = $('set-banks');
  if (!setObj) { grid.innerHTML = '<div>No set data</div>'; return; }
  let html = '';
  for (let b = 0; b < 50; b++) {
    const v = setObj.banks[b];
    html += `<div style="display:flex;flex-direction:column;gap:2px;">
      <span style="font-size:10px;color:var(--mute);">BK${String(b+1).padStart(2,'0')}</span>
      <select data-set-bank="${b}" style="font-size:11px;">${songOptionList(v)}</select>
    </div>`;
  }
  grid.innerHTML = html;
}

async function saveSet() {
  const num = parseInt($('sets-pick').value, 10);
  const banks = [];
  document.querySelectorAll('#set-banks [data-set-bank]').forEach(sel => {
    const v = sel.value === '' ? null : parseInt(sel.value, 10);
    banks.push(v);
  });
  const r = await fetch(`/api/set/${num}`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({banks}),
  }).then(r=>r.json());
  if (!r.ok) { toast('Set save failed: ' + (r.error||''), true); return; }
  const i = state.sets.findIndex(s => s.idx === num);
  if (i >= 0) state.sets[i] = r.set;
  toast(`SET${num} saved`);
}

// ── PC Map ─────────────────────────────────────────────────────────────
function renderPCMap() {
  const grid = $('pcmap-grid'); if (!grid) return;
  const m = state.pc_map || [];
  let html = '';
  for (let i = 0; i < 128; i++) {
    const v = m[i];
    const presetNum = v == null ? null : v + 1;
    html += `<div style="display:flex;flex-direction:column;gap:2px;min-width:0;">
      <span style="font-size:10px;color:var(--mute);">PC${String(i+1).padStart(3,'0')}</span>
      <select data-pcmap="${i}" style="font-size:11px;width:100%;max-width:100%;">${presetOptionList(presetNum)}</select>
    </div>`;
  }
  grid.innerHTML = html;
}

async function savePCMap() {
  const map = [];
  document.querySelectorAll('#pcmap-grid [data-pcmap]').forEach(sel => {
    map.push(sel.value === '' ? null : parseInt(sel.value, 10));
  });
  const r = await fetch('/api/pcmap', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({map}),
  }).then(r=>r.json());
  if (!r.ok) { toast('PC map save failed: ' + (r.error||''), true); return; }
  state.pc_map = r.pc_map;
  toast('PC map saved');
  renderPCMap();
}

function resetPCMapIdentity() {
  document.querySelectorAll('#pcmap-grid [data-pcmap]').forEach(sel => {
    const i = parseInt(sel.dataset.pcmap, 10);
    sel.value = String(Math.min(120, i + 1));
  });
}

// ── Channel / switch / pedal names → write back ───────────────────────
async function saveNamesToDump() {
  const channels = [];
  document.querySelectorAll('#channel-names-grid input[data-ch]').forEach(inp => {
    channels[parseInt(inp.dataset.ch, 10)] = inp.value.toUpperCase().slice(0, 4);
  });
  const switches = [];
  document.querySelectorAll('#switch-names-grid input[data-sw]').forEach(inp => {
    switches[parseInt(inp.dataset.sw, 10)] = inp.value.toUpperCase().slice(0, 4);
  });
  const pedals = [];
  document.querySelectorAll('#pedal-names-grid input[data-pd]').forEach(inp => {
    pedals[parseInt(inp.dataset.pd, 10)] = inp.value.toUpperCase().slice(0, 4);
  });
  const r = await fetch('/api/names', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({channel_names: channels, switch_names: {pedals, switches}}),
  }).then(r=>r.json());
  if (!r.ok) { toast('Names save failed: ' + (r.error||''), true); return; }
  state.channel_names = r.channel_names;
  state.switch_names = r.switch_names;
  toast('Names saved to dump');
  renderChannelNames();
}

// ── Write All pacing ───────────────────────────────────────────────────
// Pacing is hard-locked server-side to the manual's 1-byte-per-15-ms spec.
// The pacing panel in the Globals tab is purely informational. No client-
// side controls — see /api/writeback-pacing for the locked values.
async function loadPacing() {
  // No-op: panel values are baked into the HTML now.  This stub stays so
  // existing boot-time call sites keep working.
}

// ── Globals: write back to dump ────────────────────────────────────────
const SWITCH_TYPE_OPTIONS = ['LATCH', 'MOMENTARY', 'HOLD'];
function renderSwitchTypes(types) {
  const grid = $('switch-type-grid');
  if (!grid) return;
  grid.innerHTML = '';
  for (let sw = 1; sw <= 15; sw++) {
    const cur = types && types[sw];
    const unknown = !SWITCH_TYPE_OPTIONS.includes(cur);
    const cell = document.createElement('div');
    cell.className = 'sw-card';
    const opts = SWITCH_TYPE_OPTIONS.map(o =>
      `<option ${o === cur ? 'selected' : ''}>${o}</option>`).join('');
    cell.innerHTML = `<div class="n">SW${sw}</div>
      <select data-sw="${sw}">${unknown ? '<option value="" disabled selected>Unknown — preserve stored value</option>' : ''}${opts}</select>`;
    grid.appendChild(cell);
  }
}
async function saveSwitchTypesToDump() {
  const switch_types = {};
  document.querySelectorAll('#switch-type-grid [data-sw]').forEach(sel => {
    if (SWITCH_TYPE_OPTIONS.includes(sel.value)) switch_types[sel.dataset.sw] = sel.value;
  });
  const r = await fetch('/api/dump-globals', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({switch_types}),
  }).then(r=>r.json());
  if (!r.ok) { toast('Switch type save failed: ' + (r.error||''), true); return; }
  state.globals = r.globals;
  renderSwitchTypes(r.globals.switch_types);
  toast('Switch types saved to dump');
}

async function saveGlobalToDump() {
  const body = {};
  if ($('g-bank-size').value !== '') body.bank_size = Number($('g-bank-size').value);
  if ($('g-rx-omni').checked) body.midi_rx_channel = 'OMNI';
  else if ($('g-rx-ch').value !== '') body.midi_rx_channel = Number($('g-rx-ch').value);
  // Unconfirmed mode/style and unchanged unknown values are omitted.
  const rt = $('g-remote-title');
  if (rt && rt.value !== '') body.remote_title = parseInt(rt.value, 10);
  if ($('g-pc-status')?.value) body.pc_status = $('g-pc-status').value;
  const r = await fetch('/api/dump-globals', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  }).then(r=>r.json());
  if (!r.ok) { toast('Globals save failed: ' + (r.error||''), true); return; }
  state.globals = r.globals;
  toast('Globals saved to dump');
}

function applyWriteStatus(write = {enabled: false, busy: false}) {
  state.write = write;
  $('btn-write-all').disabled = !write.enabled || write.busy || !state.dumpComplete;
  $('btn-write-preset').disabled = !write.enabled || write.busy || !state.dumpComplete || !state.selectedPreset;
  $('btn-write-all').title = write.enabled
    ? 'Experimental transport: host completion does not verify device receipt'
    : 'Hardware writes disabled until experimental writing is enabled on the server';
  for (const id of ['midi-in-select', 'midi-out-select']) $(id).disabled = !!write.busy;
}

function enableDumpButtons() {
  applyWriteStatus(state.write);
  const save = document.getElementById('btn-save-dump');
  if (save) save.disabled = false;
  $('btn-read-preset').disabled = false;
  // write-preset is enabled once a preset is selected
}

async function sendDump() {
  if (!confirm('Write All — send entire dump to device?\n\nPut the unit on SYSX page 1 → LOAD first.')) return;
  const r = await fetch('/api/dump/send', {method:'POST'}).then(r=>r.json());
  if (!r.ok) { toast('Write All failed: ' + (r.error||''), true); return; }
  toast(`Writing ${r.frames} frames…`);
}

// Save the current in-memory dump to a .syx file (browser download).
function saveDump() {
  // /api/dump/save returns 400 if no dump is loaded; the button is
  // disabled in that case so this should always succeed when fired.
  window.location = '/api/dump/save';
  toast('Saving rocktron-*.syx to your Downloads folder');
}

async function writePreset() {
  const num = state.selectedPreset;
  if (!num) { toast('Select a preset first', true); return; }
  if (!confirm(`Write Preset PR${String(num).padStart(3,'0')} to device?\n\nPut the unit on SYSX page 1 → LOAD first.`)) return;
  const r = await fetch(`/api/preset/${num}/send`, {method:'POST'}).then(r=>r.json());
  if (!r.ok) { toast('Write Preset failed: ' + (r.error||''), true); return; }
  toast(`PR${String(num).padStart(3,'0')} sent (${r.bytes} B)`);
}

async function readAll() { startCapture('all'); }
async function readPreset() {
  if (!state.selectedPreset) { toast('Select a preset first', true); return; }
  startCapture('preset:' + state.selectedPreset);
}
async function startCapture(target) {
  if (!confirm('Read mode — listening for an incoming bulk dump.\n\n' +
               'On the All Access: press 2ND, then SYSX. On page 1 select DUMP, then press CTR STORE.')) return;
  const r = await fetch('/api/dump/capture', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({target}),
  }).then(r=>r.json());
  if (!r.ok) { toast('Read failed: ' + (r.error||''), true); return; }
  toast('Capture armed — initiate DUMP on the device');
  $('dump-status').textContent = 'capturing… 0 frames';
}

// ── preset list & detail ──────────────────────────────────────────────────
function renderPresetList() {
  const el = $('preset-list'); el.innerHTML = '';
  state.presets.forEach(p => {
    const row = document.createElement('div');
    row.className = 'preset-row';
    row.innerHTML = `<span class="n">${p.num}</span>
                     <span class="n2">PR${String(p.num).padStart(3,'0')}</span>
                     <span>${escapeHtml(p.name || '')}</span>`;
    row.onclick = () => selectPreset(p.num);
    el.appendChild(row);
  });
}

function selectPreset(num) {
  const p = state.presets.find(p => p.num === num);
  if (!p) return;
  state.selectedPreset = num;
  applyWriteStatus(state.write);
  document.querySelectorAll('.preset-row').forEach((r,i)=>{
    r.classList.toggle('active', i === num-1);
  });
  const det = $('preset-detail');
  det.innerHTML = `
    <table class="kv">
      <tr><th>Number</th><td>PR${String(p.num).padStart(3,'0')}</td></tr>
      <tr><th>Name (13 chars)</th><td>
        <input type="text" id="p-name" maxlength="13" value="${escapeHtml(p.name||'')}" style="width:200px; text-transform:uppercase;">
        <button class="primary save-btn" onclick="renamePreset()">Save name</button>
      </td></tr>
    </table>

    <div class="editor-section" id="ed-pc">
      <h4>PC slots — 16 channels</h4>
      <div class="pc-grid" id="pc-grid"></div>
      <div class="editor-actions">
        <button class="primary save-btn" onclick="savePresetPC()">Save PC slots</button>
        <span class="hint">Value is the displayed PC# (0-127 or 1-128 depending on starting-preset). OFF = slot inactive.</span>
      </div>
    </div>

    <div class="editor-section" id="ed-ia">
      <h4>Instant-access switches — on/off (CTR STORE)</h4>
      <div class="ia-row" id="ia-row"></div>
      <div class="editor-actions">
        <button class="primary save-btn" onclick="savePresetIA()">Save IA bitmap</button>
        <span class="hint">All 15 switches are editable. Storage: 3 bytes at 0xC8/0xCA/0xCC, REVERSED bit order. Click pill to toggle.</span>
      </div>
    </div>

    <div class="editor-section" id="ed-cmd">
      <h4>Custom MIDI string — up to 5 commands per preset</h4>
      <div id="cmd-list"></div>
      <div class="editor-actions">
        <button class="primary save-btn" onclick="savePresetCMD()">Save custom MIDI</button>
        <span class="hint">Type byte = index into the manual's CUSTOM P1 list (layout verified against all 120 presets offline; see docs/REVERSE_ENGINEERING.md).</span>
      </div>
    </div>

    <div class="editor-section" id="ed-sysex">
      <h4>SysEx string — 30 bytes + per-preset ON/OFF (SYSX P2/P3)</h4>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <label class="act" style="font-size:11px;color:var(--accent);"><input type="checkbox" id="p-sysex-on"> Send sysex on preset recall (SYSX P2)</label>
      </div>
      <div class="sysex-grid" id="sysex-grid"></div>
      <div class="editor-actions">
        <button class="primary save-btn" onclick="savePresetSYSX()">Save SysEx</button>
        <span class="hint">Each byte 0-127. Toggle byte at 0xF6, payload at 0xF8.</span>
      </div>
    </div>`;
  renderPresetPC(p);
  renderPresetIA(p);
  renderPresetCMD(p);
  renderPresetSYSX(p);
  $('preset-hex').textContent = p.raw_hex.match(/.{1,2}/g).join(' ');
}

// ── per-preset editor renderers ───────────────────────────────────────────
function chName(ch) {
  const n = (state.channel_names && state.channel_names[ch-1]) || '';
  return n || `CH${ch}`;
}

function renderPresetPC(p) {
  const g = $('pc-grid'); g.innerHTML = '';
  (p.pc_slots || []).forEach(s => {
    const cell = document.createElement('div');
    cell.className = 'pc-cell' + (s.active ? '' : ' off');
    cell.innerHTML = `
      <div class="lbl"><span>CH${s.ch}</span><span class="ch-name">${escapeHtml(chName(s.ch))}</span></div>
      <input type="number" min="0" max="127" value="${s.value}" data-pc-ch="${s.ch}" data-pc-field="value">
      <label class="act"><input type="checkbox" ${s.active ? 'checked' : ''} data-pc-ch="${s.ch}" data-pc-field="active">active</label>`;
    g.appendChild(cell);
    cell.querySelector('input[type="checkbox"]').addEventListener('change', e => {
      cell.classList.toggle('off', !e.target.checked);
    });
  });
}

function renderPresetIA(p) {
  const r = $('ia-row'); r.innerHTML = '';
  (p.ia_bits || []).forEach((b, i) => {
    const num = i + 1;
    const sw = (state.switch_names && state.switch_names.switches && state.switch_names.switches[i]) || '';
    const pill = document.createElement('div');
    pill.className = 'ia-pill' + (b ? ' on' : '');
    pill.innerHTML = `<div class="num">SW${num}</div><div>${escapeHtml(sw || '—')}</div>`;
    pill.dataset.swNum = num;
    pill.dataset.on = b ? '1' : '0';
    pill.onclick = () => {
      const cur = pill.dataset.on === '1';
      pill.dataset.on = cur ? '0' : '1';
      pill.classList.toggle('on', !cur);
    };
    r.appendChild(pill);
  });
}

// Manual CUSTOM P1 list order — the type byte is the index into this list
// (N ON>=0x01, C CH>=0x03, NONE=0x11 observed on hardware; layout confirmed
// against all 120 baseline presets by tools/analyze_captures.py).
const CMD_TYPE_OPTIONS = ['NOFF>','N ON>','KPRS>','C CH>','P CH>','CPRS>','PBEN>',
  'T CLK','START','CONTU','STOP','ACTSN','SYSRS','M T C>','SGPP>','SGSL>',
  'T REQ','NONE'];

function renderPresetCMD(p) {
  const list = $('cmd-list'); list.innerHTML = '';
  (p.cmd_slots || []).forEach(c => {
    const row = document.createElement('div');
    row.className = 'cmd-row';
    const opts = CMD_TYPE_OPTIONS.map(o =>
      `<option ${o === c.type_label ? 'selected' : ''}>${o}</option>`).join('');
    const isUnknown = !CMD_TYPE_OPTIONS.includes(c.type_label);
    row.innerHTML = `
      <div class="cmd-num">CMD${c.idx}</div>
      <select data-cmd-idx="${c.idx}" data-cmd-field="type_label">
        ${opts}
        ${isUnknown ? `<option selected value="${escapeHtml(c.type_label)}" data-type-byte="${c.type_byte}">${escapeHtml(c.type_label)}</option>` : ''}
      </select>
      <input type="number" min="1" max="16" value="${c.channel}" data-cmd-idx="${c.idx}" data-cmd-field="channel" title="MIDI channel (1-16)">
      <input type="number" min="0" max="127" value="${c.data1}" data-cmd-idx="${c.idx}" data-cmd-field="data1" title="Data 1 (note / cc# / prog)">
      <input type="number" min="0" max="127" value="${c.data2}" data-cmd-idx="${c.idx}" data-cmd-field="data2" title="Data 2 (velocity / value)">
      <span class="hint" style="font-size:9px;">type 0x${c.type_byte.toString(16).padStart(2,'0').toUpperCase()}</span>`;
    list.appendChild(row);
  });
}

function renderPresetSYSX(p) {
  const g = $('sysex-grid'); g.innerHTML = '';
  (p.sysex_bytes || []).forEach((b, i) => {
    const cell = document.createElement('div');
    cell.className = 'sysex-cell';
    cell.innerHTML = `<div class="idx">B${String(i+1).padStart(2,'0')}</div>
      <input type="number" min="0" max="127" value="${b == null ? '' : escapeHtml(b)}" placeholder="EOX" title="Empty preserves the stored byte and EOX flag" data-sx-i="${i}">`;
    g.appendChild(cell);
  });
  if ($('p-sysex-on')) $('p-sysex-on').checked = !!p.sysex_on;
}

// ── per-preset edit savers ────────────────────────────────────────────────
async function savePresetPC() {
  const num = state.selectedPreset;
  const slots = [];
  document.querySelectorAll('#pc-grid .pc-cell').forEach(cell => {
    const ch = parseInt(cell.querySelector('input[type="number"]').dataset.pcCh, 10);
    const value = parseInt(cell.querySelector('input[type="number"]').value, 10) || 0;
    const active = cell.querySelector('input[type="checkbox"]').checked;
    slots.push({ch, value, active});
  });
  const r = await fetch(`/api/preset/${num}/pc`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({slots}),
  }).then(r=>r.json());
  if (!r.ok) { toast('PC save failed: ' + (r.error||''), true); return; }
  state.presets[num-1] = r.preset;
  toast(`PR${String(num).padStart(3,'0')} PC slots saved`);
  selectPreset(num);
}

async function savePresetIA() {
  const num = state.selectedPreset;
  const bits = [];
  for (let i = 1; i <= 15; i++) {
    const pill = document.querySelector(`#ia-row .ia-pill[data-sw-num="${i}"]`);
    bits.push(pill ? pill.dataset.on === '1' : false);
  }
  const r = await fetch(`/api/preset/${num}/ia`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({bits}),
  }).then(r=>r.json());
  if (!r.ok) { toast('IA save failed: ' + (r.error||''), true); return; }
  state.presets[num-1] = r.preset;
  toast(`PR${String(num).padStart(3,'0')} IA bitmap saved`);
  selectPreset(num);
}

async function savePresetCMD() {
  const num = state.selectedPreset;
  const cmds = [];
  document.querySelectorAll('#cmd-list .cmd-row').forEach(row => {
    const idx = parseInt(row.querySelector('select').dataset.cmdIdx, 10);
    const fields = {idx};
    row.querySelectorAll('[data-cmd-field]').forEach(el => {
      const f = el.dataset.cmdField;
      fields[f] = (el.tagName === 'SELECT') ? el.value : (parseInt(el.value, 10) || 0);
    });
    const selected = row.querySelector('select').selectedOptions[0];
    if (selected.dataset.typeByte != null) fields.type_byte = Number(selected.dataset.typeByte);
    cmds.push(fields);
  });
  const r = await fetch(`/api/preset/${num}/cmd`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({cmds}),
  }).then(r=>r.json());
  if (!r.ok) { toast('CMD save failed: ' + (r.error||''), true); return; }
  state.presets[num-1] = r.preset;
  toast(`PR${String(num).padStart(3,'0')} custom MIDI saved`);
  selectPreset(num);
}

async function savePresetSYSX() {
  const num = state.selectedPreset;
  const bytes = [];
  document.querySelectorAll('#sysex-grid input').forEach(inp => {
    bytes.push(inp.value.trim() === '' ? null : parseInt(inp.value, 10));
  });
  const on = $('p-sysex-on') ? $('p-sysex-on').checked : false;
  const r = await fetch(`/api/preset/${num}/sysex`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({bytes, on}),
  }).then(r=>r.json());
  if (!r.ok) { toast('SysEx save failed: ' + (r.error||''), true); return; }
  state.presets[num-1] = r.preset;
  toast(`PR${String(num).padStart(3,'0')} SysEx saved (${on ? 'ON' : 'OFF'})`);
  selectPreset(num);
}

async function renamePreset() {
  const num = state.selectedPreset;
  const name = $('p-name').value.toUpperCase();
  const r = await fetch(`/api/preset/${num}/rename`, {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name}),
  }).then(r=>r.json());
  if (!r.ok) { toast('Rename failed: ' + (r.error||''), true); return; }
  state.presets[num-1] = r.preset;
  renderPresetList();
  selectPreset(num);
  toast(`PR${String(num).padStart(3,'0')} renamed`);
}

// ── channel / switch names ────────────────────────────────────────────────
function renderChannelNames() {
  const cg = $('channel-names-grid'); cg.innerHTML = '';
  state.channel_names.forEach((n, i) => {
    cg.innerHTML += `<div class="chan-card">
      <div class="n">CH${i+1}</div>
      <input type="text" maxlength="4" value="${escapeHtml(n||'')}" data-ch="${i}">
    </div>`;
  });
  const sg = $('switch-names-grid'); sg.innerHTML = '';
  (state.switch_names.switches||[]).forEach((n, i) => {
    sg.innerHTML += `<div class="sw-card">
      <div class="n">SW${i+1}</div>
      <input type="text" maxlength="4" value="${escapeHtml(n||'')}" data-sw="${i}">
    </div>`;
  });
  const pg = $('pedal-names-grid'); pg.innerHTML = '';
  (state.switch_names.pedals||[]).forEach((n, i) => {
    pg.innerHTML += `<div class="sw-card">
      <div class="n">PED${i+1}</div>
      <input type="text" maxlength="4" value="${escapeHtml(n||'')}" data-pd="${i}">
    </div>`;
  });
  renderIaRouting();
  renderFCSwitches();
}

function renderIaRouting() {
  const g = $('ia-routing-grid'); if (!g) return;
  g.innerHTML = '';
  const map = iaMap();
  const sw  = state.switch_names.switches || [];
  for (let n = 6; n <= 15; n++) {
    const cfg = map[n] || {ch:1, cc:20+n};
    const label = sw[n-1] || ('SW' + n);
    g.innerHTML += `<div class="sw-card">
      <div class="n">SW${n} — ${escapeHtml(label)}</div>
      <label style="font-size:10px;color:var(--mute)">CH</label>
      <input type="number" min="1" max="16" value="${cfg.ch}" data-ia-ch="${n}" style="width:50px">
      <label style="font-size:10px;color:var(--mute)">CC#</label>
      <input type="number" min="0" max="120" value="${cfg.cc}" data-ia-cc="${n}" style="width:60px">
    </div>`;
  }
  g.querySelectorAll('input').forEach(inp => {
    inp.onchange = () => {
      const n = +(inp.dataset.iaCh || inp.dataset.iaCc);
      const m = iaMap();
      const cur = m[n] || {ch:1, cc:20+n};
      if (inp.dataset.iaCh) cur.ch = Math.max(1, Math.min(16, +inp.value));
      else                  cur.cc = Math.max(0, Math.min(120, +inp.value));
      m[n] = cur;
      saveIaMap(m);
      toast(`SW${n}: CH${cur.ch} CC#${cur.cc}`);
    };
  });
}

// ── virtual foot controller ───────────────────────────────────────────────
// Device layout, row-major (top → bottom, left → right).
// Each cell is either a control button {kind, label} or a numbered switch
// {kind:'sw', n: 1-15}.
const FC_LAYOUT = [
  {kind:'2nd',  label:'2ND'},  {kind:'sw',n:11}, {kind:'sw',n:12}, {kind:'sw',n:13}, {kind:'sw',n:14}, {kind:'sw',n:15},
  {kind:'up',   label:'UP'},   {kind:'sw',n: 6}, {kind:'sw',n: 7}, {kind:'sw',n: 8}, {kind:'sw',n: 9}, {kind:'sw',n:10},
  {kind:'down', label:'DOWN'}, {kind:'sw',n: 1}, {kind:'sw',n: 2}, {kind:'sw',n: 3}, {kind:'sw',n: 4}, {kind:'sw',n: 5},
];

function bankSize()    { return +($('g-bank-size')?.value || 5); }
function totalPresets(){ return state.presets.length || 120; }
function numBanks()    { return Math.max(1, Math.ceil(totalPresets() / bankSize())); }
function presetNumForSwitch(n) { return state.bank * bankSize() + n; }   // 1-indexed

// ── Per-IA-switch routing (channel + CC#) ─────────────────────────────────
// Defaults seeded from the MUSE rig CONTROL NUMBERS sheet. Editable from
// the Channels & Switches tab; persisted to localStorage.
const DEFAULT_IA_MAP = {
   6: {ch:2, cc:13},   //  6 VOX1  → HEAD CC#13
   7: {ch:2, cc:12},   //  7 DIEZ  → HEAD CC#12
   8: {ch:2, cc:11},   //  8 MARS  → HEAD CC#11
   9: {ch:1, cc:11},   //  9 FUN   → LOOP CC#11
  10: {ch:1, cc:13},   // 10 SW10  → LOOP CC#13
  11: {ch:1, cc:17},   // 11 PHSD  → LOOP CC#17
  12: {ch:2, cc:14},   // 12 COMP  → HEAD CC#14
  13: {ch:1, cc:14},   // 13 FUZZ  → LOOP CC#14
  14: {ch:1, cc:15},   // 14 TOPB  → LOOP CC#15
  15: {ch:1, cc:18},   // 15 DLAY  → LOOP CC#18
};
function iaMap() {
  try {
    const saved = JSON.parse(localStorage.getItem('rocktron-ia-map') || '{}');
    return Object.assign({}, DEFAULT_IA_MAP, saved);
  } catch { return DEFAULT_IA_MAP; }
}
function saveIaMap(m) { localStorage.setItem('rocktron-ia-map', JSON.stringify(m)); }

// Friendly channel label for the activity log: the dump's own 4-char
// channel name when a dump is loaded, otherwise plain CHn.
function chLabel(ch) {
  const n = (state.channel_names && state.channel_names[ch - 1]) || '';
  return n ? `${n} (CH${ch})` : `CH${ch}`;
}

// ── MIDI activity log ────────────────────────────────────────────────────
// Textual mirror of every PC/CC the editor sends and that lands back from
// the device. Newest entry on top, capped at 80 rows.
const MA_MAX = 80;
function maLog(dir, ch, evt, dev, val) {
  const list = document.getElementById('midi-activity-list');
  if (!list) return;
  // Drop the placeholder "click a foot switch…" message on first event.
  const empty = list.querySelector('.ma-empty');
  if (empty) empty.remove();
  const d = new Date();
  const ts = `${String(d.getHours()).padStart(2,'0')}:` +
             `${String(d.getMinutes()).padStart(2,'0')}:` +
             `${String(d.getSeconds()).padStart(2,'0')}.` +
             `${String(d.getMilliseconds()).padStart(3,'0')}`;
  const row = document.createElement('div');
  row.className = 'ma-row ma-' + dir;
  row.innerHTML =
    `<span class="ma-time">${ts}</span>` +
    `<span class="ma-${dir}">${dir.toUpperCase()}</span>` +
    `<span>CH${ch}</span>` +
    `<span class="ma-evt">${escapeHtml(evt)} <span class="ma-dev">${escapeHtml(dev || '')}</span></span>` +
    `<span class="ma-val">${val == null ? '' : val}</span>`;
  list.insertBefore(row, list.firstChild);
  while (list.children.length > MA_MAX) list.removeChild(list.lastChild);
}

// ── LED helper for the All Access SVG ─────────────────────────────────────
// Each LED is a <circle data-led="N"> over its switch. Setting the
// gradient swaps it between the "off" red ember and the "on" amber glow.
function setLed(n, on) {
  const led = document.querySelector(`#aa-svg [data-led="${n}"]`);
  if (!led) return;
  led.setAttribute('fill', on ? 'url(#ledOn)' : 'url(#ledOff)');
  if (on) led.setAttribute('filter', 'url(#ledGlow)');
  else    led.removeAttribute('filter');
}
function clearAllLeds() {
  document.querySelectorAll('#aa-svg [data-led]').forEach(l => {
    l.setAttribute('fill', 'url(#ledOff)');
    l.removeAttribute('filter');
  });
}

// ── 14-segment LCD renderer (cyan glow, ghost-segments behind) ────────────
// Standard fourteen-segment alphanumeric layout: a/b/c/d/e/f around the
// outside, g1/g2 split middle bar, h/i/j the three upper inner segments
// (top-left diagonal, top vertical, top-right diagonal), m/l/k the three
// lower inner segments (bottom-left diagonal, bottom vertical, bottom-
// right diagonal). All segments built as thin parallelograms via the
// thickLine() helper so the renderer stays declarative.
function thickLine(x1, y1, x2, y2, w) {
  const dx = x2 - x1, dy = y2 - y1;
  const len = Math.hypot(dx, dy) || 1;
  const nx = -dy / len * w / 2;
  const ny =  dx / len * w / 2;
  return `M ${(x1+nx).toFixed(3)},${(y1+ny).toFixed(3)}`
       + ` L ${(x1-nx).toFixed(3)},${(y1-ny).toFixed(3)}`
       + ` L ${(x2-nx).toFixed(3)},${(y2-ny).toFixed(3)}`
       + ` L ${(x2+nx).toFixed(3)},${(y2+ny).toFixed(3)} Z`;
}
const W = 0.10;  // segment thickness in unit-cell coordinates
const SEG_PATHS = {
  a:  thickLine(0.20, 0.06, 0.80, 0.06, W),
  b:  thickLine(0.86, 0.10, 0.86, 0.46, W),
  c:  thickLine(0.86, 0.54, 0.86, 0.90, W),
  d:  thickLine(0.20, 0.94, 0.80, 0.94, W),
  e:  thickLine(0.14, 0.54, 0.14, 0.90, W),
  f:  thickLine(0.14, 0.10, 0.14, 0.46, W),
  g1: thickLine(0.20, 0.50, 0.46, 0.50, W),
  g2: thickLine(0.54, 0.50, 0.80, 0.50, W),
  h:  thickLine(0.20, 0.14, 0.46, 0.46, W),  // ＼ upper-left
  i:  thickLine(0.50, 0.10, 0.50, 0.46, W),  // | top vertical
  j:  thickLine(0.80, 0.14, 0.54, 0.46, W),  // ／ upper-right
  k:  thickLine(0.54, 0.54, 0.80, 0.86, W),  // ＼ lower-right
  l:  thickLine(0.50, 0.54, 0.50, 0.90, W),  // | bottom vertical
  m:  thickLine(0.46, 0.54, 0.20, 0.86, W),  // ／ lower-left
};
// 14-segment glyph mapping. Multi-letter segment names like "g1" appear
// as a single token, so segs is split by spaces, not chars.
const CHAR_SEGS = {
  '0':'a b c d e f j m', '1':'b c',           '2':'a b g1 g2 e d',  '3':'a b g2 c d',
  '4':'f g1 g2 b c',     '5':'a f g1 g2 c d', '6':'a f g1 g2 c d e','7':'a b c',
  '8':'a b c d e f g1 g2', '9':'a b c d f g1 g2',
  'A':'a b c e f g1 g2',
  'B':'a b c d g2 i l',         // verticals through middle for the two bumps
  'C':'a d e f',
  'D':'a b c d i l',            // verticals for D's straight back
  'E':'a d e f g1 g2',
  'F':'a e f g1 g2',
  'G':'a c d e f g2',
  'H':'b c e f g1 g2',
  'I':'a d i l',
  'J':'b c d',
  'K':'e f g1 j k',
  'L':'d e f',
  'M':'b c e f h j',            // verticals + top diagonals to centre
  'N':'b c e f h k',             // verticals + one full diagonal
  'O':'a b c d e f',
  'P':'a b e f g1 g2',
  'Q':'a b c d e f k',
  'R':'a b e f g1 g2 k',
  'S':'a c d f g1 g2',
  'T':'a i l',
  'U':'b c d e f',
  'V':'e f k m',                 // left vertical + V-bottom diagonals
  'W':'b c e f k m',             // both verticals + V-bottom (W-ish)
  'X':'h j k m',                 // pure X
  'Y':'h j l',                   // diagonals into stem
  'Z':'a d j m',                 // top + diagonal-down + bottom
  '-':'g1 g2',
  '_':'d',
  '/':'j m',                     // forward slash (used in DLY/PH etc.)
  '\\':'h k',
  '.':'', ' ':'',
};
function fcLcd(text) {
  const root = document.getElementById('lcd-segments');
  if (!root) return;
  while (root.firstChild) root.removeChild(root.firstChild);
  const SVGNS = 'http://www.w3.org/2000/svg';
  // Layout: panel (74,76) + (382,56). Leave 14px padding.
  const panelX = 74, panelY = 76, panelW = 382, panelH = 56;
  const charW = 14, charH = 28, gap = 4;
  const startX = panelX + 16;
  const startY = panelY + (panelH - charH) / 2;
  const upper = (text || '').toUpperCase().slice(0, 20);
  for (let i = 0; i < upper.length; i++) {
    const ch = upper[i];
    const cx = startX + i * (charW + gap);
    // ghost (off) segments very faint
    const ghost = document.createElementNS(SVGNS, 'g');
    ghost.setAttribute('transform', `translate(${cx},${startY}) scale(${charW},${charH})`);
    ghost.setAttribute('fill', '#0c2a3a');
    ghost.setAttribute('opacity', '0.16');
    Object.values(SEG_PATHS).forEach(d => {
      const p = document.createElementNS(SVGNS, 'path');
      p.setAttribute('d', d);
      ghost.appendChild(p);
    });
    root.appendChild(ghost);
    const segsTokens = (CHAR_SEGS[ch] || '').split(/\s+/).filter(Boolean);
    if (!segsTokens.length) continue;
    const g = document.createElementNS(SVGNS, 'g');
    g.setAttribute('transform', `translate(${cx},${startY}) scale(${charW},${charH})`);
    g.setAttribute('fill', '#3ad6ff');
    segsTokens.forEach(s => {
      if (!SEG_PATHS[s]) return;
      const p = document.createElementNS(SVGNS, 'path');
      p.setAttribute('d', SEG_PATHS[s]);
      g.appendChild(p);
    });
    root.appendChild(g);
  }
}

// ── Foot-controller render: just refresh the LCD; SVG buttons are static ──
function renderFCSwitches() {
  updateLcd();
  // Update SW6..SW15 labels with the dump's IA names (VOX1, DIEZ, …).
  // SW1..SW5 stay numeric — they're preset switches in the current bank.
  const sw = state.switch_names && state.switch_names.switches || [];
  for (let n = 6; n <= 15; n++) {
    const el = document.getElementById('aa-label-' + n);
    if (!el) continue;
    const name = sw[n - 1];
    el.textContent = (name && name.trim() && !/^SW\d+$/.test(name))
      ? name : String(n);
  }
}

function fcSwitchSvg(n) {
  const bs = bankSize();
  console.log('[AA] click sw=', n, 'bs=', bs, 'bank=', state.bank);
  if (n <= bs) {
    // Preset switch — exclusive: clear all preset-switch LEDs, light this one
    for (let i = 1; i <= bs; i++) setLed(i, false);
    setLed(n, true);
    const pn = presetNumForSwitch(n);
    state.activePreset = pn;
    const p = state.presets[pn - 1];
    const name = p ? p.name : '';
    fcLcd(`PR${String(pn).padStart(3,'0')} ${name}`);
    // Update IA switch LEDs from this preset's stored ia_bits.
    // IA range = SW(bs+1) .. SW15 in the current bank size. SW6-15 always have
    // a bit slot (SW1-5 are also stored but become preset switches at lower
    // bank sizes). We only paint LEDs for SWs that are currently IA.
    if (p && p.ia_bits) {
      for (let i = bs + 1; i <= 15; i++) {
        setLed(i, !!p.ia_bits[i - 1]);
      }
    }
    fetch('/api/vfc/send', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({channel:1, type:'pc', num: (pn - 1) & 0x7F}),
    });
    maLog('tx', 1, `PC ${pn-1}`, `Recall PR${String(pn).padStart(3,'0')} ${name}`, pn-1);
  } else {
    // Instant-access — toggle. We track on/off via the LED's current fill.
    const led = document.querySelector(`#aa-svg [data-led="${n}"]`);
    const wasOn = led && led.getAttribute('fill') === 'url(#ledOn)';
    setLed(n, !wasOn);
    const map = iaMap()[n] || {ch:1, cc:20+n};
    const val = (!wasOn) ? 127 : 0;
    fetch('/api/vfc/send', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({channel:map.ch, type:'cc', num:map.cc, val}),
    });
    const swNames = (state.switch_names && state.switch_names.switches) || [];
    const swLabel = swNames[n-1] || `SW${n}`;
    maLog('tx', map.ch, `CC#${map.cc}`,
          `${swLabel} ${val ? 'ON' : 'OFF'} (${chLabel(map.ch)})`,
          val);
  }
}

function fcCtrlSvg(kind) {
  console.log('[AA] click ctrl=', kind);
  if (kind === '2nd') {
    state.second = !state.second;
    maLog('tx', 0, '2ND', `Secondary functions ${state.second ? 'ON' : 'OFF'}`, null);
    return;
  }
  const nb = numBanks(), delta = kind === 'up' ? +1 : -1;
  state.bank = ((state.bank + delta) % nb + nb) % nb;
  state.activePreset = null;
  // New bank — clear all preset LEDs and refresh the LCD bank readout
  const bs = bankSize();
  for (let i = 1; i <= bs; i++) setLed(i, false);
  updateLcd();
  maLog('tx', 0, kind === 'up' ? 'BANK +' : 'BANK -',
        `Bank ${state.bank+1}/${nb} (PR${String(state.bank*bs+1).padStart(3,'0')}-${String(Math.min(state.bank*bs+bs, totalPresets())).padStart(3,'0')})`,
        null);
}

// Sole click entry-point for foot-controller buttons.
// IMPORTANT: in some Chromium configurations, clicks on transformed SVG
// <g> children (with our brushed-blue chassis + drop-shadow filter on
// top of them) deliver e.target = the outer <svg> element rather than
// the dome circle that's actually under the cursor. Using e.target then
// fails to find the .aa-btn group. We therefore re-derive the topmost
// element via elementsFromPoint(clientX, clientY), which gives the true
// stack of elements regardless of how the click was routed.
$('aa-svg').addEventListener('click', e => {
  const stack = (document.elementsFromPoint
    ? document.elementsFromPoint(e.clientX, e.clientY)
    : [e.target]);
  for (const candidate of stack) {
    let el = candidate;
    while (el && el !== document.body) {
      if ($('aa-svg').contains(el) && el.dataset && (el.dataset.swNum || el.dataset.ctrlKind)) {
        if (el.dataset.swNum) {
          fcSwitchSvg(+el.dataset.swNum);
        } else {
          fcCtrlSvg(el.dataset.ctrlKind);
        }
        return;
      }
      el = el.parentNode;
    }
  }
});

function fcSwitch(n, btn) {
  const bs = bankSize();
  if (n <= bs) {
    // Preset select — exclusive among preset switches
    document.querySelectorAll('.fc-btn[data-sw-num]').forEach(b => {
      if (+b.dataset.swNum <= bs) b.classList.remove('on');
    });
    btn.classList.add('on');
    const pn = presetNumForSwitch(n);
    const p  = state.presets[pn - 1];
    state.activePreset = pn;
    fcLcd(`PR${String(pn).padStart(3,'0')}  ${p ? p.name : ''}`);
    fetch('/api/vfc/send', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({channel:1, type:'pc', num: (pn - 1) & 0x7F}),
    });
  } else {
    // Instant-access — toggle CC on the switch's configured channel + CC#
    btn.classList.toggle('on');
    const on  = btn.classList.contains('on');
    const map = iaMap()[n] || {ch:1, cc:20+n};
    fetch('/api/vfc/send', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({channel:map.ch, type:'cc', num:map.cc, val: on?127:0}),
    });
  }
}

function fcCtrl(kind, btn) {
  if (kind === '2nd') {
    state.second = !state.second;
    btn.classList.toggle('on', state.second);
    return;
  }
  // UP/DOWN — cycle through banks with wrap
  const nb = numBanks();
  const delta = kind === 'up' ? +1 : -1;
  state.bank = ((state.bank + delta) % nb + nb) % nb;
  state.activePreset = null;
  btn.classList.add('on');
  setTimeout(()=>btn.classList.remove('on'), 150);
  renderFCSwitches();
}

function updateLcd() {
  const bs = bankSize(), nb = numBanks();
  const first = state.bank * bs + 1;
  const last  = Math.min(state.bank * bs + bs, totalPresets());
  if (state.activePreset) {
    const p = state.presets[state.activePreset - 1];
    fcLcd(`PR${String(state.activePreset).padStart(3,'0')}  ${p ? p.name : ''}`);
  } else {
    fcLcd(`BNK ${String(state.bank+1).padStart(2,'0')}/${nb}  PR${String(first).padStart(3,'0')}-${String(last).padStart(3,'0')}`);
  }
}

// Process every incoming MIDI byte stream from the device.
function handleIncomingMidi(hex) {
  const bytes = (hex.match(/.{1,2}/g) || []).map(h => parseInt(h, 16));
  if (bytes.length < 2 || bytes[0] === 0xF0) return;
  const status = bytes[0] & 0xF0;
  const ch     = (bytes[0] & 0x0F) + 1;
  if (status === 0xC0)        onIncomingPC(ch, bytes[1]);
  else if (status === 0xB0)   onIncomingCC(ch, bytes[1], bytes[2] || 0);
}

function onIncomingPC(ch, pc) {
  maLog('rx', ch, `PC ${pc}`, chLabel(ch), pc);

  // NOTE: incoming PC on ch1 is NOT a preset-recall signal — it's the
  // device's outgoing program change for the MR9 loop controller (which
  // happens to live on ch1). On the All Access, the only way to detect a
  // preset change passively is by reading its bulk-dump state; the wire-
  // level traffic on ch1 reflects per-preset MR9 program numbers (e.g.
  // PR1 / MAN OF 1 sends PC 99 to the MR9). So we don't update the LCD
  // or the foot-controller's active-preset highlight from incoming PC.
  // The LCD is driven exclusively by the user's virtual-switch clicks.
}

function onIncomingCC(ch, cc, val) {
  // Resolve a friendly label: prefer the matching IA-routing entry,
  // fall back to the channel device name.
  let label = chLabel(ch);
  const map = iaMap();
  for (const k in map) {
    const cfg = map[k];
    if (cfg.ch === ch && cfg.cc === cc) {
      const swNames = (state.switch_names && state.switch_names.switches) || [];
      const swLabel = swNames[+k - 1] || `SW${k}`;
      label = `${swLabel} ${val >= 64 ? 'ON' : 'OFF'} · ${label}`;
      break;
    }
  }
  maLog('rx', ch, `CC#${cc}`, label, val);

  // Light up matching IA switch's LED in the foot controller (the
  // friendly label resolver above already grabbed iaMap, so just reuse).
  for (const k in map) {
    const cfg = map[k];
    if (cfg.ch === ch && cfg.cc === cc) {
      setLed(+k, val >= 64);
      break;
    }
  }
}

// (fcLcd lives above — drives the new 7-segment LCD via #lcd-segments.)

function fcPedal(which, val) {
  $('fc-ped'+which+'-val').textContent = val;
  fetch('/api/vfc/send', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({channel:1, type:'cc', num: which===1 ? 11 : 12, val:+val}),
  });
}
function quickSend() {
  fetch('/api/vfc/send', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      channel: +$('q-ch').value, type: $('q-type').value,
      num: +$('q-num').value, val: +$('q-val').value,
    }),
  });
}

// ── global config ─────────────────────────────────────────────────────────
async function loadGlobal() {
  const r = await fetch('/api/global-config').then(r=>r.json());
  if (r.ok === false) return;
  state.global_config = r;
  $('g-bank-mode').value = ''; $('g-bank-style').value = '';
  $('g-bank-size').value = r.bank_size;
  $('g-rx-omni').checked = r.midi_rx_channel === 'OMNI';
  $('g-rx-ch').value = r.midi_rx_channel === 'OMNI' ? '' : r.midi_rx_channel;
  $('g-preset-start').value = r.preset_start;
  // Re-render foot controller in case bank size just changed.
  applyDumpGlobalsToUI();
  renderFCSwitches();
}
async function saveGlobal() {
  const body = {preset_start: Number($('g-preset-start').value)};
  if ($('g-bank-size').value !== '') body.bank_size = Number($('g-bank-size').value);
  if ($('g-rx-omni').checked) body.midi_rx_channel = 'OMNI';
  else if ($('g-rx-ch').value !== '') body.midi_rx_channel = Number($('g-rx-ch').value);
  const r = await fetch('/api/global-config', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(body),
  });
  if (!r.ok) return;
  toast('Global saved');
}

// ── monitor ───────────────────────────────────────────────────────────────
function appendMonitor(ev) {
  const el = $('monitor');
  const d = new Date(ev.t*1000);
  const tt = d.toTimeString().slice(0,8) + '.' + String(d.getMilliseconds()).padStart(3,'0');
  const div = document.createElement('div');
  div.className = 'ev kind-' + ev.kind.replace(/[^a-z]/g,'');
  div.innerHTML = `<span>${tt}</span><span>${escapeHtml(ev.kind)}</span><span>${escapeHtml(ev.len)}B</span><span class="hex">${escapeHtml(ev.hex)}</span>`;
  el.appendChild(div);
  if (el.children.length > 500) el.removeChild(el.firstChild);
  if ($('mon-scroll').checked) el.scrollTop = el.scrollHeight;
}
function clearMonitor() { $('monitor').innerHTML = ''; }

// ── utilities ─────────────────────────────────────────────────────────────
function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// ── socket events ─────────────────────────────────────────────────────────
socket.on('hello', d => {
  if (d.ports) refreshMidiStatus(d.ports);
  restoreDump();
});
// Accept full state snapshots as well as the existing hello/status handshake.
socket.on('state', d => hydrateDump(d));
socket.on('monitor', ev => {
  appendMonitor(ev);
  if (ev.kind === 'rx') handleIncomingMidi(ev.hex);
});
socket.on('dump-progress', p => {
  applyWriteStatus(p);
  if (p.error) { toast('Write failed: ' + p.error, true); return; }
  const pct = p.total ? Math.round(p.sent * 100 / p.total) : 0;
  const elapsed = (p.elapsed_s != null) ? ` · ${p.elapsed_s}s` : '';
  const eta = (!p.done && p.elapsed_s && p.sent > 0)
    ? ` · ETA ${Math.round(p.elapsed_s * (p.total - p.sent) / p.sent)}s`
    : '';
  const status = p.done
    ? `Host send completed — device readback still required (${p.total} frames)`
    : `Writing ${p.sent}/${p.total} (${pct}%)${elapsed}${eta}`;
  toast(status);
  if ($('dump-status')) {
    $('dump-status').textContent = p.done
      ? `${state.presets.length} presets (${p.total} frames) — wrote ${p.elapsed_s}s ago`
      : `writing… ${pct}%${eta}`;
  }
});
socket.on('capture-error', p => { toast('Capture failed: ' + p.error, true); });
socket.on('capture-progress', p => {
  $('dump-status').textContent = `capturing… ${p.count} frames`;
});
socket.on('capture-done', d => {
  toast(`Captured ${d.frame_count} frames, ${d.preset_count} presets`);
  const selectedPreset = d.target && d.target.startsWith('preset:')
    ? Number(d.target.split(':')[1]) : null;
  restoreDump({selectedPreset});
});


renderFCSwitches();   // render the device immediately, even before any dump
loadGlobal();
loadPacing();
refreshPortDropdowns();   // populate the in/out selects + LEDs immediately
// Re-render foot controller if user changes bank size on the Global tab
document.addEventListener('change', e => {
  if (e.target && e.target.id === 'g-bank-size') renderFCSwitches();
});
