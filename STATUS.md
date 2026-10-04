# CodeHarness 当前状态（2026-10-03）

2026-10-03 单独授权增加 Harness CODE/DEBUG 输出格式纠正：默认最多 3 次额外重试，支持检查点计数与既有预算保护。完整本地回归 **788 passed**；未调用远程模型或 MiniOJ，也未重跑历史任务。见 [验证记录](docs/evidence/20261003-code-extraction-retries.md)。

CodeHarness 是独立单 Agent 客户端；MiniOJ 是独立远程评测服务。当前已实现单题、串行实验、公开比赛题单、Dashboard、预算、安全恢复、独立 Preflight 和 Main Final Receipt。Pilot-v2 已完成；历史 checker smoke 1 PASS / 7 FAIL、`CHECKER_LIVE_SMOKE_FAILED` 保留。本轮完成 `sample_check_v3` 与新的只读 Main Final Preflight，主实验未启动；0 LLM / Custom Run / formal / 付费。当前源码行为优先于历史文档。

## 当前能力与冻结行为

- `code-only`：一次 CODE、至多一次正式提交；不跑样例、不消费反馈修复。
- `harness-loop`：固定 PLAN → CODE → TEST → DEBUG / REVIEW → DONE；3 个 DEBUG 候选连续失败重 PLAN；不升级、无模型 REVIEW / Test Generation。
- 新任务默认 `sample_check_v3` / `checker_state_v3`（旧配置/checkpoint 不升级）：trusted exact/token/双显式容差 float 保持 semantic_check，pass→formal、mismatch→DEBUG。
- special/unknown/缺容差 float 无 verified checker 时采用 `execution_only`：公开 stdin 经 MiniOJ Custom Run；OK + 整数 exit_code=0 记 `output_unverifiable` / `passed=null`，完成全部样例后允许正式提交。stdout 不匹配不制造 WA；正式 WA→DEBUG reason=`formal_verdict_WA`，AC→原本地 REVIEW。CE/RE/TLE/MLE/OLE/非零退出 reason=`sample_execution_failure`；未知/IE/HTTP/协议失败停止、无 DEBUG/提交/重试。
- Main 关闭 LLM checker，三个 Harness condition 策略一致；仅 PLAN/CODE/DEBUG 调用。旧 v1/v2 checker generation、独立 smoke、诊断实现保留为 experimental；未认证 checker 不进入 v3 的放行/拒绝/修复路径，不复用 smoke artifact。
- 每任务默认 1 CNY、80 次模型、最多 10 次正式 POST，可调至现有边界；保守预留不退、未知 paid call/POST 不重发。code-only 保持一次调用上限。
- 默认期望 verdict_only，必须有服务端声明；full 经 `formal_verdict_only_v1` 仅留下正式 ID/status/verdict。actual/effective/policy 分开记录，native/projected/unknown 不混组。
- checkpoint 冻结配置、候选版本/SHA-256/模型调用/提交关联；旧任务沿用旧策略，不能 resume 偷换 checker 或预算。
- 新 Harness 任务冻结 `task_source_bytes_v1`：task/problem/冻结题目身份/language/实际提交 UTF-8 源码 SHA 全一致时复用原正式结果及 ID，再过冻结反馈投影。LLM、候选和 DEBUG 正常计数，duplicate 单独计数，正式 POST/轮询/反馈 GET 不重复。checkpoint 持久化 action intent/结果与复用记录，锁内 resume；未知 POST 保持停止。Custom Run 独立，不跨 task 去重，不改重 PLAN 或预算策略。

## 当前实验与报告

