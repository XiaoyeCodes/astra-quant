const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');
const source = fs.readFileSync(path.join(__dirname, '../web/js/feed.js'), 'utf8');

async function setup() {
  const sockets = [];
  const A = {
    emit() {},
    Demo: class {
      step() {}
      snapshot() { return { mode: 'demo', source: 'demo', profile: { id: 'demo' }, links: {} }; }
    },
  };
  class Socket {
    constructor() { sockets.push(this); this.readyState = 0; }
    close() { this.readyState = 3; this.onclose(); }
  }
  vm.runInNewContext(source, {
    window: { ASTRA: A }, WebSocket: Socket,
    location: { protocol: 'http:', hostname: 'localhost', host: 'localhost:8080' },
    fetch: async () => ({ ok: true }), AbortController,
    setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1,
  });
  await A.feed.start();
  const ws = sockets[0];
  ws.readyState = 1;
  ws.onopen();
  const deliver = state => ws.onmessage({ data: JSON.stringify(state) });
  return { A, ws, deliver };
}

const paper = () => ({ mode: 'paper', source: 'bridge', profile: { id: 'astra_spot_dca' },
  equity: { value: 2205.56 }, orders: [{ id: 'real-paper-order' }],
  links: { bridge: { ok: true, version: '1.0' }, octobot: { ok: true }, bitget: { ok: true } } });

test('WebSocket loss preserves actual paper data and marks it stale', async () => {
  const { A, ws, deliver } = await setup();
  deliver(paper());
  ws.close();
  assert.equal(A.state.mode, 'paper');
  assert.equal(A.state.profile.id, 'astra_spot_dca');
  assert.equal(A.state.equity.value, 2205.56);
  assert.equal(A.state.orders[0].id, 'real-paper-order');
  assert.equal(A.state.stale, true);
  assert.equal(A.state.links.bridge.ok, false);
  assert.equal(A.feed.usingDemo, false);
});

test('bridge bootstrap does not replace previously observed data with demo orders', async () => {
  const { A, deliver } = await setup();
  deliver(paper());
  deliver({ mode: 'demo', source: 'bridge', links: {
    bridge: { ok: true, version: '1.0' }, octobot: { ok: false }, bitget: { ok: true },
  } });
  assert.equal(A.state.mode, 'paper');
  assert.equal(A.state.profile.id, 'astra_spot_dca');
  assert.equal(A.state.stale, true);
  assert.equal(A.state.links.octobot.ok, false);
});

test('fresh data replaces the frozen snapshot after reconnect', async () => {
  const { A, ws, deliver } = await setup();
  deliver(paper());
  ws.close();
  deliver({ ...paper(), equity: { value: 2200 } });
  assert.equal(A.state.equity.value, 2200);
  assert.equal(A.state.stale, undefined);
  assert.equal(A.feed.usingDemo, false);
});

test('a first visit without any actual state still supports demo preview', async () => {
  const { A, ws } = await setup();
  ws.close();
  assert.equal(A.state.mode, 'demo');
  assert.equal(A.feed.usingDemo, true);
});
