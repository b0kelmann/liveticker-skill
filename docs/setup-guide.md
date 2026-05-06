# LiveTicker — Setup-Anleitung für Veranstalter

> Schritt-für-Schritt: Wie du dein Event mit LiveTicker aufsetzt und live koordinierst.

---

## Was LiveTicker ist (in zwei Sätzen)

LiveTicker ist ein Web-Tool, das vor dem Event mit Plan + Stakeholdern + Risiken befüllt wird, und während des Events eingehende Signale (von Speakern, Helfern, Teilnehmern) durch einen LLM-Agenten interpretiert. Der Agent identifiziert Probleme, gefährdete Wünsche und Plan-Cascades — und fanned passende Nachrichten an die richtigen Rollen aus, mit kontextspezifischer Formulierung pro Empfänger.

**Geeignet für:** Konferenzen (Single- + Multi-Track), Festivals, Hochzeiten, Workshops, Meetups, Corporate Events.

---

## Voraussetzungen

### Technisch
- Python 3.11+
- API-Key für [RouteTokens](https://routetokens.ai) als OpenAI-kompatibler Proxy zu Z.AI/GLM
- Browser auf Laptop oder Mobile (egal welcher)

### Inhaltlich — was du als Veranstalter mitbringen solltest

**Pflicht** (ohne das macht das Tool wenig Sinn):
- **Schedule** — entweder als öffentliche URL der Eventseite, als YAML-Datei, oder copy-pasted aus Mail/Schedule-Dokument
- **Stakeholder-Liste** — Namen + idealerweise Email + Funktion ("Speaker", "Volunteer", "Catering Crew" …)

**Sehr empfohlen:**
- **Räume oder Bereiche** — z.B. "Main Stage", "Workshop Room A", "Foyer". Diese werden später als Schauplatz für Signale verwendet.
- **Tag-Struktur** bei mehrtägigen Events — z.B. "Day 1 / Day 2 / Day 3" oder konkrete Daten.
- **Track-Aufteilung** bei Multi-Track-Konferenzen — z.B. "Agentic AI Summit", "Open Source Robotics" …

**Optional** (kann das Tool aus dem Plan ableiten — du kannst aber überschreiben):
- **Risiken**: was kann schiefgehen? ("Crowd Crush", "Mic Failure", "Headliner Delay")
- **Goals**: was sollte gelingen? ("Headliner startet pünktlich")
- **Wishes** pro Rolle: was wollen die Stakeholder? ("Headliner endet, bevor mein letzter Zug fährt")

---

## Software-Setup (einmalig)

```bash
# 1. Repo klonen + venv
git clone https://github.com/b0kelmann/liveticker-skill.git
cd liveticker-skill
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. .env konfigurieren
cp .env.example .env
# editiere .env mit deinen Werten:
#   LLM_BASE_URL=https://api.r9s.ai/v1
#   LLM_MODEL=glm-5.1
#   LLM_API_KEY=sk-...

# 3. Server starten
.venv/bin/uvicorn skill.server:app --host 127.0.0.1 --port 8765
```

Browser öffnen: **http://127.0.0.1:8765**

Beim ersten Start wird ein Default-Event ("Sample Festival — Headliner Slot Demo") aus `event-config.yaml` geladen und automatisch aktiviert. Das ist nur ein Beispiel — du legst gleich dein eigenes Event an.

---

## Workflow für ein neues Event — Schritt für Schritt

### Schritt 1: Event anlegen

In der **Event-Bar oben** das Textfeld neben "+ create empty" benutzen:
1. Eventnamen eintippen (z.B. `GOSIM Paris 2026`)
2. Klick auf `+ create empty`

Das Event wird im **`setup`-Mode** erstellt — es nimmt noch keine Signale an, ist aber auswählbar.

### Schritt 2: Auf das Event umschalten ("Go Live")

Im **"Active Event"-Dropdown** das neue Event auswählen → Klick auf `switch · go live`.

Was passiert dabei:
- Vorheriges Live-Event geht zurück auf `setup`
- Neues Event wird `live`
- **Plan-Snapshot wird gemacht** (alle Plan-Items kriegen `original_time` + `original_status` festgefroren — wichtig für die Diff-Anzeige später, wenn der Reasoning-Loop Items als `delayed` markiert)
- `started_at_live` Zeitstempel wird gesetzt
- Reasoning-Loop wird scharf

> **Tipp**: Du kannst Schritt 2 auch *vor* Schritt 3 (Plan-Import) machen. "Live" heißt nicht "Event hat schon begonnen", sondern "ich bearbeite gerade dieses Event". Das ist OK so — du kannst beim Live-Event noch importieren, editieren, Stakeholder hinzufügen.

### Schritt 3: Schedule + Risiken + Wishes importieren

In der **Setup-Card** ("Import sources into active event"), drei Pfade — wähle was zu deinen Daten passt. Du kannst auch mehrere kombinieren (z.B. URL + ergänzendes Paste mit Notizen).

#### Pfad A: URL (am bequemsten bei öffentlichen Eventseiten)

Eine URL pro Zeile in das oberste Textarea. Klick auf `Import & Synthesize`.

Was unter der Haube passiert:
1. **Auto-Crawl** holt die Hauptseite, scannt die Navigation, scoret outbound-Links nach Stichworten (DE+EN: `schedule`, `agenda`, `programm`, `speakers`, `sprecher`, `tracks`, `sessions` …) und folgt automatisch bis zu **8 Subseiten** (Tiefe 2). Negative-Hints (`impressum`, `datenschutz`, `tickets`, `sponsors` …) werden geskipped. Mehrsprachen-Pfade (`/zh/`, `/fr/`) werden ignoriert wenn die Hauptseite nicht in dieser Sprache ist.
2. Alle gefetchten Seiten gehen mit URL-Label in **einen einzigen LLM-Call** zur Konsolidierung.
3. Der LLM extrahiert: Plan-Items (mit `day`, `time`, `track`, `where`, `tags`), Risiken, Wishes pro Rolle, Goals — plus eine `view_modes`-Empfehlung für die UI (`by_day`, `by_track`, `by_where` …).
4. Das Event wird mit dem Output befüllt (bestehender Plan/Risiken/Wishes wird überschrieben).

**Latenz**: 5–10 s Crawl + 30–90 s LLM-Call. Insgesamt **40–110 s**. Während des Wartens pulsiert ein gelber Block im Result-Bereich, der Button ist disabled — kein Doppelklick versehentlich.

**Tipps für gute URL-Ergebnisse**:
- Eine **Hauptseite** des Events reicht meist — der Crawler findet Schedule + Speakers selbst.
- Wenn deine Eventseite einen ungewöhnlichen Pfad-Namen hat (`/Vortragsplan` statt `/schedule`), kannst du den Pfad direkt mit angeben (zweite URL-Zeile) als Fallback.
- Bei mehrtägigen Events: einmal die Hauptseite und das `/schedule` reicht. Der LLM erkennt aus den Datums-Headings die Tages-Aufteilung.
- Wenn die Schedule-Seite **JS-rendered** ist (statt server-side), kriegt der Crawler nichts — dann auf Pfad B (Paste) ausweichen.

#### Pfad B: Paste (Schedule-Email, Slack-Brief, abgetippt)

Freier Text in das mittlere Textarea. Klick auf `Import & Synthesize`.

Beispiel-Format das gut funktioniert:
```
Tag 1 — Montag 5. Mai
09:00 Keynote: Dr. Anna Schmidt — "Future of Coordination"  (Main Hall, Plenary)
10:30 Coffee Break (Foyer)
11:00 Workshop A: AI in Live-Coordination (Main Hall, AI Track)
11:00 Workshop B: Robotics for Events (Workshop Room, Robotics Track)
13:00 Lunch (Catering Area)
14:00 Panel: Privacy in coordination (Main Hall, Plenary)

Tag 2 — Dienstag 6. Mai
...
```

Der LLM ist tolerant gegenüber Format-Variationen. Wichtig: **Zeiten und was passiert** klar erkennbar; Räume + Tracks in Klammern hilft.

#### Pfad C: YAML (für Power-User mit strukturierter Vorlage)

Direktimport ohne LLM, sofort. Das Schema ist identisch mit `event-config.yaml`. Beispiel:

```yaml
event:
  name: My Event
  scenario: opening day with three tracks
  areas: [main_hall, workshop_a, foyer]

view_modes: [by_day, by_track]

plan:
  - day: Day 1
    time: "09:00"
    what: Opening Keynote
    track: Plenary
    where: main_hall
    who: [artist]
    tags: [keynote]
  # ... weitere Items

risks:
  - id: ...
goals:
  - text: ...
wishes:
  - text: ...
    holder_roles: [artist]
```

Verwende YAML wenn du den maximalen Kontroll-Grad willst und das Format kennst.

#### Mehrere Pfade kombinieren

Du kannst URL + Paste + YAML zusammen einreichen. Der LLM bekommt alle Quellen in einem Call und konsolidiert. Bei Konflikten (z.B. URL sagt 14:00, Paste sagt 14:30) markiert er das im `conflicts`-Feld der Antwort.

### Schritt 4: Stakeholder anlegen

In der **Stakeholder-Setup-Card** (zweite lila Card unten), zwei Pfade:

#### Pfad A: Manuell pro Kategorie

8 Default-Kategorien (Speakers, Volunteers, AV/Stage Tech, Catering, Garderobe, Security, Medical, Organizer) + freie "Custom"-Kategorie. Pro Kategorie: ein Textarea, eine Person pro Zeile.

Format pro Zeile:
```
Anna Becker
Ben Cohen <ben@example.org>
Dr. Smith <smith@uni.edu> — Topic: AI Ethics
```

Email + Notiz nach `—` sind optional. Submit → alle werden mit ihrer Kategorie + automatisch gemappter Coordination-Rolle angelegt.

**Default-Mapping Kategorie → Coordination-Rolle**:
- Speakers → `artist`
- Volunteers / Organizer / Custom → `organizer`
- AV / Stage Tech → `stage_tech`
- Catering / Garderobe / Sponsors → `vendor`
- Security → `security`
- Medical → `medic`
- Attendees → `fan`

> Die Coordination-Rolle ist abstrakt und treibt den Reasoning-Loop (welche Rollen kriegen Fanout). Die Kategorie ist die menschen-lesbare Funktion und treibt UI-Gruppierung. Für die meisten Use-Cases reichen die Defaults; wenn du was anderes willst, übergib `role` explizit per JSON-API oder editiere im Backend.

#### Pfad B: Bulk-Import via Datei oder Paste

Für CSV/TSV mit Header `Name,Email,Category,Notes` → wird **deterministisch** geparsed (kein LLM, sofort).

Beispiel CSV:
```
Name,Email,Category,Notes
Anna Becker,anna@x.com,Speaker,AI Ethics
Ben Cohen,ben@y.org,Volunteer,
Felix Weber,felix@x.com,AV / Stage Tech,FOH lead
```

Für **freien Text** (z.B. Mail-Roster, copy-pasted aus Notion, PDF-Text) → ein LLM-Call (~30–60 s) extrahiert die Liste.

Beide Pfade enden in einer **Review-Tabelle** (Name, Kategorie, Email, Notiz). Nach `✓ Confirm und alle anlegen` werden die Stakeholder ins Event geseedet.

### Schritt 5: Stakeholder verteilen (Magic-Links)

In der **Stakeholders-Card** (im Grid mittig) sind alle angelegten Stakeholder nach Kategorie gruppiert. Pro Person ein 🔗-Button → kopiert die persönliche URL `/me/{stakeholder_id}` ins Clipboard.

Diese URLs gibst du an die Personen weiter (Slack, Mail, persönlich, ausgedruckt). Sobald sie diese öffnen, sind sie als der jeweilige Stakeholder eingeloggt — kein Passwort, kein Login-Flow (Token im URL = Auth, Magic-Link-Pattern).

> **Status (Phase 2)**: Die Magic-URLs werden generiert und können kopiert/verteilt werden. Die Landing-Page unter `/me/{id}` mit eigener Inbox/Plan-Sicht/Signal-Eingabe wird in Phase 3 gebaut. Aktuell führen die Links auf 404 — die Empfänger merken nichts von den URLs solange Phase 3 nicht steht.

### Schritt 6: Live-Coordinieren

Sobald das Event läuft (= im echten Zeitsinn), kommen Signale rein. Drei Wege:

**a) Vom Veranstalter selbst** über die Footer-"Signal senden"-Form auf der Hauptseite — wähle Channel, Stakeholder (als wer schickst du), gib Text ein.

