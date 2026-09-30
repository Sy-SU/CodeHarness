# CodeHarness

[English](README.md) | [简体中文](README_zh.md)

CodeHarness is the macOS client side of a coding-agent research setup. It owns the single-agent runtime, model routing, tools, per-task workspace, trace, and experiment reporting. MiniOJ is a separately deployed service and is reached only through HTTP JSON with a Bearer token.

Phases 0 and 1 are complete. The repository and installable distribution are client-only: the legacy embedded MiniOJ server, shared judge code, and server tests have been removed. The wheel contains only `agent` and `experiments`; local tests and real MiniOJ submission evidence are recorded separately.

## Current verified scope

- client-only packaging, dependencies, console entries, and environment settings;
- CodeHarness-owned problem, submission, feedback, error, model-response, state, and trace-event types;
- typed Bearer HTTP operations, strict success-status/JSON validation, classified transport/HTTP/protocol/result-unknown failures, and OJClient-owned polling;
- explicit Custom Run compatibility mode: canonical `source_code` by default, optional legacy `code` alias;
- schema-checked tools, atomic workspace files, protected managed files, explicit overwrite, traversal/symlink rejection, and correlated redacted Trace events;
- a model-free fixed-solution workflow that persists problem, source, submission states, verdict, feedback, State, and Trace without executing code locally;
- versioned protocol example fixtures that label agreed cores, draft details, unconfirmed error shape, and forward-compatibility-only values;
- task-artifact ignore rules that ignore only the root `/workspace/`, not `agent/workspace/` source;
- a client-only pytest entry independent of MiniOJ server fixtures;
- 55 client tests using fixtures, fakes, and `httpx.MockTransport` only.
- one real fixed-solution MiniOJ chain: `t1001` submission `sub_FhqFt4PN68kPAJ7N` reached `FINISHED / AC`, passed 5/5 tests, and made zero LLM calls.

This completes Phase 1 but does not verify any model provider. Phase 2 will complete the model layer. Current loop code is retained for reuse but is not accepted as Phase 3/4 merely because the fixed-solution chain succeeds.

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
uv run codeharness-agent --help
uv run codeharness-oj-client --help
uv run codeharness-report --help
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

Provide the OJ endpoint through `.env` for real runs; choose provider and model IDs before later model integration. Do not commit `.env`; it is ignored. `config/models.example.yaml` contains placeholders, not confirmed models or prices.

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

The command does not compile or run the source locally. It uses MiniOJ HTTP only. It never automatically retries a request; a formal-submission timeout is recorded as `result_unknown` to prevent blind duplicate POSTs.

## Later-phase candidate commands

```bash
uv run codeharness-agent code-only <problem-id> --profile standard
uv run codeharness-agent harness-loop <problem-id> --max-attempts 4
uv run codeharness-report workspace
```

These entry points are installed and their parser/runtime foundations load. The first two require real model configuration and implement later-phase candidate paths. Do not treat their current escalation, pricing, budgets, or loop behavior as a confirmed experiment design.

## Repository structure

```text
agent/                       # installable client runtime
  config.py                  # client-only environment settings
  core/                      # candidate single-agent loop/context/policy
  models/                    # provider-independent types and routing
  oj_client/                 # HTTP client and CodeHarness-owned wire types
  tools/                     # tool runtime
  workspace/                 # task state, files, and trace source
experiments/                 # installable reporting code
client_tests/                # independent Phase 0/1 client tests
config/                      # non-secret placeholder model mapping
docs/architecture.md         # boundaries and recorded decisions
TODO.md                      # phases, evidence, and unresolved decisions
```

Protocol examples live in `client_tests/fixtures/protocol/`. They are client fixtures, not a shared Python schema package and not proof that a remote endpoint implements the draft.
