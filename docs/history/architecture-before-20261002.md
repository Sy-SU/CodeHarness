# CodeHarness 架构与独立开发边界

本文依据用户提供的《CodeHarness：独立 Agent 项目规划与 Codex 执行提示词》维护。它描述 Phase 0–5 实现边界、验证层次和仍待确认的契约；实现通过不等于真实解题成功或正式实验已可比。分阶段任务与勾选状态见 [TODO.md](../TODO.md)。

2026-10-01 已完成 Phase 0–5 实现与替身验收及 Dashboard 发起任务扩展，历史 T1003 失败结果保留。2026-10-02 新增比赛测试，复用逐题执行与批量日志；比赛 1 真实验证 1/2 AC、5 次模型、1 次正式提交，配置估算 0.0255813 CNY。`GET /api/v1/me` 现明确返回 full，actual/effective 分开记录；历史 unknown 不回写。五组真实比较、正式失败反馈→修复→AC 的完整真实循环，以及供应商价格/有效上限核验仍未完成。

## 目标与项目分工

CodeHarness 用于研究更强 LLM 的求解收益，以及 Judge Feedback、重试、修改、模型路由和 Agent Loop 的收益。V1 是基于状态机的单 Agent，重点是能执行、记录并比较实验。

| 项目 | 运行环境 | 职责 |
| --- | --- | --- |
| CodeHarness | macOS | CodingAgent / Harness、模型配置与路由、状态推进、工具调用、Workspace、Context、Trace、CLI 和实验 |
| MiniOJ | Ubuntu / WSL Ubuntu | 独立 Web、用户与权限、题目、测试数据、编译执行、正式评测和 HTTP JSON API |

正式 verdict 由 MiniOJ 生成，CodeHarness 读取并记录，模型 REVIEW 不替代评测。评测通过 HTTP JSON API 和 Bearer Token 访问 MiniOJ；比赛题单暂无读取 JSON API，新增狭窄的公开 HTTP 页面适配（见比赛节）。不 import 其内部模块或 schema 包、不共享数据库 / testcase / 运行时对象、不通过 SSH、WSL、Docker 或远程 Shell 绕过接口。

本地文件工具只管理任务 Workspace，不赋予本地编译、执行用户解答或管理远程服务器的职责。V1 不做 Multi-Agent、完整长期记忆、完整自动 stress testing 或本地 Judge；Phase 4 实现单题断点恢复，不扩展跨任务记忆。

## 仓库现状与证据边界

以下结论来自当前工作区文件、Phase 0–5 与 Dashboard 本地验证，以及单列的 MiniOJ 与模型联调，包含未提交内容。真实调用只验收对应最小链路，不自动验收未运行的组合。

| 证据入口 | 静态检查结果 | 对独立项目的影响 |
| --- | --- | --- |
| [pyproject.toml](../pyproject.toml) | wheel 构建 `agent` / `experiments` / `dashboard`，六个客户端入口；Web 依赖仅在 optional dashboard extra | 默认安装不携带 MiniOJ 服务端或 Web 依赖；Dashboard CLI 默认可启动任务，`--read-only` 禁用写端点 |
| [.env.example](../.env.example)、[.gitignore](../.gitignore)、[agent/config.py](../agent/config.py) | 环境样例只含客户端槽位；`ClientSettings` 校验 URL 并隐藏 Token repr；根工件规则为 `/workspace/` | endpoint / Token / Provider 仍由使用者选择；真实 `.env` 被忽略，`agent/workspace/` 未被误伤 |
| [agent/oj_client/client.py](../agent/oj_client/client.py)、[types.py](../agent/oj_client/types.py) | 五个评测方法返回客户端自有类型；Bearer/成功状态/JSON/核心字段/错误/轮询封装；ID 规范化为字符串；补充明确模式与公开比赛读取 | 真实 AC 链和当前 me.full 已确认；其他失败 schema/去重/native verdict_only 仍待确认，公开 HTML 不当作未提供的 JSON 契约 |
| [agent/tools/runtime.py](../agent/tools/runtime.py) | 五个 OJ 操作、三个文件操作及内部轮询 helper 均有参数 schema、校验和关联 Trace | 固定状态机调度，不用模型原生 Tool Call |
| [agent/workspace/task.py](../agent/workspace/task.py) | 原子写入、托管文件/路径保护、脱敏 Trace，权威 checkpoint 与单任务互斥锁 | 单题恢复已实现；不盲目重发可能已生效的模型请求/提交 POST，不做跨题记忆 |
| [agent/models/types.py](../agent/models/types.py)、[provider.py](../agent/models/provider.py)、[registry.py](../agent/models/registry.py)、[router.py](../agent/models/router.py)、[runtime.py](../agent/models/runtime.py) | 原始响应归一、安全错误、可缺失 usage、四 Profile 路由与调用记账已验证 | 复用当前 standard/strong 映射，请求 max_tokens=8192；默认单任务 1 元可调/最多 10 POST，供应商价格/有效上限仍待核验 |
| [agent/core/policy.py](../agent/core/policy.py) | 混合 Role 映射可独立覆盖；保留显式升级配置机制 | 当前 harness 强制禁止升级，3 个 DEBUG 候选连续失败重新 PLAN |
| [agent/core/agent.py](../agent/core/agent.py)、[harness.py](../agent/core/harness.py)、[context.py](../agent/core/context.py) | code-only 单次语义；harness 固定状态机、样例优先、预算预留、只读 REVIEW、逐版工件/恢复 | 替身验收完整；历史 T1003 失败保留，比赛 1 的 B 已验证样例→正式 AC→REVIEW；正式失败后的修复循环仍缺 |
| [agent/cli.py](../agent/cli.py) | `solve` 支持两种 mode，`resume` 加载单题 checkpoint，显式 HTTP / polling 参数和远程确认；旧子命令兼容 | harness 的 `--profile` 可固定所有模型 Role；省略则保留基础混合映射 |
| [agent/execution.py](../agent/execution.py)、[experiments/runner.py](../experiments/runner.py)、[results.py](../experiments/results.py) | 同一有预算单题入口、串行批量调度、隔离工件、意图日志、JSON/CSV、Trace 对账与实际条件分组；比赛复用同入口 | 失败/中断/未知不冒充成功或免费；当前 actual=full 已确认，历史 unknown 与 native/projected 模式仍分组 |
| [client_tests](../client_tests) | 默认入口只收集客户端测试，包含 Phase 0–5、Dashboard 与启动安全回归 | 最新命令/数量见 TODO；替身与真实证据严格分列 |

Phase 1 完成后，仓库中遗留的 `oj/`、`shared/` 与服务端 `tests/` 已按用户确认删除；原始基线仍可从 Git 历史 `ec73c05` 恢复。客户端源码无 MiniOJ 内部 import，wheel、sdist、默认依赖、命令入口和测试入口均只保留 CodeHarness 侧内容。真实 HTTP 联调证据仍与这个静态边界检查分开记录。

用户明确的次数/成本规则与下述实现策略已单列；请求超时继续显式传入，真实模型/价格与实验规则不能从占位配置反推。

## Phase 0 已确认的架构决策

