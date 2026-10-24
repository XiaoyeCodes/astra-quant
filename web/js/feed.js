/* ASTRA data feed: bridge WebSocket when available, local demo engine otherwise. */
(function () {
  const A = window.ASTRA;
  const feed = { ws: null, bridgeState: null, demo: null, usingDemo: false, connected: false, timer: null, retry: 1000, wsFails: 0, polling: false };
  A.feed = feed;

  function emit(state) {
    A.state = state;
    A.emit('state', state);
  }

  function ensureDemo() {
    if (!feed.demo) feed.demo = new A.Demo();
    if (!feed.timer) {
      feed.timer = setInterval(() => {
        if (!feed.usingDemo) return;
        feed.demo.step(700, false);
        emitDemo();
      }, 700);
    }
  }

  function emitDemo() {
    const s = feed.demo.snapshot();
    if (feed.bridgeState) {
      // keep the real link status from the bridge so the user sees what is missing
      s.links = Object.assign({}, feed.bridgeState.links);
      s.links.bridge = { ok: feed.connected, version: feed.bridgeState.links.bridge.version };
    }
    emit(s);
  }

  function useDemo(on) {
    if (on && !feed.usingDemo) { feed.usingDemo = true; ensureDemo(); emitDemo(); }
    if (!on) feed.usingDemo = false;
  }

  function handle(st) {
    feed.bridgeState = st;
    if (st.mode === 'demo') { useDemo(true); return; }
    useDemo(false);
    emit(st);
  }

  /* HTTP polling fallback when WebSockets are blocked (proxies, missing ws support) */
  function startPolling() {
    if (feed.polling) return;
    feed.polling = true;
    const tick = async () => {
      try {
        const res = await fetch('/api/state', { cache: 'no-store' });
        if (!res.ok) throw new Error('HTTP ' + res.status);
        feed.connected = true;
        handle(await res.json());
      } catch (e) {
        feed.connected = false;
        if (feed.bridgeState) feed.bridgeState.links.bridge.ok = false;
        useDemo(true);
      }
      setTimeout(tick, 1000);
    };
    tick();
  }

  function connect() {
    if (feed.polling) return;
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    let ws;
    try { ws = new WebSocket(`${proto}//${location.host}/ws`); } catch (e) { useDemo(true); return; }
    feed.ws = ws;
    let opened = false;
    ws.onopen = () => { opened = true; feed.connected = true; feed.retry = 1000; feed.wsFails = 0; };
    ws.onmessage = (ev) => {
      let st;
      try { st = JSON.parse(ev.data); } catch (e) { return; }
      handle(st);
    };
    ws.onclose = () => {
      feed.connected = false;
      feed.ws = null;
      if (!opened && ++feed.wsFails >= 2) { startPolling(); return; }
      if (feed.bridgeState) feed.bridgeState.links.bridge.ok = false;
      useDemo(true);
      setTimeout(connect, feed.retry);
      feed.retry = Math.min(feed.retry * 2, 15000);
    };
    ws.onerror = () => { try { ws.close(); } catch (e) { /* ignore */ } };
  }

  feed.setFocus = function (symbol) {
    if (feed.usingDemo) { feed.demo.setFocus(symbol); emitDemo(); return; }
    if (feed.ws && feed.ws.readyState === 1) feed.ws.send(JSON.stringify({ type: 'focus', symbol }));
    else fetch('/api/focus', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ symbol }) }).catch(() => {});
  };

  feed.control = async function (action, confirm) {
    const res = await fetch(`/api/control/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm }) });
    let data = {};
    try { data = await res.json(); } catch (e) { /* ignore */ }
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data.result;
  };

  feed.start = async function () {
    const servedByBridge = /^https?:$/.test(location.protocol) && !/claude|anthropic|usercontent/.test(location.hostname);
    if (!servedByBridge) { useDemo(true); return; }
    try {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), 1500);
      const res = await fetch('/api/health', { signal: ctl.signal, cache: 'no-store' });
      clearTimeout(timer);
      if (!res.ok) throw new Error('no bridge');
      connect();
      // show demo until the first bridge message arrives
      setTimeout(() => { if (!A.state) useDemo(true); }, 2500);
    } catch (e) {
      useDemo(true);
    }
  };
})();
