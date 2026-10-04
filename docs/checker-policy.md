# Sample checking policy

New tasks default to `sample_check_v3` / `checker_state_v3`, with `llm_checker: disabled`, `on_unverifiable: stop` and `unverifiable_output: execution_only`. Explicit v1/v2 configurations and old checkpoints keep their frozen policy and original checker prompt; there is no State migration or silent resume upgrade. Checker resolution uses an explicit per-problem configuration, sanitized metadata when advertised, or the allowlisted `checker` field from `GET /api/v1/problems/{id}`. The latter is read separately: rating, tags, statements, solutions and unknown fields never enter the Agent context or Trace through this metadata operation. A failed metadata GET means unknown, not token checking.

| Kind | Verification |
| --- | --- |
| exact | Explicit character comparison; optional explicit line-ending normalization |
| token | Split on whitespace; compare token sequences |
| float | Numeric tokens use explicit absolute and relative tolerances; nonnumeric tokens compare exactly. No default tolerances; nonfinite numbers cannot pass. |
| special | Only an expected-output-independent remote checker can establish correctness. Never compare against the example answer. |
| unknown | No correctness claim, even if the example text happens to match |

2026-10-02 read-only MiniOJ inspection: public metadata `tokens` maps to token; `testlib` maps to special. `lines` and `yesno` remain unknown until their normalization semantics are confirmed. The sanitized problem schema has no checker field. `/api/v1/runs` accepts source/stdin only and returns execution status, stdout and resources; **OK is not a checker verdict**. No public sample judge endpoint is advertised. The admin checker-source endpoint is outside the client boundary and is not accessed.

## v3 trusted information and execution-only fallback

| Public sample evidence | Policy/result | Action |
| --- | --- | --- |
| exact/token/float with both explicit tolerances; future attested remote checker | semantic_check; sample_pass / sample_wrong_answer | Pass proceeds to formal; semantic failure enters DEBUG |
| special/unknown/incomplete float, without a verified semantic checker; Custom Run OK and integer exit_code=0 | execution_only; execution_pass_output_unverifiable; sample_check_status=output_unverifiable; passed=null | Continue all public samples, then formal submission |
| CE/RE/TLE/MLE/OLE or nonzero exit | sample_execution_failure; execution_status=execution_failed | DEBUG reason=sample_execution_failure |
| IE, HTTP/protocol error, unknown status, missing/invalid exit code in execution-only mode | Unknown/infrastructure failure | Stop; no formal, DEBUG or automatic retry |

Execution-only trusts execution status/exit/resources, never stdout equality or special-output validity. Missing or truncated stdout cannot change a known execution pass into semantic WA; explicit OLE is still execution failure. A truncated trusted semantic check cannot prove a pass and stops safely. `output_unverifiable` is neither sample pass nor sample fail/recovery. A later official WA enters DEBUG with `formal_verdict_WA`; AC enters the existing local read-only REVIEW. Main's three Harness conditions use exactly the same trust-based policy regardless of CODE model. code-only still makes one CODE call, at most one formal POST, and no samples or feedback repair.

v3 never invokes LLM checker generation from TEST, including an explicitly diagnostic `advisory` setting. Main requires disabled. A generated checker cannot reject/authorize a candidate, trigger DEBUG or block formal under v3. `submit_on_pass` and generic `on_unverifiable: submit` are rejected by v3: formal fallback is available only for a committed known execution pass. Unknown remote intents are journaled before Custom Run and are not reissued on resume. A known saved execution pass resumes without repeating the run; diagnostics cannot override its evidence.

Ordered Trace derives `sample_semantic_verified_count`, `sample_output_unverifiable_count`, `sample_execution_failure_count`, and `formal_submit_after_unverifiable_sample_count`. Formal WA→DEBUG→new candidate→AC→matched REVIEW is formal recovery even when preceding samples were output_unverifiable; sample semantic recovery remains false. Public execution failure→later repaired AC is separately `sample_execution_recovery`, with recovery_type=sample_execution_failure. Old v1/v2 detail stays unknown; code-only's four sample counts are known zero. The existing candidate/call/submission/REVIEW association rules remain.

## Experimental checker and frozen v1/v2 behavior

The earlier user-selected v1/v2 policy defaults to `llm_checker: submit_on_pass` and `on_unverifiable: stop`. Its implementation remains available experimentally and for frozen checkpoints. During legacy TEST, the same Agent uses the CODE Profile with `purpose=sample_checker_generation`, independently of the contestant candidate. It saves the checker source/hash/call identity, sends it only to MiniOJ Custom Run, sanity-checks the public reference output and then checks the candidate output. The checker is reused within that task; no local compiler/executor or new Agent phase is introduced.