1. **发行边界：** 当前继续复用 `agent/` 与 `experiments/` 包名，不为目录美观机械迁移；CodeHarness sdist / wheel 都不包含 `oj` / `shared`，也不注册服务端命令。
2. **配置边界：** 客户端统一从 `ClientSettings` 读取 `OJ_BASE_URL`、`OJ_API_TOKEN`、`MODEL_CONFIG`；Provider endpoint / key 仍由模型 Registry 按显式配置引用。服务端数据库、Session、Docker、Worker 设置不进入客户端环境样例。
3. **类型所有权：** HTTP 类型定义在 `agent/oj_client/types.py`，模型响应定义在 `agent/models/types.py`，State / Trace 事件定义在 `agent/workspace/task.py`；不使用 MiniOJ Python schema 包。
4. **协议演进：** 只对已约定核心做最小类型校验；额外字段原样保留，未知 status / verdict / event 不提升为已知枚举。未确认的反馈细节和错误结构由 fixture manifest 明示为 draft / unconfirmed。
5. **测试边界：** 默认 `pytest` 只收集 `client_tests/`；旧服务端 `tests/` 已移除。其中可复用已有 Fake / MockTransport 测试，但输出必须标为替身验证。
6. **工件与源码：** `/workspace/` 是根目录运行工件；`agent/workspace/` 是可跟踪源码。真实 `.env` 保持忽略。

以上决定覆盖 Phase 0。Phase 1 在下节继续确认客户端内部行为；具体远程字段、服务端重试/去重支持、模型 / 价格、预算、升级阈值和 Loop 转换仍由未定项管理。

## Phase 1 已确认的客户端决策

1. **响应与错误：** `get_problem`、`run_code`、`submit_solution`、`get_submission`、`get_feedback` 验证约定成功状态、JSON object 和最小核心字段；失败分类为 transport、HTTP、protocol、result_unknown。错误对象不保存 Authorization，也不允许自动重试。
2. **提交风险：** 正式提交 POST 超时，或 HTTP 202 已接受但响应无法解析为有效 QUEUED 记录时，一律视为远程创建状态未知；客户端不自动补发。服务端查重 / 幂等键未确认前，调用方必须人工恢复。
3. **轮询终态：** 轮询由 OJClient 的 `wait_for_submission` 负责，deadline 与 interval 必须由调用方显式提供。只接受机器字段 `status=FINISHED` 加已知 verdict；不解析 summary。未知 status / verdict 立即作为协议错误停止。
4. **结果分流：** AC 为 accepted；WA / CE / RE / TLE / MLE / OLE 为 user_program_failure；IE 为 remote_infrastructure_failure。传输或协议错误不伪装成任何 verdict。
5. **Custom Run 兼容：** 默认请求只发送约定字段 `source_code`；旧混合仓库使用过的 `code` 别名必须通过 `send_custom_run_code_alias=True` 显式开启。失败状态、同步模型和资源限制仍属 U02。
6. **Workspace 写入：** 新任务目录不得覆盖；内部 State / JSON 使用同目录临时文件加 `os.replace`；文件工具默认不得覆盖，显式 `overwrite=true` 才可覆盖普通任务文件；工具禁止写 `task.json` / `state.json` / `events.jsonl`，并拒绝绝对路径、越界和符号链接。
7. **Tool 与 Trace：** 五个 OJ 方法和三个文件方法为协议工具；`wait_for_submission` 是客户端编排 helper。参数先按 ToolSpec 校验；每个调用生成 correlation id。Phase 1 Trace 使用 `phase1-v1`，含 event id、UTC timestamp、task、phase、type、payload，并对 token/key/Authorization/Bearer 值脱敏。
8. **验证分层：** `run_fixed_solution` 和 `codeharness-oj-client submit-fixed` 提供不调用模型的真实链路入口；本地 MockTransport 证据与真实 MiniOJ 提交证据分别记录。远程命令必须显式 `--confirm-submit`。
9. **真实链路证据：** 2026-10-01 使用 `t1001` 固定 C++ 解答创建提交 `sub_FhqFt4PN68kPAJ7N`，服务端返回 `QUEUED`，轮询得到 `FINISHED / AC`，feedback 为 5/5 tests；任务 State 记录一次提交、零次 LLM 调用，工件位于 `workspace/phase1-live-t1001-20261001/`。

该真实链路确认了 U01 的 AC 最小成功形态，但没有确认 U01/U02/U04 的其余远程字段，也没有为 U03 选择项目级超时/轮询默认值；联调所用 15 秒 HTTP timeout、1 秒 polling interval 和 120 秒 deadline 只是本次命令参数，不是产品默认值。

## Phase 2 已确认的模型层决策

1. **统一结果：** `LLMResponse` 包含成功/失败状态、正文、可缺失 usage、Provider、模型、Profile、request id、finish reason、工具调用、耗时和安全错误。HTTP body、SDK 对象与供应商异常文本不流入上层。
2. **配置与路由：** Provider endpoint / key 由环境变量名引用；Provider timeout、支持参数、每 Profile 的真实模型 ID 与参数由 YAML 显式提供。fast / standard / strong / max 必须全部可解析，`max` 只要求可配置，不要求调用。
3. **usage 与成本：** 缺失 usage 保持 `null` 并单独计数，不伪造零 token。价格只有输入/输出单价与币种同时配置时才可计算；缺失 usage 或价格时 `estimated_cost=null` 并记录原因，不解释为免费。
4. **Policy：** 默认 Role 映射仍为 PLAN/REVIEW/TEST_GENERATION → strong、CODE/DEBUG → standard。DEBUG 升级默认关闭；启用必须显式配置阈值、适用失败类型和 from/to Profile。
5. **调用记录：** `ModelCallRuntime` 在请求前计入尝试，成功与失败都写入 State 和共享 correlation id 的 `LLM_CALL` / `LLM_RESPONSE`；Phase 2 探针使用 `phase2-v1`。意外 Provider 异常也转换为安全失败并持久化。
6. **真实链路证据：** 一次性临时配置将四个 Profile 都指向 `qwen3.8-flash`，只调用 fast 做连通性探针。响应精确匹配 `CODEHARNESS_MODEL_OK`，usage 为 74/46 tokens，耗时 1441 ms；价格未配置，成本正确保持 unknown。该映射不是正式实验决策。

这些决定完成 Phase 2 的模型抽象与接入。原生 Tool Call 是否用于调度、正式 Profile 映射、模型参数、价格、预算和升级阈值仍由 U05–U08、U13 管理。

## Phase 3 已确认的 code-only 决策

1. **固定调用面：** code-only 只读取 `/api/v1/agent/problems/{problem_id}` 的清洗题目，只构建一次 CODE 上下文，只发起一次指定 Profile 的模型调用。它不进入 PLAN / DEBUG / REVIEW / TEST_GENERATION，不调用 Custom Run 或 feedback，也不把 verdict 回传模型。
2. **提交上限：** 成功提取并保存候选后至多尝试一次正式 POST；创建失败、POST 状态未知或轮询失败都立即终止，不自动重新生成或补发。`submission_attempt_count` 记录 POST 尝试，`submission_count` 只记录已确认创建，避免把未知创建状态当成零风险。
3. **终态：** 任务显式区分 `accepted`、`user_program_failure`、`remote_infrastructure_failure`、`model_failure`、`invalid_model_output`、`client_failure`、`result_unknown` 与 `internal_failure`，并单独保存 `termination_reason` / `error_kind`。IE 不计为算法失败，通信错误不伪装成 verdict。
4. **版本关联：** 每个成功提取的候选在 Phase 3 固定为 `solution-v1`，记录 UTF-8 源码 SHA-256 与生成它的 model call ID。`CODE_VERSION`、`SUBMISSION`、`JUDGE_RESULT` 事件携带同一版本、哈希和调用引用；提交事件另以 submission ID 关联。
5. **工件：** 保存清洗后的 `problem.json` / `problem.md`、统一模型响应元数据、`solution.cpp`、代码版本、提交创建/终态和稳定 `artifacts/result.json`。失败任务也进入 DONE 并保存结果，不能只靠异常栈判断状态。
6. **CLI：** `codeharness-agent solve` 是 Phase 3 的正式单题入口；`--mode code-only`、`--profile`、HTTP timeout、poll interval 与 deadline 必须显式给出，真实运行还必须同时确认模型调用和正式提交。旧 `code-only` / `harness-loop` 子命令仅为兼容入口，后者不因此获得 Phase 4 验收。
7. **真实协议补充：** Phase 3 首次联调发现当前 MiniOJ 的 202 创建响应使用整数 `submission_id`。一次显式协议诊断确认该 wire shape 后，客户端只将已确认的非负整数或非空字符串 ID 规范化为内部字符串；没有据此猜测其他未确认协议字段。
8. **真实链路证据：** 临时验证配置把 `standard` 指向 `qwen3.8-flash`；任务 `phase3-live-t1001-qwen38-flash-20261001-v2` 一次 CODE 调用、一次正式提交 `12`，得到 `FINISHED / AC`，使用 230/222 tokens。源码哈希、model call、submission 与 verdict 已独立对账，工件未发现凭据。

