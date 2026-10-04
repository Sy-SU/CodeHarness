# CodeHarness

[English](README.md) | [简体中文](README_zh.md)

CodeHarness is the macOS client side of a coding-agent research setup. It owns the single-agent runtime, model routing, tools, per-task workspace, trace, and experiment reporting. MiniOJ judging uses Bearer-authenticated HTTP JSON. Contest membership currently has no JSON endpoint, so a narrow public-HTTP-page adapter reads its visible problem list without accessing server internals.

Current capabilities and frozen behavior: [STATUS.md](STATUS.md). Future work: [TODO.md](TODO.md). Complete phase/dashboard evidence: [history](docs/history/README.md).

Phases 0–5 implementation and mock acceptance are complete. A minimal live T1003 experiment retained failures. Four fixed strategies plus a mixed strategy are now configured, once each, with verdict-only Agent feedback; explicitly declared full server feedback is accepted through client filtering. This five-group live comparison has not run. The distribution is client-only (`agent`, `experiments`, local task-launching `dashboard`); embedded MiniOJ/server code is excluded. Local and live evidence is recorded separately.

## Current verified scope

- client-only packaging, dependencies, console entries, and environment settings;
- CodeHarness-owned problem, submission, feedback, error, model-response, state, and trace-event types;
- typed Bearer HTTP operations, strict success-status/JSON validation, classified transport/HTTP/protocol/result-unknown failures, and OJClient-owned polling;
- explicit Custom Run compatibility mode: canonical `source_code` by default, optional legacy `code` alias;
- schema-checked tools, atomic workspace files, protected managed files, explicit overwrite, traversal/symlink rejection, and correlated redacted Trace events;
- a model-free fixed-solution workflow that persists problem, source, submission states, verdict, feedback, State, and Trace without executing code locally;
- normalized model responses, safe errors, optional usage, tool-call fields, four-profile routing, explicit Role policy, and a ModelCallRuntime that traces both success and failure;
- unknown/null cost when usage or pricing is missing, never an invented zero/free estimate;
- versioned protocol example fixtures that label agreed cores, draft details, unconfirmed error shape, and forward-compatibility-only values;
- task-artifact ignore rules that ignore only the root `/workspace/`, not `agent/workspace/` source;
- a client-only pytest entry independent of MiniOJ server fixtures;
- strict code-only execution: one CODE call, at most one formal submission, no Custom Run or feedback, explicit failure terminals, and source linkage through model-call ID, SHA-256, and submission ID;
- a fixed harness state machine, sample gate, replanning after three failed DEBUG candidates, ten submissions per task and a configurable default 1-CNY budget, read-only review and checkpoints;
- five-strategy configuration, independent task budgets and an aggregate cap, JSON/CSV exports, Trace audits and safe recovery; Dashboard budget input, confirmation, idempotency and CSRF protection;
- automated tests use fixtures, fakes, MockTransport and local ASGI only; current commands/counts are recorded in [verification evidence](docs/evidence/20261002-trust.md).
- one real fixed-solution MiniOJ chain: `t1001` submission `sub_FhqFt4PN68kPAJ7N` reached `FINISHED / AC`, passed 5/5 tests, and made zero LLM calls.
- one real model probe: `qwen3.8-flash` returned the expected text with 74/46 usage tokens; it is not promoted to a formal Profile mapping.
- one real code-only chain: an ephemeral `standard` mapping to `qwen3.8-flash` made exactly one CODE call and submission `12` for `t1001`, reaching `FINISHED / AC` with 230/222 usage tokens.

Phase 4 also verified a real public sample and missing-price preflight. On 2026-10-01, Phase 5 ran T1003 once per mode: four model calls, one failed public-sample run, zero formal submissions; both tasks stopped at `invalid_model_output`. The harness entered DEBUG but its response did not contain complete code. Estimated cost using user-configured rates was 0.0421137 CNY; conservative reservations were 0.1490944 CNY, not a verified invoice. The server did not advertise Feedback Mode, so these are unverified conditions, not a fair feedback comparison or live AC loop.

## Architecture boundary

```text
macOS CodeHarness                                      remote MiniOJ
┌─────────────────────────────────┐                  ┌───────────────────┐
│ Agent / Context / State / Trace │                  │ HTTP JSON API     │
│ Model policy / router / adapter │                  │ judge / testcase │
│ Tools → CodeHarness OJClient    ├── Bearer HTTP ─►│ sandbox / worker  │
└─────────────────────────────────┘                  └───────────────────┘
```

