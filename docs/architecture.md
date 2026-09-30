# CodeHarness 架构与独立开发边界

本文依据用户提供的《CodeHarness：独立 Agent 项目规划与 Codex 执行提示词》维护。它描述目标架构、已确认的 Phase 0/1 边界、现有代码差距和仍待确认的契约；不是“V1 已验收”的报告。分阶段任务与勾选状态见 [TODO.md](../TODO.md)。

2026-10-01 已完成 Phase 0，以及 Phase 1 的本地实现、MockTransport 验收和真实 MiniOJ 固定解答联调。真实链路未调用模型，本地未执行解答；一次正式提交得到 AC。HTTP 样例仍是分级标记的契约草案，未由本次 AC 路径覆盖的细节不会因客户端兼容实现而自动冻结。

## 目标与项目分工

CodeHarness 用于研究更强 LLM 的求解收益，以及 Judge Feedback、重试、修改、模型路由和 Agent Loop 的收益。V1 是基于状态机的单 Agent，重点是能执行、记录并比较实验。

| 项目 | 运行环境 | 职责 |
| --- | --- | --- |
| CodeHarness | macOS | CodingAgent / Harness、模型配置与路由、状态推进、工具调用、Workspace、Context、Trace、CLI 和实验 |
| MiniOJ | Ubuntu / WSL Ubuntu | 独立 Web、用户与权限、题目、测试数据、编译执行、正式评测和 HTTP JSON API |

正式 verdict 由 MiniOJ 生成，CodeHarness 读取并记录，模型 REVIEW 不替代评测。CodeHarness 只通过 HTTP JSON API 和 Bearer Token 访问 MiniOJ：不 import 其内部模块或 schema 包、不共享数据库 / testcase / 运行时对象、不通过 SSH、WSL、Docker 或远程 Shell 绕过接口。

本地文件工具只管理任务 Workspace，不赋予本地编译、执行用户解答或管理远程服务器的职责。V1 不做 Multi-Agent、完整长期记忆、完整自动 stress testing 或本地 Judge；完整断点恢复与跨任务复用也未确定。

## 仓库现状与证据边界

以下结论来自当前工作区文件、Phase 0/1 本地验证及单列的 Phase 1 MiniOJ 联调，包含未提交内容。真实 AC 只验收固定解答链，不等于模型联调成功，也不自动验收后续 Phase。

| 证据入口 | 静态检查结果 | 对独立项目的影响 |
| --- | --- | --- |
| [pyproject.toml](../pyproject.toml) | wheel 只构建 `agent` / `experiments`，注册 Agent / OJ Client / Report 三个客户端入口；运行依赖为 HTTP、dotenv、YAML | `uv sync`、构建和 wheel 内容检查通过；默认安装不携带 MiniOJ 服务端 |
| [.env.example](../.env.example)、[.gitignore](../.gitignore)、[agent/config.py](../agent/config.py) | 环境样例只含客户端槽位；`ClientSettings` 校验 URL 并隐藏 Token repr；根工件规则为 `/workspace/` | endpoint / Token / Provider 仍由使用者选择；真实 `.env` 被忽略，`agent/workspace/` 未被误伤 |
| [agent/oj_client/client.py](../agent/oj_client/client.py)、[types.py](../agent/oj_client/types.py) | 五个协议方法均返回客户端自有类型；Bearer、成功状态、JSON、核心字段、错误分类和轮询已封装 | MockTransport 已验证；真实 `t1001` 链已验证题面、提交、轮询和 AC feedback，其他 verdict / 失败 schema、重试/去重与实际 Feedback Mode 仍待确认 |
| [agent/tools/runtime.py](../agent/tools/runtime.py) | 五个 OJ 操作、三个文件操作及内部轮询 helper 均有参数 schema、校验和关联 Trace | Tool Call 不等于模型原生 Tool Call；U06 仍未确定 |
| [agent/workspace/task.py](../agent/workspace/task.py) | 原子写入、托管文件保护、显式覆盖、越界/绝对路径/符号链接拒绝、带任务/阶段/关联 ID 的脱敏 Trace | Phase 1 文件边界已确定；完整断点恢复与跨任务复用仍未实现 |
| [agent/models/types.py](../agent/models/types.py)、[provider.py](../agent/models/provider.py)、[registry.py](../agent/models/registry.py)、[router.py](../agent/models/router.py) | Profile / Role、响应类型、兼容 Provider、配置解析与路由已存在 | 真实 endpoint / 模型可用性未验证；缺失价格按零处理、usage 缺失按零处理等需与实验口径对齐 |
| [agent/core/policy.py](../agent/core/policy.py) | 已有混合 Role 默认映射与 DEBUG 升级代码 | 当前升级条件是硬编码；阈值、失败类型与启用方式仍需确认，不直接沿用为正式默认 |
| [agent/core/agent.py](../agent/core/agent.py)、[context.py](../agent/core/context.py) | 已有两种 mode、独立 ContextBuilder，提交轮询已委托 OJClient | 无 DEBUG → PLAN；非 AC 仍共用反馈 / DEBUG 路径，Phase 3/4 异常终态与 IE 分流尚未完成 |
| [agent/cli.py](../agent/cli.py) | 现有解析器提供 `code-only` / `harness-loop` 子命令 | 规划中的 `codeharness-agent solve` 尚无对应子命令，后续需明确参数与兼容方式 |
| [experiments/summarize.py](../experiments/summarize.py) | 按 mode / variant 扫描 State，汇总并输出 JSON | 尚非批量执行器，未见 CSV 导出；分组条件不足以保证模型、反馈和预算可比 |
| [client_tests](../client_tests) | 默认入口独立于服务端 conftest；覆盖协议、错误、轮询、Workspace、Tool、Trace、固定解答 HTTP 链、CLI 防误提交与既有候选回归 | Phase 0/1 共 55 项本地测试通过；另有一次真实 AC 联调，二者均不算 Phase 3/4 验收 |

