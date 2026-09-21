# 2026-09-21 Telegram Signal Channel

## Why

The scanner already produces explicit decisions (`buy` / `watch` / `reject`) with reason codes,
safety verdict and expiry, but nothing told a human about them. The request was to get signals into
Telegram first, before any shadow accumulation or live trading. So this round adds the delivery
layer only: it can format and push a signal, and it has no code path that can open a position.

## What was built

| Piece | File | Notes |
|---|---|---|
| Env contract | `config/notify_config.py` | `TELEGRAM_*` keys, `validate()`, defaults off |
| Sender | `src/notify/telegram.py` | `format_signal`, `TelegramSignalBot` |
| Pipeline hook | `src/radar/pipeline.py` | `notifier` field, `notify()`, called from `decide()` |
| Collector wiring | `tools/collect_continuous.py` | builds the notifier when `SCANNER_ENABLED=true` |
| CLI | `scripts/run_scanner.py` | `signal-preview`, `signal-test` |
| Tests | `tests/notify/test_telegram_signals.py`, `tests/scanner/test_pipeline_layers.py` | 21 cases |

## How to turn it on

1. Talk to `@BotFather`, create a bot, copy the token into `TELEGRAM_BOT_TOKEN`.
2. Send the bot one message from the target chat (or add it to the group/channel), then put the chat
   id in `TELEGRAM_CHAT_ID`. Group and channel ids look like `-1001234567890`.
3. Set `TELEGRAM_SIGNAL_ENABLED=true` and `SCANNER_ENABLED=true`.
4. Check the rendering without sending anything:
   `python3 scripts/run_scanner.py signal-preview --token 0x… --symbol FOO --mcap-usd 45000`.
5. Send one real test message: `python3 scripts/run_scanner.py signal-test`.

`TELEGRAM_SIGNAL_ACTIONS=buy` keeps the channel quiet; `buy,watch` also shows candidates that only
passed the safety side.

## Message shape

```
🟢 BUY · bsc
CA: 0xabab…ababab
名称: PEPE / PEPE
市值: $45.0k | 流动性: $12.0k
安全检查: pass | 资金确认: yes
建议仓位: 0.01 (shadow)
理由: funding_confirmed, mcap_in_range
有效期: 45s
信号时间: 2026-09-21 11:35:03

只读信号 · 不含自动交易 · 自行判断
```

Missing provider numbers render as `unknown`, never as `0`, so a provider outage is visible in the
channel instead of looking like a dead token.

## Guardrails

- **Cannot trade.** The package never imports the executor, the trading switch or a private key.
  Every signal carries an explicit "no automatic trading" footer.
- **Token hygiene.** `requests` puts the full URL, bot token included, into its exception text, so
  every outbound string passes `_redact()` before it reaches a log line. A test asserts the token and
  chat id never appear in warnings.
- **Delivery failure is contained.** `send()` swallows and logs; `ScannerPipeline.notify()` wraps the
  sink in `try/except`, so a Telegram outage cannot change or block a decision.
- **Burst control.** `TELEGRAM_SIGNAL_MIN_INTERVAL_SECONDS` spaces sends, and a `429` sets a
  `retry_after` backoff instead of sleeping inside the event loop.
- **Duplicate suppression.** The same `(chain, token, action)` is not repeated inside
  `TELEGRAM_SIGNAL_DEDUPE_SECONDS` (default 15 minutes).
- **Disabled means silent.** With the switch off the notifier logs at debug and drops messages.

## What this is not

It is not evidence of profitability, not a trading authorisation, and not the shadow run. Signals are
the scanner's opinion under the current thresholds, and those thresholds are still hypotheses from
public research.

## Next steps

1. Hook the same notifier into `src/trader/bot.py`'s signal-audit path so live entries push too.
2. Feed the channel from the attention board once `X_BEARER_TOKEN` and `GMGN_API_KEY` exist, so heat
   rankings and on-chain decisions land in the same place.
3. Add an operator command path (pause signals, change the action filter) only after the channel is
   actually in use.
