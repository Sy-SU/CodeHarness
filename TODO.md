# CodeHarness 未来工作

## 已完成的单独授权修改

- [x] **实际用量结算预算**：新调用先预留上限，收到可信 usage 后按配置价格结算并释放差额；单题、实验和比赛共用实际费用加未知预留，Trace 核对预留减释放。未知/中断/超界保留预留，恢复不重发、不重复释放；旧检查点保留冻结语义。Fake/本地完整回归 800 passed，见 [验证记录](docs/evidence/20261004-budget-settlement.md)。
- [x] **CODE/DEBUG 无效输出最多 3 次格式纠正**：首次生成后额外最多 3 次，沿用原 Role/Profile、调用/费用/输入/时间守卫；次数及响应缓存原子保存，未知请求不重发，旧冻结任务/code-only/checker 不升级。Fake/本地完整回归 788 passed；见 [验证记录](docs/evidence/20261003-code-extraction-retries.md)。本次算法变更不扩大历史 checker-only Main receipt 的授权范围。

当前能力见 [STATUS.md](STATUS.md)，最新 sample_check_v3/新 Main binding 见 [本轮证据](docs/evidence/20261003-sample-check-v3.md)；[失败 checker smoke](docs/evidence/20261003-checker-live-smoke.md) 保留。以下只列后续工作；历史原文见 [docs/history](docs/history/README.md)。

## 下一步，均需后续显式授权，不自动执行

- [ ] **P0：审阅 v3 Main receipt 后另行授权 Main**：本轮止于 `sample_check_v3` + 新 Main Final Preflight，不运行任务。原 v2 receipt stale 且保留；[新 receipt](workspace/.experiments/main-v1-preflight-20261003-sample-v3/preflight/final-receipt.json) 绑定独立 future ID。4 token semantic_check、5 special + 3 unknown execution_only→正式 Judge，Main LLM checker=disabled。历史 smoke 1 PASS / 7 FAIL 不再是独立 execution-only 路径的 blocker，不能改写为 smoke 成功。12 题 × 原五组 × 1 次，1 CNY/task、60 CNY 分配上限；不是预计费用。保留 pilot_seen、独立 task/call/candidate/submission。源码/配置/commit 漂移需新 Preflight/新运行 ID，不拼接历史结果。
- [x] **sample_check_v3 离线验收与 Main Final Preflight**：可信 checker 回归、execution-only/正式 WA/REVIEW、未知 Custom Run 不重发、旧 v1/v2 策略、五组 checker 调用为 0；新增四项 Trace 计数和独立 execution recovery。所有本轮执行/付费为 0，工程与新 Gate 见证据。
- [ ] **P1：描述性分析 Main**：AC、first_try_ac、sample/formal recovery、invalid output、budget、input/output/cached tokens、configured estimate/provider-reported/billed cost；同时保留互斥 task 终态和全部中间正式 verdict，分别报告 all/seen/unseen。不自动得出模型胜负或显著性结论。
- [ ] **P2：native verdict_only 与 projected full 协议对照**：使用明确服务端声明及隔离的新冻结条件；历史 unknown 不回写。
- [ ] **P3：Provider limits / billing**：最大 context/input/output、默认采样值、价格及实际账单仍未验证；null 不补零，不将 Pilot accepted request 当供应商上限，不构造未授权大 prompt。

## 仍需独立确认

- [ ] **Checker**：已完成新版错误分类/缓存/审计与一次 8 题 live smoke；后续可以独立研究真实 7 题失败和 JSON parser 的独立价值，但不阻塞 Main v3；不调整算法 prompt，不用生成重试掩盖失败率。CF2092D 仅 reference sanity 通过，仍 `llm_generated_unverified`，不能认证语义。
- [ ] **公开 sample judge 协议**：若 MiniOJ 提供 expected-output-independent endpoint，再确认协议并接入 RemoteChecker；不读取 admin checker/testcase。`lines`/`yesno` 语义及 float 容差未确认仍 unknown/unverifiable。
- [ ] **Performance enrichment**：冻结账户身份后独立只读核验；整场账户值不作为 task/condition 独立指标，历史缺身份不回填。
- [ ] **OJ 协议 / 联调**：尚未实测的错误响应、未完成反馈、其他 Custom Run 失败/资源字段及服务端幂等/查重语义；客户端继续无自动重试、未知 fail closed。

## Future，不属于当前里程碑

- [ ] 需求另行确认后讨论 Test Generation / brute / oracle / stress testing；输入合法性和 expected 来源必须可信，执行仍通过 MiniOJ HTTP。

本轮 v3 完成后停止；不自动运行 Main、probe 或 checker smoke。不增加新 Agent phase、Multi-Agent、原生工具调用调度、模型升级/REVIEW、跨题记忆、服务器或本地执行器。本轮实现/替身验收完成后停止，不自动发起下一付费批次。
