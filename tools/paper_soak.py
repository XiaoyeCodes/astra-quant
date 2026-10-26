"""Observe the local paper-trading stack without changing trading settings.

Usage: python -u tools/paper_soak.py --duration 1800 --output data/soak-...
Profiles are changed through the cockpit while this process records evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import threading
import time
import urllib.error
import urllib.request

BEIJING = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parents[1]
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def stamp():
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def scrub(text):
    return re.sub(r"(?i)\b(?:sk-[A-Za-z0-9_-]{12,}|github_pat_[A-Za-z0-9_]{20,}|"
                  r"apikey_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9_]{20,})",
                  "[REDACTED]", str(text))


def get(path, timeout=5):
    before = time.perf_counter()
    with OPENER.open("http://127.0.0.1:8080" + path, timeout=timeout) as response:
        value = json.load(response)
    return value, round((time.perf_counter() - before) * 1000, 2)


def compact(state, latency):
    profile, strategy = state.get("profile") or {}, state.get("strategy") or {}
    links, ticker = state.get("links") or {}, state.get("ticker") or {}
    ai = strategy.get("ai") or {}
    return {
        "ts": stamp(), "serverTs": state.get("ts"), "latencyMs": latency,
        "mode": state.get("mode"), "source": state.get("source"),
        "profile": profile.get("id"), "real": profile.get("real"),
        "scheme": strategy.get("scheme"), "switching": strategy.get("switching"),
        "octobot": (links.get("octobot") or {}).get("ok"),
        "bitget": (links.get("bitget") or {}).get("ok"),
        "private": (links.get("bitget") or {}).get("private"),
        "octobotSince": (links.get("octobot") or {}).get("since"),
        "bitgetLatencyMs": (links.get("bitget") or {}).get("latencyMs"),
        "last": ticker.get("last"), "tickerTs": ticker.get("ts"),
        "orders": len(state.get("orders") or []),
        "tradeIds": [str(t.get("id")) for t in state.get("trades") or []],
        "equity": (state.get("equity") or {}).get("value"),
        "stats": state.get("stats"), "stopped": strategy.get("stopped"),
        "aiError": ai.get("error"), "aiVerdict": ai.get("verdict"),
        "log": (state.get("log") or [])[-6:],
    }


def percentile(values, percent):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * percent))], 2)


def resources(output, stop):
    with (output / "resources.jsonl").open("a", encoding="utf-8") as stream:
        while not stop.is_set():
            try:
                result = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}",
                     "astra-bridge", "astra-octobot"],
                    capture_output=True, text=True, timeout=12, encoding="utf-8",
                    errors="replace",
                )
                rows = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
                stream.write(json.dumps({"ts": stamp(), "containers": rows}, ensure_ascii=False) + "\n")
                stream.flush()
            except Exception as err:
                stream.write(json.dumps({"ts": stamp(), "error": scrub(err)}, ensure_ascii=False) + "\n")
                stream.flush()
            stop.wait(30)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=1800)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "data" / "strategy.json", output / "strategy.before.json")
    shutil.copy2(ROOT / "octobot" / "user" / "config.json", output / "octobot-config.before.json")
    initial, _ = get("/api/state")
    profile = initial.get("profile") or {}
    if initial.get("mode") != "paper" or profile.get("real"):
        raise SystemExit("Paper mode is required before starting the soak test")
    started, deadline = time.monotonic(), time.monotonic() + args.duration
    start_wall = datetime.now(timezone.utc)
    meta = {"started": stamp(), "plannedSeconds": args.duration,
            "initial": compact(initial, 0), "root": str(ROOT)}
    (output / "metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"event": "started", "started": meta["started"],
                      "ends": (start_wall + timedelta(seconds=args.duration)).astimezone(BEIJING).isoformat(timespec="seconds"),
                      "output": str(output)}, ensure_ascii=False), flush=True)
    stop = threading.Event()
    resource_thread = threading.Thread(target=resources, args=(output, stop), daemon=True)
    resource_thread.start()
    samples, errors, previous, heartbeat = [], [], None, started
    interrupted = False
    try:
        with (output / "samples.jsonl").open("a", encoding="utf-8") as stream:
            while time.monotonic() < deadline:
                tick = time.monotonic()
                try:
                    state, latency = get("/api/state")
                    sample = compact(state, latency)
                    if sample["mode"] == "live" or sample["real"]:
                        raise RuntimeError("Unexpected live mode: simulation test aborted")
                    samples.append(sample)
                    stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    stream.flush()
                    key = (sample["profile"], sample["octobot"], bool(sample["switching"]))
                    if key != previous:
                        print(json.dumps({"event": "state", "ts": sample["ts"],
                                          "profile": sample["profile"], "octobot": sample["octobot"],
                                          "switching": bool(sample["switching"]),
                                          "orders": sample["orders"], "equity": sample["equity"]}, ensure_ascii=False), flush=True)
                        previous = key
                    if time.monotonic() - heartbeat >= 60:
                        print(json.dumps({"event": "minute", "elapsedSeconds": round(time.monotonic() - started),
                                          "samples": len(samples), "errors": len(errors),
                                          "profile": sample["profile"], "latencyMs": latency}, ensure_ascii=False), flush=True)
                        heartbeat = time.monotonic()
                except Exception as err:
                    failure = {"ts": stamp(), "error": scrub(err)}
                    errors.append(failure)
                    stream.write(json.dumps(failure, ensure_ascii=False) + "\n")
                    stream.flush()
                    if "Unexpected live mode" in str(err):
                        interrupted = True
                        break
                time.sleep(max(0, min(2 - (time.monotonic() - tick), deadline - time.monotonic())))
    except KeyboardInterrupt:
        interrupted = True
    finally:
        stop.set()
        resource_thread.join(timeout=15)
    durations = defaultdict(lambda: {"samples": 0, "connected": 0, "maxOrders": 0, "prices": []})
    for row in samples:
        entry = durations[row["profile"] or "unknown"]
        entry["samples"] += 1
        entry["connected"] += int(bool(row["octobot"]))
        entry["maxOrders"] = max(entry["maxOrders"], row["orders"])
        if row["last"]:
            entry["prices"].append(row["last"])
    for entry in durations.values():
        prices = entry.pop("prices")
        entry["minPrice"] = min(prices) if prices else None
        entry["maxPrice"] = max(prices) if prices else None
    latencies = [row["latencyMs"] for row in samples]
    initial_ids = set(meta["initial"]["tradeIds"])
    new_ids = {tid for row in samples for tid in row["tradeIds"]} - initial_ids
    report = {"started": meta["started"], "ended": stamp(),
              "elapsedSeconds": round(time.monotonic() - started, 2), "interrupted": interrupted,
              "samples": len(samples), "httpErrors": errors,
              "latencyMs": {"median": round(statistics.median(latencies), 2) if latencies else None,
                            "p95": percentile(latencies, .95), "max": max(latencies) if latencies else None},
              "profiles": dict(durations), "modeCounts": dict(Counter(row["mode"] for row in samples)),
              "newTradeIds": sorted(new_ids), "final": samples[-1] if samples else None}
    (output / "summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    for service in ("astra-bridge", "astra-octobot"):
        logs = subprocess.run(["docker", "logs", "--timestamps", "--since", start_wall.isoformat(), service],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
        (output / (service + ".log")).write_text(scrub(logs.stdout + logs.stderr), encoding="utf-8")
    print(json.dumps({"event": "finished", "elapsedSeconds": report["elapsedSeconds"],
                      "samples": len(samples), "errors": len(errors), "summary": str(output / "summary.json")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
