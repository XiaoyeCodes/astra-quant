/* ASTRA holo table (three.js r128) — replaces the old grid tower.
   Glass 3D candlesticks on a dark reflective table. Open orders are laser planes across the chart
   (blue = buy, ember = sell), fills fire light beams, MA7/25/99 and Bollinger bands float as glowing
   tubes, swing-based support / resistance are dashed bands, futures positions show entry and
   liquidation lines. API: A.Holo.init(canvas), update(state), setActive(on), applyVisibility(). */
(function () {
  const A = window.ASTRA;
  const T = window.THREE;
  const N = 64;            // visible candles
  const W = 24;            // chart width (world units)
  const H = 11.2;          // chart height
  const FLOOR = -H / 2 - 0.7;
  const BACK = -2.8;       // back wall z
  const DEPTH = 5;
  const LEVEL_PH = 1.555;   // camera at eye level (straight-on view)         // order plane depth

  const tower = {};
  A.Holo = tower;

  const COLORS = { ma7: '#f2d38a', ma25: '#c7a6ff', ma99: '#6fd6c4' };
  const C = {
    bull: new T.Color(A.C.bull), bear: new T.Color(A.C.bear), ink: new T.Color(A.C.ink), gold: new T.Color(A.C.gold),
    ma7: new T.Color(COLORS.ma7), ma25: new T.Color(COLORS.ma25), ma99: new T.Color(COLORS.ma99),
  };
  const vis = A.vis; // shared with the time halo, owned by A.Views
  tower.COLORS = COLORS;
  tower.vis = vis;

  let renderer, scene, camera, canvas, root, glowTex, ready = false, visible = true, active = false;
  let fx = 0, fy = 0; // follow-camera target
  const orbit = { th: Math.PI / 2, ph: 1.33, r: 28, cth: Math.PI / 2, tph: 1.33, tr: 28, last: -1e9 };
  let lo = 0, hi = 1, tLo = 0, tHi = 1, framed = false;
  let state = null, candles = [], ind = null, lastFocus = null, seenTrades = null, lastCandleKey = '';
  const clock = new T.Clock();

  const pool = [];              // candle meshes
  const orderObjs = new Map();  // id -> plane objects
  const srObjs = [];
  const posObjs = [];
  const beams = [];
  const tubes = {};
  let bollMesh = null, bollLines = null, priceLine, priceCore, priceTag = null, priceTagText = '';
  const rulerObjs = [];
  let rulerKey = '', timeKey = '';
  const timeObjs = [];

  const yOf = (p) => ((p - lo) / (hi - lo || 1)) * H - H / 2;
  const xOf = (i) => -W / 2 + (i + 0.5) * (W / N);

  /* ---------------------------------------------------------- helpers -- */
  function makeGlowTexture() {
    const c = document.createElement('canvas'); c.width = c.height = 128;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, 'rgba(255,255,255,1)'); grd.addColorStop(0.2, 'rgba(255,255,255,.5)');
    grd.addColorStop(0.5, 'rgba(255,255,255,.1)'); grd.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    return new T.CanvasTexture(c);
  }
  function glow(color, s, o) {
    const m = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color, transparent: true, opacity: o == null ? 1 : o, blending: T.AdditiveBlending, depthWrite: false }));
    m.scale.set(s, s, 1);
    return m;
  }
  function label(text, color, size, opts) {
    opts = opts || {};
    const c = document.createElement('canvas'), g = c.getContext('2d');
    const fs = 40, pad = opts.box ? 14 : 8;
    g.font = `500 ${fs}px ${A.C.mono}`;
    c.width = Math.ceil(g.measureText(text).width) + pad * 2; c.height = fs + 18;
    g.font = `500 ${fs}px ${A.C.mono}`;
    if (opts.box) { g.fillStyle = color; g.fillRect(0, 0, c.width, c.height); g.fillStyle = A.C.bg; }
    else g.fillStyle = color;
    g.textBaseline = 'middle'; g.fillText(text, pad, c.height / 2 + 2);
    const tex = new T.CanvasTexture(c); tex.minFilter = T.LinearFilter;
    const s = new T.Sprite(new T.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: opts.depthTest !== false }));
    const h = 0.42 * (size || 1);
    s.scale.set((h * c.width) / c.height, h, 1);
    s.center.set(opts.right ? 1 : 0, 0.5);
    s.renderOrder = 10;
    return s;
  }
  /* glowing holographic price text, same look as the time halo (no box, no frame) */
  function holoLabel(text) {
    const c = document.createElement('canvas'), g = c.getContext('2d');
    const fs = 64, font = `500 ${fs}px ${A.C.mono}`;
    g.font = font;
    c.width = Math.ceil(g.measureText(text).width) + 60; c.height = fs + 40;
    g.font = font; g.textAlign = 'left'; g.textBaseline = 'middle';
    const x = 30, y = c.height / 2;
    g.shadowColor = 'rgba(160,205,255,.95)'; g.shadowBlur = 26; g.fillStyle = 'rgba(190,220,255,.55)'; g.fillText(text, x, y);
    g.shadowBlur = 8; g.fillStyle = '#eef6ff'; g.fillText(text, x, y);
    const tex = new T.CanvasTexture(c); tex.minFilter = T.LinearFilter;
    const sp = new T.Sprite(new T.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: false, blending: T.AdditiveBlending }));
    const h = 0.6;
    sp.scale.set((h * c.width) / c.height, h, 1);
    sp.center.set(0.08, 0.5);
    sp.renderOrder = 11;
    return sp;
  }
  function dropSprite(s) { if (!s) return; root.remove(s); if (s.material.map) s.material.map.dispose(); s.material.dispose(); }
  function line(points, color, opacity, dashed) {
    const geo = new T.BufferGeometry().setFromPoints(points);
    const mat = dashed
      ? new T.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 0.3, gapSize: 0.22, depthWrite: false })
      : new T.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
    const l = new T.Line(geo, mat);
    if (dashed) l.computeLineDistances();
    return l;
  }

  /* ---------------------------------------------------------- static --- */
  function buildStatic() {
    // stars
    const n = 900, pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 60 + Math.random() * 80, u = Math.random() * 2 - 1, a = Math.random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      pos[i * 3] = r * s * Math.cos(a); pos[i * 3 + 1] = r * u * 0.6; pos[i * 3 + 2] = r * s * Math.sin(a);
    }
    const sg = new T.BufferGeometry(); sg.setAttribute('position', new T.BufferAttribute(pos, 3));
    scene.add(new T.Points(sg, new T.PointsMaterial({ color: 0xc9ccd3, size: 0.22, transparent: true, opacity: 0.45, depthWrite: false })));

    // table: dark plate + fine grid + front rim
    const plate = new T.Mesh(new T.PlaneGeometry(W + 10, 14), new T.MeshBasicMaterial({ color: 0x0a0d13, transparent: true, opacity: 0.72, depthWrite: false }));
    plate.rotation.x = -Math.PI / 2; plate.position.set(0, FLOOR - 0.01, 0);
    root.add(plate);
    const grid = new T.GridHelper(W + 10, 34, 0x252a33, 0x14171d);
    grid.position.y = FLOOR; grid.material.transparent = true; grid.material.opacity = 0.7;
    root.add(grid);
    root.add(line([new T.Vector3(-W / 2 - 5, FLOOR, 7), new T.Vector3(W / 2 + 5, FLOOR, 7)], C.ink, 0.25));

    // back wall: faint glass pane
    const wall = new T.Mesh(new T.PlaneGeometry(W + 2, H + 1.4), new T.MeshBasicMaterial({ color: 0x0f141d, transparent: true, opacity: 0.35, depthWrite: false, side: T.DoubleSide }));
    wall.position.set(0, (FLOOR + H / 2 + 0.7) / 2 + 0.35, BACK);
    root.add(wall);

    // candle pool
    for (let i = 0; i < N; i++) {
      const box = new T.BoxGeometry(1, 1, 1);
      const body = new T.Mesh(box, new T.MeshBasicMaterial({ transparent: true, opacity: 0.38, blending: T.AdditiveBlending, depthWrite: false }));
      const edge = new T.LineSegments(new T.EdgesGeometry(box), new T.LineBasicMaterial({ transparent: true, opacity: 0.95 }));
      const wick = new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(0, -0.5, 0), new T.Vector3(0, 0.5, 0)]), new T.LineBasicMaterial({ transparent: true, opacity: 0.75 }));
      const refl = new T.Mesh(box, new T.MeshBasicMaterial({ transparent: true, opacity: 0.07, blending: T.AdditiveBlending, depthWrite: false }));
      root.add(body, edge, wick, refl);
      pool.push({ body, edge, wick, refl });
    }

    // current price
    priceLine = line([new T.Vector3(-W / 2 - 0.4, 0, 0.02), new T.Vector3(W / 2 + 1.6, 0, 0.02)], C.ink, 0.75);
    priceCore = glow(C.ink, 1.6, 1);
    root.add(priceLine, priceCore);
  }

  /* ---------------------------------------------------------- input ---- */
  function setupInteraction() {
    let drag = null;
    canvas.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); orbit.last = performance.now(); });
    canvas.addEventListener('pointermove', (e) => {
      if (!drag) return;
      orbit.cth -= (e.clientX - drag.x) * 0.006;
      orbit.tph = A.clamp(orbit.tph - (e.clientY - drag.y) * 0.005, 0.5, LEVEL_PH);
      drag = { x: e.clientX, y: e.clientY };
      orbit.last = performance.now();
    });
    const end = () => { drag = null; };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      orbit.tr = A.clamp(orbit.tr * (1 + Math.sign(e.deltaY) * 0.08), 16, 60);
      orbit.last = performance.now();
    }, { passive: false });
    canvas.addEventListener('dblclick', () => { orbit.cth = Math.PI / 2; orbit.tph = 1.33; orbit.tr = 28; });
  }

  function resize() {
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / r.height;
    camera.fov = r.width / r.height < 1.1 ? 52 : 36;
    camera.updateProjectionMatrix();
  }

  tower.init = function (cv) {
    canvas = cv;
    try {
      renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    } catch (e) {
      A.$('towerFallback').hidden = false;
      return false;
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    scene = new T.Scene();
    scene.fog = new T.FogExp2(0x07080b, 0.012);
    camera = new T.PerspectiveCamera(36, 1, 0.1, 400);
    root = new T.Group();
    scene.add(root);
    glowTex = makeGlowTexture();
    buildStatic();
    setupInteraction();
    resize();
    new ResizeObserver(resize).observe(canvas);
    if ('IntersectionObserver' in window) new IntersectionObserver((es) => { visible = es[0].isIntersecting; }).observe(canvas);
    ready = true;
    requestAnimationFrame(loop);
    return true;
  };

  tower.setVisible = function () { applyVisibility(); };
  tower.applyVisibility = function () { applyVisibility(); lastCandleKey = ''; };
  tower.onCamMode = function (mode) {
    if (mode === 'follow') { orbit.cth = Math.PI / 2; orbit.tph = LEVEL_PH; }
    orbit.last = performance.now() - 10000;
  };
  tower.setActive = function (on) { active = on; if (on && ready) { resize(); if (A.state) tower.update(A.state); } };
  function applyVisibility() {
    ['ma7', 'ma25', 'ma99'].forEach((k) => { if (tubes[k]) tubes[k].visible = vis[k]; });
    if (bollMesh) bollMesh.visible = bollLines.visible = vis.boll;
    srObjs.forEach((o) => { o.group.visible = vis.sr; });
    orderObjs.forEach((o) => { o.group.visible = vis.orders; });
  }

  /* ---------------------------------------------------------- data in -- */
  tower.update = function (s) {
    if (!ready || !s) return;
    state = s;
    const sym = s.focus.symbol;
    if (sym !== lastFocus) {
      lastFocus = sym;
      orderObjs.forEach((o) => disposeOrder(o)); orderObjs.clear();
      seenTrades = null; framed = false; lastCandleKey = '';
    }
    const price = s.ticker ? s.ticker.last : null;
    if (!price || !s.candles || !s.candles.length) return;

    // candles: copy and let the live price move the last one
    candles = s.candles.map((c) => c.slice());
    const last = candles[candles.length - 1];
    last[4] = price; last[2] = Math.max(last[2], price); last[3] = Math.min(last[3], price);
    ind = A.Ind.compute(candles, price);
    const view = candles.slice(-N);

    // vertical frame: visible candles, indicators in view, S/R, nearby orders, position entry
    let fLo = Infinity, fHi = -Infinity;
    view.forEach((c) => { fLo = Math.min(fLo, c[3]); fHi = Math.max(fHi, c[2]); });
    const off = candles.length - view.length;
    ['ma7', 'ma25', 'ma99'].forEach((k) => { if (!vis[k]) return; ind[k].slice(off).forEach((v) => { if (v) { fLo = Math.min(fLo, v); fHi = Math.max(fHi, v); } }); });
    if (vis.boll) ['up', 'lo'].forEach((k) => ind.boll[k].slice(off).forEach((v) => { if (v) { fLo = Math.min(fLo, v); fHi = Math.max(fHi, v); } }));
    if (vis.sr) ind.sr.res.concat(ind.sr.sup).forEach((l) => { if (Math.abs(l.price / price - 1) < 0.02) { fLo = Math.min(fLo, l.price); fHi = Math.max(fHi, l.price); } });
    const orders = s.orders.filter((o) => o.symbol === sym && o.price > 0);
    if (vis.orders) orders.forEach((o) => { if (Math.abs(o.price / price - 1) < 0.006) { fLo = Math.min(fLo, o.price); fHi = Math.max(fHi, o.price); } });
    (s.positions || []).filter((p) => p.symbol === sym).forEach((p) => { if (Math.abs(p.entry / price - 1) < 0.02) { fLo = Math.min(fLo, p.entry); fHi = Math.max(fHi, p.entry); } });
    const pad = Math.max((fHi - fLo) * 0.1, price * 0.0008);
    tLo = fLo - pad; tHi = fHi + pad;
    if (!framed) { lo = tLo; hi = tHi; framed = true; }

    syncOrders(orders, price);
    syncSR();
    syncPositions((s.positions || []).filter((p) => p.symbol === sym));
    const key = `${candles.length}|${last[0]}|${A.fmtPx(last[4])}|${A.fmtPx(lo)}|${A.fmtPx(hi)}`;
    if (key !== lastCandleKey) { lastCandleKey = key; rebuildIndicators(true); }

    // fills -> beams at the candle they happened in
    const fills = s.trades.filter((t) => t.symbol === sym);
    if (seenTrades === null) seenTrades = new Set(fills.map((t) => t.id));
    else fills.forEach((t) => { if (!seenTrades.has(t.id)) { seenTrades.add(t.id); spawnBeam(t); } });
  };

  function candleIndexOf(time) {
    const view = candles.slice(-N);
    if (!time || !view.length) return view.length - 1;
    for (let i = view.length - 1; i >= 0; i--) if (time >= view[i][0]) return i;
    return 0;
  }

  /* orders: horizontal laser planes */
  function disposeOrder(o) {
    root.remove(o.group);
    o.plane.geometry.dispose(); o.plane.material.dispose(); o.edge.geometry.dispose(); o.edge.material.dispose(); o.bead.material.dispose();
    dropSprite(o.label);
  }
  function syncOrders(orders, price) {
    const live = new Set();
    orders.forEach((o) => {
      live.add(o.id);
      let obj = orderObjs.get(o.id);
      const color = o.side === 'buy' ? C.bull : C.bear;
      if (!obj) {
        const group = new T.Group();
        const plane = new T.Mesh(new T.PlaneGeometry(W + 1.2, DEPTH), new T.MeshBasicMaterial({ color, transparent: true, opacity: 0, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
        plane.rotation.x = -Math.PI / 2; plane.position.z = BACK + DEPTH / 2 + 0.2;
        const dashed = /STOP|LOSS/.test(o.type);
        const edge = line([new T.Vector3(-W / 2 - 0.6, 0, BACK + DEPTH + 0.2), new T.Vector3(W / 2 + 0.6, 0, BACK + DEPTH + 0.2)], color, 0, dashed);
        const bead = glow(color, 0.8, 0); bead.position.set(W / 2 + 0.9, 0, BACK + DEPTH + 0.2);
        group.add(plane, edge, bead);
        group.visible = vis.orders;
        root.add(group);
        obj = { group, plane, edge, bead, label: null, born: performance.now(), dying: 0 };
        orderObjs.set(o.id, obj);
      }
      obj.price = o.price; obj.side = o.side; obj.type = o.type; obj.dying = 0;
    });
    orderObjs.forEach((obj, id) => { if (!live.has(id) && !obj.dying) obj.dying = performance.now(); });
    const nearest = new Set(orders.slice().sort((a, b) => Math.abs(a.price - price) - Math.abs(b.price - price)).slice(0, 10).map((o) => o.id));
    orderObjs.forEach((obj, id) => {
      if (nearest.has(id) && !obj.label && !obj.dying) {
        const tag = /TAKE/.test(obj.type) ? 'TP  ' : /STOP|LOSS/.test(obj.type) ? 'SL  ' : obj.side === 'buy' ? 'BUY ' : 'SELL';
        obj.label = label(`${tag} ${A.fmtPx(obj.price)}`, obj.side === 'buy' ? A.C.bull : A.C.bear, 0.95);
        obj.label.position.set(W / 2 + 1.4, 0, BACK + DEPTH + 0.2);
        obj.group.add(obj.label);
      } else if ((!nearest.has(id) || obj.dying) && obj.label) {
        obj.group.remove(obj.label); obj.label.material.map.dispose(); obj.label.material.dispose(); obj.label = null;
      }
    });
  }

  /* support / resistance */
  function syncSR() {
    srObjs.forEach((o) => { root.remove(o.group); o.band.geometry.dispose(); o.band.material.dispose(); o.dash.geometry.dispose(); o.dash.material.dispose(); o.label.material.map.dispose(); o.label.material.dispose(); });
    srObjs.length = 0;
    if (!ind) return;
    ind.sr.res.concat(ind.sr.sup).forEach((l) => {
      const group = new T.Group();
      const isR = l.name[0] === 'R';
      const band = new T.Mesh(new T.PlaneGeometry(W + 1, 1), new T.MeshBasicMaterial({ color: C.ink, transparent: true, opacity: 0.05, blending: T.AdditiveBlending, depthWrite: false, side: T.DoubleSide }));
      band.position.z = BACK + 0.01;
      const dash = line([new T.Vector3(-W / 2 - 0.5, 0, 0.05), new T.Vector3(W / 2 + 0.5, 0, 0.05)], C.ink, 0.7, true);
      const lab = label(`${isR ? '压力' : '支撑'} ${l.name} ${A.fmtPx(l.price)}${l.touches > 1 ? ' ×' + l.touches : ''}`, A.C.ink, 0.9, { right: true });
      lab.position.set(-W / 2 - 0.7, 0, 0.05);
      group.add(band, dash, lab);
      group.visible = vis.sr;
      root.add(group);
      srObjs.push({ group, band, dash, label: lab, l });
    });
  }

  /* futures positions: entry and liquidation lines */
  let posKey = '';
  function syncPositions(list) {
    const key = list.map((p) => [p.side, A.fmtPx(p.entry), A.fmtAmt(p.size), A.fmtPx(p.liq)].join('|')).join(';');
    if (key === posKey) { posObjs.forEach((o, i) => { o.p = list[i] || o.p; }); return; }
    posKey = key;
    posObjs.forEach((o) => { root.remove(o.group); o.group.traverse((x) => { if (x.geometry && !(x instanceof T.Sprite)) x.geometry.dispose(); if (x.material) { if (x.material.map) x.material.map.dispose(); x.material.dispose(); } }); });
    posObjs.length = 0;
    list.forEach((p) => {
      const group = new T.Group();
      const entry = line([new T.Vector3(-W / 2 - 0.5, 0, 0.1), new T.Vector3(W / 2 + 0.5, 0, 0.1)], C.gold, 0.9, true);
      const sat = new T.Mesh(new T.OctahedronGeometry(0.32, 0), new T.MeshBasicMaterial({ color: C.gold, wireframe: true }));
      sat.position.set(-W / 2 - 0.2, 0, 0.1);
      const tag = label(`${p.side === 'short' ? 'SHORT' : 'LONG'} ${A.fmtAmt(p.size)} @ ${A.fmtPx(p.entry)}`, A.C.gold, 0.9, { right: true });
      tag.position.set(-W / 2 - 0.8, 0, 0.1);
      group.add(entry, sat, tag);
      let liq = null, liqTag = null;
      if (p.liq) {
        liq = line([new T.Vector3(-W / 2 - 0.5, 0, 0.1), new T.Vector3(W / 2 + 0.5, 0, 0.1)], C.bear, 0.85, true);
        liqTag = label(`强平 ${A.fmtPx(p.liq)}`, A.C.bear, 0.85, { right: true });
        liqTag.position.set(-W / 2 - 0.8, 0, 0.1);
        group.add(liq, liqTag);
      }
      root.add(group);
      posObjs.push({ group, entry, sat, tag, liq, liqTag, p });
    });
  }

  /* indicators: MA tubes and Bollinger ribbon */
  function seriesPoints(arr) {
    const off = candles.length - Math.min(N, candles.length);
    const pts = [];
    for (let i = off; i < candles.length; i++) if (arr[i] !== null && arr[i] !== undefined) pts.push(new T.Vector3(xOf(i - off), yOf(arr[i]), 0));
    return pts;
  }
  let lastRebuild = 0;
  function rebuildIndicators(force) {
    const nowT = performance.now();
    if (!force && nowT - lastRebuild < 250) return;
    lastRebuild = nowT;
    ['ma7', 'ma25', 'ma99'].forEach((k) => {
      const pts = seriesPoints(ind[k]);
      if (tubes[k]) { root.remove(tubes[k]); tubes[k].geometry.dispose(); tubes[k].material.dispose(); tubes[k] = null; }
      if (pts.length < 2) return;
      const curve = new T.CatmullRomCurve3(pts);
      const geo = new T.TubeGeometry(curve, Math.min(256, pts.length * 3), 0.045, 6, false);
      tubes[k] = new T.Mesh(geo, new T.MeshBasicMaterial({ color: C[k], transparent: true, opacity: 0.92, blending: T.AdditiveBlending, depthWrite: false }));
      tubes[k].visible = vis[k];
      root.add(tubes[k]);
    });
    if (bollMesh) { root.remove(bollMesh, bollLines); bollMesh.geometry.dispose(); bollMesh.material.dispose(); bollLines.children.forEach((l) => { l.geometry.dispose(); l.material.dispose(); }); bollMesh = null; }
    const b = ind.boll, off = candles.length - Math.min(N, candles.length);
    const upPts = [], loPts = [], verts = [];
    for (let i = off; i < candles.length; i++) {
      if (b.up[i] === null) continue;
      const x = xOf(i - off);
      upPts.push(new T.Vector3(x, yOf(b.up[i]), -0.4)); loPts.push(new T.Vector3(x, yOf(b.lo[i]), -0.4));
    }
    if (upPts.length > 1) {
      for (let i = 0; i < upPts.length - 1; i++) {
        const a = upPts[i], c = upPts[i + 1], d = loPts[i], e = loPts[i + 1];
        verts.push(a.x, a.y, a.z, d.x, d.y, d.z, c.x, c.y, c.z, c.x, c.y, c.z, d.x, d.y, d.z, e.x, e.y, e.z);
      }
      const g = new T.BufferGeometry(); g.setAttribute('position', new T.Float32BufferAttribute(verts, 3));
      bollMesh = new T.Mesh(g, new T.MeshBasicMaterial({ color: C.ma25, transparent: true, opacity: 0.06, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false }));
      bollLines = new T.Group();
      bollLines.add(line(upPts, C.ma25, 0.45), line(loPts, C.ma25, 0.45));
      bollMesh.visible = bollLines.visible = vis.boll;
      root.add(bollMesh, bollLines);
    }
  }

  function spawnBeam(t) {
    const color = t.side === 'buy' ? C.bull : C.bear;
    const i = candleIndexOf(t.time);
    const x = xOf(i), y = yOf(t.price);
    const beam = new T.Mesh(new T.CylinderGeometry(0.05, 0.05, H + 4, 8, 1, true), new T.MeshBasicMaterial({ color, transparent: true, opacity: 0.9, blending: T.AdditiveBlending, depthWrite: false }));
    beam.position.set(x, (FLOOR + H / 2 + 2) / 2, 0);
    const flash = glow(color, 4.2, 1); flash.position.set(x, y, 0.2);
    const ring = line(Array.from({ length: 65 }, (_, k) => { const a = (k / 64) * Math.PI * 2; return new T.Vector3(Math.cos(a), 0, Math.sin(a)); }), color, 0.9);
    ring.position.set(x, FLOOR + 0.02, 0);
    root.add(beam, flash, ring);
    beams.push({ beam, flash, ring, y, t: 0 });
  }

  /* ---------------------------------------------------------- frame ---- */
  function loop() {
    requestAnimationFrame(loop);
    const dt = Math.min(clock.getDelta(), 0.05);
    if (!active || !visible || document.hidden || !state || !candles.length) return;
    const t = clock.elapsedTime, now = performance.now();

    // camera: gentle sway in front of the table when idle
    const idle = now - orbit.last > 4000 && !A.reduceMotion;
    if (A.camMode === 'follow' && now - orbit.last > 2000) {
      orbit.cth = A.lerp(orbit.cth, Math.PI / 2, 0.05); orbit.tph = A.lerp(orbit.tph, LEVEL_PH, 0.05);
    }
    const th = orbit.cth + (idle && A.camMode !== 'follow' ? 0.3 * Math.sin(t * 0.11) : 0);
    orbit.th = A.lerp(orbit.th, th, 0.04);
    orbit.ph = A.lerp(orbit.ph, orbit.tph, 0.08);
    orbit.r = A.lerp(orbit.r, orbit.tr, 0.08);
    if (A.camMode === 'follow' && !A.holoTrack) {
      // centred: straight-on front view of the whole chart, no sway
      camera.position.set(0.8 + orbit.r * Math.sin(orbit.ph) * Math.cos(orbit.th), 0.6 + orbit.r * Math.cos(orbit.ph), orbit.r * Math.sin(orbit.ph) * Math.sin(orbit.th));
      camera.lookAt(0.8, 0.6, 0);
    } else if (A.camMode === 'follow') {
      // keep the newest candle and the live price in the middle of the screen
      const tx = xOf(N - 1) - 3.2, ty = yOf(state.ticker.last);
      fx = A.lerp(fx || tx, tx, 0.08); fy = A.lerp(fy || ty, ty, 0.08);
      const r = orbit.r * 0.78;
      camera.position.set(fx + r * Math.sin(orbit.ph) * Math.cos(orbit.th), fy + r * Math.cos(orbit.ph), r * Math.sin(orbit.ph) * Math.sin(orbit.th));
      camera.lookAt(fx, fy, 0);
    } else {
      camera.position.set(orbit.r * Math.sin(orbit.ph) * Math.cos(orbit.th), orbit.r * Math.cos(orbit.ph) + 1.2, orbit.r * Math.sin(orbit.ph) * Math.sin(orbit.th));
      camera.lookAt(1.2, 1.6, 0);
    }

    // vertical frame easing; indicators follow the frame
    const prevLo = lo, prevHi = hi;
    lo = A.lerp(lo, tLo, 0.06); hi = A.lerp(hi, tHi, 0.06);
    if (Math.abs(lo - prevLo) + Math.abs(hi - prevHi) > (hi - lo) * 0.002) rebuildIndicators();

    // candles
    const view = candles.slice(-N);
    const start = N - view.length;
    pool.forEach((o, i) => {
      const k = view[i - start];
      const on = !!k;
      o.body.visible = o.edge.visible = o.wick.visible = o.refl.visible = on;
      if (!on) return;
      const up = k[4] >= k[1], col = up ? C.bull : C.bear;
      const x = xOf(i - start), w = (W / N) * 0.6;
      const y0 = yOf(Math.min(k[1], k[4])), y1 = yOf(Math.max(k[1], k[4])), h = Math.max(0.04, y1 - y0);
      o.body.position.set(x, (y0 + y1) / 2, 0); o.body.scale.set(w, h, w); o.body.material.color.copy(col);
      o.edge.position.copy(o.body.position); o.edge.scale.copy(o.body.scale); o.edge.material.color.copy(col);
      const wh = Math.max(0.04, yOf(k[2]) - yOf(k[3]));
      o.wick.position.set(x, (yOf(k[2]) + yOf(k[3])) / 2, 0); o.wick.scale.y = wh; o.wick.material.color.copy(col);
      o.refl.position.set(x, FLOOR - (o.body.position.y - FLOOR), 0); o.refl.scale.copy(o.body.scale); o.refl.material.color.copy(col);
      const isLast = i - start === view.length - 1;
      o.body.material.opacity = isLast ? 0.5 + 0.15 * Math.sin(t * 4) : 0.38;
    });

    // price
    const price = state.ticker.last;
    const yP = yOf(price);
    priceLine.position.y = yP;
    priceCore.position.set(xOf(view.length - 1 + start), yP, 0.1);
    priceCore.material.opacity = 0.7 + 0.3 * Math.sin(t * 3);
    const txt = A.fmtPx(price);
    if (txt !== priceTagText) {
      dropSprite(priceTag);
      priceTag = holoLabel(txt);
      priceTag.position.set(W / 2 + 1.7, yP, 0.02);
      root.add(priceTag);
      priceTagText = txt;
    }
    priceTag.position.y = yP;
    priceTag.material.opacity = 0.88 + 0.12 * Math.sin(t * 2.4);

    // orders
    orderObjs.forEach((o, id) => {
      const y = yOf(o.price);
      o.group.position.y = y;
      const age = Math.min(1, (now - o.born) / 600);
      const near = A.clamp(1 - Math.abs(o.price - price) / ((hi - lo) * 0.7), 0.25, 1);
      let a = age;
      if (o.dying) {
        const k = (now - o.dying) / 700;
        a *= Math.max(0, 1 - k);
        if (k >= 1) { disposeOrder(o); orderObjs.delete(id); return; }
      }
      const inView = y > -H / 2 - 0.3 && y < H / 2 + 0.3;
      o.group.visible = vis.orders && inView;
      o.plane.material.opacity = (0.008 + 0.03 * near * near) * a;
      o.edge.material.opacity = (0.22 + 0.6 * near) * a;
      o.bead.material.opacity = a;
      if (o.label) o.label.material.opacity = 0.95 * a;
    });

    // S/R bands follow the frame
    srObjs.forEach((o) => {
      const y = yOf(o.l.price);
      o.group.position.y = y;
      const th2 = Math.max(0.12, yOf(o.l.hi) - yOf(o.l.lo) + 0.1);
      o.band.scale.y = th2;
      o.group.visible = vis.sr && y > -H / 2 - 0.2 && y < H / 2 + 0.2;
      o.dash.material.opacity = 0.45 + 0.25 * Math.sin(t * 2 + o.l.price);
    });

    // positions
    posObjs.forEach((o) => {
      const ye = yOf(o.p.entry);
      o.entry.position.y = ye; o.sat.position.y = ye; o.tag.position.y = ye;
      o.sat.rotation.y += dt * 1.2; o.sat.rotation.x += dt * 0.7;
      if (o.liq) {
        const yl = yOf(o.p.liq);
        const show = yl > -H / 2 - 0.3 && yl < H / 2 + 0.3;
        o.liq.visible = o.liqTag.visible = show;
        o.liq.position.y = yl; o.liqTag.position.y = yl;
      }
    });

    // ruler on the back wall
    const ticks = A.niceTicks(lo, hi, 6);
    const rk = ticks.join(',');
    if (rk !== rulerKey) {
      rulerObjs.forEach((r) => { root.remove(r.l); r.l.geometry.dispose(); r.l.material.dispose(); dropSprite(r.s); });
      rulerObjs.length = 0;
      ticks.forEach((v) => {
        const l = line([new T.Vector3(-W / 2 - 1, 0, BACK + 0.02), new T.Vector3(W / 2 + 1, 0, BACK + 0.02)], C.ink, 0.08);
        const s = label(A.fmtPx(v), A.C.ink3, 0.85); s.position.set(W / 2 + 1.2, 0, BACK + 0.02);
        root.add(l, s);
        rulerObjs.push({ v, l, s });
      });
      rulerKey = rk;
    }
    rulerObjs.forEach((r) => { const y = yOf(r.v); r.l.position.y = y; r.s.position.y = y; const show = y > -H / 2 - 0.2 && y < H / 2 + 0.6; r.l.visible = r.s.visible = show; });

    // time labels along the table front
    const tk = view.length ? view[0][0] + '|' + view.length : '';
    if (tk !== timeKey) {
      timeObjs.forEach(dropSprite); timeObjs.length = 0;
      for (let i = 0; i < view.length; i += 16) {
        const s = label(A.hhmmss(view[i][0]).slice(0, 5), A.C.ink3, 0.8);
        s.center.set(0.5, 0.5);
        s.position.set(xOf(i + start), FLOOR + 0.05, 3.2);
        root.add(s); timeObjs.push(s);
      }
      timeKey = tk;
    }

    // beams
    for (let i = beams.length - 1; i >= 0; i--) {
      const b = beams[i];
      b.t += dt;
      const k = b.t / 1.6;
      b.beam.material.opacity = Math.max(0, 0.9 * (1 - k));
      b.beam.scale.x = b.beam.scale.z = 1 + k * 2.5;
      b.flash.material.opacity = Math.max(0, 1 - k * 1.8);
      const rs = 0.4 + k * 3.5; b.ring.scale.set(rs, 1, rs); b.ring.material.opacity = Math.max(0, 0.9 * (1 - k));
      if (k >= 1) {
        root.remove(b.beam, b.flash, b.ring);
        b.beam.geometry.dispose(); b.beam.material.dispose(); b.flash.material.dispose(); b.ring.geometry.dispose(); b.ring.material.dispose();
        beams.splice(i, 1);
      }
    }

    renderer.render(scene, camera);
  }
})();