`oj/` 与 `shared/judge.py` 是当前混合工作区中的旧边界内容，不是本规划要继续开发的 CodeHarness 模块。它们及用户原有未提交改动继续保留，但已从 wheel、默认依赖、命令入口和 Phase 0 测试入口排除。客户端源码 AST 检查无 `oj` / `shared` import；这只证明代码边界，不证明 HTTP 联调。

现有代码还包含具体次数、超时、截断长度、Provider 参数及价格占位值。这些是待核验的实现选择，不能替用户解决下文未定项；本文不把它们确认为产品默认值。

## Phase 0 已确认的架构决策

1. **发行边界：** 当前继续复用 `agent/` 与 `experiments/` 包名，不为目录美观机械迁移；CodeHarness sdist / wheel 都不包含 `oj` / `shared`，也不注册服务端命令。
2. **配置边界：** 客户端统一从 `ClientSettings` 读取 `OJ_BASE_URL`、`OJ_API_TOKEN`、`MODEL_CONFIG`；Provider endpoint / key 仍由模型 Registry 按显式配置引用。服务端数据库、Session、Docker、Worker 设置不进入客户端环境样例。
3. **类型所有权：** HTTP 类型定义在 `agent/oj_client/types.py`，模型响应定义在 `agent/models/types.py`，State / Trace 事件定义在 `agent/workspace/task.py`；不使用 MiniOJ Python schema 包。
4. **协议演进：** 只对已约定核心做最小类型校验；额外字段原样保留，未知 status / verdict / event 不提升为已知枚举。未确认的反馈细节和错误结构由 fixture manifest 明示为 draft / unconfirmed。
5. **测试边界：** 默认 `pytest` 只收集 `client_tests/`，不加载旧服务端 `tests/conftest.py`。其中可复用已有 Fake / MockTransport 测试，但输出必须标为替身验证。
6. **工件与源码：** `/workspace/` 是根目录运行工件；`agent/workspace/` 是可跟踪源码。真实 `.env` 保持忽略。

以上决定覆盖 Phase 0。Phase 1 在下节继续确认客户端内部行为；具体远程字段、服务端重试/去重支持、模型 / 价格、预算、升级阈值和 Loop 转换仍由未定项管理。

## Phase 1 已确认的客户端决策