这些决定冻结 code-only 的结构语义和异常口径。历史临时模型映射不是 Phase 5 正式策略；后续执行服务添加预算守卫，不给 code-only 增加样例/重试。

## Phase 4 已确认的单题决策

1. **固定调度：** `HarnessLoop` 复用现有 ModelCallRuntime、ContextBuilder、ToolRuntime、OJClient 和 Workspace。基础 Role 映射保持 PLAN → strong、CODE / DEBUG → standard，可用 `--profile` 显式固定所有模型 Role；强制 `allow_escalation=False`，即使 YAML 开启升级也不生效。不调用 TEST_GENERATION 或模型 REVIEW。
2. **回退：** 初始候选失败进入 DEBUG；DEBUG 返回完整修正版，经 CODE 保存但不再多调一次 CODE 模型。3 个 DEBUG 生成候选连续在样例或正式评测失败后，重新 PLAN，再 CODE；重新规划清零该连续失败计数，保存原因和新版计划。
3. **样例门禁：** 每版源码先依次 Custom Run 全部公开样例。成功运行、exit code 为 0 或未提供、stdout 未截断且空白 token 序列与 expected 一致才通过；失败先调试，不创建正式提交。没有公开样例时直接正式评测。当前不支持浮点容差或 special judge。
4. **提交预算：** 每题至多 10 次正式 POST 尝试；请求前计数，未确认创建也占一次风险额度。不自动重发。Custom Run 独立计数，不充作正式提交。
5. **成本预算：** 每次模型请求前须有 CNY 输入/输出单价、有效 `input_token_limit` 和 Provider 支持的 `max_tokens`。按完整上限预留 `(输入上限 × 输入单价 + 输出上限 × 输出单价) / 1e6`，不退还预留。当前单任务默认 1 CNY，可由 YAML/CLI `--max-cost-cny` 或 Dashboard 调高/调低，须为有限正数；覆盖 Phase 4 原硬性 1 元上限，不提高 10 POST 限制。实际 usage 成本另计，缺 usage/超界停止；账单约束依赖真实价格/有效上限，不把用户配置当供应商承诺。缺配置付费前 `budget_unverifiable`。
6. **保险与 Context：** 可配置 80 次 LLM 调用熔断，避免样例持续失败/零价路由无限运行；无默认墙钟上限，可显式设累计活动运行秒数，恢复等待时间不计入。完整保留单题题面、notes、所有样例、计划和当前代码；模型提示保留最近 5 条摘要、最多 8000 字符反馈，完整反馈工件留存。输入以 UTF-8 字节数加每消息 1024 开销做保守门禁，超限停止而不裁掉源码/题面。
7. **REVIEW：** 仅正式 AC 后做本地只读一致性检查：当前不可变候选版本/哈希必须等于实际提交版本/哈希，写 `review.md` 与计数/费用预留；不调模型、不改源码，不以意见替代 MiniOJ verdict。预算/异常直接 DONE，IE 不进 DEBUG。
8. **恢复：** `checkpoint.json` 原子保存 Context、阶段/子步骤、已完成模型响应、操作日志、全部 State 与实际路由/价格/上限/策略和 OJ endpoint 指纹；它是权威状态，`state.json` 为可读投影。非阻塞单任务锁避免同时运行，身份/源码哈希/配置漂移检查失败即停止。已落盘模型响应复用；已知 submission ID 只继续查询。中断期间模型请求或创建 POST 是否生效未知时保存 result_unknown，不盲目重发；服务端无幂等支持时无法承诺任意故障点自动恢复。
9. **证据：** `phase4-v1` 关联各版源码/计划/样例/反馈、调用 ID、SHA-256、submission ID、verdict、REPLAN 与预算预留。MockTransport 完整链有样例失败、DEBUG、正式 WA 反馈后 AC；原 Phase 4 验证真实样例/缺价格拦截，后续 Phase 5 验证真实生成/样例失败/DEBUG 的安全结束，不冒充真实完整 AC 循环。

## Phase 5 与 Dashboard 启动的实现决策

1. **复用入口：** `ExecutionService` 组装现有 CodingAgent/HarnessLoop、ModelPolicy、ContextBuilder、Runtime 与 OJClient。Experiment Runner 和 Dashboard LaunchService 共用它；无 Shell、第二套求解器或本地 Judge。`RunRequest` 声明模式、策略、预算及 HTTP/反馈条件，`execution-config.json` 冻结实际 routes、模型、价格、参数、Role 映射与 endpoint 指纹，不保存密钥。
2. **策略命名：** 已确认 T1003、四个 fixed standard/strong × mode 加第五组 `mixed-harness`，各 1 次。fixed profile 固定模型 Role；混合显式 PLAN strong、CODE/DEBUG standard，不标成全 standard。不升级、不启用 TEST_GENERATION/模型 REVIEW。复用用户模型/价格映射，输出请求上限改为 8192；这是新条件，不追改历史 4096 结果，不声称有效上限已由供应商核验。
3. **批量安全：** `phase5-v1` 要求正数整批 cap、唯一题目、命名策略和重复次数（至多 1000 个任务）。串行/独立 Workspace；每策略/重复是独立任务，各自 10 POST / 默认 1 元，不再共享同题额度。整批按累计预留限额；正式五组配置 cap=5 元是五份额度总上限，不是预计支出或自动启动。较小整批 cap 会减少后续额度/记 not_started，仍须标注不公平条件。code-only 同样预留费用，只一次 CODE。未知 usage 保持 null；失败预留不退，usage 超界整批停且不能恢复继续花费。
4. **意图与恢复：** `workspace/.experiments/<id>/manifest.json` 先保存任务意图和有效额度，再执行。冻结模型/价格/参数、Role、预算与 endpoint，漂移拒绝恢复。完成任务只读或重建报告；已知 harness 响应/ID 按原 checkpoint 恢复；中断 code-only 不再次生成。不确定请求不重发，不假定 MiniOJ 有幂等键。隐藏批量目录不被普通任务索引当成题目。
5. **实际与有效反馈：** 默认 expected_feedback_mode=verdict_only、require_feedback_mode=true。OJClient 读取 `GET /api/v1/me`，仅认可明确 full/verdict_only，否则 actual=null 并记录来源/状态；该可选字段不是已确认远程协议，最近 live 记录未提供它。用户允许 full → verdict_only 单向兼容：formal_verdict_only_v1 在 ToolRuntime 正式结果返回/日志前过滤，actual 保留 full、effective 记 verdict_only。未知仍模型前停止，不用期望或权限/内容代填 actual。显式 full 仍要求 actual full，不允许反向升级；unknown 联调须显式 null/false。正式实验需确认可观测方式，若字段位置不同显式适配。
6. **结果与对账：** 导出 tasks、summary 两类 JSON/CSV，包含 solved/verdict/终态、所有调用/提交尝试及确认数、Profile counts、tokens/usage missing/uncertain_llm_calls、费用/币种/预留、DEBUG/REPLAN/Custom Run、活动耗时、版本/哈希/ID 和条件指纹。Runtime 在发出请求前持久化 pending_usage/null 成本；只有取得响应才恢复既有 subtotal 并计费。中断无响应 tokens/费用保持未知，缺失响应与已收到但无 usage 分开统计，预留均不退。实验/历史汇总及 Dashboard 一致显示未知。Trace 独立对账（含 DEBUG 实际调用）；坏行/未完成调用告警。组键包括策略、实际执行配置（含有效预算）和反馈条件；不合并固定/混合、不同模型/反馈或未核实条件。
7. **本地启动 UI：** CLI 默认 launch、程序化 Settings 默认只读。首页题目/mode/策略及“任务配置”入口，展开可改 max_cost_cny/max_llm_calls/max_submissions/http_timeout/poll_interval/deadline，默认 1/80/10/15/1/120；摘要实时更新/可复位。仅当前新任务生效，不改 YAML/凭据/已保存任务；仍最多 10 POST、DEBUG 3 次重 PLAN、不升级，code-only 次数固定 1。嵌套 task_config 严格白名单/类型/有限数验证，兼容旧平铺费用字段但拒绝双写。配置由既有 RunRequest 快照/指纹冻结，恢复不可改配置或重置预算。后台串行队列 8，确认 POST 返回 202，GET/poll 无远程副作用；反馈模式门禁/同源/CSRF 规则不变，默认期望改为 verdict_only。旧 Dashboard 需重启加载，已保存 full/unknown 任务仍按原 request/checkpoint 恢复。
8. **控制安全与恢复：** 仅 loopback；写入验证精确 Origin（含端口）、Sec-Fetch-Site、随机 CSRF、JSON/8 KiB 限额、明确确认和持久 request ID。同 nonce 同意图返回原任务，不同意图 409；重复恢复在活跃任务时拒绝，单题执行仍由 Workspace 锁保护。`workspace/.jobs/` 原子保存意图/状态；服务器重启标 interrupted、绝不自动重跑。只有 UI 发起且可安全恢复的 harness 显示手动 resume；code-only 不自动恢复。`--read-only` 移除全部控制端点。已有读侧安全/脱敏/中英文/轮询能力保留。
9. **发行与证据：** 本地 models.yaml/harness.yaml 等用户配置不进入 sdist，示例与 smoke 入包；六个 CLI 仍是纯客户端。真实 T1003 Trace/State 已对账：4 次 standard 请求，936/2539 与 11790/9288 tokens，1 次样例失败、1 次 DEBUG、无正式 POST；两次 invalid_model_output 是模型产物失败，不隐式补调用或编造 verdict。

