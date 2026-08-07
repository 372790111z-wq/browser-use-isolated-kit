#!/usr/bin/env python3
"""Keep Chrome DevTools MCP page selection in the background by default."""

from __future__ import annotations

import json
import subprocess
import sys
import threading


def protect_request(line: bytes) -> bytes:
    """Rewrite focus-capable MCP tool calls without changing other traffic."""
    try:
        message = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return line
    if message.get("method") != "tools/call":
        return line
    params = message.get("params")
    if not isinstance(params, dict):
        return line
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
        params["arguments"] = arguments
    name = params.get("name")
    if name == "new_page":
        # Upstream defaults to foreground. An autonomous isolated task cannot
        # override this safety default.
        arguments["background"] = True
        arguments.pop("isolatedContext", None)
    elif name == "select_page":
        # Explicit presentation uses Browser Use's gated show_tab() helper.
        arguments["bringToFront"] = False
    return json.dumps(message, separators=(",", ":")).encode() + b"\n"


def relay(source, destination, *, protect: bool = False) -> None:
    for line in iter(source.readline, b""):
        destination.write(protect_request(line) if protect else line)
        destination.flush()


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: mcp-stdio-guard.py COMMAND [ARG ...]", file=sys.stderr)
        return 2
    child = subprocess.Popen(
        sys.argv[1:],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert child.stdin is not None
    assert child.stdout is not None
    assert child.stderr is not None
    threading.Thread(
        target=relay, args=(child.stdout, sys.stdout.buffer), daemon=True
    ).start()
    threading.Thread(
        target=relay, args=(child.stderr, sys.stderr.buffer), daemon=True
    ).start()
    try:
        relay(sys.stdin.buffer, child.stdin, protect=True)
    finally:
        child.stdin.close()
    return child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
