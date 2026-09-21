"""Telegram signal delivery contract.

This layer only pushes signals to a chat. It never signs, sends or authorises a
trade: the trading switch stays ``ENABLE_TRADING`` in ``config/trading_config.py``.
"""

from __future__ import annotations

import os

# "candidate" is an on-chain scan result (see format_candidate), not a trade authorisation.
SIGNAL_ACTIONS = ("buy", "watch", "reject", "candidate")


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _actions_env(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    actions = tuple(part.strip().lower() for part in raw.split(",") if part.strip())
    unknown = sorted(set(actions) - set(SIGNAL_ACTIONS))
    if unknown:
        raise ValueError(f"{name} has unsupported actions: {', '.join(unknown)}")
    return actions or default


class NotifyConfig:
    # Signals are off until an operator turns them on and supplies credentials.
    TELEGRAM_SIGNAL_ENABLED = _bool_env("TELEGRAM_SIGNAL_ENABLED", False)
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    TELEGRAM_API_BASE = os.getenv("TELEGRAM_API_BASE", "").strip() or "https://api.telegram.org"
    # Only these decision actions are pushed. "buy" is the default so the channel stays quiet.
    TELEGRAM_SIGNAL_ACTIONS = _actions_env("TELEGRAM_SIGNAL_ACTIONS", ("buy",))
    TELEGRAM_SIGNAL_DEDUPE_SECONDS = _float_env("TELEGRAM_SIGNAL_DEDUPE_SECONDS", 900.0)
    TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS = _float_env("TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS", 3.0)
    TELEGRAM_REQUEST_TIMEOUT_SECONDS = _float_env("TELEGRAM_REQUEST_TIMEOUT_SECONDS", 10.0)

    @classmethod
    def validate(cls) -> None:
        if cls.TELEGRAM_SIGNAL_DEDUPE_SECONDS < 0:
            raise ValueError("TELEGRAM_SIGNAL_DEDUPE_SECONDS must be non-negative")
        if cls.TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS < 0:
            raise ValueError("TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS must be non-negative")
        if cls.TELEGRAM_REQUEST_TIMEOUT_SECONDS <= 0:
            raise ValueError("TELEGRAM_REQUEST_TIMEOUT_SECONDS must be positive")
        if not cls.TELEGRAM_API_BASE.startswith(("http://", "https://")):
            raise ValueError("TELEGRAM_API_BASE must be an http(s) URL")
        if cls.TELEGRAM_BOT_TOKEN and ":" not in cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN does not look like a Telegram bot token")
        if cls.TELEGRAM_SIGNAL_ENABLED and not cls.TELEGRAM_BOT_TOKEN:
            raise ValueError("TELEGRAM_SIGNAL_ENABLED requires TELEGRAM_BOT_TOKEN")
        if cls.TELEGRAM_SIGNAL_ENABLED and not cls.TELEGRAM_CHAT_ID:
            raise ValueError("TELEGRAM_SIGNAL_ENABLED requires TELEGRAM_CHAT_ID")