1. **响应与错误：** `get_problem`、`run_code`、`submit_solution`、`get_submission`、`get_feedback` 验证约定成功状态、JSON object 和最小核心字段；失败分类为 transport、HTTP、protocol、result_unknown。错误对象不保存 Authorization，也不允许自动重试。
2. **提交风险：** 正式提交 POST 超时一律视为远程创建状态未知；客户端不自动补发。服务端查重 / 幂等键未确认前，调用方必须人工恢复。
3. **轮询终态：** 轮询由 OJClient 的 `wait_for_submission` 负责，deadline 与 interval 必须由调用方显式提供。只接受机器字段 `status=FINISHED` 加已知 verdict；不解析 summary。未知 status / verdict 立即作为协议错误停止。
4. **结果分流：** AC 为 accepted；WA / CE / RE / TLE / MLE / OLE 为 user_program_failure；IE 为 remote_infrastructure_failure。传输或协议错误不伪装成任何 verdict。
5. **Custom Run 兼容：** 默认请求只发送约定字段 `source_code`；旧混合仓库使用过的 `code` 别名必须通过 `send_custom_run_code_alias=True` 显式开启。失败状态、同步模型和资源限制仍属 U02。
6. **Workspace 写入：** 新任务目录不得覆盖；内部 State / JSON 使用同目录临时文件加 `os.replace`；文件工具默认不得覆盖，显式 `overwrite=true` 才可覆盖普通任务文件；工具禁止写 `task.json` / `state.json` / `events.jsonl`，并拒绝绝对路径、越界和符号链接。
7. **Tool 与 Trace：** 五个 OJ 方法和三个文件方法为协议工具；`wait_for_submission` 是客户端编排 helper。参数先按 ToolSpec 校验；每个调用生成 correlation id。Phase 1 Trace 使用 `phase1-v1`，含 event id、UTC timestamp、task、phase、type、payload，并对 token/key/Authorization/Bearer 值脱敏。
8. **验证分层：** `run_fixed_solution` 和 `codeharness-oj-client submit-fixed` 提供不调用模型的真实链路入口；本地 MockTransport 证据与真实 MiniOJ 提交证据分别记录。远程命令必须显式 `--confirm-submit`。
9. **真实链路证据：** 2026-10-01 使用 `t1001` 固定 C++ 解答创建提交 `sub_FhqFt4PN68kPAJ7N`，服务端返回 `QUEUED`，轮询得到 `FINISHED / AC`，feedback 为 5/5 tests；任务 State 记录一次提交、零次 LLM 调用，工件位于 `workspace/phase1-live-t1001-20261001/`。

该真实链路确认了 U01 的 AC 最小成功形态，但没有确认 U01/U02/U04 的其余远程字段，也没有为 U03 选择项目级超时/轮询默认值；联调所用 15 秒 HTTP timeout、1 秒 polling interval 和 120 秒 deadline 只是本次命令参数，不是产品默认值。

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
  models/          # registry、router、types、provider
  tools/           # runtime 与文件 / OJ 工具注册
  oj_client/       # HTTP 客户端及 CodeHarness 自有类型
  workspace/       # Workspace / State / Trace 源码
config/
experiments/       # 当前汇总；批量 Runner 尚未实现
client_tests/      # 默认客户端测试与协议 fixture
workspace/         # 每任务工件，不是代码模块
oj/、shared/、tests/ # 保留的旧混合仓库内容，不进入客户端 wheel / 默认测试
docs/architecture.md
TODO.md
.env.example
.gitignore
pyproject.toml
README.md
```

不得为 CodeHarness 新增 `oj/server/`、`oj/worker/` 或 `docker/`，也不依赖 MiniOJ 仓库内的 Python 类型包才能运行。

## 模型层：Provider、Profile、Role

Provider 是服务接入方式。V1 沿用百炼方向，通过通用 `OpenAICompatibleProvider` 封装兼容调用，配置提供 base_url、API Key 引用、真实模型 ID 和已确认支持的参数。尚未选择或验证真实 endpoint / 模型，不能用占位符宣称可调用。

Profile 是可替换的逻辑别名：`fast`、`standard`、`strong`、`max`。真实模型名只放配置，不进入 Loop 或业务分支；Profile 名不构成某个模型能力的已验证结论。`max` 不要求 V1 必须调用，也不要求提前实现全部升级路径。

Role 是同一个 Agent 当前承担的任务，默认映射保持如下：

| Role | 默认 Profile |
| --- | --- |
| PLAN | strong |
| CODE | standard |
| TEST_GENERATION | strong |
| DEBUG | standard |
| REVIEW | strong |

ModelPolicy 保留 DEBUG 多次失败后由 standard 升为 strong 的能力，但阈值、适用失败类型和默认启用方式待确认；不能把源码中的固定次数视为本规划的确定值。

```text
CodingAgent → ModelPolicy → 逻辑 Profile → ModelRouter
           → ModelRegistry → Provider Adapter → 百炼 / 其他 Provider
           ← 内部 LLMResponse
