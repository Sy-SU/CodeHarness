# Infrastructure Qualification：诊断与冻结运行入口

本记录固定基础设施修正及验证方法。真实 qualification 的最终状态、Git SHA、新 fingerprint、费用与逐任务结果保存在独立运行文件中；此文不预先宣称 smoke 通过。

- 最终 Gate：[qualification.json](../../workspace/.infrastructure-qualification/infra-qual-20261002/qualification.json)、[可读报告](../../workspace/.infrastructure-qualification/infra-qual-20261002/qualification.md)。
- [历史诊断](../../workspace/.infrastructure-qualification/infra-qual-20261002/historical-diagnosis.json)、[原停止请求预算分解](../../workspace/.infrastructure-qualification/infra-qual-20261002/context-budget-diagnosis.json)、[历史保护清单](../../workspace/.infrastructure-qualification/infra-qual-20261002/original-protected-files.json)。
- 新 qualification preflight：`workspace/.experiments/infra-qualification-preflight-20261002/preflight/`；独立批次 `infra-qual-live-20261002`，最多 4 个任务。
- 仅在 Gate 通过后生成 Pilot-v2 preflight：`workspace/.experiments/pilot-v2-preflight-20261002-infra/preflight/`；Pilot-v2 / 主实验均不在本轮执行。

## 历史六次失败：可确认与不可追认

全部请求 provider=bailian、requested model=qwen3.7-plus、max_tokens=8192、自动 retry=0。以下 task 后缀均属于 `pilot-live-20261002`；完整时间戳、重建请求 body/hash 见历史诊断 JSON。

| task 后缀 | condition | problem | role | elapsed(s) | body 重建 bytes | 保守输入上界 |
|---|---|---|---|---:|---:|---:|
| s5-p1-r1 | mixed-harness | CF2127C | PLAN | 120.120415 | 3267 | 5087 |
| s4-p1-r1 | strong-harness | CF2127C | PLAN | 120.126196 | 3267 | 5087 |
| s2-p1-r1 | strong-code-only | CF2127C | CODE | 120.043904 | 3242 | 5062 |
| s4-p2-r1 | strong-harness | CF2125D | PLAN | 120.123400 | 1882 | 3716 |
| s5-p3-r1 | mixed-harness | CF2127D | PLAN | 120.054265 | 3782 | 5603 |
| s4-p3-r1 | strong-harness | CF2127D | PLAN | 120.137182 | 3782 | 5603 |

六次只能分类为 `transport_failure_subtype_unrecorded`。HTTP status=null、usage 未取得；headers/first byte/partial body 均为未知，不能写成“没有”。provider 可能已完成生成、未知收费可能=true；不得重发历史请求。约 120 秒与统一配置边界一致是推断，旧 Trace 无法证明 connect/read/TLS/服务端哪一阶段失败。

HTTPX 标量 timeout 配置 connect/read/write/pool 的 inactivity 上限，不构成整个请求总 deadline。[HTTPX timeout 文档](https://www.python-httpx.org/advanced/timeouts/)。新 metadata 因此明确 overall deadline=null。新诊断按异常与 trace 阶段分开记录 connect_timeout、tls_connect_failure/connect_failure、read_timeout_before_response_headers、read_timeout_during_body、write/pool timeout、429、5xx、local_cancellation；overall deadline 当前未配置，不能伪造该分类。只记录 allowlist 的事件名/时间/整数 status，不保存 trace info、header 或 exception prose；已完成 headers 是 first-byte-observed 的证据，精确 packet TTFB 仍未测量。[HTTPX trace 文档](https://www.python-httpx.org/advanced/extensions/)。

## 固定基础设施修正

| 字段 | 原运行 | 新独立配置 |
|---|---:|---:|
| connect/read/write/pool timeout(s) | 各 120 | 各 600 |
| overall/request wall deadline | null | null |
| model streaming | false（非 SSE） | false（非 SSE） |
| automatic retry | 0 | 0 |
| standard/strong 本地输入预留 | 32768 | 65536 |
| requested/reserved output | 8192 | 8192 |
| safety padding | 每 message 1024 | 每 message 1024 |
| temperature / top_p | 未指定/null | 未指定/null |

原 strong 成功调用 57.923/74.922/75.085 秒分别输出 3350/4317/4336 token；按约 57 token/s 线性外推 8192 输出已约 144 秒，另有排队/首字节延迟。此为容量设计推断，不是 provider 延迟保证。选择统一 600 秒给约四倍余量，避免按某一任务时间设阈值；qualification 才提供新路径的真实证据。read timeout 增大会延长单次失败等待，不增加自动 paid retry。

新输入边界取固定 65536，容纳原请求的 33977 保守上界及后续 Harness 状态余量。每次不退回的费用预留由 standard 0.0483328 → 0.0745472 CNY、strong 0.131072 → 0.196608 CNY；每任务仍限 1 CNY，可能更早预算停止。该变化进入新 fingerprint；不保证无限长 history 或供应商最大 context，后续自然超界继续 fail closed。原 `.env`、用户 `config/models.yaml`、原 Pilot/main 配置与原任务完全保留。

## 原 standard-harness / CF2127D DEBUG 停止分解

从原 checkpoint/solution 只读重建相同消息，hash=`1eee600bde09fc7fa58afd23704d97cd581127ac5f5ef515f282c580030ce9d4`。新 instrumentation 核对分项合计等于实际 Prompt bytes，不裁剪/改写 Prompt。

| 分项 | UTF-8 bytes（保守 token 上界） | 实际 token |
|---|---:|---|
| system | 205 | null |
| problem + public samples | 3288 | null |
| Harness plan | 22270 | null |
| Harness candidate | 5794 | null |
| tool feedback | 276 | null |
| conversation/history | 32 | null |
| phase instruction | 64 | null |
| native tool/schema | 0 | 0（未发送） |
| safety padding（2 messages） | 2048 | 非实际 token |
| 合计输入保守上界 | **33977** | **null** |

profile=standard/model=qwen3.8-flash；配置 context/provider-max-input=null，输出请求/预留=8192。本地 guard 在发送前拒绝，因此该 DEBUG 的 actual prompt tokens=null；上一 CODE 的实际输入为 9224，PLAN 为 1034，累计 10258 不能写成当前输入。剩余预留预算 0.9033344 CNY，停止条件 `prompt_exceeds_configured_input_limit`。相同消息在旧 32768 guard 被拒绝、新 65536 guard 获准，纯本地重建产生 0 LLM/Custom Run/正式提交。

## 验证与范围

本地完整 593 项测试通过，含阶段分类、partial body、安全诊断、raw actual model/usage、预算拒绝审计、相同 Prompt 新边界和旧字段兼容。真实 smoke 仅用原 CF2127D/token，覆盖 strong-code-only / strong-harness / mixed-harness / standard-harness；不要求 AC，但所有调用须 transport success、raw model match、有 usage、无 input boundary rejection。要求 effective verdict_only、token checker、无重复正式提交、OJ 正常及完整 State/Trace/report/checkpoint（Harness）关联。

运行前代码/配置先提交、Git clean、重新 preflight；每次 paid POST 检查 frozen fingerprint 与原文件 hash。独立审计不重写原报告，不读取 admin checker/testcase，不在本地执行 contestant。原 Prompt/路由/采样参数/价格/Harness/checker/feedback 不按 AC/WA 调整。费用分别保存 configured-price estimate / provider-reported / billed；后两者无证据保持 null。provider 最大 context、价格及 sampling defaults 仍是 warnings，账单缺失不导致 qualification failure。
