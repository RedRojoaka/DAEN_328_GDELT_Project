# DAEN 328 — GDELT Events Pipeline

## Dataset size and coverage

We use **GDELT 2.0 Events** to study news coverage of religion, human-rights
organizations, Iran and oil, and protests. The following snapshot was measured
from local CSV batches and daily transformation reports on November 16, 2026.

| Measure | Collected dataset |
| --- | --- |
| Event rows received by daily transformation | **17,963,639** |
| Event rows retained after cleaning | **17,963,639** |
| Raw fields per event | **61** |
| Timestamped archive batches | **17,664** |
| First / last collected archive (UTC) | **2026-05-15 06:45 / 2026-11-15 06:30** |
| Daily transformation reports | **185** |
| Duplicates removed / rows quarantined by daily transformation | **0 / 0** |
| Raw CSV storage | **7,417,505,011 bytes** (approximately **7.42 GB**) |
| Daily transformed CSV storage | **11,725,284,537 bytes** (approximately **11.73 GB**) |

Counts come from `data/transformed/YYYYMMDD/quality_report.json`; raw file counts
and sizes come from `data/batches/*.csv`. These describe collected files, rather
than a verified count of distinct records loaded into PostgreSQL. Dimension rows
can recur across daily exports; query PostgreSQL directly for database table
counts. Storage figures exclude PostgreSQL, indexes, Docker images,
backups, and temporary files.

The research collection above covers six calendar months of **archive publication
times**. It is the previously collected dataset used for analysis and dashboard
screenshots.

**Testing extraction window:** As agreed with our TAs, the default testing
configuration uses a rolling **two-day extraction window** (`LOOKBACK_DAYS=2`).
A fresh run with these settings collects that smaller window; it does not
recreate the six-month research dataset above. The number of rows returned
depends on the selected dates and GDELT activity.

`SQLDATE` is the event date and can fall outside the archive publication window.
GDELT publishes Events archives every 15 minutes; see the
[official event codebook](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf).
Generated data is ignored by Git: cloning the repository does not copy the
dataset or a teammate's database.

### Raw field descriptions

Upstream ZIPs contain headerless, tab-separated records. `extract.py` assigns the
61 names in `EVENT_COLUMNS` and saves headed CSV batches. Codes are read as
strings to preserve leading zeros. Types below describe cleaned values;
PostgreSQL column names are lowercase.

| Field or group | Cleaned type | Description / use |
| --- | --- | --- |
| `GLOBALEVENTID` | `BIGINT` | Unique event key. |
| `SQLDATE` | `DATE` | Event day, parsed from `YYYYMMDD`; used by dashboard date filters. |
| `MonthYear`, `Year`, `FractionDate` | Raw calendar fields | Redundant date representations; omitted from the normalized event table. |
| `DATEADDED` | `TIMESTAMPTZ` | UTC recording timestamp, parsed from `YYYYMMDDHHMMSS`; used to compare record versions. |
| `Actor{1,2}Code`, `Actor{1,2}Name` | `TEXT` | Actor codes and names. Actor 1 performs the action involving actor 2. |
| `Actor{1,2}CountryCode`, `KnownGroupCode`, `EthnicCode` | `TEXT` | Country affiliation, group, and ethnicity attributes for each actor. |
| `Actor{1,2}Religion1Code`, `Religion2Code` | `TEXT` | Religion attributes, stored in the `actor_religions` bridge. |
| `Actor{1,2}Type1Code`, `Type2Code`, `Type3Code` | `TEXT` | Up to three role codes for each actor. |
| `IsRootEvent` | `SMALLINT` | Lead-sentence event indicator, `0` or `1`. |
| `EventCode`, `EventBaseCode`, `EventRootCode` | `TEXT` | Detailed CAMEO action code and parent categories. |
| `QuadClass` | `SMALLINT` | 1: verbal cooperation; 2: material cooperation; 3: verbal conflict; 4: material conflict. |
| `GoldsteinScale` | `DOUBLE PRECISION` | Event-type conflict/cooperation score, −10 to +10. |
| `NumMentions`, `NumSources`, `NumArticles` | `BIGINT` | GDELT coverage counts; nonnegative integers. |
| `AvgTone` | `DOUBLE PRECISION` | News tone score, −100 to +100. |
| `{Actor1Geo,Actor2Geo,ActionGeo}_Type` | `SMALLINT` | Geographic match type; type 0 produces no location reference. |
| `{Actor1Geo,Actor2Geo,ActionGeo}_FullName` | `TEXT` | Matched location name. |
| `{Actor1Geo,Actor2Geo,ActionGeo}_CountryCode` | `TEXT` | Geographic country code, distinct from actor affiliation coding. |
| `{Actor1Geo,Actor2Geo,ActionGeo}_ADM1Code`, `_ADM2Code` | `TEXT` | Administrative subdivision codes. |
| `{Actor1Geo,Actor2Geo,ActionGeo}_Lat`, `_Long` | `DOUBLE PRECISION` | Coordinates, validated against geographic bounds. |
| `{Actor1Geo,Actor2Geo,ActionGeo}_FeatureID` | `TEXT` | Geographic feature identifier. |
| `SOURCEURL` | `TEXT` | Source article URL. |

