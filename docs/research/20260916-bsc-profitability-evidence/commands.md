# Reproduction and evidence commands

The documented RPC requests are read-only and use `LOCAL_PROXY_URL` from `.env`.
Keys and the contents of `.env` are not included in evidence. Metadata acquired
at `latest` is labeled as later audit evidence and is not used as a past signal.

## Reproduce the 615-row audit without network

```bash
python scripts/audit_bsc_profitability_evidence.py \
  --candidate-report docs/research/20260913-bsc-strategy-foundation/70-market-cap-heat-failure-attribution-20260914.json \
  --lifecycle-dir data/training/latest_bsc_20260914_31d_final \
  --quote-snapshot docs/research/20260916-bsc-profitability-evidence/quote-universe.json \
  --asset-snapshot docs/research/20260916-bsc-profitability-evidence/asset-metadata-latest.json \
  --output docs/research/20260916-bsc-profitability-evidence/candidate-audit.json
```

## Read-only RPC evidence

- `_tokenInfos(address)` selector `0xe684626b` on manager
  `0x5c952063c7fc8610ffdb798152d69f0b9550762b`: `quote-universe.json` saves
  the acquisition block, response, and decoded fields for all 615 candidates.
- ERC-20 `decimals()` `0x313ce567`, `symbol()` `0x95d89b41`:
  `asset-metadata-latest.json` saves actual acquisition time and `latest` tag.
- Helper `getTokenInfo(address)` `0x1f69565f` on
  `0xf251f83e40a78868fcfa3fa4599dad6494e46034`, separate RPC provider:
  `helper-crosscheck.json` cross-checks the three formerly called true failures.
- `eth_getLogs` with the ABI-derived LiquidityAdded topic, blocks
  `117450400..117450490`: `liquidity-logs-rpc.json` contains the raw event.
- Historic `eth_call` failure responses and alternate providers:
  `historical-quote-smoke.json`, `historical-provider-checks.json`.

## Web research

Executed via `smart-search` with HTTP/HTTPS proxy environment populated from
`LOCAL_PROXY_URL` by a subprocess wrapper, without writing runtime config:

```bash
smart-search doctor --format json
smart-search deep 'BSC Four.meme quote/execution/attention profitability evidence' --budget deep --format json
smart-search fetch 'https://docs.uniswap.org/contracts/v2/concepts/core-concepts/swaps' --format markdown
smart-search fetch 'https://defillama.com/chain/bsc' --format markdown
```

The task-local deep-plan file retains the exact Chinese decomposition question.
`heat-profit-search.json` retains the exact broader query and the 503 failure;
it is not a source of market claims. `fetch-log.json` records fetched URLs and
the unsuccessful Four.meme documentation URL. The market JSON came from the
public data endpoint stated in `current-bsc-dex.json`.
