"""LiveTicker state stores — the agent's worldview.

Per-event, six explicit knowledge stores form the input-fusion architecture:

- PlanState:        what is *intended* to happen (the schedule)
- RealityState:     what is *actually* happening (incoming signals)
- RiskCatalog:      patterns we watch for in Reality (e.g. crowd-crush)
- Goals:            organizer-defined success criteria
- Wishes:           per-role stakeholder wishes — second reasoning axis ("whose
                    wishes are at risk?") alongside risk-threshold detection
- StakeholderGraph: who's at the event and in what role

These six live inside an EventBundle. The module-level STATE is an EventStore
container that holds N events and tracks which one is currently `live`. The
reasoning loop only runs against the live event; events in `setup` mode accept
imports and edits but no incoming signals (decision #22).

Making each store explicit (vs. one big context blob) is what keeps the
agent's reasoning explainable rather than a black box.

In-memory only — boot-time seed comes from event-config.yaml (loader in
skill.config).
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


# ---------- Stakeholders ----------

class Role(str, Enum):
    FAN = "fan"
    ARTIST = "artist"
    STAGE_TECH = "stage_tech"
    SECURITY = "security"
    MEDIC = "medic"
    VENDOR = "vendor"
    ORGANIZER = "organizer"


class Stakeholder(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    role: Role  # abstract coordination role (drives reasoning fanout)
    area: Optional[str] = None
    display_name: Optional[str] = None
    category: Optional[str] = None  # human-readable function ("Garderobe", "Catering Crew")
    email: Optional[str] = None
    notes: Optional[str] = None  # free-form: speaker topic, "vegan only", etc.
    joined_at: float = Field(default_factory=time.time)


class StakeholderGraph:
    """Roster of who's at the event and in what role."""

    def __init__(self) -> None:
        self._by_id: dict[str, Stakeholder] = {}

    def add(self, s: Stakeholder) -> Stakeholder:
        self._by_id[s.id] = s
        return s

    def get(self, id: str) -> Optional[Stakeholder]:
        return self._by_id.get(id)

    def list(
        self,
        role: Optional[Role] = None,
        area: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[Stakeholder]:
        items = list(self._by_id.values())
        if role:
            items = [s for s in items if s.role == role]
        if area:
            items = [s for s in items if s.area == area]
        if category:
            items = [s for s in items if s.category == category]
        return items

    def count_by_role(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self._by_id.values():
            out[s.role.value] = out.get(s.role.value, 0) + 1
        return out


# ---------- Plan ----------

class PlanItemStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    DELAYED = "delayed"
    CANCELLED = "cancelled"


class PlanItem(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    day: Optional[str] = None       # "Day 1" / "2026-05-04" / "Mon" — set for multi-day events
    time: str                        # "19:30" or ISO string — free-form
    what: str
    who: list[Role] = Field(default_factory=list)
    where: Optional[str] = None      # physical room/stage ("Master Stage", "Chapel")
    track: Optional[str] = None      # thematic schiene ("Agentic AI Summit") — distinct from where
    tags: list[str] = Field(default_factory=list)  # free-form labels ("keynote","workshop","panel")
    status: PlanItemStatus = PlanItemStatus.PLANNED
    notes: Optional[str] = None
    # Snapshot fields, frozen on go-live so the UI can show "geplant 14:00 → ist 14:30 [delayed]".
    original_time: Optional[str] = None
    original_status: Optional[PlanItemStatus] = None


class PlanState:
    """The intended schedule. Editable by the organizer at runtime."""

    def __init__(self) -> None:
        self._items: dict[str, PlanItem] = {}

    def add(self, item: PlanItem) -> PlanItem:
        self._items[item.id] = item
        return item

    def get(self, id: str) -> Optional[PlanItem]:
        return self._items.get(id)

    def update_status(
        self,
        id: str,
        status: PlanItemStatus,
        notes: Optional[str] = None,
    ) -> Optional[PlanItem]:
        item = self._items.get(id)
        if not item:
            return None
        item.status = status
        if notes:
            item.notes = notes
        return item

    def list(self) -> list[PlanItem]:
        return sorted(
            self._items.values(),
            key=lambda x: (x.day or "", x.time, x.track or "", x.where or ""),
        )


# ---------- Reality ----------

class Channel(str, Enum):
    POST = "post"      # narrative update from organizer/crew
    SIGNAL = "signal"  # structured observation ("crowded near front")
    ASK = "ask"        # question that doubles as a sensor


class Signal(BaseModel):
    """One observation entering the system from the field."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    ts: float = Field(default_factory=time.time)
    channel: Channel
    source_id: Optional[str] = None
    source_role: Optional[Role] = None
    area: Optional[str] = None
    text: str
    interpretation: Optional[str] = None  # set by reasoning loop


class RealityState:
    """Append-only stream of what's actually happening."""

    def __init__(self) -> None:
        self._signals: list[Signal] = []

    def add(self, s: Signal) -> Signal:
        self._signals.append(s)
        return s

    def recent(
        self, seconds: int = 60, area: Optional[str] = None
    ) -> list[Signal]:
        cutoff = time.time() - seconds
        items = [s for s in self._signals if s.ts >= cutoff]
        if area:
            items = [s for s in items if s.area == area]
        return items

    def all(self) -> list[Signal]:
        return list(self._signals)


# ---------- Risks ----------

class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Risk(BaseModel):
    id: str
    name: str
    description: str
    pattern: str       # natural-language pattern the LLM checks against
    threshold: str     # human-readable trigger ("≥3 fan signals in 60s, same area")
    fanout: list[Role] = Field(default_factory=list)
    severity: Severity = Severity.MEDIUM


class RiskCatalog:
    """Known risk patterns the agent watches for."""

    def __init__(self) -> None:
        self._risks: dict[str, Risk] = {}

    def add(self, r: Risk) -> Risk:
        self._risks[r.id] = r
        return r

    def get(self, id: str) -> Optional[Risk]:
        return self._risks.get(id)

    def list(self) -> list[Risk]:
        return list(self._risks.values())


# ---------- Goals ----------

class Goal(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    text: str
    driver_for: list[Role] = Field(default_factory=list)
    metric: Optional[str] = None


class Goals:
    """Organizer-defined success criteria for the event."""

    def __init__(self) -> None:
        self._goals: dict[str, Goal] = {}

    def add(self, g: Goal) -> Goal:
        self._goals[g.id] = g
        return g

    def list(self, role: Optional[Role] = None) -> list[Goal]:
        items = list(self._goals.values())
        if role:
            items = [g for g in items if role in g.driver_for]
        return items


# ---------- Wishes ----------

class Wish(BaseModel):
    """A wish held by stakeholders of one or more roles.

    Where Risks are defensive ("avoid disaster"), Wishes are outcome-positive
    ("what would make this stakeholder leave wanting to recommend the event?").
    The reasoning loop scores both axes in parallel.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    text: str
    holder_roles: list[Role] = Field(default_factory=list)


class Wishes:
    """Per-role stakeholder wishes — second reasoning axis."""

    def __init__(self) -> None:
        self._wishes: dict[str, Wish] = {}

    def add(self, w: Wish) -> Wish:
        self._wishes[w.id] = w
        return w

    def get(self, id: str) -> Optional[Wish]:
        return self._wishes.get(id)

    def list(self, role: Optional[Role] = None) -> list[Wish]:
        items = list(self._wishes.values())
        if role:
            items = [w for w in items if role in w.holder_roles]
        return items


# ---------- Audit log ----------

AUDIT_PATH = Path(__file__).resolve().parent.parent / "audit.log"


def audit(event: str, **fields) -> None:
    """Append a single JSON line to audit.log.

    Rendered live on the dashboard so the reasoning chain is visible
    rather than implied. Manifest declares audit.enabled = true.
    """
    record = {"ts": time.time(), "event": event, **fields}
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ---------- Event Bundle + Multi-Event Store (Decision #22) ----------

class EventMode(str, Enum):
    SETUP = "setup"   # being imported / edited; no signal ingestion
    LIVE = "live"     # the agent is coordinating this one


@dataclass
class EventBundle:
    """One event's worldview. The agent holds N of these; exactly one is live."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    name: str = "Unnamed event"
    mode: EventMode = EventMode.SETUP
    scenario: str = ""
    countdown_to: str = ""
    areas: list[str] = field(default_factory=list)
    # LLM-suggested ways to render the Plan in this event ("by_day", "by_track",
    # "by_where", "chronological"). UI shows a pill-bar with these options.
    view_modes: list[str] = field(default_factory=lambda: ["chronological"])

    plan: PlanState = field(default_factory=PlanState)
    reality: RealityState = field(default_factory=RealityState)
    risks: RiskCatalog = field(default_factory=RiskCatalog)
    goals: Goals = field(default_factory=Goals)
    wishes: Wishes = field(default_factory=Wishes)
    stakeholders: StakeholderGraph = field(default_factory=StakeholderGraph)

    # Per-event runtime tracked by the reasoning loop
    triggered_risk_ids: set[str] = field(default_factory=set)
    at_risk_wish_ids: set[str] = field(default_factory=set)
    outbox: dict[str, list[dict]] = field(default_factory=dict)  # stakeholder_id → messages

    started_at: float = field(default_factory=time.time)
    # Lifecycle stamps: started_at_live is set ONCE on the first go-live transition;
    # Plan-snapshot fields on PlanItem are frozen at the same moment so subsequent
    # status changes by the reasoning loop can be diff-rendered against the original.
    started_at_live: Optional[float] = None

    def snapshot(self) -> dict:
        """Serialize the whole worldview — for dashboards and LLM prompts."""
        return {
            "event_id": self.id,
            "event_name": self.name,
            "event_mode": self.mode.value,
            "started_at": self.started_at,
            "view_modes": list(self.view_modes),
            "plan": [i.model_dump() for i in self.plan.list()],
            "reality_recent": [s.model_dump() for s in self.reality.recent(seconds=300)],
            "risks": [r.model_dump() for r in self.risks.list()],
            "goals": [g.model_dump() for g in self.goals.list()],
            "wishes": [w.model_dump() for w in self.wishes.list()],
            "stakeholder_counts": self.stakeholders.count_by_role(),
        }

    def reset_definition(self) -> None:
        """Wipe the editable definition stores (Plan/Risks/Goals/Wishes).

        Stakeholders, Reality, runtime tracking and event metadata are
        preserved. Used by /events/{id}/import to re-seed cleanly.
        """
        self.plan = PlanState()
        self.risks = RiskCatalog()
        self.goals = Goals()
        self.wishes = Wishes()
        # Re-import means a new schedule, so any frozen snapshot is now stale.
        self.started_at_live = None

    def snapshot_plan(self) -> None:
        """Freeze original_time/original_status on each plan item.

        Called on the first transition to LIVE so that subsequent status
        changes by the reasoning loop can be diff-rendered ('geplant 14:00 →
        ist 14:30 [delayed]'). Idempotent — already-snapshotted items keep
        their first-frozen values.
        """
        for p in self.plan.list():
            if p.original_time is None:
                p.original_time = p.time
            if p.original_status is None:
                p.original_status = p.status

    def reset_schedule(self) -> None:
        """Soft reset: restore plan items to their snapshot, clear runtime.

        Stakeholders + Reality + event metadata stay. Use after a demo run
        when you want to replay against the same setup.
        """
        for p in self.plan.list():
            if p.original_time is not None:
                p.time = p.original_time
            if p.original_status is not None:
                p.status = p.original_status
            p.notes = None
        self.outbox.clear()
        self.triggered_risk_ids.clear()
        self.at_risk_wish_ids.clear()


class EventStore:
    """Holds N events; tracks which one is `live` (Decision #22).

    Exactly one event can be `live` at a time. Activating a new event demotes
    the previous live one back to `setup`. Reasoning + signal endpoints route
    through `current()`; if no event is live, those endpoints should 409.
    """

    def __init__(self) -> None:
        self._events: dict[str, EventBundle] = {}
        self._active_id: Optional[str] = None

    def create(
        self,
        name: str,
        mode: EventMode = EventMode.SETUP,
        scenario: str = "",
        countdown_to: str = "",
        areas: Optional[list[str]] = None,
    ) -> EventBundle:
        ev = EventBundle(
            name=name,
            mode=mode,
            scenario=scenario,
            countdown_to=countdown_to,
            areas=list(areas or []),
        )
        self._events[ev.id] = ev
        if mode == EventMode.LIVE:
            self.activate(ev.id)
        return ev

    def get(self, id: str) -> Optional[EventBundle]:
        return self._events.get(id)

    def list(self) -> list[EventBundle]:
        return list(self._events.values())

    def remove(self, id: str) -> bool:
        if id == self._active_id:
            self._active_id = None
        return self._events.pop(id, None) is not None

    def activate(self, id: str) -> Optional[EventBundle]:
        ev = self._events.get(id)
        if not ev:
            return None
        # Demote previous live event back to setup
        if self._active_id and self._active_id != id:
            prev = self._events.get(self._active_id)
            if prev is not None:
                prev.mode = EventMode.SETUP
        self._active_id = id
        # First-time go-live: stamp + snapshot the plan for diff-rendering.
        if ev.mode != EventMode.LIVE:
            ev.snapshot_plan()
            if ev.started_at_live is None:
                ev.started_at_live = time.time()
        ev.mode = EventMode.LIVE
        return ev

    def current(self) -> Optional[EventBundle]:
        if self._active_id:
            return self._events.get(self._active_id)
        return None

    @property
    def active_id(self) -> Optional[str]:
        return self._active_id


# Module-level singleton — all events the agent knows about.
STATE = EventStore()
