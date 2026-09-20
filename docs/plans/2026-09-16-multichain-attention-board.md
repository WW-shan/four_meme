# Multi-chain attention board implementation plan

Status: implementation complete; authenticated live validation pending personal credentials.
Owner: Codex. Date: 2026-09-16; last updated 2026-09-20.

## Objective and scope

Build an independently runnable attention service that discovers multi-chain
tokens and monitors explicitly configured X topics. Supply a human dashboard,
append-only evidence, and a versioned read-only API for a future trading client.
Phase one does not place orders, load trading keys, modify existing bot behavior,
or claim profitability. Initial chains: Solana, BNB Chain, Base, Ethereum.

The user authorized investigation followed by implementation, then explicitly
requested a written plan and standardized execution. Execute these phases in
order; complete a phase or record its blocker before closing it. No parallel
agent tracks. Do not modify docs/goals. Preserve the existing dirty worktree.

## Investigation and decisions

- GMGN official Agent API supplies multi-chain hot searches, token market data,
  and labeled-wallet information. Public-demo smoke requests returned success
  for sol/bsc/base/eth. Demo responses prove connectivity, not production SLA.
- GMGN search heat is distinct from X mentions. Observed rank and visiting_count
  do not always sort identically; preserve provider rank and expose the metric,
  rather than claiming an independently validated heat score.
- GMGN X Tracker is a product capability. General original-post streaming access
  was not confirmed in the inspected Agent API. SnipeX documents Solana only.
- Use X official recent search for the first bounded polling adapter. This is
  not second-level streaming or complete X coverage. Stream integration is a
  later extension after credentials, query scope, and costs are established.
- Social metrics from LunarCrush are paid and new-token coverage is untested.
  Telegram, Reddit, and additional providers are deferred, not simulated as live.
- No GMGN or X personal credentials were found in the standard local locations.
  Implement and test without credentials; authenticated acceptance remains
  pending until credentials are available. Never use the public demo key for
  unattended production collection.
- Separate this new domain under src/attention. Do not reuse the old BSC heat
  schema, which lowercases addresses and lacks chain-qualified token identity.
- Reuse existing requests/python-dotenv dependencies; SQLite and local HTTP use
  Python's standard library. No new frontend build chain is needed for phase one.

## Data and API contracts

1. Token identity is (chain, address). EVM addresses normalize to lowercase;
   Solana base58 addresses preserve case and validate to 32 decoded bytes.
2. Topic identity is a stable configured topic ID. A topic may have zero or many
   candidate contracts. Ticker/name matches never establish contract identity.
3. X post identity is the source post ID. Overlapping queries and repeated
   observations do not inflate mentions. Original/repost/quote/reply counts and
   cumulative public engagement metrics are kept separate.
4. A contract string in a post is an observed mention, not creator endorsement,
   proof of an official token, or a trade authorization. Ambiguous EVM chains
   stay unresolved; provider metadata is not substituted for post evidence.
5. Source publication time and local receipt time remain separate. Historical
   snapshots use only observations available at the requested as-of time.
6. Keep raw responses, normalized observations, durable cursors, source health,
   and daily request usage. Raw responses exclude authentication headers/keys.
7. Missing, zero, stale, unconfigured, rate-limited, incomplete pagination, and
   demo are distinct states. Missing social coverage is never reported as a
   measured zero. Source outages are not interpreted as heat decay.
8. No opaque combined GMGN+X score. Show X activity, a transparent heuristic
   social ranking, and GMGN platform interest independently. Rankings are not
   predictions of return. Insufficient history suppresses acceleration claims.
9. HTTP exposes GET-only /api/v1/board, /api/v1/events?after=..., and health.
   Every response identifies schema version, observation time, mode, and lack
   of trading authorization. Default bind is localhost. Live/demo DBs separate.
10. Collector credentials stay server-side. Queries, budgets and source scope
    use a reviewed local JSON config. Env credentials are documented in
    .env.example and contract-tested. No private wallet key is required.

## Serial execution checklist

### Phase 1: evidence and design

- [x] Inspect worktree, root/subtree instructions and existing BSC assumptions.
- [x] Fetch official GMGN/X/social-provider documentation and inspect SDK paths.
- [x] Perform read-only GMGN multi-chain smoke; record limits and unknowns.
- [x] Confirm credentials readiness without exposing secret values.
- [x] Write this plan before continuing implementation.

Acceptance: scope and source semantics are explicit; no invented X endpoint,
unqualified multi-chain identity, or profitability claim.

### Phase 2: data service and collector

- [x] Implement validated configuration and chain-qualified identities.
- [x] Implement durable observations, cursor/budget state and source health.
- [x] Implement GMGN and X adapters with bounded requests and safe errors.
- [x] Implement deduplicated windows, topic/token associations and as-of views.
- [x] Add CLI lifecycle, local read-only API and separate offline demo data.

