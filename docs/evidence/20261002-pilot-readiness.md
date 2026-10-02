# 2026-10-02 Pilot 执行前受限核验

最终判定 **NOT_READY**。运行条件检查已通过；唯一 blocker 是本轮两次模型调用的实际账单金额未取得。按用户要求记录 actual cost，因此保留 `actual_billed_cost_cny=null`，不以配置价格或公开单价推算值冒充实际扣费。Pilot 15 个任务和主实验均为 0。

机器证据见 [readiness JSON](20261002-pilot-readiness.json)；详细请求、响应 allowlist、request ID、usage、源码和脚本 SHA-256 在 [受限核验目录](20261002-pilot-validation/) 中。该目录不保存密钥、服务器正式诊断正文或 hidden testcase。

## 提交与最终冻结

当前 preflight 及其尚未提交的客户端依赖已提交为 `08a035236fafbce81776f463a87702c12df1bcd2`。提交前全量测试 **579 passed**，暂存内容 secret scan 和 diff check 通过。新增 ignore 规则排除 `.DS_Store`、用户 `config/models.yaml` / `config/harness.yaml`，保留其原始内容。

本证据随后单独提交，最终 frozen preflight 在该提交的 clean tree 上生成：

- `workspace/.experiments/pilot-preflight-20261002-clean/preflight/`：六份独立、不可覆盖的 preflight 工件。
- `workspace/.pilot-validation/pilot-gate-20261002/readiness.json`：最终 Git SHA、clean status、新 fingerprint、preflight report hash、核验工件关联及最终 readiness 判定。

最终 SHA/fingerprint 以这两个新目录中的工件为准；探测时的源码 SHA 与最终 evidence commit 用相同 `runtime_source_hash` / 配置 hash 关联。旧 dirty preflight 继续保留，不作为新的冻结证据。独立只读 preflight 的 metadata 判定与本轮含实际扣费要求的整体 readiness 判定分开记录。

## 最小 Provider probe

经当前 Registry → Router → Provider，每个 provider/model **仅 1 次**。消息共 52 字符，仅要求回复 `OK`，没有题面、算法或 checker 生成请求。生成参数保持现配置 `max_tokens=8192`、`enable_thinking=false`；两个请求均 HTTP 200、`finish_reason=stop`，各只输出 1 token。8192 是被接受的请求上限，没有声称实际生成了 8192 tokens。

| Profile | 请求 / 响应实际 model ID | prompt / completion / total | cached tokens | 配置原价推算 CNY |
| --- | --- | --- | --- | --- |
| standard | qwen3.8-flash / qwen3.8-flash | 29 / 1 / 30 | 0 | 0.0000259 |
| strong | qwen3.7-plus / qwen3.7-plus | 29 / 1 / 30 | 0 | 0.0000660 |

原始 usage 中同时观察到 `prompt_tokens_details.text_tokens=29`。两次合计输入 58、输出 2、总计 60 tokens；无需补估未知 usage。

temperature/top_p 的配置为 null，请求未发送，响应没有回显运行时值；没有自动设置或修改。官方非思考模式默认值为 0.7 / 0.8，这属于文档证据，运行时实测值仍为 null。[官方兼容 API](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)

官方模型页记载两者 context=1,000,000、非思考输入上限 991,808、输出上限 131,072。配置 32,768 输入预留和 8,192 输出请求处于文档边界内；本轮只验证极短输入和正常短输出，没有实测完整 context 或最大输出。[Flash 规格](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)、[Plus 规格](https://help.aliyun.com/zh/model-studio/qwen3-7-plus)

两个响应均不含 cost/billing/charged_amount/currency 字段。按观察到的 usage 和配置原价推算合计 **0.0000919 CNY**；官方北京价格页另列 strong 限时八折，按该公开活动推算合计 **0.0000787 CNY**。两者都不是已核实账单，账户免费额度、资源抵扣和实际活动结算未知；实际扣费保持 null。实验配置价格没有修改。[官方价格](https://help.aliyun.com/zh/model-studio/model-pricing)、[账单口径](https://help.aliyun.com/zh/model-studio/bill-query-and-cost-management)

## MiniOJ / token checker / feedback

按 Pilot 当前 `send_custom_run_code_alias=false` 路径发送 source_code/stdin，**1 次** POST /api/v1/runs 返回 HTTP 200、status=OK、exit_code=0、time_ms=2、memory_kb=5784，输出未截断。代码仅回显固定输入的三个 token，并添加空白；只在 MiniOJ 执行，没有本地编译或执行，也没有关联任何算法题。

同一远程 stdout 输入当前 `check_run` / TokenChecker：参考 token 相同、空白不同的正例为 `sample_pass`；把 42 改成 43 的受控反例为 `sample_wrong_answer/WA`。这是合成的 smoke 对照，不是实验算法失败或 recovery，不写入历史任务。

GET /api/v1/me 明确 actual=full。使用现有 ToolRuntime 和 `formal_verdict_only_v1`，只读 GET 既有 submission 103 及其 agent feedback，返回结果仅为 ID/status/verdict 和 verdict；Trace 中 summary=null、details/extra_fields 为空，正式诊断未保存。effective=verdict_only，属于 projected，未声称 native verdict_only。旧提交的 AC 不计入本轮提交或 Pilot 成绩。

初次辅助脚本的反馈 GET 白名单路径写错，在请求发送前被拦截；该次只有三个 GET，0 POST。中止证据与原脚本保留，随后按源码真实路径修正并完成核验。无 paid/POST 重发、无兼容格式 fallback。

## Blockers、warnings 与副作用

- **Blocker：actual_provider_billed_cost_unverified**。需要可归属上述 request ID 的账单证据；再发模型请求不能补齐该证据。
- Warnings：sampling 默认值未显式冻结且未被响应回显；完整输入/输出上限仅有文档证据；feedback 为 projected；公开优惠与账户结算可能不同。基础 GET /models preflight 不导入手工文档/probe，故其 verified price/context/output 字段仍为 null，对应警告仍保留。
- 本轮 LLM calls=2（每个模型 1）、Custom Run=1、formal submissions=0、Pilot tasks=0、主实验 tasks=0、自动重试=0。费用：actual=null；配置单价 × 实测 usage=0.0000919 CNY。
- 最终 preflight 只发 GET，不追加模型/Custom Run/正式提交。完整 GET ledger 与最终总数写入最终绑定文件。
- 470 个受保护文件 SHA-256 不变，涵盖 `.env`、用户 models/harness、全部实验 YAML、已有 Workspace/State/Trace/实验结果。没有替换模型、修改实验配置、放宽 checker policy 或改写历史。
