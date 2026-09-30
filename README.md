# CodeHarness

[English](README.md) | [简体中文](README_zh.md)

CodeHarness is an experimental Coding Agent Harness built to measure two effects separately:

1. gains from stronger language models;
2. gains from an agent harness, judge feedback, test generation, and iterative control loop.

The repository is developed in runnable phases. **Phase 1 is implemented now:** a FastAPI/SQLite Online Judge foundation with real users, browser sessions, an administrator interface, and problem CRUD. Judge execution, machine APIs, and the agent are explicitly tracked in [TODO.md](TODO.md), not represented by misleading stubs.

## Architecture

```text
MacBook                                     WSL Ubuntu
CodingAgent → Tools → typed OJ Client ─────► FastAPI OJ Server
   │                                             │
Model policy → router → provider              Worker       (Phase 2)
                                                 │
                                            Docker sandbox (Phase 2)
```

The Mac runs the future agent, provider calls, workspaces, and experiments. WSL Ubuntu runs the OJ web/API process, worker, and Docker sandbox. The agent will use only HTTP OJ operations and will not know how WSL, Docker, compilers, or judge files work. See [docs/architecture.md](docs/architecture.md) for the adopted boundaries.

## Current capabilities (Phase 1)

- Registration, login, POST-only logout, profile editing, and password changes.
- Argon2id password hashes; signed HTTP-only browser sessions; CSRF protection on state-changing forms.
- Public problem list/detail pages.
- Admin problem create/edit/delete and user role/activation management.
- Configurable SQLAlchemy database and commands to initialize it or create an admin.
- Responsive server-rendered Jinja UI with no frontend framework.

API tokens, submissions, custom runs, testcases, and judging are not implemented until Phases 2-3. Accordingly, no untrusted program is executed by this version.

## Requirements

- Python 3.9+
- [`uv`](https://docs.astral.sh/uv/) (recommended), or a Python virtual environment with `pip`
- WSL Ubuntu is the intended deployment host for the OJ. Docker is required starting in Phase 2, not for Phase 1.

## Quick start

From the repository root:

```bash
uv sync --extra dev
cp .env.example .env
```

Replace `SECRET_KEY` in `.env`, then export the file for the current shell:

```bash
set -a
source .env
set +a
uv run codeharness-admin init-db
uv run codeharness-admin create-admin --username admin --email admin@example.com
uv run codeharness-oj
```

Open <http://127.0.0.1:8000>. The server listens on `0.0.0.0:8000`, so the Mac can use the WSL host address if the network/firewall permits it.

The server refuses to start without `SECRET_KEY`. Use a long random value and enable `SESSION_HTTPS_ONLY=true` behind HTTPS.

## Database and admin operations

The default database is `data/codeharness.db`. Override it with any SQLAlchemy URL in `DATABASE_URL`.

Initialize the schema:

```bash
uv run codeharness-admin init-db
```

Create an admin (the password is prompted without echo and never accepted as a command-line argument):

```bash
uv run codeharness-admin create-admin \
  --username admin \
  --email admin@example.com
```

Public registration always creates a `user`; it cannot grant admin privileges.

## Creating problems

1. Log in as an admin.
2. Open `/admin` → **Problems** → **New problem**.
3. Enter a stable lowercase problem ID, statement, limits, optional source metadata, rating, and comma-separated tags.

Problem IDs are immutable after creation. The OJ stores no Codeforces-specific assumptions and performs no automatic import.

## Testcase upload and Judge Worker

These begin in Phase 2 and deliberately have no command in Phase 1. The adopted design is:

- testcase metadata in SQLite and bodies under `data/problems/<problem-id>/tests/`;
- a separate polling worker claiming `QUEUED` submissions;
- C++20 compilation and execution only inside a restricted Docker sandbox;
- stable `QUEUED → COMPILING → RUNNING → FINISHED` states and canonical verdict enums.

README commands for testcase ingestion and `worker` startup will be added with that runnable phase.

## Agent and Bailian configuration

The Mac-side agent begins in Phase 4. `.env.example` already names the boundary values (`OJ_BASE_URL`, `OJ_API_TOKEN`, `BAILIAN_API_KEY`, and `BAILIAN_BASE_URL`) without secrets. Concrete model names will live in a non-secret model-profile configuration, never in agent loop code.

`code-only` and `harness-loop` are Phase 5 experiment commands. They are intentionally unavailable now:

- `code-only`: one model response, one submission, no feedback or retry;
- `harness-loop`: PLAN/CODE/TEST/DEBUG/REVIEW with judge feedback, retry, and model routing.

## Tests

```bash
uv run pytest
```

The test suite uses a separate temporary SQLite database and covers authentication, settings, authorization, admin user management, and problem CRUD.

## Repository structure

```text
CodeHarness/
├── agent/                 # Phase 4-5 (empty until implementation)
├── config/                # model/runtime profiles when introduced
├── docs/
│   └── architecture.md
├── experiments/           # Phase 5 experiment definitions
├── oj/
│   └── server/            # Phase 1 FastAPI app, models, routes, templates
├── shared/                # stable cross-process types when needed
├── tests/
├── .env.example
├── README.md
├── README_zh.md
├── TODO.md
└── pyproject.toml
```

## Development principles

Keep the judge, agent, model provider, and web/API boundaries explicit. Add infrastructure only when the current phase needs it. Do not run user code in the web process; do not expose hidden-test or provider internals through convenience shortcuts.
