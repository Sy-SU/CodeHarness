# Agent client package

`agent` is the installable macOS CodeHarness runtime. It owns configuration, model adapters and logical profiles, the single-agent candidate loop, the HTTP-only MiniOJ client, tool execution, per-task workspace, State, and Trace.

It must not import `oj` or `shared`, access MiniOJ storage/testcases, or execute submitted programs locally. Phase 0 verifies the package boundary. Phase 1 adds typed HTTP responses, classified safe errors, OJClient polling, schema-checked tools, guarded workspace files, correlated/redacted trace records, and a fixed-solution HTTP workflow. Its real MiniOJ evidence is a separately recorded `t1001` AC submission; provider and loop semantics belong to later phases.

Use the root [README](../README.md) for setup and [architecture.md](../docs/architecture.md) for status boundaries.
