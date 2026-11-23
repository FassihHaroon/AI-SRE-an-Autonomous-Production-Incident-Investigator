"""CLI for the MCP servers.

    python -m ai_sre.mcp_servers serve [--host 127.0.0.1] [--port 8300]
    python -m ai_sre.mcp_servers registry [--schemas]           # print the tool registry
    python -m ai_sre.mcp_servers approvals                      # list approval requests
    python -m ai_sre.mcp_servers approve <request-id> --approver <name>
    python -m ai_sre.mcp_servers reject  <request-id> --approver <name>

The client commands talk to a running server (``AI_SRE_MCP_URL``, default
http://127.0.0.1:8300) with the tokens from the environment / .env.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import httpx
import uvicorn

from ai_sre.mcp_servers.settings import get_mcp_settings
from ai_sre.observability.logging import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_sre.mcp_servers")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    reg = sub.add_parser("registry")
    reg.add_argument("--schemas", action="store_true")
    sub.add_parser("approvals")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("request_id")
        p.add_argument("--approver", required=True, help="your name (recorded on the approval)")
    args = parser.parse_args(argv)
    settings = get_mcp_settings()

    if args.cmd == "serve":
        from ai_sre.config import get_settings
        from ai_sre.mcp_servers.app import create_app

        core = get_settings()
        configure_logging(core.log_level, core.effective_log_format)
        uvicorn.run(
            create_app(settings).asgi,
            host=args.host or settings.host,
            port=args.port or settings.port,
            log_config=None,
            access_log=False,
        )
        return 0

    base = os.environ.get("AI_SRE_MCP_URL", f"http://127.0.0.1:{settings.port}")
    if args.cmd == "registry":
        token = settings.read_token.get_secret_value()
        r = httpx.get(
            f"{base}/registry",
            params={"schemas": str(args.schemas).lower()},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    else:
        headers = {"Authorization": f"Bearer {settings.admin_token.get_secret_value()}"}
        if args.cmd == "approvals":
            r = httpx.get(f"{base}/approvals", headers=headers, timeout=30)
        else:
            r = httpx.post(
                f"{base}/approvals/{args.request_id}/{args.cmd}",
                json={"approver": args.approver},
                headers=headers,
                timeout=30,
            )
    print(json.dumps(r.json(), indent=2))
    return 0 if r.status_code < 400 else 1


if __name__ == "__main__":
    sys.exit(main())
