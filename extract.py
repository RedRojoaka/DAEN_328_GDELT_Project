# libraries
import os
import io
import time
import hashlib
import zipfile
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from dataclasses import dataclass, field

# using env folder
load_dotenv()


# variables
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "2"))
INCLUDE_MENTIONS = os.getenv("INCLUDE_MENTIONS", "false").lower() == "true"
DOWNLOAD_WORKERS = 8
CHUNK_SIZE = 32
MAX_RETRIES = 3
LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"
MASTER_LIST_URL = "https://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(PROJECT_DIR, "data")
BATCH_DIR = os.path.join(RAW_DIR, "batches")
REJECT_DIR = os.path.join(RAW_DIR, "rejected")
# event schema
EVENT_COLUMNS = [
    "GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code",
    "Actor2Code", "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode",
    "Actor2EthnicCode", "Actor2Religion1Code", "Actor2Religion2Code",
    "Actor2Type1Code", "Actor2Type2Code", "Actor2Type3Code",
    "IsRootEvent", "EventCode", "EventBaseCode", "EventRootCode", "QuadClass",
    "GoldsteinScale", "NumMentions", "NumSources", "NumArticles", "AvgTone",
    "Actor1Geo_Type", "Actor1Geo_FullName", "Actor1Geo_CountryCode",
    "Actor1Geo_ADM1Code", "Actor1Geo_ADM2Code", "Actor1Geo_Lat",
    "Actor1Geo_Long", "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code", "Actor2Geo_Lat",
    "Actor2Geo_Long", "Actor2Geo_FeatureID",
    "ActionGeo_Type", "ActionGeo_FullName", "ActionGeo_CountryCode",
    "ActionGeo_ADM1Code", "ActionGeo_ADM2Code", "ActionGeo_Lat",
    "ActionGeo_Long", "ActionGeo_FeatureID",
    "DATEADDED", "SOURCEURL",
]
# mentions schema
MENTION_COLUMNS = [
    "GLOBALEVENTID", "EventTimeDate", "MentionTimeDate", "MentionType",
    "MentionSourceName", "MentionIdentifier", "SentenceID",
    "Actor1CharOffset", "Actor2CharOffset", "ActionCharOffset", "InRawText",
    "Confidence", "MentionDocLen", "MentionDocTone",
    "MentionDocTranslationInfo", "Extras",
]
# one entry per stream, replaces the global EXPECTED_FIELDS
STREAMS = {
    "events": EVENT_COLUMNS,
    "mentions": MENTION_COLUMNS,
}



#! FAILURES -------------------------------------------------------------------

class ExtractionError(Exception):
    """Raised when extraction cannot make any progress."""


@dataclass
class ExtractResult:
    status: str
    saved: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    stopped_at: str | None = None
    ready: list = field(default_factory=list)
    quality_report: dict = field(default_factory=dict)

#! PATHS PER STREAM -------------------------------------------------------

def batch_dir(stream):
    return BATCH_DIR if stream == "events" else os.path.join(BATCH_DIR, stream)

def skipped_path(stream):
    return os.path.join(RAW_DIR, f"skipped_{stream}.txt")


def skipped_timestamps(stream):
    path = skipped_path(stream)
    if not os.path.exists(path):
        return set()
    with open(path) as f:
        return {line.split(",")[0] for line in f if line.strip()}


def mark_skipped(stream, timestamp, reason):
    os.makedirs(RAW_DIR, exist_ok=True)
    with open(skipped_path(stream), "a") as f:
        f.write(f"{timestamp},{reason}\n")
    print(f"Archive failure recorded for {stream} {timestamp}: {reason}")


#! EXTRACTION FUNCTIONS -------------------------------------------------------

def get_lastupdate_timestamp():
    try:
        response = requests.get(LASTUPDATE_URL, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as exception:
        raise ExtractionError(f"Could not access {LASTUPDATE_URL}: {exception}") from exception

    # finding timestamp
    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) == 3 and ".export." in parts[2]:
            filename = parts[2].rsplit("/", 1)[-1]
            print(f"Latest update file found: {filename}")
            return filename.split(".")[0]

    # if nothing was found in LASTUPDATE_URL
    raise ExtractionError("lastupdate.txt did not list an events file.")


def get_starting_timestamp(last, saved_on_disk=None):
    """Use the database checkpoint, or the lookback window on the first run."""
    start = last if last is not None else (
        datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    ).strftime("%Y%m%d%H%M%S")
    if len(start) != 14 or not start.isascii() or not start.isdigit():
        raise ValueError("Archive boundary must be YYYYMMDDHHMMSS")
    datetime.strptime(start, "%Y%m%d%H%M%S")
    return start


