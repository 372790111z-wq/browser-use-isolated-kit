#!/usr/bin/env python3
"""Create the first isolated Chrome target without activating its window."""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request

import websockets


async def create_background_target(base_url: str, page_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/json/version", timeout=2) as response:
        websocket_url = json.load(response)["webSocketDebuggerUrl"]
    async with websockets.connect(websocket_url) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Target.createTarget",
                    "params": {"url": page_url, "background": True},
                }
            )
        )
        while True:
            message = json.loads(await websocket.recv())
            if message.get("id") != 1:
                continue
            if "error" in message:
                raise RuntimeError(message["error"])
            print(message["result"]["targetId"])
            return


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: background-target.py CDP_URL PAGE_URL")
    asyncio.run(create_background_target(sys.argv[1], sys.argv[2]))


if __name__ == "__main__":
    main()
