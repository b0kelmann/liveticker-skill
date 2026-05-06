"""LiveTicker FastAPI server — input-fusion entry point.

Three input channels (/post, /signal, /ask) feed RealityState; each new
signal triggers the reasoning loop, which may fanout to stakeholder roles.
/join is the QR-scan landing. /state and /inbox are read-side endpoints
for the dashboard and per-stakeholder views.

Run:
    uvicorn skill.server:app --reload --host 0.0.0.0 --port 8765
"""
from __future__ import annotations

import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from skill import reasoning
from skill.config import get_config, load_event_config
from skill.state import (
    AUDIT_PATH,
    STATE,
    Channel,
    PlanItemStatus,
    RealityState,
    Role,
    Signal,
    Stakeholder,
    StakeholderGraph,
    audit,
)


_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_STATIC_DIR = Path(__file__).resolve().parent / "static"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_event_config()
    yield


app = FastAPI(title="LiveTicker", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ---------- Wire shapes ----------

class JoinIn(BaseModel):
    role: Role
    area: Optional[str] = None
    display_name: Optional[str] = None


class JoinOut(BaseModel):
    id: str
    role: Role
    area: Optional[str] = None


class SignalIn(BaseModel):
    """Common shape for /post, /signal, /ask."""
    text: str
    stakeholder_id: Optional[str] = None
    area: Optional[str] = None


class SignalOut(BaseModel):
    id: str


class AskOut(BaseModel):
    id: str
    answer: str


# ---------- Lifecycle ----------

@app.post("/join", response_model=JoinOut)
def join(body: JoinIn) -> JoinOut:
    s = STATE.stakeholders.add(
        Stakeholder(
            role=body.role,
            area=body.area,
            display_name=body.display_name,
        )
    )
    audit("join", stakeholder_id=s.id, role=s.role.value, area=s.area)
    return JoinOut(id=s.id, role=s.role, area=s.area)


# ---------- Input channels ----------

def _ingest(channel: Channel, body: SignalIn) -> Signal:
    s = STATE.stakeholders.get(body.stakeholder_id) if body.stakeholder_id else None
    sig = Signal(
        channel=channel,
        source_id=s.id if s else None,
        source_role=s.role if s else None,
        area=body.area or (s.area if s else None),
        text=body.text,
    )
    STATE.reality.add(sig)
    audit(
        "signal_received",
        signal_id=sig.id,
        channel=channel.value,
        source_id=sig.source_id,
        source_role=sig.source_role.value if sig.source_role else None,
        area=sig.area,
        text=sig.text,
    )
    return sig


@app.post("/post", response_model=SignalOut)
def post(body: SignalIn) -> SignalOut:
    sig = _ingest(Channel.POST, body)
    reasoning.react(sig)
    return SignalOut(id=sig.id)


@app.post("/signal", response_model=SignalOut)
def signal(body: SignalIn) -> SignalOut:
    sig = _ingest(Channel.SIGNAL, body)
    reasoning.react(sig)
    return SignalOut(id=sig.id)


@app.post("/ask", response_model=AskOut)
def ask(body: SignalIn) -> AskOut:
    sig = _ingest(Channel.ASK, body)
    answer = reasoning.answer_question(sig)
    return AskOut(id=sig.id, answer=answer)


# ---------- Read side ----------

@app.get("/state")
def state() -> dict:
    return STATE.snapshot()


@app.get("/config")
def config() -> dict:
    """Event metadata + stakeholder display names + role distribution."""
    return get_config()


@app.get("/inbox/{stakeholder_id}")
def inbox(stakeholder_id: str) -> dict:
    s = STATE.stakeholders.get(stakeholder_id)
    if not s:
        raise HTTPException(404, "stakeholder not found")
    return {
        "stakeholder": s.model_dump(),
        "messages": reasoning.inbox_for(stakeholder_id),
    }


# ---------- UI helpers ----------

def _age(ts: float) -> str:
    delta = max(0, time.time() - ts)
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta / 60)}m"
    return f"{int(delta / 3600)}h"


def _audit_tail(n: int = 30) -> list[dict[str, Any]]:
    if not AUDIT_PATH.exists():
        return []
    with AUDIT_PATH.open() as f:
        lines = f.readlines()
    out: list[dict[str, Any]] = []
    for line in lines[-n:]:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry["age"] = _age(entry.get("ts", time.time()))
        entry["detail"] = _audit_detail(entry)
        out.append(entry)
    return list(reversed(out))