```

Registry 管配置，Router 找 Provider 和模型，Adapter 发请求并转换结果。内部 `LLMResponse` 应足以支持正文、调用状态、usage 和 Trace；准确字段、失败表示、缺失 usage 和原生 Tool Call 表示需先明确。现有响应有 content / usage / provider / model / profile / request_id，可作为基础，不将供应商 SDK 对象或散乱 dict 传遍上层。

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
    provider: another_provider
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

OJClient 是访问 MiniOJ 的唯一正式入口，提供上述五个协议方法以及内部轮询 helper。它封装 Bearer、成功 HTTP 状态、JSON object、响应类型、deadline 轮询和安全错误映射；不自动重试。连接失败、HTTP / 协议错误、结果未知、IE 与 WA / CE / RE 等程序结果分别表示。提交 POST 超时会抛出 `OJResultUnknownError(submission_state_unknown=True)`，在查重 / 幂等规则未确认时不得盲目重复 POST。

MiniOJ 不可用时使用协议 fixture / MockTransport 验证客户端与固定解答链；它们只用于测试，不成为本地 OJ。Custom Run 默认只发送 `source_code`；`code` 是显式兼容开关，其存在不表示远程双方已冻结该扩展。

V1 可使用固定状态机调度工具；是否允许模型原生 Tool Call 仍需结合 Provider 能力和范围确认，不因此引入多 Agent 或复杂框架。

## Workspace、State 与 ContextBuilder

每个任务独立保存：

```text
workspace/<task-id>/
├── task.json       # 本次任务配置
├── problem.md      # 实际使用的题目表示
├── solution.cpp    # 当前候选
├── state.json      # 当前状态与计数
├── events.jsonl    # 事件序列
└── artifacts/      # 候选版本、提交反馈、测试结果等
```

State 至少包含 task id、problem id、current phase、attempt count、submission count、last verdict、current model profile、LLM call count、token usage，并能引用当前代码和结果。State 与 Trace 持久化是 V1 要求；完整断点恢复、长期记忆及跨任务复用不是自动附带的交付物。

Phase 1 已确定文件边界：工具只接受任务根目录下相对路径，拒绝绝对路径、越界和任何符号链接；默认不覆盖普通文件，覆盖必须显式请求；`task.json`、`state.json`、`events.jsonl` 是工具不可写的托管文件。内部写入使用同目录临时文件、fsync 与 `os.replace`。当前 `solution.cpp` 在后续 Loop 中仍会显式覆盖，完整候选版本与结果关联留给 Phase 4。真实密钥不能写入 task.json、State、Trace 或普通日志。

ContextBuilder 是独立模块，按阶段选择 Problem、Current phase、Current solution、Recent judge feedback、Relevant recent history，可加入计划和样例。使用有限近期内容，不无限追加完整聊天；具体长度、裁剪顺序和保留策略待确认，不把现有字符截断值当成已批准预算。

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
```

Phase 1 的 `phase1-v1` 事件包含 event id、UTC timestamp、task id、phase、type 与 payload；Tool Call / Result 共享 correlation id，提交和评测事件关联 submission id。TraceWriter 对敏感 key 和 Bearer 文本统一脱敏。代码版本、LLM call、终止原因和 submission 的完整关联仍在 U14 / Phase 2–4 中继续扩展。

LLM 总调用数、各 Profile 调用数、输入 / 输出 tokens、提交数、调试轮数和总耗时应能与 Trace 对照。现有 `_model_call` 在响应成功后才增加计数，调用失败的记录与统计口径需补齐；缺失 usage 不能未经说明等同真实零消耗。不得只保存最后一个 verdict，也不得将失败调用隐藏在自动重试中。

## 两种运行模式与状态机

### code-only：一次生成基线

```text
获取清洗后的题目 → 指定 Profile 的一次 CODE 调用 → 保存 solution.cpp
              → 正式提交一次 → 查询最终 verdict → 记录结果并结束
```

正常任务恰好一次模型调用和一次正式提交；不额外调用 PLAN / DEBUG / REVIEW / TEST_GENERATION，不样例试跑或 Custom Run，不基于反馈改码或再次生成，不在自动重试中生成多个候选却只统计最后一次。

查询完成状态、读取最终 verdict 用于统计是允许的，这不等于向模型回传 Judge Feedback。格式异常、代码提取失败、HTTP / 模型异常等应如实记录，不能隐式重生成；异常终态和计数口径见 U12。异常任务可能尚未成功生成或提交，不应假报已执行。

现有 `run_code_only` 已有正常路径；对应替身断言已纳入 Phase 0 客户端套件并通过。它只验证候选正常路径，当前异常直接抛出等行为仍不能当作 Phase 3 完整验收。本基线研究一次性 Coding 能力，不承诺排除题库记忆等因素；本规划未扩展数据污染研究范围。