10. **条件变更不可偷换：** 新批量指纹冻结 `budget_allocation: independent_tasks_v1`，旧同题共享额度批次不得按新语义恢复；旧报告保留可读。改变 max_tokens、价格、额度须新 ID。CLI/UI 恢复沿用保存的额度/累计预留，拒绝加钱重置或改预算，并在直接 CLI 预算漂移时保持原 State 不变。

11. **verdict-only 变更验证：** `config/experiment.full.yaml` 保留历史文件名，name 为 T1003-verdict-only-five-strategies；旧 full 实验不得按新条件 resume。只有 WA verdict、没有远程 summary/隐藏用例的替身链可 DEBUG 后 AC。full → verdict_only 兼容、未知拦截、显式 full 覆盖及旧 full 恢复均有测试；低层直接 Agent CLI 原有路径不新增本门禁，不冒充共享入口条件验收。

12. **正式反馈投影与条件冻结：** 新 verdict_only 快照/State/checkpoint.config 记录 feedback_policy=formal_verdict_only_v1。submit_solution/get_submission/wait_for_submission 仅返回 ID/已识别状态/verdict，get_feedback 仅返回已识别 verdict；远程 summary、编译诊断、用例数据和任意嵌套扩展字段全部丢弃。正式错误正文也不透传，保留分类/HTTP 状态/未知 POST 标志，不改变不重试规则。过滤发生在 Trace、工件、Context 之前；公开样例 Custom Run stdout/stderr 仍可见。State/result/Trace/报告/API 区分 actual/effective/policy，分组保留三者，不将 projected full 混成 raw full/native verdict-only。条件核验允许已确认 full + 有效 verdict-only 策略，但不把 actual 改成期望值。新策略参与单题/整批指纹，旧精确 verdict_only 检查点拒绝无声升级，需新 ID；旧 full/unknown 保持原配置。过滤状态/策略漂移在付费前拒绝。本地 Fake/MockTransport 的隐藏标记未进入提示/日志/工件/检查点；尚无真实模型/OJ 兼容联调。

## 模块依赖与当前组织

```text
任务 / CLI / 实验配置
          │
          ▼
      CodingAgent
          │
   ┌──────┼───────────────┬───────────────────┐
   │      │               │                   │
 State  ContextBuilder  ModelPolicy      Tool Runtime
   │                      │                   │
Workspace                 ▼               Tool Registry
Trace                 Model Router            │
                          │              ┌────┴─────┐
                     Model Registry      │          │
                          │           文件工具    OJ 工具
                     Provider Adapter                │
                          │                       OJClient
                    百炼 / 其他 Provider             │
                                                HTTP JSON
                                                    │
                                                  MiniOJ
```

| 模块 | 负责 | 边界 |
| --- | --- | --- |
| CodingAgent / Loop | 执行当前步骤、推进状态、处理终止 | 不含真实模型名、供应商 HTTP、MiniOJ 传输细节或评测实现 |
| State | 阶段、代码 / 结果引用、计数和进度 | 不执行工具或调用模型 |
| ContextBuilder | 按阶段选择输入内容 | 不无限追加历史，不评测代码 |
| ModelPolicy | Role → Profile、模型升级 | 不处理供应商 HTTP |
| Registry / Router | 配置解析、Profile 查找、Provider 路由 | 不决定解题算法或状态转换 |
| Provider Adapter | 封装模型调用并归一化响应 | 不管理 Agent 状态机 |
| Tool Registry / Runtime | 工具名称、参数定义、实现注册；参数校验、执行、错误和结果回传 | 不含 MiniOJ 内部实现 |
| OJClient | HTTP、Token、类型映射、超时、轮询与传输错误 | 不访问数据库、Docker 或 testcase |
| Workspace / Trace | 保存任务工件、State 和事件 | 不是远程持久化服务 |
| Experiment Runner | 批量调度、配置留存、任务结果与汇总 | 复用 Agent / OJClient，不复制另一套求解器 |

Phase 0 确认复用当前目录：

```text
agent/
  cli.py
  config.py
  core/            # loop、state、policy、context 候选实现
  models/          # types、provider、registry、router、调用 runtime 与探针 CLI
  tools/           # runtime 与文件 / OJ 工具注册
  oj_client/       # HTTP 客户端及 CodeHarness 自有类型
  workspace/       # Workspace / State / Trace 源码
config/
experiments/       # 配置、批量 Runner、JSON/CSV 与历史 State 汇总
dashboard/         # 可选本地启动/只读模式，不是 MiniOJ 服务端
client_tests/      # 默认客户端测试与协议 fixture
workspace/         # 每任务工件，不是代码模块
docs/architecture.md
TODO.md
.env.example
.gitignore
pyproject.toml
README.md
```

不得为 CodeHarness 新增 `oj/server/`、`oj/worker/` 或 `docker/`，也不依赖 MiniOJ 仓库内的 Python 类型包才能运行。

## 模型层：Provider、Profile、Role

Provider 是服务接入方式。`OpenAICompatibleProvider` 已通过百炼兼容 endpoint 的真实短文本调用；配置提供 base URL / API Key 的环境变量名、显式请求 timeout、允许参数、真实模型 ID 和每 Profile 参数。Adapter 不自动重试，不把 HTTP body、密钥或底层异常文本写入上层错误。

