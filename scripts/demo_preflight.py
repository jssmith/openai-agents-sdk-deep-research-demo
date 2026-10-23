"""Preflight checks for scripts/start-demo.

Resolves configuration the same way the worker and backend do (env +
``--env-file .env`` + Temporal envconfig profiles), so the launcher checks
exactly what the processes will use.

Usage:
    uv run --env-file .env python scripts/demo_preflight.py check
    uv run --env-file .env python scripts/demo_preflight.py wait-temporal --timeout 30

``check`` validates the demo settings and the OpenAI key, then prints shell
assignments for the launcher to ``eval``. ``wait-temporal`` exits 0 once the
configured Temporal server accepts a client connection.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import sys
import time
from typing import Any

from temporalio.client import Client
from temporalio.envconfig import ClientConfig

from openai_agents.demo_config import load_demo_settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _temporal_config() -> dict[str, Any]:
    config = ClientConfig.load_client_connect_config()
    config.setdefault("target_host", "localhost:7233")
    config.setdefault("namespace", "default")
    return config


async def _temporal_up(config: dict[str, Any]) -> bool:
    try:
        await asyncio.wait_for(Client.connect(**config), timeout=5)
    except (RuntimeError, asyncio.TimeoutError):
        # Connection refused / unreachable surfaces as RuntimeError.
        return False
    return True


def _check() -> int:
    try:
        settings = load_demo_settings()
    except ValueError as exc:
        print(f"Invalid demo configuration: {exc}", file=sys.stderr)
        return 2

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or api_key == "your-openai-api-key":
        print(
            "OPENAI_API_KEY is not set (in .env or the environment).",
            file=sys.stderr,
        )
        return 2

    config = _temporal_config()
    target = config["target_host"]
    host, _, port = target.rpartition(":")
    host = host.strip("[]")
    if not host or not port.isdigit():
        print(f"Temporal address {target!r} must be host:port", file=sys.stderr)
        return 2

    values = {
        "TEMPORAL_TARGET": target,
        "TEMPORAL_HOST": host,
        "TEMPORAL_PORT": port,
        "TEMPORAL_NAMESPACE": config["namespace"],
        "TEMPORAL_IS_LOCAL": "1" if host in LOCAL_HOSTS else "0",
        "TEMPORAL_UP": "1" if asyncio.run(_temporal_up(config)) else "0",
        "LOCAL_MODEL": settings.model_local,
        "LOCAL_BASE_URL": settings.model_local_base_url,
        "WAREHOUSE_FAILURES": str(settings.data_warehouse_retry_failures),
    }
    for name, value in values.items():
        print(f"PREFLIGHT_{name}={shlex.quote(value)}")
    return 0


def _wait_temporal(timeout: float) -> int:
    config = _temporal_config()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if asyncio.run(_temporal_up(config)):
            return 0
        time.sleep(1)
    print(
        f"Temporal at {config['target_host']} did not come up within {timeout:.0f}s",
        file=sys.stderr,
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    wait = sub.add_parser("wait-temporal")
    wait.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()

    if args.cmd == "check":
        return _check()
    return _wait_temporal(args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
