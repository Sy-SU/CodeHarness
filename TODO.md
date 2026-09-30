# CodeHarness 独立项目开发计划

规划依据：用户提供的《CodeHarness：独立 Agent 项目规划与 Codex 执行提示词》。2026-10-01 已完成 Phase 0 与 Phase 1（含真实 MiniOJ 固定解答联调）；Phase 2–5 仍按依赖顺序实施。

CodeHarness 在 macOS 上负责单 Agent、模型路由、工具、Workspace、Trace 和实验；MiniOJ 是独立部署于 Ubuntu / WSL Ubuntu 的远程评测服务。两个项目仅通过 HTTP JSON API 和 Bearer Token 交互。本计划的阶段编号与 MiniOJ 无关。

## 状态与证据规则

- `[x] [文档]` 只表示文档条目完成；`[x] [实现/验证]` 必须同时有实现和本文件记录的验证证据。
- `[ ] [实现/验证]` 可以已有候选代码；只有实现符合独立项目边界且具有相应验证证据后才勾选。
- `[ ] [联调]` 单独记录真实模型 / MiniOJ 验证；测试替身、源码存在和历史 README 宣称都不能代替联调。
- Phase 0/1 本地验证只使用构建、fixture、Fake 与 MockTransport；Phase 1 另以一次固定解答正式提交验证真实 HTTP 链。全程未调用付费模型、未在本地执行解答、未开发 MiniOJ 服务端。

### 2026-10-01 仓库检查摘要

| 范围 | 已看到的证据 | 状态与后续缺口 |
| --- | --- | --- |
| 包与配置 | `pyproject.toml`、`.env.example`、`.gitignore`、`uv.lock` | wheel 只包含 `agent` / `experiments`，只注册三个客户端入口；默认依赖树无服务端依赖；环境样例只保留客户端槽位 |
| OJClient / 工具 | `agent/oj_client/`、`agent/tools/runtime.py` | 五个类型化 HTTP 方法、OJClient 轮询、安全错误分类、ToolSpec 校验和固定解答工作流已通过 MockTransport；`t1001` 真实固定解答链得到 AC，未覆盖的远程 schema 仍单列 |
| Workspace / State / Trace | `agent/workspace/task.py` | 原子写入、托管文件保护、显式覆盖、越界/符号链接拒绝、任务/阶段/调用关联和脱敏已验证 |
| 模型层 | `agent/models/`、`agent/core/policy.py` | Profile、Provider、Router、Policy 已有代码；升级阈值、调用参数和缺失价格处理不能直接视为已确认方案 |
| 两种模式 | `agent/core/agent.py`、`agent/core/context.py` | 已有 code-only 与循环路径；DEBUG → PLAN、异常终态、预算、版本追踪等仍有缺口 |
| 实验 | `experiments/summarize.py` | 已有按 mode/variant 汇总 State 的 JSON 报告；尚非批量 Experiment Runner，未见 CSV 输出 |
| 测试 | `client_tests/` | 默认 pytest 入口只收集客户端测试；Phase 0/1、固定链、CLI 防误提交与既有候选回归共 55 passed |
| 旧边界 | Git 历史中的 `oj/`、`shared/`、`tests/` | 旧内嵌 MiniOJ 服务端、共享 Judge 与服务端测试已在 Phase 1 后清理；需要时从 `ec73c05` 历史恢复，不进入当前客户端仓库 |

源码细节、现有实现与目标差异见 [architecture.md](docs/architecture.md)。Phase 0/1 完成时未混入服务端改动；随后按用户确认清理了仓库中剩余的旧 `oj/`、`shared/` 与服务端 `tests/`，进一步收敛独立客户端边界。

## Phase 0：项目基础与协议整理

**目标：** 建立可独立安装、测试和继续开发的 macOS 客户端项目，整理协议草案与模块边界。

**依赖：** 当前仓库检查；用户提供的 MiniOJ HTTP 约定。不要求远程服务或真实模型在线。

