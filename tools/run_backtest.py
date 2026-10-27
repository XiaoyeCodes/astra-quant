"""Backtest every ASTRA strategy family against buy-and-hold BTC on cached Bitget candles.

  python tools/fetch_history.py --days 730 BTCUSDT ETHUSDT SOLUSDT
  python tools/run_backtest.py                 # full period + 4 half-year segments
  python tools/run_backtest.py --fee 0.002     # stress: the 0.2% seen in OctoBot paper fills

Writes data/backtest/result-<fee>.json and prints a Markdown summary.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))

from app import backtest as bt  # noqa: E402


def load(folder, symbol):
    with open(os.path.join(folder, f"{symbol}_1h.json"), encoding="utf-8") as fh:
        return json.load(fh)


def lineup(verdicts):
    """(key, factory): key is the ASTRA profile or scheme the result describes in the cockpit."""
    return [
        ("hold_btc", lambda: bt.HoldBTC()),
        ("astra_idle", lambda: bt.Idle()),
        ("astra_spot_grid", lambda: bt.Grid(8, 0.005, name="现货网格 8×0.5%（ASTRA 参数）")),
        ("grid_wide", lambda: bt.Grid(10, 0.0075, name="现货网格 10×0.75%（报告选项 A）")),
        ("astra_trailing_grid", lambda: bt.Grid(8, 0.005, trailing=True, name="移动网格 8×0.5%")),
        # 2026-10-27 conservative parameters, chosen from a 64-combination sweep (in-sample: the
        # gain comes mostly from the first year, the second year is about flat)
        ("astra_spot_dca", lambda: bt.DCA(every=72, entry=0.04, step=0.05, sec_size=0.06, tp=0.04, sl=0.08,
                                          max_hold=0.5, name="分批补仓（保守：3 天一轮、间距 5%、止盈 4%、止损 8%、最多半仓）")),
        ("dca_old", lambda: bt.DCA(name="分批补仓（旧参数：4h、1.5%、止盈 1.2%）")),
        ("astra_trend", lambda: bt.Trend()),
        ("astra_index", "index"),
        ("rule_regime:keep", lambda: bt.RegimeSwitch(verdicts, "keep", name="指标选策略·交接 keep（原行为）")),
        ("rule_regime:flatten_on_idle", lambda: bt.RegimeSwitch(verdicts, "flatten_on_idle",
                                                                name="指标选策略·交接 flatten_on_idle")),
        ("rule_regime:flatten", lambda: bt.RegimeSwitch(verdicts, "flatten", name="指标选策略·交接 flatten")),
    ]


def run_one(make, btc, eth, sol, costs, start, end):
    if make == "index":
        s = bt.Index({"BTC/USDT": 0.5, "ETH/USDT": 0.3, "SOL/USDT": 0.2})
        r = bt.run(s, btc, costs=costs, others={"ETH/USDT": eth, "SOL/USDT": sol}, warmup=start, end=end)
    else:
        s = make()
        r = bt.run(s, btc, costs=costs, warmup=start, end=end)
    r.pop("curve")
    if hasattr(s, "switches"):
        r["switches"] = s.switches
    return r


def pct(v):
    return "—" if v is None else f"{v * 100:+.1f}%"


def main():
    sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles default to GBK
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/candles")
    ap.add_argument("--fee", type=float, default=0.001)
    ap.add_argument("--slippage", type=float, default=0.0005)
    ap.add_argument("--segments", type=int, default=4)
    ap.add_argument("--out", default="data/backtest")
    ap.add_argument("--summary", help="also write the compact summary the cockpit shows (JSON)")
    args = ap.parse_args()
    btc, eth, sol = (load(args.data, s) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT"))
    assert [c[0] for c in btc] == [c[0] for c in eth] == [c[0] for c in sol], "candles not aligned"
    costs = bt.Costs(maker=args.fee, taker=args.fee, slippage=args.slippage)
    t0 = time.time()
    verdicts = bt.regime_verdicts(btc)
    share = {r: sum(1 for v in verdicts if v == r) / len(verdicts) for r in ("range", "up", "down", "volatile")}
    warm = 200  # first candle with a radar verdict: every run starts here so all see the same market
    periods = [("全期", warm, len(btc))] + [
        (f"第{k + 1}段", max(a, warm), b) for k, (a, b) in enumerate(bt.segments(len(btc), args.segments))]
    out = {"fee": args.fee, "slippage": args.slippage, "regimeShare": share, "periods": []}
    for label, a, b in periods:
        span = f"{time.strftime('%Y-%m-%d', time.gmtime(btc[a][0] / 1000))} ~ {time.strftime('%Y-%m-%d', time.gmtime(btc[b - 1][0] / 1000))}"
        rows = [dict(run_one(make, btc, eth, sol, costs, a, b), key=key) for key, make in lineup(verdicts)]
        out["periods"].append({"label": label, "span": span, "btc": btc[b - 1][4] / btc[a][4] - 1, "results": rows})
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, f"result-fee{args.fee}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)

    print(f"# 回测结果（手续费 {args.fee:.2%}，滑点 {args.slippage:.2%}，{time.time() - t0:.0f} 秒）\n")
    print("行情雷达时间占比：" + "，".join(f"{k} {v:.0%}" for k, v in share.items()) + "\n")
    full = out["periods"][0]
    print(f"## {full['label']} {full['span']}（BTC {pct(full['btc'])}）\n")
    print("| 策略 | 总收益 | 年化 | 最大回撤 | 夏普 | 卡玛 | 手续费/本金 | 成交数 | 平均仓位 |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in full["results"]:
        calmar = "—" if r["calmar"] is None else f"{r['calmar']:.2f}"
        print(f"| {r['name']} | {pct(r['totalReturn'])} | {pct(r['cagr'])} | {r['maxDrawdown']:.1%} | "
              f"{r['sharpe']:.2f} | {calmar} | {r['feeShare']:.1%} | {r['trades']} | {r['exposure']:.0%} |")
    print("\n## 分段总收益（每段独立从 1 万 USDT 开始）\n")
    heads = [p for p in out["periods"][1:]]
    print("| 策略 | " + " | ".join(f"{p['label']} {p['span']}<br>BTC {pct(p['btc'])}" for p in heads) + " |")
    print("|---|" + "---:|" * len(heads))
    for idx, r in enumerate(full["results"]):
        cells = []
        for p in heads:
            x = p["results"][idx]
            cells.append(f"{pct(x['totalReturn'])} / 回撤 {x['maxDrawdown']:.0%}")
        print(f"| {r['name']} | " + " | ".join(cells) + " |")
    if args.summary:
        write_summary(args.summary, out)
    print(f"\n结果文件：{path}")


def write_summary(path, out):
    """Full-period headline numbers per profile/scheme for the cockpit's strategy cards."""
    full = out["periods"][0]

    def pick(r):
        return {"name": r["name"], "ret": round(r["totalReturn"], 4), "dd": round(r["maxDrawdown"], 4),
                "sharpe": round(r["sharpe"], 2), "feeShare": round(r["feeShare"], 4)}
    summary = {"span": full["span"], "fee": out["fee"], "slippage": out["slippage"],
               "btc": pick(next(r for r in full["results"] if r["key"] == "hold_btc")),
               "results": {r["key"]: pick(r) for r in full["results"]},
               "segments": [{"label": p["label"], "span": p["span"], "btc": round(p["btc"], 4),
                             "results": {r["key"]: round(r["totalReturn"], 4) for r in p["results"]}}
                            for p in out["periods"][1:]]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
