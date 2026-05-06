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

import yaml
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from skill import reasoning
from skill.config import get_config, load_event_config, seed_event_from_dict
from skill.state import (
    AUDIT_PATH,
    STATE,
    Channel,
    EventBundle,
    EventMode,
    PlanItemStatus,
    Role,
    Signal,
    Stakeholder,
    audit,
)


def _live() -> EventBundle:
    ev = STATE.current()
    if ev is None:
        raise HTTPException(409, "no event is currently live")
    return ev


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
    ev = _live()
    s = ev.stakeholders.add(
        Stakeholder(
            role=body.role,
            area=body.area,
            display_name=body.display_name,
        )
    )
    audit("join", event_id=ev.id, stakeholder_id=s.id, role=s.role.value, area=s.area)
    return JoinOut(id=s.id, role=s.role, area=s.area)


# ---------- Input channels ----------

def _ingest(channel: Channel, body: SignalIn) -> Signal:
    ev = _live()
    s = ev.stakeholders.get(body.stakeholder_id) if body.stakeholder_id else None
    sig = Signal(
        channel=channel,
        source_id=s.id if s else None,
        source_role=s.role if s else None,
        area=body.area or (s.area if s else None),
        text=body.text,
    )
    ev.reality.add(sig)
    audit(
        "signal_received",
        event_id=ev.id,
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
    """Snapshot of the live event (or empty payload if none is live)."""
    ev = STATE.current()
    return ev.snapshot() if ev else {"event_mode": None, "event_id": None}


@app.get("/config")
def config() -> dict:
    """Event metadata + stakeholder display names + role distribution."""
    return get_config()


@app.get("/inbox/{stakeholder_id}")
def inbox(stakeholder_id: str) -> dict:
    ev = reasoning.event_for_stakeholder(stakeholder_id)
    if ev is None:
        raise HTTPException(404, "stakeholder not found")
    s = ev.stakeholders.get(stakeholder_id)
    return {
        "event_id": ev.id,
        "stakeholder": s.model_dump() if s else None,
        "messages": reasoning.inbox_for(stakeholder_id),
    }


# ---------- Multi-event management (Decision #22) ----------

class EventCreateIn(BaseModel):
    name: str
    scenario: Optional[str] = ""
    countdown_to: Optional[str] = ""
    areas: Optional[list[str]] = None


class EventOut(BaseModel):
    id: str
    name: str
    mode: EventMode
    scenario: str
    countdown_to: str
    areas: list[str]
    plan_count: int
    risks_count: int
    goals_count: int
    wishes_count: int
    stakeholder_count: int
    started_at: float
    started_at_live: Optional[float] = None


def _event_summary(ev: EventBundle) -> EventOut:
    return EventOut(
        id=ev.id,
        name=ev.name,
        mode=ev.mode,
        scenario=ev.scenario,
        countdown_to=ev.countdown_to,
        areas=list(ev.areas),
        plan_count=len(ev.plan.list()),
        risks_count=len(ev.risks.list()),
        goals_count=len(ev.goals.list()),
        wishes_count=len(ev.wishes.list()),
        stakeholder_count=len(ev.stakeholders.list()),
        started_at=ev.started_at,
        started_at_live=ev.started_at_live,
    )


@app.get("/events")
def list_events() -> dict:
    return {
        "active_id": STATE.active_id,
        "events": [_event_summary(ev).model_dump() for ev in STATE.list()],
    }


@app.post("/events", response_model=EventOut)
def create_event(body: EventCreateIn) -> EventOut:
    ev = STATE.create(
        name=body.name,
        mode=EventMode.SETUP,
        scenario=body.scenario or "",
        countdown_to=body.countdown_to or "",
        areas=body.areas or [],
    )
    audit("event_created", event_id=ev.id, name=ev.name)
    return _event_summary(ev)


@app.post("/events/{event_id}/activate", response_model=EventOut)
def activate_event(event_id: str) -> EventOut:
    prev_active = STATE.active_id
    ev = STATE.activate(event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    audit("event_activated", event_id=ev.id, name=ev.name, previous_active_id=prev_active)
    return _event_summary(ev)


@app.delete("/events/{event_id}")
def delete_event(event_id: str) -> dict:
    if not STATE.get(event_id):
        raise HTTPException(404, "event not found")
    STATE.remove(event_id)
    audit("event_removed", event_id=event_id)
    return {"removed": event_id, "active_id": STATE.active_id}


@app.post("/events/{event_id}/reset-schedule")
def reset_schedule(event_id: str) -> dict:
    """Soft-reset: restore plan items to their go-live snapshot, clear runtime."""
    ev = STATE.get(event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    ev.reset_schedule()
    audit("schedule_reset", event_id=ev.id, name=ev.name)
    return {
        "event_id": ev.id,
        "items_reset": len(ev.plan.list()),
        "started_at_live": ev.started_at_live,
    }


# ---------- Stakeholder bulk + extract endpoints (Phase 1A) ----------

class StakeholderIn(BaseModel):
    display_name: str
    email: Optional[str] = None
    category: Optional[str] = None
    role: Optional[str] = None  # if omitted, mapped from category
    area: Optional[str] = None
    notes: Optional[str] = None


class StakeholderBulkIn(BaseModel):
    stakeholders: list[StakeholderIn]


class StakeholderExtractIn(BaseModel):
    """Either pasted text or CSV — server detects which path."""
    text: str
    format: Optional[str] = None  # "csv" | "paste" | None (auto-detect)


@app.post("/stakeholders/bulk")
def stakeholders_bulk(body: StakeholderBulkIn) -> dict:
    """Add many stakeholders at once to the live event."""
    from skill import setup_import

    ev = _live()
    added = []
    for s in body.stakeholders:
        if not s.display_name.strip():
            continue
        role_str = s.role or setup_import.category_to_role(s.category)
        try:
            role_enum = Role(role_str)
        except ValueError:
            role_enum = Role.ORGANIZER
        new = ev.stakeholders.add(
            Stakeholder(
                role=role_enum,
                area=s.area or None,
                display_name=s.display_name.strip(),
                category=s.category or None,
                email=s.email or None,
                notes=s.notes or None,
            )
        )
        added.append(new.id)
        audit(
            "stakeholder_added",
            event_id=ev.id,
            stakeholder_id=new.id,
            role=new.role.value,
            category=new.category,
            display_name=new.display_name,
        )
    return {"added_ids": added, "count": len(added)}


@app.post("/stakeholders/extract")
def stakeholders_extract(body: StakeholderExtractIn) -> dict:
    """Preview-extract a stakeholder list from pasted text/CSV (no mutation).

    The frontend uses this to fill a review form before the user confirms via
    /stakeholders/bulk. CSV is parsed deterministically; non-CSV goes to the LLM.
    """
    from skill import setup_import

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty input")

    fmt = body.format
    # Auto-detect: looks like CSV if first line has commas/semicolons + a likely header row
    if fmt is None:
        first_line = text.splitlines()[0]
        looks_csv = (
            ("," in first_line or ";" in first_line or "\t" in first_line)
            and any(h in first_line.lower() for h in ("name", "email", "kategorie", "category", "role"))
        )
        fmt = "csv" if looks_csv else "paste"

    if fmt == "csv":
        items = setup_import.parse_csv_stakeholders(text)
        audit("stakeholder_extract_csv", event_id=STATE.active_id, count=len(items))
        return {"format": "csv", "stakeholders": items, "notes": ""}

    try:
        result = setup_import.extract_stakeholders_from_text(text)
    except Exception as e:
        audit("stakeholder_extract_llm_error", error=str(e))
        raise HTTPException(502, f"LLM extraction failed: {e}")

    audit("stakeholder_extract_llm", event_id=STATE.active_id, count=len(result.get("stakeholders") or []))
    return {
        "format": "paste",
        "stakeholders": result.get("stakeholders") or [],
        "notes": result.get("notes") or "",
    }


@app.delete("/stakeholders/{stakeholder_id}")
def stakeholder_remove(stakeholder_id: str) -> dict:
    """Remove a stakeholder from whichever event holds them."""
    for ev in STATE.list():
        if ev.stakeholders.get(stakeholder_id):
            removed = ev.stakeholders._by_id.pop(stakeholder_id, None)
            audit("stakeholder_removed", event_id=ev.id, stakeholder_id=stakeholder_id)
            if removed:
                return {"removed": stakeholder_id, "event_id": ev.id}
    raise HTTPException(404, "stakeholder not found")


# ---------- Setup-Phase import (Decisions #17, #18) ----------

class ImportSourceIn(BaseModel):
    """One source provided to the import endpoint.

    type=url   → `value` is the URL to fetch and HTML-strip.
    type=paste → `value` is free-form text, used as-is.
    type=yaml  → `value` is YAML text in the event-config.yaml shape.
    """
    type: str  # "url" | "paste" | "yaml"
    value: str


class ImportRequest(BaseModel):
    sources: list[ImportSourceIn]


class ImportResult(BaseModel):
    event_id: str
    event_type: Optional[str] = None
    conflicts: list[str] = []
    notes: str = ""
    counts: dict[str, int]


@app.post("/events/{event_id}/import", response_model=ImportResult)
def import_event(event_id: str, body: ImportRequest) -> ImportResult:
    """Build the event's plan/risks/wishes from one or more sources.

    Strategy (Decision #18):
    - YAML sources are parsed locally and merged.
    - URL sources are fetched + HTML-stripped.
    - URL + paste content are passed to ONE LLM call that synthesizes a
      unified event (or surfaces conflicts).
    - The event's existing definition (plan/risks/goals/wishes) is reset
      before re-seeding, so calling import twice replaces rather than
      doubles up.
    """
    from skill import setup_import  # local import to avoid heavy module-load at startup

    ev = STATE.get(event_id)
    if ev is None:
        raise HTTPException(404, "event not found")
    if not body.sources:
        raise HTTPException(400, "at least one source required")

    audit(
        "import_start",
        event_id=ev.id,
        source_count=len(body.sources),
        source_types=[s.type for s in body.sources],
    )

    # --- Fetch / parse each source ---
    yaml_dicts: list[dict[str, Any]] = []
    llm_sources: list[dict[str, str]] = []  # url + paste sent to the LLM
    for src in body.sources:
        if src.type == "yaml":
            try:
                yaml_dicts.append(setup_import.parse_yaml_source(src.value))
            except Exception as e:
                raise HTTPException(400, f"yaml parse error: {e}")
        elif src.type == "paste":
            llm_sources.append({"type": "paste", "text": src.value[:setup_import.MAX_TEXT_PER_SOURCE]})
        elif src.type == "url":
            try:
                pages = setup_import.discover_and_fetch(src.value)
            except Exception as e:
                raise HTTPException(400, f"failed to crawl {src.value}: {e}")
            if not pages:
                raise HTTPException(400, f"no content fetched from {src.value}")
            for p in pages:
                llm_sources.append({"type": "url", "url": p["url"], "text": p["text"]})
        else:
            raise HTTPException(400, f"unknown source type: {src.type}")

    # --- Synthesize via LLM if URL/paste content is present ---
    if llm_sources:
        # Include any provided YAML as additional hints for the LLM.
        for y in yaml_dicts:
            llm_sources.append({"type": "yaml", "text": yaml.safe_dump(y, sort_keys=False)})
        try:
            extracted = setup_import.extract_event_from_sources(llm_sources)
        except Exception as e:
            audit("import_llm_error", event_id=ev.id, error=str(e))
            raise HTTPException(502, f"LLM extraction failed: {e}")
    elif yaml_dicts:
        # Pure YAML fast path: merge all yaml dicts (later wins).
        extracted = {}
        for y in yaml_dicts:
            for k, v in y.items():
                if k in {"plan", "risks", "goals", "wishes"} and isinstance(v, list):
                    extracted.setdefault(k, []).extend(v)
                else:
                    extracted[k] = v
        extracted.setdefault("conflicts", [])
        extracted.setdefault("notes", "")
    else:
        raise HTTPException(400, "no usable sources after parsing")

    # --- Reset and re-seed the event's definition ---
    ev.reset_definition()
    # view_modes lives on the bundle, not in the definition stores; carry it
    # through here so import always refreshes it from the LLM's recommendation.
    if extracted.get("view_modes"):
        ev.view_modes = list(extracted["view_modes"])
    seed_event_from_dict(ev, extracted)

    counts = {
        "plan": len(ev.plan.list()),
        "risks": len(ev.risks.list()),
        "goals": len(ev.goals.list()),
        "wishes": len(ev.wishes.list()),
    }
    audit(
        "import_done",
        event_id=ev.id,
        event_type=extracted.get("event_type"),
        conflicts=extracted.get("conflicts") or [],
        **counts,
    )

    return ImportResult(
        event_id=ev.id,
        event_type=extracted.get("event_type"),
        conflicts=extracted.get("conflicts") or [],
        notes=extracted.get("notes") or "",
        counts=counts,
    )


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


def _signals_for_ui(ev: Optional[EventBundle], limit: int = 25) -> list[dict[str, Any]]:
    if ev is None:
        return []
    sigs = ev.reality.all()[-limit:]
    out = []
    for s in reversed(sigs):
        d = s.model_dump()
        d["age"] = _age(s.ts)
        d["channel"] = s.channel  # keep enum for template ".value" access
        d["source_role"] = s.source_role
        out.append(d)
    return out


def _all_events_summary() -> list[dict[str, Any]]:
    return [_event_summary(ev).model_dump(mode="json") for ev in STATE.list()]


def _ctx() -> dict[str, Any]:
    cfg = get_config()
    ev = STATE.current()

    if ev is None:
        # No live event — keep the dashboard renderable but empty.
        return {
            "event": {"name": "(no live event)", "scenario": "Activate or import an event to start coordinating."},
            "areas": [],
            "roles": [r.value for r in Role],
            "plan": [],
            "risks": [],
            "goals": [],
            "wishes": [],
            "stakeholders": [],
            "signals": [],
            "audit": _audit_tail(),
            "triggered_risks": set(),
            "at_risk_wishes": set(),
            "inbox_counts": {},
            "stakeholder_count": 0,
            "reality_count": 0,
            "active_alerts": 0,
            "active_concerns": 0,
            "thinking": reasoning.is_thinking() > 0,
            "active_event_id": None,
            "events_summary": _all_events_summary(),
        }

    return {
        "event": {
            "name": ev.name,
            "scenario": ev.scenario,
            "countdown_to": ev.countdown_to,
            "areas": ev.areas,
            "id": ev.id,
            "mode": ev.mode.value,
            "view_modes": list(ev.view_modes),
        },
        "areas": ev.areas or cfg.get("event", {}).get("areas", []),
        "roles": [r.value for r in Role],
        "plan": ev.plan.list(),
        "risks": ev.risks.list(),
        "goals": ev.goals.list(),
        "wishes": ev.wishes.list(),
        "stakeholders": ev.stakeholders.list(),
        "signals": _signals_for_ui(ev),
        "audit": _audit_tail(),
        "triggered_risks": set(ev.triggered_risk_ids),
        "at_risk_wishes": set(ev.at_risk_wish_ids),
        "inbox_counts": {
            s.id: len(reasoning.inbox_for(s.id))
            for s in ev.stakeholders.list()
        },
        "stakeholder_count": len(ev.stakeholders.list()),
        "reality_count": len(ev.reality.all()),
        "active_alerts": len(ev.triggered_risk_ids),
        "active_concerns": len(ev.at_risk_wish_ids),
        "thinking": reasoning.is_thinking() > 0,
        "active_event_id": ev.id,
        "events_summary": _all_events_summary(),
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
@app.get("/ui/event-switcher", response_class=HTMLResponse)
def ui_event_switcher(request: Request) -> HTMLResponse: return _fragment("_event_switcher.html", request)
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

def _result(message: str, ok: bool = True, hx_trigger: Optional[str] = None) -> HTMLResponse:
    cls = "ok" if ok else "err"
    headers = {"HX-Trigger": hx_trigger} if hx_trigger else None
    return HTMLResponse(
        f'<div class="qs-result {cls}">{message}</div>',
        headers=headers,
    )


@app.post("/ui/join", response_class=HTMLResponse)
def ui_join(
    role: Role = Form(...),
    area: str = Form(""),
    display_name: str = Form(""),
) -> HTMLResponse:
    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)
    s = ev.stakeholders.add(
        Stakeholder(role=role, area=area or None, display_name=display_name or None)
    )
    audit("join", event_id=ev.id, stakeholder_id=s.id, role=s.role.value, area=s.area)
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
    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)
    new_ids = []
    for name, role, area in _PRESET_ROSTER:
        s = ev.stakeholders.add(
            Stakeholder(role=role, area=area, display_name=name)
        )
        audit("join", event_id=ev.id, stakeholder_id=s.id, role=role.value, area=area)
        new_ids.append(f"{name}({role.value})")
    return _result("✓ seeded: " + ", ".join(new_ids))


_CROWD_LINES = [
    "crowded near front",
    "getting really tight up here",
    "cant move, pushed forward",
]


@app.post("/ui/preset/crowd-crush", response_class=HTMLResponse)
def ui_preset_crowd_crush(background: BackgroundTasks) -> HTMLResponse:
    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)
    fans = ev.stakeholders.list(role=Role.FAN)
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


@app.post("/ui/events/activate", response_class=HTMLResponse)
def ui_events_activate(event_id: str = Form(...)) -> HTMLResponse:
    prev_active = STATE.active_id
    if event_id == prev_active:
        return _result("ℹ event ist bereits aktiv", ok=True)
    ev = STATE.activate(event_id)
    if ev is None:
        return _result(f"✗ event {event_id[:6]} nicht gefunden", ok=False)
    audit("event_activated", event_id=ev.id, name=ev.name, previous_active_id=prev_active)
    return _result(
        f"✓ aktiviert: {ev.name} ({ev.id[:6]}) — vorher live: {(prev_active or '—')[:6]}",
        hx_trigger="events-changed",
    )


_STAKEHOLDER_LINE_RE = None  # lazy import


def _parse_stakeholder_lines(text: str, category: str) -> list[dict[str, Any]]:
    """Parse a per-category textarea into stakeholder dicts.

    Format per line: `Name [<email@x>] [— Note]` — all bracketed parts optional.
    """
    import re
    global _STAKEHOLDER_LINE_RE
    if _STAKEHOLDER_LINE_RE is None:
        _STAKEHOLDER_LINE_RE = re.compile(
            r"""^\s*
                (?P<name>[^<—\-]+?)
                (?:\s*<(?P<email>[^>]+)>)?
                (?:\s*[—\-]\s*(?P<note>.+))?
                \s*$
            """,
            re.VERBOSE,
        )
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _STAKEHOLDER_LINE_RE.match(line)
        if not m:
            out.append({"display_name": line, "category": category})
            continue
        out.append({
            "display_name": (m.group("name") or "").strip(),
            "email": (m.group("email") or "").strip() or None,
            "category": category,
            "notes": (m.group("note") or "").strip() or None,
        })
    return [s for s in out if s["display_name"]]


@app.post("/ui/stakeholders/manual", response_class=HTMLResponse)
async def ui_stakeholders_manual(request: Request) -> HTMLResponse:
    """Per-category-textarea form. Field names look like `cat::Speakers`.

    Plus optional `custom_category_name` + `cat::__custom__` for a free-form
    additional category.
    """
    from skill import setup_import

    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)

    form = await request.form()
    custom_name = (form.get("custom_category_name") or "").strip()

    all_dicts: list[dict[str, Any]] = []
    for key, value in form.items():
        if not key.startswith("cat::"):
            continue
        category_label = key[len("cat::") :]
        if category_label == "__custom__":
            if not custom_name or not (value or "").strip():
                continue
            category_label = custom_name
        if not (value or "").strip():
            continue
        all_dicts.extend(_parse_stakeholder_lines(value, category_label))

    if not all_dicts:
        return _result("✗ keine Stakeholder erkannt — füll mind. eine Kategorie", ok=False)

    added = 0
    by_cat: dict[str, int] = {}
    for d in all_dicts:
        role_str = setup_import.category_to_role(d.get("category"))
        try:
            role_enum = Role(role_str)
        except ValueError:
            role_enum = Role.ORGANIZER
        new = ev.stakeholders.add(
            Stakeholder(
                role=role_enum,
                area=d.get("area") or None,
                display_name=d["display_name"],
                category=d.get("category") or None,
                email=d.get("email") or None,
                notes=d.get("notes") or None,
            )
        )
        audit(
            "stakeholder_added",
            event_id=ev.id,
            stakeholder_id=new.id,
            role=new.role.value,
            category=new.category,
            display_name=new.display_name,
        )
        added += 1
        by_cat[d.get("category") or "(no category)"] = by_cat.get(d.get("category") or "(no category)", 0) + 1

    summary = ", ".join(f"{n}× {c}" for c, n in by_cat.items())
    return _result(f"✓ {added} Stakeholder angelegt: {summary}", hx_trigger="events-changed")


