"""LiveTicker reasoning loop — diff Reality vs Plan, score against Risks.

On each new Signal, the agent:
1. Looks at STATE.snapshot() + the new signal.
2. Asks the LLM (one structured JSON call) for: interpretation, which Risk
   (if any) just crossed threshold, fanout targets, and plan-status updates.
3. Records interpretation on the signal, fires deliver_to_role for each
   fanout target, and updates plan items if requested.
4. Audits every step.

For /ask, a separate function returns a natural-language answer grounded in
the same state, but the question is also recorded as a Signal — so questions
about reality double as sensors (decision #6).

Architecture decision #4: making each store explicit (vs. one big context)
keeps the reasoning explainable. The LLM does the diff, but the structure
makes it not-a-black-box for the jury.
"""
from __future__ import annotations

import json
from typing import Any

from skill.llm import chat
from skill.state import (
    STATE,
    PlanItemStatus,
    Role,
    Signal,
    audit,
)


# Per-stakeholder outbox. react() writes here via deliver_to_role();
# /inbox/{id} reads. HTMX frontend short-polls; SSE optional in Day 2.
_OUTBOX: dict[str, list[dict[str, Any]]] = {}

# In-flight LLM-call counter (for UI "thinking..." indicator)
_INFLIGHT: int = 0

# Risks that have fired at least once (for UI highlighting)
_TRIGGERED_RISK_IDS: set[str] = set()

# Wishes the LLM has flagged as at-risk at least once (for UI highlighting)
_AT_RISK_WISH_IDS: set[str] = set()


def is_thinking() -> int:
    return _INFLIGHT


def triggered_risk_ids() -> set[str]:
    return set(_TRIGGERED_RISK_IDS)


def at_risk_wish_ids() -> set[str]:
    return set(_AT_RISK_WISH_IDS)


def reset_runtime() -> None:
    """Wipe outbox + triggered risks + at-risk wishes. For 'reset' UI button."""
    _OUTBOX.clear()
    _TRIGGERED_RISK_IDS.clear()
    _AT_RISK_WISH_IDS.clear()


# ---------- Outbox primitives ----------

def deliver(stakeholder_id: str, message: dict[str, Any]) -> None:
    _OUTBOX.setdefault(stakeholder_id, []).append(message)


def deliver_to_role(role: Role, message: dict[str, Any]) -> int:
    n = 0
    for s in STATE.stakeholders.list(role=role):
        deliver(s.id, message)
        n += 1
    return n


def inbox_for(stakeholder_id: str) -> list[dict[str, Any]]:
    return _OUTBOX.get(stakeholder_id, [])


# ---------- Prompts ----------

_REACT_SYSTEM = """\
You are LiveTicker, a coordination agent for a live event. On each new
signal from the field, you reason along TWO axes in parallel:

  AXIS 1 (defensive) — Did this signal just push a known Risk over its
  threshold? Risks are patterns to avoid (crowd-crush, mic-failure, etc).

  AXIS 2 (outcome-positive) — Did this signal put any stakeholder Wish at
  risk? Wishes are what stakeholders want from the event ("headliner doesn't
  run too late so I can catch the last train", "AV ready before walk-on").
  A schedule change, a delay, a quality drop can violate a wish even when no
  risk-threshold is crossed.

Then decide which stakeholder roles to fanout to with what message — fanout
messages should reference the affected wish or risk concretely so recipients
understand *why* they're being contacted.

You receive (as JSON in the user message):
- plan: scheduled events with status
- reality_recent: signals from the last few minutes
- risks: known patterns to watch for, each with a natural-language threshold
- goals: organizer success criteria
- wishes: per-role stakeholder wishes (each has id, text, holder_roles)
- stakeholder_counts: how many of each role are present
- new_signal: the signal that just arrived

Return a single JSON object — NO prose, NO markdown fences — with this shape:

{
  "interpretation": "<one short sentence: what this signal means in context>",
  "risk_triggered": "<risk id from the catalog, or null>",
  "severity": "low|medium|high|critical|null",
  "wishes_at_risk": [
    {"wish_id": "<id from wishes[]>", "explanation": "<one short sentence: why this wish is now at risk>"}
  ],
  "fanout": [
    {"role": "<role>", "message": "<short imperative>"}
  ],
  "plan_updates": [
    {"plan_id": "<id from plan[]>", "new_status": "planned|in_progress|done|delayed|cancelled", "notes": "<short>"}
  ]
}

Rules:
- Trigger a risk only when its threshold is plausibly met by reality_recent
  (count similar signals in the same area within the relevant window).
- Add a wish to wishes_at_risk only if this signal *concretely* threatens it,
  not speculatively. Empty list is the right answer for routine signals.
- A fanout entry can be motivated by a risk OR a wish (or both). Mention the
  cause in the message so the recipient understands why.
- For purely routine signals, return empty wishes_at_risk, empty fanout,
  empty plan_updates.
- Fanout messages are short imperatives ("Check front of main stage", not
  "We have detected a possible crowd-crush situation that requires...").
- Roles must be one of: fan, artist, stage_tech, security, medic, vendor, organizer.
- Never fabricate plan ids or wish ids; only reference ids present in the input.
"""


