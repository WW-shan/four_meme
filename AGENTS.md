# PROJECT KNOWLEDGE BASE

**Generated:** 2026-03-10
**Branch:** `main`
**Commit:** `23d41ee`

## OVERVIEW

FourMeme Hybrid Trading System. Main path: collector -> dataset -> hybrid training -> bot.

This repo is a plain Python application repo, not a packaged library. `src` is the import root, and several entry scripts prepend the repo root to `sys.path`.

## PRECEDENCE

Nearest child `AGENTS.md` wins for files in its subtree. Use this root file for routing, global commands, and repo-wide constraints; use child files for local ownership and conventions.

## STRUCTURE

```text
./
├── config/        # env contract, RPC separation, trading knobs, ABI loading
├── docs/goals/    # long-running Codex goal workflow contracts
├── docs/plans/    # dated design/implementation notes
├── scripts/       # thin CLIs for dataset build and hybrid training
├── src/           # import root; package map and training/runtime code
├── systemd/       # collector service deployment notes
├── tests/         # unittest contract and workflow coverage
└── tools/         # memectl, collector entrypoint, shell runtime helpers
```

## WHERE TO LOOK

| Task | Location | Notes |
|---|---|---|
| Env / RPC / trading config | `config/` | See `config/AGENTS.md` |
| Shared package map / training cluster | `src/` | See `src/AGENTS.md` |
| Listener / WS / transport | `src/core/` | See `src/core/AGENTS.md` |
| Live bot behavior | `src/trader/` | See `src/trader/AGENTS.md` |
| Lifecycle / dataset / features | `src/data/` | See `src/data/AGENTS.md` |
| Local service control / collector ops | `tools/` | See `tools/AGENTS.md` |
| Long-running goal workflow | `docs/goals/` | Goal files are protected; see `docs/goals/AGENTS.md` |
| Deployment install steps | `systemd/README.md` | Covered by `tools/AGENTS.md` |
| Historical design context | `docs/plans/` | Reference only; not the runtime source of truth |

## CONVENTIONS

- Dependency surface is `requirements.txt`.
- Test surface is `python -m unittest discover`.
- No `pyproject.toml`, `setup.py`, `tox.ini`, or pytest config.
- `src` is the import root; do not assume a `src/<package_name>/...` layout.
- `.env.example` is the env contract template.
- RPC roles are intentionally separated: listener WS, listener HTTP logs pool, trade HTTP RPC.
- `ENABLE_TRADING=false` is the safe default; treat real trading as opt-in.

## GOAL WORKFLOW GUARDRAIL

- Do not create, rewrite, reorganize, or edit files under `docs/goals/` unless the user explicitly asks for a goal-document change in the current turn.
- A request to continue a goal, execute a goal, optimize models, update docs, commit/push, or improve the workflow is not permission to modify `docs/goals/`.
- If a goal workflow needs clarification or a proposed process change, discuss it with the user first; record approved operational evidence elsewhere, such as `docs/model_scoreboard.md`, `docs/research/`, or `docs/superpowers/plans/`.
- Before committing or pushing, review `git status --short --untracked-files=all -- docs/goals`, `git diff -- docs/goals/`, and `git diff --cached -- docs/goals/`, then ensure no goal file was added or changed unless that exact change was explicitly requested.

## GOAL TASK PHASE GATE

- Treat long-running goal work as a serial task chain unless the user explicitly authorizes parallel task tracks.
- Inspect the current work and `git status` before starting a follow-up task; finish or explicitly mark the current task blocked first.

## SCOREBOARD CLOSEOUT GATE

- Every model / live-research round must explicitly state in its closeout artifacts whether `docs/model_scoreboard.md` was updated, or why it was intentionally not updated.
- If a round changes the experiment conclusion, live-risk interpretation, or next model direction, append the scoreboard note before archive/commit/push.
- Treat "no scoreboard update" as an intentional decision that must be recorded in the round's `evidence.md`, `review.md`, or scoreboard note itself.
- Do not rely on memory or prior rounds to satisfy the scoreboard requirement; re-check the scoreboard in every new round.

## GITHUB PUBLISHING GATE

- A user request to "commit", "push", "提交", or "推送" means a plain git commit/push only. It does not include creating a pull request, opening a draft PR, or running a publish workflow that creates a PR.
- Do not use `gh pr create`, GitHub PR creation tools, or the GitHub publish/yeet flow unless the user explicitly asks to open a PR in the current turn.
- Before every push, check whether the current branch is attached to an open PR:
  `gh pr list --head "$(git branch --show-current)" --state open`
- If the current branch has an open PR, stop and tell the user that pushing will update that PR. Continue only after the user explicitly confirms that updating the PR is intended.
- If the user expects direct-to-main work, switch to or update `main` only after checking the worktree and confirming no unrelated local changes would be carried across.

## ANTI-PATTERNS

- Assuming pytest, tox, or packaging metadata drives the workflow.
- Mixing listener RPC endpoints and trade submission RPC endpoints.
- Repeating subtree rules here instead of moving them into a child file.
- Changing env-driven behavior without updating `.env.example` and relevant contract tests.
- Treating dated docs in `docs/plans/` as newer than code plus tests.

## UNIQUE STYLES

- `tests/core/` acts like infra contract coverage.
- `tests/model/` covers dataset, feature, training, and RL behavior.
- `docs/plans/` uses dated design/implementation files by topic.
- `tools/memectl` is the repo-supported Unix service wrapper; Windows work should prefer direct Python entrypoints unless editing the wrapper itself.

## COMMANDS

```bash
python -m venv .venv
pip install -r requirements.txt

python tools/collect_continuous.py
python scripts/build_dataset_new.py --lifecycle-dir data/training --output-dir data/datasets
python scripts/run_hybrid_training.py --output-dir data/models --lifecycle-dir data/training
python -m src.trader.bot

python -m unittest discover
python -m unittest tests.core.test_rpc_config
python -m unittest tests.model.test_run_hybrid_training_cli
```

## NOTES

- Root routes; children own details.
- For cross-cutting work, read every touched child file before editing.
- For config-sensitive runtime changes, trust code plus `tests/core/*` over assumptions.
