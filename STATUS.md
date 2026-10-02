# CodeHarness 当前状态（2026-10-02）

CodeHarness 是独立单 Agent 客户端；MiniOJ 是独立远程评测服务。当前已实现单题、串行实验、公开比赛题单、Dashboard 发起/观察、预算、安全恢复及独立 Experiment Preflight。最近受限核验完成 2 次极短模型 probe、1 次 token Custom Run；未执行 Pilot 或主实验任务。当前源码行为优先于历史文档。

## 当前能力与冻结行为

- `code-only`：一次 CODE、至多一次正式提交；不跑样例、不消费反馈修复。
- `harness-loop`：固定 PLAN → CODE → TEST → DEBUG / REVIEW → DONE；3 个 DEBUG 候选连续失败重 PLAN；不升级、无模型 REVIEW / Test Generation。
- 新任务 `sample_check_v1`：显式 exact/token/float，float 必须同时提供绝对与相对容差。`testlib` → special；未确认语义保持 unknown，不能用文本不匹配制造 WA。
- 用户已选 LLM 自写 checker：复用 CODE 路由，另标 `purpose=sample_checker_generation`，只在 MiniOJ Custom Run 执行。默认 `submit_on_pass`；参考样例 sanity 通过且认可当前输出才允许正式提交。状态仍为 unverifiable，拒绝/未知停止、不触发 DEBUG。调用/费用/远程执行均单独计数；这不是经认证的 checker。
- 每任务默认 1 CNY、80 次模型、最多 10 次正式 POST，可调至现有边界；保守预留不退、未知 paid call/POST 不重发。code-only 保持一次调用上限。
- 默认期望 verdict_only，必须有服务端声明；full 经 `formal_verdict_only_v1` 仅留下正式 ID/status/verdict。actual/effective/policy 分开记录，native/projected/unknown 不混组。
- checkpoint 冻结配置、候选版本/SHA-256/模型调用/提交关联；旧任务沿用旧策略，不能 resume 偷换 checker 或预算。

## 当前实验与报告

- 原五组不变：standard/strong × code-only/harness，加 mixed-harness（PLAN strong、CODE/DEBUG standard）。
- `config/experiment.small.yaml`：公开 rating 核实的 12 题，1200/1400/1600/1800 各 3 题、每组一次，共 60 个独立任务；仅准备、未运行。整批 60 CNY 是额度分配上限，不是预计费用或执行授权。
- `codeharness-experiment preflight` 独立检查 checker、实际模型路由、反馈、预算、prompt 内容及 Git/配置指纹，保存六份审计文件；0 LLM/Custom Run/正式提交。当前主实验检查为 `READY_WITH_WARNINGS`：token 4、special 5、unknown 3；后 8 题预计使用未认证 LLM checker，不计为 verified checker。
- 主实验与 3 题 × 五组的 `config/experiment.pilot.yaml` 均要求冻结 preflight 后才可启动。seed=20261002，按题分块、SHA-256 排序并轮换 condition 位置；启动/resume/后续 task 前拒绝内容与 dirty diff 漂移。Pilot 取原题单中 token 的 1400/1600/1800 题，未执行。
- Pilot 运行检查已通过：两模型实测各输入 29 / 输出 1 token，接受当前生成参数；当前 source_code 请求的 token live smoke 与正式反馈只读投影通过。按本轮要求实际扣费未核实，整体 **NOT_READY**；配置单价推算 0.0000919 CNY 不冒充实际账单。新 clean preflight 与独立绑定见 [受限核验证据](docs/evidence/20261002-pilot-readiness.md)，实验配置和历史未改。
- Recovery 从有序 Trace 派生：first_try_ac、sample/formal recovery、DEBUG/重 PLAN/无法验证等计数；失败→后续候选→正式 AC 的关联必须可核查。正式 recovery 另要求 DEBUG 与只读 REVIEW。基础设施错误、LLM checker 拒绝均不计算法修复。
- 官方 Performance 直接读取 `GET /api/v1/contests/{id}/standings#rows[].performance`；`me.id` 与冻结的 `rows[].user_id` 唯一匹配。无计算公式、无 per-task 值。该值属于账户整场比赛，可能包含其他运行。
- 显式刷新榜单只发 GET，保存独立 `performance.json`、读时合并；不重写 report/State/Trace、不触发 Agent。普通 Dashboard GET/轮询只读本地；`--read-only` 无刷新控制端点。
- 模型快照区分 configured / verified。standard=qwen3.8-flash、strong=qwen3.7-plus 已经 GET /models 确认可见；8192 输出、32768 输入预留边界、价格及生成参数仍是配置。只读 metadata 快照的 verified 输出/context/输入上限与价格均 null，needs_live_probe=true；最小连通性 probe 及官方文档另存独立证据，完整长度未实测。未指定 temperature/top_p 保持 null。

当前 Preflight 验证与审计入口见 [本轮证据](docs/evidence/20261002-preflight.md)，此前可信性里程碑见 [验证](docs/evidence/20261002-trust.md)；历史比赛 1 为 1/2 AC，T1003 失败和旧反馈 unknown 均保留。

## 剩余问题

真实 LLM checker 的可靠性、正式失败→DEBUG→AC/REVIEW 自然修复链、60 任务同条件比较、供应商价格/有效上限及 native verdict_only 对照尚未验证。当前没有可核实的公开远程样例 judge API；不访问 admin checker/testcase。详细未来工作见 [TODO](TODO.md)。

设计入口：[架构](docs/architecture.md)、[checker](docs/checker-policy.md)、[反馈](docs/feedback-policy.md)、[实验](docs/experiment-protocol.md)、[Dashboard](docs/dashboard.md)。Phase 0–5/D0–D7 全部原文证据见 [历史](docs/history/README.md)。
