"""Prompt-size decomposition without altering the actual ContextBuilder output."""
from hashlib import sha256
import json


INSTRUCTIONS = {
    "PLAN": "Design a correct, efficient algorithm. Do not write code yet.",
    "CODE": "Implement the complete solution now.",
    "DEBUG": "Diagnose the feedback and return a corrected complete program.",
}


def prompt_breakdown(messages, *, role, plan="", current_solution="", feedback=None, recent_history=None):
    suffix = []
    if plan:
        suffix.append(("harness_plan", f"Current plan:\n{plan}"))
    if current_solution:
        suffix.append(("harness_candidate", f"Current C++20 solution:\n{current_solution}"))
    if feedback:
        rendered = json.dumps(feedback, ensure_ascii=False, default=str)
        if len(rendered) > 8000:
            rendered = rendered[:8000] + "\n[feedback clipped; full artifact retained]"
        suffix.append(("tool_feedback", f"Latest structured judge feedback:\n{rendered}"))
    if recent_history:
        suffix.append(("conversation_history", "Recent events:\n" + "\n".join(recent_history[-5:])))
    suffix.append(("phase_instruction", INSTRUCTIONS.get(role, "")))
    expected_suffix = "\n\n".join(text for _, text in suffix)
    parts = []
    complete = len(messages) == 2 and messages[0].role == "system" and messages[1].role == "user" and bool(INSTRUCTIONS.get(role))
    if complete and messages[1].content.endswith("\n\n" + expected_suffix):
        parts = [("system_prompt", messages[0].content),
                 ("problem_statement_and_public_samples", messages[1].content[:-len(expected_suffix)-2])]
        parts.extend((name, "\n\n" + text) for name, text in suffix)
        complete = sum(len(text.encode("utf-8")) for _, text in parts) == sum(len(m.content.encode("utf-8")) for m in messages)
    else:
        complete = False
    return {"status": "complete_utf8_decomposition" if complete else "decomposition_unavailable",
        "component_actual_tokens_available": False, "component_token_method": "utf8_byte_upper_bound_not_tokenizer",
        "components": [{"name": name, "utf8_bytes": len(text.encode("utf-8")),
            "conservative_token_upper_bound": len(text.encode("utf-8")), "actual_tokens": None,
            "content_sha256": sha256(text.encode("utf-8")).hexdigest()} for name, text in parts] if complete else [],
        "tool_schema_tokens": 0, "tool_schema_source": "no_native_tool_schema_in_model_request",
        "harness_state_components": ["harness_plan", "harness_candidate"],
        "safety_padding_tokens": 1024 * len(messages), "safety_padding_per_message": 1024}