def _audit_detail(a: dict[str, Any]) -> str:
    e = a.get("event")
    if e == "react_decision":
        d = a.get("decision", {}) or {}
        interp = (d.get("interpretation") or "")[:120]
        risk = d.get("risk_triggered")
        wishes = d.get("wishes_at_risk") or []
        tags = []
        if risk:
            tags.append(f"risk: {risk}")
        if wishes:
            tags.append(f"wishes-at-risk: {len(wishes)}")
        return interp + (f" [{'; '.join(tags)}]" if tags else "")
    if e == "fanout":
        return f"role={a.get('role')} → {a.get('recipients')} stakeholder(s)"
    if e == "plan_update":
        return f"{(a.get('plan_id') or '?')[:6]} → {a.get('new_status')}: {(a.get('notes') or '')[:60]}"
    if e == "signal_received":
        return f"{a.get('source_role') or 'anon'}: {(a.get('text') or '')[:80]}"
    if e == "join":
        return f"{a.get('role')} joined ({(a.get('stakeholder_id') or '?')[:6]})"
    if e == "react_start":
        return f"signal {(a.get('signal_id') or '?')[:6]} → LLM"
    if e == "ask_start":
        return f"ask {(a.get('signal_id') or '?')[:6]} → LLM"
    if e == "config_loaded":
        return f"plan={a.get('plan_items')} risks={a.get('risks')} goals={a.get('goals')}"
    return ""


def _signals_for_ui(limit: int = 25) -> list[dict[str, Any]]:
    sigs = STATE.reality.all()[-limit:]
    out = []
    for s in reversed(sigs):
        d = s.model_dump()
        d["age"] = _age(s.ts)
        d["channel"] = s.channel  # keep enum for template ".value" access
        d["source_role"] = s.source_role
        out.append(d)
    return out


def _ctx() -> dict[str, Any]:
    cfg = get_config()
    return {
        "event": cfg.get("event", {}),
        "areas": cfg.get("event", {}).get("areas", []),
        "roles": [r.value for r in Role],
        "plan": STATE.plan.list(),
        "risks": STATE.risks.list(),
        "goals": STATE.goals.list(),
        "wishes": STATE.wishes.list(),
        "stakeholders": STATE.stakeholders.list(),
        "signals": _signals_for_ui(),
        "audit": _audit_tail(),
        "triggered_risks": reasoning.triggered_risk_ids(),
        "at_risk_wishes": reasoning.at_risk_wish_ids(),
        "inbox_counts": {
            s.id: len(reasoning.inbox_for(s.id))
            for s in STATE.stakeholders.list()
        },
        "stakeholder_count": len(STATE.stakeholders.list()),
        "reality_count": len(STATE.reality.all()),
        "active_alerts": len(reasoning.triggered_risk_ids()),
        "active_concerns": len(reasoning.at_risk_wish_ids()),
        "thinking": reasoning.is_thinking() > 0,
    }


# ---------- UI: full page ----------

@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", _ctx())


# ---------- UI: HTMX fragments ----------

def _fragment(name: str, request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, name, _ctx())


@app.get("/ui/header",       response_class=HTMLResponse)
def ui_header(request: Request)       -> HTMLResponse: return _fragment("_header.html", request)
@app.get("/ui/plan",         response_class=HTMLResponse)
def ui_plan(request: Request)         -> HTMLResponse: return _fragment("_plan.html", request)
@app.get("/ui/risks",        response_class=HTMLResponse)
def ui_risks(request: Request)        -> HTMLResponse: return _fragment("_risks.html", request)
@app.get("/ui/goals",        response_class=HTMLResponse)
def ui_goals(request: Request)        -> HTMLResponse: return _fragment("_goals.html", request)
@app.get("/ui/wishes",       response_class=HTMLResponse)
def ui_wishes(request: Request)       -> HTMLResponse: return _fragment("_wishes.html", request)
@app.get("/ui/reality",      response_class=HTMLResponse)
def ui_reality(request: Request)      -> HTMLResponse: return _fragment("_reality.html", request)
@app.get("/ui/stakeholders", response_class=HTMLResponse)
def ui_stakeholders(request: Request) -> HTMLResponse: return _fragment("_stakeholders.html", request)
@app.get("/ui/audit",        response_class=HTMLResponse)
def ui_audit(request: Request)        -> HTMLResponse: return _fragment("_audit.html", request)


