# Main Experiment Final Preflight（2026-10-03）

本轮只开发最终 Main Preflight/receipt 守卫与报告字段，读取必要公开 metadata GET。真实 LLM=0、Custom Run=0、formal=0、paid cost=0；不启动任何 Main task，不改 Agent/prompt/extraction/recovery/dedup 行为。完整 Gate、Git/dirty diff、fingerprint、GET ledger 以新冻结工件为准。

## 新冻结入口

- Preflight：`main-v1-preflight-20261003-final`。
- 仅准备的未来 Main ID：`main-v1-20261003`；没有该运行的批次/任务 Workspace。
- [preflight.json](../../workspace/.experiments/main-v1-preflight-20261003-final/preflight/preflight.json) / [可读报告](../../workspace/.experiments/main-v1-preflight-20261003-final/preflight/preflight.md)。
- [final receipt](../../workspace/.experiments/main-v1-preflight-20261003-final/preflight/final-receipt.json)、[freeze diff](../../workspace/.experiments/main-v1-preflight-20261003-final/preflight/freeze-diff.json)、[checker matrix](../../workspace/.experiments/main-v1-preflight-20261003-final/preflight/checker-matrix.json)、[budget summary](../../workspace/.experiments/main-v1-preflight-20261003-final/preflight/budget-summary.json)。另保留既有 execution-order/model-snapshot/prompt-snapshot/checker-summary。
- [工程 checks](../../workspace/.main-v1-development/main-v1-20261003/checks.json) / [只读 HTTP 审计](../../workspace/.main-v1-development/main-v1-20261003/read-only-http.json)。详细命令输出、包及解包源码测试在同一开发审计目录。

Receipt 使用 `main_final_receipt_v1`，绑定完整 Preflight 内容 hash、新 Main ID、当前源指纹、Git SHA/dirty/diff、12/5/60、list/order/seed、prompt/models、checker/feedback/dedup versions、actual/effective feedback、blockers/warnings/time。Report hash 同时绑定全部 JSON 快照/receipt；存储拒绝覆盖旧 ID。60-task Runner 创建批次前、resume 与每个后续 task 前校验；缺失/BLOCKED/漂移拒绝于模型/远程执行之前，不自动 preflight。

本轮不自动提交 Git；最终 receipt 冻结本轮 dirty diff 与 runtime 内容。后续编辑或 commit 会导致指纹漂移，必须新 Preflight/新 Main ID。未有用户未提交改动被删除或覆盖。

## 历史证据和唯一获授权配置变更

真实 [Pilot-v2 validation](../../workspace/.pilot-v2-validation/pilot-v2-live-20261003/pilot-v2-validation.json) / [receipt](../../workspace/.pilot-v2-validation/pilot-v2-live-20261003/final-receipt.json) 为 `PILOT_V2_VALIDATED_WITH_WARNINGS`、blockers=0：15/15 terminal、0 infrastructure、37 LLM、25 Custom、11 formal，7 final AC；6 invalid output、1 WA、1 budget exhausted。自然 TLE→DEBUG→AC→只读 REVIEW 已有完整链，completed resume 远程增量为 0。没有自然重复候选/cache hit，不把替身 cache-hit 测试称为 live hit。

Pilot fingerprint 为 `fabe05257bc932b2360ebf3b9453173ad8fc8356160d0783163a23141efbdc28`。主实验重新冻结；共享 Agent 源码、prompt、模型/生成参数/transport/本地边界/配置价格、Harness 预算/规则、sample 全局策略及共有 override、feedback/dedup 逐项比较。不同的题单/排序/总分配额度、Main receipt/report runtime 新版本及 dirty diff 均显式记录，不复用 Pilot fingerprint。

检查发现主 YAML 仍引用用户 models.yaml（120s / 32768）。用户本轮明确回答“授权仅修改配置引用”：只将 experiment.small.yaml 的 model_config 改为 models.infrastructure.yaml，使 Main 使用已冻结的 600s / 65536。两个模型、8192 output request、temperature/top_p=null、enable_thinking=false、任务预算 1 CNY/80 calls/10 formal、checker/feedback、DEBUG/replan/extraction 都不改。`.env`、用户 models.yaml/harness.yaml 和旧运行工件按 SHA-256 保护；具体数量/获授权差异见 checks。