**b) Von Stakeholdern direkt** über ihre Magic-URL (Phase 3 — noch nicht).

**c) Programmatisch** über die HTTP-API:
```
POST /post     {text, stakeholder_id, area}    # narrative Update
POST /signal   {text, stakeholder_id, area}    # strukturierte Beobachtung
POST /ask      {text, stakeholder_id, area}    # Frage (LLM antwortet)
```

Was beim Eingang eines Signals passiert:
1. Signal landet in `RealityState`
2. Reasoning-Loop wird im Hintergrund getriggert (UI bleibt responsive)
3. LLM bekommt das ganze Event-Snapshot + neuen Signal in einem strukturierten Prompt
4. LLM entscheidet: Risk getriggert? Welche Wishes sind in Gefahr? Welche Rollen müssen informiert werden? Mit welcher konkreten Nachricht? Sind Plan-Updates nötig?
5. Output wird verteilt: Plan-Items werden upgedated (Status `delayed` etc.), Inbox-Messages an betroffene Stakeholder geschrieben, Audit-Trail wird geschrieben

**Latenz**: 30–90 s pro Reasoning-Call (das ist GLM-5.1 mit Extended Thinking). UI zeigt `⏳ LLM denkt…` während das passiert.

### Schritt 7: Plan-Diff während des Events

