"""Setup-Phase import pipeline (Decision #17, #18, #23).

Take a list of sources (URL / pasted text / YAML), reconcile them via a single
LLM call, and produce an event-config-shaped dict that can be fed through
config.seed_event_from_dict() into an EventBundle.

Source types:
    url    -- httpx GET. Auto-crawls the navigation: scores in-domain links
              by DE+EN keywords (schedule/programm/speaker/...) and follows
              up to ~8 pages across 2 depth levels so a single Conference
              homepage URL discovers Schedule + Speaker subpages.
    paste  -- free-form text (schedule emails, briefings, notes), to LLM as-is
    yaml   -- structured YAML in the event-config.yaml format, parsed locally
              and merged before/after the LLM call

If only `yaml` sources are provided, no LLM call is needed (fast path).
Otherwise: parse YAML sources locally, fetch + crawl URLs, and ask the LLM
to synthesize a unified event from all collected page contents (each labeled
with its source URL). The LLM also classifies the event_type and surfaces
conflicts.
"""
from __future__ import annotations

import json
import re
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
import yaml

from skill.llm import chat
from skill.state import audit


SourceType = Literal["url", "paste", "yaml"]
MAX_FETCH_BYTES = 400_000  # cap to keep prompt manageable
MAX_TEXT_PER_SOURCE = 30_000  # truncate long sources before sending to LLM
DEFAULT_MAX_CRAWL_PAGES = 8
DEFAULT_MAX_CRAWL_DEPTH = 2

# Two-letter ISO-ish lang codes that often prefix translation subtrees.
# We skip these unless the start URL itself is already inside one.
LANG_PATH_PREFIXES = (
    "/zh/", "/fr/", "/es/", "/de/", "/ja/", "/it/", "/pt/",
    "/ru/", "/ko/", "/nl/", "/pl/", "/cs/", "/tr/",
)

# Keywords for auto-crawl link scoring. DE + EN, lower-case.
# Two-tier weights: PRIMARY hints (schedule/agenda/tracks) score higher
# than SECONDARY hints (speakers/details), so a "Schedule" nav link wins
# over multiple featured "Speaker Profile" cards on the homepage.
#
# Score formula in score_link():
#   PRIMARY   in href: +5    in text: +3
#   SECONDARY in href: +2    in text: +1
PRIMARY_HINTS = [
    # schedule / agenda
    "schedule", "agenda", "programm", "programme", "programmplan",
    "ablauf", "zeitplan", "tagesplan", "timetable",
    # tracks / sessions / talks
    "tracks", "track", "sessions", "session", "talks", "talk",
    "vortrag", "vorträge", "lineup", "line-up",
]
SECONDARY_HINTS = [
    # speakers / presenters
    "speaker", "speakers", "sprecher", "referenten", "referent",
    "vortragende", "redner", "presenters", "presenter", "panelists",
    "keynote", "moderator", "moderation",
    # generic event detail subpages
    "details", "programm-details",
]
# Kept for backwards compatibility with older imports / tests.
POSITIVE_HINTS = PRIMARY_HINTS + SECONDARY_HINTS
# Negative hints — these in the URL or link-text mark a page to skip.
NEGATIVE_HINTS = [
    "impressum", "privacy", "datenschutz", "login", "logout", "register",
    "ticket", "tickets", "anmelden", "anmeldung", "sign-up", "signup",
    "sponsor", "sponsors", "sponsoren", "partner", "partners",
    "press", "presse", "media", "kontakt", "contact", "agb", "terms",
    "cookies", "newsletter", "subscribe", "abonnieren",
    "career", "careers", "karriere", "jobs",
    "shop", "store", "merchandise", "merch",
    "facebook.com", "twitter.com", "x.com", "instagram.com", "linkedin.com",
    "youtube.com", "mailto:", "tel:",
]


# ---------- HTML parsing helpers (regex-based, no extra deps) ----------

