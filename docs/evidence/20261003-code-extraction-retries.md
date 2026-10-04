# CODE/DEBUG 输出格式纠正

2026-10-03 用户授权：代码提取失败默认最多重试 3 次。

`HarnessPolicy.max_code_extraction_retries=3` 表示初次生成后最多额外调用 3 次，独立用于每个 CODE/DEBUG 候选；0 禁用。纠正保留 Role/Profile，system prompt 要求一个完整 C++20 代码块，不执行 XML/tool 指令。Trace 记录 `CODE_EXTRACTION_RETRY` 的次数、额度及失败调用 ID。用尽次数仍为 `invalid_model_output / code_extraction_failed`。

请求继续经过原费用预留、输入检查、模型调用次数和墙钟守卫。检查点原子保存重试计数、清除旧响应缓存；已提交的纠正响应可恢复复用，结果未知的中断请求不重发。新候选成功后清零。旧冻结任务、code-only 与独立 checker 生成不升级；历史 Main checker-only source-scope 守卫继续拒绝算法漂移，不刷新原 receipt。

验证使用 Fake Router/OJ、MockTransport、临时 Workspace 和本地 ASGI，无付费或远程调用：

- `env UV_CACHE_DIR=.uv-cache uv run --offline --extra dev --extra dashboard pytest`：788 passed。
- 新测试覆盖第 1/3 次纠正成功、4 次无效响应后终止、CODE/DEBUG 路由及反馈关联、新候选次数重置、费用/次数拦截、调度后中断计数保留、已知响应复用、未知调用不重发、旧检查点不升级、冻结策略校验。
- `compileall` 与 `git diff --check` 通过。

同步了两条原按 10 次上限拒绝第 11 次提交的测试，改为拒绝第 101 次，与修改前工作区已存在的 100 次后端/界面上限一致。本次未更改该提交上限。

Dashboard 重启后，新任务使用此默认值；已运行或 DONE 的冻结任务不会自动重跑。
