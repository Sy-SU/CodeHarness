"""Presentation-only translations; Workspace values and JSON APIs stay unchanged."""
from __future__ import annotations

import re
from urllib.parse import unquote, urlsplit


LANGUAGES = {"en", "zh"}
COOKIE_NAME = "codeharness_language"
ZH = {
    "Contests": "比赛", "Run a contest": "测试比赛", "Contest tests": "比赛测试",
    "Contest ID": "比赛 ID", "Run ID": "运行 ID", "Problems": "题目",
    "Whole contest budget (CNY)": "整场预算上限（元）", "Accepted / total": "AC / 总题数",
    "Estimated cost (CNY)": "估算成本（元）", "Start contest test": "开始测试比赛",
    "Resume contest test": "恢复比赛测试", "No contest tests yet.": "尚无比赛测试。",
    "No public problems loaded.": "尚未加载公开题单。", "Contest run unavailable": "比赛记录不可用",
    "Enter a contest ID. Run its public problems sequentially with the configured model.": "输入比赛 ID，使用已配置模型依次完成公开题目。",
    "Task settings apply per problem; the whole contest cap also applies. Budget-skipped and unknown results are not AC. No official points or penalties are calculated.": "任务配置逐题生效，并受整场预算限制；预算跳过和未知结果不计 AC，暂不计算官方分数或罚时。",
    "Only public contest problems are read. Submissions use the contest endpoint; blocked visibility is not bypassed.": "仅读取公开比赛题单，提交走比赛接口；不绕过比赛可见性限制。",
    "AC counts only; official points, penalties and rankings are not calculated. Cost is a configured estimate, not a provider bill.": "仅统计 AC 数，暂不计算官方积分、罚时或排名；成本是按配置估算，并非供应商账单。",
    "Resume keeps the frozen contest problems and all saved budgets. Continue?": "恢复沿用固定题单与全部已保存预算，是否继续？",
    "contest_unavailable": "比赛读取失败，未开始调用模型。",
    "contest_problems_not_visible": "比赛题单未公开，未开始调用模型；公开后请新建运行。",
    "completed": "已完成", "blocked": "受阻", "pending": "待开始", "fetching": "读取题单中", "stopped": "已停止",
    "accepted": "已通过", "budget_exhausted": "预算已耗尽", "not_started": "未开始",
    "cost_reservation_limit": "预算不足以预留下一次模型调用",
    "experiment_budget_limit": "整场预算已用完",
    "invalid_model_output": "模型未返回完整代码", "code_extraction_failed": "无法提取完整代码",
    "judge_accepted": "正式评测通过", "result_unknown": "结果未知", "condition_mismatch": "反馈条件不满足",
    "Run a problem": "发起任务", "Task runner": "任务执行",
    "Counts include historical HTTP and model probes. Unknown counts and conditions do not establish experimental comparability.": "统计包含历史 HTTP 与模型探针；未知计数与条件不构成实验可比性证明。",
    "New runs are separated by configuration and observed feedback mode. Unknown conditions are not verified comparisons; historical State remains observational. Exact batch JSON/CSV reports are stored in workspace/.experiments.": "新任务按实际配置与反馈模式分开汇总；未知条件不视为已核验比较，历史 State 仅供观察。完整批次 JSON/CSV 报告位于 workspace/.experiments。",
    "Local bounded task runner": "本地预算受限执行", "LOCAL · CONFIRMED CALLS": "本地 · 确认后调用",
    "Choose a problem and mode. Follow the saved progress and judge result here.": "选择题目和模式，在这里查看进度与评测结果。",
    "Model strategy": "模型策略", "Mixed roles (PLAN strong / CODE standard)": "混合策略（PLAN strong / CODE standard）",
    "Fixed": "固定模型", "Start task": "开始任务", "Resume task": "恢复任务",
    "Model configuration": "模型配置", "Queuing task…": "正在排队…", "Task unavailable": "任务不可用",
    "Public samples first · 3 failed DEBUG candidates replan · no model escalation": "公开样例优先 · 3 次 DEBUG 失败重 PLAN · 不升级模型",
    "Task budget (CNY)": "任务额度（元）",
    "Task configuration": "任务配置",
    "{cost} CNY · {calls} model calls · {submissions} submissions": "{cost} 元 · {calls} 次模型调用 · {submissions} 次提交",
    "Maximum model calls": "模型调用次数上限",
    "Maximum submission attempts": "正式提交尝试上限",
    "MiniOJ HTTP timeout (s)": "MiniOJ 请求超时（秒）",
    "Judge polling interval (s)": "评测轮询间隔（秒）",
    "Judge wait deadline (s)": "评测等待上限（秒）",
    "Applies only to this new task. Saved tasks, model configuration and API credentials are unchanged.": "仅用于本次新任务，不修改已保存任务、模型配置或 API 凭据。",
    "Model calls count PLAN, CODE and DEBUG; submission attempts do not include public sample runs.": "模型调用计入 PLAN、CODE 和 DEBUG；提交尝试不含公开样例试跑。",
    "Model calls include PLAN, CODE, DEBUG and generated sample checkers; public runs do not count as submissions.": "模型调用计入 PLAN、CODE、DEBUG 和样例 checker 生成；公开试跑不占正式提交次数。",
    "Special or unknown samples may use an unverified model-generated checker. Rejection stops without DEBUG; approval may allow formal submission.": "特殊或未知样例可由模型生成尚未核实的 checker。拒绝时停止、不进入 DEBUG；认可时可继续正式提交。",
    "Reset task defaults": "恢复任务默认值",
    "At most 10 formal submissions · resume keeps all saved task settings": "最多 10 次正式提交 · 恢复沿用全部已保存任务配置",
    "Default budget: 1 CNY per task · at most 10 formal submissions · resume keeps the saved budget": "每任务默认 1 元 · 最多 10 次正式提交 · 恢复沿用已保存额度",
    "Required server feedback mode": "要求的服务端反馈模式",
    "OJ feedback mode": "OJ 实际反馈模式",
    "Agent feedback mode": "Agent 有效反馈模式",
    "Expected Agent feedback mode": "Agent 期望反馈模式",
    "Full server feedback is accepted and reduced to verdict-only; hidden testcase details are withheld.": "服务端 full 可兼容降为 verdict-only；隐藏用例内容不会提供给 Agent。",
    "Unknown or mismatched mode stops before model calls": "模式未知或不匹配时，在模型调用前停止",
    "One CODE call · at most one formal submission · no samples or retries": "一次 CODE 调用 · 至多一次正式提交 · 不跑样例、不重试",
    "I confirm this task may call the configured model API and submit to MiniOJ within its budget.": "我确认该任务可以在预算内调用已配置模型 API，并向 MiniOJ 提交。",
    "Resume may continue model calls and MiniOJ queries within the saved budget. Continue?": "恢复任务可能在已保存预算内继续调用模型和查询 MiniOJ。是否继续？",
    "Code versions and call diagnostics": "代码版本与调用诊断", "Trace and saved artifacts": "Trace 与保存工件",
    "queued": "排队中", "running": "运行中", "finished": "已完成", "failed": "启动失败", "interrupted": "已中断",
    "Overview": "概览", "Tasks": "任务", "Experiments": "实验", "Models": "模型",
    "LOCAL DASHBOARD": "本地看板", "Main navigation": "主导航",
    "Read-only workspace view": "只读工作区视图",
    "Filesystem is the source of truth.": "数据以工作区文件为准。",
    "Workspace observability": "工作区观测", "LOCAL · READ ONLY": "本地 · 只读",
    "Language": "语言",
    "CodeHarness · Task status, submission status and judge verdict are separate records.":
        "CodeHarness · 任务状态、提交状态与评测结论分别记录。",
    "Unknown": "未知", "unknown": "未知", "Not applicable": "不适用",
    "(zone unrecorded)": "（未记录时区）", "Partial Data": "数据不完整",
    "Task / Problem": "任务 / 题目", "Mode / Profile": "模式 / Profile",
    "Task status": "任务状态", "Phase": "阶段", "Verdict": "评测结论",
    "LLM calls": "模型调用", "Submissions": "提交", "Duration": "耗时",
    "Updated (UTC)": "更新于 (UTC)", "{count} attempts": "尝试 {count} 次",
    "No tasks match this view.": "没有符合条件的任务。",
    "Original type:": "原始类型：", "Safe metadata": "脱敏元数据",
    "OBSERVE / WORKSPACE": "观测 / 工作区", "Browse tasks →": "查看任务 →",
    "A durable view of tasks, calls and remote judge results.": "查看持久化的任务、调用与远程评测结果。",
    "Total Tasks": "任务总数", "Solved": "已解决", "Unsolved": "未解决",
    "Recorded AC + solved state": "已记录 AC 且状态为 solved",
    "Includes unfinished / unavailable": "包含未完成 / 不可读任务", "Solve Rate": "通过率",
    "LLM Calls": "模型调用次数", "{count} tasks with unknown counts": "{count} 个任务计数未知",
    "OJ Submissions": "OJ 确认提交",
    "{attempts} known attempts · {count} tasks unknown": "已知尝试 {attempts} 次 · {count} 个任务计数未知",
    "Input Tokens": "输入 Tokens", "Output Tokens": "输出 Tokens",
    "{count} tasks with unknown usage": "{count} 个任务用量未知",
    "Known Cost": "已知成本", "Known amounts grouped by currency": "已知金额按币种分别汇总",
    "Unknown Cost Tasks": "成本未知任务", "Unknown is not zero": "未知成本不计为零",
    "Verdict distribution": "评测结论分布", "MiniOJ verdict": "MiniOJ 评测结论",
    "Mode distribution": "模式分布", "Observed tasks": "已观测任务",
    "Counts include historical HTTP and model probes. Unknown metric counts indicate partial totals. Phase 4 Harness Loop and Phase 5 Experiment Runner remain unfinished.":
        "统计包含历史 HTTP 与模型探针；未知计数表示汇总不完整。Phase 4 Harness Loop 与 Phase 5 Experiment Runner 仍未完成。",
    "Recent tasks": "最近任务", "View all →": "查看全部 →",
    "OBSERVE / TASK INDEX": "观测 / 任务索引",
    "Search durable task summaries. Traces load when you open a task.": "搜索已保存的任务摘要；打开详情后才加载 Trace。",
    "Search task or problem": "搜索任务或题目", "Task ID or problem ID": "任务 ID 或题目 ID",
    "Task ID": "任务 ID", "Problem ID": "题目 ID", "Contains…": "包含…",
    "Mode": "模式", "Profile": "Profile", "Judge verdict": "评测结论", "All": "全部",
    "Sort by": "排序", "Last updated": "最近更新", "Created": "创建时间",
    "Apply": "应用", "Reset": "重置",
    "INSPECT / SELECTED CONFIGURATION": "查看 / 已选配置",
    "Profile mapping": "Profile 映射", "Configuration only · no provider calls": "仅展示配置 · 不调用 Provider",
    "Provider": "Provider", "Model": "模型", "Parameters": "参数", "Pricing": "价格",
    "Known": "已知", "Missing Pricing": "缺少价格", "Missing Usage": "缺少用量",
    "Not Applicable": "不适用", "Mixed Currencies": "混合币种",
    "{currency} · input {input} / output {output} per million tokens": "{currency} · 每百万 Tokens：输入 {input} / 输出 {output}",
    "No selected model mapping.": "尚未选择模型映射。", "Role policy": "Role 策略",
    "No loaded policy.": "尚未加载策略。", "DEBUG escalation": "DEBUG 升级策略",
    "No loaded escalation configuration.": "尚未加载升级配置。",
    "Role policy is configuration for a single Agent. Historical probes do not establish a formal experiment strategy.":
        "Role 策略用于单 Agent；历史探针不代表正式实验策略。",
    "No configuration selected": "尚未选择配置", "Loaded configuration": "已加载配置",
    "Configuration unavailable": "配置不可用",
    "Select --model-config or MODEL_CONFIG to inspect a mapping.": "通过 --model-config 或 MODEL_CONFIG 选择要查看的配置。",
    "Placeholder model names; this is a structural example.": "模型名称是占位值；此文件仅为结构示例。",
    "Configuration view only; no claim of a finalized experiment policy.": "仅展示配置，不表示实验策略已确定。",
    "Unable to parse the selected configuration. No credentials are required or displayed.": "无法解析所选配置；此页面无需凭据，也不显示凭据。",
    "OBSERVE / STATE GROUPS": "观测 / State 分组",
    "Existing results grouped by mode and recorded experiment variant.": "按模式及已记录的实验变体分组展示现有结果。",
    "These are observed State summaries. Model mappings, feedback conditions and budgets are not fully recorded in historical State; comparability is not established. Phase 5 Experiment Runner is not implemented.":
        "此处仅汇总已有 State。历史 State 未完整记录模型映射、反馈条件与预算，尚不能确认实验可比性；Phase 5 Experiment Runner 尚未实现。",
    "Group / Mode": "分组 / 模式", "Profile / Policy": "Profile / 策略", "Solved / Rate": "已解决 / 通过率",
    "Input / Output": "输入 / 输出", "Submissions / Attempts": "确认提交 / 尝试",
    "Debug": "调试", "Wall Clock": "总耗时", "Observed profiles:": "已观测 Profile：",
    "No runs": "暂无运行", "{count} unknown cost tasks": "{count} 个任务成本未知",
    "Totals include only known metric values; missing values remain Unknown. Mixed policies keep their recorded variant name.":
        "仅汇总已知指标，缺失值保持未知；混合策略保留其已记录的变体名称。",
    "← All tasks": "← 全部任务", "TASK /": "任务 /",
    "Terminal · refresh stopped": "已结束 · 停止刷新", "Live · updates every 2 s": "运行中 · 每 2 秒刷新",
    "Current phase": "当前阶段", "Terminal reason": "终止原因", "Start (UTC)": "开始于 (UTC)",
    "End (UTC)": "结束于 (UTC)", "Estimated Cost": "估算成本", "Estimated cost": "估算成本",
    "Submission Attempts": "提交尝试次数", "Confirmed Submissions": "确认提交次数",
    "Debug Iterations": "调试轮数", "Task sections": "任务详情导航",
    "Solution": "解答", "Versions": "版本", "Model calls": "模型调用", "Tool calls": "工具调用",
    "Timeline": "时间线", "Artifacts": "工件", "Current / final solution": "当前 / 最终解答",
    "solution.cpp · read only": "solution.cpp · 只读", "Copy": "复制", "Copied": "已复制",
    "Select text to copy": "请选择文本复制", "Solution unavailable": "解答不可用",
    "Code versions": "代码版本", "Recorded candidate associations": "已记录的候选关联",
    "Version": "版本", "Phase / Created (UTC)": "阶段 / 创建于 (UTC)", "Model call ID": "模型调用 ID",
    "Submission ID": "提交 ID", "No recorded code versions.": "没有已记录的代码版本。",
    "Usage and cost preserve missing data": "缺失的用量与成本保持未知", "Phase / Role": "阶段 / Role",
    "Provider / Model": "Provider / 模型", "Status": "状态", "Latency": "延迟", "Call / Request": "调用 / 请求",
    "No recorded model calls.": "没有已记录的模型调用。",
    "Safe summaries; bodies are collapsed": "摘要已脱敏；正文默认折叠", "Tool": "工具",
    "Timestamp (UTC)": "时间 (UTC)", "Details": "详情", "No recorded tool calls.": "没有已记录的工具调用。",
    "Submissions / judge": "提交 / 评测", "Confirmed IDs; attempts are counted above": "展示已确认的 ID；尝试次数见上方",
    "Code version": "代码版本", "Submission status": "提交状态", "Time": "时间", "Memory": "内存",
    "Tests": "测试", "Created / Finished (UTC)": "创建 / 完成于 (UTC)",
    "No confirmed submissions.": "没有已确认的提交。", "recorded events · UTC": "条已记录事件 · UTC",
    "No complete events available.": "暂无完整事件。", "Artifacts / state": "工件 / State",
    "Allowlisted files · sanitized text": "白名单文件 · 脱敏文本", "Current state · safe JSON": "当前 State · 脱敏 JSON",
    "Problem statement · plain text": "题面 · 纯文本",
    "Terminal · draining final events": "已结束 · 读取最后事件",
    "Temporarily unavailable · retaining last view": "暂时不可用 · 保留上次视图",
    "Invalid JSONL line skipped": "已跳过无效 JSONL 行",
    "Incomplete JSONL tail; awaiting append": "JSONL 尾行未完成，等待追加",
    "Trace unavailable; showing last valid events": "Trace 不可用，显示上次有效事件",
    "Current solution.cpp differs from the recorded candidate hash; verdicts remain tied to submission records":
        "当前 solution.cpp 与已记录候选的哈希不同；评测结论仍关联原提交记录",
    "{file}: unavailable / partial data": "{file}：不可用 / 数据不完整",
    "{file}: unavailable / partial data; showing last valid data": "{file}：不可用 / 数据不完整；显示上次有效数据",
    "Submission {id}: code hash mismatch": "提交 {id}：代码哈希不一致",
    "{passed} / {total} passed": "通过 {passed} / {total}",
}


