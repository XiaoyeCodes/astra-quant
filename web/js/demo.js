/* ASTRA demo engine.
   Used when no OctoBot is connected. Simulates a Bitget spot grid on BTC/USDT and a
   futures DCA (scaled entries, martingale-like sizing) on BTC/USDT:USDT. All data is fake
   and labelled as such in the UI. Produces the same state shape as the bridge. */
(function () {
  const A = window.ASTRA;
  const FEE = 0.001;
  const rnd = () => { let u = 0, v = 0; while (!u) u = Math.random(); while (!v) v = Math.random(); return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v); };

  function Demo() {
    const now = Date.now();
    this.t = now - 3 * 3600 * 1000; // warm up over the last 3 hours
    this.seq = 1;
    this.logSeq = 1;
    this.focus = A.store.get('focus', 'BTC/USDT');
    this.mk = {
      'BTC/USDT': { p: 67240, c: 67240, sig: 0.0006, ticks: [], candles: new Map() },
      'ETH/USDT': { p: 3418, c: 3418, sig: 0.0008, ticks: [], candles: new Map() },
      'BTC/USDT:USDT': { p: 67262, c: 67262, sig: 0, ticks: [], candles: new Map() },
    };
    this.orders = [];
    this.trades = [];
    this.closed = [];
    this.log = [];
    this.eqHist = [];
    this.lastSample = 0;

    // --- spot grid
    const p0 = this.mk['BTC/USDT'].p;
    const n = 18, lo = p0 * 0.965, hi = p0 * 1.035;
    this.grid = { levels: [], perQuote: 520, hold: { USDT: 10000, BTC: 0.075 } };
    for (let i = 0; i <= n; i++) this.grid.levels.push(+(lo + (i * (hi - lo)) / n).toFixed(1));
    let nearest = 0;
    this.grid.levels.forEach((L, i) => { if (Math.abs(L - p0) < Math.abs(this.grid.levels[nearest] - p0)) nearest = i; });
    this.grid.levels.forEach((L, i) => {
      if (i === nearest) return;
      if (L < p0) this.place('BTC/USDT', 'buy', 'BUY LIMIT', L, this.grid.perQuote / L, { level: i });
      else this.place('BTC/USDT', 'sell', 'SELL LIMIT', L, 0.0078, { level: i, entry: p0 });
    });

    // --- futures DCA
    this.fut = { wallet: 3000, lev: 5, size: 0, avg: 0, cool: 0, cycle: 0 };

    this.addLog('info', '演示模式启动：未连接 OctoBot，以下全部为本地模拟数据');
    this.addLog('info', `现货网格 BTC/USDT：${n} 格，区间 ${A.fmt(lo, 0)} – ${A.fmt(hi, 0)}`);

    // warm up
    const warmSteps = 2700, dt = (now - this.t) / warmSteps;
    for (let i = 0; i < warmSteps; i++) this.step(dt, i < warmSteps - 220);
    this.t = now;
    this.equityStart = this.eqHist.length ? this.eqHist[0][1] : this.equity();
    this.addLog('ok', `已回放 3 小时模拟行情：${this.trades.length} 笔成交，${this.closed.length} 笔平仓`);
  }

  Demo.prototype.addLog = function (level, msg) {
    this.log.push({ id: this.logSeq++, t: this.t, level, msg });
    if (this.log.length > 160) this.log.shift();
  };

  Demo.prototype.place = function (symbol, side, type, price, amount, meta) {
    const o = Object.assign({ id: 'd' + this.seq++, symbol, side, type, price, amount, time: this.t, source: 'Demo' }, meta || {});
    this.orders.push(o);
    return o;
  };

  Demo.prototype.fill = function (o, quiet) {
    this.orders = this.orders.filter((x) => x !== o);
    const tr = { id: 't' + this.seq++, symbol: o.symbol, side: o.side, type: o.type, price: o.price, amount: o.amount,
      cost: o.price * o.amount, fee: o.price * o.amount * FEE, time: this.t, source: 'Demo' };
    this.trades.push(tr);
    if (this.trades.length > 600) this.trades.shift();
    if (!quiet) this.addLog('fill', `${o.side === 'buy' ? '买入' : '卖出'}成交 ${o.symbol} ${A.fmtAmt(o.amount)} @ ${A.fmtPx(o.price)}`);
    return tr;
  };

  Demo.prototype.close = function (symbol, entry, exit, amount, quiet) {
    const pnl = amount * exit * (1 - FEE) - amount * entry * (1 + FEE);
    const c = { id: 'c' + this.seq++, symbol, entry, exit, amount, pnl, pnlPct: (pnl / (amount * entry)) * 100, time: this.t };
    this.closed.push(c);
    if (this.closed.length > 500) this.closed.shift();
    if (!quiet) this.addLog(pnl >= 0 ? 'ok' : 'risk', `平仓 ${symbol} ${A.fmtSigned(pnl)} USDT（${A.fmtPct(c.pnlPct)}）`);
  };

  Demo.prototype.movePrices = function (scale) {
    const b = this.mk['BTC/USDT'];
    const prevB = b.p;
    b.p = Math.max(1000, b.p * (1 + 0.0025 * (b.c - b.p) / b.p * scale + b.sig * Math.sqrt(scale) * rnd()));
    const e = this.mk['ETH/USDT'];
    e.p = Math.max(50, e.p * (1 + 0.002 * (e.c - e.p) / e.p * scale + e.sig * Math.sqrt(scale) * rnd() + 0.6 * (b.p / prevB - 1)));
    const f = this.mk['BTC/USDT:USDT'];
    f.p = b.p * (1 + 0.00032 + 0.00004 * rnd());
    return prevB;
  };

  Demo.prototype.step = function (dt, quiet) {
    this.t += dt;
    const scale = dt / 4000;
    const prevB = this.mk['BTC/USDT'].p;
    const prevF = this.mk['BTC/USDT:USDT'].p;
    this.movePrices(scale);
    const pB = this.mk['BTC/USDT'].p, pF = this.mk['BTC/USDT:USDT'].p;

    // spot grid fills
    const g = this.grid;
    for (const o of this.orders.slice()) {
      if (o.symbol !== 'BTC/USDT') continue;
      if (o.side === 'buy' && prevB > o.price && pB <= o.price) {
        this.fill(o, quiet);
        g.hold.BTC += o.amount; g.hold.USDT -= o.amount * o.price * (1 + FEE);
        const up = o.level + 1;
        if (up < g.levels.length && !this.orders.some((x) => x.symbol === 'BTC/USDT' && x.level === up))
          this.place('BTC/USDT', 'sell', 'SELL LIMIT', g.levels[up], o.amount, { level: up, entry: o.price });
      } else if (o.side === 'sell' && prevB < o.price && pB >= o.price) {
        this.fill(o, quiet);
        g.hold.BTC -= o.amount; g.hold.USDT += o.amount * o.price * (1 - FEE);
        this.close('BTC/USDT', o.entry, o.price, o.amount, quiet);
        const dn = o.level - 1;
        if (dn >= 0 && !this.orders.some((x) => x.symbol === 'BTC/USDT' && x.level === dn))
          this.place('BTC/USDT', 'buy', 'BUY LIMIT', g.levels[dn], g.perQuote / g.levels[dn], { level: dn });
      }
    }
    if (!quiet && pB < g.levels[0] * 1.002 && prevB >= g.levels[0] * 1.002) this.addLog('warn', '价格接近网格下沿：继续下跌将全部持有 BTC，浮亏扩大');
    if (!quiet && pB > g.levels[g.levels.length - 1] * 0.998 && prevB <= g.levels[g.levels.length - 1] * 0.998) this.addLog('warn', '价格接近网格上沿：继续上涨将卖光底仓');

    // futures DCA
    const f = this.fut;
    if (f.size === 0) {
      if (f.cool > 0) f.cool -= 1;
      else {
        f.cycle += 1;
        f.size = 0.02; f.avg = pF;
        f.wallet -= f.size * pF * FEE;
        this.trades.push({ id: 't' + this.seq++, symbol: 'BTC/USDT:USDT', side: 'buy', type: 'BUY MARKET', price: pF, amount: 0.02, cost: 0.02 * pF, fee: 0.02 * pF * FEE, time: this.t, source: 'Demo' });
        [[0.007, 0.02], [0.014, 0.03], [0.021, 0.045]].forEach(([d, q], k) =>
          this.place('BTC/USDT:USDT', 'buy', 'BUY LIMIT', +(pF * (1 - d)).toFixed(1), q, { dca: k + 1 }));
        this.resetExits(pF);
        if (!quiet) this.addLog('info', `合约 DCA 第 ${f.cycle} 轮：市价开多 0.02 BTC @ ${A.fmtPx(pF)}，挂 3 档补仓单`);
      }
    } else {
      for (const o of this.orders.slice()) {
        if (o.symbol !== 'BTC/USDT:USDT') continue;
        const hitDown = prevF > o.price && pF <= o.price;
        const hitUp = prevF < o.price && pF >= o.price;
        if (o.dca && hitDown) {
          this.fill(o, quiet);
          f.avg = (f.avg * f.size + o.price * o.amount) / (f.size + o.amount);
          f.size += o.amount;
          f.wallet -= o.amount * o.price * FEE;
          this.resetExits(pF);
          if (!quiet) this.addLog('warn', `补仓 ${A.fmtAmt(o.amount)} BTC，均价降到 ${A.fmtPx(f.avg)}，仓位 ${A.fmtAmt(f.size)} BTC`);
        } else if ((o.tp && hitUp) || (o.sl && hitDown)) {
          this.fill(o, quiet);
          const pnl = f.size * (o.price - f.avg) - f.size * o.price * FEE;
          f.wallet += pnl;
          this.close('BTC/USDT:USDT', f.avg, o.price, f.size, quiet);
          if (o.sl && !quiet) this.addLog('risk', `止损触发：合约仓位按 ${A.fmtPx(o.price)} 平仓`);
          this.orders = this.orders.filter((x) => x.symbol !== 'BTC/USDT:USDT');
          f.size = 0; f.avg = 0; f.cool = 30;
          break;
        }
      }
    }

    // ticks, candles, equity
    for (const [sym, m] of Object.entries(this.mk)) {
      m.ticks.push([this.t, m.p]);
      if (m.ticks.length > 900) m.ticks.shift();
      const minute = Math.floor(this.t / 60000) * 60000;
      const c = m.candles.get(minute);
      const vol = Math.abs(rnd()) * (sym.startsWith('BTC') ? 3 : 40);
      if (c) { c[2] = Math.max(c[2], m.p); c[3] = Math.min(c[3], m.p); c[4] = m.p; c[5] += vol; }
      else {
        m.candles.set(minute, [minute, m.p, m.p, m.p, m.p, vol]);
        if (m.candles.size > 240) m.candles.delete(m.candles.keys().next().value);
      }
    }
    if (this.t - this.lastSample >= 15000) {
      this.lastSample = this.t;
      this.eqHist.push([this.t, this.equity()]);
      if (this.eqHist.length > 1200) this.eqHist.shift();
    }
  };

  Demo.prototype.resetExits = function (mark) {
    const f = this.fut;
    this.orders = this.orders.filter((x) => !(x.symbol === 'BTC/USDT:USDT' && (x.tp || x.sl)));
    this.place('BTC/USDT:USDT', 'sell', 'TAKE PROFIT', +(f.avg * 1.008).toFixed(1), f.size, { tp: true });
    this.place('BTC/USDT:USDT', 'sell', 'STOP LOSS', +(f.avg * 0.985).toFixed(1), f.size, { sl: true });
  };

  Demo.prototype.position = function () {
    const f = this.fut;
    if (!f.size) return [];
    const mark = this.mk['BTC/USDT:USDT'].p;
    return [{ symbol: 'BTC/USDT:USDT', side: 'long', size: f.size, entry: f.avg, mark,
      liq: f.avg * (1 - 1 / f.lev + 0.005), margin: (f.size * f.avg) / f.lev,
      upnl: f.size * (mark - f.avg), leverage: f.lev, source: 'Demo' }];
  };

  Demo.prototype.equity = function () {
    const g = this.grid.hold;
    const pos = this.position();
    return g.USDT + g.BTC * this.mk['BTC/USDT'].p + this.fut.wallet + (pos[0] ? pos[0].upnl : 0);
  };

  Demo.prototype.book = function (sym) {
    const p = this.mk[sym].p;
    const tick = sym.startsWith('BTC') ? 0.1 : 0.01;
    const bids = [], asks = [];
    const base = sym.startsWith('BTC') ? 0.35 : 6;
    for (let i = 0; i < 20; i++) {
      const w = base * (0.4 + Math.random() * 1.6) * (1 + i * 0.08) * (Math.random() < 0.08 ? 4 : 1);
      bids.push([+(p - tick * (1 + i * (1 + Math.floor(Math.random() * 3)))).toFixed(sym.startsWith('BTC') ? 1 : 2), w]);
      asks.push([+(p + tick * (1 + i * (1 + Math.floor(Math.random() * 3)))).toFixed(sym.startsWith('BTC') ? 1 : 2), base * (0.4 + Math.random() * 1.6) * (1 + i * 0.08)]);
    }
    bids.sort((a, b) => b[0] - a[0]); asks.sort((a, b) => a[0] - b[0]);
    return { bids, asks };
  };

  Demo.prototype.setFocus = function (sym) {
    if (this.mk[sym]) { this.focus = sym; A.store.set('focus', sym); this.addLog('info', `关注交易对切换为 ${sym}`); }
  };

  Demo.prototype.snapshot = function () {
    const m = this.mk[this.focus];
    const ticks = m.ticks.slice(-600);
    const open = m.ticks[0] ? m.ticks[0][1] : m.p;
    const hi = Math.max(...m.ticks.map((t) => t[1])), lo = Math.min(...m.ticks.map((t) => t[1]));
    const book = this.book(this.focus);
    const closed = this.closed.slice(-300);
    const pos = this.position();
    const eq = this.equity();
    let peak = 0, dd = 0;
    this.eqHist.forEach(([, v]) => { peak = Math.max(peak, v); dd = Math.max(dd, (peak - v) / peak); });
    const wins = closed.filter((c) => c.pnl > 0).length;
    return {
      v: 1, ts: this.t, source: 'demo', mode: 'demo', controls: false,
      links: {
        bridge: { ok: false, version: null },
        octobot: { ok: false, error: '演示模式', url: 'http://localhost:5001' },
        bitget: { ok: false, error: '演示模式', latencyMs: null, private: false },
      },
      profile: { id: 'demo', name: '演示：现货网格 + 合约 DCA', exchangeType: 'spot', pairs: ['BTC/USDT', 'BTC/USDT:USDT'], simulated: true, tradingMode: 'GridTradingMode · DCATradingMode', exchanges: ['bitget'], referenceMarket: 'USDT' },
      focus: { symbol: this.focus, type: this.focus.includes(':') ? 'swap' : 'spot' },
      symbols: Object.keys(this.mk),
      ticker: { symbol: this.focus, last: m.p, open24: open, change: m.p - open, changePct: ((m.p - open) / open) * 100, high: hi, low: lo,
        quoteVolume: 0, bid: book.bids[0][0], ask: book.asks[0][0], ts: this.t },
      book,
      candles: Array.from(m.candles.values()).slice(-200),
      ticks,
      orders: this.orders.map((o) => ({ id: o.id, symbol: o.symbol, side: o.side, type: o.type, price: o.price, amount: o.amount, time: o.time, source: 'Demo' })),
      trades: this.trades.slice(-200),
      positions: pos,
      closed,
      equity: { value: eq, currency: 'USDT', start: this.equityStart, history: this.eqHist.slice(-480) },
      stats: { realized: closed.reduce((s, c) => s + c.pnl, 0), unrealized: pos.reduce((s, p) => s + p.upnl, 0),
        winRate: closed.length ? wins / closed.length : null, closedCount: closed.length, tradeCount: this.trades.length, maxDD: dd },
      log: this.log.slice(-120),
    };
  };

  A.Demo = Demo;
})();