_ASK_SYSTEM = """\
You are LiveTicker, a coordination agent. Answer the user's question using
ONLY the state given (plan, reality_recent, risks, goals). If the answer is
not in the state, say so plainly.

The question itself is also a sensor: capture in `interpretation` what it
implies about reality (e.g. "fan unsure about set time" hints at unclear
schedule communication).

Return a single JSON object — NO prose, NO markdown fences — with this shape:

{
  "answer": "<concise natural-language answer>",
  "interpretation": "<one short sentence: what this question implies about reality>"
}
"""


def _build_user_payload(signal: Signal) -> str:
    snap = STATE.snapshot()
    snap["new_signal"] = signal.model_dump()
    return json.dumps(snap, default=str)


# ---------- Reasoning entry points ----------

def react(signal: Signal) -> dict[str, Any]:
    """Process a new Signal: interpret + maybe trigger risk + fanout."""
    global _INFLIGHT
    audit("react_start", signal_id=signal.id, channel=signal.channel.value)

    _INFLIGHT += 1
    try:
        try:
            raw = chat(
                [
                    {"role": "system", "content": _REACT_SYSTEM},
                    {"role": "user", "content": _build_user_payload(signal)},
                ]
            )
        except Exception as e:
            audit("react_llm_error", signal_id=signal.id, error=str(e))
            return {"error": "llm_error", "detail": str(e)}
    finally:
        _INFLIGHT -= 1

    try:
        decision = json.loads(raw)
    except json.JSONDecodeError as e:
        audit("react_parse_error", signal_id=signal.id, error=str(e), raw=raw[:500])
        return {"error": "parse_error", "raw": raw[:500]}

    interpretation = decision.get("interpretation") or ""
    signal.interpretation = interpretation
    audit("react_decision", signal_id=signal.id, decision=decision)

    for upd in decision.get("plan_updates") or []:
        try:
            STATE.plan.update_status(
                upd["plan_id"],
                PlanItemStatus(upd["new_status"]),
                notes=upd.get("notes"),
            )
            audit("plan_update", signal_id=signal.id, **upd)
        except (KeyError, ValueError) as e:
            audit("plan_update_error", signal_id=signal.id, error=str(e), upd=upd)

    risk_id = decision.get("risk_triggered")
    if risk_id:
        _TRIGGERED_RISK_IDS.add(risk_id)

    wishes_at_risk = decision.get("wishes_at_risk") or []
    for w in wishes_at_risk:
        wid = w.get("wish_id")
        if wid:
            _AT_RISK_WISH_IDS.add(wid)

    if risk_id:
        kind = "alert"
    elif wishes_at_risk:
        kind = "concern"
    else:
        kind = "info"

    base_message: dict[str, Any] = {
        "kind": kind,
        "risk_id": risk_id,
        "wishes_at_risk": wishes_at_risk,
        "severity": decision.get("severity"),
        "interpretation": interpretation,
        "ts": signal.ts,
        "trigger_signal_id": signal.id,
    }
    for entry in decision.get("fanout") or []:
        try:
            role = Role(entry["role"])
        except (KeyError, ValueError) as e:
            audit("fanout_role_error", signal_id=signal.id, error=str(e), entry=entry)
            continue
        msg = {**base_message, "message": entry.get("message") or ""}
        n = deliver_to_role(role, msg)
        audit("fanout", signal_id=signal.id, role=role.value, recipients=n)

    return decision


def answer_question(signal: Signal) -> str:
    """Answer an /ask question, grounded in STATE."""
    global _INFLIGHT
    audit("ask_start", signal_id=signal.id)

    _INFLIGHT += 1
    try:
        try:
            raw = chat(
                [
                    {"role": "system", "content": _ASK_SYSTEM},
                    {"role": "user", "content": _build_user_payload(signal)},
                ]
            )
        except Exception as e:
            audit("ask_llm_error", signal_id=signal.id, error=str(e))
            return f"(error contacting LLM: {e})"
    finally:
        _INFLIGHT -= 1

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as e:
        audit("ask_parse_error", signal_id=signal.id, error=str(e), raw=raw[:500])
        return f"(error parsing LLM response: {e})"

    answer = result.get("answer") or ""
    signal.interpretation = result.get("interpretation") or ""
    audit(
        "ask_decision",
        signal_id=signal.id,
        answer=answer,
        interpretation=signal.interpretation,
    )
    return answer
