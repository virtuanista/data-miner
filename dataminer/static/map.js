'use strict';
/* DataMiner · Mapa: los hallazgos agrupados y dibujados como gaussianas (WebGL aditivo).
   Tres niveles de detalle según el zoom: grupo → subgrupo → hallazgo. Las etiquetas se rasterizan una vez
   a su tamaño de pantalla y se pintan sin escalar, alineadas al píxel; la cámara encuadra cada nivel al entrar. */
(() => {
  const M = { run: '', kind: 'results', by: 'topic', q: '', mi: -1, adj: new Map(), data: null, cam: { x: 0, y: 0, k: 1 }, to: null, sel: { c: -1, s: -1, n: -1 }, hover: null, mouse: { x: -9999, y: -9999 }, dirty: true, raf: 0, W: 0, H: 0, dpr: 1 };
  let gl, PN, PH, PL, bufN, bufH, bufL, nN = 0, nH = 0, nL = 0, mini, mctx, cvG, cvL, ctx, stage, ro, labels = new Map(), nlabels = new Map(), grid = null;

  /* ── disposición: empaquetado circular en espiral (grupos y subgrupos) y filotaxis (hallazgos) ── */
  const SP = 0.62;                                   // separación entre hallazgos, en unidades de mundo
  function pack(items, gap) {                        // items: [{r}] → añade x,y sin solapes; devuelve el radio envolvente
    const placed = [];
    for (const it of [...items].sort((a, b) => b.r - a.r)) {
      let t = 0, x = 0, y = 0;
      const step = Math.max(0.35, it.r * 0.3);
      for (; ;) {
        x = Math.cos(t) * t * step * 0.16; y = Math.sin(t) * t * step * 0.16;
        if (placed.every(p => Math.hypot(p.x - x, p.y - y) >= p.r + it.r + gap)) break;
        t += 0.35;
      }
      it.x = x; it.y = y; placed.push(it);
    }
    let cx = 0, cy = 0; for (const p of placed) { cx += p.x; cy += p.y; } cx /= placed.length || 1; cy /= placed.length || 1;
    let R = 0; for (const p of placed) { p.x -= cx; p.y -= cy; R = Math.max(R, Math.hypot(p.x, p.y) + p.r); }
    return R;
  }
  const hue = i => { const h = (205 + i * 137.508) % 360, f = n => { const k = (n + h / 30) % 12; return 0.6 - 0.72 * Math.min(0.6, 0.4) * Math.max(-1, Math.min(k - 3, 9 - k, 1)); }; return [f(0), f(8), f(4)]; };
  function layout(d) {
    d.clusters.forEach((c, ci) => {
      c.col = c.label.startsWith('otros') ? [0.5, 0.54, 0.6] : hue(ci);
      c.subs.forEach(s => { s.r = SP * Math.sqrt(s.n) + 0.75; s.nodes = []; });
      c.r = pack(c.subs, 0.7) + 1.1;
    });
    pack(d.clusters, 3.2);
    d.nodes.forEach((n, i) => { n.i = i; d.clusters[n.c].subs[n.s].nodes.push(n); });
    for (const c of d.clusters) for (const s of c.subs) {
      s.wx = c.x + s.x; s.wy = c.y + s.y; s.c = c;
      s.nodes.forEach((n, j) => { const r = SP * Math.sqrt(j + 0.5), a = j * 2.39996323; n.x = s.wx + Math.cos(a) * r; n.y = s.wy + Math.sin(a) * r; });
    }
    let R = 1; for (const c of d.clusters) R = Math.max(R, Math.hypot(c.x, c.y) + c.r);
    d.R = R;
    const cell = 2, g = new Map();                   // rejilla espacial para localizar el hallazgo bajo el cursor
    for (const n of d.nodes) { const k = Math.floor(n.x / cell) + ',' + Math.floor(n.y / cell); (g.get(k) || g.set(k, []).get(k)).push(n); }
    grid = { cell, g };
  }

  /* ── WebGL ── */
  const VS_N = `attribute vec2 p; attribute vec3 col; attribute float st;
    uniform vec2 res, cam, mouse; uniform float k, dpr, sig;
    varying vec3 vC; varying float vA;
    void main(){ vec2 s = (p - cam) * k + res * 0.5; vec2 d = s - mouse;
      float w = exp(-dot(d, d) / (2.0 * sig * sig));
      float size = clamp(k * 1.05, 3.0, 44.0) * (1.0 + 0.5 * w) * (st > 1.5 ? 1.7 : 1.0);
      vC = col; vA = mix(0.42, 1.0, smoothstep(2.0, 16.0, k)) * min(st, 1.0) * (1.0 + 0.9 * w) * (st > 1.5 ? 1.6 : 1.0);
      gl_Position = vec4(s.x / res.x * 2.0 - 1.0, 1.0 - s.y / res.y * 2.0, 0.0, 1.0); gl_PointSize = size * dpr; }`;
  const FS_N = `precision mediump float; varying vec3 vC; varying float vA;
    void main(){ vec2 d = gl_PointCoord - 0.5; float r = dot(d, d);
      float g = exp(-r * 13.0) * 0.8 + exp(-r * 70.0) * 0.75;
      gl_FragColor = vec4(mix(vC, vec3(1.0), exp(-r * 90.0) * 0.55) * g * vA, 1.0); }`;
  const VS_H = `attribute vec2 p, q; attribute float rad, al; attribute vec3 col;
    uniform vec2 res, cam; uniform float k;
    varying vec2 vQ; varying vec3 vC; varying float vA;
    void main(){ vec2 s = (p + q * rad * 1.9 - cam) * k + res * 0.5; vQ = q; vC = col;
      float px = rad * k;                      // radio en pantalla: el halo se apaga al entrar en el grupo
      vA = al * (1.0 - smoothstep(180.0, 900.0, px)) * smoothstep(2.0, 14.0, px);
      gl_Position = vec4(s.x / res.x * 2.0 - 1.0, 1.0 - s.y / res.y * 2.0, 0.0, 1.0); }`;
  const FS_H = `precision mediump float; varying vec2 vQ; varying vec3 vC; varying float vA;
    void main(){ gl_FragColor = vec4(vC * exp(-dot(vQ, vQ) * 3.4) * vA, 1.0); }`;
  const VS_L = `attribute vec2 p; attribute vec4 col; uniform vec2 res, cam; uniform float k; varying vec4 vC;
    void main(){ vec2 s = (p - cam) * k + res * 0.5; vC = vec4(col.rgb, col.a * smoothstep(3.0, 16.0, k));
      gl_Position = vec4(s.x / res.x * 2.0 - 1.0, 1.0 - s.y / res.y * 2.0, 0.0, 1.0); }`;
  const FS_L = `precision mediump float; varying vec4 vC; void main(){ gl_FragColor = vec4(vC.rgb * vC.a, 1.0); }`;
  function prog(vs, fs) {
    const p = gl.createProgram();
    for (const [t, s] of [[gl.VERTEX_SHADER, vs], [gl.FRAGMENT_SHADER, fs]]) { const sh = gl.createShader(t); gl.shaderSource(sh, s); gl.compileShader(sh); if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) console.error(gl.getShaderInfoLog(sh)); gl.attachShader(p, sh); }
    gl.linkProgram(p); return p;
  }
  function upload() {
    const d = M.data, fq = fold(M.q.trim());
    const a = new Float32Array(d.nodes.length * 6);
    let matches = 0;
    d.nodes.forEach((n, i) => {
      const c = d.clusters[n.c].col, hit = !fq || fold(n.t + ' ' + n.d + ' ' + (n.src || '')).includes(fq);
      n.hit = hit; if (hit) matches++;
      a.set([n.x, n.y, c[0], c[1], c[2], n.i === M.sel.n ? 2 : hit ? 1 : 0.1], i * 6);
    });
    M.matches = matches;
    gl.bindBuffer(gl.ARRAY_BUFFER, bufN); gl.bufferData(gl.ARRAY_BUFFER, a, gl.STATIC_DRAW); nN = d.nodes.length;
    const halos = [];
    for (const c of d.clusters) { halos.push([c.x, c.y, c.r, c.col, 0.2]); for (const s of c.subs) halos.push([s.wx, s.wy, s.r, c.col, 0.16]); }
    const h = new Float32Array(halos.length * 6 * 9), Q = [[-1, -1], [1, -1], [-1, 1], [-1, 1], [1, -1], [1, 1]];
    halos.forEach(([x, y, r, col, al], i) => Q.forEach((q, j) => h.set([x, y, q[0], q[1], r, al, col[0], col[1], col[2]], (i * 6 + j) * 9)));
    gl.bindBuffer(gl.ARRAY_BUFFER, bufH); gl.bufferData(gl.ARRAY_BUFFER, h, gl.STATIC_DRAW); nH = halos.length * 6;
    const E = d.edges || [], l = new Float32Array(E.length * 12);        // aristas de parecido entre elementos
    E.forEach(([i, j, w], e) => { const A = d.nodes[i], Bn = d.nodes[j], ca = d.clusters[A.c].col, cb = d.clusters[Bn.c].col, al = (A.hit && Bn.hit ? 1 : 0.12) * (0.1 + 0.3 * w);
      l.set([A.x, A.y, ca[0], ca[1], ca[2], al, Bn.x, Bn.y, cb[0], cb[1], cb[2], al], e * 12); });
    gl.bindBuffer(gl.ARRAY_BUFFER, bufL); gl.bufferData(gl.ARRAY_BUFFER, l, gl.STATIC_DRAW); nL = E.length * 2;
  }
  const attr = (P, name, size, stride, off) => { const l = gl.getAttribLocation(P, name); if (l < 0) return; gl.enableVertexAttribArray(l); gl.vertexAttribPointer(l, size, gl.FLOAT, false, stride * 4, off * 4); };
  function drawGL() {
    const { W, H, dpr, cam } = M;
    gl.viewport(0, 0, cvG.width, cvG.height); gl.clearColor(0.012, 0.016, 0.024, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    if (!M.data) return;
    gl.useProgram(PH); gl.bindBuffer(gl.ARRAY_BUFFER, bufH);
    attr(PH, 'p', 2, 9, 0); attr(PH, 'q', 2, 9, 2); attr(PH, 'rad', 1, 9, 4); attr(PH, 'al', 1, 9, 5); attr(PH, 'col', 3, 9, 6);
    gl.uniform2f(gl.getUniformLocation(PH, 'res'), W, H); gl.uniform2f(gl.getUniformLocation(PH, 'cam'), cam.x, cam.y); gl.uniform1f(gl.getUniformLocation(PH, 'k'), cam.k);
    gl.drawArrays(gl.TRIANGLES, 0, nH);
    if (nL && cam.k > 3) { gl.useProgram(PL); gl.bindBuffer(gl.ARRAY_BUFFER, bufL); attr(PL, 'p', 2, 6, 0); attr(PL, 'col', 4, 6, 2);
      gl.uniform2f(gl.getUniformLocation(PL, 'res'), W, H); gl.uniform2f(gl.getUniformLocation(PL, 'cam'), cam.x, cam.y); gl.uniform1f(gl.getUniformLocation(PL, 'k'), cam.k);
      gl.drawArrays(gl.LINES, 0, nL); }
    gl.useProgram(PN); gl.bindBuffer(gl.ARRAY_BUFFER, bufN);
    attr(PN, 'p', 2, 6, 0); attr(PN, 'col', 3, 6, 2); attr(PN, 'st', 1, 6, 5);
    gl.uniform2f(gl.getUniformLocation(PN, 'res'), W, H); gl.uniform2f(gl.getUniformLocation(PN, 'cam'), cam.x, cam.y);
    gl.uniform2f(gl.getUniformLocation(PN, 'mouse'), M.mouse.x, M.mouse.y); gl.uniform1f(gl.getUniformLocation(PN, 'k'), cam.k);
    gl.uniform1f(gl.getUniformLocation(PN, 'dpr'), dpr); gl.uniform1f(gl.getUniformLocation(PN, 'sig'), 70);
    gl.drawArrays(gl.POINTS, 0, nN);
  }

  /* ── etiquetas: se rasterizan una vez a tamaño de pantalla y se reutilizan ── */
  const FONT = '"Segoe UI Variable Text","Segoe UI",system-ui,sans-serif';
  const cut = (s, n) => { s = String(s || ''); return s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s; };
  function label(key, lines, store = labels) {       // lines: [[texto, peso, px, color]]
    let l = store.get(key); if (l) return l;
    const dpr = M.dpr, c = document.createElement('canvas'), x = c.getContext('2d');
    let w = 0, h = 0; for (const [t, wt, px] of lines) { x.font = `${wt} ${px}px ${FONT}`; w = Math.max(w, x.measureText(t).width); h += Math.round(px * 1.32); }
    w = Math.ceil(w) + 16; h += 8; c.width = Math.ceil(w * dpr); c.height = Math.ceil(h * dpr); x.scale(dpr, dpr);
    x.textAlign = 'center'; x.textBaseline = 'top'; x.lineJoin = 'round';
    let y = 4;
    for (const [t, wt, px, col] of lines) { x.font = `${wt} ${px}px ${FONT}`; x.lineWidth = 4; x.strokeStyle = 'rgba(3,5,8,.86)'; x.strokeText(t, w / 2, y); x.fillStyle = col; x.fillText(t, w / 2, y); y += Math.round(px * 1.32); }
    l = { c, w, h }; store.set(key, l);
    if (store === nlabels && store.size > 900) store.delete(store.keys().next().value);
    return l;
  }
  function prerender() {                              // grupos y subgrupos, en sus dos tamaños, antes del primer fotograma
    labels.clear(); nlabels.clear();
    M.data.clusters.forEach((c, i) => { cl(c, i, 0); cl(c, i, 1); c.subs.forEach((s, j) => sl(s, i, j)); });
  }
  const cl = (c, i, big) => label(`c${i}:${big}`, big ? [[cut(c.label, 34), 700, 22, '#f2f4f7'], [`${n(c.n)} hallazgos · ${c.top.slice(0, 2).join(', ')}`, 400, 12.5, '#a9b1bd']] : [[cut(c.label, 26), 650, 14, '#eef0f3'], [n(c.n), 500, 11.5, '#9aa3af']]);
  const sl = (s, i, j) => label(`s${i}:${j}`, [[cut(s.label, 30), 600, 12.5, '#dfe3e8'], [n(s.n), 500, 11, '#8f98a4']]);
  const nl = (nd, two) => label(`n${nd.i}:${two ? 2 : 1}`, two ? [[cut(nd.t, 56), 550, 12.5, '#f0f2f5'], [cut(nd.d + (nd.src ? ' · ' + nd.src : ''), 60), 400, 11, '#98a1ad']] : [[cut(nd.t, 40), 500, 12, '#e3e6ea']], nlabels);
  // tamaño aproximado de la etiqueta de un hallazgo: decide si cabe antes de gastar en rasterizarla
  const nsize = (nd, two) => [Math.max(Math.min((nd.t || '').length, two ? 56 : 40) * 6.9, two ? Math.min(nd.d.length + (nd.src ? nd.src.length + 3 : 0), 60) * 5.8 : 0) + 16, two ? 41 : 24];

  function drawLabels() {
    const { W, H, dpr, cam } = M, d = M.data;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0); ctx.clearRect(0, 0, W, H);
    if (!d) return;
    const sx = x => (x - cam.x) * cam.k + W / 2, sy = y => (y - cam.y) * cam.k + H / 2;
    const boxes = [];
    const room = (w, h, x, y, force) => {            // reserva sitio en pantalla; null si choca o queda fuera
      const bx = Math.round(x - w / 2), by = Math.round(y - h / 2);
      if (bx > W || by > H || bx + w < 0 || by + h < 0) return null;
      if (!force) for (const b of boxes) if (bx < b[2] && bx + w > b[0] && by < b[3] && by + h > b[1]) return null;
      boxes.push([bx, by, bx + w, by + h]); return [bx, by];
    };
    const put = (l, x, y, alpha, force) => { const p = room(l.w, l.h, x, y, force); if (!p) return false; ctx.globalAlpha = alpha; ctx.drawImage(l.c, p[0], p[1], l.w, l.h); return true; };
    let budget = 28, pending = false;                // rasterizaciones nuevas por fotograma: el resto, en el siguiente
    // enlaces entre grupos: visibles en la vista general
    const far = 1 - Math.min(1, Math.max(0, (cam.k * d.R / Math.min(W, H) - 0.9) / 1.6));
    if (far > 0.02) {
      const mx = Math.max(1, ...d.links.map(l => l[2]));
      for (const [a, b, w] of d.links) {
        const A = d.clusters[a], B = d.clusters[b], x1 = sx(A.x), y1 = sy(A.y), x2 = sx(B.x), y2 = sy(B.y), cx = (x1 + x2) / 2 + (y2 - y1) * 0.12, cy = (y1 + y2) / 2 - (x2 - x1) * 0.12;
        ctx.globalAlpha = far * (0.1 + 0.3 * w / mx); ctx.strokeStyle = '#8ea0b8'; ctx.lineWidth = 0.6 + 1.6 * w / mx;
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.quadraticCurveTo(cx, cy, x2, y2); ctx.stroke();
      }
    }
    const hv = M.hover;
    if (hv && hv.kind !== 'n') { const o = hv.o, r = o.r * cam.k; ctx.globalAlpha = 0.55; ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(sx(hv.kind === 'c' ? o.x : o.wx), sy(hv.kind === 'c' ? o.y : o.wy), r, 0, 6.2832); ctx.stroke(); }
    const pivot = hv && hv.kind === 'n' ? hv.o : M.sel.n >= 0 ? d.nodes[M.sel.n] : null, near = pivot ? (M.adj.get(pivot.i) || []) : [];
    if (pivot && near.length && cam.k >= 5) {
      ctx.strokeStyle = '#ffffff'; ctx.lineWidth = 1;
      for (const [j, w] of near) { const o = d.nodes[j]; ctx.globalAlpha = 0.25 + 0.6 * w; ctx.beginPath(); ctx.moveTo(sx(pivot.x), sy(pivot.y)); ctx.lineTo(sx(o.x), sy(o.y)); ctx.stroke(); }
    }
    // nivel 1: grupos
    d.clusters.forEach((c, i) => { const r = c.r * cam.k; if (r < 9) return; const big = r > 120, inside = r > Math.min(W, H) * 0.42; put(cl(c, i, big ? 1 : 0), sx(c.x), inside ? Math.max(34, sy(c.y) - r - 6) : sy(c.y) - (big ? r + 20 : 0), inside ? 0.9 : Math.min(1, (r - 9) / 14), i === M.sel.c); });
    // nivel 2: subgrupos
    d.clusters.forEach((c, i) => c.subs.forEach((s, j) => { const r = s.r * cam.k; if (r > 36 && r < Math.max(W, H) * 0.7) put(sl(s, i, j), sx(s.wx), sy(s.wy) - r - 12, Math.min(1, 0.35 + (r - 36) / 24) * (r > 420 ? 0.6 : 1)); }));
    // nivel 3: hallazgos (primero el seleccionado y el que está bajo el cursor)
    if (cam.k >= 22) {
      const two = cam.k >= 64, a = Math.min(1, (cam.k - 22) / 14), off = Math.min(22, cam.k * 0.55) / 2 + 12;
      const x0 = cam.x - W / 2 / cam.k, x1 = cam.x + W / 2 / cam.k, y0 = cam.y - H / 2 / cam.k, y1 = cam.y + H / 2 / cam.k;
      const first = [M.sel.n >= 0 ? d.nodes[M.sel.n] : null, hv && hv.kind === 'n' ? hv.o : null].filter(Boolean);
      for (const nd of first) put(nl(nd, true), sx(nd.x), sy(nd.y) + off + 4, 1, true);
      for (const [j] of near) { const o = d.nodes[j]; if (!first.includes(o)) { first.push(o); put(nl(o, true), sx(o.x), sy(o.y) + off + 4, 0.95); } }
      let count = 0;
      for (const nd of d.nodes) {
        if (count > 140) break;
        if (!nd.hit || nd.x < x0 || nd.x > x1 || nd.y < y0 || nd.y > y1 || first.includes(nd)) continue;
        const key = `n${nd.i}:${two ? 2 : 1}`, cached = nlabels.get(key), [w, h] = cached ? [cached.w, cached.h] : nsize(nd, two);
        const p = room(w, h, sx(nd.x), sy(nd.y) + off + (two ? 4 : 0)); if (!p) continue;
        count++;
        if (!cached && budget-- <= 0) { pending = true; continue; }
        const l = cached || nl(nd, two); ctx.globalAlpha = a; ctx.drawImage(l.c, Math.round(sx(nd.x) - l.w / 2), p[1], l.w, l.h);
      }
      if (pending) touch();
    }
    ctx.globalAlpha = 1;
  }

  /* ── cámara ── */
  function frame() {
    M.raf = 0;
    if (M.to) {
      const t = M.to, f = t.from || (t.from = { ...M.cam, t0: performance.now() });
      const u = Math.min(1, (performance.now() - f.t0) / 480), e = 1 - Math.pow(1 - u, 3);
      M.cam.x = f.x + (t.x - f.x) * e; M.cam.y = f.y + (t.y - f.y) * e; M.cam.k = f.k * Math.pow(t.k / f.k, e);
      if (u >= 1) { M.cam.x = t.x; M.cam.y = t.y; M.cam.k = t.k; M.to = null; }
      M.dirty = true;
    }
    if (M.dirty) { M.dirty = false; drawMini(); drawGL(); const t0 = performance.now(); drawLabels(); M.ms = Math.max(performance.now() - t0, (M.ms || 0) * 0.9); hud(); }
    if (M.to) kick();
  }
  function drawMini() {                              // minimapa: los grupos y el rectángulo que ve la cámara
    const d = M.data; if (!mctx) return;
    const w = 176, h = 124, dpr = M.dpr; mctx.setTransform(dpr, 0, 0, dpr, 0, 0); mctx.clearRect(0, 0, w, h);
    if (!d) return;
    const s = Math.min(w, h) / (2 * d.R * 1.08), X = x => w / 2 + x * s, Y = y => h / 2 + y * s;
    d.clusters.forEach((c, i) => { mctx.globalAlpha = i === M.sel.c ? 1 : 0.6; mctx.fillStyle = `rgb(${c.col.map(v => Math.round(v * 255)).join(',')})`; mctx.beginPath(); mctx.arc(X(c.x), Y(c.y), Math.max(1.5, c.r * s), 0, 6.2832); mctx.fill(); });
    const vw = M.W / M.cam.k * s, vh = M.H / M.cam.k * s;
    mctx.globalAlpha = 1; mctx.strokeStyle = '#ffffff'; mctx.lineWidth = 1.2;
    mctx.strokeRect(Math.max(1, X(M.cam.x) - vw / 2), Math.max(1, Y(M.cam.y) - vh / 2), Math.min(w - 2, vw), Math.min(h - 2, vh));
    M.ms2 = s;
  }
  function jump(dir) {                               // Intro en el buscador: salta a la coincidencia siguiente
    const d = M.data; if (!d || !M.q.trim()) return;
    const hits = d.nodes.filter(x => x.hit); if (!hits.length) return;
    M.mi = (M.mi + dir + hits.length) % hits.length;
    const o = hits[M.mi]; M.sel = { c: o.c, s: o.s, n: o.i }; M.to = { x: o.x, y: o.y, k: Math.max(M.cam.k, 46) };
    upload(); side(); touch();
  }
  const kick = () => { if (!M.raf) M.raf = requestAnimationFrame(frame); };
  const touch = () => { M.dirty = true; kick(); };
  function fit(x, y, r) { const pad = $('#mp-side') && M.W > 900 ? 250 : 0; M.to = { x: x - pad / 2 / (Math.min(M.W - pad, M.H) / (2 * r * 1.22)), y, k: Math.min(M.W - pad, M.H) / (2 * r * 1.22) }; kick(); }
  const snap = () => { const t = M.to; if (t) { M.cam.x = t.x; M.cam.y = t.y; M.cam.k = t.k; M.to = null; } };
  function focus(c, s, nd) {
    const d = M.data; if (!d) return;
    M.sel = { c, s, n: nd == null ? -1 : nd };
    if (nd != null && nd >= 0) { const o = d.nodes[nd]; M.to = { x: o.x, y: o.y, k: Math.max(M.cam.k, 70) }; kick(); }
    else if (s >= 0) { const o = d.clusters[c].subs[s]; fit(o.wx, o.wy, o.r); }
    else if (c >= 0) { const o = d.clusters[c]; fit(o.x, o.y, o.r); }
    else fit(0, 0, d.R);
    upload(); side(); touch();
  }
  function up() { const s = M.sel; if (s.n >= 0) { closeDrawer(); focus(s.c, s.s, -1); } else if (s.s >= 0) focus(s.c, -1, -1); else focus(-1, -1, -1); }

  function pick(mx, my) {
    const d = M.data, cam = M.cam, wx = (mx - M.W / 2) / cam.k + cam.x, wy = (my - M.H / 2) / cam.k + cam.y;
    if (cam.k >= 7) {
      const lim = Math.max(9, Math.min(22, cam.k * 0.55)) / cam.k; let best = null, bd = lim * lim;
      const gx = Math.floor(wx / grid.cell), gy = Math.floor(wy / grid.cell);
      for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (const nd of grid.g.get((gx + i) + ',' + (gy + j)) || []) { const q = (nd.x - wx) ** 2 + (nd.y - wy) ** 2; if (q < bd && nd.hit) { bd = q; best = nd; } }
      if (best) return { kind: 'n', o: best };
    }
    for (let i = 0; i < d.clusters.length; i++) {
      const c = d.clusters[i]; if (Math.hypot(c.x - wx, c.y - wy) > c.r) continue;
      if (c.r * cam.k > 150) for (let j = 0; j < c.subs.length; j++) { const s = c.subs[j]; if (Math.hypot(s.wx - wx, s.wy - wy) <= s.r) return { kind: 's', o: s, c: i, s: j }; }
      return { kind: 'c', o: c, c: i };
    }
    return null;
  }

  /* ── interfaz ── */
  function hud() {
    const d = M.data, el = $('#mp-hud'); if (!el || !d) return;
    const lvl = M.cam.k >= 22 ? 'Hallazgos' : M.cam.k * (d.clusters[0]?.r || 1) > 150 ? 'Subgrupos' : 'Grupos';
    el.textContent = `${lvl} · ${M.cam.k < 10 ? M.cam.k.toFixed(1) : Math.round(M.cam.k)} px/unidad`;
  }
  function side() {
    const d = M.data, el = $('#mp-side'); if (!el || !d) return;
    const s = M.sel, C = s.c >= 0 ? d.clusters[s.c] : null, css = c => `rgb(${c.col.map(v => Math.round(v * 255)).join(',')})`;
    const crumb = `<div class="mp-crumb"><button data-act="mp-focus" data-c="-1" data-s="-1">Todo</button>${C ? `${ic('chev')}<button data-act="mp-focus" data-c="${s.c}" data-s="-1">${esc(cut(C.label, 22))}</button>` : ''}${C && s.s >= 0 ? `${ic('chev')}<span>${esc(cut(C.subs[s.s].label, 20))}</span>` : ''}</div>`;
    const rows = C ? C.subs.map((x, j) => `<button class="mp-row ${s.s === j ? 'on' : ''}" data-act="mp-focus" data-c="${s.c}" data-s="${j}"><i style="background:${css(C)}"></i><span class="trunc">${esc(x.label)}</span><b>${n(x.n)}</b></button>`).join('')
      : d.clusters.map((c, i) => `<button class="mp-row" data-act="mp-focus" data-c="${i}" data-s="-1"><i style="background:${css(c)}"></i><span class="trunc">${esc(c.label)}</span><b>${n(c.n)}</b></button>`).join('');
    el.innerHTML = crumb + `<h6>${C ? (M.by === 'domain' ? 'Fuentes' : 'Dominios') + ' del grupo' : { topic: 'Temas', domain: 'Dominios', source: 'Fuentes' }[M.by]}</h6><div class="mp-list">${rows}</div>
      <p class="mp-note">${M.q.trim() ? `${M.mi >= 0 ? `Coincidencia ${M.mi + 1} de ${n(M.matches)}` : `${n(M.matches)} coincidencias`} · Intro salta a la siguiente. ` : ''}${n(d.shown)} ${d.kind === 'files' ? 'ficheros' : 'hallazgos'} en ${d.clusters.length} grupos${d.total > d.shown ? ` (los ${n(d.shown)} más recientes de ${n(d.total)})` : ''} · ${n((d.edges || []).length)} aristas de parecido.</p>`;
  }
  function resize() {
    if (!stage) return;
    const r = stage.getBoundingClientRect(), first = !M.W;
    M.W = Math.max(50, r.width); M.H = Math.max(50, r.height); M.dpr = Math.min(devicePixelRatio || 1, 2);
    for (const c of [cvG, cvL]) { c.width = Math.round(M.W * M.dpr); c.height = Math.round(M.H * M.dpr); }
    if (mini) { mini.width = Math.round(176 * M.dpr); mini.height = Math.round(124 * M.dpr); }
    if (M.data && first) { fit(0, 0, M.data.R); snap(); }
    touch();
  }
  async function load() {
    const body = $('#mp-stage'); if (!body) return;
    if (M.kind !== 'files' && M.by === 'type') M.by = 'topic';
    const d = await api(`/api/graph?run=${encodeURIComponent(M.run)}&by=${M.by}&kind=${M.kind}`);
    seg();
    if (d.error || !$('#mp-stage')) return;
    $('#mp-empty')?.remove(); if (cvL) delete cvL.dataset.tip;
    if (!d.nodes.length) {
      M.data = null; touch(); $('#mp-side').innerHTML = '';
      body.insertAdjacentHTML('beforeend', `<div id="mp-empty" class="mp-empty">${empty('explore', 'Aún no hay nada que cartografiar', 'El mapa agrupa los hallazgos de tus búsquedas. Lanza una y vuelve aquí.', `<button class="btn pri" data-act="go" data-arg="search">${ic('search')}Buscar recursos</button>`)}</div>`);
      return;
    }
    layout(d); M.data = d; M.sel = { c: -1, s: -1, n: -1 }; M.hover = null; M.mi = -1;
    M.adj = new Map(); for (const [i, j, w] of d.edges || []) { (M.adj.get(i) || M.adj.set(i, []).get(i)).push([j, w]); (M.adj.get(j) || M.adj.set(j, []).get(j)).push([i, w]); }
    prerender(); upload(); side();
    fit(0, 0, d.R); snap(); touch();
  }

  const seg = () => { const el = $('#mp-seg'); if (el) el.innerHTML = [['topic', 'Por tema'], ['domain', 'Por dominio'], ['source', 'Por fuente'], ...(M.kind === 'files' ? [['type', 'Por tipo']] : [])].map(([k, l]) => `<button class="${M.by === k ? 'on' : ''}" data-act="mp-by" data-arg="${k}">${l}</button>`).join(''); };
  VIEWS.map = {
    state: () => ({ kind: M.kind, by: M.by, edges: nL / 2, mi: M.mi, nodes: M.data ? M.data.nodes.length : 0, ms: M.ms, nl: nlabels.size, cam: M.cam, to: M.to, sel: M.sel, W: M.W, H: M.H, labels: labels.size, clusters: M.data ? M.data.clusters.map(c => [c.label, Math.round(c.x), Math.round(c.y), Math.round(c.r)]) : [] }),
    async mount(run) {
      M.run = run && S.runs.some(r => r.id === run) ? run : ''; if (M.run) M.kind = 'results';
      view(`<div class="phead" style="padding-bottom:12px"><div class="ptitle"><div><h1>Mapa</h1><p>Los hallazgos agrupados. Acércate para pasar de grupos a subgrupos y a cada hallazgo.</p></div></div></div>
        <div class="toolbar" style="padding-top:0"><select class="in" id="mp-run" style="width:auto;max-width:260px" aria-label="Ámbito"><option value="">Hallazgos · todas las búsquedas</option><option value="@files" ${M.kind === 'files' ? 'selected' : ''}>Ficheros · la biblioteca</option>${S.runs.map(r => `<option value="${esc(r.id)}" ${r.id === M.run && M.kind !== 'files' ? 'selected' : ''}>Hallazgos · ${esc(cut(terms(r), 40))}</option>`).join('')}</select>
          <div class="seg" id="mp-seg"></div>
          <label class="search" style="max-width:280px">${ic('search')}<input class="in" id="mp-q" type="search" placeholder="Buscar en el mapa · Intro salta" value="${esc(M.q)}" aria-label="Resaltar"></label><span class="grow"></span>
          <button class="btn" data-act="mp-up" title="Esc">${ic('chev', 'flip')}Subir un nivel</button><button class="btn" data-act="mp-focus" data-c="-1" data-s="-1">${ic('explore')}Encuadrar todo</button></div>
        <div class="mp-stage" id="mp-stage"><canvas id="mp-gl"></canvas><canvas id="mp-lb"></canvas><aside class="mp-side" id="mp-side"></aside><canvas class="mp-mini" id="mp-mini" title="Minimapa: pulsa o arrastra para moverte"></canvas><div class="mp-hud" id="mp-hud"></div>
          <div class="mp-help">Rueda: zoom · Arrastrar: mover · Clic: entrar · Esc: subir</div></div>`);
      stage = $('#mp-stage'); cvG = $('#mp-gl'); cvL = $('#mp-lb'); ctx = cvL.getContext('2d');
      gl = cvG.getContext('webgl', { antialias: false, alpha: false });
      if (!gl) { stage.innerHTML = empty('alert', 'Este equipo no ofrece WebGL', 'El mapa necesita aceleración gráfica para dibujar las gaussianas.'); return; }
      PN = prog(VS_N, FS_N); PH = prog(VS_H, FS_H); PL = prog(VS_L, FS_L); bufN = gl.createBuffer(); bufH = gl.createBuffer(); bufL = gl.createBuffer();
      mini = $('#mp-mini'); mctx = mini.getContext('2d');
      const mgo = e => { if (!M.data || !M.ms2) return; const r = mini.getBoundingClientRect(); M.to = null; M.cam.x = (e.clientX - r.left - 88) / M.ms2; M.cam.y = (e.clientY - r.top - 62) / M.ms2; touch(); };
      mini.addEventListener('pointerdown', e => { mini.setPointerCapture(e.pointerId); mini._d = true; mgo(e); });
      mini.addEventListener('pointermove', e => { if (mini._d) mgo(e); });
      mini.addEventListener('pointerup', () => { mini._d = false; });
      gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
      M.W = 0; M.data = null; M.to = null;
      ro = new ResizeObserver(resize); ro.observe(stage); resize();
      $('#mp-run').onchange = e => { const v = e.target.value; M.kind = v === '@files' ? 'files' : 'results'; M.run = v === '@files' ? '' : v; history.replaceState(null, '', '#/map' + (M.run ? '/' + M.run : '')); load(); };
      $('#mp-q').oninput = debounce(e => { M.q = e.target.value; M.mi = -1; if (M.data) { upload(); side(); touch(); } }, 150);
      $('#mp-q').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); if (M.q !== e.target.value) { M.q = e.target.value; M.mi = -1; upload(); } jump(e.shiftKey ? -1 : 1); } };
      let drag = null;
      cvL.addEventListener('wheel', e => {
        e.preventDefault(); const r = cvL.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, c = M.cam;
        const dy = e.deltaMode === 1 ? e.deltaY * 33 : e.deltaY, k = Math.max(0.05, Math.min(400, c.k * Math.exp(-dy * 0.0016)));
        const wx = (mx - M.W / 2) / c.k + c.x, wy = (my - M.H / 2) / c.k + c.y;
        M.to = null; c.x = wx - (mx - M.W / 2) / k; c.y = wy - (my - M.H / 2) / k; c.k = k; touch();
      }, { passive: false });
      cvL.addEventListener('pointerdown', e => { if (e.button) return; drag = { x: e.clientX, y: e.clientY, cx: M.cam.x, cy: M.cam.y, moved: false }; cvL.setPointerCapture(e.pointerId); });
      cvL.addEventListener('pointermove', e => {
        const r = cvL.getBoundingClientRect(); M.mouse = { x: e.clientX - r.left, y: e.clientY - r.top };
        if (drag) { const dx = e.clientX - drag.x, dy = e.clientY - drag.y; if (Math.abs(dx) + Math.abs(dy) > 4) drag.moved = true; if (drag.moved) { M.to = null; M.cam.x = drag.cx - dx / M.cam.k; M.cam.y = drag.cy - dy / M.cam.k; cvL.style.cursor = 'grabbing'; } }
        else if (M.data) {
          const h = pick(M.mouse.x, M.mouse.y); M.hover = h; cvL.style.cursor = h ? 'pointer' : 'grab';
          if (h) cvL.dataset.tip = h.kind === 'n' ? `${cut(h.o.t, 90)}|${h.o.d}${h.o.src ? ' · ' + h.o.src : ''}` : `${h.o.label}|${n(h.o.n)} hallazgos · clic para entrar`; else delete cvL.dataset.tip;
        }
        touch();
      });
      cvL.addEventListener('pointerup', e => {
        const d = drag; drag = null; cvL.style.cursor = 'grab'; if (!d || d.moved || !M.data) return;
        const r = cvL.getBoundingClientRect(), h = pick(e.clientX - r.left, e.clientY - r.top);
        if (!h) return;
        if (h.kind === 'n') { const o = h.o; focus(o.c, o.s, o.i); if (o.f) previewFile(o.run, o.f, true); else previewHit(o.run, { title: o.t, url: o.u, snippet: o.sn, source: o.src, date: o.date, query: o.q }, true); }
        else if (h.kind === 's') focus(h.c, h.s, -1); else focus(h.c, -1, -1);
      });
      cvL.addEventListener('pointerleave', () => { M.mouse = { x: -9999, y: -9999 }; M.hover = null; delete cvL.dataset.tip; touch(); });
      this.key = e => {
        if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName) || $('#veil').classList.contains('on') || !M.data) return;
        const nC = M.data.clusters.length;
        if (e.key === 'Home') focus(-1, -1, -1);
        else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') focus((M.sel.c + 1) % nC, -1, -1);
        else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') focus((M.sel.c - 1 + nC) % nC, -1, -1);
        else if (e.key === 'Escape' && !$('#drawer') && (M.sel.c >= 0)) up(); else return;
        e.preventDefault();
      };
      addEventListener('keydown', this.key);
      await load();
    },
    unmount() { removeEventListener('keydown', this.key); ro && ro.disconnect(); cancelAnimationFrame(M.raf); M.raf = 0; stage = null; M.data = null; labels.clear(); nlabels.clear(); const x = gl && gl.getExtension('WEBGL_lose_context'); x && x.loseContext(); gl = null; }
  };
  Object.assign(ACT, {
    'mp-by': el => { M.by = el.dataset.arg; load(); },
    'mp-focus': el => focus(+el.dataset.c, +el.dataset.s, -1),
    'mp-up': up
  });
})();