# ---------- UI: form-encoded actions ----------

def _result(message: str, ok: bool = True) -> HTMLResponse:
    cls = "ok" if ok else "err"
    return HTMLResponse(f'<div class="qs-result {cls}">{message}</div>')


@app.post("/ui/join", response_class=HTMLResponse)
def ui_join(
    role: Role = Form(...),
    area: str = Form(""),
    display_name: str = Form(""),
) -> HTMLResponse:
    s = STATE.stakeholders.add(
        Stakeholder(role=role, area=area or None, display_name=display_name or None)
    )
    audit("join", stakeholder_id=s.id, role=s.role.value, area=s.area)
    name = display_name or s.id
    return _result(f"✓ {role.value} '{name}' joined ({s.id[:6]}) @ {area or 'no-area'}")


@app.post("/ui/send", response_class=HTMLResponse)
def ui_send(
    background: BackgroundTasks,
    channel: str = Form(...),
    text: str = Form(...),
    stakeholder_id: str = Form(""),
    area: str = Form(""),
) -> HTMLResponse:
    try:
        ch = Channel(channel)
    except ValueError:
        return _result(f"✗ unknown channel '{channel}'", ok=False)

    sig = _ingest(ch, SignalIn(text=text, stakeholder_id=stakeholder_id or None, area=area or None))

    # Background-dispatch reasoning so the UI stays responsive.
    if ch is Channel.ASK:
        background.add_task(reasoning.answer_question, sig)
    else:
        background.add_task(reasoning.react, sig)

    return _result(
        f"✓ /{ch.value} signal {sig.id[:6]} queued · text: {text!r} · LLM denkt im Hintergrund"
    )


# ---------- UI: presets ----------

_PRESET_ROSTER = [
    ("Anna",  Role.FAN,        "main_stage"),
    ("Ben",   Role.FAN,        "main_stage"),
    ("Clara", Role.FAN,        "main_stage"),
    ("Dave",  Role.SECURITY,   "main_stage"),
    ("Eva",   Role.MEDIC,      "medical_tent"),
    ("Finn",  Role.STAGE_TECH, "main_stage"),
    ("Greta", Role.VENDOR,     "food_court"),
]


@app.post("/ui/preset/seed-stakeholders", response_class=HTMLResponse)
def ui_preset_seed() -> HTMLResponse:
    new_ids = []
    for name, role, area in _PRESET_ROSTER:
        s = STATE.stakeholders.add(
            Stakeholder(role=role, area=area, display_name=name)
        )
        audit("join", stakeholder_id=s.id, role=role.value, area=area)
        new_ids.append(f"{name}({role.value})")
    return _result("✓ seeded: " + ", ".join(new_ids))


_CROWD_LINES = [
    "crowded near front",
    "getting really tight up here",
    "cant move, pushed forward",
]


@app.post("/ui/preset/crowd-crush", response_class=HTMLResponse)
def ui_preset_crowd_crush(background: BackgroundTasks) -> HTMLResponse:
    fans = STATE.stakeholders.list(role=Role.FAN)
    if len(fans) < 3:
        return _result("✗ brauche min. 3 fans — erst 'Seed' klicken", ok=False)
    used = fans[:3]
    sig_ids = []
    for fan, line in zip(used, _CROWD_LINES):
        sig = _ingest(
            Channel.SIGNAL,
            SignalIn(text=line, stakeholder_id=fan.id, area="main_stage"),
        )
        background.add_task(reasoning.react, sig)
        sig_ids.append(sig.id[:6])
    return _result(
        "✓ 3 crowd-Signale queued (" + ", ".join(sig_ids)
        + "). Jeder LLM-Call dauert 30–90s. Beobachte 'Reality' + 'Risks' oben."
    )


@app.post("/ui/preset/clear", response_class=HTMLResponse)
def ui_preset_clear() -> HTMLResponse:
    STATE.reality = RealityState()
    STATE.stakeholders = StakeholderGraph()
    # Plan-Status zurücksetzen (Plan/Risks/Goals bleiben aus YAML)
    for p in STATE.plan.list():
        p.status = PlanItemStatus.PLANNED
        p.notes = None
    reasoning.reset_runtime()
    audit("ui_reset")
    return _result("✓ Reality, Stakeholders, Outboxes geleert. Plan-Status zurück auf 'planned'.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
