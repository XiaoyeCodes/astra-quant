"""Headless browser observation for the local ASTRA cockpit (Playwright required)."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import time

from playwright.sync_api import sync_playwright

TZ = timezone(timedelta(hours=8))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration", type=int, default=1650)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--software-rendering", action="store_true",
                        help="Use CPU rendering; can substantially distort service performance")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + args.duration
    errors, messages, failures = [], [], []
    def event(kind, row):
        row = {"ts": datetime.now(TZ).isoformat(), "type": kind, **row}
        with (args.output / "browser-events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
        return row

    with sync_playwright() as pw:
        flags = (["--use-angle=swiftshader", "--enable-unsafe-swiftshader"]
                 if args.software_rendering else ["--use-angle=d3d11"])
        browser = pw.chromium.launch(channel="msedge", headless=True,
            args=flags)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(event("pageerror", {"error": str(error)})))
        page.on("console", lambda msg: messages.append(event(msg.type, {"text": msg.text}))
                if msg.type in ("error", "warning") else None)
        page.on("requestfailed", lambda req: failures.append(event("network", {"url": req.url, "error": req.failure})))
        page.goto("http://127.0.0.1:8080", wait_until="networkidle", timeout=30000)
        page.wait_for_function("window.ASTRA && window.ASTRA.state", timeout=20000)
        page.evaluate("""() => {
            window.__soakFrames = 0;
            function frame() { window.__soakFrames++; requestAnimationFrame(frame); }
            requestAnimationFrame(frame);
            window.__soakContextLoss = 0;
            for (const canvas of document.querySelectorAll('canvas'))
                canvas.addEventListener('webglcontextlost', () => window.__soakContextLoss++);
        }""")
        page.screenshot(path=str(args.output / "ui-start.png"), full_page=True)
        print(json.dumps({"event": "browser_started", "output": str(args.output)}, ensure_ascii=False), flush=True)
        last_frames, last_time = 0, time.monotonic()
        with (args.output / "ui-samples.jsonl").open("a", encoding="utf-8") as stream:
            while time.monotonic() < deadline:
                try:
                    sample = page.evaluate("""() => ({
                        mode: ASTRA.state && ASTRA.state.mode,
                        profile: ASTRA.state && ASTRA.state.profile && ASTRA.state.profile.id,
                        usingDemo: ASTRA.feed.usingDemo, connected: ASTRA.feed.connected,
                        wsState: ASTRA.feed.ws && ASTRA.feed.ws.readyState,
                        polling: ASTRA.feed.polling, stateTs: ASTRA.state && ASTRA.state.ts,
                        viewStyle: ASTRA.viewStyle,
                        frames: window.__soakFrames, contextLoss: window.__soakContextLoss,
                        heap: performance.memory && performance.memory.usedJSHeapSize,
                        bodyText: document.body.innerText.slice(0, 500)
                    })""")
                    now = time.monotonic()
                    sample["fps"] = round((sample["frames"] - last_frames) / (now - last_time), 2)
                    sample["ts"] = datetime.now(TZ).isoformat(timespec="seconds")
                    sample["pageErrors"] = len(errors)
                    sample["consoleErrors"] = sum(m["type"] == "error" for m in messages)
                    stream.write(json.dumps(sample, ensure_ascii=False) + "\n")
                    stream.flush()
                    last_frames, last_time = sample["frames"], now
                except Exception as error:
                    errors.append({"ts": datetime.now(TZ).isoformat(), "error": str(error)})
                page.wait_for_timeout(min(10000, max(1, (deadline - time.monotonic()) * 1000)))
        page.screenshot(path=str(args.output / "ui-end.png"), full_page=True)
        (args.output / "ui-summary.json").write_text(json.dumps({
            "errors": errors, "console": messages, "networkFailures": failures,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
        print(json.dumps({"event": "browser_finished", "errors": len(errors),
                          "networkFailures": len(failures)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
