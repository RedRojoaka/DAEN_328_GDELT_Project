# libraries
import os
import io
import hashlib
import zipfile
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from dataclasses import dataclass, field

# using env folder
load_dotenv()


# variables
LOOKBACK_DAYS = 3
LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"      # always updates with the last updated files
MASTER_LIST_URL = "https://data.gdeltproject.org/gdeltv2/masterfilelist.txt" # every file
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(PROJECT_DIR, "data")
BATCH_DIR = os.path.join(RAW_DIR, "batches")
REJECT_DIR = os.path.join(RAW_DIR, "rejected")
RAW_FILE = os.path.join(RAW_DIR, "gdelt_events_raw.csv")
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
EXPECTED_FIELDS = len(EVENT_COLUMNS)


#! FAILURES -------------------------------------------------------------------

class ExtractionError(Exception):
    """Raised when extraction cannot make any progress."""


@dataclass
class ExtractResult:
    status: str         
    saved: list = field(default_factory=list)
    stopped_at: str | None = None


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


def get_starting_timestamp(last, saved_on_disk):
    # use the last argument if given, otherwise the lookback window
    start = last or (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d%H%M%S")

    # never start after the newest batch, so a long gap between runs still gets filled
    if saved_on_disk:
        start = min(start, saved_on_disk[-1])

    print(f"Starting file for extraction: {start}")
    return start

def identify_files_to_pull(last, latest, saved_on_disk):
    # identifying extract timeframe
    start = get_starting_timestamp(last, saved_on_disk)
    print(f"Current extract getting files from {start} to {latest}")

    # request and exception
    try:
        response = requests.get(MASTER_LIST_URL, timeout = 300)
        response.raise_for_status()
    except requests.exceptions.RequestException as exception:
        raise ExtractionError(f"Could not access {MASTER_LIST_URL}: {exception}") from exception
    
    # finding files
    already_saved = set(saved_on_disk)
    files = []
    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) != 3: continue        # skip if line has too many/too little parts
        file_size, file_id, url = parts
        if ".export." not in url: continue   # grabs only files with event data
        timestamp = url.rsplit("/", 1)[-1].split(".")[0]    # extracts timestamp of the event uplad
        if start < timestamp <= latest and timestamp not in already_saved:            
            files.append((timestamp, url, file_id))
            if len(files) % 50 == 0:
                print(f"{len(files)} files found...")

    # return files in order
    files.sort()
    if files: 
        print(f"There are {len(files)} files to be extracted")

    return files


def download_file(url, file_id):
    # request and exceptiion
    try: 
        response = requests.get(url, timeout=60)
        response.raise_for_status()
    except requests.exceptions.RequestException as exception: 
        # if timeout exceeded or error occurs
        print(f"Exception raised for {url}: {exception}")
        return None
    
    # checking that file id's match 
    if hashlib.md5(response.content).hexdigest() != file_id:
        print(f"Checksum mismatch, will retry next run: {url}")
        return None
    
    return response.content


#! REJECTED RECORDS ROWS ----------------------------------------------------------

def split_valid_records(text):
    # identifying invalid records
    valid, rejected = [], []
    for line in text.split("\n"):  
        line = line.rstrip("\r")
        if not line:
            continue
        if line.count("\t") == EXPECTED_FIELDS - 1:
            valid.append(line)
        else:
            rejected.append(line)
    return valid, rejected


def save_rejected(rejected, timestamp):
    # make rejects csv
    os.makedirs(REJECT_DIR, exist_ok=True)
    path = os.path.join(REJECT_DIR, f"{timestamp}.csv")
    pd.DataFrame({"raw_record": rejected}).to_csv(path, index=False)
    print(f"Quarantined {len(rejected)} malformed rows: {path}")


#! TABLE BUILDING FUNCTIONS -------------------------------------------------------

def read_in_events(data, timestamp):
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
    valid, rejected = split_valid_records(text)
    if rejected:
        save_rejected(rejected, timestamp)
    if rejected and not valid:          # nothing usable: stop instead of skipping it
        print(f"Every row in {timestamp} was malformed.")
        return None
    if not valid:                       # a genuinely empty archive
        return pd.DataFrame(columns=EVENT_COLUMNS, dtype=str)
    
    # creating the events df
    events = pd.read_csv(
        io.StringIO("\n".join(valid)),
        sep="\t",
        header=None,
        names=EVENT_COLUMNS,
        dtype=str,
        quoting=3,
        keep_default_na=False,
        lineterminator="\n",
    )

    return events


