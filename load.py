"""Load the tables written by transform.py into PostgreSQL.

Reads data/transformed/{event_types,actors,locations,actor_religions,events}.csv
and upserts them in a single transaction, so reruns and overlapping extract
windows are safe. Connection settings come from .env (POSTGRES_*).

Run ``python load.py`` after ``python transform.py``.
"""

import argparse
import io
import json
import os
import time
from pathlib import Path

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2 import sql

load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
INPUT_DIR = PROJECT_DIR / "data" / "transformed"
CHUNK_ROWS = 50_000

# Column names are lowercased in Postgres so the dashboard can query them unquoted.
# Order matters: parents first, events last (foreign keys).
TABLES = {
    "event_types": {
        "pk": ["EventCode"],
        "columns": [
            ("EventCode", "TEXT NOT NULL"),
            ("EventBaseCode", "TEXT"),
            ("EventRootCode", "TEXT"),
            ("EventRootLabel", "TEXT"),
        ],
    },
    "actors": {
        "pk": ["ActorID"],
        "columns": [
            ("ActorID", "TEXT NOT NULL"),
            ("Code", "TEXT"),
            ("Name", "TEXT"),
            ("CountryCode", "TEXT"),
            ("KnownGroupCode", "TEXT"),
            ("EthnicCode", "TEXT"),
            ("Type1Code", "TEXT"),
            ("Type2Code", "TEXT"),
            ("Type3Code", "TEXT"),
        ],
    },
    "locations": {
        "pk": ["LocationID"],
        "columns": [
            ("LocationID", "TEXT NOT NULL"),
            ("Type", "SMALLINT"),
            ("FullName", "TEXT"),
            ("CountryCode", "TEXT"),
            ("ADM1Code", "TEXT"),
            ("ADM2Code", "TEXT"),
            ("Lat", "DOUBLE PRECISION"),
            ("Long", "DOUBLE PRECISION"),
            ("FeatureID", "TEXT"),
        ],
    },
    "actor_religions": {
        "pk": ["ActorID", "ReligionCode"],
        "columns": [
            ("ActorID", "TEXT NOT NULL"),
            ("ReligionCode", "TEXT NOT NULL"),
        ],
        "constraints": ["FOREIGN KEY (actorid) REFERENCES actors (actorid)"],
    },
    "events": {
        "pk": ["GLOBALEVENTID"],
        "newer_than": "DATEADDED",   # on conflict, keep the most recently added version
        "columns": [
            ("GLOBALEVENTID", "BIGINT NOT NULL"),
            ("SQLDATE", "DATE NOT NULL"),
            ("DATEADDED", "TIMESTAMPTZ"),
            ("EventMonth", "TEXT"),
            ("ReportDate", "DATE"),
            ("IsRootEvent", "SMALLINT"),
            ("EventCode", "TEXT"),
            ("QuadClass", "SMALLINT"),
            ("QuadClassLabel", "TEXT"),
            ("GoldsteinScale", "DOUBLE PRECISION"),
            ("AvgTone", "DOUBLE PRECISION"),
            ("NumMentions", "BIGINT"),
            ("NumSources", "BIGINT"),
            ("NumArticles", "BIGINT"),
            ("SOURCEURL", "TEXT"),
            ("SourceDomain", "TEXT"),
            ("Actor1ID", "TEXT"),
            ("Actor2ID", "TEXT"),
            ("Actor1GeoLocationID", "TEXT"),
            ("Actor2GeoLocationID", "TEXT"),
            ("ActionGeoLocationID", "TEXT"),
            ("IsProtest", "BOOLEAN"),
            ("IsMaterialConflict", "BOOLEAN"),
            ("IsConcessionEvent", "BOOLEAN"),
            ("IsRightsRelatedEvent", "BOOLEAN"),
            ("Actor1IsHumanRightsCoded", "BOOLEAN"),
            ("Actor1HumanRightsNameCandidate", "BOOLEAN"),
            ("Actor2IsHumanRightsCoded", "BOOLEAN"),
            ("Actor2HumanRightsNameCandidate", "BOOLEAN"),
            ("HasReligionCode", "BOOLEAN"),
            ("ReligionHumanRightsCooccurrence", "BOOLEAN"),
            ("IsIranLinked", "BOOLEAN"),
            ("OilURLCandidate", "BOOLEAN"),
            ("ReportMonth", "TEXT"),
            ("IsInAnalysisWindow", "BOOLEAN"),
            ("ActionGeo_CountryISO3", "TEXT"),
            ("Actor1IsHumanRightsActor", "BOOLEAN"),
            ("Actor2IsHumanRightsActor", "BOOLEAN"),
            ("HasOPCActor", "BOOLEAN"),
            ("IranOil", "BOOLEAN"),
        ],
        "constraints": [
            "FOREIGN KEY (eventcode) REFERENCES event_types (eventcode)",
            "FOREIGN KEY (actor1id) REFERENCES actors (actorid)",
            "FOREIGN KEY (actor2id) REFERENCES actors (actorid)",
            "FOREIGN KEY (actor1geolocationid) REFERENCES locations (locationid)",
            "FOREIGN KEY (actor2geolocationid) REFERENCES locations (locationid)",
            "FOREIGN KEY (actiongeolocationid) REFERENCES locations (locationid)",
        ],
    },
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS events_sqldate_idx ON events (sqldate)",
    "CREATE INDEX IF NOT EXISTS events_eventcode_idx ON events (eventcode)",
    "CREATE INDEX IF NOT EXISTS events_actiongeo_idx ON events (actiongeolocationid)",
    "CREATE INDEX IF NOT EXISTS events_actor1_idx ON events (actor1id)",
    "CREATE INDEX IF NOT EXISTS events_actor2_idx ON events (actor2id)",
]

