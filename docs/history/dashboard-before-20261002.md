# CodeHarness Local Dashboard

Dashboard 当前支持本地发起/恢复单题任务，并保留 `--read-only` 观测模式。D0–D5 和语言扩展是历史只读验收；后续 D6 与 Phase 5 同轮加入简化 UI/控制能力。Agent Phase 4/5 已实现，最小真实 T1003 联调失败如实记录，不等于真实 AC 或正式全组比较。

## D0：源码检查与设计

本节至“中英文扩展验证”记录当时只读版本，不描述当前 D6 的控制能力；当前入口/API/安全规则见文末 D6。

2026-10-01 基线：85 项客户端测试通过。当前工作区已有 Phase 2/3 的未提交修改，Dashboard 在其基础上新增包，不回退这些修改。

数据依据：`agent/workspace/task.py` 的 TaskState / TraceEvent，`agent/models/runtime.py` 的 CALL / RESPONSE，`agent/core/agent.py` 的 CODE_VERSION / SUBMISSION / JUDGE_RESULT，以及 `experiments/summarize.py` 的 mode / experiment_variant 分组。复用字段 `terminal_status`、`termination_reason`、`cost_estimate_status`、`solution_version`、`code_sha256`；correlation ID 实际存于事件 payload。没有修改 Agent 持久化协议。

```text
Agent → Workspace / Trace / Results
             ↓
WorkspaceRepository + TraceReader + WorkspaceIndex
             ↓
Dashboard Read Models → Statistics / Configuration Views
             ↓
FastAPI GET Routes → Jinja2 / CSS / polling JavaScript
```

`dashboard/` 包隔离文件读取、dataclass Read Model、统计服务、HTTP 和模板。默认 Agent 依赖保持 HTTP / dotenv / YAML；FastAPI、Jinja2、Uvicorn 仅属于 `dashboard` extra。

列表只读 task/state/result 小文件并缓存 summary，不解析 Trace。详情打开后才读取 Trace、代码和白名单工件。mtime / inode / size 用于失效判断；坏任务单独显示 Partial Data。读取竞争时保留上一次有效 JSON；JSONL 未完成尾行不当成有效事件。所有缓存只在内存中，Workspace 仍为数据源。

安全边界：只提供 GET；绑定 loopback；文件白名单、resolve containment、逐段拒绝符号链接；输出复用 Trace 脱敏并过滤已配置 secret；Jinja 自动 escape，JS 以文本插入内容。页面不包含 Agent/OJ/Provider 操作，也不代理 MiniOJ。

统计语义：未知 tokens / cost 保持 null；已知成本按币种分别相加；尝试提交和确认创建分别统计。实验页面仅为已有 State 的分组观察，没有实验可比性声明；四个预期组没有任务时显示 No runs。Models 只显示显式选择或 MODEL_CONFIG 指向的配置，不自动选择历史探针 YAML。

## D1–D5 实施顺序

1. D1：Read Models、安全 Repository、增量 TraceReader、WorkspaceIndex 与临时 Workspace 测试。
2. D2：optional dependencies、独立 CLI、只读 JSON API 和模板入口。
3. D3：Overview、搜索过滤排序列表、详情、代码/版本/调用/提交/Timeline/工件。
4. D4：已有 State 分组、Models 与 Role Policy，只展示存在的数据。
5. D5：2 秒详情轮询、增量 Timeline、终态停止、竞争/坏任务/安全回归，构建与浏览器验收。

## 安装与启动

