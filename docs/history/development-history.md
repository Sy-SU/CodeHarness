# CodeHarness 独立项目开发计划

规划依据：用户提供的《CodeHarness：独立 Agent 项目规划与 Codex 执行提示词》。Phase 0–5 实现/替身验收和 Dashboard 发起任务/额度已完成，历史 T1003 失败保留。2026-10-02 新增比赛测试：比赛 1 真实运行 1/2 AC、5 模型/1 POST，估算 0.0255813 元；GET me 已明确 full，实际 full/有效 verdict_only 分开记录。真实五组、正式失败反馈→修复→AC、供应商有效价格/上限和特殊判题样例仍未完成，见比赛扩展节；不追改旧工件。

CodeHarness 在 macOS 上负责单 Agent、模型路由、工具、Workspace、Trace 和实验；MiniOJ 是独立远程服务。评测通过 HTTP JSON API 和 Bearer Token；比赛题单暂无 JSON 读取接口，另适配公开 HTTP 页面，不读取任何服务端内部数据。本计划的阶段编号与 MiniOJ 无关。

## 状态与证据规则

- `[x] [文档]` 只表示文档条目完成；`[x] [实现/验证]` 必须同时有实现和本文件记录的验证证据。
- `[ ] [实现/验证]` 可以已有候选代码；只有实现符合独立项目边界且具有相应验证证据后才勾选。
- `[ ] [联调]` 单独记录真实模型 / MiniOJ 验证；测试替身、源码存在和历史 README 宣称都不能代替联调。
- Phase 0–3 本地验证使用构建、fixture、Fake 与 MockTransport；Phase 1 另以一次固定解答正式提交验证真实 OJ HTTP 链，Phase 2 另以一次短文本模型调用验证 Provider 链，Phase 3 另以一次严格 code-only 任务验证模型到正式评测的完整链。未在本地执行解答，也未开发 MiniOJ 服务端。

### 2026-10-01 仓库检查摘要

| 范围 | 已看到的证据 | 状态与后续缺口 |
| --- | --- | --- |
| 包与配置 | `pyproject.toml`、`.env.example`、`.gitignore`、`uv.lock` | wheel 包含 `agent` / `experiments` 与独立 `dashboard`，六个客户端入口；Web 依赖只在 optional extra；sdist 排除本地模型/预算配置，无 MiniOJ 服务端 |
| OJClient / 工具 | `agent/oj_client/`、`agent/tools/runtime.py` | 五个类型化 HTTP 方法、OJClient 轮询、安全错误分类、ToolSpec 校验和固定解答工作流已通过 MockTransport；`t1001` 真实固定解答链得到 AC，未覆盖的远程 schema 仍单列 |
| Workspace / State / Trace | `agent/workspace/task.py` | 原子写入、托管文件保护、显式覆盖、越界/符号链接拒绝、任务/阶段/调用关联和脱敏已验证 |
| 模型层 | `agent/models/`、`agent/core/policy.py` | 统一响应/错误、Provider、Registry、Router、调用 Runtime、Policy 与模型探针已验证；正式 Profile 映射/价格待选，Phase 4 单题提交/成本上限已确认 |
| 两种模式 | `agent/core/agent.py`、`agent/core/harness.py`、`agent/core/context.py` | code-only 单调用/单提交；harness 固定状态机、3 次 DEBUG 重 PLAN、每任务最多 10 POST/默认 1 元可调、样例优先/多版本/检查点恢复已通过替身测试 |
| 实验 | `agent/execution.py`、`experiments/{config,runner,results,cli}.py` | 同一单题实现，两模式/固定/混合、隔离任务、总及同题预算、冻结配置、安全恢复、JSON/CSV 与 Trace 对账；实际反馈 unknown 不冒充已确认 |
| 测试 | `client_tests/` | 默认只收集客户端测试；最新全量 242 passed，Phase 5 + 启动专项 53 passed；全部为本地替身 / ASGI，不产生模型或 OJ 请求 |
| 旧边界 | Git 历史中的 `oj/`、`shared/`、`tests/` | 旧内嵌 MiniOJ 服务端、共享 Judge 与服务端测试已在 Phase 1 后清理；需要时从 `ec73c05` 历史恢复，不进入当前客户端仓库 |

源码细节、现有实现与目标差异见 [architecture.md](docs/architecture.md)。Phase 0–3 未混入服务端改动；旧 `oj/`、`shared/` 与服务端 `tests/` 已按用户确认清理，保持独立客户端边界。

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
- [x] [实现/验证] 区分 transport、HTTP、protocol、result_unknown、远程 IE 与用户程序失败；不自动重试，提交超时或无法解释的 202 Accepted 响应不盲目重复 POST。
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
| sdist / wheel 内容、entry point 与 `uv.lock` 检查 | 产物无服务端源码、测试或依赖；当前含四个客户端命令入口 |
| `test ! -e oj && test ! -e shared && test ! -e tests` 及发行包内容断言 | 旧内嵌服务端、共享 Judge、服务端测试和缓存目录均已移除；客户端测试与构建不受影响 |
| `codeharness-oj-client inspect-problem t1001 --http-timeout 15` | 通过 Bearer HTTP 读取清洗题面 `Two Sum`；返回题面、限制与公开样例，未创建提交 |
| `codeharness-oj-client submit-fixed t1001 /private/tmp/codeharness-phase1-t1001.cpp --workspace-root workspace --task-id phase1-live-t1001-20261001 --http-timeout 15 --poll-interval 1 --deadline 120 --confirm-submit` | 真实正式提交 `sub_FhqFt4PN68kPAJ7N`：`QUEUED → FINISHED / AC`，5/5 tests，feedback 与资源字段成功解析；`llm_call_count=0`、`submission_count=1` |
| `rg -n -i "authorization|bearer|api[_-]?token|secret|password" workspace/phase1-live-t1001-20261001` | 无匹配；任务、源码、提交创建/终态、feedback、result、State 与 `phase1-v1` 事件均已落盘且未包含凭据 |

Phase 1 未完成事项：无。真实 AC 只确认本次使用到的题面、正式提交、轮询和 AC feedback 最小路径；Custom Run、其他 verdict、错误响应、未完成 feedback 与服务端幂等仍保留为 U01–U04，不由一次成功联调外推。

## Phase 2：Provider、Registry、Router 与 Policy

**目标：** 通过逻辑 Profile 调用可替换模型，得到内部统一响应与可追溯记录。

**依赖：** Phase 0；Workspace / Trace 依赖 Phase 1。一次性探针要求有效 Provider 配置、明确的一次调用授权和固定短提示；正式解题/实验仍需明确 U05–U08、U13。

- [x] [实现/验证] 明确 LLMProvider 接口和 LLMResponse 的正文、调用状态、可缺失 usage、请求标识、安全错误、结束原因、耗时及工具调用字段；供应商原始响应不流入上层。
- [x] [实现/验证] 完善 `OpenAICompatibleProvider` 与百炼兼容接入；endpoint / key 仅由环境引用，模型 ID、显式 timeout、支持参数及每 Profile 参数置于 YAML 并校验。
- [x] [实现/验证] 验证 Registry → Router → Adapter 的 fast / standard / strong / max 路由；缺失 Profile、未知 Provider、无效模型、参数、timeout 或价格均明确失败。
- [x] [实现/验证] 独立配置 Role → Profile，保留 PLAN/REVIEW/TEST_GENERATION → strong、CODE/DEBUG → standard 的混合默认策略。
- [x] [实现/验证] DEBUG 升级改为显式配置；默认关闭，只有同时配置阈值、失败类型和 from/to Profile 才生效，不再沿用硬编码次数。
- [x] [实现/验证] `ModelCallRuntime` 记录 Provider、模型、Profile、调用状态、usage、request id、耗时、成本状态与关联 ID；失败调用、缺失 usage 和未知价格均进入 State / Trace / 汇总。
- [x] [实现/验证] 模型测试替身覆盖配置错误、响应归一化、工具调用、HTTP/传输/协议失败、usage 缺失、策略升级、未知价格与 CLI 防误调用。
- [x] [联调] 使用用户授权的模型 API 运行一次 `codeharness-model-client probe`；临时将四个 Profile 映射到仅用于探针的 `qwen3.8-flash`，真实调用返回预期文本，usage 为 74/46 tokens。该临时映射不确认为正式实验策略。

