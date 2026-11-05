"""Clean the GDELT Events CSV produced by extract.py, without downloading data.

Run ``python transform.py`` after extraction, or pass an extracted DataFrame to
``transform(events_df)``. See README.md for table keys and analysis limitations.
"""

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent
RAW_FILE = PROJECT_DIR / "data" / "gdelt_events_raw.csv"
OUTPUT_DIR = PROJECT_DIR / "data" / "transformed"

# Do not import extract.py: its module-level extract() call performs network I/O.
ACTOR_FIELDS = [
    "Code", "Name", "CountryCode", "KnownGroupCode", "EthnicCode",
    "Religion1Code", "Religion2Code", "Type1Code", "Type2Code", "Type3Code",
]
GEO_FIELDS = [
    "Type", "FullName", "CountryCode", "ADM1Code", "ADM2Code",
    "Lat", "Long", "FeatureID",
]
GEO_PREFIXES = ("Actor1Geo", "Actor2Geo", "ActionGeo")
REQUIRED_COLUMNS = {
    "GLOBALEVENTID", "SQLDATE", "DATEADDED", "EventCode", "QuadClass",
    "GoldsteinScale", "AvgTone", "NumMentions", "NumSources", "NumArticles",
}
OPTIONAL_COLUMNS = (
    [f"Actor{actor}{field}" for actor in (1, 2) for field in ACTOR_FIELDS]
    + [f"{prefix}_{field}" for prefix in GEO_PREFIXES for field in GEO_FIELDS]
    + ["IsRootEvent", "SOURCEURL", "EventBaseCode", "EventRootCode"]
)
ROOT_LABELS = dict(zip(
    [f"{number:02d}" for number in range(1, 21)],
    [
        "Make public statement", "Appeal", "Express intent to cooperate",
        "Consult", "Engage in diplomatic cooperation", "Engage in material cooperation",
        "Provide aid", "Yield", "Investigate", "Demand", "Disapprove", "Reject",
        "Threaten", "Protest", "Exhibit force posture", "Reduce relations",
        "Coerce", "Assault", "Fight", "Use unconventional mass violence",
    ],
))
QUAD_LABELS = {
    1: "Verbal cooperation", 2: "Material cooperation",
    3: "Verbal conflict", 4: "Material conflict",
}
# A deliberately narrow, documented selection, not all human rights coverage.
RIGHTS_EVENT_CODES = {
    "0243", "0343", "0833", "092", "1043", "1122", "1233",
    "1413", "1423", "1433", "1443", "1453",
}


@dataclass
class TransformResult:
    cleaned_events: pd.DataFrame
    tables: dict[str, pd.DataFrame]
    rejected_events: pd.DataFrame
    quality_report: dict


def normalize_missing_values(events):
    """Trim strings and standardize explicit missing tokens; preserve code 'NA'."""
    cleaned = events.copy()
    for column in cleaned.columns:
        values = cleaned[column].astype("string").str.strip()
        missing = values.str.lower().isin(["", "null", "none", "nan", "n/a"])
        cleaned[column] = values.mask(missing, pd.NA)
    return cleaned


def normalize_text(events):
    """Uppercase actor names/codes; preserve URL and location-name capitalization."""
    cleaned = events.copy()
    for column in cleaned.columns:
        if column.endswith("Code") or column in ("Actor1Name", "Actor2Name"):
            cleaned[column] = cleaned[column].str.upper()
    for column in ("Actor1Name", "Actor2Name"):
        if column in cleaned:
            cleaned[column] = cleaned[column].str.replace(r"\s+", " ", regex=True)
    return cleaned


def convert_dates(events):
    """Parse event day separately from the UTC time GDELT first recorded it."""
    cleaned = events.copy()
    for column, width, date_format, utc in (
        ("SQLDATE", 8, "%Y%m%d", False),
        ("DATEADDED", 14, "%Y%m%d%H%M%S", True),
    ):
        values = cleaned[column].astype("string")
        values = values.where(values.str.fullmatch(rf"\d{{{width}}}", na=False))
        cleaned[column] = pd.to_datetime(values, format=date_format, errors="coerce", utc=utc)
    return cleaned


