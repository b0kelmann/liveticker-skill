# LiveTicker — Working Context

> **For a fresh Claude Code session**: read this top-to-bottom before doing anything. It contains every settled decision, the current build state, and the next-action sketch. The pre-existing README.md is the public-facing pitch; this file is the internal working contract.

> **Status as of 2026-05-06:**
> - **Hackathon path is closed.** Tom missed the GOSIM live-demo cutoff and explicitly opted out of the submission-video path too — no Z.AI Innovation Award attempt, no stage demo, no submission to `create.gosim.org/submit`.
> - **Tool continues as a generic event-coordination tool**, not GOSIM-specific. Tom's framing: *"am Ende soll man das Tool auf jedes X beliebige Event anwenden können"*. The Setup-Phase becomes the heart: multiple source types (URL, files, manual) → LLM builds the source of truth.
> - **No deadline pressure.** This is now a personal tool / portfolio project. Build pace and direction are Tom's call.
> - **GOSIM-specific content** (Master Stage delay cascade, hackathon Day 1 schedule seed, fan/speaker/vendor wishes for that scenario) is **demoted from product-definition to example-event-seed**. Useful as a worked example, not the mission.

---

## TL;DR

LiveTicker is an event-coordination agent. It maintains explicit knowledge stores (Plan, Reality, Risks, Goals, Stakeholders, Wishes) and reasons across two axes — *which risks crossed threshold?* AND *whose wishes are at risk?* — to fan out targeted updates to the right stakeholders.

The vision: works for **any event** — conference, festival, wedding, meetup, hackathon. Setup is multi-source: paste a URL, drop schedule files, add catering PDFs, type free-form notes. The LLM reconciles these into a unified plan + risk catalog + per-role wish templates that the user reviews and edits before "go live".

Original anchor: built during/around the GOSIM Agentic Hackathon 2026 as an OpenClaw skill (runtime: GLM-5.1 via RouteTokens). The skill-shape and OpenClaw manifest still exist in code; whether to keep that wrapper or strip it for a standalone product is an open question.

Repo: `https://github.com/b0kelmann/liveticker-skill` · Public · Apache-2.0.

---

## Settled Architectural Decisions (the grilling output)

These were settled in a `/grill-me` session. Each has rationale that future sessions should respect unless the user explicitly revisits. They are still load-bearing for the *generic-tool* version — only #11 and #14 changed status (from product-defining to example-only), and #7-bis / #12 went dormant with the demo-pressure gone.

| # | Decision | Status | Rationale |
|---|---|---|---|
| 1 | **Aha-moment**: dynamic event coordination (NOT smart router, NOT auto-broadcaster) | active | Real pain is propagating delays/changes across many stakeholders simultaneously |
| 2 | **Tool type**: Coordination-Tool, not Schedule-Tool | active | Coordination is genuinely agentic; Schedule-tools already exist (Sched, Whova) |
| 3 | **Agent form**: Voll Input-Fusion (NOT Output-Router only, NOT Hybrid) | active | Build state-model + multi-stream watcher |
| 4 | **Architectural spine**: Plan State / Reality State / Risk Catalog (three explicit knowledge stores) | active | Makes agent reasoning explainable. LLM does the diff, structure makes it not-a-black-box |
| 5 | **Stakeholder Graph**: first-class citizen in the data model | active | Without it, no multi-stakeholder coordination possible |
| 6 | **Input channels**: 3 separate endpoints (`ticker.post`, `ticker.signal`, `ticker.ask`) feeding shared Reality State | active | Aligned with manifest.yaml capabilities. Permissions per channel are clearer than unified inbox |
| 7-bis | **Demo format**: Audience-as-System (E) — QR-driven roles | dormant | Was relevant for stage-demo; with no stage, parked. Can be revisited if/when there's an audience |
| 8 | **Goal granularity**: Stufe B (Mittel) — Goals + per-stakeholder-type Drivers in YAML, editable live | active | Stufe A felt hardcoded, Stufe C (live metrics dashboard) was scope-bloat |
| 11 | **Event scenario**: GOSIM Hackathon Day 1 (Tuesday) — Master Stage delay cascade | example-only | Was meta-resonant for the GOSIM jury; with no jury, this is just *one* example event the tool should work on |
| 12 | **Time frame in demo**: Frozen Moment with countdown | dormant | Demo-specific; relevant if/when a demo is recorded |
| 13 | **Scale framing**: Real-scale (use real numbers from the example event, not fictional inflation) | active | Real numbers > fictional inflation regardless of which event |
| 14 | **Killer-Moment**: Master Stage Delay Cascade | example-only | Genuine demo of the wishes-axis, but specific to a conference scenario; weddings/festivals have other killer-moments |
| 15 | **Wishes** as 6th knowledge store | active | Outcome-positive reasoning axis ("wishes at risk?") alongside defensive ("risk threshold met?") |
| 16 | **Demo is 2-part: Setup + Live** | promoted to product | The Setup-Phase is now central to the *product*, not just the demo. Multi-source ingestion is the hard interesting part of the generic version |
| 17 | **Source types for v1**: URL fetch + plain-text paste + YAML upload. PDF / CSV / images deferred. *(NEW 2026-05-06)* | active | URL was Tom's primary idea. Plain-text covers the realistic case (schedule email, Slack message, transcribed briefing). YAML upload is near-free since the internal format already is YAML. PDF/CSV/images add dependencies + parsing complexity without a concrete need yet. |
| 18 | **Source reconciliation**: one LLM call ingests all provided sources. LLM merges and returns plan items with per-item confidence + explicit `conflict_with` flags. Setup-UI surfaces conflicts; user resolves manually. *(NEW 2026-05-06)* | active | LLM is good at this kind of reconciliation; no Python merge code. Priority-order alternative (URL > File > Manual) silently auto-resolves and hides conflicts from the user — worse, since the user reviews everything anyway. |
| 19 | **Per-event-type configuration**: 3 built-in presets (Conference, Wedding, Festival) + "Custom blank" + LLM-suggested from source. *(NEW 2026-05-06)* | active | Presets give sane stakeholder roles + wish templates. Custom-blank covers everything else. The setup LLM call also classifies event-type and proposes roles + wishes from the extracted plan; user can accept the preset or override. **Implication**: the hardcoded `Role` enum in `state.py` must become configurable (per-event-type role lists) — this is the only non-trivial code change behind this decision. |
| 20 | **Skill wrapper vs. standalone**: skill-shape stays, but as optional. Standalone FastAPI server is the primary use case. *(NEW 2026-05-06)* | active | `manifest.yaml` is 5 lines, no maintenance overhead. Door stays open if anyone wants to use the tool inside an OpenClaw setup. README rewrite makes "standalone server" the primary positioning, "OpenClaw skill" a bonus. |
| 21 | **End-user frontends**: NO in v1. Admin UI is the only touchpoint. *(NEW 2026-05-06)* | active | Per-role frontends (speaker view, attendee view) are their own world: auth, push notifications, mobile, QR onboarding. First prove the coordination model with Wishes works via Admin UI + the existing `/inbox/{id}` endpoint. Frontend question is tagged open for v2. |