Profile 是可替换的逻辑别名：`fast`、`standard`、`strong`、`max`。真实模型名只放配置，不进入 Loop 或业务分支；Profile 名不构成某个模型能力的已验证结论。`max` 不要求 V1 必须调用，也不要求提前实现全部升级路径。

Role 是同一个 Agent 当前承担的任务，默认映射保持如下：

| Role | 默认 Profile |
| --- | --- |
| PLAN | strong |
| CODE | standard |
| TEST_GENERATION | strong |
| DEBUG | standard |
| REVIEW | strong |

ModelPolicy 保留显式升级能力，但当前 harness 根据用户决定强制关闭升级；重新 PLAN 不改变基础 Role 路由。

```text
CodingAgent → ModelPolicy → 逻辑 Profile → ModelRouter
           → ModelRegistry → Provider Adapter → 百炼 / 其他 Provider
           ← 内部 LLMResponse
```

Registry 校验配置，Router 找 Provider 和模型，Adapter 发请求并转换结果，ModelCallRuntime 统一记账和写 Trace。内部 `LLMResponse` 已表示正文、状态、可缺失 usage、Provider / 模型 / Profile、request id、finish reason、工具调用、耗时与安全错误；上层不接触供应商 SDK 对象或原始 dict。工具调用表示已归一化，当前固定状态机不采用模型原生 Tool Call 调度。

配置示例只展示层次，不可直接用于真实调用：

```yaml
models:
  fast:
    provider: bailian
    model: MODEL_NAME_A
  standard:
    provider: bailian
    model: MODEL_NAME_B
  strong:
    provider: bailian
    model: MODEL_NAME_C
  max:
    provider: bailian
    model: MODEL_NAME_D
```

## 工具层与 OJClient

工具注册名称保持精简：

| 工具 | 实际边界 |
| --- | --- |
| `get_problem(problem_id)` | OJClient 获取清洗后的程序用题目 |
| `run_code(code, input)` | OJClient 向 MiniOJ 提交调用方输入；现有 Python 参数名为 `stdin` |
| `submit_solution(problem_id, code)` | OJClient 创建正式提交 |
| `get_submission(submission_id)` | OJClient 查询状态和结果 |
| `get_feedback(submission_id)` | OJClient 获取服务端允许的结构化反馈 |
| `read_file` / `write_file` / `list_files` | 仅访问当前任务 Workspace |

```text
Agent → Tool Runtime → OJ 工具 → OJClient → HTTP JSON → MiniOJ
Agent → Tool Runtime → 文件工具 → 当前任务 Workspace
```

Registry 管名称、参数定义与实现，Runtime 负责参数校验、执行、异常、结果回传和 Trace。上层 Agent 不手工拼 URL、不解析原始 HTTP 响应，不通过工具操作 MiniOJ 的 Docker、数据库或内部文件。V1 不提供通用 Shell / SSH / 系统执行工具。

OJClient 是访问 MiniOJ 的唯一正式入口，提供上述五个协议方法以及内部轮询 helper。它封装 Bearer、成功 HTTP 状态、JSON object、响应类型、deadline 轮询和安全错误映射；不自动重试。连接失败、HTTP / 协议错误、结果未知、IE 与 WA / CE / RE 等程序结果分别表示。提交 POST 超时或无法解释的 202 Accepted 响应会抛出 `OJResultUnknownError(submission_state_unknown=True)`，在查重 / 幂等规则未确认时不得盲目重复 POST。

MiniOJ 不可用时使用协议 fixture / MockTransport 验证客户端与固定解答链；它们只用于测试，不成为本地 OJ。Custom Run 默认只发送 `source_code`；`code` 是显式兼容开关，其存在不表示远程双方已冻结该扩展。

V1 当前使用固定状态机调度工具，不引入多 Agent 或复杂框架。

## Workspace、State 与 ContextBuilder

每个任务独立保存：

```text
workspace/<task-id>/
├── task.json       # 本次任务配置
├── problem.md      # 实际使用的题目表示
├── solution.cpp    # 当前候选
├── state.json      # 当前状态与计数
├── checkpoint.json # harness 权威状态 / Context / 配置 / 操作日志
├── .run.lock       # harness 单任务互斥锁
├── events.jsonl    # 事件序列
└── artifacts/      # 候选版本、提交反馈、测试结果等
```

State 包含 task/problem id、current phase、候选数、提交尝试/确认数、last verdict、current model profile、LLM 尝试/成功/失败数、各 Profile 次数、usage/成本状态、失败类型与终态，代码版本/哈希及生成调用/提交引用。Phase 4 另记录连续 DEBUG 失败、重新规划、Custom Run、预算预留和恢复次数。checkpoint 支持单题恢复；长期记忆、跨任务复用不在范围。

文件工具只接受任务根目录下相对路径，拒绝绝对路径、越界和任何符号链接；默认不覆盖普通文件，覆盖必须显式请求；task/state/events/checkpoint/lock 都是工具不可写的托管文件。内部写入使用同目录临时文件、fsync 与 `os.replace`。`solution.cpp` 是当前候选投影，每版完整代码保留在 `artifacts/solutions/` 并与结果关联。真实密钥不能写入任何工件或普通日志。

ContextBuilder 是独立模块，按阶段构建单题输入；题面/样例/计划/代码完整保留，反馈最多 8000 字符并标明裁剪，最近 5 条摘要。完整反馈与 Context 原件在当前任务工件/checkpoint，不无限追加聊天或混入其他题目。请求超输入门禁时停止，不静默裁掉关键内容。

code-only 只构建一次 CODE 输入，后续反馈不进入模型；harness-loop 可消费当前任务获准的结构化反馈。清洗接口未提供的 rating、tags、editorial、历史解法或 hidden testcase 不从其他接口补回；full 模式反馈中的用例工件不得混入其他任务。

## Trace 与可核查计数

稳定事件名称至少覆盖：

```text
LLM_CALL
LLM_RESPONSE
TOOL_CALL
TOOL_RESULT
SUBMISSION
JUDGE_RESULT
STATE_CHANGE
MODEL_ESCALATION
CODE_VERSION
TASK_TERMINATED
SAMPLE_RESULT / REPLAN / BUDGET_RESERVATION
TASK_RESUMED / TASK_INTERRUPTED
```

Phase 1 的 `phase1-v1` 事件包含 event id、UTC timestamp、task id、phase、type 与 payload；Tool Call / Result 共享 correlation id，提交和评测事件关联 submission id。Phase 2 的 `phase2-v1` 模型 CALL / RESPONSE 共享独立 correlation id，并记录 Role、Profile、Provider、模型、状态、usage 是否可用、request id、耗时、成本状态和安全错误。Phase 3 的 `phase3-v1` 增加 CODE_VERSION / TASK_TERMINATED，并将 model call ID、代码版本、源码哈希、submission ID 和 verdict 串联。TraceWriter 对敏感 key 和 Bearer 文本统一脱敏。

LLM 尝试数、成功/失败数、各 Profile 调用数、失败类型、缺失 usage 次数、输入 / 输出 tokens 和成本可与 Trace 对照：请求前先计数，失败也持久化；缺失 usage 或价格把成本标成 unknown/null。`phase4-v1` 将同一关联扩展到多候选、样例、重新规划、预算预留和恢复；最终 AC 必须指向实际提交的源码。

## 两种运行模式与状态机

### code-only：一次生成基线

```text
获取清洗后的题目 → 指定 Profile 的一次 CODE 调用 → 保存 solution.cpp
              → 正式提交一次 → 查询最终 verdict → 记录结果并结束
```