@app.post("/ui/stakeholders/extract", response_class=HTMLResponse)
async def ui_stakeholders_extract(
    request: Request,
    text: str = Form(""),
) -> HTMLResponse:
    """Bulk-extract entry: parse CSV deterministically or call LLM for free-text.

    Returns an HTML confirm-form pre-filled with the extracted stakeholder list.
    File-upload support: if a file is included, its text content replaces `text`.
    """
    from skill import setup_import

    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)

    # Read uploaded file if present
    form = await request.form()
    upload = form.get("upload")
    if upload and hasattr(upload, "read") and getattr(upload, "filename", ""):
        try:
            raw = await upload.read()
            text = raw.decode("utf-8", errors="replace")
        except Exception:
            pass

    text = (text or "").strip()
    if not text:
        return _result("✗ keine Eingabe — paste Liste oder lade Datei", ok=False)

    # Auto-detect format
    first_line = text.splitlines()[0] if text else ""
    looks_csv = (
        ("," in first_line or ";" in first_line or "\t" in first_line)
        and any(h in first_line.lower() for h in ("name", "email", "kategorie", "category", "role"))
    )
    try:
        if looks_csv:
            items = setup_import.parse_csv_stakeholders(text)
            llm_notes = ""
        else:
            result = setup_import.extract_stakeholders_from_text(text)
            items = result.get("stakeholders") or []
            llm_notes = result.get("notes") or ""
    except Exception as e:
        return _result(f"✗ Extract fehlgeschlagen: {e}", ok=False)

    if not items:
        return _result("✗ keine Stakeholder gefunden", ok=False)

    # Render review form with hidden JSON payload + readable list
    payload = json.dumps({"stakeholders": items})
    rows_html = []
    for s in items:
        rows_html.append(
            f"<tr>"
            f"<td>{(s.get('display_name') or '?')}</td>"
            f"<td>{(s.get('category') or '—')}</td>"
            f"<td>{(s.get('email') or '—')}</td>"
            f"<td>{(s.get('notes') or '')}</td>"
            f"</tr>"
        )
    notes_html = f'<p class="setup-hint">{llm_notes}</p>' if llm_notes else ""
    html = (
        '<div class="extract-review">'
        f"<h4>Review: {len(items)} Stakeholder erkannt</h4>"
        + notes_html +
        '<table class="extract-table"><thead><tr><th>Name</th><th>Kategorie</th><th>Email</th><th>Notiz</th></tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody></table>'
        '<form hx-post="/ui/stakeholders/bulk-confirm" hx-target="#stakeholder-result" hx-swap="innerHTML">'
        f'<input type="hidden" name="payload" value="{payload.replace(chr(34), "&quot;")}">'
        '<button>✓ Confirm und alle anlegen</button>'
        "</form>"
        "</div>"
    )
    return HTMLResponse(html)