### Decisions previously open, now closed by the pivot

| Topic | Resolution |
|---|---|
| Hosting for live demo | n/a — no live demo |
| Audience-as-System in Live-Phase | n/a — no stage |
| Backstage-Plant inputs | n/a — no demo |
| Submission video tactics | n/a — no submission |
| GOSIM Day 1 schedule source | becomes "one example seed" — not blocking for the generic tool |
| GOSIM-context Risk seed | same — example-only |
| Wishes-per-role concrete content | becomes "one preset template" — generic version needs multiple presets |

---

## Current Code State

### Repo structure
```
liveticker-skill/
├── README.md              ← public pitch (8.6 KB) — written for hackathon framing, needs rewrite for generic version
├── CLAUDE.md              ← THIS FILE
├── LICENSE                ← Apache-2.0
├── .gitignore             ← excludes .env, .venv, audit.log
├── .env.example           ← LLM provider config template
├── .env                   ← LOCAL ONLY (gitignored), key for Paris Hackathon workspace (still works for now)
├── requirements.txt       ← fastapi, uvicorn, openai, python-dotenv, etc.
├── audit.log              ← LOCAL ONLY (gitignored), agent decisions log
├── docs/
│   ├── demo.md            ← (placeholder, pre-existing)
│   ├── demo-video.md      ← (placeholder, pre-existing — defunct now)
│   └── project-overview.html  ← visual project overview
├── examples/
│   └── run_broadcaster_demo.py  ← end-to-end demo script (broadcaster + real LLM)
└── skill/
    ├── __init__.py
    ├── manifest.yaml      ← OpenClaw skill capabilities (post/feed/ask/digest/recap)
    ├── server.py          ← FastAPI server shell
    ├── llm.py             ← LLM adapter — wraps OpenAI SDK against R9S endpoint
    └── loops/
        ├── __init__.py
        └── broadcaster.py ← Auto-Broadcaster reference loop
```

### Worktrees (4 total)
```
liveticker-skill/                       [main]                       primary
liveticker-skill-auto-broadcaster/      [loop/auto-broadcaster]      Loop 1 worktree
liveticker-skill-bottleneck-detector/   [loop/bottleneck-detector]   Loop 2 worktree (empty)
liveticker-skill-smart-helper/          [loop/smart-helper]          Loop 3 worktree (empty)
```

All under `~/Documents/STARTPLATZ/04_Plattform-Software/Repos/`. Symlinks: each loop worktree has `.env` and `.venv` symlinked to the main worktree.

