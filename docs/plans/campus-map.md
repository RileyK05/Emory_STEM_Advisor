# Campus map: show students where things are (2026-11-17)

Students ask where things are: "where is the registrar?", "where's CAPS?", "my
Oxford class is in Pierce Hall 228, where is that?". Today the advisor can only
answer in words. This plan adds a campus map that the chat can show beside an
answer: a small map card under any answer block that names a known place, and
a full map view the student can open.

It is a first run. The map needs a lot of checking on the ground, and several
inputs need Riley (see "Open decisions"). Each phase ends in something a person
can look at and correct.

## Status (2026-11-17)

Riley's decisions: OpenStreetMap is OK as the survey; start with the **Atlanta
main campus**; and **make our own map rather than use a map tool**. Built in the
first run, by Claude:

- `backend/scripts/fetch_campus_map.py`: one Overpass query for the campus box,
  saved raw to `data/maps/raw/` with a SHA sidecar; backs off when the server
  is at capacity.
- `backend/scripts/build_campus_map.py`: draws **our own map file**,
  `frontend/public/maps/atlanta.json`, from the survey: metres from a campus
  origin, our own layers (lawn, woods, sport, water, parking, plaza, roads,
  paths, buildings), simplified, with ids only for named buildings inside
  Emory's boundary. Everything else is faded context. No model touches it.
