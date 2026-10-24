/* ASTRA market pulse: L2 order book ladder + live price trace with orders, fills and pulse strength. */
(function () {
  const A = window.ASTRA;
  const P = { book: null, chart: null, s: null };
  A.Pulse = P;

  P.init = function (bookCv, chartCv) {
    P.book = bookCv; P.chart = chartCv;
    const loop = () => { requestAnimationFrame(loop); if (!document.hidden && P.s) draw(); };
    requestAnimationFrame(loop);
  };
  P.update = function (s) {
    P.s = s;
    const t = s.ticker;
    A.$('pulseSym').textContent = s.focus.symbol;
    if (t) {
      const spr = t.ask && t.bid ? t.ask - t.bid : null;
      A.$('pulseMeta').textContent = `BID ${A.fmtPx(t.bid)} · ASK ${A.fmtPx(t.ask)} · SPREAD ${spr === null ? '—' : A.fmtPx(spr)}`;
    }
  };

  function draw() {
    drawBook();
    drawChart();
  }

  function drawBook() {
    const { ctx, w, h } = A.prep(P.book);
    const C = A.C;
    ctx.clearRect(0, 0, w, h);
    const book = P.s.book || { bids: [], asks: [] };
    const rows = Math.max(4, Math.floor((h - 40) / 2 / 13));
    const asks = book.asks.slice(0, rows).reverse();
    const bids = book.bids.slice(0, rows);
    const maxQ = Math.max(1e-9, ...asks.map((r) => r[1]), ...bids.map((r) => r[1]));
    ctx.font = `8.5px ${C.mono}`; ctx.fillStyle = C.ink3; ctx.textAlign = 'left';
    ctx.fillText('ORDER BOOK · L2', 0, 9);
    ctx.textAlign = 'left'; ctx.fillText('价格', 0, 22); ctx.textAlign = 'right'; ctx.fillText('数量', w, 22);
    const rowH = 13;
    let y = 30;
    const row = (r, colr) => {
      const bw = (r[1] / maxQ) * w;
      ctx.fillStyle = A.alpha(colr, 0.13); ctx.fillRect(w - bw, y, bw, rowH - 2);
      ctx.font = `10px ${C.mono}`;
      ctx.fillStyle = colr; ctx.textAlign = 'left'; ctx.fillText(A.fmtPx(r[0]), 0, y + 9.5);
      ctx.fillStyle = C.ink2; ctx.textAlign = 'right'; ctx.fillText(A.fmtCompact(r[1]), w, y + 9.5);
      y += rowH;
    };
    asks.forEach((r) => row(r, C.bear));
    const last = P.s.ticker ? P.s.ticker.last : null;
    ctx.strokeStyle = C.line2; ctx.beginPath(); ctx.moveTo(0, y + 1.5); ctx.lineTo(w, y + 1.5); ctx.stroke();
    ctx.font = `600 11.5px ${C.mono}`; ctx.fillStyle = C.ink; ctx.textAlign = 'left';
    ctx.fillText(last ? A.fmtPx(last) : '—', 0, y + 15);
    ctx.strokeStyle = C.line2; ctx.beginPath(); ctx.moveTo(0, y + 21.5); ctx.lineTo(w, y + 21.5); ctx.stroke();
    y += 24;
    bids.forEach((r) => row(r, C.bull));
    if (!book.bids.length && !book.asks.length) {
      ctx.fillStyle = C.ink3; ctx.font = `11px ${C.ui}`; ctx.textAlign = 'center'; ctx.fillText('等待订单簿数据', w / 2, h / 2);
    }
  }

  function drawChart() {
    const { ctx, w, h } = A.prep(P.chart);
    const C = A.C, s = P.s;
    ctx.clearRect(0, 0, w, h);
    const ticks = s.ticks.slice(-300);
    if (ticks.length < 2) {
      ctx.fillStyle = C.ink3; ctx.font = `11px ${C.ui}`; ctx.textAlign = 'center'; ctx.fillText('等待实时价格', w / 2, h / 2);
      return;
    }
    const padR = 62, padT = 14, pulseH = 40, padB = 18 + pulseH + 10;
    const t0 = ticks[0][0], t1 = ticks[ticks.length - 1][0];
    let lo = Infinity, hi = -Infinity;
    ticks.forEach(([, p]) => { lo = Math.min(lo, p); hi = Math.max(hi, p); });
    const m = Math.max((hi - lo) * 0.25, hi * 0.0004);
    lo -= m; hi += m;
    const X = (t) => ((t - t0) / Math.max(1, t1 - t0)) * (w - padR);
    const Y = (p) => padT + (1 - (p - lo) / (hi - lo)) * (h - padT - padB);

    // grid
    ctx.font = `9.5px ${C.mono}`; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    A.niceTicks(lo, hi, 4).forEach((v) => {
      const y = Y(v);
      ctx.strokeStyle = C.line; ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(w - padR, y + 0.5); ctx.stroke();
      ctx.fillStyle = C.ink3; ctx.fillText(A.fmtPx(v), w - padR + 8, y);
    });

    // open orders inside the visible band
    const sym = s.focus.symbol;
    s.orders.filter((o) => o.symbol === sym && o.price > lo && o.price < hi).forEach((o) => {
      const y = Y(o.price), colr = o.side === 'buy' ? C.bull : C.bear;
      ctx.strokeStyle = A.alpha(colr, 0.6); ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(0, y + 0.5); ctx.lineTo(w - padR, y + 0.5); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = colr; ctx.font = `8.5px ${C.mono}`; ctx.textAlign = 'left';
      ctx.fillText(o.side === 'buy' ? 'BUY' : 'SELL', 4, y - 6);
    });

    // area + line
    const grd = ctx.createLinearGradient(0, padT, 0, h - padB);
    grd.addColorStop(0, A.alpha(C.ink, 0.12)); grd.addColorStop(1, A.alpha(C.ink, 0));
    ctx.beginPath();
    ticks.forEach(([t, p], i) => (i ? ctx.lineTo(X(t), Y(p)) : ctx.moveTo(X(t), Y(p))));
    ctx.lineTo(X(t1), h - padB); ctx.lineTo(X(t0), h - padB); ctx.closePath();
    ctx.fillStyle = grd; ctx.fill();
    ctx.beginPath();
    ticks.forEach(([t, p], i) => (i ? ctx.lineTo(X(t), Y(p)) : ctx.moveTo(X(t), Y(p))));
    ctx.strokeStyle = C.ink; ctx.lineWidth = 1.4; ctx.stroke(); ctx.lineWidth = 1;

    // fills
    s.trades.filter((t) => t.symbol === sym && t.time >= t0 && t.time <= t1).forEach((t) => {
      const x = X(t.time), y = Y(t.price), colr = t.side === 'buy' ? C.bull : C.bear;
      ctx.fillStyle = C.bg; ctx.strokeStyle = colr; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      ctx.fillStyle = colr; ctx.beginPath(); ctx.arc(x, y, 1.6, 0, Math.PI * 2); ctx.fill();
      ctx.lineWidth = 1;
    });
    // realised pnl pills
    (s.closed || []).filter((c) => c.symbol === sym && c.time >= t0 && c.time <= t1).slice(-4).forEach((c) => {
      const x = X(c.time), y = Y(c.exit) - 16;
      const txt = A.fmtSigned(c.pnl);
      ctx.font = `9.5px ${C.mono}`;
      const tw = ctx.measureText(txt).width + 12;
      const colr = c.pnl >= 0 ? C.bull : C.bear;
      ctx.fillStyle = A.alpha(colr, 0.16); ctx.strokeStyle = A.alpha(colr, 0.7);
      const bx = A.clamp(x - tw / 2, 0, w - padR - tw);
      roundRect(ctx, bx, y - 8, tw, 15, 7); ctx.fill(); ctx.stroke();
      ctx.fillStyle = colr; ctx.textAlign = 'center'; ctx.fillText(txt, bx + tw / 2, y);
    });

    // last price tag
    const last = ticks[ticks.length - 1][1], yl = Y(last);
    ctx.strokeStyle = A.alpha(C.ink, 0.35); ctx.setLineDash([2, 3]);
    ctx.beginPath(); ctx.moveTo(X(t1), yl + 0.5); ctx.lineTo(w - padR, yl + 0.5); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = C.ink; ctx.fillRect(w - padR + 2, yl - 8, padR - 2, 16);
    ctx.fillStyle = C.bg; ctx.font = `600 10px ${C.mono}`; ctx.textAlign = 'left';
    ctx.fillText(A.fmtPx(last), w - padR + 6, yl + 0.5);
    ctx.fillStyle = C.ink; ctx.beginPath(); ctx.arc(X(t1), yl, 2.6, 0, Math.PI * 2); ctx.fill();

    // time axis
    ctx.fillStyle = C.ink3; ctx.font = `9px ${C.mono}`; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    for (let i = 0; i <= 4; i++) {
      const t = t0 + ((t1 - t0) * i) / 4;
      ctx.textAlign = i === 0 ? 'left' : i === 4 ? 'right' : 'center';
      ctx.fillText(A.hhmmss(t), (i / 4) * (w - padR), h - pulseH - 14);
    }

    // pulse strength
    const py0 = h - pulseH, ph = pulseH - 6;
    ctx.fillStyle = C.ink3; ctx.font = `8.5px ${C.mono}`; ctx.textAlign = 'left';
    ctx.fillText('PULSE · 波动强度', 0, py0 + 2);
    const rets = [];
    for (let i = 1; i < ticks.length; i++) rets.push(Math.abs(ticks[i][1] / ticks[i - 1][1] - 1));
    const sm = []; let acc = 0;
    rets.forEach((r, i) => { acc = i ? acc * 0.85 + r * 0.15 : r; sm.push(acc); });
    const mx = Math.max(1e-9, ...sm);
    ctx.beginPath();
    sm.forEach((v, i) => { const x = X(ticks[i + 1][0]); const y = py0 + ph - (v / mx) * (ph - 8); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); });
    ctx.strokeStyle = A.alpha(C.bull, 0.8); ctx.stroke();
  }

  function roundRect(ctx, x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r); ctx.closePath();
  }
  A.roundRect = roundRect;
})();