### harness-loop：反馈驱动的单 Agent

状态集合为 `PLAN / CODE / TEST / DEBUG / REVIEW / DONE`，不是要求每次逐项经过的单向流水线。

```text
PLAN → CODE → TEST
  ↑      ↑      │
  │      │      └── 可用于调试的失败反馈 → DEBUG
  │      └────────────────────────────────┘
  └──────── DEBUG 可触发重新规划

REVIEW → DONE
（进入 REVIEW 的具体条件、异常终止和预算处理需先明确）
```

| 阶段 | 职责 |
| --- | --- |
| PLAN | 理解题目，形成或修订计划 |
| CODE | 生成或修改当前解答 |
| TEST | 通过 MiniOJ 工具测试或正式评测，整理机器结果 |
| DEBUG | 消费可见结构化反馈，定位问题并推动修改或重新规划 |
| REVIEW | 审查解答与结果，不以模型意见代替 MiniOJ verdict |
| DONE | 保存终态、工件、Trace、结束原因与实验结果 |

必须允许 `DEBUG → CODE` 和 `DEBUG → PLAN`。反馈可用于重试、代码修改与模型路由；升级逻辑由独立 ModelPolicy 负责。TEST_GENERATION 是保留 Role，不要求每次 TEST 都调用模型生成数据。

TEST 中样例、Custom Run、正式提交的顺序和使用策略尚待确认。Custom Run 的 `OK` 表示运行状态，不等于样例输出正确或正式 AC；输出比较依据也需明确。当前 `_run_samples` 只收集 status 后继续正式提交，这是现有行为，不是冻结的 TEST 策略。

CE / WA / RE / TLE / MLE / OLE、IE、客户端错误与模型通信错误应分流；IE 不能直接视为算法失败。模型可使用协议允许的编译诊断，不通过 Docker 日志、宿主路径或 Worker traceback 猜测 verdict。

循环必须有明确转换、失败处理、预算与停止条件，不无限运行。重试、提交、调用、token 或时间等启用哪些约束及具体值仍未选定；当前循环已有尝试次数限制，但不能据此宣称所有预算和异常终态已完成。

REVIEW 触发、是否可修改代码、改码后如何重新评测仍待明确。当前实现是在 AC 或尝试耗尽后调用 REVIEW，只写 review.md、不改解答；这是候选实现，不能替代方案确认。任何最终 AC 必须对应实际被 MiniOJ 评测的版本，不能沿用旧 verdict 为新代码背书。

## 实验模式、策略标注与指标

保留四组比较目标：

| 比较目标 | 需明确的实验解释 |
| --- | --- |
| standard + code-only | 一次 CODE 使用 standard；记录实际 Provider / 模型映射 |
| strong + code-only | 一次 CODE 使用 strong；记录实际 Provider / 模型映射 |
| standard + harness-loop | 可能指 CODE / DEBUG 使用 standard，也可能指所有模型阶段均 standard；尚未确认 |
| strong + harness-loop | 也需明确所有 Role 的映射与升级规则，不能仅用一个 Profile 标签概括 |

已约定的混合默认策略保持不变：PLAN / REVIEW / TEST_GENERATION 使用 strong，CODE / DEBUG 使用 standard，并保留 DEBUG 升级能力。正式实验前需确认固定模型策略与混合模型策略的命名、是否分别运行；不能把混合结果描述成“所有阶段只使用 standard 的 Harness 收益”。

Experiment Runner 以配置批量运行题目，复用同一 Agent、OJClient、Workspace 和记录逻辑。实验配置应保留 mode、Profile → Provider / 模型映射、Role 策略及升级设置、实际反馈模式、预算、题目集合、重复次数、测试生成是否启用等条件；具体 schema 和比较规则待明确。

至少输出以下逐任务指标及可核查的 JSON / CSV：

| 指标 | 解释边界 |
| --- | --- |
| solved / unsolved、final verdict | verdict 来自 MiniOJ；错误 / 结果未知不伪造成用户程序 WA |
| LLM calls、calls per model profile | 与调用 Trace 对账；失败调用口径需明确 |
| input tokens、output tokens | 来自可得的 usage；缺失表达待定 |
| estimated cost | 依确认的模型价格、usage、计费单位与币种计算；缺失价格不等于免费 |
| OJ submissions、debug iterations | 与实际提交、调试及去重处理对应 |
| wall clock time | 记录任务耗时，异常与中断任务的统计口径待明确 |

