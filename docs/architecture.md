# CodeHarness 当前架构

当前状态见 [STATUS](../STATUS.md)，未来项见 [TODO](../TODO.md)。本文描述当前源码；Phase 0–5 与旧架构全文见 [历史](history/README.md)，历史结论不替代当前证据。

## 独立边界

CodeHarness 在 macOS 管理单 Agent、模型路由、工具、Workspace、Trace、实验和可选 Dashboard。MiniOJ 是远程服务：题面、Custom Run、正式评测仅通过 Bearer HTTP。客户端不 import MiniOJ、共享数据库/testcase、读 admin checker、远程 shell 绕过 API，也不在本地编译或执行 contestant/checker 源码。Web 依赖仅在 dashboard extra。

## 实际执行路径

```text
Agent CLI ───────────────────────────────────────→ CodingAgent
Experiment CLI → ExperimentRunner ─┐
Contest CLI → ContestRunner → ExperimentRunner ──┼→ ExecutionService → CodingAgent
Dashboard launch → bounded serial queue ────────┘
CodingAgent → ModelPolicy → ModelCallRuntime → Router → Registry → Provider
CodingAgent → ToolRuntime → OJClient → MiniOJ HTTP
                    └────→ TaskWorkspace / State / Trace
Dashboard GET → bounded local readers → State / Trace / reports
Performance refresh → standalone OJClient GET → additive performance.json
```

`code-only` 一次 CODE、至多一次正式 POST，无样例/反馈修复。Harness 固定 PLAN/CODE/TEST/DEBUG/REVIEW/DONE：可信样例程序失败或正式程序 verdict 进入 DEBUG；3 个 DEBUG 候选连续失败重 PLAN；正式 AC 后本地只读 REVIEW 核对版本/hash。Role 是同一 Agent 的调用策略；不升级、不启用模型 REVIEW / Test Generation。

新 Harness 的 CODE/DEBUG 若 `extract_cpp` 失败，默认最多 3 次额外格式纠正（`HarnessPolicy.max_code_extraction_retries`，可设 0 禁用）。纠正保持 CODE 阶段和原 Role/Profile，将“只返回完整 C++20、无工具/XML”要求加入 system prompt；新候选成功后重置计数。计数与响应缓存失效同一次检查点提交，恢复可复用已知响应，不重置重试额度；调用中断且结果未知仍停止。每次调用经过相同预算/输入/LLM/时间守卫，Trace 保留失败调用与 `CODE_EXTRACTION_RETRY`，用尽次数仍保留 invalid output 终态。code-only、checker 生成及旧冻结任务不升级；历史 Main checker-only receipt 不授权本次算法改变，仍由漂移检查阻止直接复用。

## 样例门禁

`agent/core/checker.py` 定义 CheckerSpec、SampleGatePolicy、Exact/Token/Float/Remote/UnknownChecker。新任务冻结 `sample_check_v3`，优先显式 override，随后清洗元数据或单独 allowlist checker GET。普通元数据 rating/tags/题解不进入 Agent。float 必须同时显式指定绝对/相对容差；special/unknown 不能用参考文本不匹配判 WA。

可信 semantic checker 继续 pass→正式提交、fail→DEBUG。special/unknown/缺容差 float 在没有 verified remote adapter 时改为 execution_only：公开 stdin 经 MiniOJ Custom Run，OK + 整数 exit_code=0 记 execution_pass_output_unverifiable / passed=null，完成全部样例后允许正式提交。正式 WA 的 DEBUG reason 为 formal_verdict_WA，AC 进入原本地只读 REVIEW。CE/RE/TLE/MLE/OLE/非零退出记 sample_execution_failure；未知/IE/通信/协议失败停止，无 DEBUG/提交/重试。新 contestant Custom Run 原子记录意图；结果未知时恢复不重发，已知结果可复用。

Main 默认关闭 LLM checker，三个 Harness condition 共用相同策略；不生成/注入 smoke checker。generated_checker.py 与 GeneratedCheckerSession、独立 checker smoke 保留为 experimental/diagnostic，以及明确 v1/v2 冻结策略的实现；历史正向 LLM gate 行为不升级。所有执行仍仅经过 MiniOJ HTTP，当前没有公开 verified sample judge。见 [checker policy](checker-policy.md)。

