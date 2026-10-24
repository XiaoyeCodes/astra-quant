/* ASTRA probability lattice: each closed trade drops as a ball and lands in its return bucket. */
(function () {
  const A = window.ASTRA;
  const EDGES = [-Infinity, -2, -1, -0.5, -0.2, 0, 0.2, 0.5, 1, 2, Infinity];
  const NAMES = ['<-2%', '-2~-1', '-1~-.5', '-.5~-.2', '-.2~0', '0~.2', '.2~.5', '.5~1', '1~2', '>2%'];
  const NB = NAMES.length;
  const ROWS = 7;

  const L = { cv: null, seen: null, landed: new Array(NB).fill(0), balls: [], queue: [], flash: new Array(NB).fill(0),
    session: { n: 0, wins: 0, pnl: 0 }, winSeries: [], lastSpawn: 0 };
  A.Lattice = L;

  const binOf = (pct) => { for (let i = 0; i < NB; i++) if (pct >= EDGES[i] && pct < EDGES[i + 1]) return i; return NB - 1; };

  L.init = function (cv) { L.cv = cv; requestAnimationFrame(L.loop); };

  L.update = function (s) {
    const closed = s.closed || [];
    if (L.seen === null || L.focusKey !== s.source) {
      L.focusKey = s.source;
      L.seen = new Set(closed.map((c) => c.id));
      L.landed = new Array(NB).fill(0);
      L.balls = []; L.queue = [];
      const intro = A.reduceMotion ? [] : closed.slice(-16);
      const introIds = new Set(intro.map((c) => c.id));
      closed.forEach((c) => { if (!introIds.has(c.id)) L.landed[binOf(c.pnlPct)]++; });
      L.queue.push(...intro);
    } else {
      closed.forEach((c) => {
        if (!L.seen.has(c.id)) {
          L.seen.add(c.id);
          L.queue.push(c);
          L.session.n++; if (c.pnl > 0) L.session.wins++; L.session.pnl += c.pnl;
        }
      });
      if (L.queue.length > 30) {
        L.queue.splice(0, L.queue.length - 30).forEach((c) => L.landed[binOf(c.pnlPct)]++);
      }
    }
    // cumulative win-rate series for the convergence inset
    let w = 0;
    L.winSeries = closed.map((c, i) => { if (c.pnl > 0) w++; return w / (i + 1); });
    L.closed = closed;
    renderStats(s);
  };

  function renderStats(s) {
    const closed = L.closed;
    const wins = closed.filter((c) => c.pnl > 0), losses = closed.filter((c) => c.pnl <= 0);
    const avg = (arr) => (arr.length ? arr.reduce((a, c) => a + c.pnl, 0) / arr.length : null);
    const total = closed.reduce((a, c) => a + c.pnl, 0);
    A.$('latTitleCount').textContent = closed.length;
    A.$('latBalls').textContent = L.session.n;
    A.$('latWin').textContent = L.session.n ? Math.round((L.session.wins / L.session.n) * 100) + '%' : '—';
    const ev = closed.length ? total / closed.length : null;
    setSigned('latEv', ev);
    setSigned('latSession', L.session.n ? L.session.pnl : null);
    A.$('latAll').textContent = closed.length;
    setSigned('latAvgWin', avg(wins));
    setSigned('latAvgLoss', avg(losses));
    setSigned('latReal', closed.length ? total : null);
  }
  function setSigned(id, v) {
    const el = A.$(id);
    el.textContent = v === null ? '—' : A.fmtSigned(v);
    el.className = v === null ? '' : A.signCls(v);
  }

  function geometry(w, h) {
    const left = 18, right = w - 18;
    const binW = (right - left) / NB;
    const pegTop = 58, pegBot = h * 0.5;
    const binTop = h * 0.56, binBot = h - 30;
    return { left, right, binW, pegTop, pegBot, binTop, binBot, cx: (left + right) / 2 };
  }

  function spawn(c, g) {
    const k = binOf(c.pnlPct);
    const xk = g.left + (k + 0.5) * g.binW;
    const pts = [[g.cx, 26]];
    for (let r = 0; r < ROWS; r++) {
      const f = (r + 1) / ROWS;
      const y = g.pegTop + (r + 0.5) * ((g.pegBot - g.pegTop) / ROWS);
      const jitter = (Math.random() - 0.5) * g.binW * 1.6 * (1 - f);
      pts.push([A.lerp(g.cx, xk, f) + jitter, y]);
    }
    pts.push([xk, g.binTop - 4]);
    L.balls.push({ c, k, pts, t: 0, dur: 1.5 + Math.random() * 0.3, trail: [] });
  }

  L.loop = function (ts) {
    requestAnimationFrame(L.loop);
    if (!L.cv || document.hidden) return;
    const { ctx, w, h } = A.prep(L.cv);
    const g = geometry(w, h);
    const dt = L.lastTs ? Math.min(0.05, (ts - L.lastTs) / 1000) : 0.016;
    L.lastTs = ts;

    if (L.queue.length && ts - L.lastSpawn > 140) { spawn(L.queue.shift(), g); L.lastSpawn = ts; }

    ctx.clearRect(0, 0, w, h);
    const C = A.C;
    const totalLanded = L.landed.reduce((a, b) => a + b, 0);
    const lossShare = totalLanded ? L.landed.slice(0, 5).reduce((a, b) => a + b, 0) / totalLanded : 0;

    // background zones
    const xBE = g.left + 5 * g.binW;
    ctx.fillStyle = A.alpha(C.bear, 0.035); ctx.fillRect(g.left, 40, xBE - g.left, g.binBot - 40);
    ctx.fillStyle = A.alpha(C.bull, 0.035); ctx.fillRect(xBE, 40, g.right - xBE, g.binBot - 40);

    // zone labels
    ctx.font = `10px ${C.mono}`;
    ctx.textBaseline = 'alphabetic';
    ctx.fillStyle = A.alpha(C.bear, 0.85);
    ctx.textAlign = 'left';
    ctx.fillText(`亏损 · ${Math.round(lossShare * 100)}%`, g.left + 8, 54);
    ctx.fillStyle = A.alpha(C.bull, 0.9);
    ctx.textAlign = 'right';
    ctx.fillText(`盈利 · ${totalLanded ? Math.round((1 - lossShare) * 100) : 0}%`, g.right - 8, 54);

    // breakeven
    ctx.strokeStyle = A.alpha(C.ink, 0.55); ctx.setLineDash([3, 4]); ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(xBE, 36); ctx.lineTo(xBE, g.binBot); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = C.bg; ctx.strokeStyle = A.alpha(C.ink, 0.6);
    ctx.font = `9.5px ${C.mono}`; ctx.textAlign = 'center';
    const bw = 64; ctx.fillRect(xBE - bw / 2, 22, bw, 15); ctx.strokeRect(xBE - bw / 2 + 0.5, 22.5, bw - 1, 14);
    ctx.fillStyle = C.ink; ctx.fillText('盈亏平衡', xBE, 33);

    // dropper
    ctx.strokeStyle = A.alpha(C.ink, 0.7);
    ctx.beginPath(); ctx.moveTo(g.cx - 9, 8); ctx.lineTo(g.cx, 20); ctx.lineTo(g.cx + 9, 8); ctx.stroke();

    // pegs
    ctx.fillStyle = A.alpha(C.ink, 0.28);
    for (let r = 0; r < ROWS; r++) {
      const y = g.pegTop + (r + 0.5) * ((g.pegBot - g.pegTop) / ROWS);
      for (let j = 0; j <= NB; j++) {
        const x = g.left + (j + (r % 2 ? 0.5 : 0)) * g.binW;
        if (x < g.left || x > g.right) continue;
        ctx.beginPath(); ctx.arc(x, y, 1.6, 0, Math.PI * 2); ctx.fill();
      }
    }

    // bins
    const maxC = Math.max(1, ...L.landed);
    const binH = g.binBot - g.binTop;
    for (let i = 0; i < NB; i++) {
      const x = g.left + i * g.binW;
      const pos = i >= 5;
      const hh = (L.landed[i] / maxC) * (binH - 16);
      const colr = pos ? C.bull : C.bear;
      L.flash[i] = Math.max(0, L.flash[i] - dt * 1.5);
      const grd = ctx.createLinearGradient(0, g.binBot - hh, 0, g.binBot);
      grd.addColorStop(0, A.alpha(colr, 0.85 + 0.15 * L.flash[i]));
      grd.addColorStop(1, A.alpha(colr, 0.35));
      ctx.fillStyle = grd;
      ctx.fillRect(x + 4, g.binBot - hh, g.binW - 8, hh);
      if (L.flash[i] > 0) { ctx.fillStyle = A.alpha(C.ink, L.flash[i] * 0.5); ctx.fillRect(x + 4, g.binBot - hh - 2, g.binW - 8, 2); }
      ctx.strokeStyle = C.line2;
      ctx.beginPath(); ctx.moveTo(x + 0.5, g.binTop); ctx.lineTo(x + 0.5, g.binBot); ctx.stroke();
      ctx.fillStyle = L.landed[i] ? C.ink2 : C.ink3;
      ctx.font = `10px ${C.mono}`; ctx.textAlign = 'center';
      if (L.landed[i]) ctx.fillText(L.landed[i], x + g.binW / 2, g.binBot - hh - 5);
      ctx.fillStyle = C.ink3; ctx.font = `9px ${C.mono}`;
      ctx.fillText(NAMES[i], x + g.binW / 2, g.binBot + 14);
    }
    ctx.strokeStyle = C.line2;
    ctx.beginPath(); ctx.moveTo(g.right - 0.5, g.binTop); ctx.lineTo(g.right - 0.5, g.binBot); ctx.moveTo(g.left, g.binBot + 0.5); ctx.lineTo(g.right, g.binBot + 0.5); ctx.stroke();

    // balls
    for (let i = L.balls.length - 1; i >= 0; i--) {
      const b = L.balls[i];
      b.t += dt / b.dur;
      const tt = Math.min(1, b.t);
      const e = tt * tt * (3 - 2 * tt) * 0.35 + tt * tt * 0.65; // eased fall
      const seg = e * (b.pts.length - 1);
      const si = Math.min(b.pts.length - 2, Math.floor(seg));
      const f = seg - si;
      const x = A.lerp(b.pts[si][0], b.pts[si + 1][0], f);
      const y = A.lerp(b.pts[si][1], b.pts[si + 1][1], f) - Math.sin(f * Math.PI) * 3;
      b.trail.push([x, y]); if (b.trail.length > 10) b.trail.shift();
      const colr = b.c.pnl > 0 ? C.bull : C.bear;
      ctx.strokeStyle = A.alpha(colr, 0.25); ctx.lineWidth = 1;
      ctx.beginPath(); b.trail.forEach(([tx, ty], k) => (k ? ctx.lineTo(tx, ty) : ctx.moveTo(tx, ty))); ctx.stroke();
      const grd = ctx.createRadialGradient(x, y, 0, x, y, 9);
      grd.addColorStop(0, A.alpha(colr, 0.55)); grd.addColorStop(1, A.alpha(colr, 0));
      ctx.fillStyle = grd; ctx.beginPath(); ctx.arc(x, y, 9, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = C.ink; ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill();
      ctx.strokeStyle = colr; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(x, y, 3.6, 0, Math.PI * 2); ctx.stroke();
      if (b.t >= 1) { L.landed[b.k]++; L.flash[b.k] = 1; L.balls.splice(i, 1); }
    }

    // convergence inset (cumulative win rate)
    const ws = L.winSeries;
    if (ws.length > 2 && w > 420) {
      const iw = 150, ih = 40, ix = g.right - iw, iy = 64;
      ctx.fillStyle = 'rgba(13,15,20,.85)'; ctx.strokeStyle = C.line2;
      ctx.fillRect(ix, iy, iw, ih); ctx.strokeRect(ix + 0.5, iy + 0.5, iw - 1, ih - 1);
      ctx.fillStyle = C.ink3; ctx.font = `8.5px ${C.mono}`; ctx.textAlign = 'left';
      ctx.fillText('累计胜率收敛', ix + 6, iy + 11);
      ctx.strokeStyle = C.ink; ctx.lineWidth = 1; ctx.beginPath();
      ws.forEach((v, k) => { const px = ix + 6 + (k / (ws.length - 1)) * (iw - 44); const py = iy + ih - 6 - v * (ih - 18); k ? ctx.lineTo(px, py) : ctx.moveTo(px, py); });
      ctx.stroke();
      ctx.fillStyle = C.ink; ctx.textAlign = 'right'; ctx.font = `10px ${C.mono}`;
      ctx.fillText((ws[ws.length - 1] * 100).toFixed(1) + '%', ix + iw - 5, iy + ih - 7);
    }

    if (!L.closed || !L.closed.length) {
      ctx.fillStyle = C.ink3; ctx.font = `12px ${C.ui}`; ctx.textAlign = 'center';
      ctx.fillText('还没有已平仓交易。策略完成第一笔买卖后，小球会从顶部落下。', w / 2, g.binTop - 18);
    }
  };
})();
