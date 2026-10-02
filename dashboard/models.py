"""Dashboard-owned read models; no web or Agent runtime dependency."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskSummary:
    task_id: str
    problem_id: Optional[str] = None
    mode: Optional[str] = None
    profile: Optional[str] = None
    policy: Optional[str] = None
    current_phase: Optional[str] = None
    status: str = "unknown"
    terminal_reason: Optional[str] = None
    final_verdict: Optional[str] = None
    terminal: bool = False
    solved: bool = False
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    finished_at: Optional[str] = None
    duration: Optional[float] = None
    llm_call_count: Optional[int] = None
    submission_attempt_count: Optional[int] = None
    submission_count: Optional[int] = None
    debug_iterations: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    usage_missing_count: Optional[int] = None
    estimated_cost: Optional[float] = None
    cost_status: str = "unknown"
    cost_currency: Optional[str] = None
    data_status: str = "ok"
    warnings: List[str] = field(default_factory=list)
    configuration_fingerprint: Optional[str] = None
    experiment_id: Optional[str] = None
    experiment_strategy: Optional[str] = None
    actual_feedback_mode: Optional[str] = None
    effective_feedback_mode: Optional[str] = None
    feedback_policy: Optional[str] = None
    feedback_mode_status: str = "unobserved"
    sample_gate_status: Optional[str] = None


@dataclass
class TimelineEvent:
    timestamp: Optional[str]
    event_type: str
    phase: Optional[str]
    correlation_id: Optional[str]
    summary: str
    metadata: Dict[str, Any]
    event_id: Optional[str] = None
    original_type: Optional[str] = None


@dataclass
class ModelCallView:
    call_id: str
    timestamp: Optional[str] = None
    phase: Optional[str] = None
    role: Optional[str] = None
    profile: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    status: str = "pending"
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    latency_ms: Optional[float] = None
    estimated_cost: Optional[float] = None
    cost_status: str = "unknown"
    cost_currency: Optional[str] = None
    request_id: Optional[str] = None
    finish_reason: Optional[str] = None


@dataclass
class ToolCallView:
    call_id: str
    name: Optional[str] = None
    timestamp: Optional[str] = None
    phase: Optional[str] = None
    status: str = "pending"
    duration_ms: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SubmissionView:
    submission_id: str
    code_version: Optional[str] = None
    code_sha256: Optional[str] = None
    model_call_id: Optional[str] = None
    status: Optional[str] = None
    verdict: Optional[str] = None
    created_at: Optional[str] = None
    finished_at: Optional[str] = None
    time_ms: Optional[float] = None
    memory_kb: Optional[float] = None
    test_summary: Optional[str] = None


@dataclass
class CodeVersionView:
    version: str
    phase: Optional[str] = None
    created_at: Optional[str] = None
    model_call_id: Optional[str] = None
    sha256: Optional[str] = None
    submission_ids: List[str] = field(default_factory=list)
    verdicts: List[str] = field(default_factory=list)


@dataclass
class ArtifactView:
    name: str
    size: int


@dataclass
class ProblemView:
    problem_id: Optional[str] = None
    title: Optional[str] = None
    statement: Optional[str] = None


@dataclass
class TaskDetail:
    summary: TaskSummary
    problem: ProblemView
    current_state: Dict[str, Any]
    solution: Optional[str]
    code_versions: List[CodeVersionView]
    model_calls: List[ModelCallView]
    tool_calls: List[ToolCallView]
    submissions: List[SubmissionView]
    judge_results: List[TimelineEvent]
    state_changes: List[TimelineEvent]
    artifacts: List[ArtifactView]
    timeline: List[TimelineEvent]
    trace_generation: int = 0
    warnings: List[str] = field(default_factory=list)
    solution_sha256: Optional[str] = None
    recovery_metrics: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Metrics:
    total_tasks: int
    solved: int
    unsolved: int
    solve_rate: float
    llm_calls: Optional[int]
    submission_attempts: Optional[int]
    submissions: Optional[int]
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    debug_iterations: Optional[int]
    wall_clock: Optional[float]
    known_costs: Dict[str, float]
    unknown_cost_tasks: int
    unknown_metric_tasks: Dict[str, int]
    verdict_distribution: Dict[str, int]
    mode_distribution: Dict[str, int]


@dataclass
class ExperimentSummary:
    name: str
    mode: str
    policy: str
    metrics: Metrics
    profiles: List[str]
    comparison_status: str = "Observed State group; comparability not established"


@dataclass
class ProfileView:
    profile: str
    provider: Optional[str]
    model: Optional[str]
    parameters: Dict[str, Any]
    pricing_status: str
    currency: Optional[str] = None
    input_cost_per_million: Optional[float] = None
    output_cost_per_million: Optional[float] = None


@dataclass
class ModelsView:
    source: Optional[str]
    status: str
    profiles: List[ProfileView]
    roles: Dict[str, str]
    escalation: Dict[str, Any]
    warning: Optional[str] = None
