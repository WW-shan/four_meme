"""Telegram signal delivery.

The scanner decides; this module only formats and pushes. It never touches a
private key, an RPC endpoint or the trading switch, so enabling it cannot open a
position on its own.

Two properties matter more than throughput:

* the bot token must never reach a log line, including inside an exception
  raised by the HTTP client (requests puts the full URL, token included, into
  its messages), so every outbound string goes through ``_redact``;
* a delivery failure must never propagate into the decision path, so ``send``
  swallows and logs instead of raising.
"""

from __future__ import annotations

import html
import logging
import math
import time
from typing import Any, Callable, Mapping

from config.notify_config import NotifyConfig

logger = logging.getLogger(__name__)

# Telegram rejects text over 4096 characters; keep a margin for the HTML tags.
MAX_MESSAGE_CHARS = 3800
# A 429 tells us when to retry. Waiting inline would stall the event loop, so we
# drop signals until that moment instead.
MAX_BACKOFF_SECONDS = 300.0

ACTION_LABEL = {
    "buy": "🟢 BUY",
    "watch": "🟡 WATCH",
    "reject": "⚪ REJECT",
}


def shorten_address(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= 14:
        return text
    return f"{text[:6]}…{text[-6:]}"


def escape(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=False)


def _field(source: Any, key: str, default: Any = None) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def _number(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _money(value: Any) -> str:
    parsed = _number(value)
    if parsed is None:
        return "unknown"
    if abs(parsed) >= 1_000_000:
        return f"${parsed / 1_000_000:.2f}M"
    if abs(parsed) >= 1_000:
        return f"${parsed / 1_000:.1f}k"
    return f"${parsed:.2f}"


def format_signal(
    decision: Any,
    *,
    chain: str = "bsc",
    symbol: str | None = None,
    name: str | None = None,
    snapshot: Mapping[str, Any] | None = None,
    now: float | None = None,
) -> str:
    """Render one decision as an HTML Telegram message.

    Missing numbers stay "unknown" instead of turning into 0, so a provider
    outage is visible in the channel rather than looking like a dead token.
    """
    token = str(_field(decision, "token", "") or "")
    action = str(_field(decision, "action", "") or "").lower()
    mode = str(_field(decision, "mode", "") or "shadow")
    reasons = _field(decision, "reason_codes", ()) or ()
    size_quote = _field(decision, "size_quote", None)
    safety = _field(decision, "safety_verdict", None)
    funding = _field(decision, "funding_confirmed", None)
    expires_at = _number(_field(decision, "expires_at", None))
    created_at = _number(_field(decision, "created_at", None))
    stamp = now if now is not None else time.time()

    snapshot = snapshot or {}
    lines = [
        f"<b>{ACTION_LABEL.get(action, action.upper() or 'SIGNAL')}</b> · {escape(chain)}",
        f"CA: <code>{escape(token)}</code>",
    ]
    title = " / ".join(part for part in (escape(name or ""), escape(symbol or "")) if part)
    if title:
        lines.append(f"名称: {title}")
    lines.append(
        "市值: {mcap} | 流动性: {liq}".format(
            mcap=_money(snapshot.get("mcap_usd")),
            liq=_money(snapshot.get("liquidity_usd")),
        )
    )
    if safety is not None:
        lines.append(f"安全检查: {escape(safety)} | 资金确认: {'yes' if funding else 'no'}")
    if size_quote is not None:
        lines.append(f"建议仓位: {escape(size_quote)} ({escape(mode)})")
    if reasons:
        lines.append("理由: " + ", ".join(escape(reason) for reason in reasons))
    if expires_at is not None:
        remaining = max(0.0, expires_at - stamp)
        lines.append(f"有效期: {remaining:.0f}s")
    if created_at is not None:
        lines.append(f"信号时间: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(created_at))}")
    lines.append("")
    lines.append("<i>只读信号 · 不含自动交易 · 自行判断</i>")
    return "\n".join(lines)[:MAX_MESSAGE_CHARS]


class TelegramSignalBot:
    """Push decisions to one Telegram chat. Never raises into the caller."""

    def __init__(
        self,
        token: str | None = None,
        chat_id: str | None = None,
        *,
        session: Any | None = None,
        api_base: str = "https://api.telegram.org",
        enabled: bool = False,
        actions: tuple[str, ...] = ("buy",),
        dedupe_seconds: float = 900.0,
        min_interval_seconds: float = 3.0,
        timeout_seconds: float = 10.0,
        clock: Callable[[], float] = time.time,
    ):
        self.token = (token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.session = session
        self.api_base = (api_base or "https://api.telegram.org").rstrip("/")
        self.enabled = bool(enabled)
        self.actions = tuple(action.lower() for action in actions)
        self.dedupe_seconds = max(0.0, float(dedupe_seconds))
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.clock = clock
        self.sent_count = 0
        self.skipped_count = 0
        self.failed_count = 0
        self._last_sent_at: float | None = None
        self._blocked_until = 0.0
        self._last_signal_at: dict[tuple[str, str, str], float] = {}
        self._warned_missing_credentials = False

    @classmethod
    def from_config(
        cls,
        config: type[NotifyConfig] | None = None,
        *,
        session: Any | None = None,
        enabled: bool | None = None,
        clock: Callable[[], float] = time.time,
    ) -> "TelegramSignalBot":
        config = config or NotifyConfig
        return cls(
            token=config.TELEGRAM_BOT_TOKEN,
            chat_id=config.TELEGRAM_CHAT_ID,
            session=session,
            api_base=config.TELEGRAM_API_BASE,
            enabled=config.TELEGRAM_SIGNAL_ENABLED if enabled is None else enabled,
            actions=config.TELEGRAM_SIGNAL_ACTIONS,
            dedupe_seconds=config.TELEGRAM_SIGNAL_DEDUPE_SECONDS,
            min_interval_seconds=config.TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS,
            timeout_seconds=config.TELEGRAM_REQUEST_TIMEOUT_SECONDS,
            clock=clock,
        )

    def _redact(self, text: Any) -> str:
        """Strip the bot token and chat id from anything we log."""
        out = str(text)
        if self.token:
            out = out.replace(self.token, "<bot-token>")
        if self.chat_id:
            out = out.replace(self.chat_id, "<chat-id>")
        return out

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.token and self.chat_id and self.session is not None)

    def not_ready_reason(self) -> str:
        if not self.enabled:
            return "TELEGRAM_SIGNAL_ENABLED is false"
        missing = [name for name, value in (("TELEGRAM_BOT_TOKEN", self.token),
                                           ("TELEGRAM_CHAT_ID", self.chat_id)) if not value]
        if missing:
            return "missing " + ", ".join(missing)
        if self.session is None:
            return "no HTTP session configured"
        return "not ready"

    def send(self, text: str) -> bool:
        """Send raw text. Returns True only when Telegram accepted the message."""
        now = self.clock()
        if not self.ready:
            # Enabled but misconfigured is an operator error worth one warning; a disabled
            # channel is the normal state and must not spam the log.
            if self.enabled:
                if not self._warned_missing_credentials:
                    logger.warning("Telegram signals are not being sent: %s", self.not_ready_reason())
                    self._warned_missing_credentials = True
            else:
                logger.debug("Telegram signals are disabled; dropping message")
            self.skipped_count += 1
            return False
        if now < self._blocked_until:
            self.skipped_count += 1
            return False
        if self._last_sent_at is not None and now - self._last_sent_at < self.min_interval_seconds:
            logger.debug("Telegram send skipped by min interval (%.1fs)", self.min_interval_seconds)
            self.skipped_count += 1
            return False

        url = f"{self.api_base}/bot{self.token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": str(text)[:MAX_MESSAGE_CHARS],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = self.session.post(url, json=payload, timeout=self.timeout_seconds)
        except Exception as exc:
            self.failed_count += 1
            logger.warning("Telegram send failed: %s", self._redact(exc))
            return False

        status = getattr(response, "status_code", None)
        if status == 200:
            self._last_sent_at = now
            self.sent_count += 1
            return True
        if status == 429:
            retry_after = self._retry_after(response)
            self._blocked_until = now + min(max(retry_after, 1.0), MAX_BACKOFF_SECONDS)
            self.failed_count += 1
            logger.warning("Telegram rate limited; pausing sends for %.0fs", self._blocked_until - now)
            return False
        self.failed_count += 1
        logger.warning(
            "Telegram send rejected (status=%s): %s", status, self._redact(self._body_snippet(response))
        )
        return False

    @staticmethod
    def _retry_after(response: Any) -> float:
        try:
            payload = response.json()
        except Exception:
            return 1.0
        parameters = payload.get("parameters") if isinstance(payload, Mapping) else None
        value = (parameters or {}).get("retry_after") if isinstance(parameters, Mapping) else None
        parsed = _number(value)
        return parsed if parsed is not None else 1.0

    @staticmethod
    def _body_snippet(response: Any) -> str:
        body = getattr(response, "text", None)
        if body is None:
            try:
                body = response.json()
            except Exception:
                body = "<unreadable body>"
        return str(body)[:200]

    def notify_decision(
        self,
        decision: Any,
        *,
        chain: str = "bsc",
        token: str | None = None,
        symbol: str | None = None,
        name: str | None = None,
        snapshot: Mapping[str, Any] | None = None,
    ) -> bool:
        """Push one decision if its action is subscribed and it is not a duplicate."""
        action = str(_field(decision, "action", "") or "").lower()
        if action not in self.actions:
            return False
        address = str(token or _field(decision, "token", "") or "").lower()
        now = self.clock()
        key = (str(chain), address, action)
        previous = self._last_signal_at.get(key)
        if previous is not None and now - previous < self.dedupe_seconds:
            logger.debug("Duplicate %s signal for %s suppressed", action, shorten_address(address))
            self.skipped_count += 1
            return False
        text = format_signal(decision, chain=chain, symbol=symbol, name=name, snapshot=snapshot, now=now)
        if not self.send(text):
            return False
        self._last_signal_at[key] = now
        return True

    def notify_text(self, text: str) -> bool:
        """Send an operator message (channel test, outage notice)."""
        return self.send(text)