def get_master_list():
    try:
        response = requests.get(MASTER_LIST_URL, timeout=300, stream=True)
        response.raise_for_status()
        lines = [line for line in response.iter_lines(decode_unicode=True) if line]
    except requests.exceptions.RequestException as exception:
        raise ExtractionError(f"Could not access {MASTER_LIST_URL}: {exception}") from exception
    return lines


def identify_files_to_pull(stream, master_lines, last, latest, saved_on_disk):
    # finding archives after the database checkpoint, including saved files
    start = get_starting_timestamp(last, saved_on_disk)
    print(f"Current {stream} extract getting files from {start} to {latest}")

    # finding files
    marker = ".export." if stream == "events" else f".{stream}."
    files = []
    for line in master_lines:
        parts = line.split()
        if len(parts) != 3: continue        # skip if line has too many/too little parts
        file_size, file_id, url = parts
        if marker not in url: continue      # grabs only files for this stream
        timestamp = url.rsplit("/", 1)[-1].split(".")[0]    # extracts timestamp of the event uplad
        if start < timestamp <= latest:
            files.append((timestamp, url, file_id))
            if len(files) % 100 == 0:
                print(f"{len(files)} files found...")

    # return files in order
    files.sort()
    if files:
        print(f"There are {len(files)} {stream} files to be extracted")

    return files


def download_file(url, file_id):
    reason = "unknown"
    for attempt in range(1, MAX_RETRIES + 1):
        # request and exceptiion
        try:
            response = requests.get(url, timeout=60)
            if response.status_code == 404:
                return "skip", "404 not found"
            response.raise_for_status()
        except requests.exceptions.RequestException as exception:
            # if timeout exceeded or error occurs
            reason = f"request failed: {exception}"
        else:
            # checking that file id's match
            if hashlib.md5(response.content).hexdigest() == file_id:
                return "ok", response.content
            reason = "checksum mismatch"

        if attempt < MAX_RETRIES:
            time.sleep(2 ** attempt)    # wait 2s, then 4s

    print(f"Gave up on {url} after {MAX_RETRIES} tries: {reason}")
    if reason == "checksum mismatch":
        return "skip", reason
    return "stop", reason


#! REJECTED RECORDS ROWS ----------------------------------------------------------

def split_valid_records(text, expected_fields):
    # identifying invalid records
    valid, rejected = [], []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line:
            continue
        if line.count("\t") == expected_fields - 1:
            valid.append(line)
        else:
            rejected.append(line)
    return valid, rejected


def save_rejected(rejected, timestamp, stream):
    # make rejects csv
    os.makedirs(REJECT_DIR, exist_ok=True)
    path = os.path.join(REJECT_DIR, f"{stream}_{timestamp}.csv")
    pd.DataFrame({"raw_record": rejected}).to_csv(path, index=False)
    print(f"Quarantined {len(rejected)} malformed rows: {path}")


#! TABLE BUILDING FUNCTIONS -------------------------------------------------------

def read_in_file(data, timestamp, stream, counts=None):
    columns = STREAMS[stream]

    # unzips each update file from gdelt site
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
            raw_bytes = zip_file.read(zip_file.namelist()[0])
    except (zipfile.BadZipFile, IndexError) as exception:
        print(f"Could not read file {timestamp}: {exception}")
        return None

    # bytes to text
    text = raw_bytes.decode("utf-8", errors="replace")

    # separate valid and invalid rows
    valid, rejected = split_valid_records(text, len(columns))
    if counts is not None:
        counts["received_from_api"] += len(valid) + len(rejected)
        counts["malformed_rows"] += len(rejected)
    if rejected:
        save_rejected(rejected, timestamp, stream)
    if rejected and not valid:          # nothing usable
        print(f"Every row in {timestamp} was malformed.")
        return None
    if not valid:                       # a genuinely empty archive
        return pd.DataFrame(columns=columns, dtype=str)

    # creating the df
    df = pd.read_csv(
        io.StringIO("\n".join(valid)),
        sep="\t",
        header=None,
        names=columns,
        dtype=str,
        quoting=3,
        keep_default_na=False,
        lineterminator="\n",
    )

    return df


#! SAVING / READING BATCHES -------------------------------------------------------

def save_batch(df, timestamp, stream):
    # saves all records from individual zip file
    folder = batch_dir(stream)
    os.makedirs(folder, exist_ok=True)
    final_path = os.path.join(folder, f"{timestamp}.csv")

    # creates temp file
    tmp_path = final_path + ".tmp"
    df.to_csv(tmp_path, index=False)

    # rename once the write is complete
    os.replace(tmp_path, final_path)


