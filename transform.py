"""Clean extracted GDELT batches without downloading data.

Run ``python transform.py`` for daily CSV exports, or pass a DataFrame to
``transform(events_df)``. Use ``iter_transformed_days`` for daily loading.
"""

import argparse
import hashlib
import json
from dataclasses import dataclass
from itertools import groupby
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pandas as pd


PROJECT_DIR = Path(__file__).resolve().parent
BATCH_DIR = PROJECT_DIR / "data" / "batches"
OUTPUT_DIR = PROJECT_DIR / "data" / "transformed"

# Optional actor and location fields remain NULL when absent.
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


# GeoNames countryInfo.txt (2026-11-11), FIPS and ISO3 columns:
# https://download.geonames.org/export/dump/countryInfo.txt (CC BY 4.0).
# GDELT's separate Gaza/West Bank codes both join to PSE. Unmapped codes
# (including historical entities and Kosovo without an official ISO3) stay NULL.
FIPS_TO_ISO3 = dict(pair.split(":") for pair in """
    AA:ABW AC:ATG AE:ARE AF:AFG AG:DZA AJ:AZE AL:ALB AM:ARM AN:AND AO:AGO
    AQ:ASM AR:ARG AS:AUS AU:AUT AV:AIA AY:ATA BA:BHR BB:BRB BC:BWA BD:BMU
    BE:BEL BF:BHS BG:BGD BH:BLZ BK:BIH BL:BOL BM:MMR BN:BEN BO:BLR BP:SLB
    BR:BRA BT:BTN BU:BGR BV:BVT BX:BRN BY:BDI CA:CAN CB:KHM CD:TCD CE:LKA
    CF:COG CG:COD CH:CHN CI:CHL CJ:CYM CK:CCK CM:CMR CN:COM CO:COL CQ:MNP
    CS:CRI CT:CAF CU:CUB CV:CPV CW:COK CY:CYP DA:DNK DJ:DJI DO:DMA DR:DOM
    EC:ECU EG:EGY EI:IRL EK:GNQ EN:EST ER:ERI ES:SLV ET:ETH EZ:CZE FG:GUF
    FI:FIN FJ:FJI FK:FLK FM:FSM FO:FRO FP:PYF FR:FRA FS:ATF GA:GMB GB:GAB
    GG:GEO GH:GHA GI:GIB GJ:GRD GK:GGY GL:GRL GM:DEU GP:GLP GQ:GUM GR:GRC
    GT:GTM GV:GIN GY:GUY GZ:PSE HA:HTI HK:HKG HM:HMD HO:HND HR:HRV HU:HUN
    IC:ISL ID:IDN IM:IMN IN:IND IO:IOT IR:IRN IS:ISR IT:ITA IV:CIV IZ:IRQ
    JA:JPN JE:JEY JM:JAM JO:JOR KE:KEN KG:KGZ KN:PRK KR:KIR KS:KOR KT:CXR
    KU:KWT KZ:KAZ LA:LAO LE:LBN LG:LVA LH:LTU LI:LBR LO:SVK LS:LIE LT:LSO
    LU:LUX LY:LBY MA:MDG MB:MTQ MC:MAC MD:MDA MF:MYT MG:MNG MH:MSR MI:MWI
    MJ:MNE MK:MKD ML:MLI MN:MCO MO:MAR MP:MUS MR:MRT MT:MLT MU:OMN MV:MDV
    MX:MEX MY:MYS MZ:MOZ NC:NCL NE:NIU NF:NFK NG:NER NH:VUT NI:NGA NL:NLD
    NN:SXM NO:NOR NP:NPL NR:NRU NS:SUR NU:NIC NZ:NZL OD:SSD PA:PRY PC:PCN
    PE:PER PK:PAK PL:POL PM:PAN PO:PRT PP:PNG PS:PLW PU:GNB QA:QAT RE:REU
    RI:SRB RM:MHL RN:MAF RO:ROU RP:PHL RQ:PRI RS:RUS RW:RWA SA:SAU SB:SPM
    SC:KNA SE:SYC SF:ZAF SG:SEN SH:SHN SI:SVN SL:SLE SM:SMR SN:SGP SO:SOM
    SP:ESP ST:LCA SU:SDN SV:SJM SW:SWE SX:SGS SY:SYR SZ:CHE TB:BLM TD:TTO
    TH:THA TI:TJK TK:TCA TL:TKL TN:TON TO:TGO TP:STP TS:TUN TT:TLS TU:TUR
    TV:TUV TW:TWN TX:TKM TZ:TZA UC:CUW UG:UGA UK:GBR UP:UKR US:USA UV:BFA
    UY:URY UZ:UZB VC:VCT VE:VEN VI:VGB VM:VNM VQ:VIR VT:VAT WA:NAM WE:PSE
    WF:WLF WI:ESH WS:WSM WZ:SWZ YM:YEM ZA:ZMB ZI:ZWE
""".split())