## Checker、seen 标记与指标

全 12 题通过新的公开 metadata/清洗题面 GET 检查；matrix 保存 rating、checker 类型/来源/样例数量、float 容差需求和 abs/rel、remote/LLM 需求、四类 verification、seen/run_id/live coverage。类型数量由当次观测和显式冻结 override 计算；不根据题面猜 checker/容差，不把 `llm_generated_unverified` 加入 CheckerSpec kinds。special/unknown 的合法正向 LLM 路径允许带警告准备；未做 live smoke，不声称认证。缺覆盖 live_validation_required=true。

CF2127C/CF2125D/CF2127D 为 seen，保留 3 题/15 tasks；其他 9 题/45 tasks 为 unseen。该信息只进计划/结果 JSON/CSV/分组，不进入 Agent context。Main 的模型响应、候选、正式结果/checkpoint/去重缓存全部新建、task-local；Pilot 只读供证据 hash 和标记。即使生成同源码，也不能跨 task/实验复用正式结果。

Main 结果冻结字段包括 first_try/sample/formal recovery、完整候选/DEBUG/提交关联、重复/复用、LLM/Custom/formal、input/output/cached tokens、估价/provider-reported/billed、sample reject/unverifiable、invalid output、wall time。保留所有中间正式 verdict 和互斥最终 outcome；先 WA 再 invalid/budget 停止不算 final WA，TLE→AC 保留 TLE。缺 cached metadata 或不完整关联保持 null；cached 已包含于 input，无未配置折扣。response finish_reason/model/usage 和既有 extraction 链保留，未修改 parser/prompt。all/seen/unseen 仅描述性分组，仍隔离条件/预算/checker/actual/effective feedback，不给模型排名或 task Performance。

## Provider / 反馈 / 预算边界

Main 配置 standard=qwen3.8-flash、strong=qwen3.7-plus；600 秒是 connect/read/write/pool inactivity timeout，overall request deadline=null、retry=0；65536 是本地费用/输入预留，8192 是请求 output 上限。Provider GET /models 仅用于当次可见性，其他 verified 字段只按显式 metadata 填写。temperature/top_p 的 null 表示 provider default/unverified，不猜默认值。

Pilot-observed 是历史 37 次真实响应、numeric usage 可关联及 accepted requests；不等于 provider 最大 context/output。历史 input=216010/output=157672/cached=51200，配置估价 1.0598962 CNY，provider-reported/billed=null。Main 最大分配 60×1=60 CNY，不是预计消费，不用 Pilot×4 推算精确支出。当前反馈由新 GET /me 核验，unknown 必须 BLOCKED，full 经 formal_verdict_only_v1 投影为 effective verdict_only。

## 验证与停止

测试仅 Fake/MockTransport：Pilot 标记/持久化/不继承；60 唯一组合与 seed 顺序；prompt/model/checker/feedback/dedup/list/order 漂移；有效 receipt 只允许本地 launch preparation，缺失/BLOCKED/mismatch/历史漂移均零付费/远程执行；exact/token/float/special/unknown/LLM 未认证 matrix；缺/非法 cached metadata；TLE→DEBUG→AC→REVIEW、invalid 终态及中间 WA 保留。

最终 targeted/full pytest、系统 Python 3.9/venv compileall、diff、offline wheel/sdist、独立解包源码完整 pytest、包成员/secret scan/保护 hash 的具体命令和结果均见 checks/log。JS 未改，不引入 UI/Agent 行为。发行包排除用户配置/密钥/Workspace/MiniOJ server/testcases，以及历史原始付费 probe/smoke 目录；仓库原件不动。

本轮完成后停止。Main Gate 通过也只允许用户另行明确授权绑定 ID/指纹的 60-task 执行；不自动启动、不重跑 Pilot 或主实验。