**交付物：** 模型配置、统一请求 / 响应、Provider / Registry / Router / Policy 与独立测试。

**验收标准：** 改配置即可切换真实模型，无需修改 Loop；供应商原始响应不流入上层；Role 不是子 Agent；模型调用可追踪；max 可配置但不强制调用；真实服务可用性与替身测试分别记录。

### Phase 2 验证记录（2026-10-01）

| 命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | `73 passed`；包含 Phase 2 Provider、Registry、Router、Policy、Runtime、汇总及 CLI 专项测试 |
| `client_tests/test_phase2_models.py` | 成功/工具调用响应归一化、缺失 usage、429、transport、无效 schema、配置错误、四 Profile 路由、已知/未知成本、显式升级、成功/失败 Trace 与防误调用全部通过 |
| `env UV_CACHE_DIR=.uv-cache uv sync --extra dev --offline`、`codeharness-model-client --help` | 客户端依赖离线同步成功；新增模型探针入口可加载 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline`，随后从 sdist 解包运行 `python -m pytest` | sdist / wheel 构建成功；解包后的 73 项测试全部通过；wheel 只含客户端包并注册四个客户端入口 |
| `env PYTHONPYCACHEPREFIX=/private/tmp/codeharness-phase2-py39-cache python3 -m compileall -q agent experiments` | 系统 Python 3.9.6 语法编译通过；专用临时缓存避免写入用户级缓存目录 |
| 模型 API `GET /models` | HTTP 200；只读取可用模型 ID，未输出 API Key |
| `codeharness-model-client probe --profile fast --model-config /private/tmp/codeharness-phase2-models.yaml --workspace-root workspace --task-id phase2-live-qwen38-flash-20261001 --confirm-call` | 一次真实 `qwen3.8-flash` 调用成功，输出精确匹配 `CODEHARNESS_MODEL_OK`；request id 已记录，74 input / 46 output tokens，1441 ms |
| `workspace/phase2-live-qwen38-flash-20261001/` | `phase2-v1` 的 CALL/RESPONSE 共享 correlation id；State 为 1 call / 1 success / 0 failure，价格缺失明确记为 `missing_pricing` 与 `estimated_cost=null` |
| 对 Phase 2 工件扫描 Authorization / Bearer / key / token / secret / password，并与当前配置的模型/OJ secret 值逐项比较 | 均无匹配；真实 API Key / Token 未进入工件或跟踪源码 |

Phase 2 未完成事项：无。正式 fast / standard / strong / max 模型映射、价格、实验预算及 DEBUG 升级阈值/失败类型仍是后续实验决策；一次性探针参数不升级为产品默认值。

## Phase 3：Code Only 基线

**目标：** 实现一次生成、一次正式提交、无反馈修复的基线。

**依赖：** Phase 1、Phase 2；异常终态和统计口径 U12、统一 CLI 设计。

- [x] [实现/验证] 使用清洗后的 Agent 题目接口构建 CODE 上下文，不从普通接口补入 rating、tags、题解或历史解法。
- [x] [实现/验证] 指定 Profile 只调用一次 CODE，提取并保存 `solution.cpp`；保存该候选与调用的关联。
- [x] [实现/验证] 正式提交一次，通过 OJClient 轮询最终 verdict，持久化结果并结束。
- [x] [实现/验证] 为代码提取失败、模型异常、HTTP 失败、结果未知和 IE 保存明确终态与计数；不隐式重生成、修复或重复提交。
- [x] [实现/验证] 对齐 State、Trace 与指标；正常任务精确记录一次模型调用和一次提交。
- [x] [实现/验证] 设计并接入 `codeharness-agent solve` 的 mode / problem / Profile / Workspace 参数；保留原 `code-only` / `harness-loop` 子命令作为兼容入口，Phase 3 新任务使用带显式确认和超时的 `solve`。
- [x] [实现/验证] 覆盖成功与失败路径，断言不调用 PLAN / DEBUG / REVIEW / TEST_GENERATION、不 Custom Run、不向模型回传评测反馈。

**交付物：** code-only 模式、单题 CLI、任务工件、异常结果与计数测试。

**验收标准：** 正常任务恰好一次模型生成、一次正式提交，获取最终 verdict 后结束；异常如实记录，不能为了凑够一次提交而隐藏错误，也不能自动追加候选。读取 verdict 用于统计不等于反馈给模型。

### Phase 3 验证记录（2026-10-01）

| 命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | `85 passed`；新增 11 项 code-only 专项路径，并更新真实确认的整数 submission ID 协议测试 |
| `client_tests/test_phase3_code_only.py` | 覆盖 AC、WA、IE、题面 HTTP 错误、模型 HTTP 错误、代码提取失败、提交 HTTP/transport/result_unknown、轮询超时与 CLI 防误调用；断言每任务最多一次 CODE、最多一次 POST、零 Custom Run、零 feedback |
| `codeharness-agent solve --help` | `solve` 已接入 problem / mode / Profile / Workspace；HTTP timeout、poll interval、deadline 显式传入，真实调用必须同时给出 `--confirm-model-call` 与 `--confirm-submit` |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-phase3-dist-20261001`，随后从 sdist 解包运行客户端测试 | sdist / wheel 构建成功；发行产物无 MiniOJ server / shared Judge / 服务端 tests；解包后仍为 `85 passed` |
| Python 3.9.6 `compileall` 与 `git diff --check` | `agent` / `experiments` 全部通过语法编译；补丁无空白错误 |
| 首次真实任务 `phase3-live-t1001-qwen38-flash-20261001` | 一次模型调用后，202 响应的整数 `submission_id` 被旧客户端拒绝；任务以 `submission_creation_protocol` 结束且未自动重提，由此确认异常终态和防盲重试有效 |
| 一次无模型协议诊断 POST | 当前 MiniOJ 实际返回 `{"submission_id":11,"status":"QUEUED"}`；据此将字符串/非负整数 wire ID 统一规范化为内部字符串，不扩展其他未确认字段 |
| `codeharness-agent solve t1001 --mode code-only --profile standard ... --confirm-model-call --confirm-submit` | 修复后真实任务 `phase3-live-t1001-qwen38-flash-20261001-v2` 完成；一次 `qwen3.8-flash` CODE 调用、一次正式提交 `12`，最终 `FINISHED / AC`，230/222 tokens，11.302 秒 |
| 对真实 State / Trace / `solution.cpp` 独立对账 | `llm_call_count=1`、`submission_attempt_count=1`、`submission_count=1`；工具仅为 get problem / write file / submit / wait；CODE_VERSION、SUBMISSION、JUDGE_RESULT 的 model call ID、`solution-v1` 与 SHA-256 完全一致 |
| `codeharness-report workspace` 的 `code-only:standard` 汇总 | 两个真实 Phase 3 任务均可见：1 accepted、1 首次协议失败；平均 1 次 LLM / 1 次提交尝试，确认提交平均 0.5，两个任务成本均因缺价格保持 unknown |
| 对两个 Phase 3 任务工件扫描 Authorization / Bearer / key / token / secret / password | 无匹配；OJ Token 与模型 Key 未进入 State、Trace、源码或结果工件 |
| 使用 dotenv 实际值扫描所有 tracked files 与两个 Phase 3 Workspace | `SECRET_VALUE_SCAN=clean`；不只依赖敏感字段名扫描 |

Phase 3 未完成事项：无。真实验收所用临时 YAML 仅把 `standard` 指向 `qwen3.8-flash` 以完成本次验证，不确认为 Phase 5 正式策略；“一次 CODE、至多一次正式提交”是 code-only 模式语义，不替代 Phase 4/5 尚未选择的循环、token、时间或实验总预算。

