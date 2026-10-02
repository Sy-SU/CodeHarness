# 2026-10-02 Experiment Preflight 验证

本轮仅实现正式实验前的独立只读检查。保留已有未提交实现；不修改 `.env`、用户 models/harness YAML、历史 Workspace/State/Trace/实验结果，也不改 Harness 状态机或反馈投影语义。60 任务与 Pilot 均未执行。

## 当前判定与审计入口

主实验为 **READY_WITH_WARNINGS**，blockers 为空。底层 checker：token 4、special 5、unknown 3、exact/float 0。预计 Harness 策略为 token 4、llm_generated_unverified 8；8 个后者不计为 verified checker。路由分层：fully_client_verifiable 4、remote_verifiable 0、llm_checker_required 8、unverifiable 0。分层中的 LLM 路径仍是未认证检查，不代表可信样例通过。

standard=qwen3.8-flash、strong=qwen3.7-plus 均在 GET /models 中出现，model_available=true。configured 输出请求 8192、输入预留边界 32768；standard 输入/输出价格 0.8/2.7、strong 2/8，单位 CNY/百万 tokens。verified 输出/context/输入上限和价格均 null，needs_live_probe=true。temperature/top_p 未配置，保持 null；enable_thinking=false 是配置。预算上限不是预计实际费用，配置价格不是核实账单。

GET /api/v1/me 明确声明 actual=full；effective=verdict_only，policy=formal_verdict_only_v1，反馈检查 READY。未增加 native/projected 对照；无单题或单 condition Performance。

首次只读核验 26 个 GET 全部 HTTP 200：me 1、公开题目 metadata 12、清洗题面 12、Provider models 1。最小非秘密证据见 [只读 metadata](20261002-preflight-read-only.json)。此文件只证明 metadata，不使用临时草稿的旧 fingerprint 授权执行。最终冻结时的独立 GET 清单、时间和 status 保存于各 preflight.json 的 side_effects，不进入历史实验。

最终新报告目录（每份六个文件，不存在覆盖）：

- `workspace/.experiments/small-preflight-20261002/preflight/`：主实验 60 任务。
- `workspace/.experiments/pilot-preflight-20261002/preflight/`：Pilot 15 任务。

文件为 preflight.json/md、execution-order.json、model-snapshot.json、prompt-snapshot.json、checker-summary.json。报告保存逐题 checker 类型/来源/容差/样例数量/风险，actual/effective 反馈，模型 configured/verified、blockers/warnings，以及文件和 bundle hash。未创建 task State/Trace、未运行 solve。

## 冻结与执行守卫

Prompt 内容包含 system、PLAN/CODE/DEBUG、sample checker generation、完整动态 assembly source、各 prompt version/SHA-256 与 bundle hash；不只保存路径。指纹覆盖 commit SHA、git dirty/diff hash、parsed/raw config、problem list、model snapshot/原始模型配置、harness、prompt、实际加载 runtime source、checker/feedback policy version/hash、OJ/provider endpoint、execution order version/seed/hash。dirty diff 包含 staged、unstaged 与 untracked 文件内容；dirty 只警告，不自动提交。

seed 固定 20261002，排序版本 problem_blocks_balanced_sha256_v1：按 problem 为 block，SHA-256 排序并轮换五组位置，每完整五题循环每组占每个位置一次。60 对 task 全部唯一、无缺失；相同输入/seed 可重现。seed 不冒充模型 sampling seed。

新准备主实验/Pilot 必须指定匹配 preflight ID 和独立新运行 ID，并保留原确认标志。启动/resume/下一 task 前核对当前内容指纹；resume 绑定原 report hash，拒绝替换配置或 preflight。明确反馈及可获取的 public problem input hash 也在新任务前重查。未启动的后续任务在 drift 时停止；原历史签名和顺序保持不变。

价格、输入/输出边界、最少启动调用和批次统一任务额度均校验。未知 checker 不删除题；只有当前配置没有合法正式路径或策略/配置本身非法才阻塞。Legacy whitespace 不能通过新 preflight 被认证为 trusted checker。未用的 Profile 不做 Provider 查询；既有本地 REVIEW 的 checkpoint 定义依赖保留，不调用模型 REVIEW。

报告补齐 condition、checker_type/source/policy/stratum、候选版本链、Custom Run/正式提交别名、Trace 调用/usage/CNY cost 和新 Trace wall time；旧 Trace 缺 wall time 则 null。formal failure → DEBUG → 后续候选 → AC → REVIEW 的严格 recovery 定义未改，基础设施错误及 LLM checker reject 不获算法修复计数。

## 自动验证

| 检查 | 结果 |
| --- | --- |
| 修改前全量 pytest | 508 passed |
| Preflight 新专项 | 71 passed；Fake/MockTransport/local files，无真实网络或付费 POST |
| Preflight/experiment/preparation/recovery 专项 | 150 passed |
| 全量 pytest | 579 passed |
| 系统 Python 3.9 compileall | 通过；缓存定向 `/private/tmp`，避免系统默认 cache 写权限限制 |
| JS syntax | launch.js / dashboard.js / contest.js 通过，本轮未改 JS |
| git diff --check / CLI --help | 通过，新增 preflight / --preflight-id / --execution-seed / --offline |
| wheel / sdist | offline build 成功，发行包含 Preflight 模块和 Pilot 配置；不含 .env、用户 models/harness 配置、Workspace、MiniOJ server、hidden testcase |
| 解包 sdist 全量测试 | 独立解包目录使用项目 venv、清除 PYTHONPATH；579 passed |
| Secret scan | 实际 .env secret 值仅在内存匹配，不打印值；源码/说明/配置/测试/工件及最终 wheel/sdist clean |
| 既有文件保护 | 453 个禁止改动的文件 SHA-256 全不变，包括三份用户配置与所有历史工件 |

专项覆盖 exact/token/可信 float/缺容差/special/unknown/LLM 未认证；不猜题面容差；不可执行门禁与 rating/checker drift；配置/partial/null/unavailable/paginated Provider metadata、预算与 generation JSON；native/projected/unknown 反馈；prompt/model/checker/feedback/harness/problem/Git/raw file/endpoint drift；完整 60-task 平衡顺序；不可覆盖/符号链接/快照完整性；resume 原 ID/hash、批次中途漂移及 public input 变化；指标列齐全和 Pilot 条件。

## 副作用与后续

真实 LLM calls=0、Custom Runs=0、formal submissions=0、paid requests=0、paid cost=0。全部真实 HTTP 为上述只读 GET，各报告有独立 ledger；自动测试仅 Fake/MockTransport。未访问 admin checker/testcase、内部数据库、远程 shell，未在本地执行 contestant/checker 代码。

Pilot 配置为原题单中的 CF2127C/CF2125D/CF2127D，rating 1400/1600/1800、token checker，3 × 原五组=15，未执行。当前静态/只读条件允许在警告范围内准备 Pilot；LLM checker live smoke、必要的 Provider live probe、Pilot 本身和主实验都需要后续明确授权。主实验未验证真实 LLM checker 可靠性、正式自然 recovery 或同条件比较；不能从 READY_WITH_WARNINGS 推导工程或实验结论。