ZH.update({
    "Official Performance": "官方 Performance",
    "Performance fetched (UTC)": "Performance 获取时间（UTC）",
    "Refresh official Performance (GET only)": "刷新官方 Performance（仅 GET）",
    "Performance refresh unavailable": "无法刷新 Performance",
    "Performance refreshed; Agent execution was not started.": "Performance 已刷新，未启动 Agent 执行。",
    "Official Performance is cumulative for the contest account, including other runs. It is not a per-task score.": "官方 Performance 是比赛账户的累计成绩，包含其他运行，不是单题或独立实验得分。",
    "identity_unresolved": "身份无法确认", "identity_ambiguous": "账户行重复",
    "account_not_ranked": "账户尚未上榜", "unavailable": "不可用", "invalid": "数据无效",
    "confirmed": "已确认", "pending": "等待中",
    "Harness recovery": "Harness 修复指标", "Sample gate": "样例门禁",
    "First formal submit AC": "首次正式提交 AC", "Recovered to AC": "失败后修复至 AC",
    "Formal recovery to AC": "正式失败后 DEBUG 至 AC", "Recovery type": "修复来源",
    "Successful DEBUG": "成功 DEBUG", "Replans": "重新规划次数",
    "Sample gate rejects": "样例拒绝次数", "Unverifiable sample checks": "样例无法验证次数",
    "Candidate versions": "候选版本数", "Yes": "是", "No": "否", "None": "无",
    "sample_pass": "样例通过", "sample_wrong_answer": "样例答案错误",
    "sample_program_failure": "样例程序失败", "sample_check_unverifiable": "样例无法验证",
    "no_public_samples": "无公开样例", "sample_failure": "样例失败",
    "Recovery requires a later linked candidate and formal AC; infrastructure errors do not earn recovery credit.": "修复须有后续关联候选和正式 AC；基础设施错误不计算法修复。",
})


