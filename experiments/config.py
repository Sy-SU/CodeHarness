"""Explicit, versioned experiment conditions; no built-in problem/model choice."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import yaml

from agent.core.harness import HarnessPolicy
from agent.execution import RunRequest
from agent.core.checker import SampleGatePolicy

IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$")
MAIN_RECEIPT_POLICY = "main_final_receipt_v1"


def requires_main_receipt(config):
    """The agreed 12 x 5 main layout cannot launch with a generic preflight."""
    return len(config.problems) == 12 and len(config.strategies) == 5 and config.task_count == 60


@dataclass(frozen=True)
class Strategy:
    name: str
    mode: str
    profile: Optional[str] = None
    roles: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or not IDENTIFIER.fullmatch(self.name):
            raise ValueError("Strategy name must be a short safe identifier")
        RunRequest("validation", self.mode, self.profile, self.roles)


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    problems: List[str]
    strategies: List[Strategy]
    repetitions: int
    total_cost_cny: float
    model_config: Path
    harness: HarnessPolicy = field(default_factory=HarnessPolicy)
    http_timeout: float = 15
    poll_interval: float = 1
    deadline: float = 120
    expected_feedback_mode: Optional[str] = "verdict_only"
    require_feedback_mode: bool = True
    send_custom_run_code_alias: bool = False
    schema_version: str = "phase5-v1"
    contest_id: Optional[str] = None
    sample_checking: Optional[dict] = field(default_factory=lambda: SampleGatePolicy().as_dict())
    problem_metadata: Dict[str, dict] = field(default_factory=dict)
    preparation: dict = field(default_factory=dict)
    execution_seed: Optional[int] = None

    def __post_init__(self):
        if self.schema_version != "phase5-v1":
            raise ValueError("Unsupported experiment schema")
        if self.execution_seed is not None and (isinstance(self.execution_seed, bool)
                or not isinstance(self.execution_seed, int) or not 0 <= self.execution_seed < 2**64):
            raise ValueError("execution_seed must be an unsigned 64-bit integer")
        if self.sample_checking is not None:
            SampleGatePolicy.from_dict(self.sample_checking)
        if not isinstance(self.name, str) or not IDENTIFIER.fullmatch(self.name):
            raise ValueError("Experiment name must be a short safe identifier")
        if not isinstance(self.problems, list) or not self.problems or any(
            not isinstance(problem, str) or problem != problem.strip() for problem in self.problems
        ) or len(set(self.problems)) != len(self.problems):
            raise ValueError("problems must be a nonempty, unique list")
        if not isinstance(self.strategies, list) or not self.strategies or len({s.name for s in self.strategies}) != len(self.strategies):
            raise ValueError("strategies must be a nonempty list with unique names")
        if isinstance(self.repetitions, bool) or not isinstance(self.repetitions, int) or self.repetitions < 1:
            raise ValueError("repetitions must be a positive integer")
        if self.task_count > 1000:
            raise ValueError("One experiment is limited to 1000 planned tasks")
        if isinstance(self.total_cost_cny, bool) or not isinstance(self.total_cost_cny, (float, int)) or not math.isfinite(self.total_cost_cny) or self.total_cost_cny <= 0:
            raise ValueError("An explicit positive finite total_cost_cny is required")
        for problem in self.problems:
            for strategy in self.strategies:
                self.request(problem, strategy)
        self.validate_preparation()

    def validate_preparation(self):
        if not isinstance(self.problem_metadata, dict) or set(self.problem_metadata) - set(self.problems):
            raise ValueError("Metadata must belong to configured problems")
        for metadata in self.problem_metadata.values():
            if not isinstance(metadata, dict) or set(metadata) - {"rating", "rating_source", "fetched_at"}:
                raise ValueError("Unsupported evaluation metadata")
            rating = metadata.get("rating")
            if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or rating < 0):
                raise ValueError("Rating must be a nonnegative integer or null")
            if rating is not None and not metadata.get("rating_source"):
                raise ValueError("Known ratings require a source")
            for key in ("rating_source", "fetched_at"):
                if metadata.get(key) is not None and not isinstance(metadata[key], str):
                    raise ValueError("Metadata provenance must be text")
        if not isinstance(self.preparation, dict) or set(self.preparation) - {"problem_count", "rating_counts", "five_conditions"}:
            raise ValueError("Unsupported preparation requirements")
        count = self.preparation.get("problem_count")
        if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count != len(self.problems)):
            raise ValueError("Prepared problem count differs from requirements")
        ratings = self.preparation.get("rating_counts", {})
        if not isinstance(ratings, dict):
            raise ValueError("rating_counts must be a mapping")
        from collections import Counter
        observed = Counter(str(self.problem_metadata.get(problem, {}).get("rating")) for problem in self.problems)
        for rating, amount in ratings.items():
            if (not isinstance(rating, str) or not rating.isdigit() or isinstance(amount, bool)
                    or not isinstance(amount, int) or amount < 1 or observed[rating] != amount):
                raise ValueError("Prepared rating distribution differs from requirements")
        flag = self.preparation.get("five_conditions", False)
        if not isinstance(flag, bool):
            raise ValueError("five_conditions must be boolean")
        if flag:
            expected = {("code-only", "standard"), ("code-only", "strong"),
                        ("harness-loop", "standard"), ("harness-loop", "strong")}
            fixed = {(s.mode, s.profile) for s in self.strategies if s.profile and not s.roles}
            mixed = [s for s in self.strategies if s.mode == "harness-loop" and s.profile is None
                     and s.roles == {"PLAN": "strong", "CODE": "standard", "DEBUG": "standard"}]
            if len(self.strategies) != 5 or fixed != expected or len(mixed) != 1 or self.repetitions != 1:
                raise ValueError("Preparation requires exactly five agreed conditions and one repetition")

    @property
    def task_count(self):
        return len(self.problems) * len(self.strategies) * self.repetitions

    def request(self, problem, strategy, *, policy=None):
        return RunRequest(problem, strategy.mode, strategy.profile, strategy.roles,
            policy or self.harness, self.http_timeout, self.poll_interval, self.deadline,
            self.expected_feedback_mode, self.require_feedback_mode, self.send_custom_run_code_alias,
            self.contest_id, self.sample_checking)

    def as_dict(self):
        value = asdict(self)
        value["model_config"] = str(self.model_config)
        if self.contest_id is None:
            value.pop("contest_id")  # preserve existing batch/checkpoint signatures
        if self.sample_checking is None:
            value.pop("sample_checking")
        if not self.problem_metadata:
            value.pop("problem_metadata")
        if not self.preparation:
            value.pop("preparation")
        if self.execution_seed is None:
            value.pop("execution_seed")  # do not upgrade historical signatures
        return value

    @classmethod
    def from_dict(cls, data, *, base=Path(".")):
        if not isinstance(data, dict):
            raise ValueError("Experiment config must be a mapping")
        data = dict(data)
        data["strategies"] = [Strategy(**item) for item in data.get("strategies", [])]
        data["harness"] = HarnessPolicy(**data.get("harness", {}))
        path = Path(data["model_config"])
        data["model_config"] = (base / path).resolve() if not path.is_absolute() else path.resolve()
        return cls(**data)

    @classmethod
    def from_yaml(cls, path):
        path = Path(path).resolve()
        return cls.from_dict(yaml.safe_load(path.read_text(encoding="utf-8")), base=path.parent)