### What works end-to-end (verified, as of 2026-05-06)
- LLM call returns from GLM-5.1 ✓
- Broadcaster demo runs against live LLM ✓
- **5 explicit knowledge stores** in `skill/state.py`: Plan, Reality, Risk, Goals, Stakeholders + STATE singleton + audit() helper ✓
- **3 input channels + read-side endpoints** in `skill/server.py`: `/join`, `/post`, `/signal`, `/ask`, `/state`, `/inbox/{id}`, `/config` ✓
- **Reasoning loop** in `skill/reasoning.py`: LLM-backed Reality-vs-Plan diff, risk-threshold detection, role-specific fanout ✓
- **`event-config.yaml` + loader** (`skill/config.py`) with FastAPI lifespan auto-seed on boot ✓ — currently still has the **old Tomorrowland seed**, slated for replacement (now: with *one preset of many*, not "the GOSIM seed")
- **Killer-moment smoketest** `python -m examples.run_killer_moment_demo` — end-to-end, real LLM. ⚠️ Latency: 30–90s per LLM call.
- **Self-test admin UI** at `GET /` (Jinja2 + HTMX, 2s auto-refresh): Plan/Risks/Goals/Reality/Stakeholders/Audit cards + quick-send forms + 3 demo presets. ✓
- **Background-task LLM dispatch** for UI flows so it stays responsive. ✓

### Backlog (no priority order — Tom decides when/if)

**Generic-tool work (the new direction):**
- Multi-source Setup endpoint(s): URL-fetch, file-upload (PDF/CSV/YAML at minimum), free-form text-paste
- LLM extraction pipeline: from any source → plan items + suggested risks + per-role wish templates
- Source reconciliation: merge / priority / conflict-flagging when multiple sources contradict
- Setup-UI: review/edit extracted plan, risks, wishes before "go live"
- Wishes data model: `Wish` Pydantic + `Wishes` store + per-stakeholder-role wish templates
- Wishes-aware reasoning extension: second axis "whose wishes are at risk?"
- Per-event-type presets: at least conference + festival + wedding as seed templates
- README rewrite: drop hackathon framing, position as generic event-coordination agent

**Carried over from the GOSIM-specific plan (now: example seed):**
- Master-stage-delay scenario seed for the conference-event preset
- Reschedule-cascade reasoning: when a plan item is delayed, cascade to downstream slots + identify wish conflicts

**Optional / dormant:**
- Bühnenscreen variant of admin UI
- Audience-as-System frontend (QR-landing → role-assignment) — only if Decision #7-bis becomes load-bearing again

---

## How to continue this work in a fresh Claude Code session

1. Open this CLAUDE.md (Claude Code reads it automatically when you start in this repo).
2. Read the Status block above first. Confirm with Tom whether the direction (generic tool, no deadline) is still current — pivots happen.
3. Check current commit: `git log --oneline -5` and `git status`.
4. Verify env: run the smoketest `python -c "from skill.llm import chat; print(chat([{'role':'user','content':'hi'}]))"` — if HTTP 402, the API key needs renewal.
5. **Default mode**: Tom is mode A (Claude codes, he reviews). Don't ask him to type code unless he explicitly switches modes.
6. **Don't re-grill** unless user explicitly asks. The architectural decisions above are settled (with #11/#14 now example-only and #7-bis/#12 dormant).
7. **Always sync changes to all 4 worktrees** when committing to main: `for loop in auto-broadcaster bottleneck-detector smart-helper; do git -C ../liveticker-skill-$loop merge main --ff-only && git -C ../liveticker-skill-$loop push; done`
8. **No deadline.** Don't manufacture urgency. Tom drives pace.

---

## Memory references (Tom's auto-memory)

- `~/.claude/projects/-Users-tom/memory/MEMORY.md` — index
- `project_startplatz_repos.md` — `liveticker-skill` listed there as a personal repo
- `feedback_check_memory_first.md` — read memory before fs scans

---

## Key file paths (absolute, copy-paste-ready)

```
Repo:           /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill
This file:      /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/CLAUDE.md
README:         /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/README.md
.env:           /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/.env
LLM adapter:    /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/skill/llm.py
Manifest:       /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/skill/manifest.yaml
Visual overview: /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/docs/project-overview.html
GitHub:         https://github.com/b0kelmann/liveticker-skill
```

---

## Historical context (kept for completeness)

The tool was originally built for the **GOSIM Agentic Hackathon 2026** (Paris, Station F), OpenClaw track, theme "Claws and Octos / Ecosystem Co-creation". Solo project by Tom Bockisch (b0kelmann). Runtime model: `glm-5.1` via RouteTokens (`https://api.r9s.ai/v1`), Workspace "Paris Hackathon", $40 in hackathon credits.

Tom missed the live-demo cutoff and opted out of the submission-video path on 2026-05-06. The tool continues as a personal/portfolio project, generalized beyond the GOSIM context.

Disclosure note: the pre-existing skill skeleton (`skill/loops/broadcaster.py`, `skill/manifest.yaml`, `skill/server.py`, the README's structural pitch) was drafted with Claude Code before the hackathon. The README still mentions this in "Pre-existing components and tools used"; that section can be simplified or removed when the README is rewritten for the generic version.