```bash
uv sync --extra dashboard
uv run --extra dashboard codeharness-dashboard \
  --workspace-root workspace --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。CLI 仅允许 loopback（127.0.0.1 / localhost / ::1），拒绝 0.0.0.0；不自动打开浏览器、不创建 Workspace。可用 `--model-config <models.yaml>` 指定只读 YAML；否则 CLI 只使用环境 / `.env` 的 `MODEL_CONFIG`。没有选择配置时 Models 显示 No configuration selected；不查找 `/tmp` 或历史 probe 配置，不要求 OJ / Provider 凭据。

页面右上角 **中文 / English** 可切换语言。首次访问默认英文；选择保存在浏览器的 `codeharness_language` Cookie（有效期一年，HttpOnly、SameSite=Lax），跨页面与刷新保留，各浏览器请求独立。切换时保留当前路径、筛选查询及详情章节锚点；禁用 JavaScript 时仍可切换并保留路径与筛选。新增 GET `/language/{en|zh}?next=<local-path>` 仅设置语言 Cookie 并跳转，拒绝外部地址、编码绕过、反斜杠与控制字符。无效 Cookie 回退英文，不支持的语言返回 404。

Overview / Tasks / Task Detail / Experiments / Models 的标签、筛选、空状态、未知值、读取诊断与动态刷新提示均有中英文版本。字典位于 `dashboard/i18n.py`，模板与前端使用同一字典；前端通过不可执行的 JSON 数据块读取字典，不执行内联脚本。源码、题面、模型名、Profile / Role ID、AC / WA、状态 / 事件机器字段和原始 JSON 保持原文。所有 `/api/dashboard/` 响应与白名单工件内容不受语言选择影响；Workspace 不保存语言信息。本轮按用户收窄后的要求只实现语言切换，未加入任务启动功能。

Web 依赖仅在 `dashboard` extra 中，默认依赖仍为 httpx / dotenv / YAML。`codeharness-dashboard --help` 无需导入 Web 依赖；缺少 extra 的启动会给出安装指令。无 Node 构建、数据库、WebSocket 或外部 CDN。

## 页面与 API

| 页面 | 内容 |
| --- | --- |
| `/` | 总任务、solved / unsolved、调用、token、确认提交/已知尝试、按币种成本、未知计数、verdict / mode 分布和最近任务 |
| `/tasks` | task/problem 搜索，mode/profile/task status/verdict 过滤，updated/created/duration 降序；只读缓存摘要 |
| `/tasks/{task_id}` | State、只读源码/复制、版本/调用/提交关联、资源和 tests、统一 Timeline、白名单工件 |
| `/experiments` | mode:experiment_variant 分组的已有 State；保留 mixed 名称，缺失四组为 No runs；不声明实验可比性 |
| `/models` | 选中 YAML 的 Profile / Provider / Model / 参数 / pricing、Role 和升级策略；占位模型明确标注 |

所有本地 JSON 路由只有 GET：

```text
/api/dashboard/summary
/api/dashboard/tasks
/api/dashboard/tasks/{task_id}
/api/dashboard/tasks/{task_id}/events
/api/dashboard/experiments
/api/dashboard/models
```

tasks API 使用同名过滤参数：`q`、`task_id`、`problem_id`、`mode`、`profile`、`status`、`verdict`、`sort`；`offset` 默认 0，`limit` 默认 100 / 上限 500。detail API 返回 typed Read Model 投影，Timeline 单独通过 events API 获取，避免每次轮询重复传输整条 Trace。events API 使用 `cursor`（有效事件位置）、`generation`（文件替换/截断代数）、`limit`（默认 500 / 上限 1000）；返回 `next_cursor`、`total`、`reset` 和 warnings。文件替换后客户端清空旧 Timeline 并从头读取。没有任何 `/api/v1/` 代理或写入端点。

工件路由为 `/tasks/{task_id}/artifacts/{allowlisted-name}`，返回经过脱敏的 `text/plain`。允许根 task/state/problem JSON、problem.md、solution.cpp、result.json，以及 artifacts 下 result/code-version/submission-created/submission-final/submission/feedback/model-response JSON、plan/review Markdown。events.jsonl 经 Timeline 展示；其他文件没有通用读取入口。固定白名单不递归列出 arbitrary artifacts。

## 数据与刷新语义

- Summary 由 State 的稳定字段投影；未记录的计数、usage、cost、verdict 保持 None / Unknown。缺失 usage 的任务不把 State 的累计下界包装成完整 token 总数；聚合只相加已知值，并记录 unknown_metric_tasks。已知成本按币种分别累计，未知成本不计为免费。
- `solved` 需要记录的 `solved=true` 与 `last_verdict=AC`；非 solved 包括失败、未完成和不可读任务。model_failure / result_unknown / IE 不伪装成 WA。Overview 包含历史固定解答和模型探针；实验页面保留其原始 mode 分组。
- Phase 1/2 旧 State 的 DONE 无 terminal_status 时显示 completed，未记录的 submission_attempt_count 仍为 Unknown。终态来自 terminal_status 或 DONE，不根据模型文字/summary 推断 verdict。
- task.json 当前没有时间字段；created_at 回退到文件 birthtime（不可用时 mtime），updated_at 使用小文件和 Trace 的 mtime，finished_at 在终态回退到 updated_at。这是文件观察时间，不是恢复出的精确 Agent 起止时间。耗时仅使用已记录 wall_clock_seconds。API 保留原时间；UTC 时间格式化到秒，MiniOJ 未记录时区的资源时间不猜时区。
- LLM / Tool 通过 payload.correlation_id 合并；提交与候选通过 solution_version / code_sha256 / model_call_id / submission_id 关联。哈希不一致时报告 warning，不把该提交 verdict 附给候选；当前 solution.cpp 与 State 哈希不一致也会提示。没有记录关联时显示 Unknown，不靠事件相邻位置猜关联。
- 详情每 2 秒读取 State 和增量 JSONL；运行中终态出现后补读一次，覆盖 DONE 写入早于最终 Trace append 的正常顺序，然后停止。直接打开终态任务不启动 polling。暂时失败保留上一次有效显示，不锁定或修改 Agent 文件。

## 安全与范围限制

文件 resolve containment、逐段无符号链接，以及打开时 dir_fd / O_NOFOLLOW，防止读取期间链接替换；只允许 regular file。单文件上限 2 MiB、单 Trace 上限 16 MiB，超限显示 unavailable；JSONL 坏行跳过并提示，未完成尾行等待完整换行。索引只有一层目录发现和小文件 stat；不递归扫描 Workspace，不解析列表 Trace。缓存存在内存中，不提供数据库、断点恢复或磁盘迁移。

输出复用 `agent.workspace.task._redact`，附加敏感 key、文本赋值、环境及 `.env` 的已知 secret 值过滤。Provider 配置仅投影展示字段，不实例化 Provider，不返回 endpoint / key / token。模板自动 HTML escape；JS 使用 textContent / DOM 创建节点，CSP 只允许本地脚本资源。默认 no-store、nosniff、拒绝非 localhost Host，禁用服务器 access log。源文件内容是文本，工件不会作为 HTML 返回。

V1 不含任务控制、retry/delete/edit/submit、模型调用、MiniOJ 调用、正式实验模型冻结、成本价格选择、Harness Loop、Experiment Runner、CSV、stress testing 或外部访问。多版本 fixture 只证明 Dashboard 能显示未来事件。没有改 Agent 的持久化 schema。

## 修改文件

新增 `dashboard/__init__.py`、`models.py`、`security.py`、`config.py`、`cli.py`、`app.py`；`repository/{__init__,workspace,trace,configuration}.py`；`services/{__init__,tasks,metrics}.py`；`templates/{base,macros,overview,tasks,task_detail,experiments,models}.html`；`static/css/dashboard.css`、`static/js/dashboard.js`、`static/icon.svg`；`client_tests/test_dashboard_reader.py`、`test_dashboard_routes.py` 和本文。更新 `pyproject.toml`、`uv.lock`、README / README_zh、TODO 的独立 Dashboard 章节。当前工作区已有的 Agent Phase 2/3 修改保留；本次没有编辑 Agent 运行代码。

语言扩展新增 `dashboard/i18n.py` 与 `static/js/i18n.js`；更新模板、呈现过滤器、轮询脚本、语言按钮样式、Route 测试与文档。未增加任务控制模块或启动参数。

## 验证记录（2026-10-01）

```bash
uv sync --extra dev --extra dashboard
uv run --no-sync pytest
```

缺少 dashboard extra 时，纯 Reader 测试照常执行，Web Route 测试明确 skip；完整 Dashboard 验收必须安装两个 extra。

| 阶段 / 证据 | 结果 |
| --- | --- |
| D0 原客户端回归 | 85 passed |
| D1 Reader 首次验收 | 14 passed；覆盖 A–G 人工 fixture、坏任务、append / partial tail / replace、路径/符号链接、secret、Unknown 和分组 |
| D2 CLI / route / optional 边界专项 | 15 passed |
| D3 HTML / 页面 / 安全 API | 五个页面与六组 API 返回 200；真实 Phase 3 工件显示 230/222 tokens、3931 ms model latency、solution-v1 / submission 12 / AC、19 事件 |
| D4 Models / unknown 语义专项 | 2 passed；实际选中 YAML、无配置/坏配置、脱敏；空组为 No runs |
| D5 全量客户端回归 | 127 passed = 原 85 + Dashboard 42；新增当前源码/提交 hash mismatch 测试 |
| Chrome 宽屏 / 400px 窄屏 | Overview 和 Task Detail 视觉检查通过；恶意 script 文本显示为文本，无 JS 执行错误 |
| Chrome 人工 polling fixture | 不刷新页面新增第二个模型调用、版本、提交；Timeline 9 → 16；终态显示 refresh stopped；仅使用 `/private/tmp` 人工文件 |
| `uv build --offline` 与 wheel / sdist 检查 | 模板、CSS、JS、SVG 入包；第五个客户端 CLI 注册；Web 依赖仅带 dashboard extra marker；无 MiniOJ server/shared/tests |
| sdist 解包后的独立客户端测试 | 127 passed |
| 普通 `uv sync --offline` 的 live 安装检查 | FastAPI / Jinja2 / Uvicorn 不存在；Dashboard --help 可用；仅 dev extra 为 100 passed, 1 skipped |
| Python 3.9.6 compileall / JS syntax / git diff --check | 全部通过 |
| 当前四任务只读页面、API、所有白名单工件 | secret 实际值扫描 clean；Workspace 文件 SHA-256 读取前后不变；Phase 3 model call / code version / submission / source hash 独立对账通过 |

D0–D5 均已实现并验证；测试无真实模型 / MiniOJ 请求。验收启动命令为 `uv run --extra dashboard codeharness-dashboard --workspace-root workspace`，本次本地页面使用 127.0.0.1:8765。

### 中英文扩展验证（2026-10-01）

| 证据 | 结果 |
| --- | --- |
| `uv run --no-sync pytest` | `142 passed`，在原 127 项基础上新增 15 项语言回归；零真实模型 / MiniOJ 请求 |
| ASGI / 临时 Workspace | 五类页面可见标签切换、Cookie 持久化、并发请求语言隔离、无效语言回退、重定向过滤、HTML escape、语言不改变 JSON / 工件与 Workspace |
| Chrome，临时只读服务 `127.0.0.1:8767` | 中文概览、任务筛选、详情及 400px 窄屏可读；英文回切成功；人工任务追加第二版本 / 模型 / 提交后自动显示中文成本与刷新提示，终态停止；Console 无错误 |
| Node 执行前端语言脚本 | 同一字典的中英文动态提示、参数化诊断、原始模型 / AC 值、筛选查询与详情锚点保留均通过 |
| `uv build --offline`、sdist 独立测试 | wheel / sdist 包含字典与新脚本，无任务控制模块、无 MiniOJ 内部源码；sdist `142 passed` |
| Python 3.9.6 compileall、两份 JS 的 `node --check`、`git diff --check` | 全部通过 |

人工任务只写入 `/private/tmp`，用于展示验证；Dashboard 不修改真实 Workspace。测试服务使用 8767 并在结束后停止，不占用用户的 8765 服务。重启已有 Dashboard 即可加载新的语言路由与脚本。

## D6：简化界面与发起任务（2026-10-01）

CLI 默认启用任务控制，`--read-only` 保留历史只读路由/首页；程序化 `DashboardSettings` 默认只读以保持兼容。首页 `/` 改为题目 ID、mode、固定/混合策略、必选调用确认、“开始任务”，只保留三个统计和五列最近任务。code-only 自动禁用混合选项并说明单次 CODE/不跑样例/不重试。任务列表保留一个搜索框和 mode/Profile/status/verdict 筛选；详情保留四个核心计数、评测结果，源码/模型/工具与 Timeline/工件折叠为高级诊断，中英文与轮询仍可用。

```bash
uv sync --extra dev --extra dashboard
uv run --extra dashboard codeharness-dashboard --workspace-root workspace \
  --model-config config/models.yaml --harness-config config/harness.yaml
