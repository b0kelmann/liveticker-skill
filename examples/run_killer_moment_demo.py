"""End-to-end killer-moment smoke-test.

Reproduces the demo's 3-minute beat sequence against the real LLM:
playful inputs → tonal shift → crowd-crush trigger → 3-channel fanout.

Run:
    python -m examples.run_killer_moment_demo

This is the canonical "does the system work?" check before any demo-day
rehearsal. Output is structured for visual scan + audit-log replay.
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from skill.server import app
from skill.state import STATE


def hr(label: str) -> None:
    print(f"\n{'=' * 8} {label} {'=' * 8}")


def fire(
    client: TestClient,
    channel: str,
    who: str,
    text: str,
    area: str,
) -> None:
    t0 = time.time()
    body = {"text": text, "stakeholder_id": who, "area": area}
    r = client.post(f"/{channel}", json=body).json()
    dt = time.time() - t0
    extra = ""
    if "answer" in r:
        extra = f"\n           answer: {r['answer'][:120]!r}"
    print(f"  /{channel:<6s} ({dt:5.1f}s) {text!r}{extra}")


def print_inbox(client: TestClient, stakeholder_id: str, label: str) -> None:
    inbox = client.get(f"/inbox/{stakeholder_id}").json()
    msgs = inbox["messages"]
    if not msgs:
        return
    print(f"  {label} ({stakeholder_id}) — {len(msgs)} msg(s):")
    for m in msgs:
        marker = "ALERT" if m.get("kind") == "alert" else "info "
        sev = m.get("severity") or "—"
        risk = m.get("risk_id") or "—"
        print(f"    [{marker}|{sev:8s}|{risk}] {m.get('message')}")


def main() -> None:
    with TestClient(app) as client:
        hr("BOOT")
        cfg = client.get("/config").json()
        snap = client.get("/state").json()
        print(f"event:    {cfg['event']['name']}")
        print(f"scenario: {cfg['event']['scenario']}")
        print(
            f"seeded:   {len(snap['plan'])} plan items, "
            f"{len(snap['risks'])} risks, {len(snap['goals'])} goals"
        )

        hr("JOIN")
        sh: dict[str, str] = {}
        roster = [
            ("fan_a", "fan",        "main_stage"),
            ("fan_b", "fan",        "main_stage"),
            ("fan_c", "fan",        "main_stage"),
            ("sec",   "security",   "main_stage"),
            ("medic", "medic",      "medical_tent"),
            ("tech",  "stage_tech", "main_stage"),
            ("vend",  "vendor",     "food_court"),
        ]
        for label, role, area in roster:
            r = client.post("/join", json={"role": role, "area": area}).json()
            sh[label] = r["id"]
            print(f"  {label:6s} ({role:10s}) -> {r['id']} @ {area}")

        hr("T=0:30 — playful inputs (no risk expected)")
        fire(client, "ask",  sh["fan_a"], "where can I get water near main stage?", "main_stage")
        fire(client, "post", sh["vend"],  "running low on tacos at truck 3",        "food_court")
        fire(client, "ask",  sh["fan_b"], "when does Anna start?",                  "main_stage")

        hr("T=1:00 — tonal shift (1st crowd signal)")
        fire(client, "signal", sh["fan_a"], "crowded near front",                   "main_stage")

        hr("T=1:15 — 2nd crowd signal")
        fire(client, "signal", sh["fan_b"], "getting really tight up here",         "main_stage")

        hr("T=1:30 — KILLER MOMENT (3rd crowd signal)")
        fire(client, "signal", sh["fan_c"], "cant move, pushed forward",            "main_stage")

        hr("FANOUT — who got alerted?")
        for label in ("sec", "medic", "tech", "vend", "fan_a", "fan_b", "fan_c"):
            print_inbox(client, sh[label], label)

        hr("INTERPRETATIONS — agent's read of each signal")
        for sig in STATE.reality.all():
            interp = (sig.interpretation or "—")[:130]
            print(f"  [{sig.channel.value:6s}] {sig.text!r}")
            print(f"           -> {interp}")


if __name__ == "__main__":
    main()
