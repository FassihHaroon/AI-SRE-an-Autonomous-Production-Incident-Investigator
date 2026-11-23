"""Serve the API: ``python -m ai_sre.api [--host 127.0.0.1] [--port 8010]``.

On Windows it uses a selector event loop, which psycopg's async mode (the PostgreSQL graph
checkpointer) requires; uvicorn's default there is the Proactor loop.
"""

from __future__ import annotations

import argparse
import asyncio
import os

import uvicorn


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m ai_sre.api")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    config = uvicorn.Config(
        "ai_sre.main:app",
        host=args.host,
        port=args.port,
        loop="none",
        log_config=None,
        access_log=False,
    )
    server = uvicorn.Server(config)
    if os.name == "nt":
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
