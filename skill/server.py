"""
LiveTicker skill — minimal reference implementation.

This is a thin FastAPI server that exposes the five capabilities declared in
skill/manifest.yaml. It is intentionally small: the goal is to demonstrate
the skill shape, not to ship a polished product.

Run:
    python -m liveticker.server
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, Form
from pydantic import BaseModel

DB = Path("liveticker.db")
MEDIA = Path("media")
MEDIA.mkdir(exist_ok=True)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.execute(
        """CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            ts INTEGER,
            author TEXT,
            team TEXT,
            text TEXT,
            category TEXT,
            attachments TEXT
        )"""
    )
    return c


def classify(text: str) -> str:
    """Tiny rule-based classifier — replace with an LLM call in production."""
    t = text.lower()
    if any(w in t for w in ("?", "how do", "anyone know")):
        return "question"
    if any(w in t for w in ("stuck", "blocked", "broken", "fail")):
        return "blocker"
    if any(w in t for w in ("shipped", "merged", "done", "milestone")):
        return "milestone"
    if any(w in t for w in ("demo", "showing", "live")):
        return "demo"
    return "social"


app = FastAPI(title="LiveTicker Skill")


class PostOut(BaseModel):
    id: str


@app.post("/ticker/post", response_model=PostOut)
async def post(
    text: str = Form(...),
    author: str = Form(...),
    team: Optional[str] = Form(None),
    files: list[UploadFile] = [],
):
    item_id = uuid.uuid4().hex
    saved = []
    for f in files:
        dest = MEDIA / f"{item_id}_{f.filename}"
        dest.write_bytes(await f.read())
        saved.append(str(dest))
    with _conn() as c:
        c.execute(
            "INSERT INTO items VALUES (?,?,?,?,?,?,?)",
            (
                item_id,
                int(time.time()),
                author,
                team,
                text,
                classify(text),
                ",".join(saved),
            ),
        )
    return PostOut(id=item_id)


@app.get("/ticker/feed")
def feed(team: Optional[str] = None, category: Optional[str] = None):
    q = "SELECT * FROM items WHERE 1=1"
    args: list = []
    if team:
        q += " AND team=?"
        args.append(team)
    if category:
        q += " AND category=?"
        args.append(category)
    q += " ORDER BY ts DESC LIMIT 200"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return {"items": rows}


@app.get("/ticker/ask")
def ask(question: str):
    """Stub: in production this calls an LLM with the feed as context."""
    with _conn() as c:
        rows = c.execute("SELECT text, author, team FROM items").fetchall()
    return {
        "answer": f"(stub) Would answer '{question}' using {len(rows)} feed items.",
        "sources": [r[0] for r in rows[:5]],
    }


@app.get("/ticker/digest")
def digest(window_minutes: int = 30):
    cutoff = int(time.time()) - window_minutes * 60
    with _conn() as c:
        rows = c.execute(
            "SELECT category, COUNT(*) FROM items WHERE ts>=? GROUP BY category",
            (cutoff,),
        ).fetchall()
    parts = [f"{n} {cat}" for cat, n in rows] or ["nothing yet"]
    return {"summary": f"Last {window_minutes} min: " + ", ".join(parts) + "."}


@app.get("/ticker/recap")
def recap():
    with _conn() as c:
        rows = c.execute(
            "SELECT team, category, COUNT(*) FROM items GROUP BY team, category"
        ).fetchall()
    md = "# Event recap\n\n"
    for team, cat, n in rows:
        md += f"- **{team or 'unaffiliated'}** — {n} × {cat}\n"
    return {"markdown": md, "media": [str(p) for p in MEDIA.iterdir()]}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8765)
