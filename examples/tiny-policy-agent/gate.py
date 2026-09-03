from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


PROTECTED_CASES = [
    ("Where is order 42?", "status"),
    ("Please refund my damaged mug.", "refund"),
    ("Cancel order 42.", "cancel"),
]


def load_agent(path: Path):
    spec = importlib.util.spec_from_file_location("tiny_policy_gate_agent", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load agent from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent", required=True)
    args = parser.parse_args()
    module = load_agent(Path(args.agent))
    for request, expected in PROTECTED_CASES:
        if module.route(request) != expected:
            sys.exit(1)


if __name__ == "__main__":
    main()