Wenn der Reasoning-Loop ein Plan-Item auf `delayed` setzt oder die Zeit ändert, zeigt die Plan-Card eine **Diff-Anzeige**: 

```
~~14:00~~ → 14:30   Workshop A   [delayed]   was: planned
```

So sieht jeder auf einen Blick, wie sich der Plan vom Original verändert hat. Der Original-Snapshot wurde beim "Go Live" eingefroren.

### Schritt 8: Reset (für Demo / Wiederholung)

Im Event-Bar oben gibt's einen `🔄 reset schedule` Button. Wirft alle Plan-Items zurück auf `original_time`/`original_status` und löscht die Outboxes. **Stakeholder + Reality bleiben** — gut für eine Demo-Session, in der man dasselbe Setup mehrfach durchspielen will.

Für einen kompletten Reset (Stakeholder + Reality auch wegnehmen, nur YAML-Default behalten): `Reset Reality + Stakeholders` Button im Footer der Hauptseite.

---

## Multi-Event-Modus

Du kannst mehrere Events gleichzeitig im Setup haben (z.B. "GOSIM Paris 2026" gerade live, "Q3 Summer Conference" noch in Vorbereitung). Im Active-Event-Dropdown schaltest du zwischen ihnen um. Nur das aktive Event nimmt Signale an, der Reasoning-Loop arbeitet nur darauf.

