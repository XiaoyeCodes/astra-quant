"""Plain-language account of what the bot is doing and why: the simple home page, the
"why did it stop / switch" card and the daily report all read from here."""
from __future__ import annotations

import time
from datetime import datetime

from .autopilot import IDLE_PROFILE, RISK_TZ, RULE_LABELS, SCHEMES, trading_day

RECENT_SWITCH_MS = 24 * 3600 * 1000
ANCHOR_WORD = {"base": "方案启动时", "trail": "运行期间最高", "daily": "今日起点", "manual": "方案启动时"}
BY_WORD = {"auto": "自动切换", "scheme": "启动方案", "stop": "止损", "user": "手动切换"}


def when(ms: int | None) -> str:
    return datetime.fromtimestamp(ms / 1000, RISK_TZ).strftime("%m-%d %H:%M") if ms else "—"


def _pct(a: float | None, b: float | None) -> float | None:
    return (a / b - 1) * 100 if a and b else None


def numbers(c) -> dict:
    """Equity now, change today (Beijing day) and since the scheme started."""
    cfg = c.desk.cfg
    eq = c.oct_equity
    day = cfg.get("day") if (cfg.get("day") or {}).get("date") == trading_day() else None
    base = cfg.get("baseline")
    day_eq, base_eq = (day or {}).get("equity"), (base or {}).get("equity")
    return {
        "equity": eq,
        "currency": c.profile.get("referenceMarket", "USDT"),
        "today": eq - day_eq if eq and day_eq else None,
        "todayPct": _pct(eq, day_eq),
        "sinceStart": eq - base_eq if eq and base_eq else None,
        "sinceStartPct": _pct(eq, base_eq),
        "startedAt": (base or {}).get("ts"),
    }


def _stopped_story(st: dict, quote: str) -> dict:
    rule = st.get("rule") or "base"
    label = st.get("ruleLabel") or RULE_LABELS.get(rule, "止损")
    if st.get("by") == "octobot":
        return {"tone": "warn", "kind": "stopped", "title": "OctoBot 的备用止损停止了交易",
                "lines": [f"什么时候：{when(st.get('ts'))}",
                          "为什么：桥接服务当时没有运行，OctoBot 自带的备用止损替它执行了撤单和卖出",
                          f"现在：{st.get('problem') or '已切换到观望'}"],
                "next": "到 OctoBot 的 Portfolio 页面确认只剩 USDT，然后在设置里重新启动一个方案"}
    if rule == "manual":
        why = "你点了「停止并卖出全部」"
    else:
        loss = st.get("loss")
        why = (f"资金从{ANCHOR_WORD.get(rule, '起点')}的水平跌到 {st.get('equity') or 0:.2f}"
               f"（−{loss:.2f}%），超过了你设的{label} {st.get('pct', 0):g}%" if loss is not None
               else f"触发了{label} {st.get('pct', 0):g}%")
    if st.get("verified") is False:
        return {"tone": "bad", "kind": "stopped", "title": f"{label}没有完全执行",
                "lines": [f"什么时候：{when(st.get('ts'))}", f"为什么：{why}",
                          f"问题：{st.get('problem') or '未知'}"],
                "next": "立即到 OctoBot 检查挂单和持仓，手动撤单并卖出"}
    final = st.get("finalEquity")
    now = (f"挂单已撤销，持币已卖出，只剩 {quote}（{final:.2f}），已切换到观望，不会再下单" if final
           else "已切换到观望，不会再下单")
    title = "已手动停止交易" if rule == "manual" else f"已因「{label}」停止交易"
    return {"tone": "warn", "kind": "stopped", "title": title,
            "lines": [f"什么时候：{when(st.get('ts'))}", f"为什么：{why}", f"现在：{now}"],
            "next": "确认原因后，在设置里重新启动一个方案；想换个止损比例也在那里改"}


def _switch_line(info: dict | None) -> str | None:
    if not info or time.time() * 1000 - info.get("ts", 0) > RECENT_SWITCH_MS:
        return None
    by = BY_WORD.get(info.get("by"), "切换")
    text = f"{when(info['ts'])} {by}：{info.get('fromLabel') or '—'} → {info.get('label')}"
    if info.get("why"):
        text += f"。原因：{info['why']}"
    if info.get("handover"):
        text += f"。{info['handover']}"
    return text


def story(c) -> dict:
    """{tone: ok|info|warn|bad, kind, title, lines[], next} — what is happening, why, what to do."""
    desk, cfg = c.desk, c.desk.cfg
    mode = c._mode()
    quote = c.profile.get("referenceMarket", "USDT")
    sw_line = _switch_line(cfg.get("lastSwitchInfo"))

    if mode == "live":
        return {"tone": "bad", "kind": "live", "title": "OctoBot 正在用实盘交易",
                "lines": ["本项目的止损和自动策略只在模拟盘验证过"],
                "next": "如非刻意，请在 OctoBot 里切回模拟盘"}
    if desk._stopping:
        return {"tone": "bad", "kind": "stopping", "title": "正在停止交易",
                "lines": ["正在撤单、卖出全部持币，核实只剩 USDT 后切换到观望"], "next": "大约 1 分钟，请稍候"}
    if desk.switching:
        sw = desk.switching
        lines = ["OctoBot 正在重启，需要 1–3 分钟，期间不会交易"]
        if sw_line:
            lines.append(sw_line)
        return {"tone": "info", "kind": "switching", "title": f"正在切换到「{sw.get('label')}」",
                "lines": lines, "next": "不用操作，完成后这里会自动更新"}
    if not c.link_octobot.get("ok"):
        return {"tone": "bad", "kind": "offline", "title": "连不上 OctoBot",
                "lines": [f"原因：{c.link_octobot.get('error') or '无响应'}", "期间不会交易，止损也无法执行"],
                "next": "打开设置 → 系统体检查看处理办法"}
    if cfg.get("stopped"):
        return _stopped_story(cfg["stopped"], quote)
    if c.profile.get("id") == IDLE_PROFILE:
        lines = ["没有挂单，也不会下单"]
        if sw_line:
            lines.append(sw_line)
        return {"tone": "info", "kind": "idle", "title": "观望中，没有交易", "lines": lines,
                "next": "想开始交易：在设置 → 交易方案里选一个方案并设好止损"}

    label = c.profile.get("label") or c.profile.get("id") or "—"
    lines = [f"方案：{SCHEMES.get(cfg['scheme'], cfg['scheme'])}"]
    rule, line = desk.binding_rule()
    eq = c.oct_equity_live or c.oct_equity
    tone = "ok"
    if line and eq:
        room = (eq - line) / eq * 100
        lines.append(f"止损：{RULE_LABELS[rule]}线 {line:.2f}，还差 {room:.2f}%")
        if room < 2:
            tone = "warn"
    else:
        lines.append("止损：没有设置，亏损不会自动停止")
        tone = "warn"
    if sw_line:
        lines.append(sw_line)
    return {"tone": tone, "kind": "running", "title": f"正在用「{label}」交易", "lines": lines,
            "next": "在设置里给方案设一条止损线" if not line else ""}