def translate(message, language="en", **values):
    text = str(message)
    result = ZH.get(text, text) if language == "zh" else text
    return result.format(**values) if values else result


def presentation_message(message, language="en"):
    """Translate known reader diagnostics without touching IDs or raw metadata."""
    text = str(message)
    patterns = [
        (r"(.+): unavailable / partial data(; showing last valid data)?", lambda m:
         translate("{file}: unavailable / partial data" + ("; showing last valid data" if m[2] else ""), language, file=m[1])),
        (r"Submission (.+): code hash mismatch", lambda m:
         translate("Submission {id}: code hash mismatch", language, id=m[1])),
        (r"(\d+) / (\d+) passed", lambda m:
         translate("{passed} / {total} passed", language, passed=m[1], total=m[2])),
    ]
    for pattern, render in patterns:
        match = re.fullmatch(pattern, text)
        if match:
            return render(match)
    return translate(text, language)


def local_redirect(value: str) -> str:
    """Reject protocol-relative, encoded, backslash and control-character redirects."""
    if len(value) > 4096 or not value.startswith("/"):
        return "/"
    decoded = value
    for _ in range(8):
        new = unquote(decoded)
        if new == decoded:
            break
        decoded = new
    else:
        return "/"
    if not decoded.startswith("/") or decoded.startswith("//") or "\\" in decoded:
        return "/"
    if any(ord(char) < 32 or ord(char) == 127 for char in decoded):
        return "/"
    try:
        parts = urlsplit(decoded)
        if parts.scheme or parts.netloc:
            return "/"
    except ValueError:
        return "/"
    return value