ETL_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS etl_runs (
    day       TEXT PRIMARY KEY,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    report    JSONB
)
"""


def col(name):
    return sql.Identifier(name.lower())


def connect(retries=5):
    """Connect using POSTGRES_* env vars; retry briefly in case the DB is still waking up."""
    settings = dict(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )
    for attempt in range(1, retries + 1):
        try:
            return psycopg2.connect(**settings)
        except psycopg2.OperationalError as exception:
            if attempt == retries:
                raise SystemExit(f"Could not connect to Postgres at {settings['host']}:{settings['port']}: {exception}")
            print(f"Postgres not ready (attempt {attempt}/{retries}), retrying...")
            time.sleep(2)


def create_schema(cur):
    for name, spec in TABLES.items():
        parts = [sql.SQL("{} {}").format(col(c), sql.SQL(t)) for c, t in spec["columns"]]
        parts.append(sql.SQL("PRIMARY KEY ({})").format(sql.SQL(", ").join(col(c) for c in spec["pk"])))
        parts += [sql.SQL(constraint) for constraint in spec.get("constraints", [])]
        cur.execute(
            sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
                sql.Identifier(name), sql.SQL(", ").join(parts)
            )
        )
    for statement in INDEXES:
        cur.execute(statement)
    cur.execute(ETL_RUNS_DDL)


def upsert_statement(name, spec, stage):
    columns = [c for c, _ in spec["columns"]]
    column_list = sql.SQL(", ").join(col(c) for c in columns)
    pk_list = sql.SQL(", ").join(col(c) for c in spec["pk"])
    newer = spec.get("newer_than")

    # DISTINCT ON guards against duplicate keys inside one chunk.
    order = [pk_list]
    if newer:
        order.append(sql.SQL("{} DESC NULLS LAST").format(col(newer)))

    if newer:
        updates = sql.SQL(", ").join(
            sql.SQL("{0} = EXCLUDED.{0}").format(col(c)) for c in columns if c not in spec["pk"]
        )
        conflict = sql.SQL(
            "DO UPDATE SET {updates} WHERE {table}.{newer} IS NULL OR {table}.{newer} < EXCLUDED.{newer}"
        ).format(updates=updates, table=sql.Identifier(name), newer=col(newer))
    else:
        conflict = sql.SQL("DO NOTHING")

    return sql.SQL(
        "INSERT INTO {table} ({columns}) "
        "SELECT DISTINCT ON ({pk}) {columns} FROM {stage} ORDER BY {order} "
        "ON CONFLICT ({pk}) {conflict}"
    ).format(
        table=sql.Identifier(name), columns=column_list, pk=pk_list,
        stage=stage, order=sql.SQL(", ").join(order), conflict=conflict,
    )


def load_table(cur, name, spec, path):
    columns = [c for c, _ in spec["columns"]]
    header = set(pd.read_csv(path, nrows=0).columns)
    missing = [c for c in columns if c not in header]
    if missing:
        raise SystemExit(f"{path.name} is missing expected columns: {', '.join(missing)}")

    stage = sql.Identifier(f"stage_{name}")
    cur.execute(sql.SQL("CREATE TEMP TABLE {} (LIKE {}) ON COMMIT DROP").format(stage, sql.Identifier(name)))
    copy_sql = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv)").format(
        stage, sql.SQL(", ").join(col(c) for c in columns)
    ).as_string(cur)
    upsert = upsert_statement(name, spec, stage)

    rows_read = rows_written = 0
    # dtype=str + keep_default_na=False keeps codes like "NA" intact; empty string -> NULL in COPY.
    for chunk in pd.read_csv(path, dtype=str, keep_default_na=False, usecols=columns, chunksize=CHUNK_ROWS):
        buffer = io.StringIO()
        chunk[columns].to_csv(buffer, index=False, header=False)
        buffer.seek(0)
        cur.copy_expert(copy_sql, buffer)
        cur.execute(upsert)
        rows_written += cur.rowcount
        rows_read += len(chunk)
        cur.execute(sql.SQL("TRUNCATE {}").format(stage))

    print(f"  {name}: {rows_read:,} rows read, {rows_written:,} inserted/updated")
    return rows_read


def day_folders(input_dir):
    """Daily folders written by transform.py (data/transformed/YYYYMMDD/).

    A flat folder that directly holds the five table CSVs also works.
    """
    def complete(folder):
        return all((folder / f"{name}.csv").exists() for name in TABLES)

    if complete(input_dir):
        return [input_dir]
    if not input_dir.is_dir():
        return []
    return sorted(p for p in input_dir.iterdir() if p.is_dir() and complete(p))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    args = parser.parse_args()

    folders = day_folders(args.input_dir)
    if not folders:
        raise SystemExit(f"No complete table CSVs found in {args.input_dir}. Run transform.py first.")

    conn = connect()
    try:
        with conn, conn.cursor() as cur:
            create_schema(cur)

        # One transaction per day folder: a failure rolls back that day only,
        # and rerunning skips nothing but is safe (upserts).
        for folder in folders:
            with conn, conn.cursor() as cur:
                print(f"Loading {folder.name}...")
                for name, spec in TABLES.items():
                    load_table(cur, name, spec, folder / f"{name}.csv")

                report_path = folder / "quality_report.json"
                if report_path.exists():
                    report = json.loads(report_path.read_text(encoding="utf-8"))
                    cur.execute(
                        "INSERT INTO etl_runs (day, report) VALUES (%s, %s) "
                        "ON CONFLICT (day) DO UPDATE SET loaded_at = now(), report = EXCLUDED.report",
                        (folder.name, json.dumps(report)),
                    )
        print(f"Load complete: {len(folders)} day(s).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()