## Phase 4：Harness Loop

**目标：** 单 Agent 按显式状态转换消费反馈、修改代码，并在确定的预算内结束。

**依赖：** Phase 3；2026-10-01 用户确认固定状态机、不升级、3 次 DEBUG 未解决则重新 PLAN、最多 10 次提交与 1 元成本、公开样例优先、简单 REVIEW、保存单题 Context 与断点恢复。

- [x] [实现/验证] 定义并验证 PLAN / CODE / TEST / DEBUG / REVIEW / DONE 的转换、进入条件、失败处理和终止原因。
- [x] [实现/验证] 支持 DEBUG → CODE；3 个连续 DEBUG 候选仍失败时经 TEST 回到 PLAN，保存重新规划原因。
- [x] [实现/验证] 逐个公开样例 Custom Run，按空白分词比较输出；仅全部通过后正式提交，失败先 DEBUG；验证 exit code / 截断 / stdout，OK 不替代正式 AC。
- [x] [实现/验证] 保存单题题面/计划/完整当前代码/最新反馈/近期历史；提示不裁掉题面、代码或计划，历史保留最近 5 条，反馈提示最多 8000 字符并保留完整工件；超长输入停止而非静默截断关键代码。
- [x] [实现/验证] 将结构化反馈送入 DEBUG；经过 ModelPolicy 的基础 Role 映射，Phase 4 强制关闭升级，真实模型名仍只在配置。
- [x] [实现/验证] 限制单任务 10 次正式 POST 尝试；原 Phase 4 硬性 1 CNY 已按后续用户决定改为默认 1 元可调。按价格/token 上限预留、80 次 LLM 熔断与可选活动时间限制保持；当前追加验证见 Phase 5 决策落实节。
- [x] [实现/验证] 分流 WA / CE / RE / TLE / MLE / OLE、IE 与客户端 / 模型通信错误，避免基础设施失败触发算法修复。
- [x] [实现/验证] 保存每次候选版本、反馈、Custom Run 结果、submission id、状态和实际 Profile；关联 model call / version / SHA-256 / verdict；新候选清除旧 verdict。
- [x] [实现/验证] REVIEW 暂为本地只读一致性检查：AC 后核对被评测版本/源码哈希和预算，写总结，不额外调用模型或改码。
- [x] [实现/验证] 接入 `solve --mode harness-loop` 和 `resume`；覆盖 DEBUG、重新规划、升级被禁用、成功/预算/异常结束；检查点/锁/配置冻结/不重发未知 POST 均验证。
- [ ] [联调] 验证真实正式失败反馈→DEBUG 修复→AC/REVIEW 循环；比赛 1 的 B 已有模型→样例→正式 AC→REVIEW，但没有正式失败/修复，不能勾选完整修复循环。历史 T1003 失败证据保留。

**交付物：** 状态机、ContextBuilder、策略 / 预算配置、版本工件、可关联 Trace、循环验收用例。

**验收标准：** 受控失败进入 DEBUG 后产生可追踪的后续候选和提交；允许回退且受预算约束；最终结论来自 MiniOJ，并对应被评测代码。REVIEW 不能用模型判断替代 verdict，不解析 MiniOJ 内部日志。

### Phase 4 验证记录（2026-10-01）

