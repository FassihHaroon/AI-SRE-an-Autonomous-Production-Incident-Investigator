"""Reset the SIMULATED production environment.

    python scripts/sim_reset.py            # soft: clear all scenarios/faults, restore baseline
                                           # deployments, drain queues, new determinism epoch
    python scripts/sim_reset.py --data     # soft + delete orders created since the seed
    python scripts/sim_reset.py --hard     # recreate every container and volume from scratch

Also available as `uv run poe sim-reset` / `uv run poe sim-reset-hard`.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

COMPOSE = ["docker", "compose", "-f", "docker-compose.sim.yml"]
CHAOS = os.environ.get("SIM_CHAOS_API", "http://127.0.0.1:8210")


def soft_reset(data: bool) -> int:
    body = json.dumps({"data": data}).encode()
    request = urllib.request.Request(  # noqa: S310 - fixed local http URL
        f"{CHAOS}/api/v1/reset",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            print(json.dumps(json.load(response), indent=2))
    except urllib.error.URLError as exc:
        print(f"cannot reach the chaos controller at {CHAOS}: {exc}", file=sys.stderr)
        print("start the environment: uv run poe sim-up  (or use --hard)", file=sys.stderr)
        return 1
    return 0


def hard_reset() -> int:
    print("[SIMULATED ENVIRONMENT] removing containers and volumes ...")
    subprocess.run([*COMPOSE, "down", "-v", "--remove-orphans"], check=True)  # noqa: S603
    print("[SIMULATED ENVIRONMENT] rebuilding and starting ...")
    subprocess.run([*COMPOSE, "up", "-d", "--build", "--wait"], check=True)  # noqa: S603
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Reset the simulated environment.")
    parser.add_argument("--data", action="store_true", help="also delete orders created since seed")
    parser.add_argument("--hard", action="store_true", help="recreate containers and volumes")
    args = parser.parse_args()
    return hard_reset() if args.hard else soft_reset(args.data)


if __name__ == "__main__":
    raise SystemExit(main())
