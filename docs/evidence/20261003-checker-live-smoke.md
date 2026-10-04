# Checker fix / bounded live smoke（2026-10-03）

本轮最终 checker Gate 为 **CHECKER_LIVE_SMOKE_FAILED**：8 题各一次真实 checker generation，1 PASS / 7 FAIL。8 LLM、3 MiniOJ Custom Run、0 formal、0 Main task、0 generated checker 本地执行；配置估价 **0.0493199 CNY**，provider-reported/billed 均 null。不重生成，不补调算法 prompt，不将成功 sanity 当语义认证。未来 Main **NOT STARTED**，新 Main receipt 保留阻塞。

## 实现与旧行为

当前旧源码已经保持 LLM rejection 为 unverifiable、默认停止且不 DEBUG；本轮没有把历史行为描述成“原来会制造 WA”。缺口是 checker 状态/错误分类、冻结 artifact 身份和 generation/checker/candidate/Custom Run 审计关联。新增 sample_check_v2、checker_state_v2、llm_checker_prompt_v2、length_prefixed_valid_json_v2；v1 checkpoint/显式配置兼容，原 checker prompt 留存，不批量迁移 State。

kind 为 exact/token/float/special/unknown；source 为 problem_metadata/explicit_config/remote_judge/llm_generated，原路径保留于 CheckerSpec；verification 为 client_verified/remote_verified/llm_generated_unverified/unverifiable。LLM checker 永远不因执行/sanity 成功升级 trust。可信 sample reject 仍 DEBUG；unverified candidate pass 标记 pass_unverified 后按冻结正向策略可提交，reject 标记 rejected_unverified。generation invalid/failure、CE/RE/TLE/IE、未知/非法 JSON 与 reference reject 分别进入 checker-specific failure，默认无 DEBUG/正式提交，不算 sample/algorithm/formal recovery。原有明确 on_unverifiable=submit 兼容，不自动放宽策略。

Checker-only prompt 要求源码、不要求 Markdown；包含公开输入/输出/约束/样例，使用现有 extractor，明确多解合法性验证、禁 sample equality/hardcode/oracle/hidden/admin/official solution/files/network。算法 ContextBuilder/PLAN/CODE/DEBUG/extraction 未改。参考样例必须先接受；拒绝停止该题，不做未证明非法的 negative sanity。

Task-local identity 绑定 task/problem/frozen problem SHA、完整 checker messages/version SHA、provider/model/Profile 和 contract。checkpoint 持久化 source/hash/call metadata、sanity 和 execution；已知完成工件/执行复用，未知 paid call/Custom Run intent 不重发，已提交 checkpoint 的缺失 Trace observation 可本地修复。每个 decision 保存 checker ID、generation call、candidate version/SHA、execution ID/run event。新增八项 checker 计数不混入可信 sample reject 或 recovery。无跨 task/实验 checker 注入。

## 真实 8 题结果

[Smoke summary / 每题工件及 hash](../../workspace/.checker-smoke/checker-live-smoke-20261003-v2/summary.json)；[HTTP ledger](../../workspace/.checker-development/checker-fix-20261003-v2/smoke-http.json)；[Provider usage](../../workspace/.checker-development/checker-fix-20261003-v2/provider-usage.json)。每题生成上限 1 次、1 CNY 预留，合计 8 CNY 分配上限；每题最多 3 reference Custom Runs，retry=0。只复用现有 budget guard，只注册 run_code，不调用 Harness/contestant generation/formal submit。8 题当前 metadata GET 为 unknown 3 / special 5；公开题面通过 sanitized Agent GET。

| Problem | Kind | Generation | Remote execution | Reference sanity | Live path | Configured estimate CNY |
| --- | --- | --- | --- | --- | --- | ---: |
| CF2119B | unknown | failed：JSON only | not_run | not_run | FAIL | 0.0011047 |
| CF2118B | special | failed：JSON only | not_run | not_run | FAIL | 0.0008475 |
| CF2113B | unknown | failed：JSON only | not_run | not_run | FAIL | 0.0008831 |
| CF2111D | special | source extracted | RE | unknown | FAIL | 0.0129562 |
| CF2110C | special | failed：JSON only | not_run | not_run | FAIL | 0.0009527 |
| CF2103C | unknown | source extracted / length | CE | unknown | FAIL | 0.0233944 |
| CF2084D | special | failed：JSON only | not_run | not_run | FAIL | 0.0011824 |
| CF2092D | special | source extracted | OK | pass | PASS | 0.0079989 |

5 次模型仅返回 valid:null JSON，没有 C++ source，不执行。CF2111D source SHA `58b5c2589c9211a66e342bed817d6feb32360a1588fdf90d31b85424ce8e836a`，远程 exit=1/stderr WRONG_ANSWER，违反成功 JSON 执行契约，记 checker_execution_failed。CF2103C source SHA `576a3252df461b892d3e5c1af3ac5bd928f4735f736100322c831575f0418917`，finish_reason=length、输出 8192、缺右括号而 CE；不增大输出参数或修代码重跑。CF2092D source SHA `291d1df7b78963a3140019a87d37aae2b9ad65a3fff41aaf68b19f4a6fb66ebb`，Custom Run OK/exit=0/stdout valid:true，reference-only PASS；没有 candidate judgment 或负例 oracle。