@dataclass
class TransformResult:
    cleaned_events: pd.DataFrame
    tables: dict[str, pd.DataFrame]
    rejected_events: pd.DataFrame
    quality_report: dict


@dataclass
class MentionTransformResult:
    cleaned_mentions: pd.DataFrame
    rejected_mentions: pd.DataFrame
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


def add_analysis_features(events, *, start_date=None, end_date=None):
    """Add transparent filters for the proposal; flags do not establish causation."""
    cleaned = events.copy()
    cleaned["EventMonth"] = cleaned["SQLDATE"].dt.strftime("%Y-%m").astype("string")
    cleaned["ReportDate"] = cleaned["DATEADDED"].dt.floor("D")
    cleaned["ReportMonth"] = cleaned["ReportDate"].dt.strftime("%Y-%m").astype("string")
    # Keep actual event dates; a missing study window leaves the flag NULL.
    bounds = []
    for value in (start_date, end_date):
        day = None if value is None else pd.Timestamp(value)
        if day is not None:
            if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
                raise ValueError("Study bounds must be valid timezone-free calendar dates")
        bounds.append(day)
    start, end = bounds
    if start is not None and end is not None and start > end:
        raise ValueError("start_date must not be after end_date")
    has_window = start is not None or end is not None
    in_window = pd.Series(True if has_window else pd.NA, index=cleaned.index, dtype="boolean")
    if start is not None:
        in_window &= cleaned["SQLDATE"].ge(start)
    if end is not None:
        in_window &= cleaned["SQLDATE"].le(end)
    cleaned["IsInAnalysisWindow"] = in_window
    # Map action-country codes for joins; unknown codes remain NULL.
    cleaned["ActionGeo_CountryISO3"] = cleaned["ActionGeo_CountryCode"].map(FIPS_TO_ISO3).astype("string")
    cleaned["EventRootLabel"] = cleaned["EventRootCode"].map(ROOT_LABELS).astype("string")
    cleaned["QuadClassLabel"] = cleaned["QuadClass"].map(QUAD_LABELS).astype("string")
    cleaned["IsProtest"] = cleaned["EventRootCode"].eq("14").astype("boolean")
    cleaned["IsMaterialConflict"] = cleaned["QuadClass"].eq(4).astype("boolean")
    cleaned["IsConcessionEvent"] = cleaned["EventRootCode"].eq("08").astype("boolean")
    cleaned["IsRightsRelatedEvent"] = cleaned["EventCode"].isin(RIGHTS_EVENT_CODES).astype("boolean")
    cleaned.loc[cleaned["EventCode"].isna(), "IsRightsRelatedEvent"] = pd.NA
    # Exclude government-related types from the stricter HRI flag; keep raw HRI.
    religious = {}
    rights_actor = {}
    for actor in (1, 2):
        religious[actor] = cleaned[[f"Actor{actor}Religion{i}Code" for i in (1, 2)]].notna().any(axis=1)
        types = cleaned[[f"Actor{actor}Type{i}Code" for i in (1, 2, 3)]]
        hri = types.eq("HRI").any(axis=1)
        government = types.isin(["GOV", "MIL", "COP", "JUD", "SPY"]).any(axis=1)
        cleaned[f"Actor{actor}IsHumanRightsCoded"] = hri
        rights_actor[actor] = hri & ~government
        cleaned[f"Actor{actor}IsHumanRightsActor"] = rights_actor[actor]
        cleaned[f"Actor{actor}HumanRightsNameCandidate"] = cleaned[f"Actor{actor}Name"].str.contains(
            r"\b(?:AMNESTY INTERNATIONAL|HUMAN RIGHTS WATCH|HUMAN RIGHTS)\b", na=False, regex=True,
        )
    cleaned["HasReligionCode"] = religious[1] | religious[2]
    cleaned["ReligionHumanRightsCooccurrence"] = (
        (religious[1] & rights_actor[2]) | (religious[2] & rights_actor[1])
    )
    # URL keywords identify candidates, not confirmed oil-crisis coverage.
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
    cleaned["HasOPCActor"] = cleaned[["Actor1KnownGroupCode", "Actor2KnownGroupCode"]].eq("OPC").any(axis=1)
    cleaned["IranOil"] = cleaned["OilURLCandidate"] & cleaned["IsIranLinked"]
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


