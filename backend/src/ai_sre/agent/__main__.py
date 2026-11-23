"""AI SRE investigation CLI.

    python -m ai_sre.agent investigate "Why are orders failing in the last 20 minutes?"
    python -m ai_sre.agent approve <investigation-id> --approver alice [--reject]
    python -m ai_sre.agent cancel <investigation-id>
    python -m ai_sre.agent show <investigation-id>
    python -m ai_sre.agent graph                    # Mermaid diagram of the workflow

Needs the MCP servers (``uv run poe mcp``) and, for persistence, the platform Postgres
(``docker compose up -d postgres``). Options: --reasoner rules|llm, --memory (no persistence).
Approving uses the MCP admin token from the environment: it is the human's credential.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

import httpx

from ai_sre.agent.models import Decision, Limits
from ai_sre.agent.runner import RunResult, investigator, make_reasoner
from ai_sre.config import get_settings
from ai_sre.mcp_servers.settings import get_mcp_settings
from ai_sre.observability.logging import configure_logging


def _dsn() -> str:
    return get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://")


def _print(result: RunResult) -> None:
    if result.status == "awaiting_approval" and result.approval:
        print(f"\n=== {result.investigation_id}: AWAITING HUMAN APPROVAL ===")
        rc = result.approval.get("root_cause") or {}
        print(f"Root cause: {rc.get('title')} ({rc.get('band')}, {rc.get('confidence')})")
        for p in result.approval["proposals"]:
            print(
                f"  [{p['id']}] {p['description']} risk={p['risk']} reversible={p['reversible']} "
                f"approval_request={p['approval_request_id']}"
            )
        print(
            f"\nApprove:  python -m ai_sre.agent approve {result.investigation_id} --approver <your-name>"
        )
        print(
            f"Reject:   python -m ai_sre.agent approve {result.investigation_id} --approver <your-name> --reject"
        )
    elif result.report:
        print(result.report.markdown)
    else:
        print(json.dumps(result.model_dump(mode="json"), indent=2))


async def _run(args: argparse.Namespace) -> int:
    mcp = get_mcp_settings()
    url = os.environ.get("AI_SRE_MCP_URL", f"http://127.0.0.1:{mcp.port}")
    kwargs: dict[str, Any] = {
        "mcp_url": url,
        "read_token": mcp.read_token.get_secret_value(),
        "remediation_token": mcp.remediation_token.get_secret_value(),
        "reasoner": make_reasoner(args.reasoner),
        "postgres_dsn": None if args.memory else _dsn(),
    }
    async with investigator(**kwargs) as inv:
        if args.cmd == "investigate":
            limits = Limits(
                max_rounds=args.max_rounds,
                max_tool_calls=args.max_tool_calls,
                verification_wait_s=args.verify_wait,
            )
            _print(await inv.start(args.question, limits=limits))
        elif args.cmd == "approve":
            state = await inv.result(args.investigation_id)
            if state.status != "awaiting_approval" or not state.approval:
                print(f"{args.investigation_id} is not waiting for approval ({state.status})")
                return 1
            decisions = []
            admin = {"Authorization": f"Bearer {mcp.admin_token.get_secret_value()}"}
            for p in state.approval["proposals"]:
                if args.reject:
                    decisions.append(
                        Decision(proposal_id=p["id"], approved=False, approver=args.approver)
                    )
                    continue
                async with httpx.AsyncClient(timeout=30) as http:
                    r = await http.post(
                        f"{url}/approvals/{p['approval_request_id']}/approve",
                        json={"approver": args.approver},
                        headers=admin,
                        timeout=30,
                    )
                if r.status_code != 200:
                    print(f"approval of {p['id']} refused by the MCP server: {r.text}")
                    return 1
                decisions.append(
                    Decision(
                        proposal_id=p["id"],
                        approved=True,
                        approver=args.approver,
                        approval_token=r.json()["approval_token"],
                    )
                )
            _print(await inv.resume(args.investigation_id, decisions))
        elif args.cmd == "cancel":
            await inv.cancel(args.investigation_id)
            print(f"cancellation requested for {args.investigation_id}")
        elif args.cmd == "show":
            _print(await inv.result(args.investigation_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_sre.agent")
    parser.add_argument(
        "--reasoner",
        choices=["rules", "llm"],
        default=os.environ.get("AI_SRE_AGENT_REASONER", "rules"),
    )
    parser.add_argument(
        "--memory", action="store_true", help="in-memory checkpoints (no resume across processes)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("investigate")
    p.add_argument("question")
    p.add_argument("--max-rounds", type=int, default=3)
    p.add_argument("--max-tool-calls", type=int, default=24)
    p.add_argument(
        "--verify-wait", type=float, default=45.0, help="seconds to wait before verifying a fix"
    )
    p = sub.add_parser("approve")
    p.add_argument("investigation_id")
    p.add_argument("--approver", required=True)
    p.add_argument("--reject", action="store_true")
    for name in ("cancel", "show"):
        sub.add_parser(name).add_argument("investigation_id")
    sub.add_parser("graph")
    args = parser.parse_args(argv)

    if args.cmd == "graph":
        from ai_sre.agent.gateway import ToolGateway, inprocess_client_factory
        from ai_sre.agent.graph import Deps, build_graph
        from ai_sre.agent.reasoner import RuleBasedReasoner

        graph = build_graph(
            Deps(ToolGateway(inprocess_client_factory({})), RuleBasedReasoner())
        ).compile()
        print(graph.get_graph().draw_mermaid())
        return 0
    configure_logging("WARNING", "console")
    if os.name == "nt":  # psycopg's async mode cannot use the Proactor loop
        return asyncio.run(_run(args), loop_factory=asyncio.SelectorEventLoop)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
