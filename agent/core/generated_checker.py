"""An unverified LLM checker, executed only by MiniOJ Custom Run."""
from __future__ import annotations

import json

from agent.models.types import AgentRole, ChatMessage

LEGACY_INSTRUCTIONS = """Write a standalone C++20 OUTPUT CHECKER for this problem, not a solution.
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


CHECKER_PROMPT_VERSION = "llm_checker_prompt_v2"
CHECKER_CONTRACT_VERSION = "length_prefixed_valid_json_v2"
INSTRUCTIONS = """Write one complete standalone C++20 OUTPUT CHECKER source program only.
No Markdown fences, explanations, algorithm discussion, extra files or test data.
Use the public statement, input format, output requirements and constraints to validate
the INPUT and supplied CANDIDATE OUTPUT. Accept every valid construction, permutation
or sequence, including alternatives to the published sample output. Never compare
candidate text to the sample answer, hardcode sample answers, infer a unique answer,
or generate a contestant solution/oracle. If correctness requires an answer oracle
that cannot be validated from input/output constraints, return {"valid":null}.
Do not invent floating tolerances if they are not explicitly specified.
stdin executable contract (UTF-8 bytes): decimal input byte length + newline,
exactly that many input bytes, decimal output byte length + newline, exactly that
many candidate output bytes. Parse both blocks with bounded allocation, validate
ranges, constraints, required structure and trailing tokens.
Print exactly one JSON object: {"valid":true}, {"valid":false}, or {"valid":null}
when unsure. No hidden tests, official solution, admin checker, files, network,
subprocesses or expected-answer lookup. Only stdin/stdout are permitted.
This checker remains llm_generated_unverified, even after reference sanity passes.
"""


def legacy_checker_messages(context_builder, problem):
    messages = context_builder.build(problem=problem, role=AgentRole.CODE)
    # Replace CODE's solution instruction; no candidate solution is supplied.
    content = messages[-1].content.rsplit("\n\n", 1)[0] + "\n\n" + LEGACY_INSTRUCTIONS
    return [messages[0], ChatMessage("user", content)]


def checker_messages(context_builder, problem):
    # The algorithm ContextBuilder remains frozen; only the checker instruction
    # and its own system message change. No contestant candidate is supplied.
    messages = context_builder.build(problem=problem, role=AgentRole.CODE)
    public = messages[-1].content.rsplit("\n\n", 1)[0]
    return [ChatMessage("system", "You write unverified output checkers. Return complete C++20 source only. "
                        "Use only public constraints and the stdin/stdout contract. Never invent judge verdicts."),
            ChatMessage("user", public + "\n\n" + INSTRUCTIONS)]


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
