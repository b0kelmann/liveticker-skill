"""Run the Auto-Broadcaster against the real LLM (GLM-5.1 via RouteTokens).

Connects skill.llm.chat into the broadcaster's pluggable llm slot.
Run from the repo root:

    python -m examples.run_broadcaster_demo
"""
from __future__ import annotations

import time

from skill.llm import chat
from skill.loops.broadcaster import AutoBroadcaster


def real_llm(prompt: str) -> str:
    """Adapter: broadcaster expects (prompt:str) -> str, chat expects messages."""
    return chat([{"role": "user", "content": prompt}])


def main() -> None:
    fake_feed = [
        {
            "id": "a1",
            "ts": int(time.time()),
            "team": "narwhal",
            "category": "milestone",
            "text": "We shipped the agent loop! Demo at 4pm in Zone B.",
            "consent_public": True,
        },
        {
            "id": "a2",
            "ts": int(time.time()),
            "team": "panda",
            "category": "blocker",
            "text": "stuck on websocket auth, anyone done this with R9S?",
            "consent_public": False,  # private, broadcaster ignores
        },
    ]

    bc = AutoBroadcaster(
        fetch_feed=lambda since: fake_feed,
        llm=real_llm,
    )
    bc.tick()


if __name__ == "__main__":
    main()
