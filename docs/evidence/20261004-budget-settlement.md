# 实际用量结算预算修复

用户授权修复“实际费用约 1 元，却因累计最大 token 预留接近 20 元而停止”的问题。

## 行为与范围

- 调用前继续按配置 input bound / max_tokens / CNY 单价预留完整上限；成功预留后保存当前 reservation，ModelCallRuntime 在付费请求前绑定 call ID 并持久化。
- 收到可信用量、费用且未超上限后，将本次上限替换为实际输入/输出 token 的配置费用，释放差额。用量已知的失败调用也结算；未知、用量超界或费用不可确认保留预留并停止。
- `budget_committed_cny` 现在表示已结算配置费用加未知请求预留；现有 ExperimentRunner / ContestRunner 的总 cap 汇总直接复用该值。Trace 添加 `BUDGET_SETTLEMENT`，审计核对累计预留减累计释放。
- 新执行快照/检查点冻结 `actual_usage_settlement_v1`；旧 Harness 检查点缺少此版本时保留累计上限语义，不回写历史运行。新的执行指纹阻止用旧条件恢复新策略。
- 崩溃恢复协调 Runtime 的 State 投影与权威检查点中的预算/未决调用，不再次请求模型，不重复结算。Decimal 计算单次配置费用，避免浮点误差让满额用量被误判为超预算。
- 配置估算仍不自动包含缓存折扣、限时优惠或免费额度，不作为实际账单。没有修改模型价格、上限、提示词、重试次数或用户凭据。

## 本地验证

`env UV_CACHE_DIR=.uv-cache uv run --offline --extra dev --extra dashboard pytest -q`：**800 passed**，全部为本地 Fake / Mock 验证。

新增 `client_tests/test_budget_settlement.py` 共 12 项：56 次调用复现、批次和比赛复用释放额度、缺失/超界用量、已知失败用量、失败响应超界、Decimal 满额费用、普通中断/崩溃恢复、旧策略恢复、新执行版本冻结。原预算守卫测试使用真实耗尽上限的 Fake usage，继续验证单题/总 cap；格式纠正测试按实际 token 检查费用。

独立语法检查：8 个相关 Python 文件可按 Python 3.9 语法解析；`git diff --check` 通过。

复现 `contest-ui-f924213b22e74321be70` 的原响应总用量：Plus 15 次，输入 67,406 / 输出 82,834；Flash 41 次，输入 346,692 / 输出 143,141。相同配置单价下，新预算占用 **1.4613183 CNY**，下一次 Plus 预留允许；旧累计预留 **19.6919296 CNY** 导致下一次 Plus 预留被拒绝。这是隔离 Fake 复现，原比赛、State、Trace 和报告未回写或重新执行。

没有运行真实模型、Custom Run、正式提交、Main 或付费 probe。历史 Main receipt 不因本次修复自动更新或获得新授权。

## Dashboard 加载验证

只读查询当前队列确认没有 queued/running 任务后，按原 workspace/model/harness 参数重启 8765 服务；新 PID 16805，比赛只读 API 返回 HTTP 200 / completed。修复供新任务使用，未恢复或重新执行已结束的比赛。原比赛存储及全部关联任务文件的重启前后聚合 SHA-256 相同：`73274bc0af0d25ce937fd4506474cd87cbde647cfc679b71026d7c3c0c02b25e`。
