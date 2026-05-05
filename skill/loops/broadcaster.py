"""
Auto-Broadcaster — reference agent loop for LiveTicker.

This is a thin reference implementation of the outward-facing loop. It runs
periodically, pulls new feed items, asks an LLM which (if any) deserves to
go public, drafts the post, runs a self-check, and emits via the configured
output skill.

The LLM and output skills are pluggable — the defaults here are stubs so the
file runs without external services. In a real OpenClaw deployment, the
agent runtime injects its own LLM and skill registry.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

AUDIT = Path("audit.log")


# ---------- pluggable interfaces -----------------------------------------

LLMFn = Callable[[str], str]            # prompt -> completion
EmitFn = Callable[[dict], None]         # post payload -> side effect


def stub_llm(prompt: str) -> str:
    """Default LLM stub — replace with your agent's LLM call."""
    return json.dumps(
        {
            "newsworthy": True,
            "score": 0.72,
            "reason": "stub: pretend the model picked the first item",
            "draft": "Team Narwhal just shipped their agent loop! 🚀 #GOSIMParis",
            "alt_text": "Screenshot of a working agent loop.",
        }
    )


def stub_emit(payload: dict) -> None:
    """Default emit — just print. Real impl publishes to social / page / mail."""
    print("[broadcast]", json.dumps(payload, indent=2))


# ---------- config --------------------------------------------------------


@dataclass
class Voice:
    tone: str = "warm, energetic, technical"
    hashtags: list[str] = field(default_factory=lambda: ["#GOSIMParis"])
    blocked_words: list[str] = field(default_factory=list)


@dataclass
class Budget:
    posts_per_hour: int = 4
    posts_per_team_per_hour: int = 1


# ---------- the loop ------------------------------------------------------


@dataclass
class AutoBroadcaster:
    fetch_feed: Callable[[int], list[dict]]   # since_ts -> items
    llm: LLMFn = stub_llm
    emit: EmitFn = stub_emit
    voice: Voice = field(default_factory=Voice)
    budget: Budget = field(default_factory=Budget)
    interval_seconds: int = 300

    _last_seen_ts: int = 0
    _recent_posts: list[dict] = field(default_factory=list)

    # ----- main entry point -----
    def tick(self) -> None:
        items = self.fetch_feed(self._last_seen_ts)
        if not items:
            return

        # Only consider items the author marked public.
        public = [i for i in items if i.get("consent_public", False)]
        if not public:
            self._last_seen_ts = max(i["ts"] for i in items)
            return

        decision = self._decide(public)
        if decision and decision.get("newsworthy"):
            if self._within_budget(decision):
                self.emit(decision)
                self._recent_posts.append({"ts": int(time.time()), **decision})
                self._audit("emitted", decision)
            else:
                self._audit("budget_blocked", decision)

        self._last_seen_ts = max(i["ts"] for i in items)

    # ----- the LLM-backed decision step -----
    def _decide(self, items: list[dict]) -> dict | None:
        prompt = self._build_prompt(items)
        try:
            raw = self.llm(prompt)
            parsed = json.loads(raw)
            return parsed
        except (ValueError, json.JSONDecodeError) as e:
            self._audit("llm_error", {"error": str(e)})
            return None

    def _build_prompt(self, items: list[dict]) -> str:
        recent = "\n".join(
            f"- {p.get('draft','')[:120]}" for p in self._recent_posts[-5:]
        ) or "(none yet)"
        feed = "\n".join(
            f"#{i['id']} [{i.get('category','-')}] {i.get('team','-')}: "
            f"{i.get('text','')[:200]}"
            for i in items
        )
        return f"""You are the Auto-Broadcaster for a live event.

Voice: {self.voice.tone}
Hashtags to consider: {', '.join(self.voice.hashtags)}
Avoid these words: {', '.join(self.voice.blocked_words) or '(none)'}

Recently published (do not duplicate):
{recent}

New feed items since last run:
{feed}

Pick the single most newsworthy item to broadcast publicly, or none.
Respond as JSON:
{{
  "newsworthy": bool,
  "score": float between 0 and 1,
  "reason": short string,
  "draft": the public post copy,
  "alt_text": media alt text or null,
  "team": team name if applicable
}}
"""

    # ----- guardrails -----
    def _within_budget(self, decision: dict) -> bool:
        cutoff = int(time.time()) - 3600
        recent = [p for p in self._recent_posts if p["ts"] >= cutoff]
        if len(recent) >= self.budget.posts_per_hour:
            return False
        team = decision.get("team")
        if team:
            same_team = [p for p in recent if p.get("team") == team]
            if len(same_team) >= self.budget.posts_per_team_per_hour:
                return False
        return True

    # ----- audit -----
    def _audit(self, event: str, payload: dict) -> None:
        line = json.dumps({"ts": int(time.time()), "event": event, **payload})
        AUDIT.open("a").write(line + "\n")


# ---------- demo runner ---------------------------------------------------

if __name__ == "__main__":
    # tiny in-memory demo feed
    fake_items = [
        {
            "id": "a1",
            "ts": int(time.time()),
            "team": "narwhal",
            "category": "milestone",
            "text": "we shipped the agent loop, demo at 4pm",
            "consent_public": True,
        }
    ]

    bc = AutoBroadcaster(fetch_feed=lambda since: fake_items)
    bc.tick()