def transform(events, *, start_date=None, end_date=None):
    """Transform one in-memory batch; return tables and quality evidence.

    Optional missing attributes remain NULL. Only unusable IDs/dates quarantine a
    row. No filtering by topic is applied, so denominators remain available.
    """
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
    cleaned = add_analysis_features(cleaned, start_date=start_date, end_date=end_date)
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
        "outside_analysis_window": int((~cleaned["IsInAnalysisWindow"]).sum()),
        "window_not_evaluated": int(cleaned["IsInAnalysisWindow"].isna().sum()),
        "analysis_start": str(start_date) if start_date is not None else None,
        "analysis_end": str(end_date) if end_date is not None else None,
        "unmapped_action_country_codes": sorted(cleaned.loc[
            cleaned["ActionGeo_CountryCode"].notna() & cleaned["ActionGeo_CountryISO3"].isna(),
            "ActionGeo_CountryCode",
        ].unique().tolist()),
        "table_rows": {name: len(frame) for name, frame in tables.items()},
    }
    print(f"Transform: {len(raw):,} input rows; {len(cleaned):,} after cleaning; "
          f"{duplicates_removed:,} duplicates removed; {len(rejected_raw):,} rows quarantined.")
    print_missing_summary(report["missing_values"])
    return TransformResult(cleaned, tables, rejected_raw.reset_index(drop=True), report)


def print_missing_summary(columns):
    """Print per-column missing-value counts and handling policies."""
    for column, counts in columns.items():
        print(f"  {column}: {counts['input_missing']} missing, "
              f"{counts['invalid_values_set_null']} invalid -> NULL, "
              f"{counts['values_filled_or_derived']} derived; {counts['policy']}")


# Select archives separately from the dates of the events they describe.


def batch_paths(input_dir):
    """Find only completed timestamp-named extraction files, in archive order."""
    folder = Path(input_dir)
    if not folder.is_dir():
        raise ValueError(f"Batch directory does not exist: {folder}")
    paths = sorted(path for path in folder.glob("*.csv")
                   if len(path.stem) == 14 and path.stem.isascii() and path.stem.isdigit())
    if not paths:
        raise ValueError(f"No timestamped CSV batches in {folder}; run extract.py first")
    return paths


def iter_transformed_days(input_dir=BATCH_DIR, *, timestamps=None, start_date=None, end_date=None):
    """Yield the last archive timestamp and cleaned tables for each day.

    Pass ExtractResult.ready to select batches; an empty list produces no output.
    Study dates flag events without changing the archive selection.
    """
    if timestamps is None:
        paths = batch_paths(input_dir)
    else:
        stamps = sorted(set(timestamps))
        for stamp in stamps:
            if len(stamp) != 14 or not stamp.isascii() or not stamp.isdigit():
                raise ValueError("Archive timestamps must be YYYYMMDDHHMMSS")
        paths = [Path(input_dir) / f"{stamp}.csv" for stamp in stamps]
    for day, files in groupby(paths, key=lambda path: path.stem[:8]):
        files = list(files)
        raw = pd.concat(
            [pd.read_csv(path, dtype="string", keep_default_na=False) for path in files],
            ignore_index=True,
        )
        result = transform(raw, start_date=start_date, end_date=end_date)
        del raw
        yield files[-1].stem, result
        del result