def convert_numeric_values(events):
    """Coerce invalid/out-of-range values to NULL, never invent zero counts."""
    cleaned = events.copy()
    integer_ranges = {
        "GLOBALEVENTID": (1, 2**63 - 1), "IsRootEvent": (0, 1),
        "QuadClass": (1, 4), "NumMentions": (0, 2**63 - 1),
        "NumSources": (0, 2**63 - 1), "NumArticles": (0, 2**63 - 1),
        **{f"{prefix}_Type": (0, 5) for prefix in GEO_PREFIXES},
    }
    float_ranges = {
        "GoldsteinScale": (-10, 10), "AvgTone": (-100, 100),
        **{f"{prefix}_Lat": (-90, 90) for prefix in GEO_PREFIXES},
        **{f"{prefix}_Long": (-180, 180) for prefix in GEO_PREFIXES},
    }
    for column, (low, high) in integer_ranges.items():
        if column not in cleaned:
            continue
        # Integer strings are parsed individually to avoid float rounding of IDs.
        def parse_integer(value):
            if pd.isna(value):
                return pd.NA
            try:
                number = int(str(value))
            except ValueError:
                return pd.NA
            return number if low <= number <= high else pd.NA

        cleaned[column] = cleaned[column].map(parse_integer).astype("Int64")
    for column, (low, high) in float_ranges.items():
        if column in cleaned:
            values = pd.to_numeric(cleaned[column], errors="coerce").astype("Float64")
            cleaned[column] = values.where(values.between(low, high))
    return cleaned


def standardize_event_codes(events):
    """Keep leading zeros; derive consistent parent codes from the full code.

    This checks code structure, not membership in the entire CAMEO dictionary.
    Unpadded numeric input cannot be reconstructed unambiguously: read CSV as str.
    """
    cleaned = events.copy()
    values = cleaned["EventCode"].astype("string")
    valid = values.str.fullmatch(r"(?:0[1-9]|1[0-9]|20)\d{0,2}", na=False)
    cleaned["EventCode"] = values.where(valid)
    cleaned["EventRootCode"] = cleaned["EventCode"].str[:2]
    cleaned["EventBaseCode"] = cleaned["EventCode"].str[:3]
    return cleaned


def separate_invalid_events(events):
    """Quarantine only rows without a valid primary key or event date."""
    invalid_id = events["GLOBALEVENTID"].isna()
    invalid_date = events["SQLDATE"].isna()
    rejected = events.loc[invalid_id | invalid_date].copy()
    rejected["RejectionReason"] = [
        "; ".join(reason for reason, missing in (
            ("invalid or missing GLOBALEVENTID", invalid_id.loc[index]),
            ("invalid or missing SQLDATE", invalid_date.loc[index]),
        ) if missing)
        for index in rejected.index
    ]
    return events.loc[~(invalid_id | invalid_date)].copy(), rejected


def remove_duplicates(events):
    """Keep the latest DATEADDED per ID; equal timestamps keep last input row."""
    return (
        events.sort_values("DATEADDED", kind="stable", na_position="first")
        .drop_duplicates("GLOBALEVENTID", keep="last")
        .sort_values("GLOBALEVENTID", kind="stable")
        .reset_index(drop=True)
    )


def _source_parts(value):
    if pd.isna(value):
        return pd.NA, pd.NA
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return pd.NA, pd.NA
        return parsed.hostname.lower().removeprefix("www."), unquote(parsed.path + "?" + parsed.query)
    except ValueError:
        return pd.NA, pd.NA