| 命令 / 证据 | 结果 |
| --- | --- |
| `uv run --no-sync pytest client_tests/test_phase4_harness.py` | 47 passed；全部样例门禁、exit code/截断/缺 stdout、样例失败→DEBUG、3 次失败→PLAN、各程序 verdict / IE、10 次提交、1 CNY 预留、调用/时间/Context 限制、缺价格/usage、恢复/锁/配置一致性与 CLI 全部通过 |
| `uv run --no-sync pytest` | 189 passed，含现有 Dashboard 中英文回归；零真实模型或正式提交 |
| 完整 Provider + OJClient MockTransport 链 | PLAN / CODE / DEBUG 共 4 次模型调用，样例试跑 3 次、正式提交 2 次，WA 反馈后 AC；版本/哈希/调用/提交关联一致，未读取普通题目接口 |
| 恢复故障注入 | 样例失败到 DEBUG 之间中断不会绕过门禁；锁内重载最新状态，预读旧状态不重复提交；已知 ID 继续 GET、已落盘模型响应复用；不确定付费调用/创建 POST 停止 result_unknown；DONE 幂等读取 |
| 真实 MiniOJ `t1001` 公开样例 Custom Run（已有 Phase 3 AC 源码） | `status=OK`、stdout=`5\n`、exit code=0，公开样例比较通过；2 ms / 5052 KiB；零模型调用、零正式提交 |
| `codeharness-agent solve t1001 --mode harness-loop --model-config /private/tmp/codeharness-phase2-models.yaml --harness-config config/harness.example.yaml ...` | 真实 CLI 创建 `workspace/phase4-live-budget-guard-20261001/` 并读取清洗题面；因缺 CNY 价格/token 上限保存 budget_unverifiable，LLM calls=0、正式提交=0 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-phase4-dist-20261001`，随后解包 sdist，在解包根目录以现有 venv 的 `python -m pytest` 运行 | sdist / wheel 构建成功；独立发行源码 `189 passed`，无源码仓库路径注入 |
| tarfile / ZipFile 发行清单与入口断言 | harness 新代码/配置/测试进入 sdist，wheel 保留 Dashboard 静态资源与五个客户端入口；无 MiniOJ/server/shared/tests、任务 Workspace 或 `.env` |
| `env PYTHONPYCACHEPREFIX=/private/tmp/codeharness-phase4-py39-cache python3 -m compileall -q agent experiments dashboard`，`git diff --check` | 系统 Python 3.9.6 语法与 diff whitespace 检查均通过 |
| `codeharness-agent solve --help` / `resume --help`，实际 secret 值与工件对账脚本 | 两个入口可加载；95 个当前源码/说明/新任务文件 secret 扫描 clean；真实守卫任务 checkpoint/State 一致、Trace 只有 get_problem，零 LLM / 正式 POST / 费用预留 |

原 Phase 4 轮实现验收完成，当时缺有效价格/请求上限，因此只做样例与缺价格守卫。后续 Phase 5 已读取用户本地配置进行有保守预留的真实联调（见下节），未修改 `.env`、`models.yaml` 或 `harness.yaml`。模型产物失败，不补判 AC；估算不等于账单核验，价格/Provider 上限准确性仍须用户核对。

## Phase 5：实验执行与最终验收

**目标：** 复用同一 Agent / OJClient，以可比较配置运行两种模式并导出结果。

**依赖：** Phase 4；可用用户模型配置及模型/OJ API 授权。本轮最小联调使用 T1003、固定 standard、各 mode 一次、显式整批 cap=1 CNY；未定的反馈协议/正式全组条件不阻止实现和替身验证。

- [x] [实现/验证] 保留 standard / strong × code-only / harness-loop 四个显式 fixed Profile 配置目标；混合策略另名/独立 Role 映射，默认混合不变；固定/混合路由替身验证。
- [x] [实现/验证] 定义 phase5-v1 配置/唯一题目列表，保存 mode、实际 Provider/model/pricing/token 参数、完整 Role、预期/实际反馈、预算、题目/重复次数、endpoint 指纹与不可变条件。
- [x] [实现/验证] 串行 Experiment Runner 复用 ExecutionService → 现有 Agent / OJClient，隔离 Workspace；先保存意图、保留失败、已完成幂等/中断安全恢复。
- [x] [实现/验证] 导出逐任务和条件分组 JSON / CSV：solved、verdict/终态、LLM 尝试/成功/失败、Profile calls、tokens/未知 usage、成本/币种/预留、POST 尝试/确认、DEBUG/REPLAN/Custom Run、活动 wall time、版本/哈希/ID。
- [x] [实现/验证] 与 Trace 对账调用/POST/确认/DEBUG/usage/预留；不完整/坏事件告警；未知请求不重发，未开始/中断/配置守卫与用户程序失败分开。
- [x] [实现/验证] 预期值与实际反馈 mode/source/status 分开；只认可服务端明确字段，缺失保持 unknown；strict/不匹配在模型前拦截，full / verdict_only / unknown 分组替身验证。此勾选不声称远程已提供该字段。
- [x] [实现/验证] 按配置成对 per-million 输入/输出价格、CNY 与 usage 估算；两模式共享付费守卫；缺价格/usage 不补零，失败预留不退、usage 超界整批停且不可恢复。
- [x] [实现/验证] 覆盖两模式/固定/混合/实际反馈/预算/批量失败/输出/配置漂移/故障恢复的替身测试，不将不可比条件混组。
- [x] [联调] 使用已有 API 授权与用户配置，T1003 两模式各一次最小真实实验完成，保存配置/工件/Trace/失败结果，未隐藏追加模型请求。
- [x] [文档/验证] README 中英文、Agent/配置/实验说明、架构与 Dashboard 文档更新；可复现命令与证据/未定条件分列。
- [x] [联调] 2026-10-02 GET `/api/v1/me` 明确返回 `feedback_mode=full`，比赛 1 两题模型前记录 actual=full/effective=verdict_only；历史 unknown 不回写。native verdict_only 与 full 对照尚未完成。
- [x] [实现/验证] 用户已确认 T1003、四个固定组加 mixed-harness、每组 1 次、提高输出上限；已落实独立任务默认 1 元可调、8192 输出请求上限、正式五组配置及替身验收。反馈期望最新改为 verdict_only，覆盖此前 full，见追加节。
- [ ] [联调] 核验供应商实际价格/有效输入输出上限、可观测实际 full/verdict_only 与兼容过滤，并完成真实五组比较；未自动扩展付费调用，配置确认不等于实际联调完成。

**交付物：** Experiment Runner、配置 schema、题目列表、逐任务 / 汇总 JSON 与 CSV、实验测试和复现文档。

**验收标准：** 同一入口支持不同 Profile / Policy 和两种 mode；每个结果能回溯实际配置、代码版本、调用和 MiniOJ verdict；混合策略不标成“所有阶段只用 standard”；无隐藏调用、虚构成本或假报成功。

### Phase 5 首轮验证记录（2026-10-01，历史条件）

| 命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv sync --extra dev --extra dashboard --offline` | 成功；客户端六个入口，Web 依赖仍为 optional extra |
| `uv run --no-sync pytest client_tests/test_phase5_experiments.py client_tests/test_dashboard_launch.py` | 53 passed；纯替身/ASGI，覆盖完整 AC/失败链、全批及同题 10 POST/1 CNY、缺 usage/价格、strict 反馈门禁、JSON/CSV/Trace、恢复与控制安全；额外断言两模式中断无响应的费用/tokens 为 null、uncertain_llm_calls=1，保留预留且不重发 |
| `uv run --no-sync pytest` | 242 passed；包含原 189 项回归，零自动真实 API 调用 |
| `uv run --no-sync codeharness-experiment run config/experiment.smoke.yaml --experiment-id phase5-live-T1003-20261001 --workspace-root workspace --confirm-model-call --confirm-submit` | completed，2 tasks / 0 solved；失败数据进入 tasks/summary JSON/CSV，config 与实际路由留存 |
| 同配置同 ID `codeharness-experiment resume ... --confirm-model-call --confirm-submit` | 读取完成实验/刷新报告，不新增模型请求、Custom Run 或正式提交；计数不变 |
| 独立 State / checkpoint / JSONL / 源码 SHA-256 / Decimal 对账 | 两 task 的 Trace audit consistent；936/2539 与 11790/9288 tokens；1+3 standard calls、0+1 DEBUG、0+1 Custom Run、零正式 POST/确认提交；估算费用与预留逐笔一致 |
| 真实只读 `GET /api/v1/me`、`GET /openapi.json`、清洗题目 `T1003` | 均 200，T1003 多远都要在一起/1 个公开样例；me/OpenAPI 无反馈模式字段，actual=null/status=not_advertised；无权限/diagnostic 推断 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-phase5-dist-20261001` 与 tarfile/ZipFile 清单 | sdist 101 / wheel 66 项，含实验/执行/预算代码、配置模板及 launch UI/静态文件、六个入口；排除本地模型/预算配置、密钥、任务和 MiniOJ；Web 依赖只在 dashboard extra |
| `tar -xzf ... -C /private/tmp/codeharness-phase5-sdist.EEP2r1`，解包根目录执行现有 venv `python -m pytest` | 独立发行源码 242 passed；未注入原仓库路径 |
| `env PYTHONPYCACHEPREFIX=/private/tmp/codeharness-phase5-py39-cache python3 -m compileall -q agent experiments dashboard client_tests`、三份 JS `node --check`、`git diff --check` | 全部通过；两个新增/更新 CLI 的 --help 可加载 |
| 对实际 `.env` secret 值的源码/说明/本轮工件及发行包扫描 | 138 个文件及 sdist/wheel clean；仅输出结论，不显示凭据 |
| 本地 8875 Dashboard 中英文 GET/HTML/JSON/白名单工件 + SHA-256 | 40 个响应全部 200/secret clean；两个真实任务及批量报告共 36 文件读取前后哈希不变 |

真实实验保留在 `workspace/.experiments/phase5-live-T1003-20261001/` 与对应 `-s1-p1-r1` / `-s2-p1-r1` 任务：

| 模式/策略 | 结果 | 调用/公开样例/DEBUG/正式 POST | usage 输入/输出 | 按配置估算 CNY | 保守预留 CNY |
| --- | --- | --- | --- | --- | --- |
| code-only / fixed standard | invalid_model_output：只有算法说明、无完整候选 | 1 / 0 / 0 / 0 | 936 / 2539 | 0.0076041 | 0.0372736 |
| harness-loop / fixed standard | 样例失败后 DEBUG；修正版未形成完整代码，invalid_model_output | 3 / 1 / 1 / 0 | 11790 / 9288 | 0.0345096 | 0.1118208 |

合计 0.0421137 CNY 估算、0.1490944 CNY 预留，均按用户配置 standard 输入 0.8 / 输出 2.7 CNY 每百万 token、输入上限 32768 / max_tokens 4096。价格和上限是读取的配置，不是本轮独立核实的供应商账单承诺。MiniOJ 的 Custom Run OK 不等于样例输出通过；失败门禁阻止正式提交。没有补运行 baseline 或把失败归为 WA/AC。

必要修复：DEBUG 计数改为实际模型尝试；同题共享批量额度；拒绝空白题目别名/Provider 超上限后的继续恢复；按活跃状态而非 UUID 字典序识别恢复任务；请求发出前费用状态置 pending_usage，中断无响应的 tokens/费用保持未知，实验/历史报告与 Dashboard 均不显示为零。ModelCallRuntime 正常响应后的既有计费语义保持，原 Phase 2/4 回归通过。

Phase 5 实现与最小真实实验验收完成，正式全组可比实验和真实完整 AC 循环未完成，保留上述未勾选项。

### Phase 5 用户决策落实：额度、混合组、full（2026-10-01）

本节为此前 full 决策的历史记录；最新 verdict_only 决策见后续追加节，历史命令/工件不回写。本轮只落实当时确认条件，不追改上述 4096/共享同题额度的历史工件，不自动运行整批付费实验。

- [x] [实现/验证] 单任务默认 1 CNY，可通过 Agent/Experiment CLI `--max-cost-cny`、YAML 或 Dashboard “任务额度（元）”调高/调低；必须有限正数，拒绝零/负/布尔/字符串/null/NaN/Infinity/超浮点范围整数。直接 code-only 和旧兼容入口也启用费用守卫。
- [x] [实现/验证] 每策略/重复是独立任务，各自最多 10 POST / 默认 1 元，不再同题跨策略共享；整批显式 cap 保留。新指纹包含 independent_tasks_v1 分配语义，旧批次/条件漂移拒绝 resume，旧报告可读。CLI/UI 恢复不改额度、不退预留、不重发已知提交，预算漂移不修改原 State。
- [x] [实现/验证] `config/experiment.full.yaml`：T1003，standard/strong × 两 mode 四 fixed 组，加 PLAN strong / CODE、DEBUG standard 的 mixed-harness，各 1 次；不升级/不启用模型 REVIEW/Test Generation。每任务 1 元、整批 cap=5 元为五份额度上限，不是预计花费或自动执行。
- [x] [实现/验证] 保留用户模型/价格/其他参数，local/template max_tokens 从 4096 提高到 8192；替身验证请求实际携带 8192、预算按新上限预留。有效供应商上限与价格仍未独立核实，改参数须新任务/实验 ID。
- [x] [实现/验证] 正式五组 expected_feedback_mode=full / require_feedback_mode=true；Dashboard 默认 --feedback-mode full。期望不代填实际模式，缺元数据在模型前停止；本地/真实 HTTP 门禁分别记录。
- [x] [文档/验证] 中英文 README、配置/Agent/实验说明、架构与 Dashboard API/恢复语义同步；实现、配置决定、替身与真实证据分列。

| 命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | 273 passed；原 242 项回归保留，新增额度/分配/full/五组/CLI/Node 与坏 YAML 验证；全部自动测试无真实远程请求 |
| `uv run --no-sync pytest client_tests/test_phase5_experiments.py client_tests/test_dashboard_launch.py` | 75 passed；五组替身各一次、路由/8192/独立额度、整批 cap、非法额度、预算漂移/旧分配不可恢复、两模式小额守卫、full unknown 零调用；Node 验证数值额度/code-only 禁混合；坏策略 YAML 页面可读/启动拒绝 |
| 只读 MiniOJ `GET /api/v1/me` + `GET /openapi.json` | 账户 mode=null/status=not_advertised；OpenAPI 200、没有 feedback_mode schema；零模型/正式提交，不根据 admin/诊断推断 full |
| 真实本机 `127.0.0.1:8875` Dashboard HTML + launch/job/detail API，任务 `ui-74e1cac932384d6fab87` | 中文额度输入默认 1.0；以 0.01 元启动混合 T1003，expected=full、actual=null、condition_mismatch；零 LLM/Custom Run/POST/预留；额度写入 execution-config，重复同 nonce 幂等、改额度 409 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-phase5-decisions-dist-20261001`，tarfile/ZipFile 清单及解包源码测试 | sdist 102 / wheel 66 项；full 配置与额度 UI 入包，六个 CLI；排除本地模型/预算文件、任务、密钥、MiniOJ 内部包；最终解包独立测试 273 passed |
| Python 3.9.6 compileall、三份 JS `node --check`、`git diff --check` | 全部通过 |
| Agent solve / Experiment / Dashboard `--help` 与实际 `.env` secret 值扫描 | 三个入口均显示 --max-cost-cny；Dashboard 显示 --feedback-mode full 默认；96 个源码/说明/配置/测试/本轮任务文件及两份发行包扫描 clean，不打印凭据 |

