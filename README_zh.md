# CodeHarness

[English](README.md) | [简体中文](README_zh.md)

CodeHarness 是一个用于研究和实验的编程智能体运行框架（Coding Agent Harness），目标是分别衡量两类收益：

1. 更强语言模型带来的收益；
2. Agent Harness、评测反馈、测试生成和迭代控制循环带来的收益。

项目按照可运行、可验证的阶段逐步实现。**目前已完成 Phase 1：** 基于 FastAPI/SQLite 的在线评测系统（Online Judge，OJ）基础，包括真实用户系统、浏览器会话、管理员界面和题目的增删改查。评测执行、机器调用 API 和 Agent 的后续任务均记录在 [TODO.md](TODO.md) 中，尚未实现的功能没有提供占位实现。

## 总体架构

```text
MacBook                                     WSL Ubuntu
CodingAgent → Tools → typed OJ Client ─────► FastAPI OJ Server
   │                                             │
Model policy → router → provider              Worker       (Phase 2)
                                                 │
                                            Docker sandbox (Phase 2)
```

Mac 负责运行后续的 Agent、模型供应商调用、任务工作区和实验；WSL Ubuntu 负责运行 OJ Web/API 服务、Judge Worker 和 Docker 沙箱。Agent 仅通过 HTTP 调用 OJ，无需了解 WSL、Docker、编译器或评测文件的内部实现。具体职责边界见 [docs/architecture.md](docs/architecture.md)。

## 当前功能（Phase 1）

- 注册、登录、仅接受 POST 的退出登录、个人资料编辑和密码修改。
- Argon2id 密码哈希、使用签名 Cookie 的 HttpOnly 浏览器会话，以及修改数据的表单所需的 CSRF 防护。
- 公开的题目列表和题目详情页面。
- 管理员创建、编辑、删除题目，以及管理用户角色和启用状态。
- 可配置的 SQLAlchemy 数据库，以及数据库初始化和管理员创建命令。
- 基于 Jinja 的响应式服务端渲染界面，无前端框架依赖。

API Token、提交记录、自定义运行、测试用例和评测执行将在 Phase 2–3 实现。当前版本不执行不可信程序。

## 环境要求

- Python 3.9+
- 推荐使用 [`uv`](https://docs.astral.sh/uv/)，也可使用 Python 虚拟环境和 `pip`。
- OJ 的目标部署环境为 WSL Ubuntu。从 Phase 2 开始需要 Docker，Phase 1 无需 Docker。

## 快速启动

在仓库根目录执行：

```bash
uv sync --extra dev
cp .env.example .env
```

将 `.env` 中的 `SECRET_KEY` 替换为自己的密钥，再把配置加载到当前 Shell 的环境变量中：

```bash
set -a
source .env
set +a
uv run codeharness-admin init-db
uv run codeharness-admin create-admin --username admin --email admin@example.com
uv run codeharness-oj
```

打开 <http://127.0.0.1:8000>。服务监听 `0.0.0.0:8000`，在网络和防火墙允许的情况下，Mac 可以通过 WSL 主机地址访问服务。

未设置 `SECRET_KEY` 时，服务会拒绝启动。请使用足够长的随机值；通过 HTTPS 提供服务时，启用 `SESSION_HTTPS_ONLY=true`。

## 数据库初始化与管理员创建

默认数据库文件为 `data/codeharness.db`。可通过 `DATABASE_URL` 指定 SQLAlchemy 数据库连接地址。

初始化数据库表结构：

```bash
uv run codeharness-admin init-db
```

创建管理员账号；密码通过无回显的交互提示输入，不接受命令行密码参数：

```bash
uv run codeharness-admin create-admin \
  --username admin \
  --email admin@example.com
```

公开注册始终创建 `user` 角色，不能直接获得管理员权限。

## 创建题目

1. 使用管理员账号登录。
2. 打开 `/admin` → **Problems** → **New problem**。
3. 填写稳定的小写题目 ID、题面、资源限制，以及可选的来源信息、难度评分和逗号分隔的标签。

题目 ID 创建后不可修改。OJ 不依赖 Codeforces 特定逻辑，也不提供自动导入功能。

## 测试用例上传与 Judge Worker

这部分从 Phase 2 开始实现，Phase 1 尚无对应命令。采用的设计如下：

- SQLite 保存测试用例元数据，输入与输出正文存放在 `data/problems/<problem-id>/tests/`。
- 独立的轮询 Worker 领取状态为 `QUEUED` 的提交。
- C++20 的编译与执行均在受限 Docker 沙箱内进行。
- 使用稳定的 `QUEUED → COMPILING → RUNNING → FINISHED` 状态流转，以及统一的评测结论枚举。

测试用例导入和 Worker 启动命令将在该阶段可运行后补充到 README。

## Agent 与阿里云百炼配置

Mac 端 Agent 从 Phase 4 开始实现。`.env.example` 已列出所需的连接与认证配置项：`OJ_BASE_URL`、`OJ_API_TOKEN`、`BAILIAN_API_KEY` 和 `BAILIAN_BASE_URL`，不包含真实密钥。具体模型名称将写在不含密钥的模型档位配置中，不会写入 Agent 循环代码。

`code-only` 和 `harness-loop` 是 Phase 5 的实验命令，当前尚不可用：

- `code-only`：模型生成一次代码、提交一次，不读取反馈、不重试。
- `harness-loop`：按照 PLAN/CODE/TEST/DEBUG/REVIEW 阶段运行，支持评测反馈、重试和模型路由。

## 测试

```bash
uv run pytest
```

测试使用独立的临时 SQLite 数据库，覆盖身份认证、用户设置、权限控制、管理员用户管理和题目增删改查。

## 项目目录

```text
CodeHarness/
├── agent/                 # Phase 4–5：Agent 运行时代码待实现
├── config/                # 后续模型档位与运行配置
├── docs/
│   └── architecture.md
├── experiments/           # Phase 5：实验定义
├── oj/
│   └── server/            # Phase 1：FastAPI 应用、模型、路由和模板
├── shared/                # 按需添加稳定的跨进程类型
├── tests/
├── .env.example
├── README.md
├── README_zh.md
├── TODO.md
└── pyproject.toml
```

## 开发原则

明确评测系统、Agent、模型供应商和 Web/API 的职责边界。仅在当前阶段需要时引入基础设施。用户代码不得在 Web 进程中运行，也不得为图方便而暴露隐藏测试用例或模型供应商的内部实现。