Acceptance: restart does not skip pagination; overlapping queries do not double
count posts; no future observations leak into historical views; malformed or
missing source data cannot silently become healthy trading candidates.

### Phase 3: dashboard

- [x] Deliver responsive topic, token and KOL-event views with evidence links.
- [x] Expose chain/filter/window controls, contract status, source freshness,
      empty/error/demo states, and documented ranking semantics.
- [x] Prevent provider text/URLs from executing HTML/JS; never expose secrets.

Acceptance: desktop and mobile browser smoke pass, filters work, no console
errors, unknown CA remains visible without being promoted to a confirmed token.

### Phase 4: verification and review

- [x] unittest coverage: identities, dedupe, windows, timestamp causality,
      association ambiguity, pagination, budgets, retries and API validation.
- [x] Adapter tests against documented response envelopes and saved samples.
- [x] Run focused tests, existing affected contracts, then full unittest suite.
- [x] Browser verification using the playwright skill; record results.
- [x] Review the diff for credential leaks, trading coupling and regressions.

Acceptance: required checks pass or exact environmental blockers are recorded.
Do not treat demo tests as authenticated integration or continuous uptime tests.

### Phase 5: delivery and operational handoff

- [x] Provide quickstart, source-query examples, request-cost caveats, API usage,
      backup/restart procedures and production readiness checklist.
- [x] Record evidence/review and scoreboard decision; update this plan status.
- [x] Report implementation, validation, remaining credential blockers and exact
      startup commands. No commit/push without user request.

Acceptance: a user can start the demo immediately and start the live service
after configuring credentials and explicit queries. Continuous real X/GMGN
ingestion remains unverified until authenticated operation is observed.

## Delivery and operations

### Quickstart: offline demo, no credentials

```bash
python3 scripts/run_attention_board.py demo --db data/attention/demo.sqlite --port 8787
# open http://127.0.0.1:8787
```

`demo` seeds a frozen, clearly labelled synthetic fixture. It refuses to collect
live data and cannot share the live database.

### Live service

1. Create a personal GMGN API key at https://gmgn.ai/ai. This service only uses
   the read-only key; it never loads a wallet or `PRIVATE_KEY`, and the public
   demo key must not be used for unattended collection.
2. Create an X developer app and bearer token for the official recent-search
   endpoint. Check current pay-per-use pricing and set the daily request cap
   before leaving it running. Recent search is bounded polling, not a full
   firehose and not second-level streaming.
3. Copy `config/attention_sources.example.json` to a local file and add explicit
   topic queries plus curated handles, for example:

```json
{
  "chains": ["sol", "bsc", "base", "eth"],
  "poll_seconds": 60,
  "stale_seconds": 180,
  "x_daily_requests": 200,
  "kol_accounts": ["example_kol"],
  "topics": [
    {"id": "new_pair", "name": "New pair chatter",
     "query": "(new pair OR launch) -is:retweet"},
    {"id": "sample_narrative", "name": "Sample narrative",
     "query": "(\"sample narrative\" OR samplecoin) -is:retweet"}
  ]
}
```

4. Export credentials only in the process environment (never commit them):

```bash
export GMGN_API_KEY=...
export X_BEARER_TOKEN=...
```

5. Verify one bounded cycle, then run the dashboard with collection:

```bash
python3 scripts/run_attention_board.py collect --once --sources config/attention_sources.local.json
# exit 0 only when every configured source reported ok; exit 2 otherwise
python3 scripts/run_attention_board.py serve --sources config/attention_sources.local.json --collect
```

A changed query must use a new topic id; reusing an id with a different query is
rejected so collected history stays comparable.

### Read-only API

```text
GET /api/v1/board?chain=bsc&as_of=<unix seconds>
GET /api/v1/events?after=<observation sequence>&limit=100
GET /api/v1/health
```

The surface is GET-only. Every response carries `mode`, `as_of` and
`trading_enabled=false`. `as_of` accepts only positive, non-future timestamps,
and a historical view never reads observations that arrived later.

### Backup, restart and retention

- All state is one SQLite file in WAL mode (append-only observations plus
  cursors, daily request budgets and source health). Stop the process before a
  plain file copy, or copy with `sqlite3 <db> ".backup <target>"`.
- Restart resumes X pagination from the persisted cursor, reuses persisted rate
  limits and budgets, and does not skip pages.
- Raw provider pages are retained for audit. A retention/compaction policy is
  not implemented yet; watch disk usage before long unattended runs.

### Production readiness checklist

