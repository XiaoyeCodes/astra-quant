"""Small client for OctoBot's web interface JSON API.

Uses only the standard library (urllib) in worker threads, so it has no extra
dependency. Endpoints used (OctoBot web interface, login disabled):
  GET  /api/ping, /api/version
  GET  /api/orders, /api/trades, /api/positions, /api/pnl_history
  GET  /api/historical_portfolio_value, /portfolio (holdings table, HTML)
  POST /api/orders?action=cancel_orders   (body: list of order ids)
  GET  /commands/stop, /commands/restart
  GET  /profile?select=<id>                (strategy switch, paper profiles only)
  POST /automations?action=save|start      (stop-loss: cancel, sell all, pause)
"""
from __future__ import annotations

import asyncio
import glob
import http.cookiejar
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import strategies
from .config import settings


class OctoBotError(Exception):
    pass


# One cookie jar for the whole bridge: holds OctoBot's session when its web password is on.
# OctoBot runs next to us; never send this traffic through a proxy.
_cookies = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPCookieProcessor(_cookies))
_login_lock = threading.Lock()
_last_login = 0.0
_CSRF = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"|value="([^"]+)"[^>]*name="csrf_token"')


def _is_login_page(final_url: str, raw: str) -> bool:
    if urllib.parse.urlsplit(final_url).path.rstrip("/") == "/login":
        return True
    return "login" in raw[:400].lower() and "<html" in raw[:200].lower()


def _login() -> None:
    """Log in with OCTOBOT_PASSWORD (Flask-WTF form: CSRF token + password)."""
    global _last_login
    with _login_lock:
        if time.time() - _last_login < 60:
            return  # OctoBot bans an address after repeated failures: at most one try a minute
        _last_login = time.time()
        try:
            with _opener.open(settings.octobot_url + "/login", timeout=6) as resp:
                page = resp.read().decode("utf-8", errors="replace")
            match = _CSRF.search(page)
            form = {"password": settings.octobot_password, "remember_me": "y"}
            if match:
                form["csrf_token"] = match.group(1) or match.group(2)
            req = urllib.request.Request(settings.octobot_url + "/login", method="POST",
                                         data=urllib.parse.urlencode(form).encode("utf-8"),
                                         headers={"Content-Type": "application/x-www-form-urlencoded"})
            with _opener.open(req, timeout=6) as resp:
                final, raw = resp.geturl(), resp.read(4000).decode("utf-8", errors="replace")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
            raise OctoBotError(f"OctoBot login failed ({getattr(err, 'reason', err)})") from err
        if _is_login_page(final, raw):
            raise OctoBotError("OctoBot rejected OCTOBOT_PASSWORD: check the password in .env")


def _send(method: str, path: str, body: Any, timeout: float) -> tuple[str, str, str]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(settings.octobot_url + path, data=data, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return (resp.read().decode("utf-8", errors="replace"), resp.headers.get("Content-Type", ""),
                    resp.geturl())
    except urllib.error.HTTPError as err:
        raise OctoBotError(f"HTTP {err.code} on {path}") from err
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
        raise OctoBotError(f"unreachable ({getattr(err, 'reason', err)})") from err


def _request(method: str, path: str, body: Any = None, timeout: float = 6.0) -> Any:
    raw, ctype, final = _send(method, path, body, timeout)
    if _is_login_page(final, raw):
        if not settings.octobot_password:
            raise OctoBotError("OctoBot asks for a password: set OCTOBOT_PASSWORD in .env (same as OctoBot's)")
        _login()
        raw, ctype, final = _send(method, path, body, timeout)
        if _is_login_page(final, raw):
            raise OctoBotError("OctoBot still asks for a password after login")
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


_ROW = re.compile(r'<tr class="symbol-holding[^"]*">(.*?)</tr>', re.S)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_SYMBOL = re.compile(r'<span class="symbol">\s*([^<\s]+)\s*</span>')


def parse_holdings(page: str) -> dict[str, dict] | None:
    """Holdings table of OctoBot's /portfolio page: {currency: {"total", "value"}}.

    OctoBot has no JSON endpoint for current holdings. None when the table is absent
    (paused trader, page changed); an empty dict is never returned for a real account.
    """
    if 'id="holdings-table"' not in page:
        return None
    out: dict[str, dict] = {}
    for row in _ROW.findall(page):
        symbol = _SYMBOL.search(row)
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in _CELL.findall(row)]
        if not symbol or len(cells) < 3:
            continue
        try:
            out[symbol.group(1).upper()] = {"total": float(cells[1]), "value": float(cells[2])}
        except ValueError:
            continue
    return out or None


async def holdings() -> dict[str, dict] | None:
    page = await call("GET", "/portfolio")
    return parse_holdings(page) if isinstance(page, str) else None


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
