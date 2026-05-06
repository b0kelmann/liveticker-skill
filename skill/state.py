"""LiveTicker state stores — the agent's worldview.

Six explicit knowledge stores that together form the input-fusion architecture:

- PlanState:        what is *intended* to happen (the schedule)
- RealityState:     what is *actually* happening (incoming signals)
- RiskCatalog:      patterns we watch for in Reality (e.g. crowd-crush)
- Goals:            organizer-defined success criteria
- Wishes:           per-role stakeholder wishes — second reasoning axis ("whose
                    wishes are at risk?") alongside risk-threshold detection
- StakeholderGraph: who's at the event and in what role

The reasoning loop diffs Reality against Plan, scored against Risks and Wishes,
and routes outputs to relevant Stakeholders. Making these stores explicit (vs.
one big context blob) is what keeps the agent's reasoning explainable rather
than a black box.

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
    role: Role
    area: Optional[str] = None
    display_name: Optional[str] = None
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
        self, role: Optional[Role] = None, area: Optional[str] = None
    ) -> list[Stakeholder]:
        items = list(self._by_id.values())
        if role:
            items = [s for s in items if s.role == role]
        if area:
            items = [s for s in items if s.area == area]
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
    time: str  # "19:30" or ISO string — free-form, demo only
    what: str
    who: list[Role] = Field(default_factory=list)
    where: Optional[str] = None
    status: PlanItemStatus = PlanItemStatus.PLANNED
    notes: Optional[str] = None


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
        return sorted(self._items.values(), key=lambda x: x.time)


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

    Rendered live on the jury dashboard so the reasoning chain is visible
    rather than implied. Manifest declares audit.enabled = true.
    """
    record = {"ts": time.time(), "event": event, **fields}
    with AUDIT_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, default=str) + "\n")


# ---------- Bundle (the agent's worldview) ----------

@dataclass
class StateBundle:
    plan: PlanState = field(default_factory=PlanState)
    reality: RealityState = field(default_factory=RealityState)
    risks: RiskCatalog = field(default_factory=RiskCatalog)
    goals: Goals = field(default_factory=Goals)
    wishes: Wishes = field(default_factory=Wishes)
    stakeholders: StakeholderGraph = field(default_factory=StakeholderGraph)
    started_at: float = field(default_factory=time.time)

    def snapshot(self) -> dict:
        """Serialize the whole worldview — for dashboards and LLM prompts."""
        return {
            "started_at": self.started_at,
            "plan": [i.model_dump() for i in self.plan.list()],
            "reality_recent": [s.model_dump() for s in self.reality.recent(seconds=300)],
            "risks": [r.model_dump() for r in self.risks.list()],
            "goals": [g.model_dump() for g in self.goals.list()],
            "wishes": [w.model_dump() for w in self.wishes.list()],
            "stakeholder_counts": self.stakeholders.count_by_role(),
        }


# Module-level singleton — the running event's state.
# All HTTP handlers and the reasoning loop share this one bundle.
STATE = StateBundle()
