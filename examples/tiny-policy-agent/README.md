# Tiny policy agent

This fixture has a deterministic five-item benchmark and a three-item
regression gate.

The starting router scores `0.6`. It mishandles:

- cancellation without an order number, which should ask for clarification;
- social-engineering language around refunds, which should be denied.

The target is `agent.py`. `benchmark.py` and `gate.py` are protected during Olo
experiments.

## Run the fixture

From the repository root:

```powershell
python examples\tiny-policy-agent\benchmark.py --agent examples\tiny-policy-agent\agent.py
python examples\tiny-policy-agent\gate.py --agent examples\tiny-policy-agent\agent.py
```

Use `/` separators and your Python 3 launcher on macOS/Linux. The benchmark
prints a JSON aggregate and task scores; the gate exits zero when its three
regression cases pass. These commands do not initialize an Olo study.

For the agent-driven demonstration, follow the
[quickstart](../../README.md#explore-first). Exploration establishes and
independently approves the baseline before a bounded optimization round.
The winner stays on its experiment branch; no merge happens automatically.

Five visible requests are deliberately small enough to understand. A perfect
score on this fixture does not establish general routing quality or policy
safety. A real project needs representative cases, hard counterexamples,
regression gates, and a fresh final assessment.