CodeHarness does not import MiniOJ modules, share its database or testcase files, start a local judge, execute submitted C++ locally, or use SSH/remote shell as an API substitute. See [docs/architecture.md](docs/architecture.md) and [TODO.md](TODO.md).

## macOS setup

Requirements: Python 3.9+ and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env
cp config/models.example.yaml config/models.yaml
cp config/harness.example.yaml config/harness.yaml
uv run codeharness-agent --help
uv run codeharness-model-client --help
uv run codeharness-oj-client --help
uv run codeharness-report --help
uv run codeharness-experiment --help
uv run pytest
```

The default test command collects only `client_tests/`. It makes no network request, calls no model, and submits nothing to MiniOJ.

`.env.example` intentionally contains blank endpoint and secret fields:

```dotenv
OJ_BASE_URL=
OJ_API_TOKEN=
BAILIAN_API_KEY=
BAILIAN_BASE_URL=
MODEL_CONFIG=./config/models.yaml
```

Provide OJ and model-provider connections through `.env`; model IDs, parameters, Role mapping, and optional pricing come from YAML. Do not commit `.env`; it is ignored. `config/models.example.yaml` contains placeholder models and no prices, not confirmed experiment choices.

## Model client

`probe` sends one fixed, short connectivity prompt and persists Provider, model, usage, latency, cost status, and correlated Trace evidence. It makes one real model call and therefore requires explicit confirmation:

```bash
uv run codeharness-model-client probe \
  --profile <fast|standard|strong|max> \
  --model-config <models.yaml> \
  --workspace-root workspace \
  --confirm-call
```

The call is never automatically retried. Missing usage is counted separately; missing pricing produces `estimated_cost: null`. Neither the Phase 2 probe nor the Phase 3 code-only check with `qwen3.8-flash` is a formal Profile decision.

## Model-free MiniOJ client

Read one sanitized problem without submitting:

```bash
uv run codeharness-oj-client inspect-problem <problem-id> \
  --http-timeout <seconds>
```

Run the Phase 1 fixed-solution chain. All transport and polling values are explicit because U03 has no approved defaults. `--confirm-submit` is required because the command creates a real remote formal submission.

```bash
uv run codeharness-oj-client submit-fixed <problem-id> <solution.cpp> \
  --workspace-root workspace \
  --http-timeout <seconds> \
  --poll-interval <seconds> \
  --deadline <seconds> \
  --confirm-submit
```

The command does not compile or run the source locally. It uses MiniOJ HTTP only. It never automatically retries a request; a formal-submission timeout or an invalid 202 Accepted response is recorded as `result_unknown` to prevent blind duplicate POSTs.

## Phase 3 code-only

```bash
uv run codeharness-agent solve <problem-id> \
  --mode code-only \
  --profile <fast|standard|strong|max> \
  --model-config <models.yaml> \
  --workspace-root workspace \
  --http-timeout <seconds> \
  --poll-interval <seconds> \
  --deadline <seconds> \
  --confirm-model-call \
  --confirm-submit

uv run codeharness-report workspace
```

`solve --mode code-only` makes one CODE call and at most one submission, with no samples, feedback, repair or retry. All terminals are persisted. Both the direct CLI and compatibility entries now enforce a default 1-CNY reservation budget, requiring effective CNY prices/token bounds. Use `--max-cost-cny 2` to change it.

## Phase 4 harness and recovery

Configure verified `input_cost_per_million`, `output_cost_per_million`, `currency: CNY`, an effective `input_token_limit`, and `parameters.max_tokens` for each used Profile. The output parameter must be declared in the Provider's `supported_parameters`. Prices/input bounds are intentionally absent from the example: it is not ready for a paid loop.

```bash
uv run codeharness-agent solve <problem-id> \
  --mode harness-loop \
  --model-config <models.yaml> \
  --harness-config config/harness.example.yaml \
  --workspace-root workspace --task-id <task-id> \
  --http-timeout <seconds> --poll-interval <seconds> --deadline <seconds> \
  --confirm-model-call --confirm-submit

uv run codeharness-agent resume <task-id> \
  --model-config <same models.yaml> --workspace-root workspace \
  --http-timeout <seconds> --poll-interval <seconds> --deadline <seconds> \
  --confirm-model-call --confirm-submit
