'use strict';
/* DataMiner · interfaz. Sin dependencias: enrutado por hash, vistas con mount()/tick(), acciones delegadas por data-act. */

/* ───────── utilidades ───────── */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const n = x => (x || 0).toLocaleString('es');
const fold = s => String(s || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase();
const ic = (name, cls = '') => `<svg class="i ${cls}"><use href="#i-${name}"/></svg>`;
const fmtB = b => b == null ? '—' : b < 1024 ? b + ' B' : b < 1048576 ? (b / 1024).toFixed(0) + ' KB' : b < 1073741824 ? (b / 1048576).toFixed(1) + ' MB' : (b / 1073741824).toFixed(2) + ' GB';
const fmtMB = mb => (mb || 0) > 1000 ? (mb / 1000).toFixed(2) + ' GB' : (mb || 0).toFixed(1) + ' MB';
const fmtD = d => {
  if (!d) return '—';
  const t = new Date(d); if (isNaN(t)) return esc(String(d).slice(0, 16));
  const m = (Date.now() - t) / 6e4;
  if (m < 1) return 'ahora'; if (m < 60) return 'hace ' + (m | 0) + ' min'; if (m < 1440) return 'hace ' + (m / 60 | 0) + ' h';
  if (m < 10080) return 'hace ' + (m / 1440 | 0) + ' d'; return t.toLocaleDateString('es');
};
const fmtT = d => { const t = new Date(d); return !d || isNaN(t) ? '—' : t.toLocaleString('es', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' }); };
const fmtIn = s => s <= 0 ? 'en breve' : s < 3600 ? 'en ' + Math.ceil(s / 60) + ' min' : s < 86400 ? 'en ' + Math.round(s / 3600) + ' h' : 'en ' + Math.round(s / 86400) + ' d';
const host = u => { try { return new URL(u).hostname.replace(/^www\./, ''); } catch (e) { return ''; } };
const dayLabel = d => new Date(d + 'T12:00:00').toLocaleDateString('es', { day: 'numeric', month: 'short' });
const api = async (u, o) => {
  const r = await fetch(u, o && o.body && typeof o.body !== 'string' ? { ...o, body: JSON.stringify(o.body) } : o);
  const j = await r.json().catch(() => ({ error: 'Respuesta no válida del servidor' }));
  if (!r.ok && !j.error) j.error = 'Error ' + r.status;
  return j;
};
const post = (u, body) => api(u, { method: 'POST', body: body || {} });
const debounce = (f, ms) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => f(...a), ms); }; };

const KIND = { documento: 'Documentos', datos: 'Datos', archivo: 'Comprimidos', software: 'Software', 'código': 'Código', imagen: 'Imágenes', multimedia: 'Multimedia', otro: 'Otros' };
const RUN_ST = { running: ['info', 'En curso', 'loader'], starting: ['info', 'Iniciando', 'loader'], done: ['good', 'Completada', 'check'], stopped: ['', 'Detenida', 'pause'], interrupted: ['warn', 'Interrumpida', 'alert'] };
const SRC_ST = { ok: ['good', 'Operativa', 'check'], empty: ['warn', 'Sin resultados', 'alert'], error: ['crit', 'Con error', 'error'], running: ['info', 'En curso', 'loader'], pending: ['', 'Pendiente', 'clock'], unused: ['', 'Sin usar', 'clock'] };
const XL = { stars: 'Estrellas', points: 'Puntos', comments: 'Comentarios', lang: 'Idioma', publisher: 'Medio', type: 'Tipo', doi: 'DOI', domain: 'Dominio', country: 'País', mediatype: 'Formato', catalog: 'Catálogo', org: 'Organización', channel: 'Canal', views: 'Vistas', likes: 'Me gusta', downloads: 'Descargas', score: 'Puntuación', answers: 'Respuestas', state: 'Estado', weekly: 'Descargas/sem.', instance: 'Instancia', reblogs: 'Impulsos', favs: 'Favoritos', reposts: 'Reposts', cited: 'Citas', published: 'Publicado', edited: 'Editado', rank: 'Posición' };
const TEXT_EXT = /^(txt|md|py|sh|ps1|yaml|yml|toml|ini|xml|sql|log|tex|bib|js|html?|svg)$/;
const SINCE = [['6h', '6 horas'], ['24h', '24 horas'], ['3d', '3 días'], ['7d', '7 días'], ['14d', '14 días'], ['30d', '30 días'], ['90d', '90 días'], ['1y', '1 año'], ['100y', 'Sin límite']];
const sinceLabel = s => !s || s === '-' ? 'Web completa' : s === '100y' ? 'Sin límite de fecha' : 'Últimos ' + esc(s);
const DEPTH = [[0, '0 · solo listar'], [1, '1 · extraer cada resultado'], [2, '2 · seguir enlaces'], [3, '3 · profundo']];
const opts = (list, sel) => list.map(([v, l]) => `<option value="${esc(v)}" ${String(v) === String(sel) ? 'selected' : ''}>${esc(l)}</option>`).join('');
const pill = ([cls, label, icon]) => `<span class="pill ${cls}">${ic(icon, icon === 'loader' ? 'spin' : '')}${esc(label)}</span>`;
const runPill = st => pill(RUN_ST[st] || ['', st, 'info']);
const runDot = st => `<i class="dot ${(RUN_ST[st] || [''])[0]}"></i>`;
const isLive = r => r && ['running', 'starting'].includes(r.status);
const terms = r => (r.terms || [r.id]).join(' · ');
const empty = (icon, title, text, action = '') => `<div class="empty">${ic(icon)}<h3>${esc(title)}</h3><p>${text}</p>${action}</div>`;

const S = { runs: [], sys: null, settings: null, catalog: null, view: null, route: [], metric: 'hits' };

/* ───────── gráficos ───────── */
function drawBars(el, data, unit) {
  if (!el) return;
  const paint = () => {
    const W = el.clientWidth || 600, H = 168, L = 34, B = 22, T = 8, max = Math.max(1, ...data.map(d => d.value));
    const step = max <= 4 ? 1 : Math.ceil(max / 4 / Math.pow(10, Math.floor(Math.log10(max / 4)))) * Math.pow(10, Math.floor(Math.log10(max / 4)));
    const top = Math.ceil(max / step) * step, ph = H - B - T, slot = (W - L) / Math.max(1, data.length), bw = Math.max(2, Math.min(26, slot - 2));
    const every = Math.ceil(data.length / Math.max(1, Math.floor((W - L) / 64)));
    let g = '';
    for (let v = 0; v <= top; v += step) {
      const y = T + ph - ph * v / top;
      g += `<line x1="${L}" x2="${W}" y1="${y}" y2="${y}" stroke="var(--${v ? 'grid' : 'axis'})"/><text class="tk" x="${L - 7}" y="${y + 3.5}" text-anchor="end">${n(v)}</text>`;
    }
    data.forEach((d, i) => {
      const x = L + slot * i + (slot - bw) / 2, h = d.value ? Math.max(2, ph * d.value / top) : 0, y = T + ph - h, r = Math.min(3, bw / 2, h);
      g += `<rect class="col" x="${L + slot * i}" y="${T}" width="${slot}" height="${ph}" data-tip="${esc(n(d.value) + ' ' + unit + '|' + d.label)}"/>`;
      if (h) g += `<path class="bar" d="M${x},${y + h}V${y + r}q0,${-r} ${r},${-r}h${bw - 2 * r}q${r},0 ${r},${r}V${y + h}z"/>`;
      else g += '<path class="bar" d=""/>';
      if (i % every === 0) g += `<text class="tk" x="${L + slot * i + slot / 2}" y="${H - 6}" text-anchor="middle">${esc(d.short || d.label)}</text>`;
    });
    el.innerHTML = `<svg height="${H}" viewBox="0 0 ${W} ${H}" role="img" aria-label="Gráfico de barras: ${esc(unit)}">${g}</svg>`;
  };
  paint();
  if (el._ro) el._ro.disconnect();
  el._ro = new ResizeObserver(debounce(paint, 80)); el._ro.observe(el);
}
function hbars(items, fmt = n, act) {
  const max = Math.max(1, ...items.map(i => i.value));
  return items.map(i => `<${act ? 'button' : 'div'} class="hb" ${act ? `data-act="${act}" data-arg="${esc(i.arg ?? i.label)}"` : ''}>
    <span class="l" title="${esc(i.label)}">${esc(i.label)}</span><span class="v">${fmt(i.value)}${i.note ? ` <span class="mut">· ${esc(i.note)}</span>` : ''}</span>
    <span class="b"><i style="width:${100 * i.value / max}%"></i></span></${act ? 'button' : 'div'}>`).join('');
}
document.addEventListener('mousemove', e => {
  const t = e.target.closest?.('[data-tip]'), tip = $('#tip');
  if (!t) return tip.classList.add('hide');
  const [a, b] = t.dataset.tip.split('|');
  tip.innerHTML = `<b>${esc(a)}</b><span>${esc(b || '')}</span>`;
  tip.classList.remove('hide');
  tip.style.left = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8) + 'px';
  tip.style.top = Math.max(8, e.clientY - tip.offsetHeight - 10) + 'px';
});

/* ───────── capas: avisos, diálogos, panel lateral ───────── */
function toast(msg, kind = 'good') {
  const t = document.createElement('div');
  t.className = 'toast ' + kind;
  t.innerHTML = ic(kind === 'crit' ? 'error' : 'check') + `<span>${esc(msg)}</span>`;
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'crit' ? 6000 : 2800);
}
let veilClose = null;
function openVeil(html, onClose) { const v = $('#veil'); v.innerHTML = html; v.classList.add('on'); veilClose = onClose || null; }
function closeVeil() { const v = $('#veil'); if (!v.classList.contains('on')) return; v.classList.remove('on'); v.innerHTML = ''; const f = veilClose; veilClose = null; f && f(); }
$('#veil').addEventListener('mousedown', e => { if (e.target.id === 'veil') closeVeil(); });
function confirmDlg(title, text, ok = 'Confirmar', danger = false) {
  return new Promise(res => {
    openVeil(`<div class="dlg sm" role="alertdialog" aria-label="${esc(title)}"><header><h2>${esc(title)}</h2></header>
      <div class="bd"><p class="t2" style="margin:14px 0 0">${text}</p></div>
      <footer><button class="btn" data-act="veil-close">Cancelar</button><button class="btn ${danger ? 'danger' : 'pri'}" id="cok">${esc(ok)}</button></footer></div>`, () => res(false));
    $('#cok').onclick = () => { veilClose = null; closeVeil(); res(true); };
    $('#cok').focus();
  });
}
function openDrawer(html) {
  let d = $('#drawer');
  if (!d) { d = document.createElement('aside'); d.className = 'drawer'; d.id = 'drawer'; $('#main').appendChild(d); $('#main').classList.add('split'); }
  d.innerHTML = html; return d;
}
function closeDrawer() { $('#drawer')?.remove(); $('#main').classList.remove('split'); $$('tr.sel').forEach(t => t.classList.remove('sel')); }
const dhead = t => `<header><h3>${esc(t)}</h3><button class="btn ghost icon sm" data-act="drawer-close" aria-label="Cerrar">${ic('x')}</button></header>`;
const kv = o => `<dl class="kv">${Object.entries(o).filter(([, v]) => v != null && v !== '' && v !== '—').map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')}</dl>`;

