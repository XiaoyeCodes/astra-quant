"""Append-only audit trail of cockpit events: data/logs/astra-YYYY-MM-DD.jsonl, kept 30 days."""
from __future__ import annotations

import glob
import json
import logging
import os
import time

log = logging.getLogger("astra")
KEEP_DAYS = 30


class AuditLog:
    def __init__(self, folder: str, keep_days: int = KEEP_DAYS) -> None:
        self.folder = folder
        self.keep_days = keep_days
        self._day = None

    def _path(self, day: str) -> str:
        return os.path.join(self.folder, f"astra-{day}.jsonl")

    def write(self, entry: dict) -> None:
        day = time.strftime("%Y-%m-%d", time.localtime(entry.get("t", time.time() * 1000) / 1000))
        try:
            os.makedirs(self.folder, exist_ok=True)
            if day != self._day:
                self._day = day
                self.prune()
            with open(self._path(day), "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as err:
            log.warning("cannot write audit log: %s", err)

    def prune(self) -> None:
        cutoff = time.time() - self.keep_days * 86400
        for path in glob.glob(os.path.join(self.folder, "astra-*.jsonl")):
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
            except OSError:
                pass

    def recent(self, limit: int = 160) -> list[dict]:
        """Last entries across the newest files, oldest first (to refill the console after a restart)."""
        out: list[dict] = []
        for path in sorted(glob.glob(os.path.join(self.folder, "astra-*.jsonl")), reverse=True):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
            except OSError:
                continue
            for line in reversed(lines):
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
                if len(out) >= limit:
                    return out[::-1]
        return out[::-1]
