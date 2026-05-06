"""event-config.yaml loader — seeds the default event into STATE on startup.

The YAML file is the boot-time default: it defines one event with its plan,
risks, goals, wishes and metadata. The loader creates this as a new
EventBundle inside the EventStore and activates it (so the server has a
live event to coordinate against from the first request).

Additional events can be created at runtime via the /events* endpoints.

Usage:
    from skill.config import load_event_config
    cfg = load_event_config()  # creates + activates the default event,
                               # returns parsed config for frontend metadata
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from skill.state import (
    STATE,
    EventBundle,
    EventMode,
    Goal,
    PlanItem,
    Risk,
    Role,
    Severity,
    Wish,
    audit,
)


CONFIG_PATH = Path(__file__).resolve().parent.parent / "event-config.yaml"

_cached: dict[str, Any] | None = None


def seed_event_from_dict(ev: EventBundle, cfg: dict[str, Any]) -> EventBundle:
    """Populate an EventBundle's stores from a parsed YAML/JSON config dict.

    Reused by both the boot-time loader and the runtime /events/{id}/import
    endpoint, so URL/Paste/YAML imports go through the same seeding code path.
    """
    event_meta = cfg.get("event") or {}
    if event_meta.get("name"):
        ev.name = event_meta["name"]
    if event_meta.get("scenario"):
        ev.scenario = event_meta["scenario"]
    if event_meta.get("countdown_to"):
        ev.countdown_to = event_meta["countdown_to"]
    if event_meta.get("areas"):
        ev.areas = list(event_meta["areas"])

    # view_modes: LLM-suggested per import, or supplied in YAML at top level / under event.
    vm = cfg.get("view_modes") or event_meta.get("view_modes")
    if vm:
        ev.view_modes = list(vm)

    for item in cfg.get("plan") or []:
        ev.plan.add(
            PlanItem(
                day=item.get("day"),
                time=item["time"],
                what=item["what"],
                who=[Role(r) for r in item.get("who", [])],
                where=item.get("where"),
                track=item.get("track"),
                tags=list(item.get("tags") or []),
            )
        )

    for r in cfg.get("risks") or []:
        ev.risks.add(
            Risk(
                id=r["id"],
                name=r["name"],
                description=r["description"],
                pattern=r["pattern"],
                threshold=r["threshold"],
                fanout=[Role(x) for x in r.get("fanout", [])],
                severity=Severity(r["severity"]),
            )
        )

    for g in cfg.get("goals") or []:
        ev.goals.add(
            Goal(
                text=g["text"],
                driver_for=[Role(x) for x in g.get("driver_for", [])],
            )
        )

    for w in cfg.get("wishes") or []:
        ev.wishes.add(
            Wish(
                text=w["text"],
                holder_roles=[Role(x) for x in w.get("holder_roles", [])],
            )
        )

    return ev


def load_event_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read the default-seed YAML, create + activate an event from it.

    Returns the parsed config so callers can read stakeholders.display_names
    and stakeholders.distribution metadata for the frontend.
    """
    global _cached
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    event_meta = cfg.get("event") or {}
    ev = STATE.create(
        name=event_meta.get("name") or "Default Event",
        mode=EventMode.LIVE,
        scenario=event_meta.get("scenario", ""),
        countdown_to=event_meta.get("countdown_to", ""),
        areas=event_meta.get("areas") or [],
    )
    seed_event_from_dict(ev, cfg)

    audit(
        "config_loaded",
        path=str(path),
        event_id=ev.id,
        event_name=ev.name,
        plan_items=len(ev.plan.list()),
        risks=len(ev.risks.list()),
        goals=len(ev.goals.list()),
        wishes=len(ev.wishes.list()),
    )
    _cached = cfg
    return cfg


def get_config() -> dict[str, Any]:
    if _cached is None:
        return load_event_config()
    return _cached