#! SAVING / READING BATCHES -------------------------------------------------------

def save_batch(events, timestamp):
    # saves all records from individual zip file
    os.makedirs(BATCH_DIR, exist_ok=True)
    final_path = os.path.join(BATCH_DIR, f"{timestamp}.csv")

    # creates temp file
    tmp_path = final_path + ".tmp"
    events.to_csv(tmp_path, index=False)

    # rename once the write is complete
    os.replace(tmp_path, final_path)


def saved_timestamps():
    # gets timestamps from saved batch filenames
    if not os.path.isdir(BATCH_DIR):
        return []
    return sorted(
        name.removesuffix(".csv")
        for name in os.listdir(BATCH_DIR)
        if name.endswith(".csv")
    )

def read_batch(timestamp):
    path = os.path.join(BATCH_DIR, f"{timestamp}.csv")
    return pd.read_csv(path, dtype=str, keep_default_na=False)

def append_to_raw(events):
    # multiple runs add to the raw file
    os.makedirs(RAW_DIR, exist_ok=True)
    write_header = not os.path.exists(RAW_FILE) or os.path.getsize(RAW_FILE) == 0
    events.to_csv(RAW_FILE, mode="a", header=write_header, index=False)


#! HANDLING CRASHES -------------------------------------------------------

def repair_raw_file():
    # removes a half-written last row left by a crash
    if not os.path.exists(RAW_FILE):
        return

    size = os.path.getsize(RAW_FILE)

    # only the end of the file needs checking
    start = max(0, size - 1_048_576)    
    with open(RAW_FILE, "r+b") as f:
        f.seek(start)
        last_newline = f.read().rfind(b"\n")
        f.truncate(start + last_newline + 1)

def verify_raw_file(timestamps):
    # the raw file must hold at least every finished batch
    paths = [os.path.join(BATCH_DIR, f"{timestamp}.csv") for timestamp in timestamps]
    with open(paths[0], "rb") as f:
        header_bytes = len(f.readline())

    expected = sum(os.path.getsize(path) - header_bytes for path in paths) + header_bytes
    if os.path.getsize(RAW_FILE) < expected:
        raise ExtractionError(
            f"{RAW_FILE} is smaller than its saved batches. "
            f"Delete {BATCH_DIR} and {RAW_FILE}, then run again to rebuild them."
        )


#! CREATING DF AND CSV -------------------------------------------------------


def extract(last=None):
    # finding where to start/resume extracting
    # last overrides LOOKBACK_DAYS as the start of the window 
    saved_on_disk = saved_timestamps()
    if saved_on_disk and not os.path.exists(RAW_FILE):
        raise ExtractionError(
            f"{RAW_FILE} is missing but batches exist. "
            f"Delete {BATCH_DIR} and run again to rebuild it."
        )

    # fixes crash mid-extraction
    repair_raw_file()
    if saved_on_disk:
        verify_raw_file(saved_on_disk)


    # find the files to download
    latest = get_lastupdate_timestamp()
    files = identify_files_to_pull(last, latest, saved_on_disk)
    if not files:
        print("No new files to extract.")
        return ExtractResult("no_new_files")

    # tracking what this run saved and where it stopped
    saved, stopped_at = [], None

    # downloading files
    for file, (timestamp, url, file_id) in enumerate(files, start=1):
        data = download_file(url, file_id)
        events = None if data is None else read_in_events(data, timestamp)
        if events is None:              # stop so a later run can't leave a gap
            print(f"Ending at {timestamp}.")
            stopped_at = timestamp
            break

        # dropping duplicated based on globalid
        events = events.drop_duplicates(subset="GLOBALEVENTID")
        append_to_raw(events)           # rows first
        save_batch(events, timestamp)   # marks this archive as finished
        saved.append(timestamp)
        if file % 50 == 0:
            print(f"{file} files saved...")

    # report
    if stopped_at is None:
        return ExtractResult("success", saved)
    if saved:
        return ExtractResult("partial", saved, stopped_at)
    raise ExtractionError(f"No archives saved; stopped at {stopped_at}")


#! RUN EXTRACTION ----------------------------------------------------------------

if __name__ == "__main__":
    try:
        result = extract()
    except ExtractionError as exception:
        raise SystemExit(f"Extraction failed: {exception}")

    print(f"Extract finished: {result.status}, {len(result.saved)} files saved")
    if result.status == "partial":
        raise SystemExit(f"Extraction stopped early at {result.stopped_at}")