# 同 task 正式提交去重（2026-10-03）

本轮仅修复 Harness 重复候选再次正式提交。原 Infrastructure Qualification 的 mixed-harness solution-v3/v4 来自两次 DEBUG 调用，实际源码 SHA 相同却创建 395/396；`_submit` 原先只按迭代推进、计数后 POST，没有 task 内源码评测索引。原 15-task Pilot、4-task Qualification、两个提交及历史统计保持只读；不把旧 `INFRA_NOT_READY` 改成成功。新逻辑适用于新冻结任务。

## 语义与恢复

`agent/core/formal_dedup.py` 定义 `task_source_bytes_v1`。评测身份为 task_id + problem_id + 冻结清洗题目的 SHA（包括可见 revision）+ language + 实际 `source_code` UTF-8 bytes SHA-256。只做 byte-identical 判定，不格式化、删空格/注释或 AST normalize；一个 task 的 cache 不影响另一个条件/task。

正式 POST 前在现有 task lock 内，将 action intent 与评测身份原子写进 checkpoint；已知 ID/终态/反馈随后分别保存。完整缓存命中不 POST、不轮询、不反馈 GET；复用原 ID/结构化观察，标明原候选/模型调用与当前候选关联，保存 `FORMAL_RESULT_REUSED` 及 `artifacts/formal-reuses/<version>.json`。这不是一个新的 Submission/JUDGE_RESULT。已知未完成 ID 可恢复 GET，未知 POST intent 永不补发，损坏身份/策略/关联停止。复用 ledger 先写 checkpoint，再幂等补 Trace，避免中断后 duplicate counter 重增。

`cached_observation` 重新执行冻结 `formal_verdict_only_v1` 投影，即使本地缓存更丰富，DEBUG/Trace/复用工件也只能看到正式 ID/status/verdict。当前实际 full、effective verdict_only 保持。原 `_failure` 收到同样 verdict-only feedback，LLM/CODE/DEBUG/history 与现有预算、3 次 DEBUG 重 PLAN 机制照常；没有重复次数早停、升级或 Prompt 改写。Custom Run 独立照常，不填充正式缓存。

State 的 `duplicate_candidate_count` 和 Trace 派生的 duplicate/reuse 指标纳入原报告 audit/JSON/CSV 汇总。原 SUBMISSION/TOOL_CALL 统计只认真实正式动作。缓存观察不伪造新的算法 recovery；新成功提交仍按原因果链判定。

## 本地验证

- 针对性 **304 passed**，完整 **629 passed**（原 593 + 新 36）。既有测试未删除/弱化断言；原先用相同源码期待多个正式结果的 fixture 改为不同提交字节。证据：[targeted-tests.log](../../workspace/.formal-dedup/dedup-20261002/targeted-tests.log)、[full-tests.log](../../workspace/.formal-dedup/dedup-20261002/full-tests.log)。
- Case A–G：A→A 一次 POST；A→B→A 两次；跨 task 各自提交；Custom Run 不阻止首次正式提交；reload/resume 不增加正式请求；rich cache 重新投影；LLM/候选/DEBUG 与正式/duplicate 统计一致。
- 另测 exact UTF-8/task/problem/revision/language 身份、未知 intent、已知 ID 重入、确认终态重入/AC REVIEW、并发 task lock、Trace 写入前后中断幂等恢复、损坏 cache/Trace 关联拒绝、旧反馈 full 路径及冻结 dedup version 漂移。测试入口：[test_formal_dedup.py](../../client_tests/test_formal_dedup.py)。
- 独立进程隔离 smoke 使用 Fake Router + `httpx.MockTransport`，不编译/执行 contestant。首次 A：fixture LLM 2、候选 1、formal POST 1、WA，随后中断；新进程生成 A：总 fixture LLM 3、候选 2、formal 1、duplicate 1、复用 fixture-1 的 WA，新增 OJ 请求 **0**；第三进程恢复完成任务：新增请求/模型调用 **0**。见 [首次](../../workspace/.formal-dedup/dedup-20261002/smoke-first.json)、[重启](../../workspace/.formal-dedup/dedup-20261002/smoke-resume.json)、[完成后恢复](../../workspace/.formal-dedup/dedup-20261002/smoke-completed-resume.json)。fixture 使用配置估价不算真实费用；本轮真实 LLM/Custom Run/formal 均 0、费用 0 CNY。
- 编译/JS syntax、wheel/sdist、发行包内容、diff、秘密扫描、原测试断言保留及 434 项保护 hash 的最终结果见 [checks.json](../../workspace/.formal-dedup/dedup-20261002/checks.json)。初次完整测试发现的两处旧 fixture 和 smoke 反馈路径 fixture 的失败记录保留在新 dedup 审计目录，修正后验证通过。

## 独立提交与新冻结 Gate

修复以独立 commit 提交；commit 后重新生成 `pilot-v2-preflight-20261003-dedup` 六份审计文件，只发公开 MiniOJ/provider metadata GET，不执行 15-task Pilot-v2 或主实验。Git SHA、clean status、新 runtime hash、显式 `formal_submission_dedup_policy`、fingerprint 与最终 `READY_FOR_PILOT_V2` / `NOT_READY` 以独立 [readiness.json](../../workspace/.formal-dedup/dedup-20261002/readiness.json) / [可读报告](../../workspace/.formal-dedup/dedup-20261002/readiness.md) 为准，避免提交 SHA 自引用。旧 IQ fingerprint `89441c6a233f0f3fbc2cc09dc1c9010083861e5a243c9bb764c6fe8908b512f5` 不适用于新源码。

保持原配置：provider connect/read/write/pool inactivity timeout 600s、overall deadline null、retry 0；standard/strong 本地输入预留 65536、请求 max output 8192；qwen3.8-flash/qwen3.7-plus、生成参数、Prompt/checker/feedback、3 题 × 原五组/seed=20261002/order 都不调整。供应商有效长度/价格未验证，temperature/top_p 未指定（null）、历史账单未知（null）仍是 warnings。准备 Gate 不构成 Pilot-v2/主实验执行授权。