/* ───────── vista previa ───────── */
const fileUrl = (run, p, dl) => `/api/run/${encodeURIComponent(run)}/file?path=${encodeURIComponent(p)}${dl ? '&dl=1' : ''}`;
const headOf = (run, p, b) => api(`/api/run/${encodeURIComponent(run)}/head?path=${encodeURIComponent(p)}&bytes=${b}`);
function parseCSV(t, sep) {
  const rows = []; let row = [], f = '', q = false;
  for (let i = 0; i < t.length; i++) {
    const c = t[i];
    if (q) { if (c === '"') { if (t[i + 1] === '"') { f += '"'; i++; } else q = false; } else f += c; }
    else if (c === '"') q = true; else if (c === sep) { row.push(f); f = ''; }
    else if (c === '\n') { row.push(f); rows.push(row); row = []; f = ''; if (rows.length > 200) break; } else if (c !== '\r') f += c;
  }
  if (f || row.length) { row.push(f); rows.push(row); } return rows;
}
function mdRender(t) {
  t = (t || '').replace(/^---[\s\S]*?\n---\s*/, ''); let h = esc(t);
  h = h.replace(/^### (.*)$/gm, '<h3>$1</h3>').replace(/^## (.*)$/gm, '<h2>$1</h2>').replace(/^# (.*)$/gm, '<h1>$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`\n]+)`/g, '<code>$1</code>')
    .replace(/^[-*] (.*)$/gm, '<li>$1</li>').replace(/(<li>.*<\/li>\n?)+/g, m => `<ul>${m}</ul>`);
  return h.split(/\n{2,}/).map(b => /^<(h\d|ul)/.test(b) ? b : `<p>${b.replace(/\n/g, '<br>')}</p>`).join('');
}
const ext_ = (u, l) => `<a class="btn" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${ic('ext')}${l}</a>`;
const openRunBtn = (run, show) => show ? `<button class="btn" data-act="go" data-arg="run/${esc(run)}">${ic('runs')}Ver recolección</button>` : '';
async function previewFile(run, it, link) {
  const ext = (it.ext || '').toLowerCase(), url = fileUrl(run, it.path), name = it.path.split('/').pop();
  openDrawer(dhead(name) + `<div class="pv" id="pv">${empty('loader', 'Cargando vista previa', '')}</div>
    <div class="info">${kv({
    Tipo: `${KIND[it.kind] || esc(it.kind || '—')} · .${esc(ext)}`, 'Tamaño': fmtB(it.size), Fuente: esc(it.source), Recurso: esc(it.title),
    Origen: it.url ? `<a class="link" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${esc(host(it.url))}</a>` : '',
    Descargado: it.fetched ? fmtT(it.fetched) : '', MIME: esc(it.content_type), 'SHA-256': it.sha256 ? `<span class="mono">${esc(it.sha256)}</span>` : '', Ruta: `<span class="mono">${esc(it.path)}</span>`
  })}</div>
    <footer><a class="btn pri" href="${fileUrl(run, it.path, 1)}">${ic('download')}Descargar</a><button class="btn" data-act="reveal" data-run="${esc(run)}" data-arg="${esc(it.path)}">${ic('folder')}Mostrar en carpeta</button>${it.url ? ext_(it.url, 'Origen') : ''}${openRunBtn(run, link)}</footer>`);
  const box = $('#pv');
  try {
    if (it.kind === 'imagen' && ext !== 'svg') box.innerHTML = `<div style="padding:16px"><img src="${url}" alt=""></div>`;
    else if (ext === 'pdf') box.innerHTML = `<iframe src="${url}#view=FitH" title="PDF"></iframe>`;
    else if (/^(mp3|ogg|wav|flac)$/.test(ext)) box.innerHTML = `<div style="padding:24px"><audio controls style="width:100%" src="${url}"></audio></div>`;
    else if (/^(mp4|webm)$/.test(ext)) box.innerHTML = `<video controls src="${url}"></video>`;
    else if (ext === 'zip') {
      const z = await api(`/api/run/${encodeURIComponent(run)}/zip?path=${encodeURIComponent(it.path)}`);
      box.innerHTML = (z.items || []).length ? `<table class="t static"><thead><tr><th>Contenido · ${n(z.count)} entradas</th><th class="num">Tamaño</th></tr></thead><tbody>${z.items.map(e => `<tr><td>${esc(e.name)}</td><td class="num">${fmtB(e.size)}</td></tr>`).join('')}</tbody></table>` : empty('file', 'Sin contenido', esc(z.error || 'El archivo está vacío.'));
    } else if (/^(csv|tsv)$/.test(ext)) {
      const h = await headOf(run, it.path, 300000), l0 = (h.text || '').split('\n')[0] || '';
      const sep = ext === 'tsv' ? '\t' : (l0.split(';').length > l0.split(',').length ? ';' : ','), rows = parseCSV(h.text || '', sep).slice(0, 150);
      box.innerHTML = rows.length ? `<table class="t static"><thead><tr>${rows[0].map(c => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${rows.slice(1).map(r => `<tr>${r.map(c => `<td>${esc(c)}</td>`).join('')}</tr>`).join('')}</tbody></table>` : empty('file', 'Vacío', '');
    } else if (/^(json|jsonl|geojson|ipynb)$/.test(ext)) {
      const h = await headOf(run, it.path, 400000); let t = h.text || '';
      try { if (!h.truncated && ext !== 'jsonl') t = JSON.stringify(JSON.parse(t), null, 2); } catch (e) { }
      box.innerHTML = `<pre>${esc(t.slice(0, 200000))}${h.truncated ? '\n\n… vista parcial' : ''}</pre>`;
    } else if (TEXT_EXT.test(ext)) {
      const h = await headOf(run, it.path, 200000);
      box.innerHTML = ext === 'md' ? `<div class="md">${mdRender(h.text)}</div>` : `<pre>${esc(h.text)}${h.truncated ? '\n\n… vista parcial' : ''}</pre>`;
    } else if (it.md) { const h = await headOf(run, it.md, 200000); box.innerHTML = `<div class="md">${mdRender(h.text)}</div>`; }
    else box.innerHTML = empty('file', 'Sin vista previa', `Los ficheros .${esc(ext)} no se previsualizan ni se ejecutan. Revísalos antes de abrirlos.`);
  } catch (e) { box.innerHTML = empty('alert', 'No se pudo generar la vista previa', ''); }
}
function previewHit(run, it, link) {
  const ex = it.extra || {}, files = ex.files || [], more = {};
  for (const [k, v] of Object.entries(ex)) { if (['files', 'links', 'no_fetch'].includes(k) || v == null || typeof v === 'object') continue; more[XL[k] || esc(k)] = esc(v); }
  openDrawer(dhead(it.title || it.url) + `<div class="pv auto"><div class="md"><p>${esc(it.snippet || 'Sin descripción.')}</p>
    ${files.length ? `<h3>Ficheros asociados (${files.length})</h3><ul>${files.slice(0, 25).map(f => `<li><a class="link" href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">${esc(f.name || f.url)}</a>${f.size ? ' · ' + fmtB(f.size) : ''}</li>`).join('')}</ul>` : ''}</div></div>
    <div class="info free">${kv({ Fuente: it.source ? `<span class="tag">${esc(it.source)}</span>` : '', Publicado: it.date ? fmtT(it.date) + (it.dated === false ? ' <span class="mut">(sin fecha fiable)</span>' : '') : 'Sin fecha', Consulta: it.query ? `<span class="mono">${esc(it.query)}</span>` : '', Dominio: esc(host(it.url)), URL: `<span class="mono">${esc(it.url)}</span>`, ...more })}</div>
    <footer><a class="btn pri" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${ic('ext')}Abrir</a><button class="btn" data-act="copy" data-arg="${esc(it.url)}">${ic('copy')}Copiar enlace</button>${openRunBtn(run, link)}</footer>`);
}
async function previewPage(run, it, link) {
  openDrawer(dhead(it.title || it.url) + `<div class="pv" id="pv">${empty('loader', 'Cargando', '')}</div>
    <div class="info">${kv({ Sitio: esc(it.sitename || host(it.url)), Autor: esc(it.author), Fecha: it.date ? fmtT(it.date) : '', Relevancia: it.relevance, Idioma: esc(it.lang), Nivel: it.depth, Fichero: it.md ? `<span class="mono">${esc(it.md)}</span>` : '' })}</div>
    <footer><a class="btn pri" href="${esc(it.url)}" target="_blank" rel="noopener noreferrer">${ic('ext')}Original</a>${it.md ? `<a class="btn" href="${fileUrl(run, it.md, 1)}">${ic('download')}Markdown</a>` : ''}${openRunBtn(run, link)}</footer>`);
  if (!it.md) { $('#pv').innerHTML = empty('page', 'Sin texto guardado', ''); return; }
  const h = await headOf(run, it.md, 300000); if ($('#pv')) $('#pv').innerHTML = `<div class="md">${mdRender(h.text)}</div>`;
}

/* ───────── navegación ───────── */
const NAV = [['search', 'Buscar', 'search', 'Recursos'], ['library', 'Biblioteca', 'file'], ['runs', 'Búsquedas', 'runs'], ['map', 'Mapa', 'graph'], ['explorer', 'Explorador', 'explore'], ['watches', 'Vigilancias', 'watch'],
  ['panel', 'Panel', 'panel', 'Sistema'], ['sources', 'Fuentes', 'sources'], ['activity', 'Actividad', 'activity'], ['settings', 'Ajustes', 'settings']];
function renderNav() {
  const cur = S.route[0] === 'run' ? 'runs' : S.route[0], live = S.runs.filter(isLive).length;
  $('#nav').innerHTML = NAV.map(([id, label, icon, grp]) => (grp ? `<h6>${grp}</h6>` : '') + `<button class="nav ${cur === id ? 'on' : ''}" data-act="go" data-arg="${id}" title="${label}">${ic(icon)}<span class="lbl">${label}</span>${id === 'runs' ? (live ? `<span class="live"><i class="dot info"></i></span>` : `<span class="c">${S.runs.length || ''}</span>`) : id === 'watches' && S.nWatches ? `<span class="c">${S.nWatches}</span>` : ''
    }</button>`).join('') + (S.runs.length ? `<h6>Recientes</h6>` + S.runs.slice(0, 6).map(r => `<button class="railrun ${S.route[0] === 'run' && S.route[1] === r.id ? 'on' : ''}" data-act="go" data-arg="run/${esc(r.id)}" title="${esc(terms(r))}">
      <b>${runDot(r.status)}<span class="trunc">${esc(terms(r))}</span></b><small>${n(r.hits)} hallazgos · ${fmtD(r.started)}</small></button>`).join('') : '');
  $('#sysline').textContent = live ? `${live} recolección${live > 1 ? 'es' : ''} en curso` : 'Motor local en reposo';
  $('#sysdot').className = 'dot ' + (live ? 'info' : 'good');
}
function go(path) { const h = '#/' + path; if (location.hash === h) route(); else location.hash = h; }
function route() {
  const parts = decodeURI(location.hash.replace(/^#\/?/, '')).split('/').filter(Boolean);
  S.route = parts.length ? parts : ['search'];
  if (S.route[0] === 'new') S.route[0] = 'search';
  closeDrawer();
  if (S.view && S.view.unmount) S.view.unmount();
  const name = S.route[0];
  S.view = VIEWS[name] || VIEWS.search;
  $('#view').scrollTop = 0;
  S.view.mount(...S.route.slice(1));
  renderNav();
}
async function poll() {
  try {
    const rs = await api('/api/runs');
    if (Array.isArray(rs)) { S.runs = rs; renderNav(); }
    if (S.view && S.view.tick && !document.hidden) await S.view.tick();
  } catch (e) { $('#sysline').textContent = 'Sin conexión con el motor'; $('#sysdot').className = 'dot crit'; }
}
const view = html => { const v = $('#view'); v.innerHTML = html; return v; };
const keepScroll = (el, f) => { const t = el ? el.scrollTop : 0; f(); if (el) el.scrollTop = t; };

/* ───────── vistas ───────── */
const VIEWS = {};

/* Panel */
VIEWS.panel = {
  async mount() {
    view(`<div class="phead"><div class="ptitle"><div><h1>Panel</h1><p>Estado general de todo lo recolectado en este equipo.</p></div>
      <div class="actions"><button class="btn" data-act="go" data-arg="watches">${ic('watch')}Vigilancias</button><button class="btn pri" data-act="new">${ic('search')}Buscar recursos</button></div></div></div>
      <div class="scroll pad" id="pbody"><div class="skel" style="width:40%"></div></div>`);
    await this.tick(true);
  },
  async tick(force) {
    const o = await api('/api/overview'), b = $('#pbody'); if (!b || o.error) return;
    const sig = JSON.stringify(o); if (!force && sig === this.sig) return; this.sig = sig;
    if (!o.runs) { b.innerHTML = empty('runs', 'Todavía no hay búsquedas', 'Escribe un término y DataMiner lo buscará en las fuentes abiertas, extraerá las páginas y descargará los recursos que encuentre.', `<button class="btn pri lg" data-act="new">${ic('search')}Buscar recursos</button>`); return; }
    const t = o.totals, src = o.sources, cnt = c => src.filter(s => s.last_class === c).length, bad = src.filter(s => s.last_class !== 'ok');
    const M = { hits: ['Hallazgos', 'hallazgos'], pages: ['Páginas', 'páginas'], files: ['Recursos', 'recursos'] };
    keepScroll(b, () => b.innerHTML = `
      <div class="kpis">
        <div class="kpi"><span>${ic('runs')}Recolecciones</span><b>${n(o.runs)}</b><small>${o.active.length ? o.active.length + ' en curso' : 'ninguna en curso'}</small></div>
        <div class="kpi"><span>${ic('hit')}Hallazgos</span><b>${n(t.hits)}</b><small>resultados únicos</small></div>
        <div class="kpi"><span>${ic('page')}Páginas</span><b>${n(t.pages)}</b><small>texto extraído</small></div>
        <div class="kpi"><span>${ic('file')}Recursos</span><b>${n(t.files)}</b><small>${fmtB(t.bytes)} en disco</small></div>
        <div class="kpi"><span>${ic('net')}Tráfico</span><b>${fmtMB(t.mb)}</b><small>${n(t.requests)} peticiones</small></div>
      </div>
      <div class="grid cols-12" style="margin-top:14px">
        ${o.active.length ? `<div class="card s12"><header><div><h3>En curso</h3></div></header><div class="cb flush"><table class="t"><tbody>${o.active.map(r => `<tr data-act="go" data-arg="run/${esc(r.id)}">
          <td class="w"><div class="cell"><div class="tx"><b>${esc(terms(r))}</b><small>Iniciada ${fmtD(r.started)}</small></div></div></td>
          <td>${runPill(r.status)}</td><td class="num">${n(r.hits)} hallazgos</td><td class="num">${n(r.pages)} páginas</td><td class="num">${n(r.files)} recursos</td><td class="num">${n(r.queue)} en cola</td></tr>`).join('')}</tbody></table></div></div>` : ''}
        <div class="card s8"><header><div><h3>${M[S.metric][0]} de los últimos 14 días</h3><p>Por fecha en que se recogieron</p></div>
          <div class="actions"><div class="seg">${Object.entries(M).map(([k, v]) => `<button class="${S.metric === k ? 'on' : ''}" data-act="metric" data-arg="${k}">${v[0]}</button>`).join('')}</div></div></header>
          <div class="cb"><div class="chart" id="pchart"></div></div></div>
        <div class="card s4"><header><div><h3>Salud de las fuentes</h3><p>Según la última recolección que usó cada una</p></div><div class="actions"><button class="btn ghost sm" data-act="go" data-arg="sources">Ver todas</button></div></header>
          <div class="cb"><div class="row" style="flex-wrap:wrap;gap:6px;margin-bottom:10px">${pill(['good', cnt('ok') + (cnt('ok') === 1 ? ' operativa' : ' operativas'), 'check'])}${pill(['warn', cnt('empty') + ' sin resultados', 'alert'])}${pill(['crit', cnt('error') + ' con error', 'error'])}</div>
          ${bad.length ? `<div class="mut" style="font-size:12px;margin-bottom:4px">Requieren atención</div>` + bad.slice(0, 7).map(s => `<div class="row" style="padding:3px 0"><span class="mono grow trunc">${esc(s.id)}</span>${pill(SRC_ST[s.last_class] || SRC_ST.unused)}</div>`).join('') + (bad.length > 7 ? `<div class="mut" style="font-size:12px;margin-top:4px">y ${bad.length - 7} más</div>` : '') : '<p class="t2" style="margin:0">Todas las fuentes usadas devolvieron resultados.</p>'}</div></div>
        <div class="card s8"><header><div><h3>Recolecciones recientes</h3></div><div class="actions"><button class="btn ghost sm" data-act="go" data-arg="runs">Ver todas</button></div></header>
          <div class="cb flush"><table class="t"><thead><tr><th>Recolección</th><th>Estado</th><th class="num">Hallazgos</th><th class="num">Páginas</th><th class="num">Recursos</th><th class="num">Iniciada</th></tr></thead><tbody>
          ${o.recent.map(r => `<tr data-act="go" data-arg="run/${esc(r.id)}"><td class="w"><div class="cell"><div class="tx"><b>${esc(terms(r))}</b><small>${sinceLabel(r.since)} · profundidad ${esc(r.depth ?? '—')}</small></div></div></td>
            <td>${runPill(r.status)}</td><td class="num">${n(r.hits)}</td><td class="num">${n(r.pages)}</td><td class="num">${n(r.files)}</td><td class="num mut">${fmtD(r.started)}</td></tr>`).join('')}</tbody></table></div></div>
        <div class="card s4"><header><div><h3>Dominios principales</h3><p>Por número de hallazgos</p></div></header><div class="cb flush">${o.domains.length ? hbars(o.domains.map(d => ({ label: d.domain, value: d.n })), n, 'find') : '<p class="mut" style="padding:8px 16px">Sin datos todavía.</p>'}</div></div>
        ${o.kinds.length ? `<div class="card s12"><header><div><h3>Recursos por tipo</h3><p>Ficheros descargados y su tamaño</p></div></header><div class="cb flush">${hbars(o.kinds.map(k => ({ label: KIND[k.kind] || k.kind, value: k.n, note: fmtB(k.bytes) })))}</div></div>` : ''}
      </div>`);
    drawBars($('#pchart'), o.by_day.map(d => ({ label: dayLabel(d.day), value: d[S.metric] })), M[S.metric][1]);
  }
};

/* Recolecciones */
VIEWS.runs = {
  f: { q: '', st: '', sort: 'date' },
  mount() {
    const f = this.f;
    view(`<div class="phead"><div class="ptitle"><div><h1>Búsquedas</h1><p>Cada búsqueda o rastreo que has lanzado, con todo lo que produjo.</p></div>
      <div class="actions"><button class="btn pri" data-act="new">${ic('search')}Buscar recursos</button></div></div></div>
      <div class="toolbar"><label class="search">${ic('search')}<input class="in" id="rq" type="search" placeholder="Filtrar por término…" value="${esc(f.q)}" aria-label="Filtrar"></label>
        <div class="seg" id="rst"></div><span class="grow"></span>
        <select class="in" id="rsort" style="width:auto" aria-label="Orden">${opts([['date', 'Más recientes'], ['hits', 'Más hallazgos'], ['files', 'Más recursos']], f.sort)}</select></div>
      <div class="scroll" id="rlist"></div>`);
    $('#rq').oninput = debounce(e => { f.q = e.target.value; this.tick(); }, 160);
    $('#rsort').onchange = e => { f.sort = e.target.value; this.tick(); };
    this.tick();
  },
  tick() {
    const f = this.f, L = $('#rlist'); if (!L) return;
    const cnt = st => S.runs.filter(r => !st || (st === 'live' ? isLive(r) : st === 'done' ? r.status === 'done' : !isLive(r) && r.status !== 'done')).length;
    $('#rst').innerHTML = [['', 'Todas'], ['live', 'En curso'], ['done', 'Completadas'], ['other', 'Detenidas']].map(([k, l]) => `<button class="${f.st === k ? 'on' : ''}" data-act="runs-st" data-arg="${k}">${l} <i>${cnt(k)}</i></button>`).join('');
    let rs = S.runs.filter(r => (!f.q || fold(terms(r)).includes(fold(f.q))) && (!f.st || (f.st === 'live' ? isLive(r) : f.st === 'done' ? r.status === 'done' : !isLive(r) && r.status !== 'done')));
    if (f.sort !== 'date') rs = [...rs].sort((a, b) => b[f.sort] - a[f.sort]);
    if (!S.runs.length) { L.innerHTML = empty('runs', 'Todavía no hay búsquedas', 'Lanza la primera para empezar a reunir resultados.', `<button class="btn pri" data-act="new">${ic('search')}Buscar recursos</button>`); return; }
    if (!rs.length) { L.innerHTML = empty('search', 'Ninguna recolección coincide', 'Prueba con otro término o quita el filtro de estado.'); return; }
    keepScroll(L, () => L.innerHTML = `<table class="t"><thead><tr><th>Recolección</th><th>Estado</th><th>Tipo</th><th class="num">Hallazgos</th><th class="num">Páginas</th><th class="num">Recursos</th><th class="num">Tráfico</th><th class="num">Iniciada</th></tr></thead><tbody>
      ${rs.map(r => `<tr data-act="go" data-arg="run/${esc(r.id)}"><td class="w"><div class="cell"><div class="tx"><b>${esc(terms(r))}</b><small class="mono">${esc(r.id)}</small></div></div></td>
        <td>${runPill(r.status)}</td><td><span class="tag">${r.since && r.since !== '-' ? 'término' : 'web completa'}</span></td>
        <td class="num">${n(r.hits)}</td><td class="num">${n(r.pages)}</td><td class="num">${n(r.files)}</td><td class="num">${fmtMB(r.mb)}</td><td class="num mut">${fmtD(r.started)}</td></tr>`).join('')}</tbody></table>
      <div class="pager"><span>${n(rs.length)} de ${n(S.runs.length)} recolecciones</span></div>`);
  }
};

/* Detalle de una recolección */
const R = { id: null, info: null, tab: 'summary', q: '', kind: '', source: '', domain: '', sort: 'date', off: 0, items: [] };
VIEWS.run = {
  async mount(id, tab) {
    Object.assign(R, { id, tab: tab || 'summary', q: '', kind: '', source: '', domain: '', sort: 'date', off: 0, items: [] });
    R.info = await api(`/api/run/${encodeURIComponent(id)}/info`);
    if (R.info.error) { view(`<div class="scroll pad">${empty('alert', 'Esta recolección ya no existe', 'Puede que se haya eliminado.', `<button class="btn" data-act="go" data-arg="runs">Volver a recolecciones</button>`)}</div>`); return; }
    view(`<div class="phead"><div class="crumb"><button data-act="go" data-arg="runs">Recolecciones</button>${ic('chev')}<span class="mono trunc">${esc(id)}</span></div><div id="rhead"></div><div class="tabs" id="rtabs" role="tablist"></div></div>
      <div class="toolbar" id="rtb"></div><div class="scroll" id="rbody"></div>`);
    this.head(); this.toolbar(); await this.body(false);
  },
  head() {
    const r = R.info, H = $('#rhead'); if (!H) return;
    H.innerHTML = `<div class="ptitle"><div class="grow"><h1>${esc(terms(r))}</h1>
      <div class="meta">${runPill(r.status)}<span>${ic('clock')}${sinceLabel(r.since)}</span><span>${ic('depth')}Profundidad ${esc(r.depth ?? '—')}</span><span>${ic('play')}Iniciada ${fmtT(r.started)}</span><span>${ic('net')}${fmtMB(r.mb)}</span>
      ${r.disk_limit ? pill(['warn', 'Límite de disco alcanzado', 'alert']) : ''}</div></div>
      <div class="actions">${r.alive ? `<button class="btn danger" data-act="run-stop">${ic('stop')}Detener</button>` : r.status !== 'done' ? `<button class="btn" data-act="run-resume">${ic('play')}Reanudar</button>` : ''}
        <button class="btn" data-act="reveal" data-run="${esc(r.id)}" data-arg="">${ic('folder')}Carpeta</button>
        <button class="btn" data-act="go" data-arg="map/${esc(r.id)}">${ic('graph')}Mapa</button>
        <a class="btn" href="${fileUrl(r.id, 'results.csv', 1)}">${ic('download')}CSV</a><a class="btn" href="${fileUrl(r.id, 'summary.md', 1)}">${ic('page')}Informe</a>
        <button class="btn danger icon" data-act="run-delete" title="Eliminar recolección" aria-label="Eliminar recolección">${ic('trash')}</button></div></div>`;
    const T = [['summary', 'Resumen'], ['files', 'Recursos', r.files], ['results', 'Hallazgos', r.hits], ['pages', 'Páginas', r.pages], ['answer', 'Respuesta'], ['sources', 'Fuentes', Object.keys(r.source_status || r.per_source || {}).length], ['log', 'Registro']];
    $('#rtabs').innerHTML = T.map(([k, l, c]) => `<button class="tab ${k === R.tab ? 'on' : ''}" role="tab" data-act="run-tab" data-arg="${k}">${l}${c != null ? `<i>${n(c)}</i>` : ''}</button>`).join('');
  },
  toolbar() {
    const tb = $('#rtb'); if (!tb) return;
    const list = ['files', 'results', 'pages'].includes(R.tab);
    tb.classList.toggle('hide', !list); if (!list) return;
    const srcs = Object.keys(R.info.per_source || {}).sort();
    tb.innerHTML = `<label class="search">${ic('search')}<input class="in" id="lq" type="search" placeholder="Buscar en ${R.tab === 'files' ? 'recursos' : R.tab === 'pages' ? 'páginas' : 'hallazgos'}…" value="${esc(R.q)}" aria-label="Buscar"></label>
      ${R.tab === 'files' ? '<div class="chips" id="lkinds"></div>' : ''}${R.domain ? `<button class="chip on" data-act="run-domain" data-arg="">${esc(R.domain)} ${ic('x')}</button>` : ''}<span class="grow"></span>
      <select class="in" id="lsrc" style="width:auto" aria-label="Fuente"><option value="">Todas las fuentes</option>${srcs.map(s => `<option ${s === R.source ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select>
      <select class="in" id="lsort" style="width:auto" aria-label="Orden">${opts([['date', 'Más recientes'], ...(R.tab === 'files' ? [['size', 'Más grandes']] : []), ...(R.tab === 'pages' ? [['relevance', 'Más relevantes']] : [])], R.sort)}</select>`;
    $('#lq').oninput = debounce(e => { R.q = e.target.value; R.off = 0; this.body(false); }, 200);
    $('#lsrc').onchange = e => { R.source = e.target.value; R.off = 0; this.body(false); };
    $('#lsort').onchange = e => { R.sort = e.target.value; R.off = 0; this.body(false); };
  },
  async tick() {
    if (!R.info || R.info.error) return;
    const was = isLive(R.info), i = await api(`/api/run/${encodeURIComponent(R.id)}/info`);
    if (i.error) return;
    const changed = JSON.stringify([i.status, i.alive, i.hits, i.pages, i.files, i.queue]) !== JSON.stringify([R.info.status, R.info.alive, R.info.hits, R.info.pages, R.info.files, R.info.queue]);
    R.info = i;
    if (changed || was) { this.head(); if (!$('#drawer') || R.tab === 'log') await this.body(true); }
  },
  async body(soft) {
    const L = $('#rbody'); if (!L) return;
    const id = encodeURIComponent(R.id);
    L.classList.toggle('pad', !['files', 'results', 'pages'].includes(R.tab));
    if (R.tab === 'summary') return this.summary(L, soft);
    if (R.tab === 'sources') return this.sources(L);
    if (R.tab === 'answer') return soft ? null : this.answer(L);
    if (R.tab === 'log') {
      const r = await api(`/api/run/${id}/log`), end = L.scrollTop + L.clientHeight >= L.scrollHeight - 40;
      L.innerHTML = `<div class="card"><div class="cb"><pre class="log">${esc(r.log || 'Sin actividad todavía.')}</pre></div></div>`;
      if (!soft || end) L.scrollTop = L.scrollHeight; return;
    }
    const lim = 60, r = await api(`/api/run/${id}/${R.tab}?offset=${R.off}&limit=${lim}&q=${encodeURIComponent(R.q)}&source=${encodeURIComponent(R.source)}&sort=${R.sort}&kind=${encodeURIComponent(R.kind)}&domain=${encodeURIComponent(R.domain)}`);
    if (r.error) { L.innerHTML = empty('alert', 'No se pudo cargar', esc(r.error)); return; }
    R.items = r.items;
    if (R.tab === 'files' && $('#lkinds')) {
      const ks = Object.entries(r.kinds || {}).sort((a, b) => b[1] - a[1]), tot = ks.reduce((s, x) => s + x[1], 0);
      $('#lkinds').innerHTML = ks.length ? `<button class="chip ${!R.kind ? 'on' : ''}" data-act="run-kind" data-arg="">Todo <i>${n(tot)}</i></button>` + ks.map(([k, c]) => `<button class="chip ${R.kind === k ? 'on' : ''}" data-act="run-kind" data-arg="${esc(k)}">${KIND[k] || esc(k)} <i>${n(c)}</i></button>`).join('') : '';
    }
    if (!r.items.length) {
      const live = isLive(R.info), filtered = R.q || R.kind || R.source || R.domain;
      const why = R.tab === 'files' ? 'Esta recolección no descargó ningún fichero.' : R.tab === 'pages' ? 'No se extrajo el texto de ninguna página. En la pestaña Resumen verás por qué se descartaron.' : 'Ninguna fuente devolvió resultados dentro de la ventana.';
      L.innerHTML = live ? empty('loader', 'Recolectando', 'Los elementos aparecerán aquí en cuanto lleguen.') : filtered ? empty('search', 'Nada coincide con el filtro', 'Prueba a quitar filtros o a buscar otra cosa.') : empty(R.tab === 'files' ? 'file' : R.tab === 'pages' ? 'page' : 'hit', 'Nada que mostrar', why);
      return;
    }
    let rows;
    if (R.tab === 'files') rows = `<table class="t"><thead><tr><th>Nombre</th><th>Tipo</th><th class="num">Tamaño</th><th>Fuente</th><th class="num">Descargado</th></tr></thead><tbody>` +
      r.items.map((f, i) => `<tr data-act="pv" data-i="${i}"><td class="w"><div class="cell"><span class="ext">${esc((f.ext || '?').slice(0, 4))}</span><div class="tx"><b>${esc(f.path.split('/').pop())}</b><small>${esc(f.title || host(f.url))}</small></div></div></td>
        <td><span class="tag">${KIND[f.kind] || esc(f.kind || '—')}</span></td><td class="num">${fmtB(f.size)}</td><td class="mut">${esc(f.source || host(f.url))}</td><td class="num mut">${fmtD(f.fetched)}</td></tr>`).join('') + '</tbody></table>';
    else if (R.tab === 'results') rows = `<table class="t"><thead><tr><th>Hallazgo</th><th>Fuente</th><th class="num">Publicado</th></tr></thead><tbody>` +
      r.items.map((h, i) => `<tr data-act="pv" data-i="${i}"><td class="w"><div class="cell"><div class="tx"><b>${esc(h.title || h.url)}</b><small>${esc(host(h.url))}${h.snippet ? ' — ' + esc(h.snippet.slice(0, 170)) : ''}</small></div></div></td>
        <td><span class="tag">${esc(h.source)}</span></td><td class="num mut">${h.dated === false ? 'sin fecha' : fmtD(h.date)}</td></tr>`).join('') + '</tbody></table>';
    else rows = `<table class="t"><thead><tr><th>Página</th><th class="num">Relevancia</th><th class="num">Texto</th><th class="num">Fecha</th></tr></thead><tbody>` +
      r.items.map((p, i) => `<tr data-act="pv" data-i="${i}"><td class="w"><div class="cell"><div class="tx"><b>${esc(p.title || p.url)}</b><small>${esc(p.sitename || host(p.url))}${p.author ? ' · ' + esc(p.author) : ''}</small></div></div></td>
        <td class="num">${n(p.relevance)}</td><td class="num">${n(p.text_len)} car.</td><td class="num mut">${fmtD(p.date)}</td></tr>`).join('') + '</tbody></table>';
    const pages = Math.max(1, Math.ceil(r.total / lim)), pg = Math.floor(R.off / lim) + 1, st = L.scrollTop;
    L.innerHTML = rows + `<div class="pager"><span>${n(r.total)} elementos${R.tab === 'files' ? ' · ' + fmtB(r.bytes) : ''}</span>
      <span class="row"><button class="btn sm" ${R.off <= 0 ? 'disabled' : ''} data-act="run-page" data-arg="-1">Anterior</button><span>${pg} / ${pages}</span><button class="btn sm" ${pg >= pages ? 'disabled' : ''} data-act="run-page" data-arg="1">Siguiente</button></span></div>`;
    L.scrollTop = soft ? st : 0;
  },
  async summary(L, soft) {
    const id = encodeURIComponent(R.id), r = R.info;
    const [x, qh] = await Promise.all([api(`/api/run/${id}/insights`), api(`/api/run/${id}/head?path=query.json&bytes=200000`)]);
    if (x.error || R.tab !== 'summary') return;
    let q = {}; try { q = JSON.parse(qh.text || '{}'); } catch (e) { }
    const f = x.funnel, steps = [['Candidatos', f.raw, 'devueltos por las fuentes'], ['Hallazgos', f.kept, 'aceptados y únicos'], ['Páginas', f.pages, 'con texto extraído'], ['Recursos', f.files, 'ficheros descargados']];
    const disc = Object.entries(x.discard).filter(([, v]) => v).map(([k, v]) => ({ label: k, value: v })).sort((a, b) => b.value - a.value);
    const st = L.scrollTop;
    L.innerHTML = `<div class="kpis">
        <div class="kpi"><span>${ic('hit')}Hallazgos</span><b>${n(r.hits)}</b><small>${n(x.n_domains)} dominios</small></div>
        <div class="kpi"><span>${ic('page')}Páginas</span><b>${n(r.pages)}</b><small>texto extraído</small></div>
        <div class="kpi"><span>${ic('file')}Recursos</span><b>${n(r.files)}</b><small>${x.kinds.length ? fmtB(x.kinds.reduce((s, k) => s + k.bytes, 0)) : 'ninguno descargado'}</small></div>
        <div class="kpi"><span>${ic('clock')}${r.alive ? 'En cola' : 'Pendientes'}</span><b>${n(r.queue)}</b><small>URL por visitar</small></div>
        <div class="kpi"><span>${ic('net')}Tráfico</span><b>${fmtMB(r.mb)}</b><small>${n(r.requests)} peticiones</small></div></div>
      <div class="grid cols-12" style="margin-top:14px">
        <div class="card s7"><header><div><h3>Embudo de la recolección</h3><p>Cuánto sobrevive en cada etapa</p></div></header><div class="cb"><div class="funnel">
          ${steps.map(([l, v, d], i) => `<div class="fstep"><div><b>${l}</b><br><small>${d}</small></div><div class="b"><i style="width:${f.raw ? 100 * v / f.raw : 0}%;min-width:${v ? 2 : 0}px"></i></div><div class="num"><b>${n(v)}</b>${i && steps[i - 1][1] ? `<br><small>${Math.round(100 * v / steps[i - 1][1])} % del anterior</small>` : ''}</div></div>`).join('')}</div></div></div>
        <div class="card s5"><header><div><h3>Descartes</h3><p>Lo que se filtró y por qué</p></div></header><div class="cb flush">${disc.length ? hbars(disc) : '<p class="mut" style="padding:8px 16px;margin:0">No se descartó nada.</p>'}</div></div>
        <div class="card s7"><header><div><h3>Hallazgos por fecha de publicación</h3><p>${x.undated ? n(x.undated) + ' hallazgos sin fecha fiable quedan fuera del gráfico' : 'Todos los hallazgos tienen fecha'}</p></div></header><div class="cb">${x.by_day.length ? '<div class="chart" id="rchart"></div>' : '<p class="mut" style="margin:0">Sin hallazgos fechados.</p>'}</div></div>
        <div class="card s5"><header><div><h3>Dominios principales</h3><p>Pulsa uno para ver sus hallazgos</p></div></header><div class="cb flush">${x.domains.length ? hbars(x.domains.map(d => ({ label: d.domain, value: d.n })), n, 'run-domain') : '<p class="mut" style="padding:8px 16px;margin:0">Sin datos.</p>'}</div></div>
        <div class="card s6"><header><div><h3>Fuentes con resultados</h3><p>Hallazgos aceptados por fuente</p></div><div class="actions"><button class="btn ghost sm" data-act="run-tab" data-arg="sources">Detalle</button></div></header><div class="cb flush">${x.by_source.length ? hbars(x.by_source.map(s => ({ label: s.source, value: s.n }))) : '<p class="mut" style="padding:8px 16px;margin:0">Ninguna fuente aportó hallazgos.</p>'}</div></div>
        <div class="card s6"><header><div><h3>Parámetros</h3><p>Cómo se lanzó esta recolección</p></div></header><div class="cb">${kv({
      'Términos': (q.terms || r.terms || []).map(t => `<span class="tag">${esc(t)}</span>`).join(' '), Ventana: q.since && q.since !== '-' ? sinceLabel(q.since) : '', 'Desde': q.cutoff ? fmtT(q.cutoff) : '',
      Profundidad: esc(q.depth ?? r.depth), Idiomas: esc((q.langs || []).join(', ')), Fuentes: n((q.sources || r.sources || []).length), 'Consultas': q.variants ? n(q.variants.length) + ' variantes' : ''
    })}
          ${q.variants && q.variants.length ? `<details class="adv"><summary>Ver las ${q.variants.length} consultas generadas</summary><pre class="log" style="margin-top:8px">${esc(q.variants.join('\n'))}</pre></details>` : ''}</div></div>
        ${x.kinds.length ? `<div class="card s6"><header><div><h3>Recursos por tipo</h3></div></header><div class="cb flush">${hbars(x.kinds.map(k => ({ label: KIND[k.kind] || k.kind, value: k.n, note: fmtB(k.bytes) })))}</div></div>` : ''}
        ${x.top_pages.length ? `<div class="card s6"><header><div><h3>Páginas más relevantes</h3></div></header><div class="cb flush">${hbars(x.top_pages.map(p => ({ label: p.title || p.url, value: p.relevance })))}</div></div>` : ''}
      </div>`;
    if (soft) L.scrollTop = st;
    drawBars($('#rchart'), x.by_day.map(d => ({ label: dayLabel(d.day), value: d.n })), 'hallazgos');
  },
  async answer(L, q) {
    const a = await api(`/api/run/${encodeURIComponent(R.id)}/answer?q=${encodeURIComponent(q || '')}`);
    if (a.error || R.tab !== 'answer') return;
    R.answer = a;
    L.innerHTML = `<div class="ans"><form class="bigsearch" id="aform">${ic('search')}<input id="aq" value="${esc(a.question)}" placeholder="Pregunta o términos" aria-label="Pregunta" autocomplete="off"><button class="btn pri">Buscar en lo recolectado</button></form>
      <div class="banner" style="margin:14px 0">${ic('info')}<div><b>Extractos literales, no un texto generado</b><p>Son los párrafos de las páginas, documentos y resúmenes de esta búsqueda que mejor casan con la pregunta, cada uno con su fuente. Se revisaron ${n(a.scanned)} fragmentos${a.scanned && !a.passages.length ? ' y ninguno contiene los términos' : ''}.</p></div>
        ${a.passages.length ? `<button class="btn sm" data-act="ans-copy">${ic('copy')}Copiar en Markdown</button>` : ''}</div>
      ${a.passages.length ? a.passages.map(p => `<blockquote class="pas"><p>${esc(p.text)}</p><footer><a class="link" href="${esc(a.sources[p.ref - 1].url)}" target="_blank" rel="noopener noreferrer">[${p.ref}] ${esc(a.sources[p.ref - 1].title)}</a><span class="mut"> · ${esc(a.sources[p.ref - 1].site)} · ${esc(a.sources[p.ref - 1].kind)}</span></footer></blockquote>`).join('')
        + `<h3 style="font-size:13.5px;margin:22px 0 8px">Fuentes</h3><ol class="srcs">${a.sources.map(s => `<li><a class="link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.title)}</a><span class="mut"> · ${esc(s.site)}${s.date ? ' · ' + fmtT(s.date) : ''}</span></li>`).join('')}</ol>`
        : empty('search', 'Sin extractos', R.info.pages ? 'Prueba con otras palabras.' : 'Esta búsqueda solo listó resultados. Con «Descargar los ficheros» o «seguir enlaces» habrá texto de página que citar.')}</div>`;
    $('#aform').onsubmit = e => { e.preventDefault(); this.answer(L, $('#aq').value); };
  },
  sources(L) {
    const ps = R.info.per_source || {}, ss = R.info.source_status || {}, names = [...new Set([...Object.keys(ss), ...Object.keys(ps)])];
    names.sort((a, b) => ((ps[b] || {}).kept || 0) - ((ps[a] || {}).kept || 0) || a.localeCompare(b));
    if (!names.length) { L.innerHTML = empty('sources', 'Sin fuentes todavía', 'Aparecerán en cuanto arranque la búsqueda.'); return; }
    L.innerHTML = `<div class="card"><div class="cb flush"><table class="t static"><thead><tr><th>Fuente</th><th>Estado</th><th>Detalle</th><th class="num">Aceptados</th><th class="num">Duplicados</th><th class="num">Antiguos</th><th class="num">Descartados</th></tr></thead><tbody>
      ${names.map(k => { const v = ps[k] || {}, st = ss[k] || ''; return `<tr><td><span class="mono">${esc(k)}</span></td><td>${pill(SRC_ST[srcClass(st, v.kept)])}</td><td class="mut">${esc(srcDetail(st))}</td><td class="num"><b>${n(v.kept)}</b></td><td class="num">${n(v.dup)}</td><td class="num">${n(v.old)}</td><td class="num">${n(v.irrelevant)}</td></tr>`; }).join('')}</tbody></table></div></div>`;
  }
};
function srcClass(st, kept) { if (!st || st === 'pendiente') return 'pending'; if (st === 'running') return 'running'; if (st.startsWith('error')) return 'error'; if (st.startsWith('timeout')) return kept ? 'ok' : 'error'; if (st.includes('0 resultados')) return 'empty'; return 'ok'; }
function srcDetail(st) {
  if (!st || st === 'pendiente' || st === 'running') return '';
  const s = (st.match(/\((\d+)s\)/) || [])[1];
  if (st.startsWith('timeout')) return 'Tiempo agotado' + (s ? ' tras ' + s + ' s' : '');
  if (st.startsWith('error')) return st.slice(6, 110).trim();
  if (st.includes('0 nuevos')) return 'Sin novedades: ya vistos';
  if (st.includes('0 resultados')) return 'Nada en la ventana, o la fuente bloqueó el acceso';
  return s ? 'Respondió en ' + s + ' s' : '';
}

/* Explorador */
VIEWS.explorer = {
  q: '', type: '', res: null,
  mount(q) {
    if (q != null) { try { this.q = decodeURIComponent(q); } catch (e) { this.q = q; } this.type = ''; }
    view(`<div class="phead"><div class="ptitle"><div><h1>Explorador</h1><p>Busca en todo lo que has recolectado: hallazgos, páginas y ficheros de cualquier recolección.</p></div></div></div>
      <div class="toolbar"><label class="search" style="max-width:560px">${ic('search')}<input class="in" id="xq" type="search" placeholder="Título, dominio, texto del resumen, nombre de fichero…" value="${esc(this.q)}" aria-label="Buscar en todo"></label><div class="seg" id="xtypes"></div></div>
      <div class="scroll flush" id="xlist"></div>`);
    const i = $('#xq'); i.oninput = debounce(e => { this.q = e.target.value; this.type = ''; this.run(); }, 220); i.focus(); i.select();
    this.run();
  },
  async run() {
    const L = $('#xlist'); if (!L) return;
    if (this.q.trim().length < 2) { $('#xtypes').innerHTML = ''; L.innerHTML = empty('explore', 'Busca en tu archivo', 'Escribe al menos dos letras. La búsqueda recorre las últimas 100 recolecciones guardadas en este equipo.'); return; }
    const q = this.q, r = await api(`/api/find?q=${encodeURIComponent(q)}&type=${this.type}`);
    if (q !== this.q || !$('#xlist')) return;
    this.res = r; const c = r.counts || {};
    $('#xtypes').innerHTML = [['', 'Todo', r.total], ['results', 'Hallazgos', c.results || 0], ['pages', 'Páginas', c.pages || 0], ['files', 'Recursos', c.files || 0]].map(([k, l, v]) => `<button class="${this.type === k ? 'on' : ''}" data-act="x-type" data-arg="${k}">${l} <i>${n(v)}</i></button>`).join('');
    if (!r.items.length) { L.innerHTML = empty('search', 'Sin coincidencias', `Nada de lo recolectado contiene «${esc(q)}».`, `<button class="btn pri" data-act="new" data-arg="${esc(q)}">${ic('plus')}Recolectar «${esc(q)}»</button>`); return; }
    const I = { results: 'hit', pages: 'page', files: 'file' };
    L.innerHTML = r.items.map((it, i) => `<button class="hit" data-act="x-open" data-i="${i}"><div class="h">${ic(I[it.type])}<b class="trunc">${esc(it.title || it.url)}</b><span class="grow"></span><span class="tag">${esc(it.label)}</span></div>
      ${it.snippet ? `<p>${esc(it.snippet)}</p>` : ''}<small>${esc(host(it.url))}${it.source ? ' · ' + esc(it.source) : ''}${it.size ? ' · ' + fmtB(it.size) : ''} · ${fmtD(it.date)} · <span class="mono">${esc(it.run)}</span></small></button>`).join('')
      + `<div class="pager" style="padding:12px 26px">${r.items.length < (this.type ? c[this.type] : r.total) ? `Se muestran los primeros ${r.items.length}. Afina la búsqueda para ver el resto.` : n(r.items.length) + ' coincidencias'}</div>`;
  }
};

/* Vigilancias */
VIEWS.watches = {
  items: [], sel: null, intervals: {},
  async mount(sel) {
    this.sel = sel || null;
    view(`<div class="phead"><div class="ptitle"><div><h1>Vigilancias</h1><p>Búsquedas guardadas que se repiten solas y te enseñan únicamente lo nuevo.</p></div>
      <div class="actions"><button class="btn pri" data-act="watch-new">${ic('plus')}Nueva vigilancia</button></div></div></div>
      <div class="scroll pad" id="wbody"></div>`);
    await this.tick(true);
  },
  async tick(full) {
    const r = await api('/api/watches'), B = $('#wbody'); if (!B || r.error) return;
    this.items = r.items; this.intervals = r.intervals; S.nWatches = r.items.length; renderNav();
    const now = Date.now() / 1000;
    const cards = r.items.length ? `<div class="wgrid">${r.items.map(w => {
      const li = w.last_info, live = li && li.alive;
      return `<div class="card watch ${this.sel === w.id ? 'on' : ''}" data-act="watch-sel" data-arg="${w.id}">
        <div class="row"><div class="grow"><h3 class="trunc">${esc(w.name)}</h3><div class="mut trunc" style="font-size:12px">${esc([w.term, ...(w.extra || [])].join(' · '))}</div></div>
          ${live ? pill(RUN_ST.running) : w.enabled ? pill(['good', 'Activa', 'check']) : pill(['', 'En pausa', 'pause'])}</div>
        <div class="stats"><div><span>Frecuencia</span><b>${esc(w.interval_label)}</b></div><div><span>Próxima</span><b>${!w.enabled ? '—' : live ? 'en curso' : fmtIn((w.next || 0) - now)}</b></div><div><span>Última</span><b>${li ? fmtD(li.started) : 'nunca'}</b></div></div>
        <div class="row"><button class="btn sm" data-act="watch-run" data-arg="${w.id}" ${live ? 'disabled' : ''}>${ic('play')}Ejecutar ahora</button><button class="btn sm" data-act="watch-toggle" data-arg="${w.id}">${ic(w.enabled ? 'pause' : 'play')}${w.enabled ? 'Pausar' : 'Reanudar'}</button>
          <span class="grow"></span><button class="btn ghost icon sm" data-act="watch-edit" data-arg="${w.id}" title="Editar" aria-label="Editar">${ic('edit')}</button><button class="btn ghost icon sm danger" data-act="watch-delete" data-arg="${w.id}" title="Eliminar" aria-label="Eliminar">${ic('trash')}</button></div></div>`;
    }).join('')}</div>` : empty('watch', 'Sin vigilancias', 'Guarda una búsqueda y DataMiner la repetirá con la frecuencia que elijas. En cada pasada compara con las anteriores y separa lo que no habías visto.', `<button class="btn pri" data-act="watch-new">${ic('plus')}Nueva vigilancia</button>`);
    const sync = S.sys && S.sys.sync.configured;
    const html = `<div class="banner" style="margin-bottom:16px">${ic('info')}<div><b>Las vigilancias se ejecutan mientras DataMiner está abierto</b>
      <p>El programador vive en esta aplicación. Para que sigan corriendo con el equipo apagado hay que vincular esta instalación con el servicio en la nube de DataMiner, que guarda un proceso por instalación. ${sync ? 'Hay una dirección de servicio configurada, pero la sincronización todavía no está disponible en esta versión.' : 'Ese servicio todavía no está disponible; cuando lo esté, se activará desde Ajustes → Sincronización.'}</p></div>
      <button class="btn sm" data-act="go" data-arg="settings/sync">Ver sincronización</button></div>${cards}<div id="wdetail" style="margin-top:16px"></div>`;
    if (full || !$('#wdetail')) B.innerHTML = html;
    else keepScroll(B, () => { const d = $('#wdetail'); const tmp = document.createElement('div'); tmp.innerHTML = html; tmp.querySelector('#wdetail').replaceWith(d); B.replaceChildren(...tmp.childNodes); });
    if (full) this.detail();
  },
  async detail() {
    const D = $('#wdetail'); if (!D) return;
    const w = this.items.find(x => x.id === this.sel); if (!w) { D.innerHTML = ''; return; }
    const r = await api(`/api/watch/${w.id}/news`); if (r.error || !$('#wdetail')) return;
    this.news = r;
    D.innerHTML = `<div class="grid cols-12"><div class="card s8"><header><div><h3>Novedades de «${esc(w.name)}»</h3><p>${r.history.length ? `${n(r.total)} hallazgos que no estaban en pasadas anteriores` : 'Aún no se ha ejecutado'}</p></div>
        ${r.last_run ? `<div class="actions"><button class="btn ghost sm" data-act="go" data-arg="run/${esc(r.last_run)}">Abrir recolección</button></div>` : ''}</header>
        <div class="cb flush">${r.items.length ? `<table class="t"><tbody>${r.items.slice(0, 80).map((h, i) => `<tr data-act="watch-pv" data-i="${i}"><td class="w"><div class="cell"><div class="tx"><b>${esc(h.title || h.url)}</b><small>${esc(host(h.url))}${h.snippet ? ' — ' + esc(h.snippet.slice(0, 150)) : ''}</small></div></div></td><td><span class="tag">${esc(h.source)}</span></td><td class="num mut">${fmtD(h.date)}</td></tr>`).join('')}</tbody></table>` : `<p class="mut" style="padding:8px 16px;margin:0">${r.history.length ? 'La última pasada no trajo nada nuevo.' : 'Pulsa «Ejecutar ahora» para la primera pasada.'}</p>`}</div></div>
      <div class="card s4"><header><div><h3>Historial</h3><p>Pasadas guardadas</p></div></header><div class="cb flush">${r.history.length ? `<table class="t"><thead><tr><th>Pasada</th><th class="num">Total</th><th class="num">Nuevos</th></tr></thead><tbody>${r.history.map(h => `<tr data-act="go" data-arg="run/${esc(h.run)}"><td><div class="row">${runDot(h.status)}${fmtT(h.started)}</div></td><td class="num">${n(h.total)}</td><td class="num"><b>${n(h.new)}</b></td></tr>`).join('')}</tbody></table>` : '<p class="mut" style="padding:8px 16px;margin:0">Sin pasadas todavía.</p>'}</div></div></div>`;
  }
};
function watchDialog(w) {
  const s = S.settings || {}; w = w || { name: '', term: '', extra: [], since: s.since || '7d', depth: 1, scope: 'res', every: 1440, enabled: true };
  const iv = Object.entries(VIEWS.watches.intervals).map(([k, v]) => [k, v]);
  openVeil(`<form class="dlg" id="wform" role="dialog" aria-label="Vigilancia"><header><h2>${w.id ? 'Editar vigilancia' : 'Nueva vigilancia'}</h2><button type="button" class="btn ghost icon sm" data-act="veil-close" aria-label="Cerrar">${ic('x')}</button></header>
    <div class="bd"><label class="field"><span>Qué vigilar</span><input class="in" name="term" required value="${esc(w.term)}" placeholder="p. ej. ransomware sector sanitario" autocomplete="off"></label>
      <div class="g2"><label class="field"><span>Nombre</span><input class="in" name="name" value="${esc(w.name)}" placeholder="Opcional: por defecto, el término" autocomplete="off"></label>
        <label class="field"><span>Términos relacionados</span><input class="in" name="extra" value="${esc((w.extra || []).join(', '))}" placeholder="Separados por comas" autocomplete="off"></label></div>
      <div class="g3"><label class="field"><span>Frecuencia</span><select class="in" name="every">${opts(iv, w.every)}</select></label>
        <label class="field"><span>Ventana de cada pasada</span><select class="in" name="since">${opts(SINCE, w.since)}</select></label>
        <label class="field"><span>Profundidad</span><select class="in" name="depth">${opts(DEPTH, w.depth)}</select></label></div>
      <label class="field"><span>Alcance</span><select class="in" name="scope">${opts([['res', 'Solo fuentes con recursos descargables'], ['all', 'Todas las fuentes: web, noticias, redes y foros']], w.scope)}</select>
        <small>Una ventana algo mayor que la frecuencia evita huecos: lo repetido se descarta al comparar con las pasadas anteriores.</small></label>
      ${w.id ? '' : `<label class="check"><input type="checkbox" name="run_now" checked><span>Ejecutar la primera pasada ahora<small>Si no, empezará cuando venza el primer intervalo.</small></span></label>`}</div>
    <footer><span class="note">Solo se ejecuta con la aplicación abierta.</span><button type="button" class="btn" data-act="veil-close">Cancelar</button><button class="btn pri">${w.id ? 'Guardar cambios' : 'Crear vigilancia'}</button></footer></form>`);
  const f = $('#wform').elements; f.term.focus();
  $('#wform').onsubmit = async e => {
    e.preventDefault();
    const r = await post('/api/watches', { id: w.id, term: f.term.value, name: f.name.value, extra: f.extra.value.split(','), every: +f.every.value, since: f.since.value, depth: +f.depth.value, scope: f.scope.value, enabled: w.enabled, run_now: f.run_now ? f.run_now.checked : false });
    if (r.error) return toast(r.error, 'crit');
    closeVeil(); toast(w.id ? 'Vigilancia actualizada' : 'Vigilancia creada'); VIEWS.watches.sel = r.id;
    if (S.route[0] === 'watches') VIEWS.watches.tick(true); else go('watches');
  };
}

/* Fuentes */
VIEWS.sources = {
  group: '', q: '',
  async mount() {
    view(`<div class="phead"><div class="ptitle"><div><h1>Fuentes</h1><p>El catálogo de dónde busca DataMiner y cómo ha respondido cada fuente en tus recolecciones.</p></div></div></div>
      <div class="scroll pad" id="sbody"><div class="skel" style="width:40%"></div></div>`);
    S.catalog = await api('/api/catalog'); this.paint();
  },
  paint() {
    const B = $('#sbody'), all = S.catalog; if (!B || !Array.isArray(all)) return;
    const c = k => all.filter(s => s.last_class === k).length, groups = [...new Set(all.map(s => s.group))];
    const rows = all.filter(s => (!this.group || s.group === this.group) && (!this.q || fold(s.id + ' ' + s.desc).includes(fold(this.q))));
    const rate = s => { const t = s.kept + s.dup + s.irrelevant + s.old; return t ? s.kept / t : null; };
    B.innerHTML = `<div class="kpis"><div class="kpi"><span>${ic('sources')}Fuentes</span><b>${all.length}</b><small>${groups.length} categorías</small></div>
        <div class="kpi"><span>${ic('check')}Operativas</span><b>${c('ok')}</b><small>devolvieron resultados</small></div>
        <div class="kpi"><span>${ic('alert')}Sin resultados</span><b>${c('empty')}</b><small>vacías o bloqueadas</small></div>
        <div class="kpi"><span>${ic('error')}Con error</span><b>${c('error')}</b><small>fallo o tiempo agotado</small></div>
        <div class="kpi"><span>${ic('clock')}Sin usar</span><b>${c('unused')}</b><small>nunca consultadas</small></div></div>
      <div class="banner warn" style="margin:14px 0">${ic('alert')}<div><b>«Sin resultados» no distingue entre vacío y bloqueado</b><p>El motor marca igual una fuente que no tenía nada reciente y una que rechazó la petición. El estado refleja la última recolección que usó cada fuente, no una comprobación en directo.</p></div></div>
      <div class="row" style="margin-bottom:12px;flex-wrap:wrap"><label class="search" style="max-width:300px">${ic('search')}<input class="in" id="sq" type="search" placeholder="Filtrar fuentes…" value="${esc(this.q)}" aria-label="Filtrar fuentes"></label>
        <div class="chips"><button class="chip ${!this.group ? 'on' : ''}" data-act="src-group" data-arg="">Todas <i>${all.length}</i></button>${groups.map(g => `<button class="chip ${this.group === g ? 'on' : ''}" data-act="src-group" data-arg="${esc(g)}">${esc(g)} <i>${all.filter(s => s.group === g).length}</i></button>`).join('')}</div></div>
      <div class="card"><div class="cb flush"><table class="t static"><thead><tr><th>Fuente</th><th>Categoría</th><th>Acceso</th><th>Último estado</th><th class="num">Usos</th><th class="num">Aceptados</th><th class="num">Aprovechamiento</th></tr></thead><tbody>
      ${rows.map(s => { const r = rate(s); return `<tr><td class="w"><div class="cell"><div class="tx"><b class="mono">${esc(s.id)}</b><small>${esc(s.desc)}</small></div></div></td><td>${esc(s.group)}</td><td><span class="tag">${esc(s.method)}</span></td>
        <td>${pill(SRC_ST[s.last_class] || SRC_ST.unused)}</td><td class="num">${n(s.runs)}</td><td class="num"><b>${n(s.kept)}</b></td><td class="num" ${r != null ? `data-tip="${n(s.kept)} aceptados de ${n(s.kept + s.dup + s.irrelevant + s.old)} devueltos|${n(s.dup)} duplicados · ${n(s.irrelevant)} irrelevantes · ${n(s.old)} antiguos"` : ''}>${r == null ? '<span class="mut">—</span>' : Math.round(r * 100) + ' %'}</td></tr>`; }).join('')}</tbody></table>
      ${rows.length ? '' : empty('search', 'Ninguna fuente coincide', 'Cambia el filtro.')}</div></div>`;
    $('#sq').oninput = debounce(e => { this.q = e.target.value; this.paint(); const i = $('#sq'); i.focus(); i.setSelectionRange(i.value.length, i.value.length); }, 200);
  }
};

/* Actividad */
VIEWS.activity = {
  level: '', q: '', rows: [],
  async mount() {
    view(`<div class="phead"><div class="ptitle"><div><h1>Actividad</h1><p>Registro unificado de las últimas 25 recolecciones. Se actualiza solo.</p></div></div></div>
      <div class="toolbar"><label class="search">${ic('search')}<input class="in" id="aq" type="search" placeholder="Filtrar el registro…" value="${esc(this.q)}" aria-label="Filtrar"></label><div class="seg" id="alv"></div></div>
      <div class="scroll flush" id="alist"></div>`);
    $('#aq').oninput = debounce(e => { this.q = e.target.value; this.paint(); }, 160);
    await this.tick();
  },
  async tick() { const r = await api('/api/activity'); if (Array.isArray(r)) { this.rows = r; this.paint(); } },
  paint() {
    const L = $('#alist'); if (!L) return;
    const c = l => this.rows.filter(r => !l || r.level === l).length;
    $('#alv').innerHTML = [['', 'Todo'], ['warn', 'Avisos'], ['error', 'Errores'], ['ok', 'Hitos']].map(([k, l]) => `<button class="${this.level === k ? 'on' : ''}" data-act="act-level" data-arg="${k}">${l} <i>${c(k)}</i></button>`).join('');
    const rows = this.rows.filter(r => (!this.level || r.level === this.level) && (!this.q || fold(r.msg + ' ' + r.run).includes(fold(this.q))));
    const I = { warn: 'alert', error: 'error', ok: 'check', info: 'info' };
    keepScroll(L, () => L.innerHTML = rows.length ? rows.map(r => `<div class="ev ${r.level}"><time>${new Date(r.t).toLocaleDateString('es', { day: '2-digit', month: 'short' })} ${esc(r.t.slice(11))}</time>${ic(I[r.level])}<span class="m">${esc(r.msg)}</span><button class="btn ghost sm r trunc" data-act="go" data-arg="run/${esc(r.run)}/log" title="${esc(r.run)}">${esc(r.run.replace(/_\d{8}-\d{6}$/, ''))}</button></div>`).join('') : empty('activity', this.rows.length ? 'Nada coincide con el filtro' : 'Sin actividad', this.rows.length ? 'Cambia el nivel o el texto.' : 'Los eventos aparecerán cuando lances una recolección.'));
  }
};

/* Ajustes */
VIEWS.settings = {
  async mount(anchor) {
    const [s, sys] = await Promise.all([api('/api/settings'), api('/api/system')]); S.settings = s; S.sys = sys;
    const theme = localStorage.getItem('dm-theme') || 'system', dens = localStorage.getItem('dm-density') || 'comfortable';
    const SEC = [['defaults', 'Valores por defecto'], ['look', 'Apariencia'], ['storage', 'Almacenamiento'], ['sync', 'Sincronización'], ['about', 'Acerca de']];
    view(`<div class="phead b"><div class="ptitle"><div><h1>Ajustes</h1><p>Preferencias de esta instalación. Se guardan al cambiar cada campo.</p></div></div></div>
      <div class="scroll pad"><div class="setwrap"><div class="setnav">${SEC.map(([k, l]) => `<button class="nav" data-act="set-jump" data-arg="${k}">${l}</button>`).join('')}</div><div>
      <section class="sect" id="set-defaults"><h2>Valores por defecto</h2><p>Con qué arranca cada búsqueda. El tipo de recurso que elijas puede cambiar la antigüedad sola.</p><form class="card" id="setform"><div class="cb">
        <div class="g3"><label class="field" style="margin-top:0"><span>Ventana</span><select class="in" name="since">${opts(SINCE, s.since)}</select></label>
</div>
        <div class="g3"><label class="field"><span>Idiomas</span><input class="in" name="langs" value="${esc(s.langs)}"><small>Locales separados por comas</small></label>
          <label class="field"><span>Máx. páginas</span><input class="in" name="max_pages" type="number" min="1" value="${esc(s.max_pages)}"><small>Tope del rastreo</small></label>
          <label class="field"><span>Máx. por dominio</span><input class="in" name="per_domain" type="number" min="1" value="${esc(s.per_domain)}"><small>Evita que un sitio lo acapare</small></label></div>
        <div class="g3"><label class="field"><span>Límite de disco (GB)</span><input class="in" name="max_gb" type="number" min="0" step="0.5" value="${esc(s.max_gb)}"><small>Por recolección · 0 = sin límite</small></label></div>
        <label class="check"><input type="checkbox" name="images" ${s.images ? 'checked' : ''}><span>Descargar también imágenes<small>Aumenta mucho el volumen en disco.</small></span></label></div></form></section>
      <section class="sect" id="set-look"><h2>Apariencia</h2><p>Solo afecta a este equipo.</p><div class="card"><div class="cb">
        <div class="row" style="justify-content:space-between"><div><b>Tema</b><div class="mut">«Sistema» sigue el modo claro u oscuro de Windows.</div></div><div class="seg">${[['system', 'Sistema'], ['light', 'Claro'], ['dark', 'Oscuro']].map(([k, l]) => `<button class="${theme === k ? 'on' : ''}" data-act="set-theme" data-arg="${k}">${l}</button>`).join('')}</div></div>
        <div class="row" style="justify-content:space-between;margin-top:16px"><div><b>Densidad de las tablas</b><div class="mut">Compacta muestra más filas por pantalla.</div></div><div class="seg">${[['comfortable', 'Cómoda'], ['compact', 'Compacta']].map(([k, l]) => `<button class="${dens === k ? 'on' : ''}" data-act="set-density" data-arg="${k}">${l}</button>`).join('')}</div></div></div></div></section>
      <section class="sect" id="set-storage"><h2>Almacenamiento</h2><p>Todo se guarda en local, en una carpeta por recolección.</p><div class="card"><div class="cb">${kv({ Carpeta: `<span class="mono">${esc(sys.output)}</span>`, Recolecciones: n(sys.runs), 'Espacio usado': fmtB(sys.disk_bytes), 'Espacio libre': sys.disk_free != null ? fmtB(sys.disk_free) : '' })}
        <div class="row" style="margin-top:14px"><button class="btn" data-act="open-output">${ic('folder')}Abrir carpeta</button><button class="btn" data-act="go" data-arg="runs">${ic('runs')}Gestionar recolecciones</button></div></div></div></section>
      <section class="sect" id="set-sync"><h2>Sincronización</h2><p>Vincula esta instalación con el servicio en la nube para que las vigilancias sigan con el equipo apagado.</p>
        <div class="banner" style="margin-bottom:12px">${ic('cloud')}<div><b>Todavía no disponible</b><p>El servicio en la nube aún no está en marcha, así que hoy nada sale de este equipo. Cuando exista, guardará un proceso por instalación, identificado por el código de abajo y sin cuenta de usuario. Puedes dejar anotada la dirección para entonces.</p></div></div>
        <div class="card"><div class="cb">${kv({ 'Estado': pill(['', 'Sin conexión', 'pause']), 'ID de instalación': `<span class="mono">${esc(sys.install_id)}</span> <button class="btn ghost icon sm" data-act="copy" data-arg="${esc(sys.install_id)}" title="Copiar" aria-label="Copiar ID">${ic('copy')}</button>`, 'Programador local': sys.scheduler ? pill(['good', 'En marcha', 'check']) : pill(['warn', 'Detenido', 'alert']) })}
          <label class="field"><span>Dirección del servicio</span><input class="in" id="syncurl" value="${esc(s.sync_url)}" placeholder="https://…" autocomplete="off"><small>Se guarda, pero esta versión no se conecta a ella.</small></label></div></div></section>
      <section class="sect" id="set-about"><h2>Acerca de</h2><p>DataMiner respeta robots.txt y no intenta saltarse captchas, inicios de sesión ni bloqueos.</p><div class="card"><div class="cb">${kv({ 'Aplicación': 'DataMiner ' + esc(sys.build), 'Motor de recolección': esc(sys.engine), Python: esc(sys.python), Entorno: document.documentElement.dataset.shell === 'desktop' ? 'Ventana de escritorio' : 'Navegador' })}
        <div style="margin-top:14px" class="t2"><b>Atajos</b><div class="kv" style="margin-top:8px">${[['Ctrl K', 'Paleta de comandos'], ['Ctrl N', 'Buscar recursos'], ['/', 'Ir al explorador'], ['Esc', 'Cerrar panel o diálogo']].map(([k, l]) => `<dt><kbd class="kbd">${k}</kbd></dt><dd>${l}</dd>`).join('')}</div></div></div></div></section>
      </div></div></div>`);
    const save = async body => { const r = await post('/api/settings', body); if (r.error) return toast(r.error, 'crit'); S.settings = r; toast('Ajustes guardados'); };
    const f = $('#setform');
    f.onchange = () => save({ since: f.since.value, langs: f.langs.value, max_pages: +f.max_pages.value, per_domain: +f.per_domain.value, max_gb: +f.max_gb.value, images: f.images.checked });
    f.onsubmit = e => e.preventDefault();
    $('#syncurl').onchange = e => save({ sync_url: e.target.value.trim() });
    if (anchor) $('#set-' + anchor)?.scrollIntoView();
  }
};

/* ───────── buscar: un término, qué quieres conseguir, y el resto se elige solo ───────── */
const WEB_ENGINES = ['yahoo_web', 'ddg_meta'];
const FT_NOTE = { ok: 'Comprobado: devuelve ficheros de este tipo', partial: 'Parcial: devuelve paquetes, no siempre con esta extensión', untested: 'Sin comprobar en el buscador web', no: 'El buscador web no devolvió nada: para este tipo se usan los catálogos' };
const B = { mode: 'term', term: '', since: null, manualSince: false, presets: null, ft: new Set(), packs: new Set(), sources: new Set(), manualSrc: false, manualFt: false, cfg: null };
function buildQueries() {
  const g = id => $('#' + id), val = id => (g(id)?.value || '').trim();
  const terms = [B.term.trim(), ...val('b-extra').split(',').map(x => x.trim())].filter(Boolean);
  const exact = g('b-exact')?.checked ?? true, out = [];
  const mod = val('b-exclude').split(/[\s,]+/).filter(Boolean).map(w => ' -' + w.replace(/^-/, '')).join('') + (val('b-inurl') ? ' inurl:' + val('b-inurl') : '');
  const sites = [...[...B.packs].flatMap(p => B.cfg.site_packs[p].sites), ...val('b-sites').split(/[\s,]+/).filter(Boolean)];
  for (const t of terms) {
    const e = exact && /\s/.test(t) ? `"${t}"` : t;
    out.push(e + mod);
    if (g('b-intitle')?.checked) out.push(`intitle:${e}${mod}`);
    for (const ft of B.ft) out.push(`${e} filetype:${ft}${mod}`);
    for (const s of sites) out.push(`${e} site:${s}${mod}`);
    if (g('b-opendir')?.checked) out.push(`intitle:"index of" ${e}${mod}`);
  }
  out.push(...val('b-raw').split('\n').map(x => x.trim()).filter(Boolean));
  return [...new Set(out)];
}
VIEWS.search = {
  async mount(prefill) {
    if (!S.settings) S.settings = await api('/api/settings');
    if (!Array.isArray(S.catalog)) S.catalog = await api('/api/catalog');
    if (!B.cfg) B.cfg = await api('/api/builder');
    if (prefill != null) { try { B.term = decodeURIComponent(prefill); } catch (e) { B.term = prefill; } }
    if (!B.presets) { let saved = null; try { saved = JSON.parse(localStorage.getItem('dm-presets')); } catch (e) { } B.presets = new Set(Array.isArray(saved) && saved.length ? saved : ['docs', 'data', 'archives']); this.auto(); }
    const s = S.settings, c = B.cfg;
    view(`<div class="scroll pad"><div class="finder">
      <div class="row" style="justify-content:space-between;margin-bottom:14px"><h1 style="font-size:21px">Buscar recursos</h1>
        <div class="seg"><button class="${B.mode === 'term' ? 'on' : ''}" data-act="b-mode" data-arg="term">Por término</button><button class="${B.mode === 'web' ? 'on' : ''}" data-act="b-mode" data-arg="web">Copiar una web</button></div></div>
      <form id="bterm" class="${B.mode === 'term' ? '' : 'hide'}">
        <div class="bigsearch">${ic('search')}<input id="b-term" placeholder="¿Qué quieres conseguir? Escribe un tema, un título o un nombre" autocomplete="off" value="${esc(B.term)}" aria-label="Término"><button class="btn pri lg" id="b-go">Buscar</button></div>
        <div class="lead">Qué tipo de recurso</div>
        <div class="presets">${c.presets.map(p => `<button type="button" class="preset ${B.presets.has(p.id) ? 'on' : ''}" data-act="b-preset" data-arg="${p.id}" aria-pressed="${B.presets.has(p.id)}">${ic(p.icon)}<span><b>${esc(p.label)}</b><small>${esc(p.desc)}</small></span></button>`).join('')}</div>
        <div class="quick">
          <label>Antigüedad <select class="in" id="b-since">${opts(SINCE, B.since || s.since)}</select></label>
          <label>Al encontrarlos <select class="in" id="b-action"><option value="get">Descargar los ficheros</option><option value="list">Solo listarlos</option><option value="deep">Descargar y seguir enlaces</option></select></label>
          <label>Tamaño máximo <select class="in" id="b-mb">${opts([[100, '100 MB por fichero'], [500, '500 MB por fichero'], [2000, '2 GB por fichero'], [0, 'Sin límite']], 500)}</select></label>
        </div>
        <div class="tpl" id="b-tpl"></div>
        <div class="plan" id="b-plan"></div>
        <details class="tune" id="b-tune"><summary>${ic('settings')}Afinar la búsqueda<span class="mut">operadores, sitios, fuentes y límites</span></summary>
          <div class="tunegrid">
            <section><h4>Coincidencia</h4>
              <label class="check"><input type="checkbox" id="b-exact" checked><span>Frase exacta<small>Entrecomilla los términos de varias palabras.</small></span></label>
              <label class="check"><input type="checkbox" id="b-intitle"><span>En el título<small>Añade una consulta con intitle:</small></span></label>
              <label class="check"><input type="checkbox" id="b-opendir"><span>Directorios abiertos<small>Añade intitle:"index of" para listados de ficheros.</small></span></label>
              <label class="field"><span>Sinónimos y términos relacionados</span><input class="in" id="b-extra" placeholder="Separados por comas" autocomplete="off"></label>
              <label class="field"><span>Excluir palabras</span><input class="in" id="b-exclude" placeholder="p. ej. tutorial curso" autocomplete="off"></label>
              <label class="field"><span>Texto en la URL</span><input class="in" id="b-inurl" placeholder="p. ej. download" autocomplete="off"></label></section>
            <section><h4>Tipo de fichero en el buscador web</h4><p class="mut" style="margin:0 0 8px">El punto indica lo que devolvió el buscador en la última comprobación.</p>
              ${c.filetypes.map(g => `<div class="ftg"><span>${esc(g.group)}</span><div class="chips">${g.items.map(([ft, st]) => `<button type="button" class="chip ft" data-act="b-ft" data-arg="${ft}" data-tip="filetype:${ft}|${FT_NOTE[st]}"><i class="dot ${st === 'ok' ? 'good' : st === 'partial' ? 'warn' : st === 'no' ? 'crit' : ''}"></i>${ft}</button>`).join('')}</div></div>`).join('')}
              <h4 style="margin-top:16px">Dónde buscar</h4>
              <div class="chips">${Object.entries(c.site_packs).map(([k, p]) => `<button type="button" class="chip" data-act="b-pack" data-arg="${k}" data-tip="${esc(p.label)}|site: ${esc(p.sites.join(', '))}">${esc(p.label)}</button>`).join('')}</div>
              <label class="field"><span>Otros sitios</span><input class="in" id="b-sites" placeholder="dominio.com otro.org" autocomplete="off"></label></section>
            <section><h4>Límites</h4>
              <div class="g2"><label class="field" style="margin-top:0"><span>Máx. páginas</span><input class="in" id="b-maxp" type="number" value="${esc(s.max_pages)}"></label><label class="field" style="margin-top:0"><span>Máx. por dominio</span><input class="in" id="b-perd" type="number" value="${esc(s.per_domain)}"></label>
                <label class="field"><span>Disco (GB)</span><input class="in" id="b-gb" type="number" min="0" step="0.5" value="${esc(s.max_gb)}"></label><label class="field"><span>Idiomas</span><input class="in" id="b-langs" value="${esc(s.langs)}"></label></div>
              <label class="check"><input type="checkbox" id="b-one" checked><span>Un solo formato por obra<small>En libros, vídeo y audio baja el mejor formato de cada título, no todas las variantes.</small></span></label>
              <label class="check"><input type="checkbox" id="b-images" ${s.images ? 'checked' : ''}><span>Descargar también imágenes</span></label>
              <label class="check"><input type="checkbox" id="b-keep"><span>Guardar también lo que no mencione el término<small>Más volumen y más ruido.</small></span></label>
              <label class="field"><span>Consultas propias</span><textarea class="in mono" id="b-raw" placeholder='Una por línea, con los operadores que quieras'></textarea></label></section>
          </div>
          <h4 style="margin:18px 0 6px">Fuentes <span class="mut" style="font-weight:400">· se eligen solas según el tipo de recurso; aquí puedes cambiarlas</span></h4>
          <div class="srcpick" id="b-src"></div>
          <h4 style="margin:18px 0 6px">Consultas web que se lanzarán</h4><pre class="log qprev" id="b-queries"></pre>
        </details>
      </form>
      <form id="bweb" class="${B.mode === 'web' ? '' : 'hide'}">
        <label class="field" style="margin-top:0"><span>Direcciones de partida</span><textarea class="in mono" id="w-urls" style="min-height:96px" placeholder="https://ejemplo.com&#10;https://docs.ejemplo.org"></textarea><small>Recorre el dominio completo: páginas, documentos y ficheros enlazados. Respeta robots.txt.</small></label>
        <div class="g3"><label class="field"><span>Profundidad</span><input class="in" id="w-depth" type="number" value="6"></label><label class="field"><span>Máx. páginas</span><input class="in" id="w-max" type="number" value="5000"></label><label class="field"><span>Disco (GB)</span><input class="in" id="w-gb" type="number" min="0" step="0.5" value="${esc(s.max_gb)}"></label></div>
        <label class="check"><input type="checkbox" id="w-images" checked><span>Descargar también imágenes</span></label>
        <div style="margin-top:16px"><button class="btn pri lg">Copiar la web</button></div></form>
      <div id="b-recent"></div></div></div>`);
    const groups = [...new Set(S.catalog.map(x => x.group))];
    $('#b-src').innerHTML = groups.map(g => `<div class="g">${esc(g)}<button type="button" class="btn ghost sm" data-act="b-grp" data-arg="${esc(g)}">Alternar</button></div><div class="o">${S.catalog.filter(x => x.group === g).map(x => `<label title="${esc(x.desc)}"><input type="checkbox" value="${esc(x.id)}" data-g="${esc(g)}">${esc(x.id)}</label>`).join('')}</div>`).join('');
    $('#b-term').oninput = e => { B.term = e.target.value; this.paint(); };
    $('#bterm').oninput = e => { if (!e.target.closest('#b-src')) this.paint(); };
    $('#bterm').onchange = e => { if (e.target.id === 'b-since') { B.manualSince = true; B.since = e.target.value; } if (e.target.closest('#b-src')) { B.manualSrc = true; B.sources = new Set($$('#b-src input:checked').map(x => x.value)); } this.paint(); };
    $('#bterm').onsubmit = e => { e.preventDefault(); this.submit(); };
    $('#bweb').onsubmit = e => { e.preventDefault(); this.submitWeb(); };
    this.paint(); this.recent(); this.tpl();
    (B.mode === 'term' ? $('#b-term') : $('#w-urls')).focus();
  },
  auto() {  // fuentes y tipos de fichero que se derivan de los tipos de recurso elegidos
    const ps = B.cfg.presets.filter(p => B.presets.has(p.id));
    if (!B.manualSrc) B.sources = new Set(ps.flatMap(p => p.sources));
    if (!B.manualFt) B.ft = new Set(ps.flatMap(p => p.filetypes));
    // los catálogos de libros, vídeo y audio no son «recientes»: sin límite de fecha salvo que el usuario elija otra cosa
    B.catalog = ps.some(p => p.catalog);
    if (!B.manualSince) { B.since = B.catalog ? '100y' : (S.settings || {}).since || '7d'; const sel = $('#b-since'); if (sel) sel.value = B.since; }
  },
  paint() {
    if (!$('#b-plan')) return;
    $$('#b-src input').forEach(x => x.checked = B.sources.has(x.value));
    $$('.chip.ft').forEach(x => x.classList.toggle('on', B.ft.has(x.dataset.arg)));
    $$('[data-act="b-pack"]').forEach(x => x.classList.toggle('on', B.packs.has(x.dataset.arg)));
    const q = buildQueries(), web = WEB_ENGINES.some(w => B.sources.has(w)), nq = web ? q.length : 0, src = [...B.sources];
    const byG = {}; for (const x of S.catalog) if (B.sources.has(x.id)) (byG[x.group] = byG[x.group] || []).push(x.id);
    $('#b-queries').textContent = !B.term.trim() ? 'Escribe un término para ver las consultas.' : web ? q.join('\n') : 'Ninguna: no hay un buscador web entre las fuentes. Los catálogos reciben solo el término.';
    $('#b-plan').innerHTML = !B.presets.size && !src.length ? `<span class="pill warn">${ic('alert')}Elige al menos un tipo de recurso</span>` :
      `<div class="row" style="flex-wrap:wrap;gap:6px"><b>${src.length} fuente${src.length === 1 ? '' : 's'}</b>${B.manualSrc ? `<button type="button" class="btn ghost sm" data-act="b-reset">Volver a automático</button>` : '<span class="mut">elegidas automáticamente</span>'}${B.catalog && !B.manualSince ? '<span class="mut">· sin límite de fecha, porque los catálogos de libros, vídeo y audio no son recientes</span>' : ''}
        ${B.term.trim() && web ? `<span class="mut">·</span><b>${nq} consulta${nq === 1 ? '' : 's'} web</b>` : ''}${nq > 14 ? `<span class="pill warn">${ic('alert')}Muchas consultas: el buscador web puede bloquear a partir de unas 14</span>` : ''}</div>
       <div class="planlist">${Object.entries(byG).map(([g, ids]) => `<span><em>${esc(g)}</em> ${ids.map(esc).join(', ')}</span>`).join('')}</div>`;
  },
  async tpl(reload = true) {                         // plantillas: combinaciones de tipos, operadores y límites con nombre
    if (reload || !B.tpls) B.tpls = await api('/api/templates');
    const el = $('#b-tpl'); if (!el || !Array.isArray(B.tpls)) return;
    el.innerHTML = `<span class="mut">Plantillas</span>${B.tpls.map(t => `<span class="chip tp"><button type="button" data-act="b-tpl-use" data-arg="${t.id}">${esc(t.name)}</button><button type="button" data-act="b-tpl-del" data-arg="${t.id}" aria-label="Eliminar plantilla ${esc(t.name)}">${ic('x')}</button></span>`).join('')}<button type="button" class="btn ghost sm" data-act="b-tpl-save">${ic('plus')}Guardar esta configuración</button>`;
  },
  snapshot() {
    const v = id => $('#' + id).value, c = id => $('#' + id).checked;
    return { presets: [...B.presets], ft: [...B.ft], packs: [...B.packs], manualFt: B.manualFt, manualSrc: B.manualSrc, sources: B.manualSrc ? [...B.sources] : null,
      since: B.manualSince ? B.since : null, action: v('b-action'), mb: v('b-mb'), exact: c('b-exact'), intitle: c('b-intitle'), opendir: c('b-opendir'), one: c('b-one'),
      extra: v('b-extra'), exclude: v('b-exclude'), inurl: v('b-inurl'), sites: v('b-sites'), raw: v('b-raw') };
  },
  apply(d) {
    B.presets = new Set(d.presets || []); B.packs = new Set(d.packs || []); B.manualFt = !!d.manualFt; B.manualSrc = !!d.manualSrc; B.manualSince = !!d.since;
    if (d.since) B.since = d.since;
    this.auto(); if (d.manualFt) B.ft = new Set(d.ft || []); if (d.manualSrc && d.sources) B.sources = new Set(d.sources);
    const set = (id, val) => { const e = $('#' + id); if (e && val != null) e.value = val; }, chk = (id, val) => { const e = $('#' + id); if (e && val != null) e.checked = !!val; };
    set('b-since', B.since); set('b-action', d.action); set('b-mb', d.mb); set('b-extra', d.extra); set('b-exclude', d.exclude); set('b-inurl', d.inurl); set('b-sites', d.sites); set('b-raw', d.raw);
    chk('b-exact', d.exact); chk('b-intitle', d.intitle); chk('b-opendir', d.opendir); chk('b-one', d.one);
    $$('.preset').forEach(p => { p.classList.toggle('on', B.presets.has(p.dataset.arg)); p.setAttribute('aria-pressed', B.presets.has(p.dataset.arg)); });
    this.paint();
  },
  recent() {
    const R_ = $('#b-recent'); if (!R_) return;
    R_.innerHTML = S.runs.length ? `<div class="lead" style="margin-top:28px">Búsquedas recientes</div><div class="card"><div class="cb flush"><table class="t"><tbody>${S.runs.slice(0, 6).map(r => `<tr data-act="go" data-arg="run/${esc(r.id)}"><td class="w"><div class="cell"><div class="tx"><b>${esc(terms(r))}</b><small>${sinceLabel(r.since)} · ${fmtD(r.started)}</small></div></div></td><td>${runPill(r.status)}</td><td class="num">${n(r.files)} recursos</td><td class="num">${n(r.hits)} hallazgos</td><td><button type="button" class="btn ghost sm" data-act="b-again" data-arg="${esc((r.terms || [])[0] || '')}">Repetir</button></td></tr>`).join('')}</tbody></table></div></div>` : '';
  },
  tick() { if (!document.activeElement?.closest('#bterm,#bweb')) this.recent(); },
  async submit() {
    if (!B.term.trim()) return $('#b-term').focus();
    if (!B.sources.size) return toast('Elige al menos un tipo de recurso o una fuente', 'crit');
    const v = id => $('#' + id).value, act = v('b-action');
    const body = { term: B.term.trim(), extra: v('b-extra').split(',').map(x => x.trim()).filter(Boolean), queries: buildQueries(), since: v('b-since'), depth: act === 'list' ? 0 : act === 'deep' ? 2 : 1, no_files: act === 'list',
      sources: [...B.sources], langs: v('b-langs'), max_pages: +v('b-maxp'), per_domain: +v('b-perd'), max_gb: +v('b-gb'), max_file_mb: +v('b-mb'), one_format: $('#b-one').checked, images: $('#b-images').checked, keep_all: $('#b-keep').checked };
    $('#b-go').disabled = true;
    const r = await post('/api/search', body);
    if (r.error) { $('#b-go').disabled = false; return toast(r.error, 'crit'); }
    try { localStorage.setItem('dm-presets', JSON.stringify([...B.presets])); } catch (e) { }
    toast('Búsqueda iniciada'); await poll(); go('run/' + r.id);
  },
  async submitWeb() {
    const urls = $('#w-urls').value.split('\n').map(x => x.trim()).filter(Boolean); if (!urls.length) return $('#w-urls').focus();
    const r = await post('/api/crawl', { urls, depth: +$('#w-depth').value, max_pages: +$('#w-max').value, max_gb: +$('#w-gb').value, images: $('#w-images').checked });
    if (r.error) return toast(r.error, 'crit');
    toast('Copia iniciada'); await poll(); go('run/' + r.id);
  }
};
const ACT_EXTRA = {};
Object.assign(ACT_EXTRA, {
  'b-mode': el => { B.mode = el.dataset.arg; VIEWS.search.mount(); },
  'b-preset': el => { const k = el.dataset.arg; B.presets.has(k) ? B.presets.delete(k) : B.presets.add(k); el.classList.toggle('on'); el.setAttribute('aria-pressed', B.presets.has(k)); VIEWS.search.auto(); VIEWS.search.paint(); },
  'b-ft': el => { const k = el.dataset.arg; B.manualFt = true; B.ft.has(k) ? B.ft.delete(k) : B.ft.add(k); VIEWS.search.paint(); },
  'b-pack': el => { const k = el.dataset.arg; B.packs.has(k) ? B.packs.delete(k) : B.packs.add(k); VIEWS.search.paint(); },
  'b-grp': el => { const g = S.catalog.filter(x => x.group === el.dataset.arg).map(x => x.id), on = g.every(x => B.sources.has(x)); g.forEach(x => on ? B.sources.delete(x) : B.sources.add(x)); B.manualSrc = true; VIEWS.search.paint(); },
  'b-reset': () => { B.manualSrc = false; B.manualFt = false; B.manualSince = false; VIEWS.search.auto(); VIEWS.search.paint(); },
  'b-tpl-use': el => { const t = (B.tpls || []).find(x => x.id === el.dataset.arg); if (t) { VIEWS.search.apply(t.data); toast(`Plantilla «${t.name}» aplicada`); } },
  'b-tpl-del': async el => { await post(`/api/template/${el.dataset.arg}/delete`); VIEWS.search.tpl(); },
  'b-tpl-save': () => {
    openVeil(`<form class="dlg sm" id="tform" role="dialog" aria-label="Guardar plantilla"><header><h2>Guardar plantilla</h2></header>
      <div class="bd"><label class="field"><span>Nombre</span><input class="in" name="tname" required maxlength="60" placeholder="p. ej. Informes en PDF de administraciones" autocomplete="off"></label>
        <p class="mut" style="margin:10px 0 0">Guarda los tipos de recurso, los operadores, los sitios y los límites actuales. El término no se guarda.</p></div>
      <footer><button type="button" class="btn" data-act="veil-close">Cancelar</button><button class="btn pri">Guardar</button></footer></form>`);
    const f = $('#tform'); f.elements.tname.focus();
    f.onsubmit = async e => { e.preventDefault(); const r = await post('/api/templates', { name: f.elements.tname.value, data: VIEWS.search.snapshot() }); if (r.error) return toast(r.error, 'crit'); closeVeil(); toast('Plantilla guardada'); VIEWS.search.tpl(); };
  },
  'b-again': el => { B.term = el.dataset.arg; $('#b-term').value = B.term; VIEWS.search.paint(); $('#b-term').focus(); $('#view .scroll').scrollTop = 0; }
});

/* Biblioteca: todos los ficheros descargados, de cualquier búsqueda */
VIEWS.library = {
  f: { q: '', kind: '', sort: 'date', off: 0 }, items: [],
  mount() {
    const f = this.f;
    view(`<div class="phead"><div class="ptitle"><div><h1>Biblioteca</h1><p>Todos los ficheros descargados, de cualquier búsqueda, en un solo sitio.</p></div>
      <div class="actions"><button class="btn" data-act="open-output">${ic('folder')}Abrir carpeta</button><button class="btn pri" data-act="go" data-arg="search">${ic('search')}Buscar recursos</button></div></div></div>
      <div class="toolbar"><label class="search">${ic('search')}<input class="in" id="lbq" type="search" placeholder="Nombre, título u origen…" value="${esc(f.q)}" aria-label="Buscar en la biblioteca"></label><div class="chips" id="lbk"></div><span class="grow"></span>
        <select class="in" id="lbs" style="width:auto" aria-label="Orden">${opts([['date', 'Más recientes'], ['size', 'Más grandes']], f.sort)}</select></div>
      <div class="scroll" id="lbl"></div>`);
    $('#lbq').oninput = debounce(e => { f.q = e.target.value; f.off = 0; this.load(); }, 200);
    $('#lbs').onchange = e => { f.sort = e.target.value; f.off = 0; this.load(); };
    this.load();
  },
  async load() {
    const f = this.f, L = $('#lbl'); if (!L) return;
    const lim = 60, r = await api(`/api/library?offset=${f.off}&limit=${lim}&q=${encodeURIComponent(f.q)}&kind=${encodeURIComponent(f.kind)}&sort=${f.sort}`);
    if (r.error || !$('#lbl')) return;
    this.items = r.items;
    const ks = Object.entries(r.kinds || {}).sort((a, b) => b[1] - a[1]), tot = ks.reduce((s, x) => s + x[1], 0);
    $('#lbk').innerHTML = ks.length ? `<button class="chip ${!f.kind ? 'on' : ''}" data-act="lib-kind" data-arg="">Todo <i>${n(tot)}</i></button>` + ks.map(([k, c]) => `<button class="chip ${f.kind === k ? 'on' : ''}" data-act="lib-kind" data-arg="${esc(k)}">${KIND[k] || esc(k)} <i>${n(c)}</i></button>`).join('') : '';
    if (!r.items.length) { L.innerHTML = tot || f.q ? empty('search', 'Nada coincide', 'Prueba con otro texto o quita el filtro de tipo.') : empty('file', 'La biblioteca está vacía', 'Aquí aparecerán los ficheros que descarguen tus búsquedas.', `<button class="btn pri" data-act="go" data-arg="search">${ic('search')}Buscar recursos</button>`); return; }
    const pages = Math.max(1, Math.ceil(r.total / lim)), pg = Math.floor(f.off / lim) + 1;
    L.innerHTML = `<table class="t"><thead><tr><th>Nombre</th><th>Tipo</th><th class="num">Tamaño</th><th>Fuente</th><th>Búsqueda</th><th class="num">Descargado</th></tr></thead><tbody>${r.items.map((x, i) => `<tr data-act="lib-pv" data-i="${i}"><td class="w"><div class="cell"><span class="ext">${esc((x.ext || '?').slice(0, 4))}</span><div class="tx"><b>${esc(x.path.split('/').pop())}</b><small>${esc(x.title || host(x.url))}</small></div></div></td>
      <td><span class="tag">${KIND[x.kind] || esc(x.kind || '—')}</span></td><td class="num">${fmtB(x.size)}</td><td class="mut">${esc(x.source || host(x.url))}</td><td class="mut trunc" style="max-width:180px">${esc(x.run.replace(/_\d{8}-\d{6}$/, ''))}</td><td class="num mut">${fmtD(x.fetched)}</td></tr>`).join('')}</tbody></table>
      <div class="pager"><span>${n(r.total)} ficheros · ${fmtB(r.bytes)}</span><span class="row"><button class="btn sm" ${f.off <= 0 ? 'disabled' : ''} data-act="lib-page" data-arg="-1">Anterior</button><span>${pg} / ${pages}</span><button class="btn sm" ${pg >= pages ? 'disabled' : ''} data-act="lib-page" data-arg="1">Siguiente</button></span></div>`;
  }
};
Object.assign(ACT_EXTRA, {
  'lib-kind': el => { VIEWS.library.f.kind = el.dataset.arg; VIEWS.library.f.off = 0; VIEWS.library.load(); },
  'lib-page': el => { const f = VIEWS.library.f; f.off = Math.max(0, f.off + 60 * +el.dataset.arg); VIEWS.library.load(); },
  'lib-pv': el => { const it = VIEWS.library.items[+el.dataset.i]; if (!it) return; $$('#lbl tbody tr').forEach(tr => tr.classList.toggle('sel', tr === el)); previewFile(it.run, it, true); }
});

/* ───────── paleta de comandos ───────── */
function palette() {
  const base = [
    ...NAV.map(([id, l, i]) => ({ g: 'Ir a', l, i, f: () => go(id) })),
    
    { g: 'Acciones', l: 'Nueva vigilancia', i: 'watch', f: async () => { if (!Object.keys(VIEWS.watches.intervals).length) VIEWS.watches.intervals = (await api('/api/watches')).intervals; watchDialog(); } },
    { g: 'Acciones', l: 'Cambiar entre tema claro y oscuro', i: 'theme', f: () => ACT.theme() },
    { g: 'Acciones', l: 'Abrir la carpeta de datos', i: 'folder', f: () => ACT['open-output']() },
    ...S.runs.slice(0, 12).map(r => ({ g: 'Recolecciones', l: terms(r), i: 'runs', k: fmtD(r.started), f: () => go('run/' + r.id) }))
  ];
  let items = [], idx = 0;
  openVeil(`<div class="pal" role="dialog" aria-label="Paleta de comandos"><input id="pq" placeholder="Escribe un comando o busca en lo recolectado…" autocomplete="off" aria-label="Comando"><div class="list" id="pl"></div></div>`);
  const paint = () => {
    const q = $('#pq').value.trim(), fq = fold(q);
    items = base.filter(b => !fq || fold(b.l).includes(fq));
    if (q.length >= 2) items.unshift({ g: 'Buscar', l: `Buscar «${q}» en todo lo recolectado`, i: 'search', f: () => go('explorer/' + encodeURIComponent(q)) }, { g: 'Buscar', l: `Buscar recursos sobre «${q}»`, i: 'plus', f: () => go('search/' + encodeURIComponent(q)) });
    idx = Math.min(idx, Math.max(0, items.length - 1));
    let g = '';
    $('#pl').innerHTML = items.map((it, i) => (it.g !== g ? `<h6>${g = it.g}</h6>` : '') + `<button class="it ${i === idx ? 'on' : ''}" data-i="${i}">${ic(it.i)}<span class="trunc">${esc(it.l)}</span>${it.k ? `<small>${esc(it.k)}</small>` : ''}</button>`).join('') || '<div class="empty" style="padding:26px">Ningún comando coincide.</div>';
    $('#pl .it.on')?.scrollIntoView({ block: 'nearest' });
  };
  const run = i => { const it = items[i]; if (!it) return; closeVeil(); it.f(); };
  const inp = $('#pq');
  inp.oninput = () => { idx = 0; paint(); };
  inp.onkeydown = e => { if (e.key === 'ArrowDown') { idx = Math.min(items.length - 1, idx + 1); paint(); e.preventDefault(); } else if (e.key === 'ArrowUp') { idx = Math.max(0, idx - 1); paint(); e.preventDefault(); } else if (e.key === 'Enter') run(idx); };
  $('#pl').onclick = e => { const b = e.target.closest('.it'); if (b) run(+b.dataset.i); };
  paint(); inp.focus();
}

/* ───────── acciones ───────── */
const ACT = {
  go: el => go(el.dataset.arg),
  new: el => go('search' + (el && el.dataset && el.dataset.arg ? '/' + encodeURIComponent(el.dataset.arg) : '')),
  palette, 'veil-close': closeVeil, 'drawer-close': closeDrawer,
  theme() { const r = document.documentElement, dark = r.dataset.theme ? r.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches; ACT['set-theme']({ dataset: { arg: dark ? 'light' : 'dark' } }); },
  'set-theme': el => { const t = el.dataset.arg; localStorage.setItem('dm-theme', t); if (t === 'system') delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t; if (S.route[0] === 'settings') VIEWS.settings.mount(); },
  'set-density': el => { localStorage.setItem('dm-density', el.dataset.arg); document.documentElement.dataset.density = el.dataset.arg; VIEWS.settings.mount(); },
  'set-jump': el => $('#set-' + el.dataset.arg)?.scrollIntoView({ behavior: 'smooth' }),
  metric: el => { S.metric = el.dataset.arg; VIEWS.panel.tick(true); },
  find: el => go('explorer/' + encodeURIComponent(el.dataset.arg)),
  copy: async el => { try { await navigator.clipboard.writeText(el.dataset.arg); toast('Copiado al portapapeles'); } catch (e) { toast('No se pudo copiar', 'crit'); } },
  reveal: el => post(`/api/run/${encodeURIComponent(el.dataset.run)}/open`, { path: el.dataset.arg || '' }),
  'open-output': () => post('/api/open-output'),
  'runs-st': el => { VIEWS.runs.f.st = el.dataset.arg; VIEWS.runs.tick(); },
  'run-tab': el => { Object.assign(R, { tab: el.dataset.arg, off: 0, q: '', kind: '', source: '', domain: '', sort: 'date' }); closeDrawer(); history.replaceState(null, '', `#/run/${R.id}/${R.tab}`); VIEWS.run.head(); VIEWS.run.toolbar(); VIEWS.run.body(false); },
  'run-kind': el => { R.kind = el.dataset.arg; R.off = 0; VIEWS.run.body(false); },
  'run-domain': el => { Object.assign(R, { tab: 'results', domain: el.dataset.arg, off: 0, q: '', source: '' }); VIEWS.run.head(); VIEWS.run.toolbar(); VIEWS.run.body(false); },
  'run-page': el => { R.off = Math.max(0, R.off + 60 * +el.dataset.arg); VIEWS.run.body(false); },
  'run-stop': async () => { await post(`/api/run/${encodeURIComponent(R.id)}/stop`); toast('Recolección detenida'); setTimeout(poll, 900); },
  'run-resume': async () => { const r = await post(`/api/run/${encodeURIComponent(R.id)}/resume`); if (r.error) return toast(r.error, 'crit'); toast('Recolección reanudada'); setTimeout(poll, 1500); },
  'run-delete': async () => {
    if (!await confirmDlg('Eliminar recolección', `Se borrará <b>${esc(terms(R.info))}</b> con todos sus ficheros del disco. No se puede deshacer.`, 'Eliminar', true)) return;
    const r = await post(`/api/run/${encodeURIComponent(R.id)}/delete`); if (r.error) return toast(r.error, 'crit');
    toast('Recolección eliminada'); await poll(); go('runs');
  },
  pv: el => {
    const it = R.items[+el.dataset.i]; if (!it) return;
    $$('#rbody tbody tr').forEach(tr => tr.classList.toggle('sel', tr === el));
    (R.tab === 'files' ? previewFile : R.tab === 'results' ? previewHit : previewPage)(R.id, it);
  },
  'ans-copy': async () => {
    const a = R.answer; if (!a) return;
    const md = `# ${a.question}\n\n` + a.passages.map(p => `> ${p.text} [${p.ref}]`).join('\n\n') + '\n\n## Fuentes\n\n' + a.sources.map((s, i) => `${i + 1}. [${s.title}](${s.url}) · ${s.site}`).join('\n') + '\n';
    try { await navigator.clipboard.writeText(md); toast('Copiado en Markdown'); } catch (e) { toast('No se pudo copiar', 'crit'); }
  },
  'x-type': el => { VIEWS.explorer.type = el.dataset.arg; VIEWS.explorer.run(); },
  'x-open': el => { const it = VIEWS.explorer.res.items[+el.dataset.i]; if (!it) return; (it.type === 'files' ? previewFile : it.type === 'results' ? previewHit : previewPage)(it.run, it, true); },
  'watch-new': () => watchDialog(),
  'watch-sel': el => { VIEWS.watches.sel = el.dataset.arg; $$('.watch').forEach(c => c.classList.toggle('on', c === el)); VIEWS.watches.detail(); },
  'watch-edit': el => watchDialog(VIEWS.watches.items.find(w => w.id === el.dataset.arg)),
  'watch-run': async el => { const r = await post(`/api/watch/${el.dataset.arg}/run`); if (r.error) return toast(r.error, 'crit'); toast('Pasada iniciada'); VIEWS.watches.sel = el.dataset.arg; await poll(); VIEWS.watches.tick(true); },
  'watch-toggle': async el => { await post(`/api/watch/${el.dataset.arg}/toggle`); VIEWS.watches.tick(true); },
  'watch-delete': async el => {
    const w = VIEWS.watches.items.find(x => x.id === el.dataset.arg);
    if (!await confirmDlg('Eliminar vigilancia', `Dejará de ejecutarse <b>${esc(w.name)}</b>. Las recolecciones que ya produjo se conservan.`, 'Eliminar', true)) return;
    await post(`/api/watch/${w.id}/delete`); if (VIEWS.watches.sel === w.id) VIEWS.watches.sel = null; toast('Vigilancia eliminada'); VIEWS.watches.tick(true);
  },
  'watch-pv': el => { const nw = VIEWS.watches.news; const it = nw && nw.items[+el.dataset.i]; if (it) previewHit(nw.last_run, it, true); },
  'src-group': el => { VIEWS.sources.group = el.dataset.arg; VIEWS.sources.paint(); },
  'act-level': el => { VIEWS.activity.level = el.dataset.arg; VIEWS.activity.paint(); },
  'win-min': () => window.pywebview?.api.minimize(),
  'win-max': () => window.pywebview?.api.toggle_maximize(),
  'win-close': () => window.pywebview?.api.close()
};
Object.assign(ACT, ACT_EXTRA);
document.addEventListener('click', e => {
  const el = e.target.closest('[data-act]'); if (!el || el.disabled) return;
  const inner = e.target.closest('a[href],button'); if (inner && inner !== el && el.contains(inner) && !inner.dataset.act) return;
  const f = ACT[el.dataset.act]; if (f) { e.stopPropagation(); f(el, e); }
});
document.addEventListener('keydown', e => {
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName);
  if (e.key === 'Escape') { if ($('#veil').classList.contains('on')) closeVeil(); else if ($('#drawer')) closeDrawer(); else if (typing) document.activeElement.blur(); return; }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); palette(); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'n') { e.preventDefault(); go('search'); }
  else if (e.key === '/' && !typing && !$('#veil').classList.contains('on')) { e.preventDefault(); go('explorer'); }
});
$('.titlebar').addEventListener('dblclick', e => { if (e.target.closest('.pywebview-drag-region')) ACT['win-max'](); });
(() => {  // redimensionado de la ventana sin bordes desde la esquina
  const g = $('#grip'); let s = null;
  g.addEventListener('mousedown', e => { s = { x: e.screenX, y: e.screenY, w: window.outerWidth, h: window.outerHeight }; e.preventDefault(); });
  window.addEventListener('mousemove', e => { if (s && window.pywebview) window.pywebview.api.resize(s.w + e.screenX - s.x, s.h + e.screenY - s.y); });
  window.addEventListener('mouseup', () => { s = null; });
})();

/* ───────── arranque ───────── */
window.addEventListener('hashchange', route);
(async () => {
  const [rs, sys, ws] = await Promise.all([api('/api/runs'), api('/api/ping'), api('/api/watches')]);
  if (Array.isArray(rs)) S.runs = rs;
  S.sys = sys; S.nWatches = (ws.items || []).length; VIEWS.watches.intervals = ws.intervals || {};
  if (sys.build) $('#ver').textContent = sys.build;
  // las vistas de otros ficheros (map.js) se registran al cargar: no enrutar hasta que estén todas
  if (document.readyState === 'loading') await new Promise(r => addEventListener('DOMContentLoaded', r, { once: true }));
  route();
  setInterval(poll, 4000);
})();
