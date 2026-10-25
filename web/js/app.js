/* ASTRA boot + view manager */
(function () {
  const A = window.ASTRA;
  const views = { halo: { mod: A.Halo, cv: A.$('halo'), ok: null }, holo: { mod: A.Holo, cv: A.$('tower'), ok: null } };
  const HINT = {
    orbit: '拖动任意角度旋转 · 滚轮缩放 · 双击复位 · 1 分钟 K 线',
    follow: '正对最新 K 线 · 拖动可临时转动，松手 2 秒后自动回正 · 滚轮缩放',
  };

  function ensure(name) {
    const v = views[name];
    if (v.ok === null) v.ok = !!window.THREE && v.mod.init(v.cv);
    return v.ok;
  }
  function activate(name) {
    Object.entries(views).forEach(([k, v]) => {
      const on = k === name;
      v.cv.hidden = !on;
      if (v.ok) v.mod.setActive(on);
    });
    if (!ensure(name)) { A.$('towerFallback').hidden = false; return; }
    views[name].mod.setActive(true);
  }

  // camera controls differ per style: the halo has "follow price"; the holo table has
  // "centred view" plus an optional "follow price" switch that appears once centred is chosen
  function camUI() {
    const holo = A.viewStyle === 'holo';
    A.$('camFollowBtn').textContent = holo ? '居中显示' : '跟随现价居中';
    const t = A.$('trackToggle');
    t.hidden = !(holo && A.camMode === 'follow');
    t.classList.toggle('on', !!A.holoTrack);
    t.setAttribute('aria-pressed', String(!!A.holoTrack));
    A.$('viewHint').textContent = A.camMode === 'follow'
      ? (holo ? (A.holoTrack ? '镜头跟随最新 K 线和现价 · 松手 2 秒后自动回正' : '正面居中显示整张图表 · 松手 2 秒后自动回正')
              : '正对最新 K 线 · 拖动可临时转动，松手 2 秒后自动回正 · 滚轮缩放')
      : HINT.orbit;
  }
  A.Views = {
    setStyle(name) { A.viewStyle = name; A.store.set('viewStyle', name); activate(name); camUI(); },
    setTrack(on) { A.holoTrack = on; A.store.set('holoTrack', on); camUI(); },
    setCam(mode) {
      A.camMode = mode; A.store.set('camMode', mode); camUI();
      Object.values(views).forEach((v) => { if (v.ok && v.mod.onCamMode) v.mod.onCamMode(mode); });
    },
    setVisible(k, on) {
      A.vis[k] = on; A.store.set('holoVis', A.vis);
      Object.values(views).forEach((v) => { if (v.ok) v.mod.applyVisibility(); });
    },
  };

  if (!window.THREE) A.$('towerFallback').hidden = false;
  A.Lattice.init(A.$('lattice'));
  A.Pulse.init(A.$('book'), A.$('pulseChart'));
  A.Graph.init(A.$('graph'));
  A.Panels.init();
  A.Strategy.init();
  activate(A.viewStyle === 'holo' ? 'holo' : 'halo');
  camUI();
  A.$('trackToggle').addEventListener('click', () => A.Views.setTrack(!A.holoTrack));

  A.on('state', (s) => {
    A.Panels.update(s);
    const v = views[A.viewStyle === 'holo' ? 'holo' : 'halo'];
    if (v.ok) v.mod.update(s);
    A.Lattice.update(s);
    A.Pulse.update(s);
    A.Graph.update(s);
    A.Strategy.update(s);
  });

  A.feed.start();
})();
