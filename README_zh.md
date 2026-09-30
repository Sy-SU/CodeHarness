# CodeHarness

[English](README.md) | [简体中文](README_zh.md)

CodeHarness 是编程智能体研究环境的 macOS 客户端，负责单 Agent Runtime、模型路由、工具、逐任务 Workspace、Trace 与实验汇总。MiniOJ 是独立部署的服务，双方只通过 Bearer Token 认证的 HTTP JSON API 交互。

Phase 0 与 Phase 1 已经完成。可安装发行包只包含客户端 `agent` 与 `experiments`，不包含 MiniOJ 服务端命令、服务端依赖，也不 import `oj` / `shared`。旧 `oj/`、`shared/` 源码继续作为历史内容保留。Phase 1 的本地测试与真实 MiniOJ 提交证据分别记录。

## 当前已验证范围

- 客户端独立的构建清单、依赖、命令入口和环境配置；
- CodeHarness 自己定义的题目、提交、反馈、错误、模型响应、State 与 Trace 事件类型；
- 带 Bearer 认证的类型化 HTTP 操作、成功状态/JSON 校验、传输/HTTP/协议/结果未知错误分类，以及完全封装在 OJClient 的轮询；
- 显式 Custom Run 兼容模式：默认只发送规范字段 `source_code`，旧 `code` 别名必须主动开启；
- 带参数 schema 的工具、原子 Workspace 文件、托管文件保护、显式覆盖、路径穿越/符号链接拒绝，以及有关联 ID 和脱敏的 Trace；
- 不依赖模型的固定解答工作流，保存题目、源码、提交状态、verdict、反馈、State 与 Trace，且不在本地执行代码；
- 带状态标记的协议样例 fixture，明确区分已约定核心、细节草案、未确认错误结构和仅用于前向兼容的未知值；
- 任务工件只忽略仓库根目录 `/workspace/`，不会误伤 `agent/workspace/` 源码；
- 不加载 MiniOJ 服务端 fixture 的客户端 pytest 入口；
- 55 项客户端测试，只使用 fixture、Fake 与 `httpx.MockTransport`。
- 一次真实 MiniOJ 固定解答链：`t1001` 提交 `sub_FhqFt4PN68kPAJ7N` 得到 `FINISHED / AC`，5/5 测试通过，零次 LLM 调用。

以上完成 Phase 1，但不代表真实模型已经联调。Phase 2 才完善模型层；现有 Loop 代码会继续复用，不能因为固定解答链成功就视为 Phase 3/4 已验收。

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
uv run codeharness-agent --help
uv run codeharness-oj-client --help
uv run codeharness-report --help
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

实际运行时通过 `.env` 提供 OJ endpoint；后续模型联调前仍需选择 Provider 与模型 ID。不要提交 `.env`；它已被忽略。`config/models.example.yaml` 中只有占位符，不代表模型或价格已经确认。

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

命令不会在本地编译或运行源码，只走 MiniOJ HTTP。任何请求都不会自动重试；正式提交超时会记录为 `result_unknown`，防止盲目重复 POST。

## 后续阶段候选命令

```bash
uv run codeharness-agent code-only <problem-id> --profile standard
uv run codeharness-agent harness-loop <problem-id> --max-attempts 4
uv run codeharness-report workspace
```

这些入口已经被打包，解析器与基础 Runtime 可以加载。前两个命令仍需要真实模型配置，属于后续阶段的候选路径。当前升级、价格、预算与 Loop 行为都不是已确认的实验方案。

## 仓库结构

```text
agent/                       # 可安装客户端 Runtime
  config.py                  # 仅客户端环境配置
  core/                      # 单 Agent Loop/Context/Policy 候选实现
  models/                    # Provider 无关类型与路由
  oj_client/                 # HTTP 客户端及 CodeHarness 自有协议类型
  tools/                     # 工具 Runtime
  workspace/                 # 任务 State、文件与 Trace 源码
experiments/                 # 可安装的结果汇总代码
client_tests/                # 独立 Phase 0/1 客户端测试
config/                      # 不含秘密的模型占位配置
docs/architecture.md         # 边界与已确认决策
TODO.md                      # 阶段、证据和未定项
oj/, shared/, tests/         # 保留的旧混合仓库内容
```

协议样例位于 `client_tests/fixtures/protocol/`。它们是客户端 fixture，不是共享 Python schema 包，也不证明远程接口已经实现该草案。