def transform_mentions(mentions, existing_event_ids):
    """Clean mentions and keep only references to existing events.

    Return cleaned rows, rejected rows, and quality counts. The loader assigns
    MentionID and prevents duplicate inserts across runs.
    """
    required = {"GLOBALEVENTID", "EventTimeDate", "MentionTimeDate", "MentionIdentifier"}
    missing = required - set(mentions.columns)
    if missing:
        raise ValueError(f"Missing required mention columns: {', '.join(sorted(missing))}")
    raw = mentions.drop(columns="MentionID", errors="ignore").reset_index(drop=True).copy()
    before = normalize_missing_values(raw)
    cleaned = convert_numeric_values(before)
    for column in ("EventTimeDate", "MentionTimeDate"):
        values = cleaned[column].where(cleaned[column].str.fullmatch(r"\d{14}", na=False))
        cleaned[column] = pd.to_datetime(values, format="%Y%m%d%H%M%S", errors="coerce", utc=True)
    for column, high in {
        "MentionType": 3, "SentenceID": 2**31 - 1, "Actor1CharOffset": 2**31 - 1,
        "Actor2CharOffset": 2**31 - 1, "ActionCharOffset": 2**31 - 1,
        "InRawText": 1, "Confidence": 100, "MentionDocLen": 2**31 - 1,
    }.items():
        if column in cleaned:
            values = pd.to_numeric(cleaned[column], errors="coerce")
            low = 1 if column == "MentionType" else 0
            cleaned[column] = values.where(values.between(low, high) & values.mod(1).eq(0)).astype("Int64")
    if "MentionDocTone" in cleaned:
        values = pd.to_numeric(cleaned["MentionDocTone"], errors="coerce")
        cleaned["MentionDocTone"] = values.where(values.between(-100, 100)).astype("Float64")
    known_ids = pd.Index(existing_event_ids).astype("int64")
    orphan = ~cleaned["GLOBALEVENTID"].isin(known_ids)
    invalid = cleaned[["EventTimeDate", "MentionTimeDate", "MentionIdentifier"]].isna().any(axis=1)
    kept = cleaned.loc[~(orphan | invalid)].drop_duplicates().reset_index(drop=True)
    rejected = raw.loc[orphan | invalid].copy()
    rejected["RejectionReason"] = [
        "; ".join(reason for reason, applies in (
            ("invalid or unknown GLOBALEVENTID", orphan.loc[index]),
            ("missing/invalid mention timestamp or identifier", invalid.loc[index]),
        ) if applies) for index in rejected.index
    ]
    missing_values = {
        column: {
            "input_missing": int(before[column].isna().sum()),
            "invalid_values_set_null": int((before[column].notna() & cleaned[column].isna()).sum()),
            "values_filled_or_derived": 0,
            "policy": "quarantine row" if column in required else "retain NULL",
        } for column in before.columns
    }
    report = {
        "input_rows": len(raw), "cleaned_rows": len(kept),
        "rejected_rows": len(rejected),
        "unknown_event_rows": int(orphan.sum()), "invalid_mention_rows": int(invalid.sum()),
        "duplicates_removed": int((~(orphan | invalid)).sum()) - len(kept),
        "missing_values": missing_values,
    }
    print(f"Mentions: {len(raw)} input; {len(kept)} cleaned; "
          f"{len(rejected)} quarantined; {report['duplicates_removed']} duplicates removed.")
    print_missing_summary(missing_values)
    return MentionTransformResult(kept, rejected.reset_index(drop=True), report)


# Export daily tables for inspection without combining the whole dataset.


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=BATCH_DIR, help="Directory of timestamped event CSVs")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--start-date", help="Inclusive SQLDATE analysis start (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="Inclusive SQLDATE analysis end (YYYY-MM-DD)")
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output_dir.resolve()
    if source == output or source in output.parents or output in source.parents:
        parser.error("Use an output directory separate from the raw batch tree.")
    try:
        for through, result in iter_transformed_days(
            args.input, start_date=args.start_date, end_date=args.end_date,
        ):
            folder = args.output_dir / through[:8]
            folder.mkdir(parents=True, exist_ok=True)
            for name, frame in result.tables.items():
                frame.to_csv(folder / f"{name}.csv", index=False)
            result.rejected_events.to_csv(folder / "rejected_events.csv", index=False)
            (folder / "quality_report.json").write_text(
                json.dumps(result.quality_report, indent=2) + "\n", encoding="utf-8",
            )
            print(f"Wrote daily tables to {folder}")
            del result
    except (OSError, ValueError, pd.errors.ParserError) as exception:
        parser.exit(1, f"Transform failed: {exception}\n")


if __name__ == "__main__":
    main()
