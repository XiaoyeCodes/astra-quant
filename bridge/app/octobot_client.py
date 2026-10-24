"""Small client for OctoBot's web interface JSON API.

Uses only the standard library (urllib) in worker threads, so it has no extra
dependency. Endpoints used (OctoBot web interface, login disabled):
  GET  /api/ping, /api/version
  GET  /api/orders, /api/trades, /api/positions, /api/pnl_history
  POST /api/orders?action=cancel_orders   (body: list of order ids)
  GET  /commands/stop
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import urllib.error
import urllib.request
from typing import Any

from .config import settings


class OctoBotError(Exception):
    pass


def _request(method: str, path: str, body: Any = None, timeout: float = 6.0) -> Any:
    url = settings.octobot_url + path
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    # OctoBot runs next to us; never send this traffic through a proxy
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            ctype = resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as err:
        raise OctoBotError(f"HTTP {err.code} on {path}") from err
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
        raise OctoBotError(f"unreachable ({getattr(err, 'reason', err)})") from err
    if "login" in raw[:400].lower() and "<html" in raw[:200].lower():
        raise OctoBotError("OctoBot asks for a password: disable web password or keep it local only")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        if "json" in ctype:
            raise OctoBotError(f"invalid JSON on {path}")
        return raw


async def call(method: str, path: str, body: Any = None) -> Any:
    return await asyncio.to_thread(_request, method, path, body)


async def ping() -> str:
    res = await call("GET", "/api/ping")
    return res if isinstance(res, str) else str(res)


async def version() -> str:
    res = await call("GET", "/api/version")
    return res if isinstance(res, str) else str(res)


async def orders() -> list[dict]:
    res = await call("GET", "/api/orders")
    return res if isinstance(res, list) else []


async def trades() -> list[dict]:
    res = await call("GET", "/api/trades")
    return res if isinstance(res, list) else []


async def positions() -> list[dict]:
    res = await call("GET", "/api/positions")
    return res if isinstance(res, list) else []


async def pnl_history() -> list[dict]:
    res = await call("GET", "/api/pnl_history")
    return res if isinstance(res, list) else []


async def cancel_orders(order_ids: list[str]) -> str:
    res = await call("POST", "/api/orders?action=cancel_orders", order_ids)
    return str(res)


async def stop_bot() -> str:
    res = await call("GET", "/commands/stop")
    return str(res)


# ---------------------------------------------------------------- profile ---

def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}


def read_active_profile() -> dict:
    """Read the selected OctoBot profile from the mounted user folder.

    Returns an empty dict when the folder is not mounted or not initialised yet.
    """
    base = settings.octobot_user_dir
    if not os.path.isdir(base):
        return {}
    cfg = _load_json(os.path.join(base, "config.json"))
    wanted = cfg.get("profile")
    candidates = glob.glob(os.path.join(base, "profiles", "*", "profile.json"))
    chosen_path, chosen = None, None
    for path in candidates:
        data = _load_json(path)
        pid = data.get("profile", {}).get("id")
        if wanted and pid == wanted:
            chosen_path, chosen = path, data
            break
    if chosen is None:
        if wanted:
            return {"id": wanted, "name": wanted, "builtin": True}
        return {}

    pconf = chosen.get("config", {})
    meta = chosen.get("profile", {})
    exchanges = pconf.get("exchanges", {})
    enabled_ex = [name for name, ex in exchanges.items() if ex.get("enabled")]
    ex_type = "spot"
    for name in enabled_ex:
        ex_type = exchanges[name].get("exchange-type", "spot") or "spot"
        break
    pairs: list[str] = []
    for crypto in pconf.get("crypto-currencies", {}).values():
        if crypto.get("enabled", True):
            pairs.extend(crypto.get("pairs", []))
    trader_real = bool(pconf.get("trader", {}).get("enabled"))
    simulator = bool(pconf.get("trader-simulator", {}).get("enabled"))
    tentacles = _load_json(os.path.join(os.path.dirname(chosen_path), "tentacles_config.json"))
    modes = [k for k, v in tentacles.get("tentacle_activation", {}).get("Trading", {}).items() if v]
    return {
        "id": meta.get("id", wanted),
        "name": meta.get("name", wanted),
        "exchanges": enabled_ex,
        "exchangeType": "future" if ex_type == "future" else "spot",
        "pairs": pairs,
        "real": trader_real,
        "simulated": simulator and not trader_real,
        "startingPortfolio": pconf.get("trader-simulator", {}).get("starting-portfolio", {}),
        "referenceMarket": pconf.get("trading", {}).get("reference-market", "USDT"),
        "tradingMode": modes[0] if modes else None,
    }