@app.post("/ui/stakeholders/bulk-confirm", response_class=HTMLResponse)
def ui_stakeholders_bulk_confirm(payload: str = Form(...)) -> HTMLResponse:
    """Confirm step: take the JSON payload from the review form and seed."""
    from skill import setup_import

    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)

    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return _result("✗ Confirm-Payload korrupt", ok=False)

    items = data.get("stakeholders") or []
    if not items:
        return _result("✗ keine Stakeholder im Payload", ok=False)

    added = 0
    for s in items:
        name = (s.get("display_name") or "").strip()
        if not name:
            continue
        role_str = s.get("role") or setup_import.category_to_role(s.get("category"))
        try:
            role_enum = Role(role_str)
        except ValueError:
            role_enum = Role.ORGANIZER
        new = ev.stakeholders.add(
            Stakeholder(
                role=role_enum,
                area=s.get("area") or None,
                display_name=name,
                category=s.get("category") or None,
                email=s.get("email") or None,
                notes=s.get("notes") or None,
            )
        )
        audit(
            "stakeholder_added",
            event_id=ev.id,
            stakeholder_id=new.id,
            role=new.role.value,
            category=new.category,
            display_name=new.display_name,
        )
        added += 1

    return _result(f"✓ {added} Stakeholder angelegt aus Bulk-Import", hx_trigger="events-changed")


