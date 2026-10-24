/* ASTRA time halo (three.js r128) — the 360° main view.
   The latest 72 one-minute candles stand in a ring like a clock face: angle = time, height = price,
   the gap is "now". MA7/25/99 and Bollinger bands wrap the ring as glowing tubes, open orders and
   support / resistance are horizontal rings, futures positions are gold / red rings, the live price
   floats above the centre. Two camera modes (A.camMode): 'orbit' = free 360° rotation,
   'follow' = the newest candle and the live price stay in the middle of the screen. */
(function () {
  const A = window.ASTRA;
  const T = window.THREE;
  const N = 72, GAP = 6, R = 9, H = 11;
  const KEEP = 36;  // candles shown in follow mode
  const FLOOR = -H / 2 - 1.3;

  const halo = {};
  A.Halo = halo;
  const vis = A.vis;
  const COL = {
    bull: new T.Color(A.C.bull), bear: new T.Color(A.C.bear), ink: new T.Color(A.C.ink), gold: new T.Color(A.C.gold),
    ma7: new T.Color('#f2d38a'), ma25: new T.Color('#c7a6ff'), ma99: new T.Color('#6fd6c4'),
  };
  // angle decreases with time, so seen from outside the ring the candles read left to right
  const slotA = (i) => -((i + GAP / 2 + 0.5) / (N + GAP)) * Math.PI * 2;
  const NOW_A = slotA(N - 1);

  let renderer, scene, camera, canvas, root, glowTex, ready = false, visible = true, active = false;
  let state = null, candles = [], ind = null, lastFocus = null, seenTrades = null, lastKey = '', lastRebuild = 0;
  let lo = 0, hi = 1, tLo = 0, tHi = 1, framed = false;
  const orbit = { th: 0.6, ph: 1.15, r: 30, tth: 0.6, tph: 1.15, tr: 30, last: -1e9 };
  const FOLLOW_PH = 1.309, FOLLOW_R = 25;  // follow view: camera ~15° above the ring
  const follow = { off: 0, ph: FOLLOW_PH, r: FOLLOW_R, toff: 0, tph: FOLLOW_PH, tr: FOLLOW_R };
  const tgt = new T.Vector3();
  const clock = new T.Clock();

  const pool = [];
  const orderObjs = new Map();
  const srObjs = [];
  const posObjs = [];
  const meshes = {};
  const fx = [];
  let core, beam, sweep, priceTag = null, priceTagText = '';
  let right = new T.Vector3(1, 0, 0);

  const yOf = (p) => ((p - lo) / (hi - lo || 1)) * H - H / 2;
  const onRing = (a, y, r) => new T.Vector3(Math.cos(a) * (r || R), y, Math.sin(a) * (r || R));

  /* ---------------------------------------------------------- helpers -- */
  function makeGlowTexture() {
    const c = document.createElement('canvas'); c.width = c.height = 128;
    const g = c.getContext('2d');
    const grd = g.createRadialGradient(64, 64, 0, 64, 64, 64);
    grd.addColorStop(0, 'rgba(255,255,255,1)'); grd.addColorStop(0.22, 'rgba(255,255,255,.5)'); grd.addColorStop(1, 'rgba(255,255,255,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    return new T.CanvasTexture(c);
  }
  function glow(color, s, o) {
    const m = new T.Sprite(new T.SpriteMaterial({ map: glowTex, color, transparent: true, opacity: o == null ? 1 : o, blending: T.AdditiveBlending, depthWrite: false }));
    m.scale.set(s, s, 1);
    return m;
  }
  function label(text, color, size, box) {
    const c = document.createElement('canvas'), g = c.getContext('2d');
    const pad = box ? 14 : 8;
    g.font = `500 40px ${A.C.mono}`;
    c.width = Math.ceil(g.measureText(text).width) + pad * 2; c.height = 58;
    g.font = `500 40px ${A.C.mono}`;
    if (box) { g.fillStyle = color; g.fillRect(0, 0, c.width, c.height); g.fillStyle = A.C.bg; } else g.fillStyle = color;
    g.textBaseline = 'middle'; g.fillText(text, pad, 31);
    const tex = new T.CanvasTexture(c); tex.minFilter = T.LinearFilter;
    const s = new T.Sprite(new T.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: false }));
    const h = 0.55 * (size || 1);
    s.scale.set((h * c.width) / c.height, h, 1);
    s.userData.base = [s.scale.x, s.scale.y];
    s.renderOrder = 10;
    return s;
  }
  function holoLabel(text) {
    const c = document.createElement('canvas'), g = c.getContext('2d');
    const fs = 64, font = `500 ${fs}px ${A.C.mono}`;
    g.font = font;
    const tw = Math.ceil(g.measureText(text).width);
    c.width = tw + 80; c.height = fs + 70;
    g.font = font; g.textAlign = 'center'; g.textBaseline = 'middle';
    const cx = c.width / 2, cy = fs / 2 + 14;
    // soft outer glow, then a crisp core
    g.shadowColor = 'rgba(160,205,255,.95)'; g.shadowBlur = 26; g.fillStyle = 'rgba(190,220,255,.55)'; g.fillText(text, cx, cy);
    g.shadowBlur = 8; g.fillStyle = '#eef6ff'; g.fillText(text, cx, cy);
    g.shadowBlur = 0;
    // leader line only (no frame)
    const y1 = fs + 22;
    const lg = g.createLinearGradient(0, y1, 0, c.height);
    lg.addColorStop(0, 'rgba(170,210,255,.8)'); lg.addColorStop(1, 'rgba(170,210,255,0)');
    g.strokeStyle = lg; g.beginPath(); g.moveTo(cx, y1); g.lineTo(cx, c.height); g.stroke();
    const tex = new T.CanvasTexture(c); tex.minFilter = T.LinearFilter;
    const sp = new T.Sprite(new T.SpriteMaterial({ map: tex, transparent: true, depthWrite: false, depthTest: false, blending: T.AdditiveBlending }));
    const h = 0.6;
    sp.scale.set((h * c.width) / c.height, h, 1);
    sp.renderOrder = 11;
    return sp;
  }
  function fit(s, k) { if (s && s.userData.base) s.scale.set(s.userData.base[0] * k, s.userData.base[1] * k, 1); }
  function freeSprite(s) { if (!s) return; if (s.parent) s.parent.remove(s); s.material.map.dispose(); s.material.dispose(); }
  function ring(r, color, opacity, dashed) {
    const pts = [];
    for (let i = 0; i <= 160; i++) { const a = (i / 160) * Math.PI * 2; pts.push(new T.Vector3(Math.cos(a) * r, 0, Math.sin(a) * r)); }
    const g = new T.BufferGeometry().setFromPoints(pts);
    const m = dashed
      ? new T.LineDashedMaterial({ color, transparent: true, opacity, dashSize: 0.35, gapSize: 0.25, depthWrite: false })
      : new T.LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
    const l = new T.Line(g, m);
    if (dashed) l.computeLineDistances();
    return l;
  }
  function disposeTree(o) {
    o.traverse((x) => {
      if (x.geometry && !(x instanceof T.Sprite)) x.geometry.dispose();
      if (x.material) { if (x.material.map && x.material.map !== glowTex) x.material.map.dispose(); x.material.dispose(); }
    });
  }

  /* ---------------------------------------------------------- static --- */
  function buildStatic() {
    const n = 1000, pos = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      const r = 70 + Math.random() * 80, u = Math.random() * 2 - 1, a = Math.random() * Math.PI * 2, s = Math.sqrt(1 - u * u);
      pos[i * 3] = r * s * Math.cos(a); pos[i * 3 + 1] = r * u; pos[i * 3 + 2] = r * s * Math.sin(a);
    }
    const sg = new T.BufferGeometry(); sg.setAttribute('position', new T.BufferAttribute(pos, 3));
    scene.add(new T.Points(sg, new T.PointsMaterial({ color: 0xc9ccd3, size: 0.2, transparent: true, opacity: 0.5, depthWrite: false })));

    // floor clock face
    [3, 6, 9, 12, 15].forEach((r, i) => { const l = ring(r, COL.ink, 0.1 - i * 0.012); l.position.y = FLOOR; root.add(l); });
    const ticks = [];
    for (let i = 0; i < N + GAP; i++) {
      const a = (i / (N + GAP)) * Math.PI * 2, l = i % 6 === 0 ? 1 : 0.45;
      ticks.push(new T.Vector3(Math.cos(a) * 15.4, FLOOR, Math.sin(a) * 15.4), new T.Vector3(Math.cos(a) * (15.4 + l), FLOOR, Math.sin(a) * (15.4 + l)));
    }
    root.add(new T.LineSegments(new T.BufferGeometry().setFromPoints(ticks), new T.LineBasicMaterial({ color: COL.ink, transparent: true, opacity: 0.22 })));
    const disc = new T.Mesh(new T.CircleGeometry(15, 96), new T.MeshBasicMaterial({ color: 0x0b0e14, transparent: true, opacity: 0.6, depthWrite: false, side: T.DoubleSide }));
    disc.rotation.x = -Math.PI / 2; disc.position.y = FLOOR - 0.01; root.add(disc);
    const outer = ring(16.8, COL.bull, 0.18, true); outer.position.y = FLOOR; outer.name = 'outer'; root.add(outer);
    root.add(new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(0, FLOOR, 0), new T.Vector3(0, H / 2 + 2.6, 0)]), new T.LineBasicMaterial({ color: COL.ink, transparent: true, opacity: 0.12 })));

    // candle pool, each box turned to face outwards
    for (let i = 0; i < N; i++) {
      const box = new T.BoxGeometry(1, 1, 1);
      const body = new T.Mesh(box, new T.MeshBasicMaterial({ transparent: true, opacity: 0.42, blending: T.AdditiveBlending, depthWrite: false }));
      const edge = new T.LineSegments(new T.EdgesGeometry(box), new T.LineBasicMaterial({ transparent: true, opacity: 0.95 }));
      const wick = new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(0, -0.5, 0), new T.Vector3(0, 0.5, 0)]), new T.LineBasicMaterial({ transparent: true, opacity: 0.7 }));
      const refl = new T.Mesh(box, new T.MeshBasicMaterial({ transparent: true, opacity: 0.07, blending: T.AdditiveBlending, depthWrite: false }));
      const a = slotA(i);
      [body, edge, wick, refl].forEach((m) => { m.rotation.y = -a; root.add(m); });
      pool.push({ body, edge, wick, refl, a });
    }

    // now: beam, core, sweep
    beam = new T.Mesh(new T.CylinderGeometry(0.035, 0.035, H + 4, 8, 1, true), new T.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.3, blending: T.AdditiveBlending, depthWrite: false }));
    beam.position.copy(onRing(NOW_A, -0.6)); root.add(beam);
    core = glow(COL.ink, 1.9, 1); root.add(core);
    sweep = new T.Line(new T.BufferGeometry().setFromPoints([new T.Vector3(0, 0, 0), new T.Vector3(R + 1.8, 0, 0)]), new T.LineBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.45 }));
    sweep.rotation.y = -NOW_A; root.add(sweep);
  }

  /* ---------------------------------------------------------- input ---- */
  function setupInteraction() {
    let drag = null;
    canvas.addEventListener('pointerdown', (e) => { drag = { x: e.clientX, y: e.clientY }; canvas.setPointerCapture(e.pointerId); orbit.last = performance.now(); });
    canvas.addEventListener('pointermove', (e) => {
      if (!drag) return;
      const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
      if (A.camMode === 'follow') {
        follow.toff = A.clamp(follow.toff - dx * 0.005, -1.2, 1.2);
        follow.tph = A.clamp(follow.tph - dy * 0.005, 0.35, 2.2);
      } else {
        orbit.tth -= dx * 0.006;
        orbit.tph = A.clamp(orbit.tph - dy * 0.006, 0.08, 3.06);
      }
      drag = { x: e.clientX, y: e.clientY };
      orbit.last = performance.now();
    });
    const end = () => { drag = null; };
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointercancel', end);
    canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      const k = 1 + Math.sign(e.deltaY) * 0.08;
      if (A.camMode === 'follow') follow.tr = A.clamp(follow.tr * k, 14, 50);
      else orbit.tr = A.clamp(orbit.tr * k, 12, 60);
      orbit.last = performance.now();
    }, { passive: false });
    canvas.addEventListener('dblclick', () => {
      if (A.camMode === 'follow') { follow.toff = 0; follow.tph = FOLLOW_PH; follow.tr = FOLLOW_R; }
      else { orbit.tph = 1.15; orbit.tr = 30; }
    });
  }

  let hudRects = [];
  function measureHud() {
    const cr = canvas.getBoundingClientRect();
    hudRects = Array.from(document.querySelectorAll('.stage .hud')).map((el) => {
      const r = el.getBoundingClientRect();
      return { l: r.left - cr.left - 12, t: r.top - cr.top - 12, r: r.right - cr.left + 12, b: r.bottom - cr.top + 12 };
    });
  }
  const tmpV = new T.Vector3();
  function hudFade(sprite) {
    if (!sprite || !hudRects.length) return 1;
    sprite.getWorldPosition(tmpV).project(camera);
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const x = (tmpV.x + 1) / 2 * w, y = (1 - tmpV.y) / 2 * h;
    // labels extend sideways from their anchor: test the anchor and a point 110px along the text
    const dir = sprite.center.x > 0.5 ? -1 : 1;
    for (const r of hudRects) {
      for (const xx of [x, x + dir * 110]) if (xx > r.l && xx < r.r && y > r.t && y < r.b) return 0.08;
    }
    return 1;
  }
  function resize() {
    measureHud();
    const r = canvas.getBoundingClientRect();
    if (!r.width || !r.height) return;
    renderer.setSize(r.width, r.height, false);
    camera.aspect = r.width / r.height;
    camera.fov = r.width / r.height < 1.1 ? 54 : 40;
    camera.updateProjectionMatrix();
  }

  halo.init = function (cv) {
    canvas = cv;
    try {
      renderer = new T.WebGLRenderer({ canvas, antialias: true, alpha: true, powerPreference: 'high-performance' });
    } catch (e) { return false; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0x000000, 0);
    scene = new T.Scene();
    scene.fog = new T.FogExp2(0x07080b, 0.012);
    camera = new T.PerspectiveCamera(40, 1, 0.1, 400);
    root = new T.Group(); scene.add(root);
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
  const wrapA = (a) => Math.atan2(Math.sin(a), Math.cos(a));
  /* called when the camera mode changes: start from where the camera is now, then ease to the target */
  halo.onCamMode = function (mode) {
    if (mode === 'follow') {
      follow.off = wrapA(orbit.th - NOW_A); follow.ph = orbit.ph;
      follow.toff = 0; follow.tph = FOLLOW_PH; follow.tr = FOLLOW_R;
    } else {
      orbit.th = orbit.tth = NOW_A + follow.off; orbit.ph = follow.ph; orbit.tph = 1.15;
    }
    orbit.last = performance.now() - 10000;
  };
  halo.setActive = function (on) { active = on; if (on && ready) { resize(); if (A.state) halo.update(A.state); } };
  halo.applyVisibility = function () { lastKey = ''; if (state) halo.update(state); };

  /* ---------------------------------------------------------- data in -- */
  halo.update = function (s) {
    if (!ready || !s) return;
    state = s;
    const sym = s.focus.symbol;
    if (sym !== lastFocus) {
      lastFocus = sym;
      orderObjs.forEach((o) => { root.remove(o.group); disposeTree(o.group); }); orderObjs.clear();
      seenTrades = null; framed = false; lastKey = '';
    }
    if (!active) return;
    const price = s.ticker ? s.ticker.last : null;
    if (!price || !s.candles || !s.candles.length) return;
    candles = s.candles.map((c) => c.slice());
    const last = candles[candles.length - 1];
    last[4] = price; last[2] = Math.max(last[2], price); last[3] = Math.min(last[3], price);
    ind = A.Ind.compute(candles, price);

    const view = candles.slice(-N), off = candles.length - view.length;
    let fLo = Infinity, fHi = -Infinity;
    const take = (v) => { if (v !== null && v !== undefined && isFinite(v)) { fLo = Math.min(fLo, v); fHi = Math.max(fHi, v); } };
    view.forEach((c) => { take(c[2]); take(c[3]); });
    ['ma7', 'ma25', 'ma99'].forEach((k) => { if (vis[k]) ind[k].slice(off).forEach(take); });
    if (vis.boll) ['up', 'lo'].forEach((k) => ind.boll[k].slice(off).forEach(take));
    if (vis.sr) ind.sr.res.concat(ind.sr.sup).forEach((l) => { if (Math.abs(l.price / price - 1) < 0.02) take(l.price); });
    const orders = s.orders.filter((o) => o.symbol === sym && o.price > 0);
    if (vis.orders) orders.forEach((o) => { if (Math.abs(o.price / price - 1) < 0.006) take(o.price); });
    const positions = (s.positions || []).filter((p) => p.symbol === sym);
    positions.forEach((p) => { if (Math.abs(p.entry / price - 1) < 0.02) take(p.entry); });
    const pad = Math.max((fHi - fLo) * 0.1, price * 0.0008);
    tLo = fLo - pad; tHi = fHi + pad;
    if (!framed) { lo = tLo; hi = tHi; framed = true; }

    syncOrders(orders, price);
    syncPositions(positions);
    const key = `${candles.length}|${last[0]}|${A.fmtPx(price)}|${JSON.stringify(vis)}|${A.camMode}`;
    if (key !== lastKey) { lastKey = key; rebuild(true); }

    const fills = s.trades.filter((t) => t.symbol === sym);
    if (seenTrades === null) seenTrades = new Set(fills.map((t) => t.id));
    else fills.forEach((t) => { if (!seenTrades.has(t.id)) { seenTrades.add(t.id); spawnFill(t); } });
  };

  function syncOrders(orders, price) {
    const live = new Set();
    orders.forEach((o) => {
      live.add(o.id);
      let obj = orderObjs.get(o.id);
      const color = o.side === 'buy' ? COL.bull : COL.bear;
      if (!obj) {
        const group = new T.Group();
        const outer = ring(R + 1.7, color, 0, /STOP|LOSS/.test(o.type));
        const inner = ring(R - 1.5, color, 0);
        group.add(outer, inner);
        root.add(group);
        obj = { group, outer, inner, lab: null, born: performance.now(), dying: 0 };
        orderObjs.set(o.id, obj);
      }
      obj.price = o.price; obj.side = o.side; obj.type = o.type; obj.dying = 0;
    });
    orderObjs.forEach((obj, id) => { if (!live.has(id) && !obj.dying) obj.dying = performance.now(); });
    const nearest = new Set(orders.slice().sort((a, b) => Math.abs(a.price - price) - Math.abs(b.price - price)).slice(0, 8).map((o) => o.id));
    orderObjs.forEach((obj, id) => {
      if (nearest.has(id) && !obj.lab && !obj.dying) {
        const tag = /TAKE/.test(obj.type) ? 'TP  ' : /STOP|LOSS/.test(obj.type) ? 'SL  ' : obj.side === 'buy' ? 'BUY ' : 'SELL';
        obj.lab = label(`${tag} ${A.fmtPx(obj.price)}`, obj.side === 'buy' ? A.C.bull : A.C.bear, 0.85);
        obj.lab.center.set(1, 0.5);
        obj.group.add(obj.lab);
      } else if ((!nearest.has(id) || obj.dying) && obj.lab) { freeSprite(obj.lab); obj.lab = null; }
    });
  }

  let posKey = '';
  function syncPositions(list) {
    const key = list.map((p) => [p.side, A.fmtPx(p.entry), A.fmtAmt(p.size), A.fmtPx(p.liq)].join('|')).join(';');
    if (key === posKey) { posObjs.forEach((o, i) => { o.p = list[i] || o.p; }); return; }
    posKey = key;
    posObjs.forEach((o) => {
      root.remove(o.group); disposeTree(o.group);
      if (o.liq) { root.remove(o.liq); o.liq.geometry.dispose(); o.liq.material.dispose(); freeSprite(o.liqTag); }
    });
    posObjs.length = 0;
    list.forEach((p) => {
      const group = new T.Group();
      const entry = ring(R + 2.6, COL.gold, 0.85, true);
      const tag = label(`${p.side === 'short' ? 'SHORT' : 'LONG'} ${A.fmtAmt(p.size)} @ ${A.fmtPx(p.entry)}`, A.C.gold, 0.85);
      tag.center.set(0, 0.5);
      group.add(entry, tag);
      let liq = null, liqTag = null;
      if (p.liq) {
        liq = ring(R + 2.6, COL.bear, 0.85, true);
        liqTag = label(`强平 ${A.fmtPx(p.liq)}`, A.C.bear, 0.8);
        liqTag.center.set(0, 0.5);
        root.add(liq); root.add(liqTag);
      }
      root.add(group);
      posObjs.push({ group, entry, tag, liq, liqTag, p });
    });
  }

  /* MA tubes, Bollinger ribbon and S/R rings: rebuilt when data or frame changes */
  function swap(key, mesh) {
    if (meshes[key]) { root.remove(meshes[key]); disposeTree(meshes[key]); }
    meshes[key] = mesh || null;
    if (mesh) root.add(mesh);
  }
  function ringPoints(arr, rad) {
    const off = candles.length - Math.min(N, candles.length), start = N - Math.min(N, candles.length), pts = [];
    const from = off; // the whole ring is shown in both camera modes
    for (let i = from; i < candles.length; i++) {
      const v = arr[i];
      if (v === null || v === undefined) continue;
      pts.push(onRing(slotA(start + i - off), yOf(v), rad));
    }
    return pts;
  }
  function tube(pts, color, radius, opacity) {
    if (pts.length < 2) return null;
    const g = new T.TubeGeometry(new T.CatmullRomCurve3(pts), Math.min(400, pts.length * 3), radius, 6, false);
    return new T.Mesh(g, new T.MeshBasicMaterial({ color, transparent: true, opacity: opacity == null ? 0.92 : opacity, blending: T.AdditiveBlending, depthWrite: false }));
  }
  function rebuild(force) {
    const now = performance.now();
    if (!force && now - lastRebuild < 250) return;
    lastRebuild = now;
    if (!ind) return;
    ['ma7', 'ma25', 'ma99'].forEach((k) => swap(k, vis[k] ? tube(ringPoints(ind[k]), COL[k], 0.05) : null));
    if (vis.boll) {
      const up = ringPoints(ind.boll.up, R - 0.05), dn = ringPoints(ind.boll.lo, R - 0.05);
      const g = new T.Group();
      if (up.length > 1) {
        const verts = [];
        for (let i = 0; i < up.length - 1; i++) {
          const a = up[i], b = up[i + 1], c = dn[i], d = dn[i + 1];
          verts.push(a.x, a.y, a.z, c.x, c.y, c.z, b.x, b.y, b.z, b.x, b.y, b.z, c.x, c.y, c.z, d.x, d.y, d.z);
        }
        const geo = new T.BufferGeometry(); geo.setAttribute('position', new T.Float32BufferAttribute(verts, 3));
        g.add(new T.Mesh(geo, new T.MeshBasicMaterial({ color: COL.ma25, transparent: true, opacity: 0.06, side: T.DoubleSide, blending: T.AdditiveBlending, depthWrite: false })));
        [up, dn].forEach((pts) => g.add(new T.Line(new T.BufferGeometry().setFromPoints(pts), new T.LineBasicMaterial({ color: COL.ma25, transparent: true, opacity: 0.5, depthWrite: false }))));
      }
      swap('boll', g);
    } else swap('boll', null);

    srObjs.forEach((o) => { root.remove(o.group); disposeTree(o.group); });
    srObjs.length = 0;
    if (vis.sr) ind.sr.res.concat(ind.sr.sup).forEach((l) => {
      const group = new T.Group();
      const r = ring(R + 0.8, COL.ink, 0.75, true);
      const lab = label(`${l.name[0] === 'R' ? '压力' : '支撑'} ${l.name} ${A.fmtPx(l.price)}${l.touches > 1 ? ' ×' + l.touches : ''}`, A.C.ink, 0.85);
      lab.center.set(0, 0.5);
      group.add(r, lab);
      root.add(group);
      srObjs.push({ group, ring: r, lab, l });
    });
  }

  function spawnFill(t) {
    const color = t.side === 'buy' ? COL.bull : COL.bear;
    const view = candles.slice(-N), start = N - view.length;
    let idx = view.length - 1;
    if (t.time) for (let i = view.length - 1; i >= 0; i--) if (t.time >= view[i][0]) { idx = i; break; }
    const a = slotA(start + idx);
    const shock = ring(1, color, 1);
    const flash = glow(color, 4, 1);
    root.add(shock, flash);
    fx.push({ shock, flash, a, price: t.price, t: 0 });
  }

  /* ---------------------------------------------------------- frame ---- */
  function loop() {
    requestAnimationFrame(loop);
    const dt = Math.min(clock.getDelta(), 0.05);
    if (!active || !visible || document.hidden || !state || !candles.length) return;
    const t = clock.elapsedTime, now = performance.now();

    const pLo = lo, pHi = hi;
    lo = A.lerp(lo, tLo, 0.06); hi = A.lerp(hi, tHi, 0.06);
    if (Math.abs(lo - pLo) + Math.abs(hi - pHi) > (hi - lo) * 0.002) rebuild(false);
    const price = state.ticker.last, yP = yOf(price);

    // camera
    const idle = now - orbit.last > 3500 && !A.reduceMotion;
    if (A.camMode === 'follow') {
      // after a manual drag, glide back to the straight-on view of the newest candle
      if (now - orbit.last > 2000) { follow.toff = A.lerp(follow.toff, 0, 0.05); follow.tph = A.lerp(follow.tph, FOLLOW_PH, 0.05); }
      follow.off = A.lerp(follow.off, follow.toff, 0.06);
      follow.ph = A.lerp(follow.ph, follow.tph, 0.08);
      follow.r = A.lerp(follow.r, follow.tr, 0.08);
      // aim a few candles back so the recent move fills the middle and the live price sits just right of centre
      // front view: camera outside the ring, newest candle straight ahead in the middle,
      // height follows the live price so it stays vertically centred
      tgt.lerp(onRing(NOW_A, yP * 0.55, R * 0.62), 0.08);   // aim just inside the newest candle so it sits mid-screen
      const az = NOW_A + follow.off;
      camera.position.set(tgt.x + Math.cos(az) * Math.sin(follow.ph) * follow.r, tgt.y + Math.cos(follow.ph) * follow.r, tgt.z + Math.sin(az) * Math.sin(follow.ph) * follow.r);
      camera.lookAt(tgt);
    } else {
      if (idle) orbit.tth -= dt * 0.12;   // clockwise seen from above: new candles enter on the right
      orbit.th = A.lerp(orbit.th, orbit.tth, 0.08);
      orbit.ph = A.lerp(orbit.ph, orbit.tph, 0.07);
      orbit.r = A.lerp(orbit.r, orbit.tr, 0.08);
      tgt.lerp(new T.Vector3(0, 0, 0), 0.08);
      camera.position.set(tgt.x + orbit.r * Math.sin(orbit.ph) * Math.cos(orbit.th), tgt.y + orbit.r * Math.cos(orbit.ph), tgt.z + orbit.r * Math.sin(orbit.ph) * Math.sin(orbit.th));
      camera.lookAt(tgt);
    }
    camera.updateMatrixWorld();
    right = new T.Vector3().setFromMatrixColumn(camera.matrixWorld, 0).setY(0);
    if (right.lengthSq() < 1e-6) right.set(1, 0, 0);
    right.normalize();
    const following = false; // labels and candles use the full-ring layout in both modes
    // label anchors: around the ring in orbit mode, beside the live point in follow mode
    const nowPt = onRing(NOW_A, 0);
    const col = (y, k) => new T.Vector3(nowPt.x + right.x * k, y, nowPt.z + right.z * k);
    const LK = following ? 0.66 : 1;
    const anchorL = (y, d) => following ? col(y, 4.6) : new T.Vector3(-right.x * (R + 1.0 + (d - R) * 0.3), y, -right.z * (R + 1.0 + (d - R) * 0.3));
    const anchorR = (y, d) => following ? col(y, d < 5 ? 1.0 : 2.6) : new T.Vector3(right.x * (R + 1.0 + (d - R) * 0.3), y, right.z * (R + 1.0 + (d - R) * 0.3));

    beam.material.opacity = following ? 0.06 : 0.16;
    sweep.material.opacity = following ? 0.2 : 0.45;
    const outer = root.getObjectByName('outer');
    if (outer && !A.reduceMotion) outer.rotation.y += dt * 0.02;

    // candles
    const view = candles.slice(-N), start = N - view.length;
    pool.forEach((o, i) => {
      const k = view[i - start];
      const age = view.length - 1 - (i - start);
      const fade = following ? A.clamp((KEEP - age) / 8, 0, 1) : 1;
      const on = !!k && fade > 0;
      o.body.visible = o.edge.visible = o.wick.visible = o.refl.visible = on;
      if (!on) return;
      o.edge.material.opacity = 0.95 * fade; o.wick.material.opacity = 0.7 * fade; o.refl.material.opacity = 0.07 * fade;
      const col = k[4] >= k[1] ? COL.bull : COL.bear;
      const y0 = yOf(Math.min(k[1], k[4])), y1 = yOf(Math.max(k[1], k[4])), h = Math.max(0.05, y1 - y0);
      const p = onRing(o.a, (y0 + y1) / 2);
      o.body.position.copy(p); o.body.scale.set(0.56, h, 0.34); o.body.material.color.copy(col);
      o.edge.position.copy(p); o.edge.scale.copy(o.body.scale); o.edge.material.color.copy(col);
      o.wick.position.copy(onRing(o.a, (yOf(k[2]) + yOf(k[3])) / 2)); o.wick.scale.y = Math.max(0.05, yOf(k[2]) - yOf(k[3])); o.wick.material.color.copy(col);
      o.refl.position.set(p.x, FLOOR - (p.y - FLOOR), p.z); o.refl.scale.copy(o.body.scale); o.refl.material.color.copy(col);
      o.body.material.opacity = (i === N - 1 ? 0.55 + 0.2 * Math.sin(t * 4) : 0.42) * fade;
    });

    // live price
    core.position.copy(onRing(NOW_A, yP)); core.material.opacity = 0.7 + 0.3 * Math.sin(t * 3);
    sweep.position.y = yP;
    const txt = A.fmtPx(price);
    const riding = true; // the live price rides above the newest candle in both camera modes
    if (txt !== priceTagText || priceTag.userData.riding !== riding) {
      freeSprite(priceTag);
      priceTag = riding ? holoLabel(txt) : label(txt, A.C.ink, 2.1, true);
      priceTag.userData.riding = riding;
      root.add(priceTag); priceTagText = txt;
    }
    if (riding) {
      // live price sits just above the newest candle's wick and moves with it
      const lastC = view[view.length - 1];
      const top = lastC ? Math.max(yOf(lastC[2]), yP) : yP;
      priceTag.position.copy(onRing(NOW_A, top + 0.15)); priceTag.center.set(0.5, 0);
      priceTag.material.opacity = 0.88 + 0.12 * Math.sin(t * 2.4);
    } else { priceTag.position.set(0, H / 2 + 2.3, 0); priceTag.center.set(0.5, 0.5); }

    // order rings
    orderObjs.forEach((o, id) => {
      const y = yOf(o.price);
      o.group.position.y = y;
      const age = Math.min(1, (now - o.born) / 600);
      const near = A.clamp(1 - Math.abs(o.price - price) / ((hi - lo) * 0.7), 0.2, 1);
      let a = age;
      if (o.dying) {
        const k = (now - o.dying) / 700;
        a *= Math.max(0, 1 - k);
        if (k >= 1) { root.remove(o.group); disposeTree(o.group); orderObjs.delete(id); return; }
      }
      o.group.visible = vis.orders && y > -H / 2 - 0.6 && y < H / 2 + 0.6;
      const c = o.side === 'buy' ? COL.bull : COL.bear;
      o.outer.material.color.copy(c); o.inner.material.color.copy(c);
      o.outer.material.opacity = (0.22 + 0.58 * near) * a;
      o.inner.material.opacity = (0.05 + 0.12 * near) * a;
      if (o.lab) { const p = anchorL(0, R + 2.2); o.lab.position.set(p.x, 0, p.z); fit(o.lab, LK); o.lab.center.set(following ? 0 : 1, 0.5); o.lab.material.opacity = (0.4 + 0.6 * near) * a * hudFade(o.lab); }
    });

    // S/R rings
    srObjs.forEach((o) => {
      const y = yOf(o.l.price);
      o.group.position.y = y;
      o.group.visible = y > -H / 2 - 0.5 && y < H / 2 + 0.5;
      const p = anchorR(0, R + 1.5); o.lab.position.set(p.x, 0, p.z); fit(o.lab, LK); o.lab.material.opacity = 0.92 * hudFade(o.lab);
      o.ring.material.opacity = 0.5 + 0.25 * Math.sin(t * 2 + o.l.price);
    });

    // positions
    posObjs.forEach((o) => {
      const ye = yOf(o.p.entry);
      o.group.position.y = ye;
      const p = anchorR(0, R + 3.2); o.tag.position.set(p.x, 0, p.z); fit(o.tag, LK); o.tag.material.opacity = hudFade(o.tag);
      if (o.liq) {
        const yl = yOf(o.p.liq);
        const show = yl > -H / 2 - 0.6 && yl < H / 2 + 0.6;
        o.liq.visible = o.liqTag.visible = show;
        o.liq.position.y = yl;
        o.liqTag.position.copy(anchorR(yl, R + 3.2));
      }
    });

    // fills: shock ring at the fill price + flash at the candle
    for (let i = fx.length - 1; i >= 0; i--) {
      const f = fx[i];
      f.t += dt;
      const k = f.t / 1.4, y = yOf(f.price), sc = R * (0.6 + k * 0.9);
      f.shock.position.y = y; f.shock.scale.set(sc, 1, sc); f.shock.material.opacity = Math.max(0, 1 - k);
      f.flash.position.copy(onRing(f.a, y)); f.flash.material.opacity = Math.max(0, 1 - k * 1.6);
      if (k >= 1) { root.remove(f.shock, f.flash); f.shock.geometry.dispose(); f.shock.material.dispose(); f.flash.material.dispose(); fx.splice(i, 1); }
    }

    renderer.render(scene, camera);
  }
})();