正常任务恰好一次模型调用和一次正式提交；不额外调用 PLAN / DEBUG / REVIEW / TEST_GENERATION，不样例试跑或 Custom Run，不基于反馈改码或再次生成，不在自动重试中生成多个候选却只统计最后一次。

查询完成状态、读取最终 verdict 用于统计是允许的，这不等于向模型回传 Judge Feedback。格式异常、代码提取失败、HTTP / 模型异常等如实进入不同终态，不能隐式重生成。提交 POST 尝试与确认创建分开计数；结果未知保留为 unknown，不假报 WA 或零提交风险。

`run_code_only` 已用替身覆盖成功、用户程序失败、IE、模型/HTTP/transport/协议错误、代码提取失败、创建状态未知和轮询超时；真实 `t1001` 任务以一次 CODE 和一次正式提交得到 AC。测试同时断言没有 PLAN / DEBUG / REVIEW / TEST_GENERATION、Custom Run 或 feedback。本基线研究一次性 Coding 能力，不承诺排除题库记忆等因素；本规划未扩展数据污染研究范围。

### harness-loop：反馈驱动的单 Agent

状态集合为 `PLAN / CODE / TEST / DEBUG / REVIEW / DONE`，入口为 FETCH_PROBLEM。固定状态机由 `HarnessLoop` 执行，TEST 含样例/样例失败/提交/轮询/反馈子步骤。

```text
PLAN → CODE → TEST（公开样例 → 正式评测）→ AC → REVIEW → DONE
  ↑      ↑       │
  │      └─ DEBUG ← 可调试的失败
  └────────────── 3 个 DEBUG 候选连续失败
任何阶段的预算/异常 → DONE；中断 → checkpoint → resume
```

| 阶段 | 职责 |
| --- | --- |
| PLAN | 理解题目，形成或修订计划 |
| CODE | 生成或修改当前解答 |
| TEST | 通过 MiniOJ 工具测试或正式评测，整理机器结果 |
| DEBUG | 消费可见结构化反馈，定位问题并推动修改或重新规划 |
| REVIEW | 审查解答与结果，不以模型意见代替 MiniOJ verdict |
| DONE | 保存终态、工件、Trace、结束原因与实验结果 |

DEBUG 修正版经 CODE 保存再进入 TEST；第三个 DEBUG 候选仍失败时在 TEST 回到 PLAN。反馈用于修复与重新规划，模型升级关闭。TEST_GENERATION 只是保留 Role，当前不调用。

样例先 Custom Run 并做空白 token 比较，全部通过才正式提交。Custom Run 的 `OK` 不是输出正确或正式 AC；失败样例保存 input/expected/actual/stderr 后先 DEBUG，恢复不能绕过这个门禁。

CE / WA / RE / TLE / MLE / OLE、IE、客户端错误与模型通信错误应分流；IE 不能直接视为算法失败。模型可使用协议允许的编译诊断，不通过 Docker 日志、宿主路径或 Worker traceback 猜测 verdict。

循环受单任务 10 POST / 默认 1 CNY 可调预留约束，另有 80 次 LLM 熔断和可选活动时间上限；成本/Context 不可核验即停。各守卫有替身验证，不把未知价格当免费。

REVIEW 仅正式 AC 后本地只读检查版本/哈希一致，写 review.md、不改解答、不调模型。预算和异常直接 DONE；不能沿用旧 verdict 为新代码背书。

## 实验模式、策略标注与指标

已确认五组比较配置（T1003，每组一次，最新要求 verdict_only，覆盖此前 full）：

| 比较目标 | 需明确的实验解释 |
| --- | --- |
| standard + code-only | 一次 CODE 使用 standard；记录实际 Provider / 模型映射 |
| strong + code-only | 一次 CODE 使用 strong；记录实际 Provider / 模型映射 |
| standard + harness-loop | 模板明确 profile=standard，全部模型 Role 固定 standard；混合策略另名 |
| strong + harness-loop | 模板明确 profile=strong，全部模型 Role 固定 strong；关闭升级 |
| mixed-harness | PLAN strong；CODE/DEBUG standard；本地 REVIEW，无升级 |

基础混合映射保持 PLAN strong、CODE/DEBUG standard；REVIEW 本地只读、TEST_GENERATION 未启用、升级关闭。显式 profile 固定模型 Role，State 区分 fixed/mixed。配置和次数已确认，实际 verdict_only/有效价格/输出限制及真实全组比较尚未验证，不能把 mixed 说成全 standard。

Experiment Runner 支持独立额度/整批 cap、保存条件和逐任务/分组 JSON/CSV；schema/安全/统计见 Phase 5 决策和 experiments/README。`experiment.full.yaml` 沿用历史文件名、五组期望 Agent verdict_only；真实运行需服务端可核实 full/verdict_only，full 时通过版本化投影保持 Agent 有效 verdict_only。

至少输出以下逐任务指标及可核查的 JSON / CSV：

| 指标 | 解释边界 |
| --- | --- |
| solved / unsolved、final verdict | verdict 来自 MiniOJ；错误 / 结果未知不伪造成用户程序 WA |
| LLM calls、successes / failures、calls per model profile | 与调用 Trace 对账；请求前计入尝试，失败按安全类型汇总 |
| input tokens、output tokens | 来自可得的 usage；缺失时单独计数，不补零 |
| estimated cost | 只有 usage、输入/输出价格和币种齐全时计算；否则为 unknown/null，不等于免费 |
| OJ submissions、debug iterations | 与实际提交、调试及去重处理对应 |
| wall clock time | 记录任务耗时，异常与中断任务的统计口径待明确 |

full 可暴露失败 hidden testcase，必须与 verdict_only 分开标注实验条件。服务端实际模式的可观测协议仍待确认，客户端仅认可显式服务端元数据；当前字段缺失记录 unknown，不用期望或内容推断。Phase 5 按实际模型 / Role / 反馈 / 有效预算指纹分组；历史 mode / variant 汇总仍不能自行建立公平性。

`qwen3.8-flash` 的历史探针不构成正式 Profile 选择。本轮读取用户配置，standard 仍指向该模型，仅执行 T1003 最小两模式联调。模型名/价格/token 上限是本地配置条件，不等于已独立核实的供应商承诺；新正式全组运行需确认价格有效性、重复次数与总预算。

## Test Generation 的 V1 边界

保留 `TEST_GENERATION → strong`，后续可针对边界条件、极端输入、合法约束与复杂度弱点生成测试。完整生成 / oracle / 自动 stress testing 不阻塞 V1，默认实验不能声称已启用。

```text
candidate + generator + brute/oracle + stress test
```

生成逻辑属于 CodeHarness，受限执行仍交 MiniOJ；不能新增本地执行器或把生成逻辑塞入 MiniOJ。输入合法性、expected 来源、brute / oracle 构造和验证均待后续设计；生成一份输入不等于获得可信正确性判据。

## 配置、密钥与 CLI

密钥来自环境变量或 `.env`，环境样例只提供空位：

```dotenv
BAILIAN_API_KEY=
BAILIAN_BASE_URL=
OJ_BASE_URL=
OJ_API_TOKEN=
```

Profile、真实模型名、Role 策略、显式请求 timeout、允许参数和可选价格放 YAML；Provider endpoint / key 只通过环境变量名引用。真实 `.env` 必须被忽略，不将密钥或 Token 写入日志 / Trace。`config/models.example.yaml` 仍使用占位模型且不含价格。

CodeHarness 不需要 MiniOJ 的 DATABASE_URL、SECRET_KEY、Docker image、宿主路径等配置。Phase 0 已从 `.env.example` 删除这些服务端项，并把真实 endpoint 保持为空。

