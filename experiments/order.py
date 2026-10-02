"""A portable, seeded order: problem blocks with rotating condition positions."""
from __future__ import annotations

from agent.execution import fingerprint

DEFAULT_EXECUTION_SEED = 20261002
EXECUTION_ORDER_VERSION = "problem_blocks_balanced_sha256_v1"


def execution_order(config, seed=None):
    seed = config.execution_seed if seed is None else seed
    seed = DEFAULT_EXECUTION_SEED if seed is None else seed
    if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed < 2**64:
        raise ValueError("Execution seed must be an unsigned 64-bit integer")
    order = []
    count = len(config.strategies)
    for repetition in range(config.repetitions):
        for index, problem in enumerate(config.problems):
            block = repetition * len(config.problems) + index
            cycle, rotation = divmod(block, count)
            # Hash ranking is independent of Python's random implementation.
            ranked = sorted(config.strategies, key=lambda strategy: (
                fingerprint([EXECUTION_ORDER_VERSION, seed, cycle, strategy.name]), strategy.name))
            ranked = ranked[rotation:] + ranked[:rotation]
            for position, strategy in enumerate(ranked):
                order.append({"sequence": len(order) + 1, "problem_id": problem,
                              "condition": strategy.name, "mode": strategy.mode,
                              "repetition": repetition + 1, "position_in_block": position + 1})
    validate_order(config, order)
    return {"version": EXECUTION_ORDER_VERSION, "execution_seed": seed,
            "execution_order": order, "execution_order_hash": fingerprint(order)}


def validate_order(config, order):
    expected = {(problem, strategy.name, repetition + 1)
                for problem in config.problems for strategy in config.strategies
                for repetition in range(config.repetitions)}
    observed = [(item["problem_id"], item["condition"], item["repetition"]) for item in order]
    if len(observed) != len(expected) or set(observed) != expected:
        raise ValueError("Invalid execution ordering: missing or duplicate tasks")
    size = len(config.strategies)
    for index, item in enumerate(order):
        if item["sequence"] != index + 1 or item["position_in_block"] != index % size + 1:
            raise ValueError("Invalid execution ordering: sequence/position")
        if observed[index][::2] != observed[index - index % size][::2]:
            raise ValueError("Invalid execution ordering: problem blocks")
