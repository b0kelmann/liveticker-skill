# LiveTicker — An OpenClaw Skill for Live Events

> An agentic co-host for hackathons, conferences and meetups.
> It watches the room, helps participants when they are stuck, and broadcasts
> the most interesting moments to the outside world — autonomously.

**Submitted to:** GOSIM Agentic Hackathon 2026 — *OpenClaw track*
**Theme:** *Go Upstream* — every skill flows back into the open-source community.

---

## The pitch

Every live event has the same problem: the most interesting things happen
in pockets, and nobody outside the room ever sees them. Hosts miss who is
stuck, participants miss demos two tables over, and sponsors / press /
families on the outside see nothing until the post-event recap — which
arrives too late.

LiveTicker is an OpenClaw skill that turns any agent into an event
co-host. Participants and hosts post raw updates — text, images, code,
links — and three agent loops work on top of that feed:

1. **Auto-Broadcaster** *(the hero feature)* — an outward-facing loop that
   watches the feed and autonomously publishes the most newsworthy moments
   to the outside world: short clips for social, a live public page for
   families and sponsors, and a periodic curated brief for press.
2. **Bottleneck-Detector** *(host loop)* — spots clusters of pain
   (three teams blocked on the same library, a session running long, a
   silent team) and proposes interventions to the host.
3. **Smart Helper** *(participant loop)* — when someone posts "stuck on X",
   it searches the feed for someone who already solved it and brokers an
   intro — without the host having to play switchboard.

Because it ships as an OpenClaw skill, all three loops are **portable**:
any agent that speaks the OpenClaw skill protocol can host them, and any
*other* OpenClaw skill (calendar, notification, social) can be composed in.

## What makes it agentic

The skill itself is a tool surface; the agentic behaviour lives in three
loops that **plan, decide, and act** on top of it:

| Loop                | Senses                              | Decides                                        | Acts via                                          |
|---------------------|-------------------------------------|------------------------------------------------|---------------------------------------------------|
| Auto-Broadcaster    | new posts, media, milestones        | which moments are newsworthy + how to frame    | social-post skill, public-page skill, email skill |
| Bottleneck-Detector | blocker frequency, silence, clocks  | when to escalate + to whom                     | notification skill, calendar skill                |
| Smart Helper        | questions, prior solutions          | who to connect to whom                         | DM skill, ticker.post                             |

None of these are if-this-then-that rules — each loop runs on the agent's
LLM with the feed as context, calls tools when it needs them, and writes
its reasoning to the audit log so a human can review.

## Auto-Broadcaster in detail

The piece I'd want a reviewer to focus on.

**Inputs**
- the live feed (`ticker.feed`)
- a "voice" config (tone, hashtags, accounts to mention, blocked words)
- a publishing budget ("at most 4 posts/hour", "no more than 1 per team")

**Loop**
1. Every N minutes, fetch new feed items since last run.
2. Score each item for newsworthiness: novelty, media quality, milestone
   weight, breadth (does it interest people outside the room?).
3. If the top item passes threshold *and* the budget allows, draft a
   public post — copy + alt text + which media to attach.
4. Run a self-check: is the team OK with being broadcast? (consent flag
   on post). Is the copy on-voice? Does it duplicate something we already
   posted?
5. Emit the post via whichever output skill is configured: social, public
   page, email digest. All output skills are pluggable — swap in your own.

**Why this is "Go Upstream"**: the broadcaster is one skill, but it
publishes to many channels. Other events adopt it, they get their voice
config and channels for free; their improvements flow back upstream into
the skill.

## Architecture

```
       ┌──────────────────────────────────────────┐
       │           OpenClaw agent runtime         │
       │  ┌──────────┐ ┌──────────┐ ┌──────────┐  │
       │  │  Auto-   │ │Bottleneck│ │  Smart   │  │
       │  │Broadcast │ │ Detector │ │  Helper  │  │
       │  └────┬─────┘ └────┬─────┘ └────┬─────┘  │
       └───────┼────────────┼────────────┼────────┘
               │  capability calls       │
       ┌───────▼────────────▼────────────▼────────┐
       │            LiveTicker skill              │
       │  post · feed · ask · digest · recap      │
       └──────────────────┬───────────────────────┘
                          │
                  ┌───────▼────────┐
                  │  Event store   │
                  │ (SQLite + FS)  │
                  └────────────────┘
```

The skill exposes five capabilities; the loops above are agent programs
that compose them with other OpenClaw skills (social, calendar, mail).

| Capability       | Description                                               |
|------------------|-----------------------------------------------------------|
| `ticker.post`    | Post an update (text + optional attachments + consent).   |
| `ticker.feed`    | Stream the live feed, optionally filtered.                |
| `ticker.ask`     | Natural-language query over the feed.                     |
| `ticker.digest`  | Periodic summary for any audience.                        |
| `ticker.recap`   | End-of-event report with media gallery.                   |

Full schema in [`skill/manifest.yaml`](skill/manifest.yaml).

## Three perspectives, one feed

**Host:** the Bottleneck-Detector pings them when intervention is useful
(*"Team Pangolin has been silent for 90 min and posted a blocker earlier
— want me to ask if they're OK?"*) and the Auto-Broadcaster handles the
sponsor/press updates they'd otherwise have to write themselves.

**Participant:** they post freely, the Smart Helper finds them help when
they're stuck, and at the end they get a personal recap (their posts,
who they helped, what they shipped).

**Outside world (sponsors, families, press, social followers):** they see
the event live without anyone manually translating it. The
Auto-Broadcaster is the bridge — curated, on-voice, consent-aware.

## Quick start

```bash
git clone https://github.com/b0kelmann/liveticker-skill
cd liveticker-skill
pip install -r requirements.txt
python -m skill.server
```

Then point your OpenClaw agent at `http://localhost:8765/skill.json` and
enable any of the three loops in your agent config. A reference
Auto-Broadcaster loop is in [`skill/loops/broadcaster.py`](skill/loops/broadcaster.py).

A two-minute walk-through is in [`docs/demo.md`](docs/demo.md), and the
demo video is at [`docs/demo-video.md`](docs/demo-video.md).

## Possible directions covered

- **Practical skills for families and teams** — the core use case.
- **Cross-platform agent capabilities** — manifest follows the portable
  skill spec; loops are pluggable across agent frameworks.
- **Security and permission management** — per-event ACL, per-post
  consent flag (broadcaster only publishes posts marked public),
  full audit log of every agent action.
- **Creative skills** — the Auto-Broadcaster's framing layer is itself a
  creative-writing tool that can be retargeted (event recap → blog post,
  internal feed → newsletter).

## Roadmap (post-hackathon)

- Octos integration as a first-class OS-level service.
- Federated tickers — multiple events sharing a track view.
- Speech-to-ticker for hands-free posting during demos.
- Visual auto-broadcaster: short clip generation from media + caption.

## License

Apache-2.0 — same spirit as the rest of the OpenClaw ecosystem.

## Author

Built for GOSIM Paris 2026. Contact: [startplatz@tom-bockisch.de](mailto:startplatz@tom-bockisch.de) · [@b0kelmann](https://github.com/b0kelmann).
