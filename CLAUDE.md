# LiveTicker — Hackathon Working Context

> **For a fresh Claude Code session**: read this top-to-bottom before doing anything. It contains every settled decision, the current build state, and the next-action plan. The pre-existing README.md is the public-facing pitch; this file is the internal working contract.

---

## TL;DR

LiveTicker is an **OpenClaw skill** for **real-time coordination at live events**. Solo project by **Tom Bockisch (b0kelmann)** for the **GOSIM Agentic Hackathon 2026 (Paris, OpenClaw track)**, runtime model **GLM-5.1 via RouteTokens** — targeting the **Z.AI Innovation Award ($2,000)**.

The architecture is **input-fusion-based**: an LLM-backed agent maintains three explicit knowledge stores (Plan State, Reality State, Risk Catalog), enriched by Goals and a Stakeholder Graph. Audience-volunteered questions (`ticker.ask`) double as crowdsourced sensors — questions about reality reveal drifts in the plan.

The demo is a **live audience-as-system** experience: ~30 jury/audience members scan a QR, get assigned festival roles (Tomorrowland-style, 19:30 before headliner), and play through a 2-minute scenario where their distributed signals trigger a **crowd-crush detection killer-moment** — Astroworld 2021 resonance.

Repo: `https://github.com/b0kelmann/liveticker-skill` · Public · Apache-2.0.

---

## Hackathon Context

- **Event**: GOSIM Agentic Hackathon 2026
- **Venue**: Station F, Paris
- **Track**: OpenClaw (Theme: "Claws and Octos / Ecosystem Co-creation")
- **Team size**: solo (max 3 allowed; Tom decided to stay solo)
- **Runtime model**: `glm-5.1` via RouteTokens (`https://api.r9s.ai/v1`)
  - $40 in hackathon credits (Workspace: "Paris Hackathon")
  - API key in `.env`, multi-account history (use the one tied to RouteTokens workspace `Paris Hackathon`)
- **Judging** (5 dimensions × 20% each, 5-point scale, trimmed mean):
  1. Innovation
  2. Technical Depth
  3. Completeness
  4. Practicality
  5. Presentation
- **Demo format**: 3 min demo + 2 min Q&A, top 10 advance if >10 teams complete
- **Submission required**: GitHub URL, 3-5 min demo video, README, OSI license — submitted at `create.gosim.org/submit`
- **Awards** (top 5):
  - Top 3 → Sponsor Awards ($2k each, sponsor matched to model used)
  - 4th + 5th → GOSIM Awards ($1k + $1k Kimi tokens each)
  - **Tom's path: top 3 → Z.AI Innovation Award (because he uses GLM)**

### Disclosure obligations (Rules §5 + §13)