def add_analysis_features(events):
    """Add transparent filters for the proposal; flags do not establish causation."""
    cleaned = events.copy()
    cleaned["EventMonth"] = cleaned["SQLDATE"].dt.strftime("%Y-%m").astype("string")
    cleaned["ReportDate"] = cleaned["DATEADDED"].dt.floor("D")
    cleaned["EventRootLabel"] = cleaned["EventRootCode"].map(ROOT_LABELS).astype("string")
    cleaned["QuadClassLabel"] = cleaned["QuadClass"].map(QUAD_LABELS).astype("string")
    cleaned["IsProtest"] = cleaned["EventRootCode"].eq("14").astype("boolean")
    cleaned["IsMaterialConflict"] = cleaned["QuadClass"].eq(4).astype("boolean")
    cleaned["IsConcessionEvent"] = cleaned["EventRootCode"].eq("08").astype("boolean")
    cleaned["IsRightsRelatedEvent"] = cleaned["EventCode"].isin(RIGHTS_EVENT_CODES).astype("boolean")
    cleaned.loc[cleaned["EventCode"].isna(), "IsRightsRelatedEvent"] = pd.NA
    religious = {}
    rights_actor = {}
    for actor in (1, 2):
        religious[actor] = cleaned[[f"Actor{actor}Religion{i}Code" for i in (1, 2)]].notna().any(axis=1)
        rights_actor[actor] = cleaned[[f"Actor{actor}Type{i}Code" for i in (1, 2, 3)]].eq("HRI").any(axis=1)
        cleaned[f"Actor{actor}IsHumanRightsCoded"] = rights_actor[actor]
        cleaned[f"Actor{actor}HumanRightsNameCandidate"] = cleaned[f"Actor{actor}Name"].str.contains(
            r"\b(?:AMNESTY INTERNATIONAL|HUMAN RIGHTS WATCH|HUMAN RIGHTS)\b", na=False, regex=True,
        )
    cleaned["HasReligionCode"] = religious[1] | religious[2]
    cleaned["ReligionHumanRightsCooccurrence"] = (
        (religious[1] & rights_actor[2]) | (religious[2] & rights_actor[1])
    )
    cleaned["IsIranLinked"] = (
        cleaned[["Actor1CountryCode", "Actor2CountryCode"]].eq("IRN").any(axis=1)
        | cleaned[[f"{prefix}_CountryCode" for prefix in GEO_PREFIXES]].eq("IR").any(axis=1)
    )
    parts = [_source_parts(value) for value in cleaned["SOURCEURL"]]
    cleaned["SourceDomain"] = pd.Series([part[0] for part in parts], index=cleaned.index, dtype="string")
    url_text = pd.Series([part[1] for part in parts], index=cleaned.index, dtype="string")
    cleaned["OilURLCandidate"] = url_text.str.contains(
        r"(?i)(?:^|[^a-z0-9])(?:oil|petroleum|crude|hormuz)(?:$|[^a-z0-9])", regex=True,
    ).astype("boolean")
    return cleaned


def _stable_ids(frame):
    """Hash complete descriptors with explicit nulls; keys survive reruns/batches."""
    def make_key(row):
        values = [None if pd.isna(value) else str(value) for value in row]
        if all(value is None for value in values):
            return pd.NA
        payload = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return pd.Series(
        [make_key(row) for row in frame.itertuples(index=False, name=None)],
        index=frame.index, dtype="string",
    )


def build_tables(events):
    """Split cleaned records into related tables for the future PostgreSQL loader."""
    facts = events.copy()
    actor_frames, location_frames = [], []
    for actor in (1, 2):
        columns = [f"Actor{actor}{field}" for field in ACTOR_FIELDS]
        profiles = events[columns].copy()
        profiles.columns = ACTOR_FIELDS
        profiles.insert(0, "ActorID", _stable_ids(profiles))
        facts[f"Actor{actor}ID"] = profiles["ActorID"]
        actor_frames.append(profiles)
        facts = facts.drop(columns=columns)
    actors = pd.concat(actor_frames, ignore_index=True).dropna(subset=["ActorID"]).drop_duplicates("ActorID")
    religion_frames = []
    for slot in (1, 2):
        religion_frames.append(actors[["ActorID", f"Religion{slot}Code"]].rename(
            columns={f"Religion{slot}Code": "ReligionCode"},
        ))
    actor_religions = pd.concat(religion_frames, ignore_index=True).dropna(subset=["ReligionCode"]).drop_duplicates()
    actors = actors.drop(columns=["Religion1Code", "Religion2Code"])
    for prefix in GEO_PREFIXES:
        columns = [f"{prefix}_{field}" for field in GEO_FIELDS]
        locations = events[columns].copy()
        locations.columns = GEO_FIELDS
        # Type 0 means no geographic match, not a real location.
        locations.loc[locations["Type"].eq(0).fillna(False), :] = pd.NA
        locations.insert(0, "LocationID", _stable_ids(locations))
        facts[f"{prefix}LocationID"] = locations["LocationID"]
        location_frames.append(locations)
        facts = facts.drop(columns=columns)
    locations = pd.concat(location_frames, ignore_index=True).dropna(subset=["LocationID"]).drop_duplicates("LocationID")
    event_types = events[["EventCode", "EventBaseCode", "EventRootCode", "EventRootLabel"]].dropna(
        subset=["EventCode"],
    ).drop_duplicates("EventCode")
    # Redundant calendar fields are derivable from SQLDATE; event taxonomy lives in event_types.
    facts = facts.drop(columns=[
        "EventBaseCode", "EventRootCode", "EventRootLabel", "MonthYear", "Year", "FractionDate",
    ], errors="ignore")
    return {name: frame.reset_index(drop=True) for name, frame in {
        "events": facts, "actors": actors, "actor_religions": actor_religions,
        "locations": locations, "event_types": event_types,
    }.items()}


