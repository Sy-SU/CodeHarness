# sample_check_v3 与独立 Main Final Preflight（2026-10-03）

本轮完成授权的 v3 策略、替身回归与新的只读 Main Final Preflight 后停止。Main 未启动；本轮 LLM / Checker LLM / Custom Run / formal / paid cost 均为 0。最终 Gate、blockers、warnings、Git SHA/dirty diff 和 fingerprint 由下列不可覆盖的 JSON receipt 给出；任何后续源码/配置/commit 变化均需新 ID 与新 preflight。

- Preflight：`main-v1-preflight-20261003-sample-v3`
- Future Main：`main-v1-20261003-sample-v3`（未启动）
- [Final receipt](../../workspace/.experiments/main-v1-preflight-20261003-sample-v3/preflight/final-receipt.json)
- [Preflight report / GET audit](../../workspace/.experiments/main-v1-preflight-20261003-sample-v3/preflight/preflight.json)
- [Checker matrix](../../workspace/.experiments/main-v1-preflight-20261003-sample-v3/preflight/checker-matrix.json)
- [Freeze diff](../../workspace/.experiments/main-v1-preflight-20261003-sample-v3/preflight/freeze-diff.json)
- [工程结果](../../workspace/.sample-v3-development/sample-check-v3-20261003/checks.json)
- [独立本轮只读 HTTP ledger](../../workspace/.sample-v3-development/sample-check-v3-20261003/preflight-http.json)
- [最终本地绑定 / stale / 历史保护检查](../../workspace/.sample-v3-development/sample-check-v3-20261003/final-local-verification.json)

## sample_check_v3 决策矩阵

| 可信证据 | 样例 policy / 结果 | 下一步 |
| --- | --- | --- |
| exact/token/双显式容差 float；未来独立 attested remote checker | semantic_check，sample_pass / sample_wrong_answer | pass→formal；fail→DEBUG |
| special/unknown/缺容差 float 无 verified checker，MiniOJ OK + 整数 exit_code=0 | execution_only；execution_pass_output_unverifiable；sample_check_status=output_unverifiable；passed=null | 全部公开样例执行成功后允许 formal |
| CE/RE/TLE/MLE/OLE 或非零退出 | sample_execution_failure / execution_status=execution_failed | DEBUG reason=sample_execution_failure |
| IE、未知 status、缺失/非法 exit code、HTTP/protocol/result_unknown | fail closed | 无 DEBUG/formal/自动重试 |

只信任 MiniOJ 执行状态、退出码和资源错误，不比较 unknown/special stdout 与参考输出，不断言特殊语义有效。已知 execution-only OK 的 stdout 缺失/截断也不制造语义 WA；明确 OLE 仍是执行失败。可信语义检查发生截断则无法证明通过，保持停止。

## Trusted checker behavior

Trusted token mismatch 仍先 DEBUG，第一候选不立即提交。Pilot-v2 已验证的 CF2127C/CF2125D/CF2127D 只有 public input hash 与 CheckerSpec 一致才保留 coverage；CF2120D 缺真实样例路径覆盖仍警告。当前没有可接入的公开 verified remote sample judge；不访问 admin checker/testcase，不提升 lines/yesno 或猜测 float 容差。

## Special / unknown behavior

测试证明 stdout 不匹配不会在正式反馈前 DEBUG：execution OK→output_unverifiable→formal。正式 WA→DEBUG reason=formal_verdict_WA；AC→原本地只读 REVIEW。增加 sample_semantic_verified_count、sample_output_unverifiable_count、sample_execution_failure_count、formal_submit_after_unverifiable_sample_count。

output_unverifiable 不计 sample pass/fail/recovery。其后正式 WA→成功 DEBUG→新候选→关联 AC→REVIEW 为 formal_recovery_to_ac=true、sample_recovery_to_ac=false。公开样例 RE→后续修复 AC 独立记录 sample_execution_recovery / recovery_type=sample_execution_failure，不称为语义 WA 修复。正式缓存复用不伪造新 POST/提交计数。因果关联守卫未放宽。

新 contestant Custom Run 在请求前原子保存 intent，结果未知时停止且 resume 不重发；已知执行结果可恢复处理且不重复调用。旧 v1/v2 序列化键、positive-only gate、原 checker prompt/恢复规则保持冻结，没有 State 迁移。

