"""Bounded prompt assembly for coding phases."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from agent.models.types import AgentRole, ChatMessage


class ContextBuilder:
    """Compose only task-relevant state rather than appending unlimited history."""

    SYSTEM = (
        "You are the sole coding agent in a controlled Online Judge experiment. "
        "Follow the requested phase. Never invent judge results. When asked for code, "
        "return one complete C++20 program in a fenced code block."
    )

    def build(
        self,
        *,
        problem: Dict[str, Any],
        role: AgentRole,
        current_solution: str = "",
        plan: str = "",
        feedback: Optional[Dict[str, Any]] = None,
        recent_history: Optional[List[str]] = None,
    ) -> List[ChatMessage]:
        limits = problem.get("limits") or {}
        sections = [
            f"Phase: {role.value}",
            f"Problem: {problem.get('title', '')} ({problem.get('problem_id', '')})",
            str(problem.get("statement", "")),
            f"Input:\n{problem.get('input_specification', '')}",
            f"Output:\n{problem.get('output_specification', '')}",
            f"Limits: {limits.get('time_ms', 0)} ms, {limits.get('memory_mb', 0)} MB",
            f"Notes:\n{problem.get('notes', '')}",
        ]
        samples = problem.get("samples") or []
        for index, sample in enumerate(samples, 1):
            sections.append(
                f"Sample {index} input:\n{sample.get('input', '')}\n"
                f"Sample {index} output:\n{sample.get('output', '')}"
            )
        if plan:
            sections.append(f"Current plan:\n{plan}")
        if current_solution:
            sections.append(f"Current C++20 solution:\n{current_solution}")
        if feedback:
            rendered = json.dumps(feedback, ensure_ascii=False, default=str)
            if len(rendered) > 8000:
                rendered = rendered[:8000] + "\n[feedback clipped; full artifact retained]"
            sections.append(f"Latest structured judge feedback:\n{rendered}")
        if recent_history:
            sections.append("Recent events:\n" + "\n".join(recent_history[-5:]))
        instructions = {
            AgentRole.PLAN: "Design a correct, efficient algorithm. Do not write code yet.",
            AgentRole.CODE: "Implement the complete solution now.",
            AgentRole.DEBUG: "Diagnose the feedback and return a corrected complete program.",
            AgentRole.REVIEW: "Review correctness, complexity, and any remaining risk concisely. Do not return a new program.",
            AgentRole.TEST_GENERATION: "Propose high-value valid edge cases and expected behavior.",
        }
        sections.append(instructions[role])
        return [
            ChatMessage("system", self.SYSTEM),
            ChatMessage("user", "\n\n".join(sections)),
        ]