Beim Umschalten:
- Vorheriges Live-Event geht auf `setup` zurück (= friert ein, nimmt keine Signale mehr)
- Neues Event wird `live`
- Plan-Snapshot wird beim ersten Mal Live-Gehen festgefroren (idempotent — beim zweiten Mal nicht überschrieben)

---

## Tipps für gute Ergebnisse

### Beim URL-Import
- **Eine repräsentative URL** reicht meistens — das ist die Hauptseite des Events. Der Auto-Crawler findet den Rest.
- Wenn der Auto-Crawl die wichtige Seite verpasst (z.B. `/Vortragsplan` ist im Menü versteckt), gibt eine zweite URL als Hint mit.
- **Vermeide URLs mit JS-Rendered-Content** — der httpx-Crawler liest nur statisches HTML. Bei Single-Page-Apps muss du Paste oder YAML benutzen.
- **Mehrsprachige Seiten**: gib die Hauptsprachen-URL an. Der Crawler ignoriert `/zh/`, `/fr/` etc. um Doppelungen zu vermeiden.

### Bei Multi-Track-Events
- Damit das Tool die Track-Aufteilung erkennt, müssen die Sources sie *erwähnen* — entweder als Spalte/Tabelle ("Track: Agentic AI Summit") oder als Section-Heading.
- Im Output kriegst du `view_modes: [by_day, by_track, by_where]` zurück und die Plan-Card zeigt entsprechende Pills oben (📅 By Day / 🎯 By Track / 🏛 By Room).
- Default-View ist die *erste* in `view_modes`. Der LLM ordnet die Liste nach Sinnhaftigkeit für das jeweilige Event.