```

Base roles stay PLAN → strong, CODE / DEBUG → standard, with escalation disabled. Add `--profile standard` to explicitly fix every model Role. New candidates use sample_check_v3: trusted exact/token/float checking with both explicit float tolerances, or execution-only public samples for unverifiable semantics. Known OK/exit=0 permits formal Judge evaluation with passed=null; runtime failures can DEBUG, and unknown/infrastructure failures stop. Main disables LLM checkers; old v1/v2 experimental gates retain their frozen behavior. Three consecutively failing DEBUG candidates trigger a new PLAN. REVIEW runs locally only after formal AC, checks the judged version/hash and writes a summary without model calls or source edits.

Each independent task permits at most ten formal POST attempts and defaults to 1 CNY. YAML `max_cost_cny` or CLI `--max-cost-cny 2` can raise/lower the budget. New tasks reserve the full configured input/output ceiling before calling, then settle known usage at configured rates and release the unused difference. `budget_committed_cny` is settled cost plus unresolved reservations, used by both task and aggregate caps. `BUDGET_SETTLEMENT` records the reservation, usage cost, release and call association. Unknown/interrupted or excessive usage retains its reservation and stops; historical checkpoints keep their frozen cumulative-reservation policy. Estimates do not automatically include cached-input discounts, promotions or free quotas and are not invoices. Billing protection depends on accurate tariffs/effective provider bounds. Missing prices/bounds/usage or excessive usage stops the loop. The 80-call backstop remains; no wall-clock limit is enabled by default.

New Harness tasks retry malformed CODE/DEBUG output with the same Role/Profile up to three additional times per candidate. Configure `max_code_extraction_retries` in the harness YAML (`3` by default; `0` disables correction). Corrections explicitly request one complete fenced C++20 program, count toward model-call and cost limits, and emit `CODE_EXTRACTION_RETRY`. Invalid responses never execute or submit. Exhaustion retains `invalid_model_output / code_extraction_failed`; transport errors and unknown requests are not retried. The policy/count are checkpointed; legacy frozen tasks, code-only and independent checker generation retain their original behavior.

Atomic `checkpoint.json` retains the single-task Context, progress, counters, responses and actual config; `state.json` is its projection. Problem, plan and code stay complete; feedback prompts keep 8000 characters and five recent summaries. Oversize prompts stop instead of silently cutting code. Resume requires matching routes/prices/bounds, policy and OJ endpoint, with a per-task lock. Saved responses are reused and known submission IDs are queried without another POST. An interrupted model request/submission POST with unknown effects stops as `result_unknown`, never blindly reissued. A timed-out formal poll with a known ID can resume; other completed tasks simply return the saved result.

## Phase 5 experiments

Verify the real models, CNY rates and effective token bounds in your local model configuration first. `config/experiment.smoke.yaml` uses T1003, fixed standard, one repetition of each mode and an explicit 1-CNY total cap:

```bash
uv run codeharness-experiment run config/experiment.smoke.yaml \
  --experiment-id <new-id> --workspace-root workspace \
  --confirm-model-call --confirm-submit
uv run codeharness-experiment resume config/experiment.smoke.yaml \
  --experiment-id <same-id> --workspace-root workspace \
  --confirm-model-call --confirm-submit
```

Each strategy/repetition is an independent task with ten POST attempts and a default 1-CNY budget, not a shared per-problem allowance. The explicit aggregate cap still applies. Experiment CLI `--max-cost-cny` overrides task budgets without raising the aggregate cap. Code-only still makes just one CODE call; known usage (including reported usage on failed calls) is settled, while unresolved calls retain their reservations. Provider bound violations stop the batch and block resume.

`workspace/.experiments/<id>/` contains a frozen `manifest.json`, per-task `tasks.json/csv` and grouped `summary.json/csv`, with actual routes, Role policy, budget, usage/cost status, configuration fingerprints and Trace audits. Completed resume only rebuilds reports. Interrupted code-only requests are never regenerated; harness recovery uses checkpoints and existing submission IDs.

`config/experiment.full.yaml` configures the confirmed T1003 five-group comparison: standard/strong × both modes plus `mixed-harness` (PLAN strong, CODE/DEBUG standard), once each. Per-task cap is 1 CNY, aggregate allocation cap 5 CNY, not predicted spending. The template has the same five strategies. Local models/template request `max_tokens: 8192`; effective provider limits/tariffs still need verification. Changed parameters, budgets or allocation semantics require a new experiment ID, not a silent resume.

```bash
uv run codeharness-experiment run config/experiment.full.yaml \
  --experiment-id <new-verdict-only-id> --workspace-root workspace \
  --confirm-model-call --confirm-submit