full 可暴露失败 hidden testcase，必须与 verdict_only 分开标注实验条件。服务端实际生效模式的获取方法仍待确认，客户端期望值不等于实际模式；未知条件不可伪称已核实。结果分组需要模型 / Role / 反馈 / 预算等可比条件，现有 mode / variant 汇总只是基础。

真实模型、价格、题目集合、重复次数与预算尚未选定；Phase 0 不运行批量实验，也不产生任何真实成本估算或联调成功结论。

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

Profile、真实模型名、Role 策略和已确认参数放配置文件，具体文件名可采用 models / providers / policies YAML，但不是强制 API。未来 Provider 的密钥配置按实际接入补充，不预填真实值。真实 `.env` 必须被忽略，不将密钥或 Token 写入日志 / Trace。

CodeHarness 不需要 MiniOJ 的 DATABASE_URL、SECRET_KEY、Docker image、宿主路径等配置。Phase 0 已从 `.env.example` 删除这些服务端项，并把真实 endpoint 保持为空。

CLI 目标至少表达单题的 problem、mode、Profile 或 policy、Workspace，以及实验配置和批量题目输入。沿用 `codeharness-agent solve` 作为规划中的命令名，完整参数在实施时确认；它不是当前可直接运行的命令。

当前安装入口包括模型无关的 `codeharness-oj-client`、候选 `codeharness-agent code-only` / `harness-loop` 及 `codeharness-report`。OJ Client 提供只读 `inspect-problem` 和需要 `--confirm-submit` 的 `submit-fixed`；后者要求显式 HTTP timeout、poll interval 与 deadline。两种 Agent 求解模式仍是后续阶段候选实现。

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

V1 默认模式是 `full`。模式和访问权限由 MiniOJ 服务端控制，CodeHarness 不能自行提高反馈权限。普通 API、专用 Feedback API 和 Web 页面均应遵守服务端的信息暴露规则。

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

## 未定项、验证层次与后续顺序

以下编号与 [TODO.md](../TODO.md) 的未定项对应，不在本文补未经确认的默认值：

| 编号 | 实施前需明确 |
| --- | --- |
| U01 | 已确认 `QUEUED` 创建响应、`FINISHED / AC` 查询和 AC feedback 最小 schema；完整查询 schema、进行中字段省略 / null、未完成 feedback、其他 verdict 与 HTTP 错误结构仍待确认 |
| U02 | Custom Run 交付方式、失败状态、资源限制与现有 `code` 别名兼容；不擅自扩展轮询 API |
| U03 | 请求超时、轮询间隔 / 期限、可重试错误、提交去重 / 查重及结果未知的恢复处理 |
| U04 | diagnostic 白名单、实际 Feedback Mode 获取、多 testcase 时间 / 内存汇总与单位含义 |
| U05 | 各 Profile 对应 Provider、真实 endpoint / 模型、支持参数、调用限制 |
| U06 | 固定工具调度或原生 Tool Call；内部 LLMResponse 的状态 / 错误 / usage / 工具字段 |
| U07 | DEBUG 升级阈值、失败类型、默认启用方式及重新规划条件 |
| U08 | 每任务预算的约束类型、数值及停止规则 |
| U09 | TEST 的样例 / Custom Run / 正式提交顺序、比较依据与失败策略 |
| U10 | REVIEW 触发、是否改码、重新评测与预算关系 |
| U11 | Context 裁剪 / 保留；Workspace 访问 / 越界 / 符号链接 / 覆盖规则；是否需要完整恢复 |
| U12 | code-only 与其他异常终态、失败调用 / 提交、中断和未知结果的统计 |
| U13 | 固定 / 混合策略命名、题目集合、重复次数、公平条件、价格 / 币种 / 计费与未知成本 |
| U14 | 事件 schema、关联标识、usage 缺失与脱敏；实验配置 schema 和分组键 |

验证分三层记录：静态文件检查只说明内容存在；测试替身检查协议处理与 Loop 行为；真实 MiniOJ / 模型调用检查实际接入。不同层次不能互相冒充。业务状态以 TODO 中的实现与验收证据为准，不以历史 README 的完成宣称推断。

Phase 0 与 Phase 1 已完成，后者包含本地验证和单列的真实 MiniOJ AC 固定解答链。随后推进 Phase 2 模型层、Phase 3 一次生成基线、Phase 4 反馈循环、Phase 5 实验与验收；U01–U14 中未由本文明确确认的事项不能从候选代码或一次成功联调反推。
