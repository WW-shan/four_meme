# 2026-09-21 Full-Repository Code Audit

Scope: every Python module under `src/`, `config/`, `tools/` and `scripts/` (≈79k lines), plus the
live data directories that feed them. Method: static AST scans, pyflakes, `compileall`, targeted
manual reads of the money path, and the full unittest suite before and after each fix.

Baseline before this round: `main` @ `43d5a9f`, `python3 -m unittest discover` = 1538 tests, 1
skipped, 0 failures. After this round: 1556 tests, 1 skipped, 0 failures.

## What was found and fixed

| # | Severity | Location | Problem | Fix |
|---|---|---|---|---|
| 1 | critical | `src/decision/engine.py` | A `learning`-mode safety report only blocks on `honeypot_sim`, so `decide()` could return `action=buy, size=1.0` off a fail-open verdict | `decide()` now rejects any report whose `mode != "safe"` with reason `safety_mode_not_safe` |
| 2 | critical | `src/core/trader.py` | Sell amount was rounded down to 1e9-wei multiples; a 6-decimal balance below one step silently became 0 and the sell was skipped | `align_sell_amount()` + explicit error log; a below-step balance now refuses loudly instead of dropping the position |
| 3 | high | `src/core/trader.py` | `chainId: 56` hard-coded in five `build_transaction` calls | `TradingConfig.CHAIN_ID` (env `MEME_CHAIN_ID`, validated > 0), documented in `.env.example` |
| 4 | high | `src/safety/fetchers.py` | GoPlus put the chain in the query string while calling the BSC path; DexScreener was pinned to `/bsc/`; an unknown chain silently got BSC data | Chain goes into the GoPlus path, DexScreener uses a chain slug, and every chain-keyed fetcher fails closed (`unsupported_chain:<id>`) |
| 5 | high | `src/radar/pipeline.py` | `ScannerPipeline` defaulted to `chain_id=56`, so a non-BSC chain would query BSC endpoints | `chain_id` resolves from `config/chains.json` by chain name; unknown chains stay `None` and fail closed |
| 6 | high | `src/trader/bot.py` | `_save_state` wrote the position file in place and `_load_state` swallowed parse errors, so a truncated file silently forgot every open position | Atomic temp+`fsync`+`os.replace` write; unparseable state now logs `critical` and refuses to start |
| 7 | medium | `src/core/listener.py` | A bare `except: pass` around address checksumming left a malformed contract address in place | Raises `ValueError` naming the bad address |
| 8 | medium | `src/trader/bot.py` | Three `except Exception: pass` blocks hid buy-confirmation and shutdown failures | Balance polling counts failures and warns; receipt/shutdown failures log at debug |
| 9 | low | `tools/collect_continuous.py` | Bare `except:` around a status-only `block_number` call | Narrowed to `Exception` with a debug log |

## Reviewed and deliberately left alone

- `_sell_on_pancakeswap` falls back to `minOut=1` when `getAmountsOut` fails. This is an emergency
  exit for a pool that will not quote; the alternative (refusing to sell) can trap funds in a rug.
  It is logged as `unquoted_exit=True`. Accepted risk, not changed.
- `src/walletflow/gmgn.py` returns `[]` both for "no API key" and for a failed request. The
  wallet-flow layer then finds no confirmation and does not buy, so the failure direction is safe,
  but the two cases are indistinguishable. Worth splitting when wallet flow goes live.
- 16 remaining broad `except Exception` blocks in `src/` are parse/format/provider fallbacks with
  safe return values (`return None/False/[]/0`), verified individually.
- No mutable default arguments anywhere in `src/`. No `time.sleep` or blocking `requests` call
  inside an `async def`. No hard-coded private keys or RPC credentials. No unparameterised SQL.
- `python3 -m compileall` clean; pyflakes reports only three intentional re-exports
  (`KNOWN_QUOTE_ASSETS`, `TOPIC_CREATE`, `TOPIC_STOP`) kept because tests import them from there.

## Depth limits (stated honestly)

- `src/pipeline/train_hybrid.py` (≈6.4k lines), `src/data/collector.py`, `src/data/dataset_builder.py`
  and the `*_probe.py` research scripts were scanned structurally (silent excepts, network timeouts,
  split/seed handling, file writes) but not line-by-line read. No defect was found in what was
  checked; this is not a claim of full coverage.
- The audit is static plus unit tests. It is not a live-trading test, and no on-chain transaction was
  sent. The bot was not started.
- The running collector (`tmux meme-collector`, PID 15829) was never restarted, so it still runs the
  pre-audit code; the fixes take effect on its next start.

## Data cleanup (same round)

Deleted 64 paths / 726 MiB after verifying each was unreferenced by `docs/`, `src/`, `scripts/`,
`tests/` or `config/`: the stale `logs/bot.log` (196 MB, last write 2026-06-10) and the May training
and replay logs, `.pytest_cache`, `.test_tmp`, root `.DS_Store`, 24 experiment model directories from
the withdrawn 2026-09-10/11/12 rounds, the three derived `data/datasets/*` trees (rebuildable with
`scripts/build_dataset_new.py`), `data/training/rolling_bsc_20260911`,
`data/training/recent_week_20260910_split4`, and the empty/failed `bsc_month_raw_*` runs 2–6.

Kept on purpose: `data/training/` live output and every window referenced by a research document
(2.9 GB), `bsc_month_raw_20260911_run7` as the raw month source (416 MB),
`data/models/20260519_v95_v84_selective_nearmiss_gate` (the model `.env` loads),
`data/research/`, `.env`, and the two live logs (`logs/collector.log`, `data/collection.log`).
Per-path detail: `data-cleanup.json`.
