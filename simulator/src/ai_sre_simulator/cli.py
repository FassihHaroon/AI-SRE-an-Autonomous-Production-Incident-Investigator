"""simctl: command-line control of the SIMULATED production environment.

Examples::

    simctl scenarios                                   # list predefined incidents
    simctl describe orders-n-plus-one-deploy
    simctl trigger orders-n-plus-one-deploy            # real-time timeline
    simctl trigger redis-outage --speed 10             # compress the timeline 10x
    simctl status
    simctl clear                                       # clear every run and fault
    simctl inject api_latency --target api-gateway --param delay_ms=400 --param jitter_ms=100
    simctl remove f-0003
    simctl fault-types
    simctl deployments --service orders-service
    simctl reset                                       # back to baseline (keeps order data)
    simctl reset --data                                # also delete orders created since seed

Talks to the chaos controller (``SIM_CHAOS_API``, default http://127.0.0.1:8210) and the
deployer (``SIM_DEPLOYER_API``, default http://127.0.0.1:8202).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

BANNER = "[SIMULATED ENVIRONMENT] "


def _parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _params(pairs: list[str]) -> dict[str, Any]:
    params: dict[str, Any] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep:
            raise SystemExit(f"--param expects key=value, got {pair!r}")
        params[key] = _parse_value(value)
    return params


def _print(data: Any) -> None:
    print(json.dumps(data, indent=2, default=str))


class Client:
    def __init__(self, chaos: str, deployer: str) -> None:
        self.chaos = httpx.Client(base_url=chaos, timeout=60)
        self.deployer = httpx.Client(base_url=deployer, timeout=30)

    @staticmethod
    def _check(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise SystemExit(f"error {response.status_code}: {detail}")
        return response.json()

    def get(self, path: str) -> Any:
        return self._check(self.chaos.get(path))

    def post(self, path: str, body: Any = None) -> Any:
        return self._check(self.chaos.post(path, json=body))

    def delete(self, path: str) -> Any:
        return self._check(self.chaos.delete(path))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="simctl", description="Control the SIMULATED production environment."
    )
    parser.add_argument(
        "--chaos-url", default=os.environ.get("SIM_CHAOS_API", "http://127.0.0.1:8210")
    )
    parser.add_argument(
        "--deployer-url", default=os.environ.get("SIM_DEPLOYER_API", "http://127.0.0.1:8202")
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scenarios", help="list predefined incident scenarios")
    p = sub.add_parser("describe", help="show a scenario's timeline")
    p.add_argument("scenario")
    p = sub.add_parser("trigger", help="start a scenario")
    p.add_argument("scenario")
    p.add_argument("--speed", type=float, default=1.0, help="timeline compression factor")
    p = sub.add_parser("clear", help="clear a scenario (or everything when omitted)")
    p.add_argument("scenario", nargs="?")
    sub.add_parser("status", help="active scenario runs and faults")
    sub.add_parser("fault-types", help="list injectable fault types and their parameters")
    p = sub.add_parser("inject", help="inject a single fault")
    p.add_argument("type")
    p.add_argument("--target", required=True)
    p.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--seed", type=int)
    p = sub.add_parser("remove", help="remove one fault by id")
    p.add_argument("fault_id")
    p = sub.add_parser("reset", help="restore the baseline environment")
    p.add_argument("--data", action="store_true", help="also delete orders created since seed")
    p = sub.add_parser("deployments", help="deployment history (as the CD tool shows it)")
    p.add_argument("--service")
    p.add_argument("--limit", type=int, default=20)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    client = Client(args.chaos_url, args.deployer_url)
    print(BANNER + args.command, file=sys.stderr)
    try:
        run(args, client)
    except httpx.HTTPError as exc:
        raise SystemExit(
            f"cannot reach the simulator ({exc}). Is it running? "
            "docker compose -f docker-compose.sim.yml up -d --wait"
        ) from exc


def run(args: argparse.Namespace, client: Client) -> None:
    cmd = args.command
    if cmd == "scenarios":
        for s in client.get("/api/v1/scenarios"):
            print(f"{s['id']:<32} {s['category']:<22} {s['title']}")
    elif cmd == "describe":
        _print(client.get(f"/api/v1/scenarios/{args.scenario}"))
    elif cmd == "trigger":
        run = client.post(f"/api/v1/scenarios/{args.scenario}/trigger", {"speed": args.speed})
        print(f"started {run['id']} ({run['scenario_id']}, speed x{run['speed']})")
        for step in run["steps"]:
            print(f"  t+{step['at_s'] / args.speed:>6.1f}s  {step['description']}")
    elif cmd == "clear":
        if args.scenario:
            _print(client.post(f"/api/v1/scenarios/{args.scenario}/clear"))
        else:
            _print(client.post("/api/v1/clear"))
    elif cmd == "status":
        _print(client.get("/api/v1/status"))
    elif cmd == "fault-types":
        for t in client.get("/api/v1/fault-types"):
            props = t["params"].get("properties", {})
            described = ", ".join(f"{k}={v.get('default', '<required>')}" for k, v in props.items())
            print(f"{t['type']:<20} {t['mechanism']:<11} targets={','.join(t['targets'])}")
            print(f"{'':<20} {t['description']}  params: {described}")
    elif cmd == "inject":
        body: dict[str, Any] = {
            "type": args.type,
            "target": args.target,
            "params": _params(args.param),
        }
        if args.seed is not None:
            body["seed"] = args.seed
        _print(client.post("/api/v1/faults", body))
    elif cmd == "remove":
        _print(client.delete(f"/api/v1/faults/{args.fault_id}"))
    elif cmd == "reset":
        _print(client.post("/api/v1/reset", {"data": args.data}))
    elif cmd == "deployments":
        query: dict[str, Any] = {"limit": args.limit}
        if args.service:
            query["service"] = args.service
        rows = Client._check(client.deployer.get("/api/v1/deployments", params=query))
        for d in rows:
            print(
                f"{d['id']:<7} {d['started_at'][:19]}  {d['service']:<18} {d['kind']:<13} "
                f"{(d['previous_version'] or '-'):>6} -> {d['version']:<7} {d['status']:<11} "
                f"{d['changes_summary']}"
            )


if __name__ == "__main__":
    main()