单题入口 `codeharness-agent solve` 支持 code-only / harness-loop，后者有 `--harness-config` 和可选固定 `--profile`；`resume <task-id>` 恢复同一 Workspace。均要求显式 HTTP / polling 参数、`--confirm-model-call` 和 `--confirm-submit`；参数非法时在加载配置或网络前停止。恢复默认读取检查点策略/Role 映射，但实际模型配置仍须与保存路由一致。

当前安装入口还包括 `codeharness-model-client`、`codeharness-oj-client`、兼容 Agent 旧子命令、`codeharness-report` 与只读 `codeharness-dashboard`。模型 probe 固定一条短提示要求 `--confirm-call`；OJ submit-fixed 要求 `--confirm-submit` 和显式 timeout / polling 参数。

Feedback Mode 由 MiniOJ 权限与配置控制；CLI 可记录或校验期望条件，不能仅凭参数提升信息访问权限。

## 双方共用的 HTTP 接口约定

本节在 MiniOJ 和 CodeHarness 两份规划中保持一致。2026-10-01 已实际验证 `/me`、题目列表、清洗题面、正式提交、提交查询及 AC feedback 路径；未调用的接口和未覆盖的响应形态仍只是分级接口约定。

MiniOJ 是服务端；CodeHarness 是远程客户端。两个项目分别开发和部署，不相互 import 内部模块，不共享数据库、题库目录或运行时对象。联调通过 HTTP、Bearer Token 和 JSON 完成。

### 认证与命名

- 浏览器使用 MiniOJ Session；程序客户端使用 `Authorization: Bearer <token>`。
- CodeHarness 从 `OJ_BASE_URL` 和 `OJ_API_TOKEN` 读取连接配置。
- API 前缀保留 `/api/v1/`。`/agent/` 只是面向程序消费的接口命名空间，不表示 MiniOJ 内部实现了 Agent。
- 不因两个项目重新命名而擅自修改已约定的 API 路径。

### 接口清单

| 方法   | 路径                                                 | 用途                         |
| ------ | ---------------------------------------------------- | ---------------------------- |
| GET    | `/api/v1/me`                                         | 检查身份与连接配置           |
| GET    | `/api/v1/problems`                                   | 查询普通题目列表             |
| GET    | `/api/v1/problems/{problem_id}`                      | 获取普通题目详情             |
| GET    | `/api/v1/agent/problems/{problem_id}`                | 获取清洗后的程序用题目       |
| POST   | `/api/v1/runs`                                       | 使用调用方提供的输入执行代码 |
| POST   | `/api/v1/submissions`                                | 创建正式评测提交             |
| GET    | `/api/v1/submissions/{submission_id}`                | 查询提交状态和结果           |
| GET    | `/api/v1/agent/submissions/{submission_id}/feedback` | 获取结构化评测反馈           |
| GET    | `/api/v1/tokens`                                     | 查看本人 Token metadata      |
| POST   | `/api/v1/tokens`                                     | 创建本人 Token               |
| DELETE | `/api/v1/tokens/{token_id}`                          | 撤销本人 Token               |

CodeHarness 日常求解不必使用 Token 管理接口。初始 Token 由用户登录 MiniOJ Web 页面后创建。

### 程序用题目响应

```json
{
  "problem_id": "example-problem",
  "title": "Example Problem",
  "statement": "...",
  "input_specification": "...",
  "output_specification": "...",
  "notes": "...",
  "limits": {
    "time_ms": 2000,
    "memory_mb": 256
  },
  "samples": [
    {"input": "...", "output": "..."}
  ]
}
```

默认不包含 `rating`、`tags`、`editorial`、历史解法或 hidden testcase。CodeHarness 的求解上下文使用这个接口，不以普通题目接口补回被排除的信息。

### 正式提交

请求：

```json
{
  "problem_id": "example-problem",
  "language": "cpp20",
  "source_code": "..."
}
```

提交成功返回 `HTTP 202 Accepted`：

```json
{
  "submission_id": "sub_xxx",
  "status": "QUEUED"
}
```

真实联调已确认 `submission_id` 的 wire 值可能是非空字符串，也可能是非负整数（例如 `12`）；CodeHarness 在自有类型中统一规范化为字符串。除此之外不扩展或猜测提交响应字段。

提交状态统一为：

```text
QUEUED → COMPILING → RUNNING → FINISHED
```

编译失败等情况可以提前进入 `FINISHED`，不要求每次经过全部中间状态。

最终 verdict 统一为：

```text
AC / WA / CE / RE / TLE / MLE / OLE / IE
```

客户端根据机器字段判断状态，不解析 `summary` 文本来推断 verdict。尚未完成的提交不应被当作最终评测结果；具体的未完成反馈响应形式需要在正式联调前确定。

### Custom Run

请求：

```json
{
  "language": "cpp20",
  "source_code": "...",
  "stdin": "..."
}
```

成功执行后的结果示例：

```json
{
  "status": "OK",
  "stdout": "...",
  "stderr": "",
  "exit_code": 0,
  "time_ms": 15,
  "memory_kb": 4096,
  "stdout_truncated": false,
  "stderr_truncated": false
}
```

Custom Run 不运行 hidden testcase，也不创建正式 Submission。编译、执行仍由 MiniOJ Sandbox 完成。上述 JSON 仅表示成功结果形态；失败状态、同步或异步交付方式尚未完全确定，不擅自添加新的轮询接口。

### 结构化反馈

CE 示例：

```json
{
  "verdict": "CE",
  "summary": "Compilation failed.",
  "compile": {
    "success": false,
    "stderr": "..."
  }
}
```

WA 的完整反馈示例，仅适用于服务端允许暴露这些字段的模式：

```json
{
  "verdict": "WA",
  "summary": "Wrong answer on test 13.",
  "failure": {
    "test_index": 13,
    "input": "...",
    "expected": "...",
    "actual": "..."
  },
  "resources": {
    "time_ms": 17,
    "memory_kb": 4200
  }
}
```

TLE 示例：

```json
{
  "verdict": "TLE",
  "summary": "Time limit exceeded on test 21.",
  "failure": {"test_index": 21},
  "limits": {"time_ms": 2000}
}
```

还需为 AC、RE、MLE、OLE、IE 定义明确结构。编译诊断可以用于调试，但 Docker 内部日志、宿主文件路径和 Worker traceback 不属于对外协议。

### Feedback Mode

| 模式           | 已约定的用途与暴露范围                                       |
| -------------- | ------------------------------------------------------------ |
| `full`         | 开发调试；允许返回失败用例的 input、expected、actual、stderr、编译诊断，可能包含 hidden testcase |
| `diagnostic`   | 返回诊断信息，但不提供完整 hidden testcase；具体字段白名单待明确 |
| `verdict_only` | benchmark；返回 verdict 和受限说明，可保留约定的失败测试序号，不返回 hidden testcase 内容 |

CodeHarness 最新默认期望是 `verdict_only`，覆盖此前 V1 full 调试默认值；这是实验条件，不是远程实际模式确认。模式和访问权限由 MiniOJ 服务端控制，CodeHarness 不能提高反馈权限，但可将已明确 full 的正式反馈在客户端投影为仅 verdict。actual/effective 分开记录，隐藏用例/远程自由文本不进入 Agent 或任务工件。普通 API、专用 Feedback API 和 Web 页面仍应遵守服务端信息规则；客户端过滤不是服务端权限修复。客户端识别 full/verdict_only，diagnostic 适配未实现，null/缺失仍未知。

`full` 条件下可见失败 hidden testcase，不能将它与 `verdict_only` 条件下的结果混为同一实验条件。

### 首次真实 AC 联调后仍需确定

- 普通提交查询接口的完整 schema，以及字段在未完成状态下是省略还是为 null。
- AC、RE、MLE、OLE、IE 和通用 HTTP 错误的完整 schema。
- Custom Run 的同步或异步交付方式、失败状态和具体资源限制。
- Feedback Mode 的诊断字段白名单，以及客户端如何获知实际生效的模式。
- 请求超时、轮询间隔和轮询总期限；提交请求超时后的去重或查重办法。
- 多 testcase 的耗时、内存汇总口径和单位含义。