Actor groups repeat for actors 1 and 2; geographic groups repeat for both actors
and the action location. The complete list is in [extract.py](extract.py), and
source semantics are in the [GDELT codebook](https://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf).

### Derived analysis fields

| Fields | Definition / purpose |
| --- | --- |
| `EventMonth`, `ReportDate`, `ReportMonth` | Calendar groupings derived from the event date or UTC recording timestamp. |
| `IsInAnalysisWindow` | Inclusive event-date bounds supplied to transformation; NULL without bounds. Flags rows without removing them. |
| `ActionGeo_CountryISO3`, `SourceDomain` | Mapped action-country code and normalized HTTP(S) source hostname. |
| `QuadClassLabel`, `EventRootLabel` | Readable category labels; root labels are stored in `event_types`. |
| `IsProtest`, `IsMaterialConflict`, `IsConcessionEvent` | Root code 14, quad class 4, and root code 08, respectively. |
| `IsRightsRelatedEvent` | Membership in the explicit `RIGHTS_EVENT_CODES` selection in `transform.py`. |
| `Actor{1,2}IsHumanRightsCoded`, `Actor{1,2}IsHumanRightsActor` | Raw HRI role match and a stricter match excluding specified government roles. |
| `Actor{1,2}HumanRightsNameCandidate` | Human-rights organization keyword match on actor names. |
| `HasReligionCode`, `ReligionHumanRightsCooccurrence` | Any actor religion code; religion-coded actor paired with a qualifying human-rights actor. |
| `IsIranLinked`, `OilURLCandidate`, `IranOil`, `HasOPCActor` | Iran actor/location link, selected oil words in URL path/query, their conjunction, and OPC group-code match. |
| Actor/location foreign keys | Stable references to normalized profiles. |

Topic flags are code and keyword heuristics that identify candidates for review.
They do not establish causation, confirmed topics, or comprehensive coverage.

## Project overview and API

This four-person project extracts public news-derived event data with
`requests`, cleans it with pandas, loads related PostgreSQL tables using
psycopg2, and queries the database through SQLAlchemy in a Streamlit/Plotly
dashboard. Docker Compose coordinates the database, ETL job, and dashboard.

The source is the [GDELT Project](https://www.gdeltproject.org/data.html). Our
implementation uses its public archive feed without an API key:

| Endpoint | Purpose |
| --- | --- |
| [lastupdate.txt](https://data.gdeltproject.org/gdeltv2/lastupdate.txt) | Locate the newest Events archive. |
| [masterfilelist.txt](https://data.gdeltproject.org/gdeltv2/masterfilelist.txt) | Enumerate archive URLs, compressed sizes, and published MD5 checksums. |
| `https://data.gdeltproject.org/gdeltv2/YYYYMMDDHHMMSS.export.CSV.zip` | Download one Events batch. |

The timestamped index supplies archive selection instead of page-number
pagination. Events are the production dataset. Optional Mentions extraction and
sample validation exist, but the current loader does not create a Mentions table.
Keep `INCLUDE_MENTIONS=false` for the documented pipeline.

## Research questions and dashboard scope

The team's Phase 1 questions guide the analysis:

| Question | Intended analysis |
| --- | --- |
| 1. Correlation between religions and human-rights organizations | Most-mentioned religions and organizations; most frequent religion/organization pairs. |
| 2a. Compare the volume and themes of coverage involving major religions | Religion coverage and average tone over time; top five event types involving religion. |
| 2b. Distinguish religious reporting from rights-violation coverage | Compare religion-coded only, rights-related only, and overlapping events using actor religion codes and the selected rights-related CAMEO codes. |
| 3. Compare how the Iran Oil Crisis affects countries | Countries interacting most with Iran and the cooperation/conflict patterns of those interactions. |
| 4a. Where are protests associated with escalation? | Average Goldstein score by country for protest reports, alongside subsequent higher-impact events. |
| 4b. Where are protests associated with change? | Common subsequent responses, such as concessions, arrests, and force. |

The completed dashboard, developed by Tony, organizes these questions into five
research views: religions and rights organizations, religion coverage, religion
versus rights, Iran and oil, and protests. Each view combines charts with
explanations of its measures. The Dashboard section below describes the views
and interactive controls.

Cooccurrence counts alone do not establish correlation. Average Goldstein scores
for protest-coded reports do not establish that protests caused later escalation
or change. Protest-response comparisons use the same country on protest days
with at least five protest events and the following two days; these are temporal
associations rather than links between individual incidents. Country/location
links also do not directly measure economic oil impacts.

## PostgreSQL schema and ERD

### Schema Table:

| Table | Primary key | Contents and relationships |
| --- | --- | --- |
| `events` | `globaleventid` | 40 columns: dates, measures, source information, flags, and references to one event type, two actors, and three location roles. |
| `event_types` | `eventcode` | 4 columns: detailed/base/root codes and root label. Referenced by `events.eventcode`. |
| `actors` | `actorid` | 9 columns: profile ID, code, name, country, group, ethnicity, and three roles. Referenced by `events.actor1id` and `events.actor2id`. |
| `locations` | `locationid` | 9 columns: profile ID, geographic type/name, country/subdivisions, coordinates, and feature ID. Referenced by `actor1geolocationid`, `actor2geolocationid`, and `actiongeolocationid` in `events`. |
| `actor_religions` | `(actorid, religioncode)` | Religion memberships; `actorid` references `actors`. |
| `etl_runs` | `day` | Independent load audit: folder/day, successful load time, and JSONB transformation report. |

Actor and location IDs are SHA-256 hashes of normalized descriptors, so identical
profiles receive the same key across batches. Missing optional attributes and
references remain NULL. Dates use `DATE`, recording times `TIMESTAMPTZ`, counts
`BIGINT`, measures `DOUBLE PRECISION`, flags `BOOLEAN`, and codes `TEXT`.
Event indexes cover date, type, action location, and both actor references.


### Final ERD
![PostgreSQL schema exported from pgAdmin](docs/images/erd.png)

Decision Context:
The design is a **star schema**: `events` is the fact table, surrounded by the dimension tables
`actors`, `locations` and `event_types`. `actor_religions` is a bridge table for the
many-to-many relationship between actors and religions, and `etl_runs` is a standalone audit log.

### Loading into PostgreSQL:

`load.py` runs after transformation and loads one daily folder at a time:

1. Create the tables, keys and indexes if they don't exist (safe to rerun).
2. For each `data/transformed/YYYYMMDD/` folder, in date order, open one transaction.
3. Load the parent tables first (`event_types`, `actors`, `locations`,
   `actor_religions`), then `events`, so every foreign key has a parent.
4. Each table is copied with `COPY` into a staging table, then inserted with
   primary-key conflict handling. Existing dimension rows are left unchanged;
   an event is updated only if the incoming `dateadded` is newer.
5. Record the day's quality report in `etl_runs`, then commit.
6. If any step fails, that day rolls back and earlier days stay loaded.

Because loads are upserts, rerunning or overlapping date ranges never creates
duplicate rows.


## Run the complete stack

Prerequisites: Git, Docker Desktop (or Docker Engine with Compose), internet
access to GDELT, and enough disk space for CSVs and PostgreSQL. The measured CSV
footprint of the earlier six-month research collection is approximately 19.14 GB
before database storage. The standard two-day run collects less data; its disk
usage depends on news volume and database storage.

1. Clone the repository and enter its directory:

   ```sh
   git clone https://github.com/RedRojoaka/DAEN_328_GDELT_Project.git
   cd DAEN_328_GDELT_Project
   ```

2. Copy the configuration template:

   ```sh
   cp .env.sample .env
   ```

   Edit `.env` and set `POSTGRES_PASSWORD` to a unique local password. Keep
   `POSTGRES_DB=gdelt`, `POSTGRES_USER=gdelt`, `POSTGRES_HOST=db`,
   `POSTGRES_PORT=5432`, and `INCLUDE_MENTIONS=false` for Compose.
   `.env` is ignored by Git. Compose enforces the internal database host/port
   for Python services.

   Set the agreed two-day testing window:

   ```dotenv
   LOOKBACK_DAYS=2
   INCLUDE_MENTIONS=false
   EXTRACT_START=
   EXTRACT_END=
   ```

   Clear any previous fixed `EXTRACT_START` / `EXTRACT_END` values so they do not
   override the rolling window. An explicit `LOOKBACK_DAYS=2` takes precedence
   over an older local `LOOKBACK_MONTHS` setting. Six-month research collection
   is not required for the standard startup.

3. Start the full stack with the assignment's command:

   ```sh
   docker-compose up --build
   ```

   The equivalent Compose v2 spelling is `docker compose up --build`.
   PostgreSQL starts first; after its health check, ETL runs extraction,
   transformation, and loading in order. The dashboard starts after ETL exits
   successfully. No manual database creation or ETL trigger is needed.

4. Open the dashboard after loading completes:

   | Service | Address / behavior |
   | --- | --- |
   | Streamlit | [http://localhost:8501](http://localhost:8501); change `DASHBOARD_PORT` in `.env` if occupied. |
   | PostgreSQL | `localhost:5433` for host tools such as pgAdmin; database/user/password come from `.env`. Within Compose: `db:5432`. |
   | ETL | Batch job, logs in its container, exits with code 0 on success; no browser endpoint. |

Stop while preserving the named `postgres_data` database volume:

```sh
docker-compose down
```

Run `docker-compose up --build` again to rerun the pipeline. Avoid `down -v`
unless intentionally deleting the database. Changing `.env` credentials does not
change credentials already initialized in an existing database volume.

`ETL_WORK_DIR` is included in the sample but currently unused by the scripts.
See [docs/DOCKER.md](docs/DOCKER.md) for additional Docker/dependency notes;
the startup sequence above reflects the current application.

## Incremental pulls and duplicate prevention

Archive timestamps identify batches; `GLOBALEVENTID` identifies records.
`get_lastupdate_timestamp()` finds the newest upstream batch, rather than the
last locally loaded file. `get_starting_timestamp()` selects an explicit boundary
or the configured lookback; `saved_timestamps()` lists completed local CSVs.

Extraction selects timestamps in `(start, end]`, reuses completed batches, and
downloads missing archives inside the window, including gaps before newer cached
files. The standard configuration selects a two-day lookback in UTC. Downloads
use retries/backoff and published MD5 checks; malformed records are quarantined.
Network failures or logged archive gaps stop extraction and prevent the next ETL
script from running. Investigate `data/skipped_events.txt` before retrying:
logged archive failures block progress.

Transformation deduplicates event IDs within each reporting-day batch, keeping
the latest `DATEADDED` and the last input row on a tie. Loading uses staging
tables, `COPY`, and primary-key conflict handling. Events update only when the
incoming recording timestamp is newer or the stored timestamp is NULL; existing
dimension keys are left unchanged. Each day's load is transactional, and primary
keys prevent duplicate database rows.

### Progress storage and current restart limitation

PostgreSQL's `etl_runs` stores successful load days, `loaded_at`, and quality
reports. **Extraction does not currently read a database checkpoint.** Its cache
is the timestamped files in `data/batches/`; transformation and loading revisit
available daily exports.

The default Compose file persists PostgreSQL but does not mount `data/`.
Removing containers with `down` loses their raw-file cache, so a fresh ETL
container can download the configured lookback again while database keys prevent
duplicate rows. The assignment's requirement to resume extraction from the last
successful position stored in PostgreSQL still needs implementation.
`etl_runs` is a load audit, not an implemented extraction checkpoint.

## Cleaning and data quality

Separate functions in `transform.py` normalize missing values/text, parse dates,
convert numbers, standardize event codes, quarantine invalid rows, deduplicate,
and derive analysis features. Missing-value handling is explicit:

| Fields / condition | Handling |
| --- | --- |
| Blank strings and explicit `null`, `none`, `nan`, `n/a` tokens | Normalize to NULL; preserve the valid code `NA`. |
| Missing/invalid `GLOBALEVENTID` or `SQLDATE` | Quarantine the row with original values and a reason. |
| Optional actor, location, URL, count, score, and recording-date fields | Retain missing values as NULL; invalid typed dates/numbers also become NULL. Do not invent values or zero counts. |
| Out-of-range numbers | Set the invalid value to NULL. |
| `EventCode` | Preserve leading zeros and validate structure; full CAMEO dictionary membership is not checked. |
| `EventBaseCode`, `EventRootCode` | Derive from the validated full code. |
| Unmapped action countries / geographic type 0 | Keep ISO3 NULL / omit the unmatched location reference. |

The measured daily reports record 8 invalid full event codes, with corresponding
invalid base/root values, and 1 invalid action longitude set to NULL. Missing
optional values were retained, including 7,865,626 actor-1 country values,
10,057,851 actor-2 country values, and 507,266 action latitudes. Zero quarantines
in these transformation reports does not establish zero malformed upstream rows.

Extraction prints received, malformed, cached, and ready row counts.
Transformation prints input/cleaned rows, duplicates, quarantines, and per-column
missing/invalid/derived counts. Loading prints rows read and **combined
inserted/updated** counts per table; separate insert and update totals required
by the assignment are not yet reported. Daily `quality_report.json` and
`rejected_events.csv` preserve evidence; successful load reports are also stored
in PostgreSQL's `etl_runs` table.

### Tests and logs

Run the assignment's test command:

```sh
docker-compose run --rm etl pytest
```

Run unit tests without starting PostgreSQL:

```sh
docker compose build etl
docker compose run --rm --no-deps etl python -m pytest -q
```

Tests use hand-built records and mocked HTTP responses. They cover cleaning,
missing-value policies, code preservation, stable keys/relationships, daily
batching, extraction windows/cache behavior, failure handling, and sample
validation. They do not prove complete Docker startup or a database-driven
incremental restart. [tests/README.md](tests/README.md) describes optional real
GDELT sample validation.

```sh
docker compose logs --tail=100 etl
docker compose run --rm etl python scripts/docker_smoke.py
```

The second command checks packages, permissions, and database connectivity;
it does not validate the complete extraction or dashboard.

## Dashboard

The app reads PostgreSQL through SQLAlchemy. Use the horizontal navigation to
switch research views, and expand the sidebar for event-date, action-country,
religion, and human-rights organization filters. The default date range is the
latest 182 days of loaded event dates; the slider covers at most the latest 400
days. Display controls group denominations into world religions, switch between
event counts and summed media mentions, and select daily, weekly, or monthly
trends. SQL filter values are bound parameters.

| Research view | Visualizations and analysis |
| --- | --- |
| Q1 · Religions × rights orgs | Religion and organization rankings, religion coverage over time, frequent pairs, a sunburst, and a pairing heatmap. Queries join `events`, `actors`, and `actor_religions`. |
| Q2a · Religion coverage | Volume versus tone, coverage and tone trends for the five leading religions in the selection, and the top five event types. Taxonomy queries join `event_types`. |
| Q2b · Religion vs. rights | Overlap metrics, coverage trends, tone/Goldstein comparisons, relative group sizes, and event-type comparisons for religion-only, rights-only, and overlapping events. |
| Q3 · Iran & oil | Partner-country event counts and Goldstein rankings/maps, Iran and oil-candidate coverage/tone trends, and action-country coverage maps. Partner queries join both actor profiles. |
| Q4 · Protests | Country Goldstein rankings/maps, volume versus conflict level, and concession/arrest/repression/coercion/force comparisons within protest windows, including response-rate lift versus other days. |

Coverage counts reflect news reporting and GDELT coding, rather than independent
counts of all real-world events. Rights and oil classifications remain heuristic;
the charts do not establish that protests caused subsequent responses.

### Dashboard screenshots

The screenshots below show the running dashboard's research views and
interactive filters using the previously collected research dataset.

<table>
  <tr>
    <td align="center">
      <img src="docs/images/Dashboard_preview_1.png" alt="Dashboard Preview Q1" width="450"><br>
      <sub>Q1 · Religions × rights organizations</sub>
    </td>
    <td align="center">
      <img src="docs/images/Dashboard_preview_2.png" alt="Dashboard Preview Q2b: Religion vs. rights" width="450"><br>
      <sub>Q2b · Religion vs. rights</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <img src="docs/images/Dashboard_preview_3.png" alt="Dashboard Preview Q3" width="450"><br>
      <sub>Q3 · Iran &amp; oil</sub>
    </td>
    <td align="center">
      <img src="docs/images/Dashboard_preview_4.png" alt="Dashboard Preview Q4" width="450"><br>
      <sub>Q4 · Protests</sub>
    </td>
  </tr>
</table>

## CSV collection in Docker

This workflow preserves raw CSVs and daily exports on the host for inspection and
retrying. It does not load PostgreSQL or launch the dashboard. Use the same
two-day settings shown in the startup instructions. With Docker running and
`.env` configured:

```sh
docker compose build etl
docker compose run --rm --no-deps etl python -m pytest -q
```

Download the configured archive window, then transform the cached batches one
reporting day at a time:

```sh
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/data:/app/data" \
  etl sh -c 'python extract.py && python transform.py'
```

Raw batches remain in `data/batches/`; normalized tables, rejected rows, and
quality reports go to `data/transformed/YYYYMMDD/`. This bind mount preserves the
cache for retries. The UID/GID option is intended for macOS/Linux or WSL shells.
Building images and running unit tests do not start a full download.

Rerun transformation without downloading again:

```sh
docker compose run --rm --no-deps \
  --user "$(id -u):$(id -g)" \
  --volume "$PWD/data:/app/data" \
  etl python transform.py
```

## Repository guide

| Path | Responsibility |
| --- | --- |
| `extract.py` | Index selection, HTTP downloads, validation, timestamped raw batches. |
| `transform.py` | Cleaning, features, normalized daily CSVs, quality reports. |
| `load.py` | Schema creation, transactions, conflict handling, load audit. |
| `streamlit_app.py` | Five database-backed research views, charts, and interactive filters. |
| `tests/`, `scripts/` | Unit tests, Docker connection check, GDELT sample validation. |
| `Dockerfile`, `docker-compose.yml` | Python image and database/ETL/dashboard services. |
| `requirements.in`, `requirements.txt` | Direct dependencies and pinned packages. |
| `.env.sample` | Configuration template without secrets. |
| `data/` | Local generated files, excluded from Git and Docker builds. |

## Group members and contributions

| Member | Contribution |
| --- | --- |
| Maya Brumbalow | Extraction implementation, building presentation, transformation changes, dashboard planning. |
| Peyton Kocher | Daily GDELT processing, validation tests, Docker setup, infrastructure checks. |
| Tony (Anastacio) Moreno | PostgreSQL loading, database schema/pgAdmin work, and completed dashboard development. |
| Alejandro Rojo | Transformation, repository integration/documentation, and ERD export/README integration. |
