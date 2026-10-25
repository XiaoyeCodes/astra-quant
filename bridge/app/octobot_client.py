"""Small client for OctoBot's web interface JSON API.

Uses only the standard library (urllib) in worker threads, so it has no extra
dependency. Endpoints used (OctoBot web interface, login disabled):
  GET  /api/ping, /api/version
  GET  /api/orders, /api/trades, /api/positions, /api/pnl_history
  GET  /api/historical_portfolio_value
  POST /api/orders?action=cancel_orders   (body: list of order ids)
  GET  /commands/stop, /commands/restart
  GET  /profile?select=<id>                (strategy switch, paper profiles only)
  POST /automations?action=save|start      (stop-loss: cancel, sell all, pause)
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import strategies
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


async def portfolio_value(currency: str) -> float | None:
    """Current portfolio value as computed by OctoBot, in the reference market.

    OctoBot keeps the simulated portfolio across restarts and profile switches, so
    this is the only reliable source for paper trading equity.
    """
    since = int(time.time()) - 900
    res = await call("GET", f"/api/historical_portfolio_value?currency={currency}&time_frame=1m&from_timestamp={since}")
    if not isinstance(res, list) or not res:
        return None
    try:
        value = float(res[-1]["value"])
    except (KeyError, TypeError, ValueError):
        return None
    return value if value > 0 else None  # OctoBot reports 0 while it starts up


async def cancel_orders(order_ids: list[str]) -> str:
    res = await call("POST", "/api/orders?action=cancel_orders", order_ids)
    return str(res)


async def stop_bot() -> str:
    res = await call("GET", "/commands/stop")
    return str(res)


async def select_profile(profile_id: str) -> None:
    """Same call as picking a profile in OctoBot's web page; it is saved to config.json.

    OctoBot only knows the profiles it loaded at startup (HTTP 500 otherwise).
    The redirect goes to a JSON endpoint so the reply is easy to check.
    """
    await call("GET", f"/profile?select={urllib.parse.quote(profile_id)}&next=/api/version")


async def save_automations(config: dict) -> None:
    """Replace the Automation config of the active profile (stop-loss)."""
    await call("POST", "/automations?action=save", config)


async def start_automations() -> None:
    """(Re)start automations with the saved config, without restarting OctoBot."""
    await call("POST", "/automations?action=start", {})


async def restart_bot() -> str:
    res = await call("GET", "/commands/restart")
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
    # OctoBot rewrites this file with every exchange tentacle activated too, so keep trading modes only
    modes = [k for k, v in tentacles.get("tentacle_activation", {}).get("Trading", {}).items()
             if v and k.endswith("TradingMode")]
    strategy = strategies.META.get(meta.get("id", wanted), {})
    return {
        "id": meta.get("id", wanted),
        "name": meta.get("name", wanted),
        "label": strategy.get("label"),
        "family": strategy.get("family"),
        "exchanges": enabled_ex,
        "exchangeType": "future" if ex_type == "future" else "spot",
        "pairs": pairs,
        "real": trader_real,
        "simulated": simulator and not trader_real,
        "startingPortfolio": pconf.get("trader-simulator", {}).get("starting-portfolio", {}),
        "referenceMarket": pconf.get("trading", {}).get("reference-market", "USDT"),
        "tradingMode": modes[0] if modes else None,
    }
