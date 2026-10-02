"""An unverified LLM checker, executed only by MiniOJ Custom Run."""
from __future__ import annotations

import json

from agent.models.types import AgentRole, ChatMessage

INSTRUCTIONS = """Write a standalone C++20 OUTPUT CHECKER for this problem, not a solution.
Judge whether the supplied candidate output satisfies the public problem constraints.
Multiple valid outputs must be accepted. Never compare against the sample answer.
Do not invent floating tolerances if none are specified; in that case print {"valid":null}.
stdin is a byte-length-prefixed pair:
  decimal input byte length + newline, then exactly that many input bytes,
  decimal output byte length + newline, then exactly that many output bytes.
Parse both blocks, validate ranges, constraints and completeness (including trailing tokens).
Print exactly one JSON object: {"valid":true}, {"valid":false}, or {"valid":null} when unsure.
No files, network, subprocesses or expected-answer lookup. Return a complete fenced C++20 program.
This checker is unverified; its result is recorded separately from official Judge verdicts.
"""


def checker_messages(context_builder, problem):
    messages = context_builder.build(problem=problem, role=AgentRole.CODE)
    # Replace CODE's solution instruction; no candidate solution is supplied.
    content = messages[-1].content.rsplit("\n\n", 1)[0] + "\n\n" + INSTRUCTIONS
    return [messages[0], ChatMessage("user", content)]


def checker_stdin(stdin, stdout):
    return f"{len(stdin.encode('utf-8'))}\n{stdin}{len(stdout.encode('utf-8'))}\n{stdout}"


def generated_decision(result):
    if (result.status != "OK" or result.exit_code not in {None, 0}
            or result.stdout_truncated or result.stdout is None):
        return None
    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != {"valid"}:
        return None
    value = data["valid"]
    return value if isinstance(value, bool) else None