- [x] [文档] 检查 README、旧 TODO、架构、Agent、配置及测试源码，区分现有文件与验证状态。
- [x] [文档] 将任务重排为 CodeHarness 自己的 Phase 0–5；排除 OJ Server、用户系统、题库数据库、Worker 和 Sandbox 的开发任务。
- [x] [文档] 记录模型层、状态机、Workspace、Context、Trace、两种 mode、HTTP 样例及未定项。
- [x] [文档] 明确禁止 import MiniOJ 内部模块、共享数据库 / testcase、启动本地 Judge 或通过远程 Shell 绕过 HTTP。
- [x] [实现/验证] 整理包入口、构建清单与依赖，使 CodeHarness 安装和运行不要求携带 MiniOJ 服务端；复用现有 `agent/` / `experiments/`，sdist 与 wheel 均不含 `oj` / `shared`。
- [x] [实现/验证] 新增统一 `ClientSettings`，清理 `.env.example`，只保留客户端 / 模型槽位；真实 endpoint、Provider 与模型保持待选。
- [x] [实现/验证] 将任务工件规则收窄为根目录 `/workspace/`，确认 `agent/workspace/` 源码未被忽略；真实 `.env` 仍被忽略。
- [x] [实现/验证] 在客户端内部定义题目、提交、反馈、错误、模型响应、State 和事件类型；客户端源码静态检查无 `oj` / `shared` import。
- [x] [实现/验证] 将协议样例转为 `client_tests/fixtures/protocol/`；manifest 显式标记草案等级，解析器保留未知字段并把未知状态 / verdict 与已知枚举区分。
- [x] [实现/验证] 默认 pytest 入口只收集 `client_tests/`；旧服务端 `tests/` 已在边界清理中移除，包、配置、fixture 及已有 Fake/MockTransport 候选测试独立通过。
- [x] [实现/验证] 同步 README / README_zh 与 Agent、配置、实验说明，记录独立分工、macOS 起步、现有命令和未验证边界。

**交付物：** 独立包与配置入口、非秘密环境样例、核心类型、协议 fixture、基础测试入口、更新后的起步文档。

**验收标准：** 从干净检出可加载客户端包和配置、执行基础测试；源码未被工件规则误忽略；无需 MiniOJ 内部模块或服务端运行配置；无真实密钥进入版本控制。文档完成不代表此阶段业务验收已通过。

### Phase 0 验证记录（2026-10-01）