未完成：实际 full 的可核实 HTTP 元数据、供应商有效价格/8192 支持核验、真实五组/完整 AC 循环。浏览器自动化本轮返回空白截图/缺页面 AX，不能声称新布局视觉验收通过；真实本机 HTTP 页面与 API、ASGI 和 Node 行为已经分别验证。保留原用户未提交修改、`.env` 和旧实验工件；只按本轮授权修改 local 模型输出参数/预算注释。

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
| U01 | 真实联调已确认 `QUEUED` 创建响应的 `submission_id` 可为字符串或非负整数（客户端统一为字符串）、`FINISHED / AC` 查询结果及 AC feedback 最小 schema；其他 verdict、通用错误、进行中字段省略 / null 与未完成 feedback 仍待确认 | 1 |
| U02 | 已用规范 `source_code` 验证真实同步 Custom Run `OK / stdout / exit_code / time_ms / memory_kb`；`code` 仍为显式兼容开关，其他失败形态、资源限制及远程是否保留别名仍待确认 | 1 |
| U03 | 已确认客户端不自动重试、提交超时为 result_unknown、参数显式传入；项目默认值、可重试条件、服务端幂等 / 查重及恢复流程仍待确认 | 1、3–4 |
| U04 | me#feedback_mode=full 已确认；native verdict_only、其他诊断字段与多 testcase 资源汇总/单位仍待确认 | 1、5 |
| U05 | 用户已有本地模型/价格/参数配置；现有 CF2049D UI/远程提交已核对四 Profile 调用。真实同条件比较、账单价格/有效上限仍待核验 | 3、5 |
| U06 | 用户已确认 Phase 4 使用固定状态机；不启用模型原生 Tool Call 驱动调度 | 4，已确认 |
| U07 | 用户已确认不升级模型；3 个 DEBUG 生成候选连续失败后重新 PLAN，保留基础 Role 映射；以后启用升级另行确认 | 4，当前已确认 |
| U08 | 已确认独立任务默认 1 元可调/最多 10 POST，五组配置整批 cap=5 元，历史 smoke cap=1；恢复不改额度。供应商有效价格/上限仍待核验，5 元不是预计支出 | 4–5，额度已确认 |
| U09 | 已实现公开样例逐个 Custom Run → 空白 token 比较 → 全部通过才正式提交；样例失败先 DEBUG，IE / 未知结果安全停止；special judge / 浮点容差需后续专门配置 | 4，当前已确认 |
| U10 | 委托设计的简版 REVIEW 已实现：仅正式 AC 后本地只读检查版本/哈希一致与计数，写 review.md；零模型调用、不改源码，后续可调整 | 4，当前已确认 |
| U11 | 已确认保留单题状态并断点恢复；完整题面/样例/计划/当前代码，最近 5 条摘要、反馈 8k 字符。checkpoint 权威、State 投影；已知 ID 继续查询，未确认模型请求/提交 POST 不重发；跨题复用不在范围 | 4，当前已确认 |
| U12 | 单题/批量预算、失败、未知、中断、恢复、Trace 对账/停机已实现验证；not_started 不当作完成比较 | 4–5 |
| U13 | 已确认 T1003、四固定组加 mixed-harness、各 1 次、8192/verdict_only；full 兼容已观测，五组结果/供应商有效上限仍待联调 | 5，配置已确认 |
| U14 | 多版本/phase5-v1/指纹分组与 JSON/CSV 已实现；2026-10-02 full 元数据已确认，native verdict_only 与全组比较未完成 | 4–5 |

## Dashboard：独立 D0–D5

本节 D0–D5 为历史只读验收，详见 [docs/dashboard.md](docs/dashboard.md)。当前按用户请求增加 D6 简化与启动能力，见后节；只读模式仍保留，浏览/轮询无远程副作用。

- [x] [文档/验证] D0：检查实际 State、Trace、模型配置、汇总与入口；创建设计文档。基线客户端测试 85 passed。
- [x] [实现/验证] D1：Dashboard Read Models、WorkspaceRepository、TraceReader、WorkspaceIndex；用临时 Workspace 验证兼容性、安全读取、多版本/多提交关联。
- [x] [实现/验证] D2：optional dashboard extra、codeharness-dashboard CLI、loopback FastAPI、只读 API 与模板入口。
- [x] [实现/验证] D3：Overview、Tasks 搜索/过滤/排序、Task Detail、Timeline、Solution / Model / Tool / Submission / Artifacts。
- [x] [实现/验证] D4：现有 State 分组的 Experiments、实际选中配置的 Models / Role Policy；未知成本与 No runs。
- [x] [实现/验证] D5：详情轮询、Timeline 增量、终态停止、写入竞争、坏任务隔离、测试/构建/浏览器验收与 README。

Dashboard 的人工 harness-loop fixture 只验证展示能力，不是 Phase 4 实现或真实联调。