### Bei der Stakeholder-Liste
- **CSV mit klarem Header** ist deterministisch geparsed (kein LLM-Call, kein Drift-Risiko, sofort).
- Spalten die erkannt werden (case-insensitive): `Name`, `Email`, `Category` / `Kategorie` / `Function`, `Role`, `Area` / `Bereich`, `Notes` / `Notiz` / `Topic`.
- Bei Plain-Text-Listen ohne Spalten: der LLM ist tolerant — z.B. "Anna Becker (Speaker, AI Ethics)" funktioniert.
- **Speaker-Topic** kann im `Notes`/`Topic`-Feld stehen — wird im Stakeholder-Profil als Notiz angezeigt.

### Bei Risiken/Wishes
- Wenn der LLM-Output nicht passt, kannst du das `event-config.yaml` direkt editieren oder per YAML-Pfad nachschieben. Das reset-import wirft die alten weg und nimmt deine.
- **Goal-Granularität**: lieber 4–6 prägnante Goals als 20 mikroskopische. Der Reasoning-Loop nutzt sie als Kontext, nicht als Checkliste.

---

## Was das Tool *nicht* gut kann (ehrlich)

- **JS-rendered Eventseiten** — der Crawler liest nur statisches HTML. Bei React/Vue/Angular-SPAs musst du auf Paste oder YAML ausweichen.
- **Sehr große Events (>200 Plan-Items)** — der LLM-Prompt hat Limits, das Output-JSON wird unzuverlässig. Pragmatik: nur den nächsten relevanten Tag importieren, oder das Event in Sub-Events teilen.
- **Real-time-Latenz unter 30 s** — GLM-5.1 mit Extended Thinking braucht 30–90 s pro Reasoning-Call. Für Echtzeit-kritische Coordination (z.B. medizinischer Notfall) nicht der richtige Layer — aber das ist eh `911`-Territorium, nicht ein Event-Tool.
- **Foto/Audio-Verarbeitung** — der Crawler ignoriert Bilder, Reasoning-Calls bekommen nur Text. Wenn dein Schedule nur als Bild-PDF existiert, brauchst du vorher OCR.
- **Persistenz** — der Server hält alles in-memory. Beim Neustart ist alles weg. Für ein einzelnes Event über 1–2 Tage ist das OK, aber bei längeren Phases brauchst du eine externe DB-Schicht.
- **Externe Stakeholder (Attendees, Press, Sponsoren)** — aktuell noch nicht über den Wizard onboardbar (Phase 1B-future).
- **Multi-User mit echter Auth** — der Magic-Link-Mechanismus ist Auth-light. Für sicherheitskritische Settings musst du Phase 1C (Passwort + Sessions) bauen.

---

## Troubleshooting

**LLM-Call hängt > 2 min**
- Check `LLM_API_KEY` ist gültig, Quota nicht aufgebraucht (RouteTokens-Dashboard checken).
- HTTP 402 in Server-Logs = Key abgelaufen oder Limit erreicht.
- Network-Issue: GLM-API ist von China aus betrieben, ggf. VPN-bedingte Latenz.

