# CodeHarness

[English](README.md) | [简体中文](README_zh.md)

当前能力与冻结行为见 [STATUS.md](STATUS.md)，未来项见 [TODO.md](TODO.md)；原阶段/Dashboard 证据完整保存于 [历史档案](docs/history/README.md)。
CodeHarness 是编程智能体研究环境的 macOS 客户端，负责单 Agent Runtime、模型路由、工具、逐任务 Workspace、Trace 与实验汇总。MiniOJ 是独立部署的服务，评测只通过 Bearer Token 认证的 HTTP JSON API；比赛题单暂无 JSON 接口，另适配公开 HTTP 页面，不读取服务端内部数据。

Phase 0–5 实现与替身验收已完成；Phase 5 的 T1003 两模式最小真实实验失败结果保留。现已配置四个固定组加一个混合组、每组一次、Agent 期望 verdict_only，服务端明确返回 full 时允许客户端过滤兼容；真实五组比较尚未运行。发行包是纯客户端，包含 `agent`、`experiments` 和可发起任务的本地 `dashboard`；旧内嵌 MiniOJ 服务端、共享 Judge 与服务端测试已移除。本地与真实联调证据分别记录。

## 当前已验证范围

- 客户端独立的构建清单、依赖、命令入口和环境配置；
- CodeHarness 自己定义的题目、提交、反馈、错误、模型响应、State 与 Trace 事件类型；
- 带 Bearer 认证的类型化 HTTP 操作、成功状态/JSON 校验、传输/HTTP/协议/结果未知错误分类，以及完全封装在 OJClient 的轮询；
- 显式 Custom Run 兼容模式：默认只发送规范字段 `source_code`，旧 `code` 别名必须主动开启；
- 带参数 schema 的工具、原子 Workspace 文件、托管文件保护、显式覆盖、路径穿越/符号链接拒绝，以及有关联 ID 和脱敏的 Trace；
- 不依赖模型的固定解答工作流，保存题目、源码、提交状态、verdict、反馈、State 与 Trace，且不在本地执行代码；
- 统一模型响应、安全错误、可缺失 usage、工具调用、四 Profile 配置路由、显式 Role Policy，以及成功/失败均可追踪的 ModelCallRuntime；
- 缺失 usage 或价格时成本保持 unknown/null，不把未知成本记成零或免费；
- 带状态标记的协议样例 fixture，明确区分已约定核心、细节草案、未确认错误结构和仅用于前向兼容的未知值；
- 任务工件只忽略仓库根目录 `/workspace/`，不会误伤 `agent/workspace/` 源码；
- 不加载 MiniOJ 服务端 fixture 的客户端 pytest 入口；
- 严格 code-only：一次 CODE、至多一次正式提交、零 Custom Run / feedback，异常也保存明确终态；源码版本通过模型调用 ID、SHA-256 和提交 ID 关联；
- 固定 harness 状态机、样例门禁、3 个 DEBUG 候选失败重 PLAN、单任务最多 10 次提交/默认 1 CNY 可调额度、只读 REVIEW、单题 Context 与检查点恢复；
- 五组批量配置、独立任务额度与整批总预算、JSON/CSV 导出、Trace 对账和安全恢复；Dashboard 额度输入、本地串行队列与确认/防重/CSRF 守卫；
- 客户端自动测试只使用 fixture、Fake、MockTransport 与本地 ASGI；命令和最新数量见 [本轮验证](docs/evidence/20261002-trust.md)。
- 一次真实 MiniOJ 固定解答链：`t1001` 提交 `sub_FhqFt4PN68kPAJ7N` 得到 `FINISHED / AC`，5/5 测试通过，零次 LLM 调用。
- 一次真实模型探针：`qwen3.8-flash` 返回预期文本，usage 为 74/46 tokens；它不自动成为正式 Profile 映射。
- 一次真实 code-only 链：临时把 `standard` 指向 `qwen3.8-flash`，`t1001` 恰好一次 CODE、一次正式提交 `12`，得到 `FINISHED / AC`，usage 为 230/222 tokens。

