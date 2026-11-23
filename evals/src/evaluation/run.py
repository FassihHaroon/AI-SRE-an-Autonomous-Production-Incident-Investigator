"""``python -m evaluation.run --dataset evals/datasets/incidents.json`` (see docs/evaluation.md)."""

import sys

from ai_sre_evals.cli import main

if __name__ == "__main__":
    sys.exit(main())
