# Demo walk-through

The 3-minute story we tell in the video. Each step is reproducible from
your terminal.

## Setup

```bash
pip install -r requirements.txt
python -m skill.server      # in one terminal
```

## Scenario: a hackathon room with three teams and an Auto-Broadcaster

**1. A participant posts a blocker (private).**

```bash
curl -F text="stuck on cuda OOM, anyone got 8GB to spare?" \
     -F author=alex -F team=pangolin -F consent_public=false \
     http://localhost:8765/ticker/post
```

The Smart Helper loop sees this and silently scans the feed — nobody else
has solved it yet, so it does nothing.

**2. A second team posts a milestone with a screenshot (public).**

```bash
curl -F text="we shipped the agent loop, demo at 4pm" \
     -F author=mira -F team=narwhal -F consent_public=true \
     -F files=@screenshot.png \
     http://localhost:8765/ticker/post
```

`consent_public=true` is the gate — the Auto-Broadcaster will only ever
consider items the author marked public.

**3. Auto-Broadcaster tick (runs every 5 min in production).**

```bash
python -m skill.loops.broadcaster
```

The loop fetches new feed items, asks the LLM which (if any) is
newsworthy, drafts a post, runs the budget self-check, and emits. Out of
the box the `emit` function just prints — swap in a social-post skill,
public-page skill, or email-digest skill as needed.

**4. A third team also asks about CUDA — Smart Helper now has a match.**

```bash
curl -F text="how did you get past cuda OOM?" \
     -F author=jordan -F team=koala -F consent_public=false \
     http://localhost:8765/ticker/post
```

Smart Helper notices the same theme as Alex's earlier blocker and brokers
an introduction — without involving the host.

**5. Host asks for a digest.**

```bash
curl "http://localhost:8765/ticker/digest?window_minutes=30"
```

**6. Bottleneck-Detector tick.**

If two more teams hit CUDA OOM within an hour, the Detector escalates to
the host: *"3 teams blocked on CUDA OOM in the last 45 min — want me to
ping a mentor or open a shared channel?"*

**7. End-of-event recap.**

```bash
curl http://localhost:8765/ticker/recap
```

Returns markdown plus the media gallery. Each participant also receives
a personalised version (their posts, who they helped, what they shipped).

## What the agent layer adds

The skill is deliberately thin. The OpenClaw agent on top adds:

- **Auto-Broadcaster** — autonomous outward-facing publishing.
- **Bottleneck-Detector** — proactive host alerts.
- **Smart Helper** — peer-to-peer matchmaking on questions.
- Cross-skill composition (e.g. "open a calendar slot when 5 teams are
  ready to demo").
