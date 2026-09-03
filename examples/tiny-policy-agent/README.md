# Tiny policy agent

This fixture has a deterministic five-item benchmark and a three-item
regression gate.

The starting router scores `0.6`. It mishandles:

- cancellation without an order number, which should ask for clarification;
- social-engineering language around refunds, which should be denied.

The target is `agent.py`. `benchmark.py` and `gate.py` are protected during Olo
experiments.
