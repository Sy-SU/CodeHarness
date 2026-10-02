# Sample checking policy

New tasks use `sample_check_v1`. Checker resolution uses an explicit per-problem configuration, sanitized metadata when advertised, or the allowlisted `checker` field from `GET /api/v1/problems/{id}`. The latter is read separately: rating, tags, statements, solutions and unknown fields never enter the Agent context or Trace through this metadata operation. A failed metadata GET means unknown, not token checking.

| Kind | Verification |
| --- | --- |
| exact | Explicit character comparison; optional explicit line-ending normalization |
| token | Split on whitespace; compare token sequences |
| float | Numeric tokens use explicit absolute and relative tolerances; nonnumeric tokens compare exactly. No default tolerances; nonfinite numbers cannot pass. |
| special | Only an expected-output-independent remote checker can establish correctness. Never compare against the example answer. |
| unknown | No correctness claim, even if the example text happens to match |

2026-10-02 read-only MiniOJ inspection: public metadata `tokens` maps to token; `testlib` maps to special. `lines` and `yesno` remain unknown until their normalization semantics are confirmed. The sanitized problem schema has no checker field. `/api/v1/runs` accepts source/stdin only and returns execution status, stdout and resources; **OK is not a checker verdict**. No public sample judge endpoint is advertised. The admin checker-source endpoint is outside the client boundary and is not accessed.

Results distinguish `sample_pass`, `sample_wrong_answer`, `sample_program_failure` and `sample_check_unverifiable`; `passed` is null for unverifiable. Only verified wrong answers/program failures enter DEBUG. HTTP/protocol failures, IE and unknown run status terminate safely. Truncated output cannot prove WA.

The user selected an LLM-written checker for unverifiable output. New tasks default to `llm_checker: submit_on_pass` and `on_unverifiable: stop`. During TEST, the same Agent uses the CODE Profile with `purpose=sample_checker_generation`, independently of the contestant candidate. It saves the checker source/hash/call identity, sends it only to MiniOJ Custom Run, sanity-checks the public reference output and then checks the candidate output. The checker is reused within the task; no local compiler/executor or new Agent phase is introduced.

A sanity pass and `{"valid":true}` permit formal submission under `submit_on_pass`. The sample still has `passed: null`, `status: sample_check_unverifiable` and separate `llm_generated_unverified` evidence. `false`, `null`, invalid JSON, checker CE/RE or failed sanity keep unverifiable and **do not enter DEBUG**. Sanity is not checker certification. Official verdicts remain authoritative; no generated checker rejection is algorithm-recovery evidence. The model call uses the task's existing cost/call/time reservations; checker Custom Runs are counted separately from contestant runs as well as in the total.

`disabled` avoids generated checkers; `advisory` only records the observation. An explicit `on_unverifiable: submit` allows formal evaluation without a trusted sample conclusion, irrespective of the advisory decision. Both settings are frozen; defaults reflect the user's positive-only gate decision. The supplied checker prompt requests `valid:null` when float tolerance is absent, rather than inventing tolerances. All formal POST, budget and feedback-projection guards remain.

Agent CLI and Dashboard accept a server-side `--sample-config config/checker.example.yaml`; Experiment YAML nests the same object under `sample_checking`. Contest CLI can explicitly freeze `--llm-checker disabled|advisory|submit_on_pass` and `--sample-unverifiable stop|submit`. Browser launch requests cannot provide arbitrary checker code/paths. Resume cannot change these settings.

Old checkpoints retain `whitespace_tokens` semantics. New tasks cannot silently inherit that historical assumption. RemoteChecker is an adapter boundary; current MiniOJ has no verified public endpoint to wire to it. It never compiles a checker or runs contestant code locally.