def transform(events=None):
    """Transform a batch (or the full raw CSV); return tables and quality evidence.

    Optional missing attributes remain NULL. Only unusable IDs/dates quarantine a
    row. No filtering by topic is applied, so denominators remain available.
    """
    if events is None:
        events = pd.read_csv(RAW_FILE, dtype="string", keep_default_na=False)
    missing_columns = REQUIRED_COLUMNS - set(events.columns)
    if missing_columns:
        raise ValueError(f"Missing required extract columns: {', '.join(sorted(missing_columns))}")
    raw = events.copy().reset_index(drop=True)
    cleaned = normalize_missing_values(raw)
    missing_before = cleaned.isna().sum()
    for column in OPTIONAL_COLUMNS:
        if column not in cleaned:
            cleaned[column] = pd.Series(pd.NA, index=cleaned.index, dtype="string")
    cleaned = normalize_text(cleaned)
    before_conversion = cleaned.copy()
    cleaned = convert_dates(cleaned)
    cleaned = convert_numeric_values(cleaned)
    cleaned = standardize_event_codes(cleaned)
    converted_columns = {column: cleaned[column] for column in before_conversion.columns}
    missing_after_conversion = cleaned.isna().sum()
    invalid_values = {
        column: int((before_conversion[column].notna() & cleaned[column].isna()).sum())
        for column in before_conversion.columns
    }
    valid, rejected = separate_invalid_events(cleaned)
    # Keep the original unparsed values for inspection of rejected rows.
    rejected_raw = raw.loc[rejected.index].copy()
    rejected_raw["RejectionReason"] = rejected["RejectionReason"]
    cleaned = remove_duplicates(valid)
    duplicates_removed = len(valid) - len(cleaned)
    cleaned = add_analysis_features(cleaned)
    tables = build_tables(cleaned)
    report = {
        "input_rows": len(raw), "cleaned_rows": len(cleaned),
        "duplicates_removed": duplicates_removed, "rejected_rows": len(rejected_raw),
        "missing_values": {
            column: {
                "input_missing": int(missing_before.get(column, len(raw))),
                "invalid_values_set_null": invalid_values[column],
                "values_filled_or_derived": int((before_conversion[column].isna() & cleaned_value.notna()).sum()),
                "nulls_before_row_filter": int(missing_after_conversion[column]),
                "policy": (
                    "quarantine row" if column in ("GLOBALEVENTID", "SQLDATE")
                    else "derive from EventCode" if column in ("EventBaseCode", "EventRootCode")
                    else "retain NULL"
                ),
            }
            for column, cleaned_value in converted_columns.items()
        },
        "table_rows": {name: len(frame) for name, frame in tables.items()},
    }
    print(f"Transform: {len(raw):,} input rows; {len(cleaned):,} after cleaning; "
          f"{duplicates_removed:,} duplicates removed; {len(rejected_raw):,} rows quarantined.")
    for column, counts in report["missing_values"].items():
        print(f"  {column}: {counts['input_missing']} missing, "
              f"{counts['invalid_values_set_null']} invalid -> NULL, "
              f"{counts['values_filled_or_derived']} derived; {counts['policy']}")
    return TransformResult(cleaned, tables, rejected_raw.reset_index(drop=True), report)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=RAW_FILE)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if args.input.resolve() == args.output_dir.resolve() or args.input.resolve().parent == args.output_dir.resolve():
        parser.error("Use a separate output directory to preserve the raw input.")
    try:
        raw = pd.read_csv(args.input, dtype="string", keep_default_na=False)
        result = transform(raw)
    except (OSError, ValueError, pd.errors.ParserError) as exception:
        parser.exit(1, f"Transform failed: {exception}\nRun extract.py first or supply --input PATH.\n")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in result.tables.items():
        frame.to_csv(args.output_dir / f"{name}.csv", index=False)
    result.cleaned_events.to_csv(args.output_dir / "gdelt_events_clean.csv", index=False)
    result.rejected_events.to_csv(args.output_dir / "rejected_events.csv", index=False)
    (args.output_dir / "quality_report.json").write_text(
        json.dumps(result.quality_report, indent=2) + "\n", encoding="utf-8",
    )
    print(f"Wrote transformed data to {args.output_dir}")


if __name__ == "__main__":
    main()