**Import bringt nur wenige Plan-Items zurück**
- URL möglicherweise JS-rendered → Crawler kriegt nichts. Test: `curl <url>` und schau ob das HTML deine Schedule enthält. Falls nicht: Paste-Pfad nehmen.
- Wichtige Subseite vom Auto-Crawl verpasst: zweite URL als Hint mit angeben.

**Stakeholder-Import bricht ab**
- CSV-Header muss eine erkannte Spalte haben (Name + mindestens eine andere).
- LLM-Pfad: Quote-Limit reached → Plain-Text-Liste verkürzen oder CSV-Format umbauen.

**Plan-Diff zeigt nichts**
- Nur sichtbar wenn `original_time` oder `original_status` gesetzt ist (= Plan-Snapshot wurde gemacht). Snapshot passiert *einmal* beim ersten Übergang `setup → live`. Wenn dein Event noch nie aktiviert war, kein Snapshot, kein Diff.
- Re-Import ersetzt den Plan und löscht den Snapshot — du musst dann wieder einmal aktivieren.

**Server-Restart und alles weg**
- In-memory-Persistenz, by design. Wenn dir das Event zwischenzeitlich verloren gehen darf, kein Problem. Sonst: dein Plan/Risiken/Wishes können in `event-config.yaml` versioniert werden, dann beim Restart automatisch geseedet.

**View-Pills im Plan zeigen falsche Optionen**
- Pill nur sichtbar wenn das Event entsprechende Daten hat: `by_day` braucht mind. 1 Item mit `day`, `by_track` braucht mind. 2 unterschiedliche Tracks, `by_where` braucht mind. 2 Räume.
- Wenn der LLM falsch klassifiziert hat: per `event-config.yaml` direkt `view_modes` setzen.

---

## API-Referenz (Kurz)

Wichtigste Endpunkte:

| Pfad | Methode | Zweck |
|---|---|---|
| `/` | GET | Admin-Dashboard (HTML) |
| `/state` | GET | Snapshot des Live-Events (JSON) |
| `/events` | GET | Alle Events mit ihren Modes |
| `/events` | POST | Neues Event anlegen (`{name, scenario, areas}`) |
| `/events/{id}/activate` | POST | Event live schalten |
| `/events/{id}/import` | POST | Sources importieren (`{sources: [{type, value}]}`) |
| `/events/{id}/reset-schedule` | POST | Plan-Items zurück auf Snapshot |
| `/post`, `/signal`, `/ask` | POST | Signal vom Stakeholder ans Event |
| `/inbox/{stakeholder_id}` | GET | Messages an einen Stakeholder |
| `/stakeholders/bulk` | POST | Mehrere Stakeholder auf einmal anlegen |
| `/stakeholders/extract` | POST | Stakeholder-Liste aus Text/CSV ziehen (Preview) |

Vollständige OpenAPI-Doku unter `http://127.0.0.1:8765/docs` (FastAPI auto-rendered).

---

## Was als nächstes kommt (Roadmap)

- **Phase 3**: Stakeholder-Frontend pro Person (`/me/{id}`) — eigene UI mit Plan-Sicht (gefiltert auf relevante Items), Inbox, Signal-Eingabe-Form. Mobile-friendly.
- **Phase 4**: Background-Time-Tick — proaktiv Plan-Items auf `in_progress`/`delayed` setzen wenn ihre geplante Zeit erreicht ist (heute musst du selbst Signale schicken).
- **Phase 5**: Externe Stakeholder onboarding (öffentlicher Magic-Link für Attendees, separater Flow für Sponsoren/Presse).
- **Optional**: Passwort-Auth (Phase 1C), Email-Versand der Magic-Links, persistente DB.

Diese Phasen sind nicht gebaut — siehe `CLAUDE.md` für aktuellen Build-Stand und Decisions-Log.

---

## Hilfe / Bugs

- Repo: <https://github.com/b0kelmann/liveticker-skill>
- Setup-Probleme: schau in `CLAUDE.md`, dort steht der aktuelle interne Build-Status mit allen architectural Decisions.
- Bei LLM-bezogenen Problemen: Audit-Log unter `audit.log` zeigt alle Reasoning-Decisions, oft hilft das beim Debuggen.
