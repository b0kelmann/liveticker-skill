"""event-config.yaml loader — seeds STATE on FastAPI startup.

The YAML file is the single source of truth for the demo scenario:
plan items, risks, goals, stakeholder display names, role distribution.
Editing it (or hot-reloading during demo) reshapes the agent's worldview
without touching code.

Usage:
    from skill.config import load_event_config
    cfg = load_event_config()  # populates STATE.plan / risks / goals,
                               # returns parsed config for frontend metadata
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from skill.state import (
    STATE,
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


def load_event_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read YAML and seed STATE.plan / risks / goals.

    Returns the full parsed config so callers can read event.* and
    stakeholders.* metadata (display names, distribution) for the frontend.
    Cached after first call so /config doesn't re-read disk on every hit.
    """
    global _cached
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}

    for item in cfg.get("plan") or []:
        STATE.plan.add(
            PlanItem(
                time=item["time"],
                what=item["what"],
                who=[Role(r) for r in item.get("who", [])],
                where=item.get("where"),
            )
        )

    for r in cfg.get("risks") or []:
        STATE.risks.add(
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
        STATE.goals.add(
            Goal(
                text=g["text"],
                driver_for=[Role(x) for x in g.get("driver_for", [])],
            )
        )

    for w in cfg.get("wishes") or []:
        STATE.wishes.add(
            Wish(
                text=w["text"],
                holder_roles=[Role(x) for x in w.get("holder_roles", [])],
            )
        )

    audit(
        "config_loaded",
        path=str(path),
        plan_items=len(STATE.plan.list()),
        risks=len(STATE.risks.list()),
        goals=len(STATE.goals.list()),
        wishes=len(STATE.wishes.list()),
    )
    _cached = cfg
    return cfg


def get_config() -> dict[str, Any]:
    if _cached is None:
        return load_event_config()
    return _cached