- `frontend/src/components/map/`: `mapData.ts` (format, loader, helpers),
  `CampusMap.tsx` (the SVG drawing: drag, arrow keys, buttons and Ctrl + wheel
  to move; labels thin out without overlapping; pins on highlighted buildings;
  Swoop and Dooley colours), and `CampusMapCard.tsx` (the card under an answer,
  with a text list, Directions and Emory's-map links, and the credit line).
- The contract: `ResearchDecisionInfo.places` (an optional list of building ids)
  in `schemas.py` and `types.ts`. It is per answer, not per block, because the
  chat renders an answer as one text. This replaces `place_ids` on blocks below.
- Mock mode: a question starting with "where" shows the card.

Second run (Riley: "save maps to the backend via the script"):

- `build_campus_map` also writes `backend/app/places/atlanta.json`, the
  backend's place index: each building id with the names that find it in text.
  Names come only from OSM (`name`, `official_name`, `alt_name`, `short_name`,
  `loc_name`; never `old_name`, which collides with today's buildings) through
  one deterministic rule, `app.places.match_names`: a name counts when it reads as
  a building (two or more words with a building word such as Hall or Library, or
  four or more words) and has a distinctive word of three letters or more, so
  "Anthropology", "Administration" and "Building C" never match. A name with a
  middle initial also matches the words after it ("Goodrich C. White Hall" ->
  "White Hall"). Names two places share match neither.
- `app/places/`: `places_in(*texts)` returns at most three place ids in order of
  first mention; the longest overlapping name wins ("Emory University Hospital
  Tower" is the tower). `query_service` sets `ResearchDecisionInfo.places` from
  the student's question and the answer's blocks. Tests: `tests/test_places.py`.
- Greek-letter houses get `osm-<type>-<id>` ids (their names slug to nothing).
- `scripts/manual/map_audit.py` (1c, first version): drawn ids vs index ids,
  places no text can find, shared names, OSM-id ids, and `--texts` to try
  wording.

Still not built: the gazetteer (1a). It is what adds the names students use
("MSC", "Atwood", "PAIS", "the library"), offices inside buildings and
entrances, and the `reviewed` gate. Until it exists, place ids are the builder's
slugs of OSM names and can change when OSM renames a building; the map shows any
OSM-named building an answer names, without review.

## What the data can and cannot do (surveyed 2026-11-17)

- **Atlas rooms.** The public Atlas detail pages have a "Schedule and Location"
  section (`meet-room-*` spans in the saved captures). Oxford sections show real
  rooms ("Pierce Hall 228", "Seney Hall 215", "Oxford Science Bldg. 415"), and
  so do Rollins (public health) and Nursing. Atlanta College sections almost all
  show nothing or "Registrar Room Pool", even for past terms (fall 2025: about
  790 of 940 sections with a room string have no building). So "where is my
  class?" works for Oxford from the Atlas, and for Atlanta only when the student
  tells us the room. Do not look for Atlanta rooms anywhere else.
- **Emory's own map** (`map.emory.edu`, linked from `emory.edu/maps`) is
  GIS-based. Emory publishes it as an interactive site and PDF maps (by building
  name, by building number, accessibility maps for Atlanta and Clairmont). No
  KML, GeoJSON, shapefile or building list is published, and the pages say
  "All Rights Reserved". So Emory's map is something to **link to**, never
  something to copy or trace. Oxford has its own map pages
  (`oxford.emory.edu/map/`, `oxford.hostexp.com`); not yet read.
- **Shapes** therefore have to come from open data: OpenStreetMap (ODbL), which
  maps campus buildings, paths and green spaces with names. ODbL requires the
  credit line "© OpenStreetMap contributors" wherever the map is shown, and a
  database built from it stays under ODbL. OSM is not on the pre-approved source
  list, so using it is open decision 1.
- **Place facts** (an office's building, its address, what it is for) come from
  Emory's own pages, ingested like any other source, so an answer can cite them.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Who writes place data | A hand-written gazetteer (`configs/campus_places.toml`) plus geometry from a pinned OSM extract. No model writes, guesses or fills in a place, an alias, a location or a room match | Same rule as the prerequisite graph. A wrong pin sends a student to the wrong building, and a model will happily invent one. |
| Facts vs shapes | Facts about a place (the office is in which building, its address) are cited from Emory pages. Shapes (where the building outline is) come from OSM and are never stated as an Emory fact | Shapes can't be cited to Emory, and Emory's map can't be copied. Keeping them apart keeps every sentence citable. |
| How the map is drawn | Our own SVG from a small GeoJSON file, in a React component. No map library, no tile server, no API key | The whole campus is about 1.5 km across. At that size a flat projection (longitude scaled by the cosine of the latitude) is off by well under a metre, so no projection library is needed. The frontend today depends only on React and react-markdown; an SVG keeps it that way, works in mock mode and offline, and uses the Swoop and Dooley theme tokens directly. Street-level context beyond campus is not needed. If it is later, MapLibre with a self-hosted extract is the upgrade path. |
| How a place reaches an answer | Code attaches `place_ids` to answer blocks that name a known place, after the model finishes (the same step that adds special-course and retired-course notes) | The model never has to remember to do it, and it can't attach a place it made up. |
| Directions | A "Directions" link that opens the student's own maps app at the building's entrance point, and an "Open in Emory's map" link | Turn-by-turn routing is a large, risky feature. Linking out gives it for free. |
| Accessibility | Every map has a text list of the places it shows, the SVG is labeled, and keyboard pan and zoom work. Accessible entrances are a later phase that needs Riley | A map that only works by sight fails students who use screen readers. Entrance data needs checking on the ground. |

## Phase 1: data, checkable before any UI

### 1a. The gazetteer (`backend/configs/campus_places.toml`, new, hand-written)

```toml
schema_version = 1

[[place]]
id = "pierce-hall"            # stable slug; never reused for another place
name = "Pierce Hall"
campus = "oxford"             # atlanta | oxford
kind = "building"             # building | office | library | dining | outdoor
aliases = ["Pierce"]           # how Atlas rooms and students write it
osm = "way/123456789"         # the OSM element whose outline is drawn
entrance = [33.6, -83.86]     # lat, lon of the main door, for the pin and Directions
source = "https://..."        # the Emory page that names it (ingested)
reviewed = false

[[place]]
id = "registrar"
name = "Office of the University Registrar"
campus = "atlanta"
kind = "office"
in = "some-building-id"       # an office points at its building; it has no outline of its own
room = "..."                  # only as the source page states it
source = "https://registrar.emory.edu/..."
reviewed = false
```

- Unknown keys are errors. `id` values are unique, aliases are unique across
  places after normalization (case, punctuation, "Bldg." = "Building",
  "Ctr" = "Center"), and `in` must name a `building`.
- First content: every building in the Atlas room strings (Pierce, Seney,
  Johnson, Humanities Hall, Oxford Science Building, Williams, Tarbutton,
  the Oxford library and gyms, R. Randall Rollins, the Nursing Learning
  Center, the Carlos Museum), the buildings where Atlanta STEM departments
  teach (taken from the department pages already in `configs/programs.toml`),
  and the offices the advisor already sends students to (Registrar, CAPS,
  the Office for Undergraduate Education, the Career Center, Student Health,
  the libraries). About 60 places. Riley reviews each one, marking it
  `reviewed = true`; unreviewed places are not shown to students.
- Room strings that are not places stay out of it and are listed in an
  `ignore` table: `ONLINE`, `Registrar Room Pool`, `Oxford Room Pool`,
  `Oxford Room Pool Note`, `Off Campus Location`, and the `EP_*` codes until
  someone knows what they are.

### 1b. Geometry (`scripts/fetch_campus_map.py`, new)

- Reads the OSM elements for the two campus bounding boxes (fixed in the
  script, one per campus) from one documented public source (open decision 1:
  a Geofabrik Georgia extract, or one Overpass query), and keeps buildings,
  footways, service roads, parking, grass and water.
- Writes `data/maps/<campus>-<date>.geojson` plus a sidecar with the source,
  the time and the SHA-256, the same pattern as program sidecars. Coordinates
  are rounded to 6 decimals (about 10 cm) and simplified, so each campus file
  stays under about 300 KB.
- `scripts.publish_campus_map` copies a checked file to the storage seam
  (`app/storage/`) under a backend-neutral key, so hosting works the same as
  artifact originals.

### 1c. The check (`scripts/manual/map_audit.py`, new; never in the test suite)

Prints, in the terminal:

- every gazetteer building whose `osm` element is missing from the extract or
  outside its campus box, and every `entrance` more than 40 m from its outline;
- every distinct building string in the saved Atlas captures that matches no
  alias and is not in `ignore`, with its section count;
- aliases that match two places;
- places still `reviewed = false`.

This is the list Riley works from on the ground: open the map, walk to a pin,
fix the file, re-run.

## Phase 2: backend

### 2a. `app/places/` (new, deterministic; no model imports)

- `load_gazetteer(path) -> Gazetteer`, cached by mtime like
  `_routing_config` in `app/connections/selection.py`.
- `places_in(text) -> tuple[str, ...]`: the place ids named in a text, by
  alias, longest alias first, on word boundaries. Same role as
  `canonical_codes_in` for course codes, and used everywhere a place is
  recognized, so recognizing and checking can't disagree.
- `place_for_room(room) -> tuple[str, str] | None`: "Pierce Hall 228" →
  (`pierce-hall`, `228`). Only an exact alias at the start of the string counts.
- Extend `tests/test_no_llm_in_prereq.py`'s import check (or its table) to
  cover `app/places/`.

### 2b. API

- `GET /api/campus-map/{campus}`: the published GeoJSON plus the reviewed
  places (id, name, kind, `in`, entrance, Emory map link). `response_model`
  `CampusMap` in `schemas.py`, mirrored in `frontend/src/api/types.ts`; the
  contract-drift test covers it. Cached with an ETag from the file's SHA.
- `DecisionBlock` gets `place_ids: tuple[str, ...] = ()`, in `schemas.py` and
  `types.ts` as well.

### 2c. Places in answers

- In `ResearchSession._disclose` (`generation/research_tools.py`), after the
  existing disclosures: for each non-question block, `place_ids =
  places_in(block.text)` limited to reviewed places, at most 3 per block.
- Course records: an Oxford section whose room matches `place_for_room` gets
  `place_id` and `room` in its record, so "where does CHEM 150 meet at Oxford?"
  is answered from the record and the block gets the place.
- No new research tool in this phase. The gazetteer's offices are Emory facts
  only through their ingested source pages, which research already searches.

## Phase 3: frontend

- `frontend/src/components/map/CampusMap.tsx` (new): draws the GeoJSON as SVG
  layers (grass, paths, roads, buildings, then labels), with theme tokens for
  Swoop (light blue and gold) and Dooley (black and white). Props: `campus`,
  `highlight: string[]`, `compact: boolean`.
  - Pan with drag and arrow keys, zoom with the wheel, pinch and `+`/`-`,
    by changing the `viewBox`. Labels appear above a zoom level.
  - Highlighted places get a pin at `entrance` and a filled outline. An office
    highlights its building and names itself in the pin label.
  - Below the map: the text list of highlighted places with "Directions" and
    "Open in Emory's map" links, and the line "Map data © OpenStreetMap
    contributors".
- `MessageView.tsx`: a block with `place_ids` shows a compact map card under it,
  loaded on first view (`useLoad`). One card per answer, holding every place
  the answer names, rather than one per block.
- `frontend/src/api/mock.ts`: a small fixture campus (a few rectangles) and
  one mock answer with `place_ids`, so mock mode shows a map without real data.
- Browser checks (what only a browser can prove): the card renders and
  highlights the right building, keyboard zoom changes the view, focus order
  reaches the links, both themes have readable labels, and the text list
  matches the pins.

## Phase 4: later, each needs Riley

- **My week at Oxford**: a map artifact of a planned Oxford schedule, pinned by
  room, from the term planner.
- **Accessible entrances and routes**: from Emory's Atlanta and Clairmont
  access maps, checked on the ground. These are PDFs under "All Rights
  Reserved", so they are read by a person, not traced.
- **Atlanta rooms a student types**: "my class is in MSC E208" → the building,
  through the same alias table.

## Phase 5: contract and docs

- `AGENTS.md`: the `configs/` line gets `campus_places.toml` (hand-written;
  reviewed places only are shown), the layout gets `app/places/` under
  deterministic data, and Commands gets `scripts.fetch_campus_map`,
  `scripts.publish_campus_map` and `scripts.manual.map_audit`. The hard rule
  section gets one sentence: no model writes place data.
- `docs/architecture.md`: places as deterministic data, `place_ids` on blocks,
  the map endpoint, and the OSM credit requirement.

## Tests (fast suite; AGENTS.md policy)

- `places_in` and `place_for_room`: one parametrized table (aliases with
  punctuation, longest match wins, "Pierce" inside another word does not
  match, "Registrar Room Pool" matches nothing, unreviewed places are skipped).
- Gazetteer loading: one table of bad files (unknown key, duplicate alias,
  `in` naming an office) that must raise.
- `_disclose` attaching `place_ids`: one row in the existing disclosure table.
- No tests for the fetch, publish or audit scripts.

## Open decisions for Riley

1. **OpenStreetMap for shapes.** Settled 2026-11-17: yes, as the survey only; the map itself is our own drawing.
2. **Which places first.** Settled 2026-11-17: the Atlanta main campus first.
3. **Ground checks.** Pins and entrances need someone on campus. Can Riley
   check the Oxford buildings, or should the first release say "approximate"
   on every pin until checked?
4. **Directions link.** OK to link out to the student's maps app with the
   entrance coordinates? It sends nothing about the student, only a location.
