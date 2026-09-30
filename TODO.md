# CodeHarness Roadmap

Checked items are implemented and verified in the current repository.

## Phase 1 — OJ foundation

- [x] Create the FastAPI application factory and configurable SQLite connection.
- [x] Add SQLAlchemy `User` and `Problem` models with stable role values.
- [x] Add Argon2 password hashing, signed browser sessions, and CSRF protection.
- [x] Implement register, login, logout, profile, and password settings.
- [x] Implement public home, problem list, and problem detail pages.
- [x] Implement admin problem create/edit/delete pages.
- [x] Implement admin user list, role changes, and activation changes with self-lockout protection.
- [x] Add database/admin CLI commands and server-rendered UI styling.
- [x] Add integration tests for authentication, authorization, problem CRUD, and user management.

## Phase 2 — Judge

- [ ] Add testcase metadata and filesystem-backed sample/hidden/generated data.
- [ ] Add submission models with canonical status and verdict enums.
- [ ] Add a polling worker with atomic job claiming and crash recovery.
- [ ] Add C++20 compile/run/check pipeline.
- [ ] Add Docker sandbox restrictions: no network, non-root, memory/CPU/PID/time/output limits, isolated mounts.
- [ ] Add submission list/detail pages and sample/full submission flows.
- [ ] Distinguish infrastructure errors (`IE`) from submitted-program errors (`RE`).

## Phase 3 — Agent-friendly API

- [ ] Add hashed, one-time-display API tokens and settings UI.
- [ ] Add bearer authentication independent of browser sessions.
- [ ] Add `/api/v1` problem, submission, token, and custom-run endpoints.
- [ ] Add sanitized agent problem endpoint.
- [ ] Add structured feedback with `full`, `diagnostic`, and `verdict_only` modes.

## Phase 4 — Mac agent foundation

- [ ] Add typed OJ client and small tool registry/runtime.
- [ ] Add model profiles, registry, router, and OpenAI-compatible provider.
- [ ] Add Bailian configuration without concrete model names in agent code.
- [ ] Add per-task workspace, state persistence, and JSONL trace.
- [ ] Add independent context builder.

## Phase 5 — Experiments

- [ ] Add one-shot `code-only` mode.
- [ ] Add single-agent `harness-loop` state machine.
- [ ] Add independent role-to-profile model policy and escalation.
- [ ] Record calls, tokens, estimated cost, submissions, retries, verdict, and wall time.
- [ ] Reserve the `TEST_GENERATION` role; defer automated stress testing.