| 命令 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv sync --extra dev --offline` | 成功；按新 lock 移除 FastAPI、SQLAlchemy、Uvicorn 等 24 个旧服务端/间接包，只安装客户端及 dev 依赖 |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | `20 passed`；只收集 `client_tests/`，包含协议、配置、导入边界和既有 Fake/MockTransport 回归 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline` | 成功生成 sdist 与 wheel |
| `tar -tzf dist/codeharness-0.1.0.tar.gz`、`unzip -l dist/codeharness-0.1.0-py3-none-any.whl` 与 entry point 检查 | sdist / wheel 均为纯客户端；wheel 只有 `agent`、`experiments` 和 metadata，当前入口为 Agent / OJ Client / Report |
| 解包 sdist 后从其根目录运行 `python -m pytest` | `20 passed`，确认发布源码自带的客户端包、fixture 与测试入口可独立加载 |
| Python 3.9.6 `compileall` | `agent` / `experiments` 全部通过语法编译 |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync codeharness-agent --help` 与 `codeharness-report --help` | 两个已安装客户端入口均可加载并显示帮助 |
| `git check-ignore -v .env workspace/example/state.json` 及对 `agent/workspace/*.py` 的反向检查 | `.env` 和根任务工件被忽略；两个 Workspace 源码文件未被忽略 |

Phase 0 未完成事项：无。真实 MiniOJ / 模型联调、完整 HTTP schema / 错误处理、模型与价格、预算和 Loop 规则分别保留在 Phase 1–5 与 U01–U14，不能由本阶段的替身测试推出。

## Phase 1：OJClient、Workspace 与工具路径

**目标：** 不依赖模型，打通题目输入、任务工件和 MiniOJ HTTP 执行路径。

**依赖：** Phase 0；相关 HTTP 未定项（U01–U04）与文件 / 事件规则（U11、U14）；真实联调依赖 MiniOJ 对应 API 可用。

- [x] [实现/验证] 完善 `OJ_BASE_URL` / `OJ_API_TOKEN` 配置、Bearer 认证、成功 HTTP 状态 / JSON / 核心字段校验及安全错误映射。
- [x] [实现/验证] 类型化验证 `get_problem`、`run_code`、`submit_solution`、`get_submission`、`get_feedback`；Custom Run 默认仅发 `source_code`，`code` 别名需显式开启。
- [x] [实现/验证] 将轮询与传输细节封装到 OJClient；deadline / interval 由调用方显式传入，只按机器字段接纳最终结果，不解析 summary。
- [x] [实现/验证] 区分 transport、HTTP、protocol、result_unknown、远程 IE 与用户程序失败；不自动重试，提交超时不盲目重复 POST。
- [x] [实现/验证] 验证逐任务 task/problem/solution/state/artifacts、State 原子保存；普通文件默认不覆盖，托管文件不可由工具写，拒绝绝对路径、越界与符号链接。
- [x] [实现/验证] ToolSpec 定义参数并由 Runtime 校验、执行、分类错误与回传；接入五个 OJ 方法、三个文件方法及内部轮询 helper。
- [x] [实现/验证] Trace `phase1-v1` 记录 event/task/phase/correlation/time；敏感 key 与 Bearer 值脱敏，测试确认不写 MiniOJ Token。
- [x] [实现/验证] fixture / MockTransport 覆盖正常结果、提前 FINISHED、未完成反馈、全部 verdict、认证失败、无效响应、未知状态、轮询超时及提交超时不重试。
- [x] [实现/验证] `run_fixed_solution` 验证“获取题目 → 保存固定解答 → 提交 → OJClient 轮询 → 记录反馈”，单独标为 MockTransport 模拟结果；未调用模型或本地执行代码。
- [x] [联调] 使用 MiniOJ `t1001` 与固定 C++ 解答运行 `codeharness-oj-client submit-fixed`；提交 `sub_FhqFt4PN68kPAJ7N` 得到机器终态 `FINISHED / AC`，5/5 测试通过，完整工件保存在 `workspace/phase1-live-t1001-20261001/`。未调用 LLM，也未在本地编译或执行解答。

**交付物：** OJClient、工具注册与执行路径、Workspace / State / 基础 Trace、协议与异常测试、单列的联调记录。

**验收标准：** 固定测试解答只通过 HTTP 交给 MiniOJ 执行；CodeHarness 不编译或运行解答，不访问远程 testcase；只接纳机器字段表示的最终结果；模拟测试与真实联调分别可核查。

### Phase 1 验证记录（2026-10-01）

| 命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | `55 passed`；Phase 0/1 全部客户端测试通过 |
| `client_tests/test_phase1_oj_client.py` | 五个方法、Bearer、规范/兼容 Custom Run、全部 verdict、提前 FINISHED、401、无效 JSON/schema、未知状态、未完成反馈、transport、deadline 与提交超时只发一次 POST 均通过 |
| `client_tests/test_phase1_workspace_tools.py` | 原子替换、覆盖规则、托管文件保护、越界/绝对路径/符号链接、九个 schema 化 Runtime 操作、Trace 关联和脱敏均通过 |
| `client_tests/test_phase1_workflow.py` | 固定源码 MockTransport 链保存 problem/source/state/submission/feedback/result/trace；断言未调用 Custom Run、本地 Judge 或模型 |
| `client_tests/test_phase1_cli.py` | 未给 `--confirm-submit` 或给出 NaN deadline 时在任何网络请求前停止 |
| `codeharness-oj-client --help` | 独立 HTTP CLI 可加载；真实提交子命令要求显式 timeout / interval / deadline 与 `--confirm-submit` |
| `env UV_CACHE_DIR=.uv-cache uv build --offline`，随后从 sdist 解包运行 `python -m pytest` | sdist / wheel 构建成功；解包后的独立客户端测试仍为 `55 passed` |
| sdist / wheel 内容、entry point 与 `uv.lock` 检查 | 产物无服务端源码、测试或依赖；只含三个客户端命令入口 |
| `test ! -e oj && test ! -e shared && test ! -e tests` 及发行包内容断言 | 旧内嵌服务端、共享 Judge、服务端测试和缓存目录均已移除；客户端测试与构建不受影响 |
| `codeharness-oj-client inspect-problem t1001 --http-timeout 15` | 通过 Bearer HTTP 读取清洗题面 `Two Sum`；返回题面、限制与公开样例，未创建提交 |
| `codeharness-oj-client submit-fixed t1001 /private/tmp/codeharness-phase1-t1001.cpp --workspace-root workspace --task-id phase1-live-t1001-20261001 --http-timeout 15 --poll-interval 1 --deadline 120 --confirm-submit` | 真实正式提交 `sub_FhqFt4PN68kPAJ7N`：`QUEUED → FINISHED / AC`，5/5 tests，feedback 与资源字段成功解析；`llm_call_count=0`、`submission_count=1` |
| `rg -n -i "authorization|bearer|api[_-]?token|secret|password" workspace/phase1-live-t1001-20261001` | 无匹配；任务、源码、提交创建/终态、feedback、result、State 与 `phase1-v1` 事件均已落盘且未包含凭据 |

Phase 1 未完成事项：无。真实 AC 只确认本次使用到的题面、正式提交、轮询和 AC feedback 最小路径；Custom Run、其他 verdict、错误响应、未完成 feedback 与服务端幂等仍保留为 U01–U04，不由一次成功联调外推。

## Phase 2：Provider、Registry、Router 与 Policy

**目标：** 通过逻辑 Profile 调用可替换模型，得到内部统一响应与可追溯记录。

**依赖：** Phase 0；Workspace / Trace 依赖 Phase 1；真实调用前明确 U05–U08、U13。

- [ ] [实现/验证] 明确 LLMProvider 接口和 LLMResponse 的正文、调用状态、usage、请求标识、错误及工具调用字段；评估复用现有 `ModelProvider` / `LLMResponse`。
- [ ] [实现/验证] 完善 `OpenAICompatibleProvider` 与百炼配置接入；将 endpoint、模型 ID 和已支持参数置于配置并校验。
- [ ] [实现/验证] 验证 Registry → Router → Adapter 的 fast / standard / strong / max 路由，缺失或错误映射应明确失败。
- [ ] [实现/验证] 独立配置 Role → Profile，保留 PLAN/REVIEW/TEST_GENERATION → strong、CODE/DEBUG → standard 的混合默认策略。
- [ ] [实现/验证] 将 DEBUG 升级做成按已确认阈值和失败类型生效的 Policy；不把当前硬编码次数当成正式默认值。
- [ ] [实现/验证] 记录 Provider、真实模型、Profile、调用结果、usage 和耗时；失败调用与缺失 usage 不得静默从统计中消失。
- [ ] [实现/验证] 用模型测试替身覆盖配置错误、响应归一化、调用失败、usage 缺失、策略升级与缺失价格场景。
- [ ] [联调] 后续有有效配置及调用授权时，验证选定模型的最小调用；本轮不调用、不消耗额度。

**交付物：** 模型配置、统一请求 / 响应、Provider / Registry / Router / Policy 与独立测试。

**验收标准：** 改配置即可切换真实模型，无需修改 Loop；供应商原始响应不流入上层；Role 不是子 Agent；模型调用可追踪；max 可配置但不强制调用；真实服务可用性与替身测试分别记录。

## Phase 3：Code Only 基线

**目标：** 实现一次生成、一次正式提交、无反馈修复的基线。

**依赖：** Phase 1、Phase 2；异常终态和统计口径 U12、统一 CLI 设计。

- [ ] [实现/验证] 使用清洗后的 Agent 题目接口构建 CODE 上下文，不从普通接口补入 rating、tags、题解或历史解法。
- [ ] [实现/验证] 指定 Profile 只调用一次 CODE，提取并保存 `solution.cpp`；保存该候选与调用的关联。
- [ ] [实现/验证] 正式提交一次，通过 OJClient 轮询最终 verdict，持久化结果并结束。
- [ ] [实现/验证] 为代码提取失败、模型异常、HTTP 失败、结果未知和 IE 保存明确终态与计数；不隐式重生成、修复或重复提交。
- [ ] [实现/验证] 对齐 State、Trace 与指标；正常任务精确记录一次模型调用和一次提交。
- [ ] [实现/验证] 设计并接入 `codeharness-agent solve` 的 mode / problem / Profile / Workspace 参数；记录现有子命令的兼容处理。
- [ ] [实现/验证] 覆盖成功与失败路径，断言不调用 PLAN / DEBUG / REVIEW / TEST_GENERATION、不 Custom Run、不向模型回传评测反馈。

**交付物：** code-only 模式、单题 CLI、任务工件、异常结果与计数测试。

**验收标准：** 正常任务恰好一次模型生成、一次正式提交，获取最终 verdict 后结束；异常如实记录，不能为了凑够一次提交而隐藏错误，也不能自动追加候选。读取 verdict 用于统计不等于反馈给模型。

## Phase 4：Harness Loop

**目标：** 单 Agent 按显式状态转换消费反馈、修改代码，并在确定的预算内结束。

**依赖：** Phase 3；转换、TEST / REVIEW、升级、预算与 Context 规则先明确（U07–U11）。

- [ ] [实现/验证] 定义并验证 PLAN / CODE / TEST / DEBUG / REVIEW / DONE 的转换、进入条件、失败处理和终止原因。
- [ ] [实现/验证] 支持 DEBUG → CODE 和 DEBUG → PLAN 回退，记录重新规划原因；不要求每轮机械遍历全部阶段。
- [ ] [实现/验证] 按确认策略调度样例 Custom Run 与正式提交；区分运行成功和输出正确，不把 Custom Run 的 OK 当成正式 AC。
- [ ] [实现/验证] 独立 ContextBuilder 选择题目、当前解答、近期反馈、计划与相关历史，并验证裁剪规则及任务隔离。
- [ ] [实现/验证] 将结构化反馈送入 DEBUG；所有模型选择与升级经过 ModelPolicy，不在 Loop 写真实模型名或升级阈值。
- [ ] [实现/验证] 按确认预算限制重试、提交、LLM 调用和时间等；测试每一种启用的停止条件，无无限循环。
- [ ] [实现/验证] 分流 WA / CE / RE / TLE / MLE / OLE、IE 与客户端 / 模型通信错误，避免基础设施失败触发算法修复。
- [ ] [实现/验证] 保存每次候选版本、反馈、Custom Run 结果、submission id、状态和模型变更，关联代码版本与最终 verdict。
- [ ] [实现/验证] 实现确认后的 REVIEW 规则；若允许改码，重新评测该版本，禁止把新代码标成旧提交的 AC。
- [ ] [实现/验证] 接入 harness-loop CLI；用受控失败覆盖 DEBUG、重新规划、升级、成功结束、预算耗尽和异常结束。

**交付物：** 状态机、ContextBuilder、策略 / 预算配置、版本工件、可关联 Trace、循环验收用例。

**验收标准：** 受控失败进入 DEBUG 后产生可追踪的后续候选和提交；允许回退且受预算约束；最终结论来自 MiniOJ，并对应被评测代码。REVIEW 不能用模型判断替代 verdict，不解析 MiniOJ 内部日志。

## Phase 5：实验执行与最终验收

**目标：** 复用同一 Agent / OJClient，以可比较配置运行两种模式并导出结果。

**依赖：** Phase 4；实际模型、反馈条件、预算、实验命名及统计规则已明确（U04–U05、U08、U12–U14）。

- [ ] [方案确认] 保留 standard / strong × code-only / harness-loop 四组比较目标；确认固定模型与混合 Role 策略如何命名、是否分别运行，不改动已约定的混合默认策略。
- [ ] [实现/验证] 定义实验配置与题目列表输入，保存 mode、Profile 实际映射、Role 策略、反馈条件、预算、题目集合与重复运行设置。
- [ ] [实现/验证] 实现批量 Experiment Runner，复用单题执行与记录逻辑；隔离每个任务的 Workspace，并保留失败任务结果。
- [ ] [实现/验证] 输出逐任务 JSON / CSV 与汇总：solved、final verdict、LLM calls、各 Profile calls、输入 / 输出 tokens、estimated cost、OJ submissions、debug iterations、wall clock time。
- [ ] [实现/验证] 将调用 / 提交 / 调试计数与 Trace 对账，明确中断、未知结果、未完成任务和未知 usage 的处理。
- [ ] [实现/验证] 记录服务端实际生效的 Feedback Mode，不能仅记录客户端期望值；full 与 verdict_only 作为不同实验条件。
- [ ] [实现/验证] 根据确认的价格、计费单位和币种估算成本；缺失价格或 usage 显式表示未知，不按免费调用汇总。
- [ ] [实现/验证] 覆盖两种 mode、固定 / 混合策略、反馈模式、批量失败与结果导出的替身测试，防止不可比任务混组。
- [ ] [联调] 取得后续模型 API / MiniOJ 使用授权并具备配置后，运行最小真实实验，保存配置、工件、Trace 与结果。
- [ ] [文档/验证] 补齐 README：项目分工、macOS 起步、地址 / Token / Provider 配置、Profile / Role、工具、Workspace / Trace、两种 mode、反馈模式、实验、结果与测试复现步骤。

**交付物：** Experiment Runner、配置 schema、题目列表、逐任务 / 汇总 JSON 与 CSV、实验测试和复现文档。

**验收标准：** 同一入口支持不同 Profile / Policy 和两种 mode；每个结果能回溯实际配置、代码版本、调用和 MiniOJ verdict；混合策略不标成“所有阶段只用 standard”；无隐藏调用、虚构成本或假报成功。

## 后续扩展：Test Generation 与 Stress Testing

此部分不是 V1 必须完成的 Phase，不阻塞 Phase 0–5 验收；V1 保留 `TEST_GENERATION → strong` Role，不表示默认实验已经启用。

- [ ] 在后续需求明确后，实现边界条件、极端输入、合法约束与复杂度弱点的测试生成。
- [ ] 明确输入合法性验证、expected 来源和 brute / oracle 的构造与校验；只有输入不等于已有可信判据。
- [ ] 保存生成测试，通过 MiniOJ Custom Run 受限执行，不新增本地执行器。
- [ ] 扩展 `candidate + generator + brute/oracle + stress test`，在实验配置和结果中标注是否启用。

## 待明确事项

以下是实施前决策清单，不是已经冻结的默认值；没有确认的模型、价格、次数、预算和协议字段不补猜测值。

| 编号 | 待明确内容 | 影响阶段 |
| --- | --- | --- |
| U01 | 真实联调已确认 `QUEUED` 创建响应、`FINISHED / AC` 查询结果及 AC feedback 最小 schema；其他 verdict、通用错误、进行中字段省略 / null 与未完成 feedback 仍待确认 | 1 |
| U02 | 客户端默认 `source_code`、`code` 为显式兼容开关；Custom Run 同步 / 异步交付、失败状态、资源限制及远程是否保留别名仍待确认 | 1 |
| U03 | 已确认客户端不自动重试、提交超时为 result_unknown、参数显式传入；项目默认值、可重试条件、服务端幂等 / 查重及恢复流程仍待确认 | 1、3–4 |
| U04 | diagnostic 字段白名单、服务端实际 Feedback Mode 的获知方式、多 testcase 耗时 / 内存汇总及单位含义 | 1、5 |
| U05 | fast / standard / strong / max 对应 Provider、真实 endpoint / 模型 ID、支持参数、调用限制 | 2、5 |
| U06 | 固定工具调度还是模型原生 Tool Call；统一 LLMResponse 的状态、错误、usage 与工具调用字段 | 2、4 |
| U07 | DEBUG 升级阈值、适用失败类型、是否默认启用；DEBUG → PLAN 的触发条件 | 2、4 |
| U08 | 每任务重试 / 提交 / LLM 调用 / token / 时间等预算，启用哪些约束及具体值 | 3–5 |
| U09 | TEST 中样例、Custom Run 和正式提交的顺序、输出比较依据与失败后策略 | 4 |
| U10 | REVIEW 触发时机、是否允许改码、改码后的重新评测与预算占用 | 4 |
| U11 | Workspace 访问、越界、符号链接、工具覆盖规则已确认；Context 长度 / 裁剪 / 保留及是否需要完整断点恢复仍待确认 | 4 |
| U12 | code-only 格式失败、通信错误、IE、结果未知等终态；失败调用 / 提交和中断任务的统计口径 | 3、5 |
| U13 | 固定 / 混合模型策略命名与分组、题目集合、重复次数、公平比较条件；价格配置、币种、计费口径和成本未知表达 | 2、5 |
| U14 | Phase 1 基础事件、任务 / 阶段 / Tool 调用关联与脱敏已确认；代码版本 / LLM / submission 完整关联、usage 缺失、实验配置 schema 与分组键仍待确认 | 2–5 |

## 下一步与本轮停止条件

Phase 1 已完成并停止。下一阶段是 Phase 2；开始真实模型联调前仍需确认 U05–U08、U13 中的 Provider、模型、响应、策略与成本口径。用户已授权本项目按需调用正式 MiniOJ HTTP API，包括创建测试或正式提交；该授权不改变“不得绕过 API 访问数据库、testcase 或远程 Shell”的架构边界。

本轮未调用付费模型，创建了且仅创建了上述一次远程正式提交，未开发 MiniOJ 服务端，也未把 U01–U10、U12–U14 中仍未确认的协议、模型、价格、预算、次数或实验规则自行确认为默认值。