@app.post("/ui/setup/import", response_class=HTMLResponse)
def ui_setup_import(
    urls: str = Form(""),
    paste: str = Form(""),
    yaml_text: str = Form(""),
) -> HTMLResponse:
    """Form-flavored entry point that wraps POST /events/{active}/import.

    Targets the currently-active event so the user can import on any event
    by first switching to it via the events-bar dropdown.
    """
    ev = STATE.current()
    if ev is None:
        return _result("✗ kein aktives Event — erst 'create empty' im Switcher", ok=False)

    sources: list[ImportSourceIn] = []
    for line in (urls or "").splitlines():
        url = line.strip()
        if url:
            sources.append(ImportSourceIn(type="url", value=url))
    if (paste or "").strip():
        sources.append(ImportSourceIn(type="paste", value=paste))
    if (yaml_text or "").strip():
        sources.append(ImportSourceIn(type="yaml", value=yaml_text))

    if not sources:
        return _result("✗ keine Quellen — mindestens eine Eingabe brauchts", ok=False)

    try:
        result = import_event(ev.id, ImportRequest(sources=sources))
    except HTTPException as e:
        return _result(f"✗ Import fehlgeschlagen ({e.status_code}): {e.detail}", ok=False)

    parts = [f"✓ {ev.name}: plan={result.counts['plan']} risks={result.counts['risks']} goals={result.counts['goals']} wishes={result.counts['wishes']}"]
    if result.event_type:
        parts.append(f"event-type: {result.event_type}")
    if result.conflicts:
        parts.append(f"conflicts ({len(result.conflicts)}): " + " · ".join(result.conflicts[:3]))
    if result.notes:
        parts.append(f"notes: {result.notes[:200]}")
    return _result("\n".join(parts), hx_trigger="events-changed")