A sanity pass and `{"valid":true}` permit formal submission under `submit_on_pass`, labeled `pass_unverified`. The sample still has `passed: null`, `status: sample_check_unverifiable` and separate `llm_generated_unverified` evidence. Candidate false is `rejected_unverified` / terminal `checker_rejected_unverified`. Generation/invalid source, CE/RE/TLE/IE, protocol/malformed output and reference rejection become `checker_generation_failed`, `checker_execution_failed`, `checker_result_unknown` or `checker_sanity_failed`. They **do not enter DEBUG or create candidate WA**. Under the default frozen stop policy they do not submit; an already explicit `on_unverifiable: submit` remains independently honored. Sanity is not checker certification. Official verdicts remain authoritative; no generated checker rejection is algorithm-recovery evidence. The model call uses the task's existing cost/call/time reservations; checker Custom Runs are counted separately from contestant runs as well as in the total.

For legacy v1/v2, `disabled` avoids generated checkers; `advisory` only records the observation. An explicit `on_unverifiable: submit` allows formal evaluation without a trusted sample conclusion, irrespective of the advisory decision. Both settings stay frozen. The supplied checker prompt is unchanged in this v3 work and requests `valid:null` when float tolerance is absent, rather than inventing tolerances. All formal POST, budget and feedback-projection guards remain.

Agent CLI and Dashboard accept a server-side `--sample-config config/checker.example.yaml`; Experiment YAML nests the same object under `sample_checking`. Contest CLI can explicitly freeze `--llm-checker disabled|advisory|submit_on_pass` and `--sample-unverifiable stop|submit`. Browser launch requests cannot provide arbitrary checker code/paths. Resume cannot change these settings.

Old checkpoints retain `whitespace_tokens` semantics. New tasks cannot silently inherit that historical assumption. RemoteChecker is an adapter boundary; current MiniOJ has no verified public endpoint to wire to it. It never compiles a checker or runs contestant code locally.

## Independent dimensions and journal

`checker_kind` remains exact/token/float/special/unknown. `checker_source` is problem_metadata/explicit_config/remote_judge/llm_generated; the original source path is retained separately in CheckerSpec. `checker_verification` is client_verified/remote_verified/llm_generated_unverified/unverifiable. A successfully executed LLM checker never changes verification class.

The v2 checker-only system/user prompt (`llm_checker_prompt_v2`, `length_prefixed_valid_json_v2`) requests complete source only, with no fences/explanation, reusing the existing C++ extractor for compatibility. It includes the sanitized public statement, input/output formats, limits, notes and samples; no contestant source. Validation uses input, candidate output and public constraints, accepts multiple constructions, forbids sample equality/hardcoded answers/hidden tests/admin/official solutions/files/network and returns unknown when an answer oracle would be needed. PLAN/CODE/DEBUG prompts and parsing remain unchanged.

`GeneratedCheckerSession` is shared by TEST and checker-only smoke. Task-local identity binds task/problem, frozen problem SHA, actual checker message/version hash, provider/model/Profile and contract version. It saves source SHA, generation call/usage metadata, per-reference sanity and linked Custom Run observations. Complete known artifacts/results resume without new requests; incomplete paid generation or remote execution intents fail closed. A missing Trace observation after a known checkpoint commit can be repaired without repeating any request. No artifact is imported into another task or experiment.

Every candidate decision links generation call, checker ID/source SHA, candidate version/SHA and execution ID/Trace run event. Reference rejection stops before candidate judging; reference acceptance is only sanity, never semantic certification. No speculative negative oracle is generated. Checker generation/failure, sanity pass/failure, candidate pass/reject, execution failure and unverified stop are separate Trace-derived metrics. Checker rejection/exception/unknown cannot enter trusted sample reject or algorithm/sample/formal recovery. Genuine contestant Custom Run and official program verdicts retain their existing behavior.

## Bounded live evidence

`codeharness-checker-smoke --smoke-id ID --confirm-model-call --confirm-custom-run` is an explicit checker-only entry, fixed to the eight Main special/unknown problems. Its CODE route comes from the unchanged infrastructure model config. Each problem allows one generation, 1 CNY conservative reservation cap and at most three published-reference Custom Runs; total allocation is 8 CNY, at most 8 generations / 24 Custom Runs, retry zero. It reuses the existing paid-call budget guard and has no formal submission handler or algorithm Harness. An uncertain paid call is not retried. This independent evidence cannot provide pre-generated checkers to Main.

Per-problem PASS requires real generation, remote execution and bounded reference acceptance. FAIL/UNKNOWN are preserved without regeneration. The overall checker Gate has only CHECKER_LIVE_SMOKE_PASSED, CHECKER_LIVE_SMOKE_PASSED_WITH_WARNINGS or CHECKER_LIVE_SMOKE_FAILED. Actual billing remains null without provider evidence. Results and the historical v2 Main binding are recorded in [checker evidence](evidence/20261003-checker-live-smoke.md).

The smoke remains **CHECKER_LIVE_SMOKE_FAILED (1 PASS / 7 FAIL)**. CF2092D remains llm_generated_unverified. This failure is one basis for v3 removing generated checkers from Main critical decisions. No new smoke is run in the v3 revision; see [v3 evidence](evidence/20261003-sample-check-v3.md).