- [ ] Personal GMGN key issued and IP allowlist understood.
- [ ] X bearer token funded with an explicit spending limit and daily cap.
- [ ] Reviewed topic queries and KOL list; none treat a name match as a contract.
- [ ] `collect --once` reports `ok` for every configured source.
- [ ] Dashboard reports fresh source status, and a deliberately stopped source
      shows `stale` rather than heat decay.
- [ ] Operator understands the board is a discovery aid, not a trade signal.

## Verification evidence

- 2026-09-20 `python3 -m unittest tests.attention.test_attention_service`:
  41 tests pass, including saved GMGN and X response envelopes.
- Full repository `python3 -m unittest discover` with `PYTHON_DOTENV_DISABLED=1`:
  see the latest execution-log entry. Without that variable, one pre-existing
  contract test reads the local `.env`
  (`tests/core/test_env_template_rpc_sections.py::test_trading_config_exposes_action_policy_router_defaults`)
  and fails; the failure is unrelated to this feature and unchanged by it.
- Playwright browser smoke against demo data: desktop 1440x1100 and mobile
  390x844; Rising/Sustained/Tokens/KOL views, chain/window/search filters, token
  evidence dialog, partial-window and provider-only states; 0 console errors and
  0 warnings.
- Read-only GMGN multi-chain smoke (2026-09-16): HTTP 200 and business success
  for sol/bsc/base/eth hot searches. The raw sample stayed in `/tmp` and is not
  committed.
- Authenticated GMGN/X collection is still unobserved: no personal credentials
  exist in the standard local locations.

## Scoreboard closeout

`docs/model_scoreboard.md` was updated intentionally on 2026-09-20 with an
infrastructure note because the round records a next-direction capability shift.
This round adds no model result, no promoted threshold, no sizing change, no
`.env` trading switch, no bot process change and no live-risk reinterpretation.
No profitability claim is made.

## Validation and release gate

Use python -m unittest (repository contract), not pytest. Meaningful behavioral
tests must cover causal and failure cases, not only implementation mirrors.
Check UI in an actual browser at desktop and mobile widths. Network smoke is
read-only and bounded; never execute transactions. Data service can run 24/7,
but upstream coverage depends on credentials, budgets, backlogs and outages.
Do not call phase one a profitable strategy or ready-to-trade system.

## Risks / deferred work

- API access, pricing, quality and rate limits can change; authenticated account
  quotas and actual latency need production sampling.
- Recent-search polling misses deleted/unavailable posts and is bounded by
  configured queries and pagination. There is no claim of all-social coverage.
- Curated KOL labels are explicit configuration, not inferred endorsement.
- Exact-address association leaves many narrative-only posts unresolved;
  semantic entity resolution and human verification are future work.
- Long-term retention/compaction, stream collectors, authenticated public
  hosting, alert delivery, simulation and trading adapters are later phases.

## Execution log

- 2026-09-16: phase 1 complete. Phase 2 in progress: initial configuration,
  identity, SQLite store and polling adapters written; not yet validated.
- 2026-09-16: phase 2 local behavioral acceptance passed: 23 unittest cases
  covering provider envelopes, chain identity, deduplication, causal views,
  pagination restart, persisted budgets/cooldowns and API validation. Personal
  credentials remain missing; live authentication is not claimed. Phase 3 begins.
- 2026-09-18: phase 2 hardening added atomic page publication, persisted rate
  limits shared across X topics, frozen X pagination bounds, an entity index for
  as-of snapshots, strict configuration shapes and stale-source preservation;
  39 unittest cases passed. Full repository run: 1212 tests, 1 skipped, 0
  failures (with `PYTHON_DOTENV_DISABLED=1`).
- 2026-09-20: phases 3-5 complete. Two saved provider envelopes raised focused
  coverage to 41 tests; full repository run: 1214 tests, 1 skipped, 0 failures
  (with `PYTHON_DOTENV_DISABLED=1`). Desktop and mobile Playwright smoke passed
  with 0 console errors. Dashboard, quickstart, operational checklist and scoreboard note are
  delivered. Remaining blocker is authenticated validation: no personal GMGN or
  X credentials, so continuous live ingestion is not claimed. No commit or push
  was requested or performed.

## Primary sources

- https://github.com/GMGNAI/gmgn-skills/blob/main/docs/cli-usage.md
- https://docs.gmgn.ai/index/gmgn-agent-api
- https://docs.gmgn.ai/index/x-tracker
- https://docs.gmgn.ai/index/snipex
- https://docs.gmgn.ai/index/gmgn-callout-openapi
- https://docs.x.com/x-api/posts/recent-search
- https://docs.x.com/x-api/posts/filtered-stream/introduction
- https://docs.x.com/x-api/getting-started/pricing
- https://lunarcrush.com/developers/api
- https://docs.nansen.ai/api/smart-money