Phase 4 另验证了真实 `t1001` 公开样例及缺价格守卫。2026-10-01 Phase 5 的 T1003 两种模式各一次，共 4 次模型调用、1 次样例试跑、0 次正式提交，均以 `invalid_model_output` 结束；harness 在样例失败后进入一次 DEBUG，但修正版未形成完整代码。按用户配置估算成本 0.0421137 CNY，保守预留 0.1490944 CNY，不等于账单核验。服务端未返回 Feedback Mode，记录为 unknown，不能宣称公平反馈比较或完整真实 AC 循环。

## 架构边界

```text
macOS CodeHarness                                      远程 MiniOJ
┌─────────────────────────────────┐                  ┌───────────────────┐
│ Agent / Context / State / Trace │                  │ HTTP JSON API     │
│ 模型策略 / 路由 / Adapter       │                  │ Judge / Testcase  │
│ Tools → CodeHarness OJClient    ├── Bearer HTTP ─►│ Sandbox / Worker  │
└─────────────────────────────────┘                  └───────────────────┘
```

CodeHarness 不 import MiniOJ 内部模块，不共享数据库或 testcase，不启动本地 Judge，不在本地执行提交的 C++，也不使用 SSH/远程 Shell 绕过 API。详见 [docs/architecture.md](docs/architecture.md) 与 [TODO.md](TODO.md)。

## macOS 起步

