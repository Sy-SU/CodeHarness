# Formal feedback policy

The default expectation is `verdict_only` with a mandatory explicit server declaration from `GET /api/v1/me#feedback_mode`. Explicit full satisfies verdict_only through `formal_verdict_only_v1`; actual remains full, effective is verdict_only. Native verdict_only, projected full and unknown are separate experiment conditions. Unknown stops before model calls. Explicit full requires full.

ToolRuntime projects formal submit/query/wait/feedback results **before** returning them or writing Trace. Only recognized ID/status/verdict survive; summaries, diagnostics, hidden testcase data and arbitrary extension fields are removed. Formal error messages are also restricted while preserving HTTP classification and unknown-POST flags. Public sample Custom Run output remains available for debugging.

Checkpoint/configuration fingerprints freeze this versioned policy. Old jobs are not upgraded or rewritten. Known submission IDs can be queried; uncertain paid calls or creation POSTs are never blindly reissued. Leaderboard metadata is report-only and never enters a model prompt or affects feedback projection.