## LLM checker

生成、GeneratedCheckerSession、独立 bounded smoke 与旧明确 v1/v2 experimental/diagnostic 实现保留。本轮未调任何 PLAN/CODE/DEBUG/checker prompt、模型、参数、retry 或解析器，没有新 checker smoke。Main 为 disabled，v3 TEST 不生成 checker，也不使用其 pass/reject/未知决定；不导入旧 smoke 源码/响应/缓存。

历史 [checker smoke](20261003-checker-live-smoke.md) 原件仍为 **CHECKER_LIVE_SMOKE_FAILED，1 PASS / 7 FAIL**。逐文件实际证据为 5 个 JSON-only/no-source、CF2111D RE、CF2103C length→CE；CF2092D 仅公开 reference sanity PASS，仍 llm_generated_unverified。这一失败是将 unverified LLM checker 移出 Main 关键路径的依据之一，未改写为成功。

## Experiment fairness

12 题 × 原五组 × 1 次仍为 60 个独立 planned task、1 CNY/task、60 CNY 分配上限。三个 Harness 条件共用 v3 execution-only policy，不依赖 CODE Profile；Main 模型调用只含 PLAN/CODE/DEBUG。两个 code-only 保持 1 CODE、≤1 formal、无样例/反馈修复。

Main YAML 只改 sample_checking 的 version=sample_check_v3、llm_checker=disabled、unverifiable_output=execution_only；原 on_unverifiable=stop、12 个 override、模型配置引用、题单/rating、条件、预算、feedback/order/seed 均保持。standard=qwen3.8-flash、strong=qwen3.7-plus，temperature/top_p=null（未指定），8192 请求输出、65536 本地输入预留、600s transport inactivity、overall deadline=null、retry=0 保持原配置，不升级为供应商核实上限。

## Tests

新增 v3 回归 71 passed；最终 targeted 415 passed、full 771 passed、解包 sdist full 771 passed。所有 runtime/OJ/provider 都为 Fake/Mock，本轮没有真实执行。Python 3.9 与项目 Python compileall、git diff --check、offline wheel/sdist、发行包 runtime bytes/排除项、secret scan 通过；JS 未改，无需重复 node 检查。精确命令、日志、发行包 SHA 与保护文件数量见工程 JSON。

旧 checker/phase4/preflight 测试的预期不弱化；相应 fixture 显式冻结原 v1/v2，另以新测试验证 v3 未知执行意图不重发与全部五 condition。提示词完整快照与本轮前 v2 完全相同。authorized_sample_check_v3 核对允许变动文件、原算法/submit/dedup/review/budget 函数与 recovery 因果 AST；仅批准新的样例决策/原因/指标分类。

## Main Preflight

新的 checker matrix 是 4 token semantic_check/client_verified，5 special + 3 unknown execution_only/unverifiable、semantic_verification=unavailable、formal_fallback=enabled；requires_llm_checker=0。无需验证或使用任何 LLM checker 才拥有合法执行路径。未认证 LLM smoke 失败不阻塞该独立路径；未知反馈、模型不可用、题目不可访问、非法路径或冻结漂移仍阻塞。

必要 GET 仅限 MiniOJ /me、12 题 public metadata/sanitized problem 与 provider /models，均独立审计；无 POST。真实 GET 所得 feedback actual/effective、模型可见性与状态以 final receipt/model-snapshot 为准；当前已确认的 full→formal_verdict_only_v1→verdict_only 投影保持，native 与 projected 不混组。

旧 main-v1-preflight-20261003-checker-v2 与更早 final receipt 对当前版本标记 stale，原目录/工件不改；新的 ID/fingerprint 不用于恢复旧任务。未创建 future Main 批次或任何 planned task Workspace，execution_authorized=false。源码保留已有未提交改动并冻结 dirty diff，未自动提交；下一次 commit 会使本 receipt stale。

## Side effects

LLM=0，Checker LLM=0，Custom Run=0，formal=0，Main started=0，paid cost=0 CNY。只读 GET 数量及响应状态在独立 ledger/report 中单列。已保存的 Pilot/IQ/failed smoke/旧 Main preflight Workspace 与用户 .env/models/harness 配置逐字节保护；本轮仅写新开发审计、新 receipt 和授权源码/文档。
