/* ASTRA shared helpers */
(function () {
  const A = (window.ASTRA = window.ASTRA || {});
  const css = getComputedStyle(document.documentElement);
  const tok = (n) => css.getPropertyValue(n).trim();

  A.C = {
    bg: tok('--bg') || '#07080b',
    ink: tok('--ink') || '#e7e3d8',
    ink2: tok('--ink-2') || '#a3a6ae',
    ink3: tok('--ink-3') || '#62666f',
    line: 'rgba(231,227,216,.075)',
    line2: 'rgba(231,227,216,.15)',
    bull: tok('--bull') || '#7db3ff',
    bear: tok('--bear') || '#ff7a52',
    gold: tok('--gold') || '#d8b46a',
    ok: tok('--ok') || '#8fd3b0',
    warn: tok('--warn') || '#f1b55c',
    mono: '"JetBrains Mono", ui-monospace, Consolas, monospace',
    serif: '"Instrument Serif", Georgia, serif',
    ui: '"PingFang SC","Microsoft YaHei UI","Microsoft YaHei",sans-serif',
  };

  A.reduceMotion = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);

  A.$ = (id) => document.getElementById(id);

  A.decimalsFor = function (p) {
    const a = Math.abs(p || 0);
    if (a >= 1000) return 1;
    if (a >= 100) return 2;
    if (a >= 1) return 3;
    if (a >= 0.01) return 5;
    return 7;
  };
  A.fmt = function (v, d) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    if (d === undefined) d = 2;
    return Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  };
  A.fmtPx = (v) => A.fmt(v, A.decimalsFor(v));
  A.fmtSigned = function (v, d) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return (v >= 0 ? '+' : '−') + A.fmt(Math.abs(v), d === undefined ? 2 : d);
  };
  A.fmtPct = function (v, d) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    return (v >= 0 ? '+' : '−') + Math.abs(v).toFixed(d === undefined ? 2 : d) + '%';
  };
  A.fmtAmt = function (v) {
    if (!isFinite(v)) return '—';
    const a = Math.abs(v);
    if (a >= 1000) return A.fmt(v, 2);
    if (a >= 1) return A.fmt(v, 4);
    return A.fmt(v, 6);
  };
  A.fmtCompact = function (v) {
    if (!isFinite(v)) return '—';
    const a = Math.abs(v);
    if (a >= 1e9) return (v / 1e9).toFixed(2) + 'B';
    if (a >= 1e6) return (v / 1e6).toFixed(2) + 'M';
    if (a >= 1e3) return (v / 1e3).toFixed(1) + 'K';
    return v.toFixed(2);
  };
  A.hhmmss = function (ms, utc) {
    const d = new Date(ms);
    const p = (n) => String(n).padStart(2, '0');
    return utc
      ? `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
      : `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
  };
  A.mdhm = function (ms) {
    const d = new Date(ms);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  };
  A.signCls = (v) => (v > 0 ? 'pos' : v < 0 ? 'neg' : '');
  A.esc = (s) => String(s == null ? '' : s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

  /* Canvas helper: sizes the backing store to the element and returns a 2D context in CSS pixels */
  A.prep = function (cv) {
    const r = cv.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(r.width * dpr));
    const h = Math.max(1, Math.round(r.height * dpr));
    if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w: r.width, h: r.height };
  };

  A.alpha = function (hex, a) {
    const h = hex.replace('#', '');
    const n = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  };

  A.lerp = (a, b, t) => a + (b - a) * t;
  A.clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  /* nice ticks for axes */
  A.niceTicks = function (lo, hi, count) {
    const span = hi - lo;
    if (!(span > 0)) return [lo];
    const raw = span / Math.max(1, count);
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm < 1.5 ? 1 : norm < 3 ? 2 : norm < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(10));
    return out;
  };

  /* tiny event bus */
  const subs = {};
  A.on = (ev, fn) => ((subs[ev] = subs[ev] || []).push(fn));
  A.emit = (ev, data) => (subs[ev] || []).forEach((fn) => { try { fn(data); } catch (e) { console.error(e); } });

  A.store = {
    get(k, d) { try { const v = localStorage.getItem('astra.' + k); return v === null ? d : JSON.parse(v); } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem('astra.' + k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
  };

  /* view settings shared by both 3D styles */
  A.vis = Object.assign({ ma7: true, ma25: true, ma99: true, boll: false, sr: true, orders: true }, A.store.get('holoVis', {}));
  A.viewStyle = A.store.get('viewStyle', 'halo');   // 'halo' | 'holo'
  A.camMode = A.store.get('camMode', 'orbit');       // 'orbit' | 'follow'
  A.holoTrack = A.store.get('holoTrack', false);     // holo table, centred view: also follow the live price
})();