### Dashboard 验证记录（2026-10-01）

| 命令 / 证据 | 结果 |
| --- | --- |
| D0 基线 `uv run --no-sync pytest` | 原 85 passed |
| D1 Reader 首次测试 | 14 passed，后续 D5 加入源码/提交 hash mismatch，Reader 共 15 项 |
| D2 CLI / 页面 API / optional 边界专项 | 15 passed |
| D4 Models / HTML 脱敏专项 | 2 passed；空实验组显示 No runs |
| `uv sync --extra dev --extra dashboard --offline` 后 `uv run --no-sync pytest` | 127 passed = 原 85 + Dashboard 42；临时 Workspace / 本地 ASGI，零模型 / MiniOJ 调用 |
| 普通 `uv sync --offline` 与独立 import 检查 | FastAPI / Jinja2 / Uvicorn 均不存在；Dashboard `--help` 仍可加载 |
| 仅 dev extra 的测试 | 100 passed, 1 skipped；纯 Reader 运行，Web Route 模块显式 skip |
| sdist / wheel 构建与发行源码测试 | 产物含 dashboard 模板、静态 CSS/JS/SVG 和第五个 CLI；无 MiniOJ server / shared / 服务端 tests；sdist 解包后 127 passed |
| Python 3.9.6 compileall / JS syntax / diff whitespace | 全部通过 |
| 本地 127.0.0.1:8765 与 Chrome 宽屏 / 400px 页面 | Overview、现有 Phase 3 详情可读；1 model call、230/222 tokens、solution-v1、submission 12、FINISHED / AC、19 事件；版本/调用/源码 hash/提交关联独立对账 |
| `/private/tmp` 人工 polling fixture | 运行中 Timeline 9 → 16，新增第二版本/模型/提交，终态停止；未在真实 Workspace 写人工任务 |
| 当前四个 Workspace 的所有只读页面/API/白名单工件 | 实际 secret 值扫描 clean，读取前后 Workspace 文件 SHA-256 全部不变 |

Dashboard D0–D5 已完成；当时只读 UI 的多版本 fixture 不作为 Agent Phase 4/5 验收。当前 Phase 5 状态见上节，D6 状态见下节。读取上限与历史 API 语义见 docs/dashboard.md。

### Dashboard 中英文切换（2026-10-01）

- [x] [实现/验证] 右上角 中文 / English，Cookie 记忆与跨页面选择；保留筛选条件，前端保留详情锚点。
- [x] [实现/验证] 五类页面标签、空状态、未知值、读取诊断和轮询提示使用统一字典；保留源码、模型名、机器字段与 JSON API 原值。
- [x] [实现/验证] 15 项语言相关回归；全量 `142 passed`，sdist 解包后同为 `142 passed`。验证并发语言隔离、持久选择、重定向边界、HTML escape、API / 工件一致与 Workspace 不变。
- [x] [实现/验证] Chrome 中文宽屏 / 400px 窄屏与英文回切；临时人工任务新增第二版本 / 模型 / 提交后自动刷新，中文终态停止；Node 验证共享字典、动态提示、筛选与锚点保留；Python 3.9.6 / JS 语法和 diff whitespace 通过。

该语言切换轮未加入任务启动功能。验证只读本地或使用 `/private/tmp` 人工工件，零真实模型 / MiniOJ 请求；该轮未推进 Agent Phase 4/5。

### Dashboard D6：简化与发起任务（2026-10-01）

- [x] [实现/验证] 简化首页为单题启动表单、三个统计/最近任务；五列任务列表；详情源码/调用/Timeline 折叠，评测可见；保留语言/轮询/安全读取。
- [x] [实现/验证] 本地串行有界队列复用 ExecutionService，异步跳转详情、持久 nonce 防重、配置来自启动参数，代码路径不执行任意 shell。
- [x] [实现/验证] loopback/精确同源/CSRF/明确确认/JSON 8 KiB/参数白名单/容量守卫，浏览 GET 不发起 API；`--read-only` 无写端点。
- [x] [实现/验证] UI harness 手动检查点恢复、配置冻结、活跃恢复去重、重启不自动重跑；未知付费/POST 不重发，code-only 不自动重生成。
- [x] [实现/验证] 22 项本地 ASGI/worker 替身测试；Chrome 中文启动表单、未确认阻止发起、code-only fixed standard/禁用混合、英文切换与折叠详情核验。浏览器未额外启动付费任务；真实两模式测试走同一 ExecutionService 的批量入口，不冒充 UI 真实付费验收。

本轮测试服务使用 `127.0.0.1:8875`，不替换用户原 8765 进程；最后已重启本轮服务加载新实现。浏览器默认诊断折叠，点击 model-calls 锚点自动展开，显示 3 次实际调用及 DEBUG finish_reason=length。旧 Dashboard 需重启才加载发起任务；启动命令见 README。UI 独立任务每任务限额；跨策略/重复共同预算使用 Experiment Runner，UI 不是无穷批量后台任务。

## Dashboard D7：基本任务配置入口（2026-10-01）

用户要求把费用控制区域改成可修改请求次数等基本配置的入口。本轮扩展现有发起任务表单，不实现新的求解器、全局配置文件编辑或 MiniOJ 服务端。

- [x] [实现/验证] 原费用输入改为“任务配置”折叠入口：费用、模型调用次数、正式提交尝试次数、MiniOJ HTTP 超时、轮询间隔、评测等待上限六项；默认取 YAML/CLI（1 元/80/10/15/1/120），摘要实时更新，可恢复默认且不清空题目/确认。
- [x] [实现/验证] 仅当前新任务生效，复用 HarnessPolicy/RunRequest、ExecutionService 与原队列；次数和等待参数写入 job/execution-config/checkpoint，实际 model-call/POST 守卫与 OJClient/轮询使用新值。
- [x] [实现/验证] code-only 的调用/提交次数锁定为 1；切回 harness 恢复之前填写的次数。最多 10 POST、3 次 DEBUG 重 PLAN、不升级、full 门禁保持；模型调用次数不是全部 HTTP 请求数，样例不占正式提交次数。
- [x] [实现/验证] 嵌套 task_config 六字段白名单；拒绝错误类型/NaN/Infinity/超范围次数/超大超时。旧平铺费用 API 兼容但禁止双写；同 nonce 改配置 409，resume 不接受配置覆盖，旧任务不加钱、不重跑。
- [x] [文档/验证] README 中英文、配置说明、架构与 Dashboard D7/API/参数含义同步；无关未提交改动、用户 YAML/密钥、旧任务工件保持。