# http://127.0.0.1:8765；旧进程需要重启才能加载控制路由
```

浏览/轮询只读本地文件；确认 POST 才排队。模型/OJClient 由 worker 调用，不经 Shell/subprocess。原费用输入现在扩展为 D7 “任务配置”入口（见下），允许明确白名单的新任务覆盖，不改 YAML/凭据/历史任务；路径/endpoint/命令不可覆写。串行队列最多 8 个等待任务，单任务最多 10 POST。配置/累计预留落盘，恢复不能改配置/重置。缺价格/上限付费前停。UI 多任务无整批 cap，批量保护使用 Experiment Runner。

CLI 默认 `--feedback-mode verdict_only`：用户确认按实际 Agent 仅取得受限 verdict 的条件运行，不假定隐藏用例可见。要求服务端明确实际模式，无法核实/不匹配时模型前 condition_mismatch，不代填 actual；最近一次远程 `/me` 记录未提供，改变期望不等于修复缺少元数据。保留显式 `--feedback-mode full` 用于独立实验，参数不改变服务器权限。旧 Dashboard 需重启；旧任务的 full/unknown 条件冻结，不由新默认值改写。

新增 full → verdict_only 单向兼容：服务端明确 full 时仍启动，但正式提交/查询/反馈仅保留 ID/状态/verdict，远程自由文本、诊断/隐藏用例、全部扩展字段与正式错误正文在写工具日志/工件/检查点/模型前过滤。公开样例结果不变。首页提示兼容降级，详情/API 区分 OJ actual 与 Agent effective；State/result/实验分组记录 formal_verdict_only_v1 策略。未知模式不放行；显式 full 仍要求 actual full。过滤策略纳入指纹，旧精确 verdict-only job/checkpoint 不自动改配置，需新任务 ID；原 explicit full/unknown 工件保持。

新增本地 API（仅 launch 模式）：

| 方法/路径 | 语义 |
| --- | --- |
| `POST /api/dashboard/launch` | `{request_id, problem_id, mode, profile, task_config?, confirm_remote_calls:true}` → 202；六项配置见 D7。兼容旧 max_cost_cny 平铺字段，但与 task_config 双写拒绝；路径/command/任意 policy 不接受 |
| `POST /api/dashboard/tasks/{task_id}/resume` | `{request_id, confirm_remote_calls:true}` → 202；只支持本 Dashboard 发起、配置相同且可安全恢复的 harness |
| `GET /api/dashboard/jobs/{request_id}` | 只返回请求/任务 ID、operation、status 和安全 error kind |

写入要求精确同源 `Origin`（含端口）、`Sec-Fetch-Site` 非跨站、`X-CodeHarness-CSRF` 随机本地令牌、JSON ≤8 KiB、明确布尔确认。localhost Host/loopback、CSP、脱敏、文件安全与无 access log 保持。POST 不代理 MiniOJ 或暴露密钥；同 request_id 同意图返回已有任务，不同意图 409；队列满 429，参数非法 422。只读模式不注册控制端点。

`workspace/.jobs/<request_id>/job.json` 原子保存意图/原配置/状态，不含凭据，不被任务索引扫描。重复点击不重跑，活跃恢复不允许另排一次；任务本身仍有 Workspace 锁。重启后旧 queued/running 显示 interrupted，绝不自动付费重启。详情只为 eligible harness 显示手动恢复；既有模型响应/已知提交 ID 按原 checkpoint 处理，不确定模型调用/创建 POST 不重发。中断 code-only 不自动重新生成。

Phase 5 新 State 带实际配置指纹、experiment ID/strategy、反馈 mode/source/status；只读实验分组同时区分指纹和实际反馈状态，不用混合默认策略冒充 fixed standard，也不把 unknown 标为已确认条件。报告 JSON/CSV 仍通过实验目录读取，不开放任意文件下载或目录列表；白名单新增 `artifacts/execution-config.json`。

验证：`client_tests/test_dashboard_launch.py` 22 项 ASGI/真实 worker 路径替身测试，覆盖两种模式、排队后执行、确认/CSRF/Origin/端口/Host、容量、nonce、防重复恢复、重启不重跑和已知 ID 恢复；完整自动测试命令/数量见 TODO。浏览器核验中文简化首页、确认未勾选阻止发起、code-only 自动选择 fixed standard/禁用 mixed、英文回切及折叠详情；本轮浏览器未额外发起付费任务。真实 T1003 两模式通过同一执行服务的 Experiment Runner 联调，不将 ASGI 替身当作真实 UI 付费验收。

后续额度/full 决策验证记录见 TODO 对应追加节；上述 22 项为原 D6 历史记录，不是最新总数。新增测试覆盖可调高额度、两模式小额拦截、nonce 额度漂移、恢复不能加钱/重复 POST、实际模式未知拦截和非法额度；真实浏览器验收不替代付费 full 循环。

## D7：任务配置入口（2026-10-01）

“任务额度”区域改为可展开的“任务配置”，默认折叠显示金额/模型调用次数/提交次数摘要。展开提供六项设置和“恢复任务默认值”。重置不清空题目、不取消已勾选确认；切到 code-only 锁定次数为 1，切回 harness 恢复之前填写的次数。折叠区域有无效输入时自动展开以便定位。

| task_config 字段 | 默认来源 / 校验 |
| --- | --- |
| max_cost_cny | YAML/CLI 默认 1 元；有限正数 |
| max_llm_calls | harness YAML 默认 80；正整数，计 PLAN/CODE/DEBUG 调用 |
| max_submissions | harness YAML 默认 10；1–10 正整数，正式 POST 尝试（不是公开样例） |
| http_timeout | 启动默认 15 秒；有限正数，仅 MiniOJ HTTP，不改模型 Provider 超时 |
| poll_interval | 启动默认 1 秒；有限非负数 |
| deadline | 启动默认 120 秒；有限非负数，单次正式评测等待上限，不是整题活动时间 |

code-only 明确覆盖/默认的次数均只能为 1；不会开启循环或多次 POST。不存在“保存为全局默认”的写文件动作，每次新表单读取服务器 YAML/CLI 默认。旧平铺费用 API 保持兼容，不能与新 task_config 并用。嵌套字段、类型、NaN/Infinity/超大超时/越界次数非法时 422，排队前拒绝；服务端不信任表单 min/max。DEBUG 阈值、反馈权限、Role/模型参数、原生工具调用、模型升级不在此入口。

LaunchService 仍记录完整原始 RunRequest，ExecutionService 冻结实际预算/网络等待参数；同 nonce 改配置为 409，resume 不接受 task_config，原配置严格匹配后才继续。所有读侧/CSRF/重启不重跑语义保持。替身 worker 验证模型调用/提交上限实际停机及超时传给 OJClient/轮询，Node 验证提交数值、摘要/复位/模式切换/无效输入展开；命令/结果见 TODO。

## D8：比赛测试入口（2026-10-02）

左侧新增“比赛”；`/contests` 使用同一个任务表单，填写比赛 ID、整场预算（默认 1 元）、mode/Profile，原六项任务配置逐题生效。明确确认后入同一个串行队列，一题结束再执行下一题。比赛题单来自公开 HTTP 页面，题面与评测仍走现有 JSON API；页面 GET/轮询不读取远程，不调用模型。未知反馈模式/未公开题单/协议变化在付费前停止，不绕过权限或报名。

`/contests/{run-id}` 与 `GET /api/dashboard/contests/{run-id}` 从 `.contests/<run-id>/report.json` 读取轻量投影；列表 API 为 `GET /api/dashboard/contests`。ContestRepository 复用 WorkspaceRepository 的 2 MiB/regular-file/dir_fd/O_NOFOLLOW/逐段路径保护与脱敏，列表最多展示 100 项、浅扫候选最多 1000。报告包括 AC/总题数、逐题状态/verdict、模型/POST 次数和估算成本，逐题链接到原 Task Detail；2 秒刷新到终态，JS 只用 textContent。官方分数、罚时、排名留空，不将未开始或结果未知计为 AC。

`POST /api/dashboard/contests/launch` 允许 `contest_id`、`total_cost_cny`、mode/profile、task_config、nonce/确认；不接受任意 problems/模型路径/评分代码。恢复端点为 `POST /api/dashboard/contests/{run-id}/resume`，只接 nonce/确认，自动还原原配置；配置漂移或重复活动恢复为 409。沿用 CSRF、Origin、Host、8 KiB、串行队列与持久 nonce，同 nonce 重发不再入队。`--read-only` 没有比赛控制端点，仍可查看本地比赛报告；重启不自动重跑，安全检查后才显示恢复按钮。

Chrome 桌面实测：中文表单、双栏布局、真实比赛 1 报告可读；使用整场 `0.000001` 元启动 `contest-ui-44c7112131d948a8b75e`，表单→queued→自动完成，两题 budget_exhausted、零模型/零 POST。另一次 CLI 真实付费比赛运行保留 1/2 AC、5 模型/1 POST/估算 0.0255813 元。浏览器测试不追加付费补测；机器/API 值不随语言改变。专项/全量/发行测试与未完成事项见 TODO。
