# Dashboard 当前行为

Dashboard 是可选 loopback Web UI，读取已有 Workspace 与报告。CLI 默认允许明确确认的新任务/比赛/恢复；`--read-only` 保留纯观察模式。历史 D0–D7/比赛实现与证据全文见 [history](history/README.md)；本轮结果见 [验证](evidence/20261002-trust.md)。

## 页面与读侧

首页单题启动、Tasks、Task Detail、Experiments、Models、Contests 与比赛详情。中文/English Cookie 只改 UI 标签，不改 API、源码、模型名或机器值。运行中每 2 秒轮询本地 API，终态停止；浏览/轮询不触发模型/OJ 网络请求。

WorkspaceRepository/TraceReader/ContestRepository 使用 bounded、dir_fd/O_NOFOLLOW 等防护读取白名单工件；路径越界/符号链接/坏 JSON/写入竞争隔离。模板 escape、动态 textContent、敏感值过滤、CSP 与 loopback host 校验保留。未知 usage/cost 保持 Unknown/null；POST 尝试与确认提交分开。

详情新增样例门禁状态与 Recovery panel；从现有 Trace 只读派生 first_try_ac、recovered_to_ac、formal_recovery_to_ac、recovery type、候选/DEBUG/重 PLAN/拒绝/无法验证计数。坏 Trace 不冒充完整指标。旧 State/Trace 不回写。自动更新复用原详情轮询。

比赛列表/详情显示 Official Performance、machine status、source/fetched time 与整场账户范围说明。null 显示未知，不变成 0；confirmed 0 可显示 0。官方值从独立 performance.json 合并，普通 GET 仅读本地。

## 受控执行与配置

发起任务/比赛沿用本地串行有界队列和 ExecutionService，8 KiB JSON、Origin/CSRF、确认、nonce、防重和字段白名单。新任务配置六项：CNY、模型调用上限、正式 POST 尝试上限、HTTP timeout、poll interval、deadline。默认 YAML/CLI；code-only 锁定一次调用/提交。恢复冻结原配置，不能加钱/重跑；重启不自动执行，未知 paid call/POST 不补发。

模型调用上限也包括样例 checker 生成。新 Harness 默认 LLM checker 正向通过可提交，但样例仍无法验证；拒绝/未知停止、不 DEBUG。`--sample-config config/checker.example.yaml` 可选服务器端配置；浏览器不能提交 checker 源码、任意模型/YAML 路径或修改全局配置。默认 sample policy 与替换配置必须通过新 ID 生效。

## Performance 显式刷新

`POST /api/dashboard/contests/{run_id}/performance` 只是一条本地控制路由；对 MiniOJ 仅 GET me/standings。请求为原控制白名单 `request_id`、`confirm_remote_calls`，同源/CSRF/确认校验。服务直接调用独立 refresh_performance，不进入执行队列、模型 Registry 或 Agent。

刷新核对 run_id/contest_id/配置指纹与冻结账户/endpoint；缺身份或切换账户保持 null。只新增/替换 performance.json，不重写 report.json/problems.csv/State/Trace。整场账户成绩不是单次运行隔离成绩。旧运行没有冻结身份则 identity_unresolved；不会猜测归属。read-only 模式无此端点或按钮。

| 本地入口 | 用途 |
| --- | --- |
| GET /api/dashboard/overview、/tasks、/tasks/{id}、/tasks/{id}/timeline | 本地任务/Trace 观察 |
| GET /api/dashboard/models、/experiments | 本地配置展示/已有状态分组 |
| GET /api/dashboard/contests、/contests/{run_id} | 本地报告与官方 enrichment |
| POST /api/dashboard/tasks/launch、/tasks/{id}/resume | 已确认单题队列 |
| POST /api/dashboard/contests/launch、/contests/{run_id}/resume | 已确认比赛队列 |
| POST /api/dashboard/contests/{run_id}/performance | 只读远程榜单刷新，零 Agent 执行 |

启动与配置参数见 [README](../README_zh.md)，两种模式/安全恢复与反馈投影见 [架构](architecture.md)。