| 验证命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | 300 passed；包含原 273 项及 27 项新配置/守卫/安全回归；自动测试只用替身、ASGI 和 Node |
| `uv run --no-sync pytest client_tests/test_dashboard_launch.py` | 65 passed；六项落盘/指纹、实际调用/提交停机、HTTP/轮询参数到达执行层、非法嵌套/越权字段、nonce/双写/恢复冻结；Node 检查数值提交、摘要、reset、code-only 切换和无效输入展开 |
| 真实本机 `127.0.0.1:8875` 中英文 GET + launch/detail，任务 `ui-a075e66e6def4d979d97` | 六项输入存在；0.01 元/3 次模型/2 次提交/5 秒 HTTP/0.25 秒轮询/30 秒等待全部存入快照；actual full 仍不可核实而 condition_mismatch，零模型/正式 POST |
| Python 3.9.6 compileall、`node --check dashboard/static/js/launch.js`、`git diff --check` | 通过 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-task-config-dist-20261001` 与 sdist 解包独立测试 | wheel/sdist 构建通过，任务配置模板/脚本/校验入包，六个纯客户端入口；解包测试 300 passed；排除 MiniOJ 内部包/本地模型预算文件/Workspace/.env |
| 实际 `.env` secret 值扫描（不打印值） | 39 个配置入口源码/说明/测试/新任务文件与两份发行包 clean |

本轮不自动运行付费模型/正式提交；未写用户 models.yaml/harness.yaml/.env，不提供“保存为全局默认”动作。新界面的浏览器视觉复核仍未执行，不用 ASGI/HTTP/Node 代替视觉证据；正式 full 与五组真实联调的前置条件不因本入口改变。

## Agent 默认期望改为 verdict_only（2026-10-01）

本节记录此前精确匹配的 verdict_only 默认变更及 311 项历史证据；full 兼容规则由后续追加节覆盖。用户明确实际 Agent 按 verdict_only 条件运行，覆盖此前 full 选择。本轮只改客户端默认期望及正式/示例配置，不开发 OJ 服务端、不运行付费模型/正式提交，不修改任何历史任务或用户 models.yaml/harness.yaml/.env。

- [x] [实现/验证] RunRequest / ExperimentConfig / Dashboard Settings / Dashboard CLI 默认 expected_feedback_mode=verdict_only、require_feedback_mode=true；正式与示例五组 YAML 同步。experiment.full.yaml 保留历史文件名，name 改为 T1003-verdict-only-five-strategies，字段才是实际条件。
- [x] [实现/验证] 明确匹配 verdict_only 的两模式/五组替身链通过；只有 WA verdict/summary、没有隐藏用例时仍可 DEBUG 后 AC。不要求隐藏用例，不新增 TEST_GENERATION/升级/模型 REVIEW。
- [x] [实现/验证] 未知/full 与默认 verdict_only 不匹配时仍在题面/模型/正式提交前停止；actual 不由期望代填。显式 full 覆盖继续支持；历史 smoke 的 null/false 明确保留。
- [x] [实现/验证] 新默认不改写旧 full job/execution-config/checkpoint；既有提交 ID 恢复仅继续查询，不追加模型调用/POST。更改实验条件需要新 ID，不能 resume 偷换反馈模式。
- [x] [文档/验证] 中英文 README、Agent/实验/配置/Dashboard 说明与架构同步；旧 full 验证表明确为历史记录。

| 验证命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest client_tests/test_phase5_experiments.py client_tests/test_dashboard_launch.py` | 113 passed；默认/CLI 覆盖、两模式门禁、五组 verdict-only、受限 WA 调试、旧 full 恢复、显式 unknown smoke |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | 311 passed；原 300 项回归加 11 项新测试，全部使用本地替身/ASGI/Node，不产生真实模型或 OJ 请求 |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync python -m compileall -q agent dashboard experiments client_tests`、`git diff --check`、`codeharness-dashboard --help` | 通过；CLI 帮助明确 default: verdict_only，显式 full 选项仍保留 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-verdict-only-dist.3dYV1v` 与归档检查 | wheel/sdist 构建通过；新默认/正式与示例 verdict_only 入包，历史 smoke null/false 保留；不含 MiniOJ 服务端/任务/本地模型预算文件/.env |
| 解包 sdist 后在 `/private/tmp/codeharness-verdict-only-sdist.wtchn30g/codeharness-0.1.0` 运行 `/Users/susenyang/Code/CodeHarness/.venv/bin/python -m pytest` | 311 passed；首次解包发现 3 个新增 CLI 用例依赖未打包的 local harness.yaml，改用随包 harness.example.yaml 后仓库与重新构建的独立源码均通过，不改发布排除规则 |

未完成：最新远程实际模式仍需通过 HTTP 明确声明并与 verdict_only 匹配；本轮未重新探测远程、未真实运行五组，不将用户期望当成服务端协议确认。运行中的旧 Dashboard 需重启才能加载新默认，旧任务保持原条件。

## full → verdict_only 客户端兼容（2026-10-01）

用户要求允许服务端 full 满足 Agent verdict_only；不是把 full 原样交给 Agent，也不是将 actual 改成 verdict_only。本轮复用共享执行入口、ToolRuntime、State/checkpoint 与实验/只读 Dashboard，不新增求解器或 OJ 服务端。

- [x] [实现/验证] 新 verdict_only 请求接受明确 full 或 verdict_only；actual 如实记录服务端模式，effective=verdict_only、feedback_policy=formal_verdict_only_v1。未知仍付费前停，verdict_only 不反向满足显式 full。两 mode、UI 发起、Fake 与真实 OJClient+MockTransport 均验证。
- [x] [实现/验证] 正式 submit/get_submission/wait/get_feedback 在 ToolRuntime 返回/Trace 前白名单投影：仅 ID/已识别状态/verdict，远程 summary、编译/运行诊断、隐藏用例、任意未来嵌套扩展字段全部丢弃；未知状态/verdict/类型 fail-closed。正式错误正文也过滤，保留错误分类/HTTP 状态/未知 POST 标志，不丢失禁止重试语义。
- [x] [实现/验证] 公开样例 Custom Run stdout/stderr 保持；只有 WA verdict 的反馈可 DEBUG 后 AC。隐藏哨兵覆盖模型提示、整个工作区工件/Trace/checkpoint/批次报告，均未出现；原始响应对象未就地修改。
- [x] [实现/验证] 新策略版本写入单题/整批快照指纹及 checkpoint.config；过滤本身由策略驱动，不依赖可读 effective 字段开关。旧精确 verdict-only 检查点不自动升级/改写，需新 ID；新过滤任务可恢复已知提交，不重复先前模型/POST。策略/有效模式漂移在额外调用前拒绝；旧 explicit full 与显式 unknown smoke 保持。
- [x] [实现/验证] State/result/Trace/逐任务与分组 JSON/CSV、Dashboard API/汇总区分 actual/effective/policy；兼容条件核验不代填 actual，不将 raw full/projected full/native verdict-only/unknown 混组。首页双语提示兼容过滤，详情显示 OJ 实际与 Agent 有效模式，沿用原轮询。
- [x] [文档/验证] 中英文 README、Agent/配置/实验/Dashboard 与架构更新；此前 full 拦截为历史精确匹配记录。保留用户 YAML/密钥、旧任务和无关未提交改动。

| 验证命令 / 证据 | 结果 |
| --- | --- |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest` | 337 passed；原 311 项基础上调整精确匹配用例、增加 27 项过滤策略测试及 UI 兼容；全量无真实模型/OJ 请求 |
| `env UV_CACHE_DIR=.uv-cache uv run --no-sync pytest client_tests/test_feedback_policy.py client_tests/test_phase5_experiments.py client_tests/test_dashboard_launch.py` | 139 passed；包括 full WA→DEBUG→AC、HTTP MockTransport、任意嵌套/summary/错误/机器字段过滤、样例不变、恢复/漂移/旧版本、两模式 UI/双语/actual-effective 分组。开发中补正 UI 测试默认英文与 HTML 属性空白的断言，不改 API 数据 |
| `compileall -q agent dashboard experiments client_tests`、`node --check dashboard/static/js/dashboard.js`、`git diff --check`、`codeharness-dashboard --help` | 通过；CLI 帮助说明 full 可限制为 verdict_only，默认值未改，unknown 仍停 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-feedback-compat-dist.LMAwkf` 与归档检查 | wheel/sdist 构建通过；策略模块、模板/翻译与测试入包；仍排除服务端、任务、本地模型/预算文件/.env；未知模式早停的 result 也保留 actual/effective/policy 元数据 |
| 在解包目录 `/private/tmp/codeharness-feedback-compat-sdist.qss4cget/codeharness-0.1.0` 运行 `/Users/susenyang/Code/CodeHarness/.venv/bin/python -m pytest` | 337 passed；独立发行源码回归，未依赖本地用户配置 |

未完成：没有新增真实模型调用、Custom Run 或正式提交，不宣称真实兼容联调完成；MiniOJ 仍需声明可核实 full/verdict_only 元数据。浏览器视觉复核未执行，双语模板/ASGI 测试不代替视觉证据。旧 Dashboard 需重启，新策略使用新任务/实验 ID，历史任务保持原条件。

## 比赛测试扩展与剩余事项检查（2026-10-02）

