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

## 样例门禁

`agent/core/checker.py` 定义 CheckerSpec、SampleGatePolicy、Exact/Token/Float/Remote/UnknownChecker。新任务冻结 `sample_check_v1`，优先显式 override，随后清洗元数据或单独 allowlist checker GET。普通元数据 rating/tags/题解不进入 Agent。float 必须同时显式指定绝对/相对容差；special/unknown 不能用参考文本不匹配判 WA。

当前没有已确认的公开远程 sample judge。按用户选择，`generated_checker.py` 让同一 Agent 经 CODE 路由生成独立 C++20 checker，标明调用 purpose；Harness TEST 在 MiniOJ Custom Run 执行参考样例 sanity 与候选输出检查。checker 被复用并有独立 hash/artifact/计数，受同一 LLM/cost/time 守卫约束。默认 `submit_on_pass` 允许正向决定继续正式提交，结果仍 `sample_check_unverifiable`；拒绝/未知停止，不 DEBUG、不算 sample pass/failure 或算法 recovery。此启发式尚未认证，最终 verdict 来自正式 Judge。代码仅远程执行。

明确配置 `on_unverifiable: submit` 可无可信样例结论继续正式评测；默认 stop 配合正向 LLM checker。截断/未知结果不制造 WA；IE/通信/协议失败单独终止。旧 checkpoint 保留原 whitespace 策略，恢复不升级。见 [checker policy](checker-policy.md)。

## 付费、POST 与恢复

`budget.py` 在请求前按配置价格/input bound/max_tokens 保守预留，未知 usage/价格不补零。每任务独立默认 1 CNY、80 次 LLM、最多 10 POST；整批显式总 cap，失败预留不退。LLM checker 增加的模型/Custom Run 全计入任务。code-only 保持一次 CODE。

`TaskWorkspace` 原子写托管文件、不可变候选和检查点，拒绝路径越界/符号链接，Trace 脱敏且记录 task/phase/correlation。checkpoint 权威、State 为投影；锁内恢复重读。已知提交继续 GET；不确定 paid call、formal POST 或 checker POST 停止 result_unknown，禁止盲重发。新策略/model/参数/预算/endpoint 指纹不可通过 resume 偷换。

## 正式反馈

默认要求服务器明确 full/verdict_only；full 单向满足 verdict_only，通过 ToolRuntime 的 `formal_verdict_only_v1` 在返回/Trace 前仅保留正式 ID/status/verdict。正式错误正文同样过滤；公开 Custom Run 输出保留。actual/effective/policy 各自记录，不混组。详见 [feedback policy](feedback-policy.md)。

## 实验与可核查指标

`experiments/config.py` 冻结策略、题单、预算、样例/反馈策略、公开 rating 来源；`runner.py` 复用执行入口，串行隔离任务；`results.py` 输出 JSON/CSV、Trace audit 和条件分组。配置/价格、未知 usage/费用和 not_started 均如实保留。`prepare.py` 仅离线计划；可加载本地配置快照但不调用模型/OJ。

`agent/core/metrics.py` 只读有序 Trace，核对失败→候选→提交→终态的 version/SHA-256/model call/submission 链。first_try_ac 只认首个确认提交 AC；recovery 只认可信 sample/program failure 或正式程序 verdict 后的模型生成候选与最终 AC；正式 recovery 再要求成功 DEBUG 与 REVIEW。IE/HTTP/provider/protocol/unknown/LLM checker 拒绝不给算法修复计数。损坏/计数不完整 Trace 的关键结论为 null。不回写历史 State。

小正式集为 `config/experiment.small.yaml`：1200/1400/1600/1800 各 3 个公开核实题，原四固定组加 mixed，各一次/60 任务；仅准备、不执行。`models/evidence.py` 区分 configured 与 verified，未核实供应商字段为 null；配置 input bound 不代表 context window。详见 [experiment protocol](experiment-protocol.md)。

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
| agent/oj_client/；agent/tools/ | HTTP 解析/轮询、安全投影、参数验证与执行 |
| agent/workspace/；agent/execution.py | State/Trace/锁/原子工件与共享单题执行 |
| experiments/ | 配置、串行实验、比赛、离线准备、JSON/CSV 与 enrichment |
| dashboard/ | 可选 loopback UI、受限读取、确认式本地控制队列 |
| client_tests/ | Fake/MockTransport/ASGI/Node；无自动远程付费请求 |

构建 wheel/sdist、发行源码测试、Python 3.9 编译、JS syntax/diff/secret 检查见 [本轮证据](evidence/20261002-trust.md)。真实联调与替身严格分列；历史工件不修饰结果。