Trace 将 sample_semantic_verified_count、sample_output_unverifiable_count、sample_execution_failure_count、formal_submit_after_unverifiable_sample_count 分开；输出不可验证不计 sample pass/fail/recovery。正式修复仍要求 DEBUG/关联 AC/REVIEW，执行失败修复另标 sample_execution_recovery。旧 Workspace/State/Trace 不回写。

## 付费、POST 与恢复

`budget.py` 在请求前按配置价格/input bound/max_tokens 保守预留；新任务冻结 `actual_usage_settlement_v1`。State 的 `pending_model_reservation` 原子保留当前预留与调用身份，ModelCallRuntime 收到可信 usage/费用且未超上限后释放差额，记录 `BUDGET_SETTLEMENT`。`budget_committed_cny` 是已结算配置费用加未知调用的保守预留，单题/批次/比赛均使用此值；Trace audit 核对累计预留减累计释放。失败调用若有可信用量同样结算；未知/中断/超界用量不退预留，不重发。恢复协调 Runtime 的 State 投影与权威检查点中的预算和未决预留，不重复结算；旧冻结检查点缺少版本键时保留原累计预留行为，执行指纹拒绝策略漂移。配置估算不自动包含缓存/优惠/免费额度，仍与实际账单分开。每任务独立默认 1 CNY、80 次 LLM、最多 10 POST；整批显式总 cap。实验性旧策略的 LLM checker 模型/Custom Run 全计入任务；Main v3 checker 调用为 0。code-only 保持一次 CODE。

`TaskWorkspace` 原子写托管文件、不可变候选和检查点，拒绝路径越界/符号链接，Trace 脱敏且记录 task/phase/correlation。checkpoint 权威、State 为投影；锁内恢复重读。已知提交继续 GET；不确定 paid call、formal POST 或 checker POST 停止 result_unknown，禁止盲重发。新策略/model/参数/预算/endpoint 指纹不可通过 resume 偷换。

新 Harness 任务冻结 `task_source_bytes_v1` 正式去重：身份为 task_id、problem_id、冻结清洗题目的 hash（包含可见 revision）、language 及实际提交源码 UTF-8 bytes SHA-256。禁止空白/注释/AST 归一化；只查本 task 的 `checkpoint.formal_evaluations`，Custom Run 不填充。正式 POST 前原子保存 intent，取得 ID 后保存已知关联，确认终态/反馈后保存冻结策略可见结果。命中终态不发正式 POST/GET，记录 `FORMAL_RESULT_REUSED`、原提交来源与当前候选关联，再过反馈投影；源码改变可新提交，跨 task 独立。已知未完成 ID 仅恢复轮询；未知 intent 停止。现有 task lock 保证单 executor；复用 ledger 在 Trace 中断后幂等恢复计数。LLM/候选/DEBUG/历史与现有预算、3 次 DEBUG 重 PLAN 保持原机制，没有重复早停。旧 checkpoint 不自动升级，新实验指纹拒绝旧冻结证据。

## 正式反馈

默认要求服务器明确 full/verdict_only；full 单向满足 verdict_only，通过 ToolRuntime 的 `formal_verdict_only_v1` 在返回/Trace 前仅保留正式 ID/status/verdict。正式错误正文同样过滤；公开 Custom Run 输出保留。actual/effective/policy 各自记录，不混组。详见 [feedback policy](feedback-policy.md)。

## 实验与可核查指标

`experiments/config.py` 冻结策略、题单、预算、样例/反馈策略、公开 rating 来源；`runner.py` 复用执行入口，串行隔离任务；`results.py` 输出 JSON/CSV、Trace audit 和条件分组。配置/价格、未知 usage/费用和 not_started 均如实保留。`prepare.py` 仅离线计划；可加载本地配置快照但不调用模型/OJ。

