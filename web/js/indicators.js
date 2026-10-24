/* ASTRA indicators: moving averages, Bollinger bands, swing-based support / resistance.
   Pure functions over candles [[t, o, h, l, c, v], ...]. */
(function () {
  const A = (window.ASTRA = window.ASTRA || {});
  const I = {};
  A.Ind = I;

  /* simple moving average of closes; null until enough data */
  I.sma = function (candles, n) {
    const out = new Array(candles.length).fill(null);
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
      sum += candles[i][4];
      if (i >= n) sum -= candles[i - n][4];
      if (i >= n - 1) out[i] = sum / n;
    }
    return out;
  };

  /* Bollinger bands (n, k): {mid, up, lo} arrays */
  I.boll = function (candles, n, k) {
    n = n || 20; k = k || 2;
    const mid = I.sma(candles, n), up = mid.slice(), lo = mid.slice();
    for (let i = n - 1; i < candles.length; i++) {
      let v = 0;
      for (let j = i - n + 1; j <= i; j++) v += Math.pow(candles[j][4] - mid[i], 2);
      const sd = Math.sqrt(v / n);
      up[i] = mid[i] + k * sd; lo[i] = mid[i] - k * sd;
    }
    return { mid, up, lo };
  };

  /* Support / resistance from swing pivots.
     A pivot high is a candle whose high is the highest within +-w candles (pivot low likewise).
     Pivots closer than tol (fraction of price) are merged into one level; strength = touches.
     Returns the strongest/nearest levels above (resistance) and below (support) the price. */
  I.levels = function (candles, price, opts) {
    opts = opts || {};
    const w = opts.window || 3, tol = opts.tol || 0.0018, keep = opts.keep || 2;
    if (!candles || candles.length < w * 2 + 3 || !price) return { res: [], sup: [] };
    const piv = [];
    for (let i = w; i < candles.length - w; i++) {
      let isH = true, isL = true;
      for (let j = i - w; j <= i + w; j++) {
        if (j === i) continue;
        // ties: the earlier candle wins, so equal neighbouring highs/lows still form a pivot
        if (j < i ? candles[j][2] >= candles[i][2] : candles[j][2] > candles[i][2]) isH = false;
        if (j < i ? candles[j][3] <= candles[i][3] : candles[j][3] < candles[i][3]) isL = false;
      }
      if (isH) piv.push({ p: candles[i][2], i, kind: 'h' });
      if (isL) piv.push({ p: candles[i][3], i, kind: 'l' });
    }
    // cluster
    piv.sort((a, b) => a.p - b.p);
    const clusters = [];
    piv.forEach((pv) => {
      const c = clusters[clusters.length - 1];
      if (c && Math.abs(pv.p - c.p) / c.p <= tol) {
        c.sum += pv.p; c.n += 1; c.p = c.sum / c.n; c.last = Math.max(c.last, pv.i);
        c.lo = Math.min(c.lo, pv.p); c.hi = Math.max(c.hi, pv.p);
      } else clusters.push({ p: pv.p, sum: pv.p, n: 1, last: pv.i, lo: pv.p, hi: pv.p });
    });
    // score: touches matter most, then recency, then closeness
    const len = candles.length;
    const score = (c) => c.n * 2 + (c.last / len) - Math.abs(c.p - price) / price * 40;
    const res = clusters.filter((c) => c.p > price * 1.0004).sort((a, b) => score(b) - score(a)).slice(0, keep).sort((a, b) => a.p - b.p);
    const sup = clusters.filter((c) => c.p < price * 0.9996).sort((a, b) => score(b) - score(a)).slice(0, keep).sort((a, b) => b.p - a.p);
    const fmt = (c, k, tag) => ({ price: c.p, touches: c.n, lo: c.lo, hi: c.hi, name: tag + (k + 1) });
    return { res: res.map((c, k) => fmt(c, k, 'R')), sup: sup.map((c, k) => fmt(c, k, 'S')) };
  };

  /* convenience: everything the holo table needs */
  I.compute = function (candles, price) {
    return {
      ma7: I.sma(candles, 7),
      ma25: I.sma(candles, 25),
      ma99: I.sma(candles, 99),
      boll: I.boll(candles, 20, 2),
      sr: I.levels(candles, price),
    };
  };

  if (typeof module !== 'undefined') module.exports = I;
})();