```

The formal configuration requires `expected_feedback_mode: verdict_only` and `require_feedback_mode: true`; the historical `experiment.full.yaml` filename is not a full-feedback requirement. On 2026-10-02, `/api/v1/me` explicitly declared `feedback_mode: full`; contest 1 records actual=full/effective=verdict_only. Historical unknown snapshots remain unchanged. Unknown/mismatched modes still stop before paid calls; neither permissions nor diagnostics substitute for metadata. Historical unknown-mode smoke results are not verified comparisons. [Experiment details](experiments/README.md).

For new verdict-only tasks, declared server `full` is compatible: `actual_feedback_mode` remains `full`, while `effective_feedback_mode` is `verdict_only`. Versioned policy `formal_verdict_only_v1` strips remote summary, diagnostic/hidden-test data and all extra fields from formal submission/feedback results before tool traces, artifacts, checkpoints or prompts; formal errors also withhold remote prose. Public sample Custom Run output remains available. Unknown modes still stop; `verdict_only` cannot satisfy an explicit full requirement. The policy is frozen in task/batch fingerprints: use a new ID for old exact-verdict-only runs, never silently change their conditions on resume. Restart the Dashboard to load it.

## Contest tests

Open **Contests** in the Dashboard, enter a contest ID, choose a strategy/mode and confirm the bounded run. Per-problem task settings apply independently (default 1 CNY, at most ten formal submissions), with a separate **1-CNY whole-contest cap** by default. Only public membership is read; submissions use the contest-specific endpoint, without enrollment or contest administration. Reports count AC/total, per-problem outcomes, calls, submissions and configured cost estimates—not official points, penalties or rankings.

```bash
uv run codeharness-experiment contest 1 --experiment-id contest-1-run-001 \
  --profile standard --max-total-cost-cny 1 \
  --confirm-model-call --confirm-submit
uv run codeharness-experiment contest-resume 1 --experiment-id contest-1-run-001 \
  --profile standard --max-total-cost-cny 1 \
  --confirm-model-call --confirm-submit
```

Reports live in `workspace/.contests/<run-id>/` (`contest.json`, `report.json`, `problems.csv`), backed by the existing experiment/task journals. Resume requires the same settings and freezes membership; completed runs make no remote requests, and uncertain model calls/POSTs are never repeated. Unpublished or changed/unrecognized public pages block paid calls; start a new run once visible. Contest CLI supports per-problem `--max-cost-cny`, `--max-llm-calls` and `--max-submissions`. Code-only retains its one-call/one-submit semantics without samples.

Live contest `1` smoke on 2026-10-02: fixed standard/harness-loop, 0.35 CNY and six calls per problem, 0.7-CNY aggregate cap. B (CF1454B) accepted as submission `103`; A (CF1454A) failed token sample comparison, then DEBUG produced no complete code, with no formal submission. Total: **1/2 AC, five model calls, one formal submission, estimated 0.0255813 CNY**, not a verified invoice. A permits multiple valid answers: its alternative valid permutation was rejected by the existing text gate. That historical result is unchanged. New v3 tasks use trusted checking or execution-only fallback; no historical AC is invented.

## Checker, recovery and offline experiment preparation

`config/checker.example.yaml` retains an explicit legacy experimental gate. New defaults are sample_check_v3 / disabled LLM checker / execution_only fallback; Main generates no checker. Agent/Dashboard `--sample-config` and Experiment `sample_checking` freeze the policy, and old checkpoints cannot upgrade on resume. Standalone checker generation/smoke remains available experimentally and remote-only. [Checker policy](docs/checker-policy.md).

`first_try_ac`, `recovered_to_ac`, formal/sample recovery types, DEBUG/replan/candidate/reject/unverifiable counts and proof links are exported to State/results/JSON/CSV and the Dashboard. Formal recovery requires a linked program failure → successful DEBUG → later candidate → formal AC → REVIEW. Infrastructure errors and generated-checker rejection earn no recovery credit. Existing history is read without rewriting it.

`config/experiment.small.yaml` prepares 12 publicly verified problems (three each at 1200/1400/1600/1800), the same five conditions, and one repetition: 60 independent tasks. **Preparation does not execute them.**

```bash
uv run codeharness-experiment prepare config/experiment.small.yaml \
  --experiment-id small-prepared --output /private/tmp/codeharness-small-plan.json