用户新增需求：给比赛 ID，调用模型逐题测试；评分先不细化，真实联调 ID=1。复用已有执行器和未提交实现；不修改 models.yaml/harness.yaml/.env、旧 State/Trace、无关改动，不开发 MiniOJ 服务端。

- [x] [实现/验证] `public_html_v1` 只读取公开比赛 Problems 表及标签页公开 Problem ID；确认比赛 1 A→CF1454A/B→CF1454B，无题单 GET JSON API 时不臆造接口。正整数 ID、同源链接、结构/重复/内容类型/页面上限/符号链接等 fail-closed；未公开/401/404/异常无模型调用。
- [x] [实现/验证] ContestRunner → 现有 ExperimentRunner/ExecutionService/Agent/Harness，逐题独立状态、一次策略、串行执行；contest_id 冻结到执行配置和 checkpoint，使用 OpenAPI 已确认的比赛 POST 接口。两种模式保留各自语义，公开样例/3 DEBUG 重 PLAN/正式反馈过滤/REVIEW 不另写求解器。
- [x] [实现/验证] 显式整场总预算、默认 1 元；每题默认 1 元/最多 10 POST，可用原任务配置限制。保守预留不退，额度不足/未开始/未知不计 AC；简单 AC 数/总题数/逐题结果/调用/提交/估算成本，official_score=null，不设计积分或罚时。
- [x] [实现/验证] `.contests/<run-id>/contest.json`/report.json/problems.csv 与已有批次/逐题工件关联；恢复固定题单和原参数，完成/blocked 不再请求；已知 ID 继续查询，未知模型/POST 不补发，配置漂移拒绝。普通单题旧 nonce/无比赛批次指纹兼容保留。
- [x] [实现/验证] CLI `contest`/`contest-resume`，Dashboard `/contests` 表单/本地报告/列表/自动刷新/逐题链接/恢复，沿用同队列/Origin/CSRF/确认/nonce/8 KiB/只读模式/重启不重跑；不能传任意题单、模型文件或评分代码。报告读取复用 bounded/dir_fd/O_NOFOLLOW 安全层。
- [x] [联调] 比赛 1 有界真实测试 `contest1-live-20261002`：standard/harness-loop、每题 0.35 元/6 模型调用、整场 cap=0.7 元；5 模型、2 Custom Run、1 POST。B 提交 103 为 AC 且 REVIEW/源码哈希一致，A 样例不匹配后 DEBUG 无完整代码，0 POST。AC=1/2、估算 0.0255813 元、预留 0.241664 元，不当作账单/官方得分。
- [x] [实现/验证] Chrome 中文桌面表单→queued→自动终态，UI 守卫运行 `contest-ui-44c7112131d948a8b75e` 整场 0.000001 元；两题 budget_exhausted、0 LLM/0 POST。浏览/轮询无远程副作用，API 原值不翻译；未自动追加失败补测。
- [x] [文档/验证] README 中英文、架构、Dashboard 与实验说明同步，当前 full 已核实而历史 unknown 保留；真实/替身证据分列。

| 验证命令 / 证据 | 结果 |
| --- | --- |
| 开发前 `.venv/bin/python -m pytest` | 337 passed，保留全部已有修改 |
| `.venv/bin/python -m pytest` | 386 passed = 原 337 + 新比赛 49 项，零真实网络/模型/OJ 请求；最终补充整场额度完全用完的 not_started/完成计数/无失效任务链接回归 |
| `.venv/bin/python -m pytest client_tests/test_contests.py` | 49 项随全量通过；公开 HTML/HTTP MockTransport、比赛 POST/未知响应、两模式/预算/恢复/nonce、CSRF/只读/安全读取 |
| `compileall -q agent dashboard experiments client_tests`；`node --check dashboard/static/js/{launch,contest}.js` 分别执行；`git diff --check` | 通过 |
| `.venv/bin/python -m experiments.cli contest 1 --experiment-id contest1-live-20261002 --workspace-root workspace --profile standard --mode harness-loop --max-llm-calls 6 --max-cost-cny 0.35 --max-total-cost-cny 0.7 --confirm-model-call --confirm-submit` | completed；1/2 AC，成本/调用如上，报告 `workspace/.contests/contest1-live-20261002/report.json` |
| 同参数改 `contest-resume`；Trace/State 独立审计 | 完成运行只读返回；仍 5 模型/2 样例/1 POST，两个任务 audit=consistent；B submission=103/AC 与 review.md 存在，A 样例另一排列满足约束但 tokens 比较失败 |
| GET me、已有提交 43/45/46/47/103 | full 明确声明；依次 AC/CE/AC/WA/AC。结合现有 CF2049D UI State，standard/fast/max/strong 都已有真实调用，不等于正式五组公平比较或有效价格/上限验证；未改写其工件 |
| Chrome 本机 8892 | 表单与报告桌面视觉/入队/自动刷新实测，极低预算运行零付费；原 8765 服务未替换。新运行中的页面/配置以新服务为准 |
| `env UV_CACHE_DIR=.uv-cache uv build --offline --out-dir /private/tmp/codeharness-contest-dist.Sh1jCY`；解包 sdist 测试 | wheel/sdist 构建与新增比赛模块/模板/JS 入包检查通过；保留合法 agent/workspace 源码，排除根任务目录/.env/本地配置/服务端。解包 `/private/tmp/codeharness-contest-sdist.tGhAux/codeharness-0.1.0` 使用项目 `.venv/bin/python -m pytest`：386 passed，与工作区相同 |
| 实际 secret 值扫描与只读 ASGI/哈希审计 | 192 个源码/说明/测试/本轮工件文件与两份发行包 clean；只读比赛/模型页面/API 均 200，90 个本轮运行工件读取前后 SHA-256 完全一致，不输出密钥值 |

本次检查后仍未完成（不与本功能的最小验收混淆）：

- [ ] 多解/special judge/浮点样例策略：A 的 `2 1 / 2 3 4 5 1` 是合法另一答案，当前 whitespace_tokens 误拒。可接 MiniOJ 已有远程样例 checker 或配置明示客户端判定规则；协议/规则需确认，不静默提交、硬编码解答或将未正式评测的 A 算 AC。
- [ ] 真实正式失败反馈→DEBUG 修复→AC/REVIEW 完整循环；已有一次性 AC 不证明修复能力。
- [ ] 正式五组 standard/strong × 两种 mode + mixed-harness 比较；现有零散 UI 运行/本次比赛不代替同条件实验。
- [ ] 供应商实际账单价格、有效输入/输出上限，以及 native verdict_only 与 projected-full 对照；full 元数据本身已完成核验。

Test Generation/brute/oracle/stress 仍为前述可选后续，不作为比赛 MVP 的欠交付；官方加权分数/罚时/排名按用户要求暂不设计。当前公开 HTML 适配不承诺未公开/登录限定比赛支持；服务器页面变化即安全停机，未来 JSON 接口需显式更新。

## 上一轮 Phase 5 停止条件（历史）

Phase 5 实现/替身验收、历史 T1003 最小真实实验与 Dashboard 简化/启动已完成；额度/混合/D7 基本配置、verdict_only 默认与 full 单向兼容落实后停止。不进入 Test Generation/stress testing、不自动追加付费批次、不开发 MiniOJ 服务端。保留无关未提交修改与 .env/旧工件；本轮不修改用户模型/预算 YAML。

五组策略/各一次/独立任务额度/8192 输出请求/verdict_only/full 过滤兼容已按新决定落实，不再待选。剩余正式前置条件：MiniOJ 返回可核实实际 full/verdict_only 的 HTTP 元数据（优先模型调用前可读的账户/能力字段；其他位置则明确适配，本项目不开发服务端）、过滤兼容真实联调与有效供应商价格/token 上限核验。较小整批 cap 导致的削减/跳过仍须单列；原失败不隐式补测，新条件用新 ID。真实五组与完整 AC/REVIEW 循环仍未完成，不进入下一扩展阶段。