The pre-existing skill skeleton (`skill/loops/broadcaster.py`, `skill/manifest.yaml`, `skill/server.py`, the README's structural pitch) was drafted with Claude Code BEFORE the hackathon. This is disclosed in `README.md` under "Pre-existing components and tools used". Build during the hackathon must be substantial — the disclosure section needs to grow as more code is built (with commit-history references).

---

## Settled Architectural Decisions (the grilling output)

These were settled in a `/grill-me` session. Each has rationale that future sessions should respect unless the user explicitly revisits.

| # | Decision | Rationale |
|---|---|---|
| 1 | **Aha-moment**: dynamic event coordination (NOT smart router, NOT auto-broadcaster) | User reframed: real pain is propagating delays/changes across many stakeholders simultaneously |
| 2 | **Tool type**: Coordination-Tool, not Schedule-Tool | Coordination is genuinely agentic; Schedule-tools already exist (Sched, Whova) |
| 3 | **Agent form**: Voll Input-Fusion (NOT Output-Router only, NOT Hybrid) | User has 2 days; chose ambition. Implication: build state-model + multi-stream watcher |
| 4 | **Architectural spine**: Plan State / Reality State / Risk Catalog (three explicit knowledge stores) | Makes agent reasoning explainable to jury. LLM does the diff, structure makes it not-a-black-box |
| 5 | **Stakeholder Graph**: first-class citizen in the data model from Day 1 | Without it, no multi-stakeholder demo possible. Refactor later is half a day of pain |
| 6 | **Input channels**: 3 separate endpoints (`ticker.post`, `ticker.signal`, `ticker.ask`) feeding shared Reality State | Aligned with manifest.yaml capabilities. Permissions per channel are clearer than unified inbox |
| 7-bis | **Demo format**: Audience-as-System (E) — QR scan → role assigned → audience drives the demo with live inputs | Solves the "solo on stage doing puppet show" problem. ~30 attendees become live sensors |
| 8 | **Goal granularity**: Stufe B (Mittel) — Goals + per-stakeholder-type Drivers in YAML, organizer can edit live in demo | Stufe A felt hardcoded (kills Innovation), Stufe C (live metrics dashboard) was +1 day cost not worth it |
| 11 | **Event scenario**: Festival, Tomorrowland-style (Day 2, 19:30, before headliner) | Marathon felt too small/linear; Festival has natural multi-stakeholder vielfalt + Astroworld stakes |
| 12 | **Time frame in demo**: Frozen Moment with countdown ("5 min before Anna...err, headliner arrives") | Drama via countdown + 1:1 mapping demo-time-to-story-time. No mental compression load on audience |
| 13 | **Scale framing**: Big-Frame (claim Tomorrowland-real-scale numbers, demo a small subset) | Audience demo is necessarily small; framing big asserts production scope |
| 14 | **Killer-Moment**: Crowd-Crush Detection (Astroworld pattern) — multi-stakeholder fanout from aggregated fan signals | Maximum stakes, maximum multi-channel demo, moral-resonance differentiates from competing demos |

### Decisions still open

| Topic | Status | Recommended default if unresolved |
|---|---|---|
| Frontend tech stack | TBD | Plain HTML + vanilla JS, served by FastAPI; alternative: Telegram Bot |
| Hosting for live demo | TBD | ngrok (works but venue WiFi risk); fallback Vercel/Railway |
| Backstage-Plant inputs (yes/no) | TBD | Yes, as safety net — Tom backstage with extra device. ~3 plants reserved for killer-moment trigger if audience hesitant |
| Submission video tactics | TBD | Record AT desktop with Tom playing 4 roles in 4 browser windows (controlled "perfect run"); LIVE demo is audience-as-system bonus |
| Festival Goals concrete content | TBD | Draft inline in `event-config.yaml`: ["no crowd-crush incidents", "<60s medical response", "headliner starts on time", "fans report enjoyment > 4/5"] |
| Festival Risk Catalog seed | TBD | Draft 5-7 entries: crowd-crush pattern, mic-failure, weather-drift, foodtruck-overload, headliner-delay, missing-person-cluster, fire-route-blocked |

---

## Current Code State

### Repo structure
```
liveticker-skill/
├── README.md              ← public pitch (8.6 KB)
├── CLAUDE.md              ← THIS FILE (working context)
├── LICENSE                ← Apache-2.0
├── .gitignore             ← excludes .env, .venv, audit.log
├── .env.example           ← LLM provider config template
├── .env                   ← LOCAL ONLY (gitignored), key for Paris Hackathon workspace
├── requirements.txt       ← fastapi, uvicorn, openai, python-dotenv, etc.
├── audit.log              ← LOCAL ONLY (gitignored), agent decisions log
├── docs/
│   ├── demo.md            ← (placeholder, pre-existing)
│   ├── demo-video.md      ← (placeholder, pre-existing)
│   └── project-overview.html  ← visual project overview (built alongside this CLAUDE.md)
├── examples/
│   └── run_broadcaster_demo.py  ← end-to-end demo script (broadcaster + real LLM)
└── skill/
    ├── __init__.py
    ├── manifest.yaml      ← OpenClaw skill capabilities (post/feed/ask/digest/recap)
    ├── server.py          ← FastAPI server shell (4 KB, pre-existing)
    ├── llm.py             ← LLM adapter — wraps OpenAI SDK against R9S endpoint
    └── loops/
        ├── __init__.py
        └── broadcaster.py ← Auto-Broadcaster reference loop (5.7 KB, pre-existing)
```

### Worktrees (4 total)
```
liveticker-skill/                       [main]                       primary
liveticker-skill-auto-broadcaster/      [loop/auto-broadcaster]      Loop 1 worktree
liveticker-skill-bottleneck-detector/   [loop/bottleneck-detector]   Loop 2 worktree (empty so far)
liveticker-skill-smart-helper/          [loop/smart-helper]          Loop 3 worktree (empty so far)
```

All under `~/Documents/STARTPLATZ/04_Plattform-Software/Repos/`. Symlinks: each loop worktree has `.env` and `.venv` symlinked to the main worktree (single source of truth for credentials and dependencies).

### What works end-to-end (verified)
- LLM call: `python -c "from skill.llm import chat; print(chat([{'role':'user','content':'hi'}]))"` returns from GLM-5.1 ✓
- Broadcaster demo: `python -m examples.run_broadcaster_demo` runs the broadcaster against the live LLM, with markdown-fence stripping fix and last-choice extraction (GLM returns reasoning at index 0, answer at last index) ✓

### What does NOT yet exist (the build backlog)
- 3 endpoints (`/post`, `/signal`, `/ask`) — only broadcaster's stubs exist
- Plan State store
- Reality State store
- Risk Catalog (file + reasoning integration)
- Goals + Stakeholder Taxonomy YAML
- Reasoning loop (the watcher that diffs Reality vs Plan against Risks/Goals)
- Output-Router (multi-channel fanout)
- Web frontend (QR scan + role assignment + per-role input/output UI)
- Bühnenscreen / Jury dashboard (live Plan/Reality/Risk view + audit log)
- Deployment setup (ngrok or alternative)
- Submission video
- Demo skill drilling

---

## Build Plan (settled in grilling Q15)

### Day 1 — Backend Substance
| Time | Action |
|---|---|
| now – 18:00 | Tech-stack decision + skeleton refactor: `skill/state.py` (Plan/Reality/Risk/Goals stores) |
| 18:00 – 21:00 | 3 endpoints (`/post`, `/signal`, `/ask`) wired to reasoning loop |
| 21:00 – 22:00 | `event-config.yaml` for Tomorrowland setting (goals, risks, taxonomy) |
| 22:00 – 23:00 | Smoke-test: 5-10 manual inputs → expected outputs |
| 23:00 | Hard stop. Sleep. |

**Day 1 point of no return**: 22:00. If backend isn't running by then, drop features in this order:
1. Goals live editor → static YAML only
2. Risk Catalog editor → static YAML only
3. Multi-channel fanout from 5 → 2 channels
4. One of 3 endpoints (`/post` is most droppable)
5. Stakeholder taxonomy from 7 → 3 roles

### Day 2 — Frontend + Demo
| Time | Action |
|---|---|
| 08:00 – 11:00 | Frontend: QR-scan landing → role assignment → input/output UI |
| 11:00 – 12:00 | Bühnenscreen dashboard (Plan/Reality/Risk + audit log + countdown) |
| 12:00 – 13:00 | Lunch + Discord post for real-user collection |
| 13:00 – 15:00 | Submission video recording (Tom plays 4 roles in 4 browser windows) + collection runs in background |
| 15:00 – 16:30 | Submission form: README polish, architecture diagram, video upload, GitHub URL submission |
| 16:30 – 18:00 | Demo drill: 5× live 3-min pitch, backstage plant rehearsal, timing fix |
| 18:00 – Demo | On-site setup, QR display, last-minute connectivity test |
| Demo slot | 3 min live + 2 min Q&A |

**Day 2 point of no return**: 15:00. If submission isn't out by then, deadline risk begins.

---

## Demo Specification

### Setup
- Big stage screen showing: project name, countdown ("04:47 until headliner"), Plan State (schedule), Reality State (initially empty), Risk Catalog (5-7 pre-loaded), Goals
- Big QR code on screen → audience scans
- Audience devices show: assigned role, role context (situation), free-text input field, agent reply view, push notifications when fanned-out

### Role distribution (for ~30 attendees)
- **50% Fans / Festival-goers** (15 people, distributed across stages/areas)
- **13% Artists / Crew** (4 people, including 1 headliner)
- **10% Stage Tech / Lighting** (3 people, per stage)
- **10% Security / Crowd Control** (3 people)
- **7% Medics** (2 people)
- **7% Foodtruck / Vendors** (2 people)
- **3% Organizer** (1 person, "Sarah" stand-in for goal-authoring)

### Beats (3 minutes)
- T=0:00 – 0:30 — QR scan, role assignment, brief intro
- T=0:30 – 1:00 — Audience tipping playful inputs (food, toilet, set-time questions)
- T=1:00 – 1:30 — Tonal shift: 1-2 fans report "crowded near front" (organic or backstage plant)
- T=1:30 – 1:50 — **Killer Moment**: agent aggregates crowd-crush signal → 5-channel fanout visible on stage screen (Security, Stage Manager, Medics, PA, Fans) → audit log shows full reasoning chain
- T=1:50 – 2:30 — Resolution: "cluster defused" status, real audience members see notifications on their devices
- T=2:30 – 3:00 — Architecture slide + closing pitch + GitHub URL

### Backup plan if killer moment doesn't fire
Backstage plant inputs (Tom on hidden device) — type 2-3 "fan crowded" signals to trigger threshold. Disclose to audience after demo if asked: "I had safety nets in case the audience didn't generate the right pattern in 2 min — but the system itself didn't know the difference, it processed each input on its merits".

---

## How to continue this work in a fresh Claude Code session

1. Open this CLAUDE.md (Claude Code reads it automatically when you start in this repo).
2. Confirm with the user where they are in the day plan (above).
3. Check current commit: `git log --oneline -5` and `git status`.
4. Verify env: run the smoketest `python -c "from skill.llm import chat; print(chat([{'role':'user','content':'hi'}]))"` — if HTTP 402, the API key needs renewal (see Hackathon Context).
5. Read the most recent change in `audit.log` (if exists) for context on last system run.
6. **Default mode**: Tom is mode A (I code, he reviews). Don't ask him to type code unless he explicitly switches modes.
7. **Don't re-grill** unless user explicitly asks. The 14 architectural decisions above are settled.
8. **Always sync changes to all 4 worktrees** when committing to main: `for loop in auto-broadcaster bottleneck-detector smart-helper; do git -C ../liveticker-skill-$loop merge main --ff-only && git -C ../liveticker-skill-$loop push; done`

---

## Memory references (Tom's auto-memory)

These memory files are relevant; future sessions should respect them:
- `~/.claude/projects/-Users-tom/memory/MEMORY.md` — index
- `project_startplatz_repos.md` — `liveticker-skill` is now listed there as a personal repo
- `feedback_check_memory_first.md` — read memory before fs scans

---

## Key file paths (absolute, copy-paste-ready)

```
Repo:           /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill
This file:      /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/CLAUDE.md
README:         /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/README.md
.env:           /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/.env
LLM adapter:    /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/skill/llm.py
Broadcaster:    /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/skill/loops/broadcaster.py
Manifest:       /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/skill/manifest.yaml
Demo example:   /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/examples/run_broadcaster_demo.py
Visual overview: /Users/tom/Documents/STARTPLATZ/04_Plattform-Software/Repos/liveticker-skill/docs/project-overview.html
GitHub:         https://github.com/b0kelmann/liveticker-skill
```
