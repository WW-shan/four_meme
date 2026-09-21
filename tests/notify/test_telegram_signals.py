import unittest
from pathlib import Path

from src.notify.telegram import TelegramSignalBot, format_signal, shorten_address

TOKEN = "0x" + "ab" * 20
BOT_TOKEN = "123456:FAKE-TOKEN-FOR-TESTS"
CHAT_ID = "-1001234567890"


class FakeResponse:
    def __init__(self, payload=None, status=200, text=None):
        self._payload = payload if payload is not None else {"ok": True}
        self.status_code = status
        self.text = text if text is not None else str(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls = []
        self.raise_on_post = None

    def post(self, url, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "timeout": timeout})
        if self.raise_on_post is not None:
            raise self.raise_on_post
        if self.responses:
            return self.responses.pop(0)
        return FakeResponse()


def decision(action="buy", **overrides):
    payload = {
        "token": TOKEN,
        "action": action,
        "mode": "shadow",
        "size_quote": 0.01,
        "reason_codes": ("funding_confirmed",),
        "safety_verdict": "pass",
        "funding_confirmed": True,
        "expires_at": 1000.0,
        "created_at": 900.0,
    }
    payload.update(overrides)
    return payload


def bot(session, *, enabled=True, **kwargs):
    clock = kwargs.pop("clock", lambda: 100.0)
    return TelegramSignalBot(
        token=BOT_TOKEN, chat_id=CHAT_ID, session=session, enabled=enabled,
        api_base="https://api.telegram.org", clock=clock, **kwargs,
    )


class FormatTests(unittest.TestCase):
    def test_signal_renders_token_chain_and_numbers(self):
        text = format_signal(decision(), chain="base", symbol="FOO",
                             snapshot={"mcap_usd": 45_000, "liquidity_usd": 12_000}, now=950.0)
        self.assertIn("🟢 BUY", text)
        self.assertIn("base", text)
        self.assertIn(TOKEN, text)
        self.assertIn("$45.0k", text)
        self.assertIn("$12.0k", text)
        self.assertIn("有效期: 50s", text)
        self.assertIn("不含自动交易", text)

    def test_missing_provider_numbers_are_not_rendered_as_zero(self):
        text = format_signal(decision(), chain="bsc", now=950.0)
        self.assertIn("市值: unknown", text)
        self.assertNotIn("$0.00", text)

    def test_html_metacharacters_in_reasons_are_escaped(self):
        text = format_signal(decision(reason_codes=("<b>boom</b>",)), chain="bsc", now=950.0)
        self.assertIn("&lt;b&gt;boom&lt;/b&gt;", text)
        self.assertNotIn("<b>boom</b>", text)

    def test_shorten_address_keeps_both_ends(self):
        self.assertEqual("0xabab…ababab", shorten_address(TOKEN))
        self.assertEqual("short", shorten_address("short"))


class DeliveryTests(unittest.TestCase):
    def test_disabled_bot_never_calls_http(self):
        session = FakeSession()
        self.assertFalse(bot(session, enabled=False).notify_decision(decision(), chain="bsc", token=TOKEN))
        self.assertEqual([], session.calls)

    def test_enabled_bot_posts_html_to_send_message(self):
        session = FakeSession()
        self.assertTrue(bot(session).notify_decision(decision(), chain="bsc", token=TOKEN, symbol="FOO"))
        self.assertEqual(1, len(session.calls))
        call = session.calls[0]
        self.assertTrue(call["url"].endswith(f"/bot{BOT_TOKEN}/sendMessage"))
        self.assertEqual(CHAT_ID, call["json"]["chat_id"])
        self.assertEqual("HTML", call["json"]["parse_mode"])
        self.assertIn("🟢 BUY", call["json"]["text"])
        self.assertEqual(10.0, call["timeout"])

    def test_missing_credentials_skip_without_http(self):
        session = FakeSession()
        silent = TelegramSignalBot(token="", chat_id="", session=session, enabled=True)
        with self.assertLogs("src.notify.telegram", level="WARNING") as captured:
            self.assertFalse(silent.notify_decision(decision(), chain="bsc", token=TOKEN))
        self.assertEqual([], session.calls)
        self.assertIn("TELEGRAM_BOT_TOKEN", silent.not_ready_reason())
        self.assertIn("not being sent", "\n".join(captured.output))
        # The warning is emitted once, not per candidate.
        self.assertFalse(silent.notify_decision(decision(), chain="bsc", token=TOKEN))

    def test_reject_actions_are_not_pushed_by_default(self):
        session = FakeSession()
        self.assertFalse(bot(session).notify_decision(decision("reject"), chain="bsc", token=TOKEN))
        self.assertEqual([], session.calls)

    def test_watch_actions_can_be_subscribed(self):
        session = FakeSession()
        watcher = bot(session, actions=("buy", "watch"))
        self.assertTrue(watcher.notify_decision(decision("watch"), chain="bsc", token=TOKEN))
        self.assertEqual(1, len(session.calls))

    def test_duplicate_signal_is_suppressed_within_the_window(self):
        now = [100.0]
        session = FakeSession()
        sender = bot(session, dedupe_seconds=900.0, clock=lambda: now[0])
        self.assertTrue(sender.notify_decision(decision(), chain="bsc", token=TOKEN))
        now[0] = 200.0
        self.assertFalse(sender.notify_decision(decision(), chain="bsc", token=TOKEN))
        self.assertEqual(1, len(session.calls))
        now[0] = 1_100.0
        self.assertTrue(sender.notify_decision(decision(), chain="bsc", token=TOKEN))
        self.assertEqual(2, len(session.calls))

    def test_same_token_on_another_chain_is_not_a_duplicate(self):
        session = FakeSession()
        sender = bot(session, min_interval_seconds=0.0)
        self.assertTrue(sender.notify_decision(decision(), chain="bsc", token=TOKEN))
        self.assertTrue(sender.notify_decision(decision(), chain="base", token=TOKEN))
        self.assertEqual(2, len(session.calls))

    def test_min_interval_drops_a_burst(self):
        now = [100.0]
        session = FakeSession()
        sender = bot(session, min_interval_seconds=3.0, dedupe_seconds=0.0, clock=lambda: now[0])
        self.assertTrue(sender.send("first"))
        self.assertFalse(sender.send("second"))
        self.assertEqual(1, len(session.calls))
        now[0] = 104.0
        self.assertTrue(sender.send("third"))
        self.assertEqual(2, len(session.calls))