def saved_timestamps(stream="events"):
    # gets timestamps from saved batch filenames
    folder = batch_dir(stream)
    if not os.path.isdir(folder):
        return []
    return sorted(
        name.removesuffix(".csv")
        for name in os.listdir(folder)
        if name.endswith(".csv")
    )


def read_batch(timestamp, stream="events"):
    path = os.path.join(batch_dir(stream), f"{timestamp}.csv")
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def iter_batches(stream="events", after=None, timestamps=None):
    """Read one archive at a time; pass result.ready to exclude unfinished batches."""
    for timestamp in sorted(timestamps if timestamps is not None else saved_timestamps(stream)):
        if after is None or timestamp > after:
            yield timestamp, read_batch(timestamp, stream)


#! CREATING DF AND CSV -------------------------------------------------------

def extract(stream="events", last=None, master_lines=None, latest=None):
    """Download or reuse batches in order, stopping at the first unavailable archive."""
    if stream not in STREAMS:
        raise ValueError(f"Unknown stream: {stream}")
    start = get_starting_timestamp(last)
    latest = latest if latest is not None else get_lastupdate_timestamp()
    get_starting_timestamp(latest)  # checking the end timestamp
    if start > latest:
        raise ValueError("Starting archive boundary is after latest")
    master_lines = get_master_list() if master_lines is None else master_lines
    cached = set(saved_timestamps(stream))
    blocked = skipped_timestamps(stream)
    files = identify_files_to_pull(stream, master_lines, start, latest, cached)
    counts = {"received_from_api": 0, "malformed_rows": 0,
              "cached_rows": 0, "rows_ready": 0, "duplicates_removed": 0}
    result = ExtractResult("no_new_files", quality_report=counts)

    # download a chunk in parallel, then save that chunk in order
    def obtain(file):
        timestamp, url, checksum = file
        if timestamp in cached:
            return "cached", None
        if timestamp in blocked:
            return "skip", "previously logged archive failure"
        return download_file(url, checksum)

    with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        for offset in range(0, len(files), CHUNK_SIZE):
            chunk = files[offset:offset + CHUNK_SIZE]
            for (timestamp, _, _), (status, payload) in zip(chunk, pool.map(obtain, chunk)):
                if status in ("stop", "skip"):
                    if status == "skip":
                        if timestamp not in blocked:
                            mark_skipped(stream, timestamp, payload)
                        result.skipped.append(timestamp)
                    result.stopped_at = timestamp
                    break
                if status == "cached":
                    frame = read_batch(timestamp, stream)
                    counts["cached_rows"] += len(frame)
                else:
                    frame = read_in_file(payload, timestamp, stream, counts)
                    if frame is None:
                        mark_skipped(stream, timestamp, "unreadable or all rows malformed")
                        result.skipped.append(timestamp)
                        result.stopped_at = timestamp
                        break
                    # leave duplicates for transform to remove and count
                    save_batch(frame, timestamp, stream)
                    result.saved.append(timestamp)
                counts["rows_ready"] += len(frame)
                result.ready.append(timestamp)
                del frame
            if result.stopped_at is not None:
                break
    if result.stopped_at is not None:
        result.status = "partial" if result.ready else "blocked"
    elif result.ready:
        result.status = "success"
    print(f"Extract {stream}: {result.status}; {counts['received_from_api']} rows received "
          f"from processed API archives, {counts['malformed_rows']} malformed; "
          f"{counts['cached_rows']} cached rows; {counts['rows_ready']} rows ready; "
          "0 duplicates removed here (handled by transform).")
    if result.stopped_at:
        print(f"Coverage gap at {result.stopped_at}; do not advance a checkpoint past it.")
    return result



#! RUN EXTRACTION ----------------------------------------------------------------

if __name__ == "__main__":
    # runs events, plus mentions if INCLUDE_MENTIONS=true
    streams = ["events"] + (["mentions"] if INCLUDE_MENTIONS else [])
    try:
        latest = get_lastupdate_timestamp()
        master_lines = get_master_list()        # downloaded once, used by both
        results = {s: extract(s, master_lines=master_lines, latest=latest) for s in streams}
    except (ExtractionError, OSError, ValueError, pd.errors.ParserError) as exception:
        raise SystemExit(f"Extraction failed: {exception}")

    for stream, result in results.items():
        print(f"{stream} extract finished: {result.status}, "
              f"{len(result.saved)} files saved, {len(result.skipped)} skipped")
    partial = [s for s, r in results.items() if r.status in ("partial", "blocked")]
    if partial:
        raise SystemExit(f"Extraction stopped early for: {', '.join(partial)}")