`agent/core/metrics.py` 只读有序 Trace，核对失败→候选→提交→终态的 version/SHA-256/model call/submission 链。first_try_ac 只认首个确认提交 AC；recovery 只认可信 sample/program failure 或正式程序 verdict 后的模型生成候选与最终 AC；正式 recovery 再要求成功 DEBUG 与 REVIEW。IE/HTTP/provider/protocol/unknown/LLM checker 拒绝不给算法修复计数。损坏/计数不完整 Trace 的关键结论为 null。不回写历史 State。

Trace/报告增加 `duplicate_candidate_count` 与 `formal_result_reuse_count`；只由真实 `SUBMISSION`/工具调用统计正式提交/尝试，缓存观察不伪造 `SUBMISSION` 或新的 `JUDGE_RESULT`，不作为重复候选算法修复的证据。

小正式集为 `config/experiment.small.yaml`：1200/1400/1600/1800 各 3 个公开核实题，原四固定组加 mixed，各一次/60 任务；仅准备、不执行。`models/evidence.py` 区分 configured 与 verified，未核实供应商字段为 null；配置 input bound 不代表 context window。详见 [experiment protocol](experiment-protocol.md)。

Main Final Preflight 由现有 `preflight` CLI 增加 `--main-experiment-id` / `--pilot-run-id` 生成，复用 `.experiments/<preflight-id>/preflight/`。`main_final_receipt_v1` 绑定 Git/dirty diff、完整内容指纹、顺序、反馈、历史证据 hash 和独立 Main ID；Runner 在创建批次、resume、后续 task 前验证，缺失/BLOCKED/漂移拒绝且不自动刷新。Pilot 仅提供只读 seen/coverage/冻结比较证据，不导入执行 State。Report 增加 pilot_seen、all/seen/unseen 分组、中间正式 verdict、cached usage 和互斥终态；正式去重/因果关联守卫不改变；v3 样例策略与恢复分类单独版本化。

## 比赛与官方 Performance

`oj_client/contests.py` 只解析公开题单/题目 ID（public_html_v1），同源且异常 fail closed；比赛提交使用已确认 HTTP endpoint，不访问内部数据。`ContestRunner` 复用 ExperimentRunner，冻结题单/contest_id/账户身份/endpoint；简单 AC/总题数与汇总调用、tokens、成本、DEBUG/提交/Custom Run。

`oj_client/standings.py` 直接读取已确认 GET standings 的 `rows[].performance`，以冻结 me.id 与唯一 user_id 匹配，不推公式、不按昵称/行序匹配。值是整场账户成绩，包括其他运行；任务/独立条件不伪造 Performance。缺失/待更新/无身份/歧义/非法值保持 null/status。

`contest.refresh_performance` 与执行服务独立，只读取 me/standings，单独保存 performance.json 并读时合并，不改旧 report/State/Trace。历史未冻结身份的运行保留 identity_unresolved。Dashboard 普通 GET/轮询只读本地，显式刷新经 Origin/CSRF/白名单控制；read-only 无控制端点。详见 [Dashboard](dashboard.md)。

## 模块地图与验证入口

| 代码 | 职责 |
| --- | --- |
| agent/config.py；agent/models/ | 非秘密配置引用、Provider/Registry/Router/响应/调用与配置证据 |
| agent/core/agent.py；harness.py；context.py；policy.py | 两 mode、固定状态机、单题上下文与 Role 路由 |
| agent/core/checker.py；generated_checker.py；metrics.py | 样例结论/启发式 checker、只读因果指标 |
| agent/core/formal_dedup.py | task 内正式评测身份、缓存关联核对、冻结反馈再投影 |
| agent/oj_client/；agent/tools/ | HTTP 解析/轮询、安全投影、参数验证与执行 |
| agent/workspace/；agent/execution.py | State/Trace/锁/原子工件与共享单题执行 |
| experiments/ | 配置、串行实验、比赛、离线准备、JSON/CSV 与 enrichment |
| dashboard/ | 可选 loopback UI、受限读取、确认式本地控制队列 |
| client_tests/ | Fake/MockTransport/ASGI/Node；无自动远程付费请求 |

构建 wheel/sdist、发行源码测试、Python 3.9 编译、JS syntax/diff/secret 检查见 [本轮证据](evidence/20261002-trust.md)。真实联调与替身严格分列；历史工件不修饰结果。