需要 Python 3.9+ 与 [`uv`](https://docs.astral.sh/uv/)。

```bash
uv sync --extra dev
cp .env.example .env
cp config/models.example.yaml config/models.yaml
cp config/harness.example.yaml config/harness.yaml
uv run codeharness-agent --help
uv run codeharness-model-client --help
uv run codeharness-oj-client --help
uv run codeharness-report --help
uv run codeharness-experiment --help
uv run pytest
```

默认测试只收集 `client_tests/`，不会访问网络、调用模型或向 MiniOJ 提交。

`.env.example` 有意保留空 endpoint 和密钥槽位：

```dotenv
OJ_BASE_URL=
OJ_API_TOKEN=
BAILIAN_API_KEY=
BAILIAN_BASE_URL=
MODEL_CONFIG=./config/models.yaml
```

实际运行时通过 `.env` 提供 OJ 和模型 Provider 连接；模型 ID、参数、Role 映射与可选价格来自 YAML。不要提交 `.env`；它已被忽略。`config/models.example.yaml` 中只有占位模型且不含价格，不代表正式模型或计费已经确认。

## 模型客户端

`probe` 固定只发送一条短连通性提示，并把 Provider、模型、usage、耗时、成本状态与关联 Trace 写入独立 Workspace。它会产生一次真实模型调用，因此必须显式确认：

```bash
uv run codeharness-model-client probe \
  --profile <fast|standard|strong|max> \
  --model-config <models.yaml> \
  --workspace-root workspace \
  --confirm-call
```

调用不会自动重试。缺失 usage 会单独计数；缺失价格时 `estimated_cost` 为 `null`。Phase 2 探针和 Phase 3 code-only 验证所用的 `qwen3.8-flash` 都不是正式 Profile 选择。

## 无模型 MiniOJ 客户端

只读取清洗后的题目，不创建提交：

```bash
uv run codeharness-oj-client inspect-problem <problem-id> \
  --http-timeout <秒>
```

运行 Phase 1 固定解答链路。U03 尚未确认默认值，因此传输和轮询参数都必须显式给出；该命令会创建真实远程正式提交，所以必须提供 `--confirm-submit`。

```bash
uv run codeharness-oj-client submit-fixed <problem-id> <solution.cpp> \
  --workspace-root workspace \
  --http-timeout <秒> \
  --poll-interval <秒> \
  --deadline <秒> \
  --confirm-submit
```

命令不会在本地编译或运行源码，只走 MiniOJ HTTP。任何请求都不会自动重试；正式提交超时，或 202 响应无法解析为有效 QUEUED 记录时，会记录为 `result_unknown`，防止盲目重复 POST。

## Phase 3 code-only

```bash
uv run codeharness-agent solve <problem-id> \
  --mode code-only \
  --profile <fast|standard|strong|max> \
  --model-config <models.yaml> \
  --workspace-root workspace \
  --http-timeout <秒> \
  --poll-interval <秒> \
  --deadline <秒> \
  --confirm-model-call \
  --confirm-submit

uv run codeharness-report workspace
```

`solve --mode code-only` 只调用一次指定 Profile 的 CODE；只有成功提取候选时才尝试一次正式提交，不试跑样例、不获取 feedback、不修复或重提。所有终态写入 State、Trace 与 `artifacts/result.json`。直接 CLI 和兼容入口也启用默认 1 元费用预留守卫，需要有效 CNY 价格和 token 上限；`--max-cost-cny 2` 可调整额度。

## Phase 4 harness 与恢复

先在实际模型 YAML 为使用的 Profile 填入已确认的 `input_cost_per_million` / `output_cost_per_million`、`currency: CNY`、有效 `input_token_limit` 与 `parameters.max_tokens`；参数须列在 Provider 的 `supported_parameters` 中。示例价格/输入上限故意留空，不能直接用于付费循环。

```bash
uv run codeharness-agent solve <problem-id> \
  --mode harness-loop \
  --model-config <models.yaml> \
  --harness-config config/harness.example.yaml \
  --workspace-root workspace --task-id <task-id> \
  --http-timeout <秒> --poll-interval <秒> --deadline <秒> \
  --confirm-model-call --confirm-submit

uv run codeharness-agent resume <task-id> \
  --model-config <同一 models.yaml> --workspace-root workspace \
  --http-timeout <秒> --poll-interval <秒> --deadline <秒> \
  --confirm-model-call --confirm-submit
```

默认 PLAN → strong、CODE / DEBUG → standard，不升级；可加 `--profile standard` 明确固定所有模型 Role。新任务冻结 sample_check_v3：可信 exact/token/双显式容差 float 做语义检查，其他输出用 execution-only 公开样例。已知 OK/exit=0 后允许正式 Judge，passed=null；运行失败可 DEBUG，未知/基础设施错误停止。Main 关闭 LLM checker，旧 v1/v2 实验性门禁保持冻结；3 个 DEBUG 候选连续失败后重 PLAN。正式 AC 后 REVIEW 只核对被评测版本/哈希并写总结，零模型调用、不改源码。

每个独立任务最多 10 次正式 POST 尝试、默认 1 CNY；YAML `max_cost_cny` 或 CLI `--max-cost-cny 2` 可调高/调低。新任务调用前按完整输入/输出上限预留费用，收到可信 usage 后按配置单价结算实际输入/输出 token，释放未使用的差额；`budget_committed_cny` 表示已结算费用加尚未确认的预留，单题和整场预算均使用此值。Trace 的 `BUDGET_SETTLEMENT` 保留预留、结算、释放与调用关联。未知/中断/超上限用量保留预留并停止，不按免费处理；旧冻结检查点保留原有累计预留策略，不回写历史结果。配置单价估算不等于账单，尚不自动计入缓存折扣、限时优惠或免费额度。账单约束依赖价格/Provider 上限准确有效；缺 CNY 价格/请求上限、缺 usage 或 usage 超上限即停止。额外 80 次 LLM 熔断防止无限循环；墙钟上限可选。

新 Harness 任务在 CODE/DEBUG 无法提取完整 C++20 代码时，沿用原 Role/Profile 要求模型纠正输出格式；`config/harness.yaml` 的 `max_code_extraction_retries` 默认 `3`，即首次生成后最多额外重试 3 次（`0` 禁用）。每个新候选独立计数；每次纠正计入 LLM 次数和费用预留，仍受输入/费用/时间上限约束。失败输出不执行、不提交，Trace 记录 `CODE_EXTRACTION_RETRY`；用尽次数仍以 `invalid_model_output / code_extraction_failed` 结束。该重试只处理已收到的无效代码输出，不重发未知请求，也不重试模型传输错误。code-only 和独立 checker 生成保持原有次数；旧检查点不自动增加重试，已 DONE 任务不会重跑。

`checkpoint.json` 原子保留单题 Context、进度、计数、响应与实际配置，`state.json` 是投影；题面/计划/源码完整保留，最新反馈提示最多 8000 字符、历史最近 5 条，超长输入不静默截代码。恢复要求模型/价格/上限、预算及 OJ endpoint 与检查点一致，且单任务互斥。已知提交 ID 继续查询，已保存模型响应复用；中断后模型请求或提交 POST 是否生效未知时停止为 `result_unknown`，不盲目重发。正式评测轮询超时且 ID 已知时可 `resume`；一般 DONE 重读结果不再调用。

## Phase 5 批量实验

先核对本地 `config/models.yaml` 的真实模型、有效 token 上限和 CNY 单价。`config/experiment.smoke.yaml` 使用 T1003、固定 standard、两种模式各一次、整批最多 1 元；这不是自动展开四组的大规模实验。

```bash
uv run codeharness-experiment run config/experiment.smoke.yaml \
  --experiment-id <新的实验-id> --workspace-root workspace \
  --confirm-model-call --confirm-submit

uv run codeharness-experiment resume config/experiment.smoke.yaml \
  --experiment-id <原实验-id> --workspace-root workspace \
  --confirm-model-call --confirm-submit
```

各策略/重复是独立任务，各自最多 10 次正式 POST、默认 1 元，不再共享同题的 1 元额度；整批仍受显式 `total_cost_cny` 约束。实验 CLI `--max-cost-cny` 仅调整单任务，不自动提高整批 cap。code-only 仍只有一次 CODE、不跑样例；用量已知的调用按实际 token 结算（包含已返回用量的失败调用），未知调用保留预留，Provider usage 超上限整批停止且拒绝恢复。

报告位于 `workspace/.experiments/<实验-id>/`：`manifest.json` 保存配置、实际模型/Role/价格/参数/endpoint 指纹及任务意图；`tasks.json/csv`、`summary.json/csv` 保存逐任务和分组指标、未知状态与 Trace 对账。完成实验的 resume 仅重建报告，不再次调用 API。中断 harness 用已有检查点/提交 ID；中断 code-only 不自动重新生成。

`config/experiment.full.yaml` 已按确认配置 T1003：standard/strong × 两种模式四个固定组，加 `mixed-harness`（PLAN strong、CODE/DEBUG standard），各 1 次。默认每任务 1 元，整批 cap=5 元是五份额度的总上限，不是预计花费；不自动执行。示例配置也包含五组。现有模型配置和模板的请求输出上限提高到 `max_tokens: 8192`，供应商实际限制/单价仍需核验。新条件请用新实验 ID，旧批次的分配语义、价格/参数/额度不能通过 resume 偷换。

```bash
uv run codeharness-experiment run config/experiment.full.yaml \
  --experiment-id <新的-verdict-only-实验-id> --workspace-root workspace \
  --confirm-model-call --confirm-submit
```

正式配置现要求 `expected_feedback_mode: verdict_only` / `require_feedback_mode: true`；`experiment.full.yaml` 沿用历史文件名，不表示要求完整反馈。实际模式只认可服务端明确字段，不根据权限/diagnostic 推断。2026-10-02 `/api/v1/me` 已明确返回 `feedback_mode: full`，比赛 1 联调保存 actual=full/effective=verdict_only；历史 unknown 工件不回写，未知/不匹配仍在付费前停止。原 smoke 的 unknown 条件不用于公平比较。详见 [experiments/README.md](experiments/README.md)。

新 verdict-only 任务允许服务端明确返回 full：actual_feedback_mode 如实保留 full，effective_feedback_mode 记 verdict_only。版本化策略 formal_verdict_only_v1 在正式提交/查询/反馈结果进入工具日志、工件、检查点或模型前，仅保留提交 ID/状态/verdict；远程 summary、诊断/隐藏用例及全部扩展字段丢弃，正式请求错误正文也不透传。公开样例 Custom Run 输出不受影响。未知仍停止，verdict_only 不能反向满足显式 full 要求。策略写入单题/批量指纹，旧精确 verdict-only 条件需新 ID，不通过 resume 偷换；旧 Dashboard 需重启。

## 测试整场比赛

Dashboard 左侧“比赛”输入比赛 ID，选择固定/混合模型和执行模式，设置整场预算，再勾选确认开始。任务配置逐题生效（默认每题 1 元、最多 10 次提交），整场另有默认 **1 元总上限**；串行执行公开题目，预算不足、失败、未知与 AC 分开显示。提交使用比赛专用接口，不报名、不修改比赛设置。只统计 AC 数/总题数、逐题结果、调用/提交次数和估算成本，暂不计算官方积分、罚时或排名。

```bash
uv run codeharness-experiment contest 1 --experiment-id contest-1-run-001 \
  --profile standard --max-total-cost-cny 1 \
  --confirm-model-call --confirm-submit
# 恢复必须保持相同的模型/预算参数，也可在 Dashboard 点击“恢复比赛测试”。
uv run codeharness-experiment contest-resume 1 --experiment-id contest-1-run-001 \
  --profile standard --max-total-cost-cny 1 \
  --confirm-model-call --confirm-submit
```

报告保存于 `workspace/.contests/<运行-id>/`（`contest.json`、`report.json`、`problems.csv`）；底层批次及单题 Trace/检查点继续使用现有 `.experiments` 和单题目录。恢复固定题单与配置，已完成运行不再调用 API，未知模型请求/POST 不补发；未公开或页面协议变化时付费前阻断，公开后用新运行 ID。`--max-cost-cny`/`--max-llm-calls`/`--max-submissions` 可限制逐题额度/次数；code-only 仍一次 CODE、至多一次提交、不跑样例。

2026-10-02 比赛 `1` 小规模实测：固定 standard、harness-loop，每题 0.35 元/最多 6 次模型、整场 cap=0.7 元。B（CF1454B）提交 `103` 为 AC，A（CF1454A）样例文本不匹配后 DEBUG 无完整代码，未正式提交；汇总 **1/2 AC、5 次模型、1 次正式提交、估算 0.0255813 元**，非供应商账单。A 是多解题，另一合法排列被现有 token 比较拒绝；该历史结果保留；新 v3 任务采用可信语义检查或 execution-only→正式 Judge，不把旧 A 补判 AC。

## Checker、Recovery 与离线实验准备

`config/checker.example.yaml` 保留明确的旧 v1 实验性门禁；新默认 `sample_check_v3` / LLM checker disabled / execution_only。可信 exact/token/双显式容差 float 保持样例语义检查；special/unknown 已知 Custom Run OK + exit=0 记 output_unverifiable / passed=null，交正式 Judge 判定，运行失败与正式 WA 的 DEBUG 原因分开。Main 三个 Harness 策略一致且不生成 checker；旧 v1/v2 checkpoint 不升级。生成/独立 smoke 保留实验用途，执行仅经过 MiniOJ HTTP。详见 [checker policy](docs/checker-policy.md)。

State/result、JSON/CSV 与详情页增加 first_try_ac、recovered_to_ac、sample/formal recovery 分类和 DEBUG/重 PLAN/候选/拒绝/无法验证计数。正式修复链必须有关联的程序失败 → 成功 DEBUG → 后续候选 → 正式 AC → REVIEW；基础设施错误与 LLM checker 拒绝不计算法修复。历史工件只读、不回写。

新 Harness 任务按 task/冻结题目/language/实际提交 UTF-8 SHA 去重正式动作。相同源码复用原 submission ID 与冻结策略下的观察，保留本次候选和模型计数，另计 duplicate；不增加正式 POST/GET，不跨 task，不用 Custom Run 填充缓存，不改重 PLAN 规则。checkpoint 与现有 task lock 保证恢复边界。原 Pilot/Qualification 保留，Pilot-v2 尚未执行；本轮本地验证、新 clean preflight 与独立 Gate 见 [正式去重证据](docs/evidence/20261003-formal-dedup.md)。

`config/experiment.small.yaml` 已准备公开 rating 核实的 12 题，1200/1400/1600/1800 各 3 题，原五组、各一次，共 60 个独立任务。**以下准备命令不执行这些任务。**

```bash
uv run codeharness-experiment prepare config/experiment.small.yaml \
  --experiment-id small-prepared --output /private/tmp/codeharness-small-plan.json
# 可加 --freeze-model-config，仅读本地模型配置快照，不发 HTTP。
uv run codeharness-experiment contest-performance 1 --experiment-id <已保存运行-id>
```

第二条命令只刷新冻结过账户身份的新运行的官方榜单元数据，不加载模型、不运行 Agent。直接使用 rows[].performance，以 user_id 唯一匹配；它是整场账户成绩，包含其他运行，不伪造单题值或公式。旧运行缺冻结身份保持 null。Dashboard GET/轮询只读本地，显式受保护刷新只保存独立 performance.json。模型配置快照区分 configured/verified，未核实供应商字段为 null。详见 [实验协议](docs/experiment-protocol.md)。

## 本地 Dashboard

Dashboard 首页填写题目 ID、模式和固定/混合策略，再展开“任务配置”调整费用、模型调用次数、正式提交尝试次数、MiniOJ HTTP 超时、轮询间隔和评测等待时间；摘要实时显示额度/次数，也可恢复默认值。默认 1 元 / 80 次模型调用 / 10 次提交，code-only 的调用/提交次数锁定为 1。勾选确认后开始，配置会冻结到该任务；不改模型 YAML 或已保存任务，resume 沿用原配置。首页保留三个统计和最近任务，诊断折叠、评测结果可见。

页面右上角 **中文 / English** 可切换语言并记住选择；协议、源码和模型值保持原文。浏览/轮询不发起远程操作；启动/恢复必须显式确认。服务器重启不自动重跑队列，只有支持安全恢复的 UI harness 任务显示恢复按钮。

```bash
uv sync --extra dashboard
uv run --extra dashboard codeharness-dashboard --workspace-root workspace \
  --model-config config/models.yaml --harness-config config/harness.yaml
# 浏览器打开 http://127.0.0.1:8765
```

`--read-only` 禁用启动/恢复端点并保留原观测首页。没有有效模型配置也能浏览，启动时才加载客户端；缺价格/上限在付费前停止。未传 `--model-config` 时使用环境变量或本地 `.env` 的 `MODEL_CONFIG`。仅绑定 loopback，写入要求精确同源、CSRF、确认与持久请求 ID，浏览器不能提交命令或配置路径。默认 HTTP timeout/poll/deadline 为 15/1/120 秒，可由启动参数改。普通 `uv sync` 不安装 Web 依赖。

任务配置默认值来自服务器 YAML/CLI；`--max-cost-cny 2` 优先设费用默认值，HTTP/poll/deadline 默认 15/1/120 秒。表单覆盖仅用于当前新任务，提交尝试仍不能超过 10 次、DEBUG 重 PLAN 仍为 3 次、不升级。模型调用次数包括 PLAN/CODE/DEBUG 与样例 checker 生成，不是所有 HTTP 请求数；公开样例不占正式提交次数。默认 `--feedback-mode verdict_only`，保留显式 `--feedback-mode full` 用于独立完整反馈实验；均不代填实际模式或改变权限。旧 Dashboard 需重启加载默认值，已保存任务仍沿用原条件。多个 UI 任务无整批总额度，整批保护使用 Experiment Runner。

```bash
uv sync --extra dev --extra dashboard
uv run --no-sync pytest
```

Dashboard 测试仅使用临时 Workspace / 本地 ASGI；未知 cost / usage 显示 Unknown，提交尝试与确认提交分开计数。运行中的详情每 2 秒刷新，终态补读一次最后事件后停止。架构、API、读取上限和验收记录见 [docs/dashboard.md](docs/dashboard.md)。

## 仓库结构

```text
agent/                       # 可安装客户端 Runtime
  config.py                  # 仅客户端环境配置
  core/                      # 单 Agent 固定 Loop / Context / Policy
  models/                    # Provider、Registry、Router、调用 Runtime 与探针
  oj_client/                 # HTTP 客户端及 CodeHarness 自有协议类型
  tools/                     # 工具 Runtime
  workspace/                 # 任务 State、文件与 Trace 源码
experiments/                 # 配置、批量执行与 JSON/CSV 报告
dashboard/                   # 可选本地任务启动/观测，支持只读模式
client_tests/                # 独立 Phase 0–5 / Dashboard 测试
config/                      # 不含秘密的模型占位 / harness 预算配置
docs/architecture.md         # 边界与已确认决策
docs/dashboard.md            # 本地 UI / API / 安全与验收
STATUS.md                    # 当前能力与冻结行为
TODO.md                      # 未来未完成项
docs/history/                # 原始阶段/Dashboard/决策证据
```

协议样例位于 `client_tests/fixtures/protocol/`。它们是客户端 fixture，不是共享 Python schema 包，也不证明远程接口已经实现该草案。
