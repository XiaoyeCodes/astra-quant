"""Container entrypoint for OctoBot: patch, run, and restart it when it hangs.

OctoBot can stay alive but unreachable, e.g. when the computer wakes from sleep and
the network was not ready while it started. Docker only restarts a container whose
process exits, so this supervisor:

* applies the guarded compatibility patch (patch_octobot.py);
* runs OctoBot's own entrypoint as a child in its own process group;
* forwards SIGTERM/SIGINT to that group (``docker stop``);
* pings the web API and, after a startup grace period, kills OctoBot when it has been
  unreachable for too long, then exits with status 1 so ``restart: on-failure`` starts
  a fresh container;
* otherwise exits with OctoBot's own status (the cockpit's emergency stop exits 0 and
  must stay stopped).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request

PING_URL = os.getenv("WATCHDOG_URL", "http://127.0.0.1:5001/api/ping")
STOP_URL = "http://127.0.0.1:5001/commands/stop"  # OctoBot ignores SIGTERM; this stops it cleanly
INTERVAL = float(os.getenv("WATCHDOG_INTERVAL", "30"))
GRACE = float(os.getenv("WATCHDOG_GRACE", "900"))      # first start may install plugins for minutes
TIMEOUT = float(os.getenv("WATCHDOG_TIMEOUT", "600"))  # longer than a strategy switch with plugin checks
HUNG_EXIT = 1


class Watchdog:
    """Decides when OctoBot is hung. Pure logic: time and ping results are passed in."""

    def __init__(self, started: float, grace: float = GRACE, timeout: float = TIMEOUT) -> None:
        self.started, self.grace, self.timeout = started, grace, timeout
        self.last_ok: float | None = None

    def observe(self, now: float, ok: bool) -> bool:
        """Record one ping result; True means OctoBot must be restarted."""
        if ok:
            self.last_ok = now
            return False
        if now - self.started < self.grace:
            return False
        reference = self.last_ok if self.last_ok is not None else self.started + self.grace
        return now - reference >= self.timeout


def exit_status(code: int) -> int:
    """Popen reports a signal-killed child as -N; shells and Docker use 128+N."""
    return 128 - code if code < 0 else code


def ping(url: str = PING_URL) -> bool:
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def log(msg: str) -> None:
    print(f"ASTRA supervisor: {msg}", flush=True)


def stop_group(proc: subprocess.Popen, sig: int = signal.SIGTERM, wait: float = 20) -> None:
    try:
        os.killpg(proc.pid, sig)
        proc.wait(timeout=wait)
    except ProcessLookupError:
        return
    except subprocess.TimeoutExpired:
        log("OctoBot did not stop in time, killing it")
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def main(argv: list[str]) -> int:
    subprocess.run([sys.executable, "/astra-tools/patch_octobot.py", "/octobot"], check=True)
    proc = subprocess.Popen(["/octobot/docker-entrypoint.sh", *argv], start_new_session=True)
    hung, stopping = threading.Event(), threading.Event()

    def graceful_stop() -> None:
        ping(STOP_URL)  # same call as the cockpit's emergency stop; fails harmlessly if unreachable
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            log("OctoBot did not stop through its API, sending SIGTERM")
            stop_group(proc, wait=8)

    def forward(signum, _frame):
        if stopping.is_set():
            return
        stopping.set()
        log(f"received signal {signum}, stopping OctoBot")
        threading.Thread(target=graceful_stop, daemon=True).start()

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)

    def watch() -> None:
        dog = Watchdog(time.monotonic())
        while proc.poll() is None:
            time.sleep(INTERVAL)
            if proc.poll() is not None:
                return
            if dog.observe(time.monotonic(), ping()):
                log(f"OctoBot web API unreachable for {TIMEOUT:.0f}s, restarting the container")
                hung.set()
                stop_group(proc)
                return

    threading.Thread(target=watch, daemon=True).start()
    code = proc.wait()
    if hung.is_set():
        return HUNG_EXIT
    if stopping.is_set():
        log("OctoBot stopped on request")
        return 0
    log(f"OctoBot exited with status {exit_status(code)}")
    return exit_status(code)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