- 原五组不变：standard/strong × code-only/harness，加 mixed-harness（PLAN strong、CODE/DEBUG standard）。
- `config/experiment.small.yaml`：公开 rating 核实的 12 题，1200/1400/1600/1800 各 3 题、每组一次，共 60 个独立任务；仅准备、未运行。整批 60 CNY 是额度分配上限，不是预计费用或执行授权。
- `codeharness-experiment preflight` 只读检查 checker、实际模型路由、反馈、预算、prompt 与 Git/配置指纹。Main Final Preflight 增加 final receipt/freeze diff/checker matrix/budget summary，0 LLM/执行/提交。旧 `main-v1-preflight-20261003-final` 和 `main-v1-preflight-20261003-checker-v2` 对当前源码均 stale，原件保留。新 ID 为 `main-v1-preflight-20261003-sample-v3`，未来 Main 为 `main-v1-20261003-sample-v3`；最终 Gate/Git/指纹以 [新 receipt](workspace/.experiments/main-v1-preflight-20261003-sample-v3/preflight/final-receipt.json) 为准。4 token 使用 semantic_check；5 special + 3 unknown 使用 execution_only，requires_llm_checker=0。
- 主实验与 3 题 × 五组的 Pilot 均要求冻结 preflight 后才可启动。seed=20261002，按题分块、SHA-256 排序并轮换 condition 位置；启动/resume/后续 task 前拒绝内容与 dirty diff 漂移。原 Pilot 取 token 的 1400/1600/1800 题：15 planned、9 completed、6 failed_infrastructure；19 LLM、8 Custom Run、7 正式提交（AC 5 / WA 1 / CE 1）。原 SHA `65cc58c1e2e2acc6337c36b8dffdac10414b27bd` / fingerprint `2769cca5b6582b16401216ac69d82d97849c9514a744ef3aa10a7056c1f3d8a7` 只对应历史运行，结果见 `workspace/.experiments/pilot-live-20261002/pilot-validation.json`。
- 原 Infrastructure Qualification `INFRA_NOT_READY`（相同源码提交 395/396）与 dedup 本地验证保留为历史证据。真实 `pilot-v2-live-20261003` 为 `PILOT_V2_VALIDATED_WITH_WARNINGS`、blockers=0：15/15 terminal、0 基础设施失败、37 LLM、25 Custom Run、11 正式提交、7 final AC；另有 6 invalid output、1 WA、1 budget exhausted。自然 TLE→DEBUG→AC→REVIEW 与 completed resume 零新增远程动作通过；未出现自然 cache hit。见 [Pilot-v2 receipt](workspace/.pilot-v2-validation/pilot-v2-live-20261003/final-receipt.json)。
- Main 保留原 12 题 × 五组 × 1 次，此前用户授权将 `model_config` 引用切换为 `models.infrastructure.yaml`；本轮另授权 Main v3/关闭 LLM checker/明确 execution-only fallback。600s 各阶段 inactivity timeout、overall deadline=null、retry=0、输入预留 65536、输出请求 8192；未根据 Pilot 结果调参。CF2127C/CF2125D/CF2127D 标记 pilot_seen（3 题/15 tasks），其余 9 题/45 tasks；新 task 独立执行，不导入 Pilot 结果/响应/候选/去重缓存。
- 60-task Runner 在创建批次、resume 和后续 task 前要求匹配 final receipt/current fingerprint；BLOCKED、缺失或漂移均拒绝，不自动重做 Preflight。报告保留中间正式 verdict 和互斥终态、cached tokens（缺失 null），支持 all/seen/unseen 描述分组。见 [v3 本轮证据](docs/evidence/20261003-sample-check-v3.md)；[原 Main 证据](docs/evidence/20261003-main-final-preflight.md) 保留。
- Checker smoke 为独立 CODE/standard 路由，每题一次生成，8 LLM / 3 Custom Run / 0 formal，input=10171/output=15253/cached=0；配置估价 0.0493199 CNY、provider/billed=null。5 题只返回 JSON；CF2111D RE、CF2103C length→CE；仅 CF2092D reference sanity PASS，仍未认证。禁止重生成或把 smoke artifact 注入 Main；completed resume 零新增动作。见 [checker 证据](docs/evidence/20261003-checker-live-smoke.md)。
- v3 增加 semantic verified / output unverifiable / execution failure / formal-after-unverifiable 四项计数；output_unverifiable 不计样例 pass/fail/recovery，执行失败修复另标 sample_execution_recovery。Recovery 从有序 Trace 派生：first_try_ac、sample/formal recovery、DEBUG/重 PLAN/无法验证等计数；失败→后续候选→正式 AC 的关联必须可核查。正式 recovery 另要求 DEBUG 与只读 REVIEW。基础设施错误、LLM checker 拒绝均不计算法修复。
- 官方 Performance 直接读取 `GET /api/v1/contests/{id}/standings#rows[].performance`；`me.id` 与冻结的 `rows[].user_id` 唯一匹配。无计算公式、无 per-task 值。该值属于账户整场比赛，可能包含其他运行。
- 显式刷新榜单只发 GET，保存独立 `performance.json`、读时合并；不重写 report/State/Trace、不触发 Agent。普通 Dashboard GET/轮询只读本地；`--read-only` 无刷新控制端点。
- 模型快照区分 configured / verified。standard=qwen3.8-flash、strong=qwen3.7-plus；8192 是请求输出上限，32768（原）/65536（新）是本地保守输入及费用预留边界，不是 model context window。metadata 的 verified 输出/context/输入上限与价格仍未确认；完整长度未实测。未指定 temperature/top_p 保持 null。账单未知保留 null；配置单价估算与实际扣费分别报告，不因账单缺失否定基础设施 qualification。

当前 Preflight 验证与审计入口见 [本轮证据](docs/evidence/20261002-preflight.md)，此前可信性里程碑见 [验证](docs/evidence/20261002-trust.md)；历史比赛 1 为 1/2 AC，T1003 失败和旧反馈 unknown 均保留。

## 剩余问题

真实 LLM checker 已有一次有界运行证据但 7 题失败，仍为独立研究问题；失败是将其移出 Main 关键路径的依据之一。v3 自然 execution-only→正式 verdict 修复链尚未 live 验证，本轮只做替身回归与只读 preflight。60 任务比较、供应商价格/有效上限及 native verdict_only 对照仍未验证。Pilot-v2 的自然正式 recovery 已有证据；不将 token 覆盖或配置估价升级为 checker 认证/供应商上限/账单。当前无可核实的公开远程样例 judge API，不访问 admin checker/testcase。后续见 [TODO](TODO.md)。

设计入口：[架构](docs/architecture.md)、[checker](docs/checker-policy.md)、[反馈](docs/feedback-policy.md)、[实验](docs/experiment-protocol.md)、[Dashboard](docs/dashboard.md)。Phase 0–5/D0–D7 全部原文证据见 [历史](docs/history/README.md)。