这些事项记录为待确定，不填写未经确认的默认值，不把草案标成已冻结协议。

## 客户端方法与 HTTP 对应

| OJClient 方法 | 接口 |
| --- | --- |
| `get_problem(problem_id)` | `GET /api/v1/agent/problems/{problem_id}` |
| `run_code(code, input)` | `POST /api/v1/runs`，传入 language / source_code / stdin |
| `submit_solution(problem_id, code)` | `POST /api/v1/submissions` |
| `get_submission(submission_id)` | `GET /api/v1/submissions/{submission_id}` |
| `get_feedback(submission_id)` | `GET /api/v1/agent/submissions/{submission_id}/feedback` |

认证与连接检查可用 `/api/v1/me`。普通题目和 Token 管理接口保持既定路径；日常求解不使用普通题目接口补回信息，也不要求客户端实现 MiniOJ 的 Token 管理系统。初始 Token 由用户在 MiniOJ Web 创建。

## 比赛测试扩展（2026-10-02）

1. **公开协议适配：** 实际 OpenAPI 提供 `POST /api/v1/contests/{contest_id}/submissions`，没有比赛题单 GET JSON 接口。`OJClient.get_contest` 只 GET `/contests/{id}` 的公开 Problems 表与 `/contests/{id}/problems/{label}` 的公开 eyebrow ID，再由已有 Agent API 获取题面；比赛 1 已确认 A→CF1454A、B→CF1454B。`public_html_v1` 不登录、不报名、不跟踪外链/重定向、不读题解/admin/隐藏用例；ID、表格、同源链接、唯一标签/题目 ID、内容类型及页面 2 MB/题数 1000 上限明确校验，变化 fail-closed。未来 JSON 协议需显式适配，不臆造 endpoint。
2. **复用求解与提交：** `ContestRunner → ExperimentRunner → ExecutionService → 现有 Agent/Harness/OJClient`，每题一种策略、一次任务，串行执行。RunRequest/批次/执行快照及 harness checkpoint 带 `contest_id`，正式提交改为比赛接口；查询、正式反馈过滤、公开样例与 REVIEW 不另实现。非比赛配置省略可选字段，旧单题 nonce、批次指纹和检查点不变。
3. **额度与结果：** 逐题默认 1 元/最多 10 POST、80 模型调用及 3 DEBUG 重 PLAN 保持，另设显式整场总预算、默认 1 元。预留不退、预算不足/未开始/未知/基础设施失败分别记录；`ac_count_only_v1` 仅统计已确认 solved+AC 与总题数，`official_score=null`，不设计罚时/加权分数/排名。code-only 保持单次生成/提交、不跑样例。
4. **日志与恢复：** `workspace/.contests/<run-id>/contest.json` 固定请求、实际条件指纹和公开题单；`report.json`/`problems.csv` 为本地投影。现有 `.experiments/<id>` 保存逐题意图/预算/完整 JSON/CSV 与 Trace 对账，单题目录保存模型/版本/提交/检查点。比赛及批次分别加锁；显式恢复不重新抓已固定题单、不给新额度、不重发未知调用/POST。已完成/blocked 运行只读，不隐式补测；题单公开后用新 ID。
5. **Dashboard：** `/contests` 复用任务表单，新增比赛 ID/整场预算，接入同一个串行队列。启动/恢复仍需 Origin/CSRF/确认/nonce/8 KiB 限制；浏览器不能上传任意题单、模型路径或评分逻辑。`/contests/{run-id}` 与 GET JSON 只读本地报告，复用已有 bounded/dir_fd/O_NOFOLLOW 读取；自动刷新到终态、逐题链接及双语呈现，`--read-only` 无控制端点。重启不自动执行旧队列。
6. **真实证据与局限：** 比赛 1 的 standard/harness 运行 `contest1-live-20261002`：B 样例通过→提交 103 AC→本地 REVIEW；A 公开样例 stdout 为合法另一排列，却被已确认的 whitespace_tokens 策略拒绝，DEBUG 无完整代码，未正式提交。5 次模型/2 次 Custom Run/1 次 POST，actual=full、effective=verdict_only、预留 0.241664 CNY、配置估算 0.0255813 CNY；不等于真实账单、官方积分或正式 WA 修复验收。special judge/多解可接远程样例 checker 或显式客户端规则，协议/规则未定，本轮不猜测或绕过门禁。

## 未定项、验证层次与后续顺序

以下编号与 [TODO.md](../TODO.md) 的未定项对应，不在本文补未经确认的默认值：

| 编号 | 实施前需明确 |
| --- | --- |
| U01 | 已确认 `QUEUED` 创建响应的 submission ID 可为字符串/非负整数、`FINISHED / AC` 查询和 AC feedback 最小 schema；完整查询 schema、进行中字段省略 / null、未完成 feedback、其他 verdict 与 HTTP 错误结构仍待确认 |
| U02 | 真实规范 `source_code` 同步 Custom Run 成功形态已验证；其他失败/资源限制、旧 `code` 别名兼容仍待确认，不扩展轮询 API |
| U03 | 请求超时、轮询间隔 / 期限、可重试错误、提交去重 / 查重及结果未知的恢复处理 |
| U04 | `GET /api/v1/me#feedback_mode=full` 现已真实确认；native verdict_only、诊断字段、多 testcase 资源汇总/单位仍待确认 |
| U05 | 用户已有模型/价格/参数配置，现有 CF2049D UI/远程提交已证实 standard/fast/max/strong 都有真实调用；正式公平比较、实际价格/有效上限仍待核验 |
| U06 | 当前固定状态机调度已确认，不启用原生 Tool Call |
| U07 | 当前不升级，3 个 DEBUG 候选连续失败重新 PLAN；以后升级策略另行确认 |
| U08 | 已确认独立任务默认 1 元可调/最多 10 POST；五组配置整批 cap=5 元，旧 smoke cap=1。有效价格/上限仍待核验，5 元不表示实际支出 |
| U09 | 公开样例优先、空白 token 比较、失败先 DEBUG 已实现；浮点容差/special judge 留后续 |
| U10 | 仅 AC 后本地只读 REVIEW，不改码、不调模型，后续调整另行确认 |
| U11 | 单题 Context/checkpoint 恢复已实现，反馈提示 8k/历史 5 条；完整题面/源码/计划不截断，不做跨题记忆 |
| U12 | 单题及批量失败/未知/预算停机/恢复/Trace 对账已实现；不能把 not_started 或配置守卫计为已完成比较 |
| U13 | 已确认 T1003、四固定组加混合各一次、8192/verdict_only；已观测 full 兼容，真实五组比较/供应商有效上限仍待验证 |
| U14 | 多版本关联、phase5-v1 配置、逐任务/分组 JSON/CSV 与实际条件指纹已实现；当前 full 已确认，native verdict_only 与全组比较未完成 |

验证分三层记录：静态文件检查只说明内容存在；测试替身检查协议处理与 Loop 行为；真实 MiniOJ / 模型调用检查实际接入。不同层次不能互相冒充。业务状态以 TODO 中的实现与验收证据为准，不以历史 README 的完成宣称推断。

Phase 0–5 实现/替身验收及比赛扩展完成，历史失败保留，未扩大到五组付费批次。full 元数据与模型→样例→正式 AC→REVIEW 已获真实证据；native verdict_only、正式失败反馈→修复→AC、供应商价格/有效 token 上限、五组比较和多解样例判定仍待完成。Test Generation/stress testing 仍为可选后续，不开发 MiniOJ 服务端。