class FailureTests(unittest.TestCase):
    def test_http_exception_is_swallowed_and_logged(self):
        session = FakeSession()
        session.raise_on_post = RuntimeError(f"connection to https://api.telegram.org/bot{BOT_TOKEN} failed")
        sender = bot(session)
        with self.assertLogs("src.notify.telegram", level="WARNING") as captured:
            self.assertFalse(sender.send("hello"))
        joined = "\n".join(captured.output)
        self.assertIn("Telegram send failed", joined)
        self.assertNotIn(BOT_TOKEN, joined)
        self.assertNotIn(CHAT_ID, joined)

    def test_rate_limit_pauses_instead_of_raising(self):
        now = [100.0]
        session = FakeSession([FakeResponse({"parameters": {"retry_after": 30}}, status=429)])
        sender = bot(session, min_interval_seconds=0.0, clock=lambda: now[0])
        with self.assertLogs("src.notify.telegram", level="WARNING") as captured:
            self.assertFalse(sender.send("hello"))
        self.assertIn("rate limited", "\n".join(captured.output))
        now[0] = 110.0
        self.assertFalse(sender.send("again"))
        self.assertEqual(1, len(session.calls))
        now[0] = 140.0
        self.assertTrue(sender.send("later"))
        self.assertEqual(2, len(session.calls))

    def test_non_200_status_is_logged_without_the_token(self):
        session = FakeSession([FakeResponse({"ok": False, "description": "chat not found"},
                                            status=400, text="chat not found")])
        sender = bot(session)
        with self.assertLogs("src.notify.telegram", level="WARNING") as captured:
            self.assertFalse(sender.send("hello"))
        joined = "\n".join(captured.output)
        self.assertIn("status=400", joined)
        self.assertNotIn(BOT_TOKEN, joined)

    def test_notify_decision_survives_a_broken_session(self):
        session = FakeSession()
        session.raise_on_post = TimeoutError("timed out")
        sender = bot(session)
        with self.assertLogs("src.notify.telegram", level="WARNING"):
            self.assertFalse(sender.notify_decision(decision(), chain="bsc", token=TOKEN))
        self.assertEqual(1, sender.failed_count)


class EnvContractTests(unittest.TestCase):
    def test_env_template_documents_the_signal_keys(self):
        content = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
        for key in ("TELEGRAM_SIGNAL_ENABLED=false", "TELEGRAM_BOT_TOKEN=", "TELEGRAM_CHAT_ID=",
                    "TELEGRAM_API_BASE=https://api.telegram.org", "TELEGRAM_SIGNAL_ACTIONS=buy",
                    "TELEGRAM_SIGNAL_DEDUPE_SECONDS=900", "TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS=3",
                    "TELEGRAM_REQUEST_TIMEOUT_SECONDS=10"):
            self.assertIn(key, content)
        self.assertIn("cannot place a trade", content)

    def test_config_defaults_are_off_and_validate(self):
        import os
        from unittest.mock import patch

        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import config.notify_config as module

            reloaded = importlib.reload(module)
            self.assertFalse(reloaded.NotifyConfig.TELEGRAM_SIGNAL_ENABLED)
            self.assertEqual(("buy",), reloaded.NotifyConfig.TELEGRAM_SIGNAL_ACTIONS)
            reloaded.NotifyConfig.validate()
            with patch.dict(os.environ, {"TELEGRAM_SIGNAL_ENABLED": "true"}, clear=True):
                enabled = importlib.reload(module)
                with self.assertRaisesRegex(ValueError, "TELEGRAM_BOT_TOKEN"):
                    enabled.NotifyConfig.validate()
            importlib.reload(module)


if __name__ == "__main__":
    unittest.main()
