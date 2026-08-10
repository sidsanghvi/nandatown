# SPDX-License-Identifier: Apache-2.0
"""Sweep seeds for the byzantine supply-chain scenario.

With 4 agents and ``byzantine_agents: 0.25``, exactly one agent is corrupted
per run, and which one it is depends on the seed. This script runs the same
scenario across many seeds and records, for each run:

* which agent was selected as byzantine,
* how many received messages arrived as unparseable garbage,
* whether the ``supply_chain`` validators still reported a clean run.

The headline result is that when the byzantine agent is the terminal hop
(``retailer-0``), every validator passes despite 15 corrupted messages.

Usage::

    pip install "nest-core[plugins]"
    python seed_sweep.py [n_seeds]
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import yaml
from nest_core.runner import ScenarioRunner
from nest_core.scenario import ScenarioConfig
from nest_core.sim.simulator import Simulator
from nest_core.validators import validate_trace

HERE = Path(__file__).parent
SCENARIO = HERE / "supply_chain_byzantine.yaml"
VALID_PREFIXES = ("material:", "product:", "shipment:", "delivered:")

# The simulator does not expose which agents it marked byzantine, so wrap
# _init_failures to capture the selection after it runs.
_selected: list[str] = []
_original_init_failures = Simulator._init_failures  # noqa: SLF001


def _capture_init_failures(self: Simulator) -> None:
    _original_init_failures(self)
    _selected.clear()
    _selected.extend(sorted(str(a) for a in self._byzantine_agents))  # noqa: SLF001


Simulator._init_failures = _capture_init_failures  # type: ignore[method-assign]  # noqa: SLF001


def run_one(seed: int, trace_path: Path) -> tuple[str, int, int, bool, str]:
    """Run at one seed -> (byzantine_agent, receives, corrupted, all_passed, detail)."""
    raw: dict[str, Any] = yaml.safe_load(SCENARIO.read_text())
    raw["seed"] = seed
    config = ScenarioConfig(**raw)
    config.output.trace = str(trace_path)
    asyncio.run(ScenarioRunner(config).run())

    events = [json.loads(line) for line in trace_path.read_text().splitlines() if line]
    receives = [e for e in events if e.get("kind") == "receive"]
    corrupted = sum(1 for e in receives if not str(e.get("msg", "")).startswith(VALID_PREFIXES))

    results = list(validate_trace(trace_path, "supply_chain"))
    all_passed = all(r.passed for r in results)
    no_lost = next(r for r in results if r.name == "supply_chain_no_lost")
    return _selected[0], len(receives), corrupted, all_passed, no_lost.detail


def main() -> None:
    n_seeds = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    trace_path = HERE / "_sweep_tmp.jsonl"
    rows: list[tuple[int, str, int, int, bool, str]] = []

    for seed in range(1, n_seeds + 1):
        agent, receives, corrupted, passed, detail = run_one(seed, trace_path)
        rows.append((seed, agent, receives, corrupted, passed, detail))

    trace_path.unlink(missing_ok=True)

    header = (
        f"{'seed':>4}  {'byzantine agent':<16}{'recv':>5}{'corrupt':>9}  {'validators':<9}  detail"
    )
    print(header)
    print("-" * len(header))
    for seed, agent, receives, corrupted, passed, detail in rows:
        verdict = "ALL PASS" if passed else "FAIL"
        print(f"{seed:>4}  {agent:<16}{receives:>5}{corrupted:>9}  {verdict:<9}  {detail}")

    invisible = sum(1 for r in rows if r[4])
    print()
    print("byzantine agent selected:", dict(Counter(r[1] for r in rows)))
    print(f"runs with corrupted messages arriving : {sum(1 for r in rows if r[3] > 0)}/{len(rows)}")
    print(f"runs where validators still ALL PASS  : {invisible}/{len(rows)}")


if __name__ == "__main__":
    main()
