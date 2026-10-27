"""Push notifications (Telegram, WeCom, Feishu, DingTalk, ServerChan, generic webhook).

Delivery runs in a background task with retries; it never raises into trading logic.
Repeated messages with the same key are suppressed for a while so a flapping link
does not flood the phone.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request

from .config import settings

log = logging.getLogger("astra")
TITLE = "ASTRA 星枢"
WEBHOOK_TYPES = ("generic", "wecom", "feishu", "dingtalk", "serverchan")


def webhook_payload(kind: str, title: str, text: str, level: str) -> dict:
    content = f"【{title}】{text}"
    if kind == "wecom" or kind == "dingtalk":
        return {"msgtype": "text", "text": {"content": content}}
    if kind == "feishu":
        return {"msg_type": "text", "content": {"text": content}}
    if kind == "serverchan":
        return {"title": f"{title}：{text[:30]}", "desp": text}
    return {"title": title, "text": text, "level": level, "source": "astra"}


class Notifier:
    def __init__(self) -> None:
        self.queue: asyncio.Queue | None = None
        self.task: asyncio.Task | None = None
        self.recent: dict[str, float] = {}
        self.last_error: str | None = None
        self.sent = 0

    @property
    def channels(self) -> list[str]:
        out = []
        if settings.notify_telegram_token and settings.notify_telegram_chat:
            out.append("telegram")
        if settings.notify_webhook_url:
            out.append(settings.notify_webhook_type)
        return out

    def start(self) -> None:
        if self.task is None:
            self.queue = asyncio.Queue(maxsize=200)
            self.task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None

    def send(self, text: str, level: str = "info", key: str | None = None, quiet_s: float = 300) -> bool:
        """Queue a notification; False when no channel, suppressed as a repeat, or queue full."""
        if not self.channels or self.queue is None:
            return False
        key = key or text
        now = time.time()
        if now - self.recent.get(key, 0) < quiet_s:
            return False
        self.recent[key] = now
        if len(self.recent) > 500:
            self.recent = {k: v for k, v in self.recent.items() if now - v < 3600}
        try:
            self.queue.put_nowait((text, level))
        except asyncio.QueueFull:
            log.warning("notification queue full, dropping: %s", text[:80])
            return False
        return True

    async def _worker(self) -> None:
        while True:
            text, level = await self.queue.get()
            for channel in self.channels:
                for attempt in range(3):
                    try:
                        await asyncio.to_thread(self._deliver, channel, text, level)
                        self.sent += 1
                        self.last_error = None
                        break
                    except Exception as err:  # network errors, bad tokens: keep trading regardless
                        self.last_error = f"{channel}: {str(err)[:120]}"
                        log.warning("notification via %s failed (%s): %s", channel, attempt + 1, err)
                        await asyncio.sleep(2 * (attempt + 1))

    def _post(self, url: str, payload: dict) -> None:
        req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json"}, method="POST")
        proxy = settings.notify_proxy
        opener = urllib.request.build_opener(urllib.request.ProxyHandler(
            {"http": proxy, "https": proxy} if proxy else {}))
        with opener.open(req, timeout=15) as resp:
            body = resp.read(2000).decode("utf-8", errors="replace")
        # WeCom / Feishu / DingTalk answer HTTP 200 with an error code in the body
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            return
        code = data.get("errcode", data.get("code", 0)) if isinstance(data, dict) else 0
        if code not in (0, None) and not (isinstance(data, dict) and data.get("ok") is True):
            raise RuntimeError(f"webhook answered {str(data)[:120]}")

    def _deliver(self, channel: str, text: str, level: str) -> None:
        if channel == "telegram":
            url = f"https://api.telegram.org/bot{settings.notify_telegram_token}/sendMessage"
            try:
                self._post(url, {"chat_id": settings.notify_telegram_chat, "text": f"【{TITLE}】{text}"})
            except urllib.error.HTTPError as err:
                raise RuntimeError(f"Telegram HTTP {err.code}") from None  # never echo the token in the URL
            return
        self._post(settings.notify_webhook_url, webhook_payload(channel, TITLE, text, level))

    def status(self) -> dict:
        return {"channels": self.channels, "sent": self.sent, "lastError": self.last_error}
