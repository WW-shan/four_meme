# BSC Profitability Round: Short vs Long Holding

Date: 2026-09-13 (Asia/Shanghai)

## Question

Compare short holding with one-to-three-day holding using fresh BSC data,
fresh models, and an executable capital replay. The experiment keeps the final
time slice sealed and does not change the runtime model or trading settings.

## Market evidence

- The fetched DefiLlama BSC page reports a high-liquidity snapshot: about $5.665B
  TVL, $1.258B 24h DEX volume, $11.068B displayed 7d volume, and 1.94M active
  addresses. See `04-fetch-defillama-bsc.md`.
- The fetched DefiLlama DEX API gives a dated volume series. Daily BSC DEX
  volume peaked at $2.307B on September 8 and $2.283B on September 9, then fell
  to $0.789B on September 12 and $0.709B on September 13. The API snapshot is
  lower than the page snapshot because the endpoints expose different update
  windows; both values are retained rather than merged.
- The fetched Sharpe Four.meme page (snapshot September 11 00:17 UTC) shows
  $1.8B 24h volume, $308.5M liquidity, 81 pairs, and concentration in a few
  narratives. It lists 4Stock pairs at roughly $15.1M and $10.6M 24h volume,
  and also lists 牛来 and 哈基米. See `38-fetch-sharpe-fourmeme-trending.md`.
- Odaily reports 4Stock briefly exceeding $70M market cap on September 8. See
  `07-fetch-4stock-odaily.md`. This is event-driven evidence, not a guarantee
  that a new token can be held profitably for days.

## Data

The rebuilt 31-day window ends at `2026-09-13T13:50:36Z` and contains
`232,549` deduplicated lifecycle rows. The builder sorts nested buys, sells,
and price history by chain timestamp and repairs stale chain `last_update`
values. The integrity report records zero malformed rows, zero future or
pre-creation events, zero duplicate output tokens, and zero ordering violations:
`09-latest-window-integrity.json`.

There are `3,011` default-gate entry candidates. Complete paths decrease from
`1,655` at 1h to `942` at 24h and `628` at 72h. A separate 7-day slice has only
`579` candidates and `24` complete 72h paths, so it is not sufficient for a
long-horizon trainer.

## Fresh model results

All models use decision-time features, 3-second entry/exit delays, 100 bps fee,
200 bps slippage, strict full-loss censoring, 1 BNB starting equity, 0.1 BNB
stakes, and an eight-position cap. The threshold is selected on validation;
the final split is only evaluated.

| Model | Validation portfolio | Final portfolio | Decision |
| --- | ---: | ---: | --- |
| 1h barrier +20/-30 | +0.2471 BNB, 57 trades | -0.4816 BNB, 38 trades | Reject |
| 1h uncapped | +0.4033 BNB, 55 trades | -0.4072 BNB, 36 trades | Reject |
| 1h barrier +50/-50 | +0.2921 BNB, 42 trades | -0.0622 BNB, 19 trades | Reject |
| 6h barrier +20/-30 | +0.4266 BNB, 47 trades | -0.9208 BNB, 34 trades | Reject |
| 6h barrier +100/-30 | -0.1537 BNB, 19 trades | -0.4134 BNB, 5 trades | Reject |
| 24h barrier +20/-30 | -0.8800 BNB, 46 trades | -0.9161 BNB, 20 trades | Reject |
| 24h uncapped +20/-50 | -0.3776 BNB, 7 trades | -0.6000 BNB, 6 trades | Reject |

The full reports are `11-model-short-1h-barrier.json` through
`23-model-long-24h-barrier50.json`. The attempted 24h uncapped +100% target
could not fit a classifier because the training slice had only one label class;
this is a tail-label sparsity failure, not a profitable result.

Selecting only the ranker's top 1% reduces the capital damage but does not
establish an edge. The best sealed final top-1% result is the 1h uncapped model
at `+0.0174 BNB` from seven trades. Bootstrap returns for those seven trades
are dominated by the small sample and are not a live acceptance criterion.

## Walk-forward and stress

An expanding walk-forward test trains on the past, purges one complete label
horizon, and takes a fixed top 1% with no validation threshold tuning. Four
non-overlapping test slices produce these cumulative profits:

| Model | Base costs | Moderate costs | Harsh costs |
| --- | ---: | ---: | ---: |
| 1h barrier +20/-30 | +0.1114 BNB | +0.0068 BNB | -0.1156 BNB |
| 1h barrier +50/-50 | +0.1406 BNB | +0.0224 BNB | -0.0907 BNB |
| 1h uncapped | +0.0830 BNB | -0.0502 BNB | -0.1564 BNB |
| 6h barrier +20/-30 | +0.0547 BNB | -0.5391 BNB | -0.7913 BNB |
| 24h barrier +20/-30 | -0.0258 BNB | -0.5937 BNB | -1.1458 BNB |

The detailed replays are `25-walkforward-top1pct.json` and
`26-walkforward-stress.json`. A previous-day whole-BSC volume gate did not
repair the failure: it reduced base profit and stayed negative under moderate
and harsh costs (`42-market-regime-gate.json`).

## Decision

The current evidence favors a short, flow-sensitive holding window over blind
one-to-three-day holding. The 1h `+50%/-50%` barrier is the strongest research
candidate in this window, but its final threshold replay is negative and its
harsh execution replay is negative. It remains shadow-only. Long holding loses
more often as paths age, and the market volume contraction after September 9
matches the final-split degradation.

The next useful improvement is data rather than another threshold sweep:
continue collecting complete post-entry paths, add timestamped DEX liquidity,
quote volume, transaction count, and price-impact snapshots, then train a
multi-horizon utility model with a predeclared top-k budget. Do not switch the
runtime model until a new independent window is positive under moderate and
harsh execution costs with enough final trades.

`docs/model_scoreboard.md` was updated for this round. Runtime `.env`,
`MODEL_DIR`, position sizing, and trading enablement were left unchanged.