@app.post("/ui/events/reset-schedule", response_class=HTMLResponse)
def ui_events_reset_schedule() -> HTMLResponse:
    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)
    ev.reset_schedule()
    audit("schedule_reset", event_id=ev.id, name=ev.name)
    return _result(
        f"✓ Schedule zurückgesetzt: {len(ev.plan.list())} Plan-Items auf Original-Zeit/-Status, Outboxes geleert.",
        hx_trigger="events-changed",
    )


@app.post("/ui/events/create", response_class=HTMLResponse)
def ui_events_create(name: str = Form(...)) -> HTMLResponse:
    name = name.strip()
    if not name:
        return _result("✗ Name darf nicht leer sein", ok=False)
    ev = STATE.create(name=name, mode=EventMode.SETUP)
    audit("event_created", event_id=ev.id, name=ev.name)
    return _result(
        f"✓ Event '{ev.name}' angelegt (id={ev.id[:6]}, mode=setup) — "
        f"per Dropdown aktivieren oder via /events/{{id}}/import füllen",
        hx_trigger="events-changed",
    )


@app.post("/ui/preset/clear", response_class=HTMLResponse)
def ui_preset_clear() -> HTMLResponse:
    """Reset the live event's reality, stakeholders, outboxes, plan-status.

    Leaves Plan/Risks/Goals/Wishes seeded from YAML/import; only the runtime
    state goes back to a fresh slate.
    """
    from skill.state import RealityState, StakeholderGraph
    ev = STATE.current()
    if ev is None:
        return _result("✗ kein Live-Event aktiv", ok=False)
    ev.reality = RealityState()
    ev.stakeholders = StakeholderGraph()
    for p in ev.plan.list():
        p.status = PlanItemStatus.PLANNED
        p.notes = None
    reasoning.reset_runtime()
    audit("ui_reset", event_id=ev.id)
    return _result("✓ Reality, Stakeholders, Outboxes geleert. Plan-Status zurück auf 'planned'.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
