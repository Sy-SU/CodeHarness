# CodeHarness 未来工作

当前已实现能力及冻结行为见 [STATUS.md](STATUS.md)，Experiment Preflight 验证见 [证据](docs/evidence/20261002-preflight.md)。以下只列未完成工作；历史原文见 [docs/history](docs/history/README.md)。替身测试不能勾选真实联调。

## 下一步，均需后续显式授权，不自动执行

- [ ] **1. explicit live checker smoke**：验证 LLM checker 的真实生成与 MiniOJ 远程执行。保持 `llm_generated_unverified`；拒绝不 DEBUG，reference sanity 不代表认证。
- [ ] **2. explicit live provider probe if required**：两款模型仅可见性已确认；8192 输出及实际输入/context 上限需独立证据，当前 needs_live_probe=true。价格/账单核验另保留来源；不能把 configured 当 verified，不修改用户配置。
- [ ] **3. 15-task pilot**：`config/experiment.pilot.yaml` 的 3 题 × 五组，使用匹配的新 preflight/运行 ID，验证 routing、feedback、checker、费用、报告、recovery 与 resume；不据此得实验结论。
- [ ] **4. 60-task main experiment**：`config/experiment.small.yaml` 保持 12 题 × 原五组 × 1 次，按冻结顺序及现有显式确认机制执行。60 CNY 是分配上限；不补跑历史失败。

## 仍需独立确认

- [ ] **P0 / 协议**：若 MiniOJ 提供公开 expected-output-independent sample judge，明确协议后接入 RemoteChecker；当前 OpenAPI 未提供，不读取 admin checker 源码/testcase。明确 `lines` / `yesno` 规范化与可信 float 容差；缺失保持 unknown/unverifiable。
- [ ] **P1 / 联调**：在新运行冻结账户身份后核验独立 Performance enrichment；已有榜单直接读取已验证，历史运行缺身份不回填。整场账户值不能作为各实验条件独立 Performance。
- [ ] **P2 / 联调**：后续授权实验自然出现正式 WA/CE/RE/TLE/MLE/OLE → DEBUG → 后续候选 → AC → REVIEW 时，按版本/hash/correlation/submission 链记录真实 `formal_recovery_to_ac`。不得制造失败或修改旧结果。
- [ ] **反馈对照 / 联调**：native verdict_only 与 projected full → verdict_only 同条件比较；full 元数据及客户端投影已有证据，历史 unknown 不回写。
- [ ] **OJ 协议 / 联调**：尚未实测的错误响应、未完成反馈、其他 Custom Run 失败/资源字段及服务端幂等/查重语义；客户端继续无自动重试、未知 fail closed。

## Future，不属于当前里程碑

- [ ] 需求另行确认后讨论 Test Generation / brute / oracle / stress testing；输入合法性和 expected 来源必须可信，执行仍通过 MiniOJ HTTP。

不增加新 Agent phase、Multi-Agent、原生工具调用调度、模型升级/REVIEW、跨题记忆、服务器或本地执行器。本轮实现/替身验收完成后停止，不自动发起下一付费批次。