_A_TAG_RE = re.compile(
    r'<a\s+[^>]*href\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)


def extract_links(html: str) -> list[tuple[str, str]]:
    """Return [(href, link_text), ...] from an HTML body. Best-effort regex."""
    out: list[tuple[str, str]] = []
    for m in _A_TAG_RE.finditer(html):
        href = m.group(1).strip()
        text = _TAG_RE.sub("", m.group(2))  # strip nested tags from anchor text
        text = re.sub(r"\s+", " ", text).strip()
        if not href or href.startswith(("javascript:", "#")):
            continue
        out.append((href, text))
    return out


def score_link(href: str, link_text: str) -> int:
    """Score a candidate link 0..N. -1 means 'skip' (negative hit).

    Primary hints (schedule/agenda/tracks/sessions) outweigh secondary hints
    (speaker/keynote) so the Schedule page wins over a flood of featured
    speaker-profile cards on the homepage.
    """
    h = href.lower()
    t = link_text.lower()
    for neg in NEGATIVE_HINTS:
        if neg in h or neg in t:
            return -1
    score = 0
    for pos in PRIMARY_HINTS:
        if pos in h:
            score += 5
        if pos in t:
            score += 3
    for pos in SECONDARY_HINTS:
        if pos in h:
            score += 2
        if pos in t:
            score += 1
    return score


def _same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    if not pa.netloc or not pb.netloc:
        return False
    return pa.netloc.lower() == pb.netloc.lower()


def _normalize(url: str) -> str:
    """Strip fragment + trailing slash for dedup purposes."""
    base = url.split("#", 1)[0]
    return base.rstrip("/") or base


def _is_translation_path(candidate_url: str, start_url: str) -> bool:
    """True if candidate looks like a /<lang>/-prefixed translation subtree
    of a non-translated start URL. Skipping these avoids fetching the same
    content in multiple languages."""
    cand_path = urlparse(candidate_url).path or "/"
    start_path = urlparse(start_url).path or "/"
    for prefix in LANG_PATH_PREFIXES:
        if cand_path.startswith(prefix) and not start_path.startswith(prefix):
            return True
    return False


# ---------- URL fetching + HTML stripping ----------

def fetch_url_raw(url: str, timeout: float = 10.0) -> tuple[str, str]:
    """Fetch URL. Return (body_decoded, content_type). Caller decides if HTML."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.get(url, headers={"User-Agent": "LiveTicker-SetupImport/0.1"})
    r.raise_for_status()
    body = r.content[:MAX_FETCH_BYTES].decode(r.encoding or "utf-8", errors="replace")
    return body, r.headers.get("content-type", "").lower()


def fetch_url(url: str, timeout: float = 10.0) -> str:
    """Fetch a single URL and return text-stripped content. No crawling.

    Used internally by discover_and_fetch and as a primitive for callers
    that explicitly don't want auto-crawl.
    """
    body, ctype = fetch_url_raw(url, timeout=timeout)
    if "html" in ctype or "<html" in body.lower()[:512]:
        body = strip_html(body)
    return body[:MAX_TEXT_PER_SOURCE]


def discover_and_fetch(
    start_url: str,
    max_pages: int = DEFAULT_MAX_CRAWL_PAGES,
    max_depth: int = DEFAULT_MAX_CRAWL_DEPTH,
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    """BFS-crawl from start_url, scoring links by DE+EN schedule/speaker hints.

    Returns a list of pages: [{url, text, depth, score, link_text, content_type}].
    Caps total fetched pages at max_pages and traversal depth at max_depth.
    Stays on the start URL's host (no cross-domain crawl).

    The first page (start_url) is always included even if it scores low —
    the user explicitly pointed there.
    """
    seen: set[str] = set()
    queue: list[tuple[str, int, str, int]] = [(start_url, 0, "(start)", 999)]
    pages: list[dict[str, Any]] = []

    while queue and len(pages) < max_pages:
        cur_url, depth, link_text, score = queue.pop(0)
        norm = _normalize(cur_url)
        if norm in seen:
            continue
        seen.add(norm)

        try:
            body, ctype = fetch_url_raw(cur_url, timeout=timeout)
        except Exception as e:
            audit("discover_fetch_error", url=cur_url, error=str(e))
            continue

        is_html = "html" in ctype or "<html" in body.lower()[:512]
        text = strip_html(body) if is_html else body
        pages.append({
            "url": cur_url,
            "text": text[:MAX_TEXT_PER_SOURCE],
            "depth": depth,
            "score": score,
            "link_text": link_text,
            "content_type": ctype,
        })

        if depth >= max_depth or not is_html:
            continue

        # Score outbound links, dedup by normalized URL keeping the highest
        # score per unique URL (a single nav target may be linked 3-4 times
        # from one page — we don't want it filling up our top-N slot).
        per_url: dict[str, tuple[int, str, str]] = {}
        for href, lt in extract_links(body):
            absolute = urljoin(cur_url, href)
            absolute_norm = _normalize(absolute)
            if absolute_norm in seen:
                continue
            if not _same_origin(start_url, absolute):
                continue
            if _is_translation_path(absolute, start_url):
                continue
            s = score_link(absolute, lt)
            if s <= 0:
                continue
            existing = per_url.get(absolute_norm)
            if existing is None or s > existing[0]:
                per_url[absolute_norm] = (s, absolute, lt)

        scored = sorted(per_url.values(), key=lambda x: x[0], reverse=True)
        # Width per depth: be generous at depth 0, narrower at depth 1+
        top_n = 4 if depth == 0 else 2
        for s, u, lt in scored[:top_n]:
            queue.append((u, depth + 1, lt or "(no text)", s))

    audit(
        "discover_done",
        start_url=start_url,
        pages_fetched=len(pages),
        page_urls=[p["url"] for p in pages],
    )
    return pages


_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    """Best-effort HTML→text. Drops <script>/<style>, strips tags, collapses
    whitespace. Not as good as BeautifulSoup but no extra dependency."""
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    # Decode common HTML entities — keep it small, the LLM handles the rest.
    text = (
        text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
    )
    lines = [_WHITESPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return _BLANK_LINES_RE.sub("\n\n", text)


# ---------- LLM extraction ----------

_IMPORT_SYSTEM = """\
You build a structured event-coordination config from one or more raw sources
(URLs scraped to text, pasted notes, or YAML blocks). Your job: synthesize a
single coherent event, surface conflicts between sources, classify the event
type, and propose realistic per-role wishes and risks for that event.

Stakeholder roles MUST be drawn from this fixed set:
    fan, artist, stage_tech, security, medic, vendor, organizer
(The role taxonomy is conference/festival-flavored today; treat "fan" as
"attendee", "artist" as "speaker/performer", "stage_tech" as "AV/tech crew",
"vendor" as "exhibitor/catering", "organizer" as "host/coordinator", "medic"
+ "security" only when applicable.)

Return a single JSON object — NO prose, NO markdown fences — with this shape:

{
  "event": {
    "name": "<short title>",
    "scenario": "<1-2 sentence scenario at the moment the agent goes live>",
    "countdown_to": "<the next anchor moment, or empty string>",
    "areas": ["<distinct location/zone names>"]
  },
  "event_type": "conference|festival|wedding|meetup|workshop|corporate|other",
  "view_modes": ["<chosen subset of: chronological, by_day, by_track, by_where>"],
  "plan": [
    {
      "day": "<optional: 'Day 1' / 'Day 2' / ISO date / weekday — set ONLY when the event spans multiple days; OMIT otherwise>",
      "time": "HH:MM",
      "what": "<short>",
      "who": ["<role>", ...],
      "where": "<physical room/stage name, or empty>",
      "track": "<thematic schiene like 'Agentic AI Summit', or empty for single-track events>",
      "tags": ["<labels like 'keynote','workshop','panel','hackathon','break'>"]
    }
  ],
  "risks": [
    {
      "id": "<kebab-case slug>",
      "name": "<short>",
      "description": "<one sentence>",
      "pattern": "<natural-language pattern signals match>",
      "threshold": "<natural-language trigger condition>",
      "fanout": ["<role>", ...],
      "severity": "low|medium|high|critical"
    }
  ],
  "goals": [
    {"text": "<organizer success criterion>", "driver_for": ["<role>", ...]}
  ],
  "wishes": [
    {"text": "<outcome-positive wish phrased in stakeholder voice>", "holder_roles": ["<role>", ...]}
  ],
  "conflicts": [
    "<one short sentence per conflict between sources, e.g. 'URL says talk at 14:00, paste says 14:30'>"
  ],
  "notes": "<optional: anything non-obvious about how you reconciled the sources>"
}

Rules:
- Plan items must be in chronological order. If a source gives no times, infer
  reasonable times or use placeholder "00:00".
- Multi-day events: detect this from the sources (date ranges, "Day 1/Day 2",
  dates in headings). If multi-day, set `day` on EVERY plan item with a
  consistent label across items (pick one of: "Day 1"/"Day 2"/..., ISO dates
  like "2026-05-04", or weekday names — but stay consistent within an event).
  For single-day events, omit `day` entirely.
- Multi-track events (a conference with parallel tracks like "Agentic AI Summit",
  "Open Source Robotics", "Workshops"): set `track` on every plan item that
  belongs to a track. Use the track name verbatim from the source. `track` is
  the *thematic* schiene; `where` is the *physical* room/stage. They differ —
  one track may move between rooms, one room may host multiple tracks.
- For single-track events (a wedding, workshop day), omit `track` on items.
- `tags`: short labels marking item *kind*: ["keynote"], ["workshop"],
  ["panel"], ["hackathon"], ["break"], ["lunch"], ["social"], ["registration"].
  Empty list if no useful label applies.
- `view_modes`: pick the subset of [chronological, by_day, by_track, by_where]
  that makes sense for THIS event's shape:
    - Always include "chronological" as a fallback.
    - Include "by_day" if and only if you set `day` on items.
    - Include "by_track" if and only if you set `track` on at least 2 distinct
      values across items.
    - Include "by_where" if and only if `where` has at least 2 distinct values
      (i.e. multiple rooms/stages — useful for festivals, multi-room confs).
  Order them by usefulness for this event (the first will be the default view).
- Propose 4-8 risks tailored to the event type. Don't invent generic festival
  risks if the event is e.g. a small workshop.
- Propose 4-10 wishes covering the main stakeholder roles for this event type.
- If sources clearly conflict, include both interpretations as separate plan
  items (or note the conflict in conflicts[]) — do not silently pick one.
- Use only roles from the fixed set above. Use empty arrays where unsure.
"""


def _build_extraction_prompt(sources: list[dict[str, str]]) -> str:
    """Wrap the sources in a clearly-labeled user message for the LLM."""
    lines = []
    for i, src in enumerate(sources, 1):
        lines.append(f"=== SOURCE {i} ({src['type']}) ===")
        if src["type"] == "url":
            lines.append(f"URL: {src.get('url', '?')}")
        body = src.get("text", "")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def extract_event_from_sources(
    sources: list[dict[str, str]],
) -> dict[str, Any]:
    """Run the import LLM call. Returns parsed JSON dict (or raises).

    `sources` is a list of dicts each with keys:
        type:  "url" | "paste" | "yaml"
        url:   present if type == "url" (for prompt context)
        text:  the body content already fetched/parsed/stripped
    """
    user_payload = _build_extraction_prompt(sources)
    raw = chat(
        [
            {"role": "system", "content": _IMPORT_SYSTEM},
            {"role": "user", "content": user_payload},
        ]
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        audit("import_parse_error", error=str(e), raw=raw[:500])
        raise


def parse_yaml_source(text: str) -> dict[str, Any]:
    """Parse a YAML block; tolerate either an event-config.yaml or a bare dict."""
    parsed = yaml.safe_load(text) or {}
    if not isinstance(parsed, dict):
        raise ValueError("YAML source did not parse to a mapping at the top level")
    return parsed


# ---------- Stakeholder bulk extraction (Phase 1B of decision #21 v2) ----------

# Default mapping from human-readable category labels to the abstract Role
# enum (which drives reasoning-loop fanout). Uses lowercase substring match —
# unknown categories fall back to "organizer".
CATEGORY_TO_ROLE = [
    # (substring patterns, role-name)
    (("speaker", "vortragend", "referent", "presenter", "moderator", "host"), "artist"),
    (("av", "stage", "lighting", "sound", "tech", "ton", "licht"), "stage_tech"),
    (("security", "bouncer", "ordner", "crowd"), "security"),
    (("medic", "first aid", "sanit", "arzt"), "medic"),
    (("catering", "food", "drink", "vendor", "barkeeper", "küche", "kueche", "bar"), "vendor"),
    (("garderobe", "wardrobe", "coat"), "vendor"),
    (("organizer", "veranstalter", "coordinator", "lead", "manager", "produktion"), "organizer"),
    (("volunteer", "helfer", "assistant"), "organizer"),  # volunteers usually coordinate, no separate enum yet
    (("attendee", "teilnehmer", "guest", "fan"), "fan"),
    (("press", "presse", "media", "journalist"), "organizer"),
    (("sponsor", "supporter", "partner"), "vendor"),
]


def category_to_role(category: Optional[str]) -> str:
    """Map a free-form category label to a Role enum value. Defaults to 'organizer'."""
    if not category:
        return "organizer"
    c = category.lower()
    for patterns, role in CATEGORY_TO_ROLE:
        if any(p in c for p in patterns):
            return role
    return "organizer"


def parse_csv_stakeholders(text: str) -> list[dict[str, Any]]:
    """Parse a CSV/TSV/semicolon-delimited table to stakeholder dicts.

    First non-empty row is the header. Recognized columns (case-insensitive):
      name, email, category, role, area, notes, topic
    Unknown columns are merged into `notes`. Rows with no name are skipped.
    """
    import csv
    import io

    text = text.strip()
    if not text:
        return []

    # Detect delimiter from the header line.
    first_line = text.splitlines()[0]
    delim = ";" if first_line.count(";") > first_line.count(",") else ","
    if "\t" in first_line and first_line.count("\t") >= max(first_line.count(","), first_line.count(";")):
        delim = "\t"

    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    out: list[dict[str, Any]] = []
    for row in reader:
        # Normalize keys: lowercase + strip
        norm = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items() if k}
        name = norm.get("name") or norm.get("display_name") or norm.get("display name") or ""
        if not name:
            continue
        entry: dict[str, Any] = {
            "display_name": name,
            "email": norm.get("email") or None,
            "category": norm.get("category") or norm.get("kategorie") or norm.get("function") or None,
            "role": norm.get("role") or None,  # may stay None → mapped from category
            "area": norm.get("area") or norm.get("bereich") or None,
            "notes": norm.get("notes") or norm.get("notiz") or norm.get("topic") or None,
        }
        out.append(entry)
    return out


_STAKEHOLDER_EXTRACT_SYSTEM = """\
You extract a list of event stakeholders (crew, helpers, speakers, etc.) from a
loosely-formatted source: pasted text, an emailed roster, copy-pasted PDF/Word
content, or similar. The user will hand-edit the result, so prefer recall over
precision — list everyone who looks like a stakeholder, even if details are missing.

Return a single JSON object — NO prose, NO markdown fences — with this shape:

{
  "stakeholders": [
    {
      "display_name": "<full name as it appears in source>",
      "email": "<email if present, else null>",
      "category": "<human function label: 'Speaker', 'Volunteer', 'AV-Tech', 'Catering', 'Garderobe', 'Security', 'Medical', 'Organizer', or any other label that fits the source>",
      "area": "<location/zone if mentioned, else null>",
      "notes": "<topic, special needs, free-form remarks, or null>"
    }
  ],
  "notes": "<optional one-line overall comment, e.g. 'Two columns appeared to be mixed up; reconciled by name'>"
}

Rules:
- One entry per real person. Don't duplicate.
- `category` should be the source's own labeling where possible (verbatim if it
  uses 'Garderobe', use 'Garderobe'; verbatim if it uses 'Catering Crew', etc.).
- For pure 'Speaker XYZ — Topic: ABC' patterns, set category='Speaker' and put
  the topic in notes.
- Don't invent emails. If the source has no email column, set null.
- Skip rows that are obviously not people (headings, totals, blank rows).
"""


def extract_stakeholders_from_text(text: str) -> dict[str, Any]:
    """Single LLM call to extract a stakeholder list from free-form text."""
    raw = chat(
        [
            {"role": "system", "content": _STAKEHOLDER_EXTRACT_SYSTEM},
            {"role": "user", "content": text[:MAX_TEXT_PER_SOURCE]},
        ]
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        audit("stakeholder_extract_parse_error", error=str(e), raw=raw[:500])
        raise
