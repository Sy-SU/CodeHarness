# CodeHarness Architecture

## Goal

CodeHarness separates model capability from harness capability so experiments can compare code-only generation with an iterative agent loop under the same Online Judge (OJ). The OJ is model-agnostic; the agent reaches it only through a versioned HTTP client.

```text
MacBook                                      WSL Ubuntu
┌──────────────────────────────┐             ┌──────────────────────────────┐
│ CodingAgent                  │ HTTP/JSON   │ FastAPI OJ Server            │
│  Agent loop / context / state├────────────►│  Web + API + persistence     │
│  Model policy/router/provider│             │              │               │
│  Tools ── OJ Client          │             │         Judge Worker         │
└──────────────────────────────┘             │              │               │
                                             │       Docker Sandbox          │
                                             └──────────────────────────────┘
```

## Boundaries

- **OJ server** owns users, browser sessions, problems, submissions, stable API schemas, and persistence. It never imports an LLM provider.
- **Judge worker** claims queued jobs and turns sandbox results into stable status/verdict data. It does not render pages or authenticate browser sessions.
- **Sandbox** is the only component allowed to compile or execute untrusted submissions. The agent and web process never execute submitted code directly.
- **Agent** owns phases, context, model policy, trace, workspace, and experiments. It sees OJ operations, not Docker, WSL, or shell details.
- **Provider adapters** normalize vendor responses into internal LLM types. Concrete model names remain in configuration; agent policy requests only logical profiles.
- **Shared schemas** are reserved for stable wire-level values used by both sides. They must not become a dumping ground for server internals.

## Repository layout

```text
agent/            # Phase 4-5, Mac-side agent and experiment runtime
oj/server/        # Phase 1, FastAPI web application and database models
oj/worker/        # Phase 2, asynchronous polling judge worker
oj/judge/         # Phase 2, compile/run/check orchestration
oj/sandbox/       # Phase 2, Docker boundary
shared/           # Stable cross-boundary types when introduced
experiments/      # Phase 5, experiment definitions and result analysis
config/           # Non-secret runtime/model profiles
docs/             # Architecture and operator documentation
tests/            # Unit and integration tests
```

Only `oj/server/` is populated in Phase 1. Future packages are added when their phase begins rather than as speculative implementations.

## Phase 1 decisions

- FastAPI serves both server-rendered Jinja pages and later versioned APIs. Web routes are intentionally separate from `/api/v1/`.
- SQLAlchemy 2 models use SQLite in V1. The database URL is configurable, and engine/session construction is application-scoped so tests can use isolated databases.
- Browser authentication uses a signed, HTTP-only session cookie. Passwords are Argon2 hashes. Every state-changing HTML form carries a per-session CSRF token.
- The first registered account is a normal user. Admin creation is an explicit CLI operation; public registration can never choose a role.
- Problem identifiers are stable human-readable strings. Tags use a JSON list in the database and are normalized at the form boundary.
- Schema creation uses `create_all` for V1. Alembic becomes necessary before the first schema migration, not before.

## Data ownership and future flow

Phase 2 adds submission metadata to SQLite and testcase metadata to the database while storing testcase bodies below `data/problems/<problem-id>/tests/`. A worker polls/claims `QUEUED` submissions without introducing a broker. Each execution gets an isolated temporary job directory and a network-disabled, non-root Docker container.

Phase 3 adds API tokens (hashed at rest), agent-safe problem representations, structured feedback modes, and custom runs. Browser sessions and bearer authentication remain separate.

Phase 4-5 add the Mac-side client and single-agent runtime. They do not import OJ database models or sandbox code.