# Optional --freeze-model-config snapshots local configured models without HTTP.
uv run codeharness-experiment contest-performance 1 --experiment-id <saved-run-id>
```

The second command only refreshes official standings metadata for a saved run with frozen account identity, without loading a model or running Agent. The official value comes directly from rows[].performance, matched by user_id; it is account-wide for the entire contest, including other runs. No formula or per-task Performance is invented. Old runs without frozen identity remain null. Dashboard GET/polling stays local; an explicit protected refresh saves separate performance.json. Configured model limits/prices and endpoint fingerprints are frozen; unverified supplier fields stay null. [Experiment protocol](docs/experiment-protocol.md).

## Local Dashboard

Choose a problem/mode/strategy, then expand **Task configuration** to edit cost, model-call and submission-attempt limits, MiniOJ HTTP timeout, polling interval and judge deadline. The live summary shows limits and Reset restores server defaults. Defaults are 1 CNY / 80 model calls / 10 submissions; code-only locks calls/submissions to one. Confirmation launches the shared execution service with frozen task settings, without editing model YAML or saved tasks. Diagnostics stay collapsed; resume retains all saved settings.

Use **中文 / English** to switch language; protocol/code/model values remain unchanged. Browsing and polling never initiate remote calls. Launch/resume requires confirmation. Restarting the server never automatically replays queued tasks; only eligible UI harness checkpoints offer manual resume.

```bash
uv sync --extra dashboard
uv run --extra dashboard codeharness-dashboard --workspace-root workspace \
  --model-config config/models.yaml --harness-config config/harness.yaml
# Open http://127.0.0.1:8765
```

Use `--read-only` to remove control endpoints and retain the old observation page. Browsing works without valid model configuration; clients are loaded only at launch, and missing prices/bounds stops before a paid call. The CLI selects `MODEL_CONFIG` from the environment/local `.env` unless overridden. Only loopback hosts are allowed. Writes require exact same-origin, CSRF, explicit confirmation and a durable request ID; browser commands/config-path overrides are rejected. HTTP/poll/deadline defaults are 15/1/120 seconds, overridable at startup. Plain `uv sync` keeps Web dependencies optional.

Task defaults come from server YAML/CLI; `--max-cost-cny 2` overrides the cost default, HTTP/poll/deadline default to 15/1/120 seconds. Form edits apply only to a new task. Ten submissions remains a hard ceiling, three-failed-DEBUG replanning/escalation-off unchanged. Model-call limits count PLAN/CODE/DEBUG and generated sample checkers, not every HTTP request; public samples do not count as submissions. The default is `--feedback-mode verdict_only`; explicit `--feedback-mode full` remains available for a separate full-feedback experiment. Neither setting grants OJ permissions or fabricates the actual mode. Restart the Dashboard to load changed defaults; saved tasks retain their original conditions. UI launches have no aggregate cap; use Experiment Runner for batch protection.

```bash
uv sync --extra dev --extra dashboard
uv run --no-sync pytest
```

Dashboard tests use synthetic temporary Workspace files and local ASGI requests. Unknown costs/usage remain Unknown; attempts and confirmed submissions are separate metrics. Detail pages poll every 2 seconds while running, drain final events once, and stop at terminal state. [Dashboard architecture, API, limits and validation](docs/dashboard.md).

## Repository structure

```text
agent/                       # installable client runtime
  config.py                  # client-only environment settings
  core/                      # fixed single-agent loop/context/policy
  models/                    # provider, registry, router, call runtime, and probe
  oj_client/                 # HTTP client and CodeHarness-owned wire types
  tools/                     # tool runtime
  workspace/                 # task state, files, and trace source
experiments/                 # batch configuration, execution and JSON/CSV reports
dashboard/                   # optional local task launch and observation
client_tests/                # independent Phase 0–5 / Dashboard tests
config/                      # non-secret model placeholders / harness policy
docs/architecture.md         # boundaries and recorded decisions
docs/dashboard.md            # current local UI and controls
STATUS.md                    # short current capabilities/frozen behavior
TODO.md                      # future unfinished work
docs/history/                # complete phase/dashboard/decision evidence
```

Protocol examples live in `client_tests/fixtures/protocol/`. They are client fixtures, not a shared Python schema package and not proof that a remote endpoint implements the draft.