8 次实际响应模型均 qwen3.8-flash（CODE/standard）；input=10171/output=15253/cached=0，原始 numeric usage/request_id/finish_reason 均可查。temperature/top_p 未指定保持 null、enable_thinking=false、请求 max_tokens=8192、本地输入预留 65536、transport 各阶段 inactivity 600s、overall=null、retry=0；这些配置未修改。供应商最大 context/input/output、价格/default sampling 和实际扣费仍未认证。smoke 只覆盖 standard；不冒充 strong checker coverage。

[Completed smoke resume](../../workspace/.checker-development/checker-fix-20261003-v2/completed-smoke-resume.json) 在禁止所有 HTTP/model/run/submit 的守卫下通过：0 新模型/Custom/formal/HTTP，84 个 task 工件不变，已知失败原样保留。模型 JSON/ref/runtime 失败属于 checker path，不是算法错误或自然 recovery。

## 新 Main receipt 与历史保护

旧 `main-v1-preflight-20261003-final` / future `main-v1-20261003` / fingerprint `f9679e95bd9299e5cf887b7ed64de31925dfd835948a5724320d0de7b20f29a9` stale。其原件、Pilot/IQ/dedup/旧 Workspace 不修改；[独立 stale 记录](../../workspace/.checker-development/checker-fix-20261003-v2/stale-receipt.json) 保存原 receipt SHA。

新 Preflight ID 为 `main-v1-preflight-20261003-checker-v2`；新 future Main ID 为 `main-v1-20261003-checker-v2`。最终 Git SHA/dirty diff/new fingerprint、checker implementation/prompt/contract hash、feedback actual/effective、blockers/warnings 以 [新 final receipt](../../workspace/.experiments/main-v1-preflight-20261003-checker-v2/preflight/final-receipt.json) / [report](../../workspace/.experiments/main-v1-preflight-20261003-checker-v2/preflight/preflight.json) / [matrix](../../workspace/.experiments/main-v1-preflight-20261003-checker-v2/preflight/checker-matrix.json) 为准；Main Gate 阻塞 7 个未通过路径。Matrix 仍 client_verified=4、llm_generated_unverified=8、remote_verified=0，smoke 仅增加 live evidence，不升级 verification。

Main 原题单/五组/seed/预算不变，只显式 bump sample_checking.version；此前已授权的 infrastructure 配置引用保留。`authorized_checker_fix_v2` 审计限 8 个 checker 相关 Agent 文件；[scope 证据](../../workspace/.checker-development/checker-fix-20261003-v2/checker-change-scope.json) 的 protected algorithm before/after hash 相同，包括原 recovery AST、algorithm functions、状态转移、提交/去重/REVIEW/预算/工具/工作空间；原模型/生成参数/feedback/stop policy 仍与 Pilot 比较。新 receipt 绑定全部 smoke 文件 hash，Runner 缺失/漂移/失败 Gate 在批次创建前阻塞，不自动重做 Preflight。Smoke source 不进入 Main tasks。

## 工程验证与边界

真实调用前 targeted=119、full=700 均通过；包括未认证拒绝/可信 token DEBUG/正向提交、sanity 失败、checker CE/RE/TLE/IE/输出协议、invalid generation、known artifact/中断窗口复用、unknown paid 不重试、legacy v1 resume、metric/recovery 隔离、bounded smoke/failed evidence/Main binding/no local source execution。新增 Main/v2 smoke 替身测试与真实结果分别报告，不用 Fake pass 替代 live failure。

本轮结束时 targeted/full pytest、Python 3.9/venv compileall、JS 是否变化、diff、offline wheel/sdist、包成员/secret scan、独立解包源码完整 pytest 的命令、数量、exit/package hash 均见 [checks](../../workspace/.checker-development/checker-fix-20261003-v2/checks.json)。2167 个历史 Workspace/用户 .env/models.yaml/harness.yaml 文件按 hash 保护。发行包不含真实密钥、运行 Workspace、MiniOJ server/testcases/raw paid artifacts；生成 checker 仅通过 MiniOJ 运行，本地执行次数 0。

[最终只读 preflight HTTP](../../workspace/.checker-development/checker-fix-20261003-v2/preflight-http.json) 仅公开 metadata/models/me GET，0 新 LLM/Custom/formal；反馈按服务端声明读取，full 仅可经 formal_verdict_only_v1 投影 effective verdict_only，unknown 阻塞。新 fingerprint 冻结 dirty 工作树，不自动提交、不覆盖既有修改。工程通过不消除 7 个真实 live blocker；本轮结束停止，不启动 Main。
