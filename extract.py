# libraries
import os
import io
import hashlib
import zipfile
import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

# using env folder
load_dotenv()

# local variables
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "3"))
LASTUPDATE_URL = "https://data.gdeltproject.org/gdeltv2/lastupdate.txt"      # always updates with the last updated files
MASTER_LIST_URL = "https://data.gdeltproject.org/gdeltv2/masterfilelist.txt" # every file
RAW_DIR = "data" 
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


#! EXTRACTION FUNCTIONS -------------------------------------------------------


def get_lastupdate_timestamp():
    """Gets the timestamp of the file in the LASTUPDATE_URL"""

    try:
        response = requests.get(LASTUPDATE_URL, timeout=30)
        response.raise_for_status()
    except requests.exceptions.RequestException as exception:
        print(f"Could not access GDELT ({LASTUPDATE_URL}): {exception}")
        return None
    
    # finding timestamp
    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) == 3 and ".export." in parts[2]:
            filename = parts[2].rsplit("/", 1)[-1]   
            print(f"Latest update file found: {filename}")
            return filename.split(".")[0]  
        
    # if nothing was found in LASTUPDATE_URL
    print("lastupdate.txt did not list an events file.")

    return None      


        
def get_starting_timestamp(last): 
    """Gets timestamp of the last pulled file. If it is the first run, it identifies the starting file. """
    
    if last:
        print(f"Starting file for extraction: {last}")
        return last        # if there is an existing stopping point from last pull, use that 
    
    start = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d%H%M%S") 
    print(f"Starting file for extraction: {start}")

    return start



def identify_files_to_pull(last, latest):
    """Identifies all the files that must be pulled within given timeframe"""

    # if get_latestupdate_timestamp() has an error
    if latest is None:
        print("No latest timestamp available; skipping this run.")
        return []
    
    # identifying extract timeframe
    start = get_starting_timestamp(last)
    print(f"Current extract getting files from {start} to {latest}")

    # request and exception
    try:
        response = requests.get(MASTER_LIST_URL, timeout = 300)
        response.raise_for_status()
    except requests.exceptions.RequestException as exception: 
        # if timeout exceeded or error occurs
        print(f"Exception raised: {exception}")
        return []
    
    # finding files
    files = []
    for line in response.text.splitlines():
        parts = line.split()
        if len(parts) != 3: continue        # skip if line has too many/too little parts
        file_size, file_id, url = parts
        if ".export." not in url: continue   # grabs only files with event data
        timestamp = url.rsplit("/", 1)[-1].split(".")[0]    # extracts timestamp of the event uplad
        if start < timestamp <= latest:     # identifies files within give timefra,e
            files.append((timestamp, url, file_id))

    # return files in order
    files.sort()
    if files: 
        print(f"There are {len(files)} files to be extracted")

    return files



def download_file(url, file_id):
    """Downloads individual zip files from GDELT website."""

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


#! TABLE BUILDING FUNCTIONS -------------------------------------------------------


def read_in_events(data,timestamp):
    """Makes a pandas table from the files downloaded"""
    try: 
        with zipfile.ZipFile(io.BytesIO(data)) as zip_file:
            with zip_file.open(zip_file.namelist()[0]) as csv_file:
                # creating pandas table
                events = pd.read_csv(
                    csv_file,
                    sep="\t",                 
                    header=None,              
                    names=EVENT_COLUMNS,      # column names must be supplied
                    dtype=str,                
                    quoting=3,                # quotes are treated as any other character
                    encoding="utf-8",         # recognizes all languages' characters
                    encoding_errors="replace",  # unreadable characters do not break file
                )      
    # if there is an issue reading in a file          
    except (zipfile.BadZipFile, pd.errors.ParserError) as exception: 
        print(f"Could not read file {timestamp}: {exception}")
        return None
    # checking amount read in
    if events is not None:
        print(f"Read {len(events):,} rows and {len(events.columns)} columns")
    return events


#! CREATING DF AND CSV -------------------------------------------------------


def extract(last = None):

    """Creates/appends to dataframe and CSV file."""
    # finding appropriate files to extract
    latest = get_lastupdate_timestamp()
    files = identify_files_to_pull(last, latest)

    # if there are no new files to extract
    if not files:
        print("No new files to extract.")
        return pd.DataFrame(columns=EVENT_COLUMNS)
    tables = []         # one table per zip, combined at end

    # download each file and check that id matches
    for file, (timestamp, url, file_id) in enumerate(files, start = 1):
        data = download_file(url, file_id)
        if data is None:
            print(f"Ending at {timestamp}.")    # next run will resume from here
            break
        # unzip into table
        events = read_in_events(data, timestamp)
        if events is None:
            print(f"Ending at {timestamp}.")    # next run will resume from here
            break
        # add data to table
        tables.append(events)
        last_loaded = timestamp      

    # nothing was read; indicates a failure
    if not tables:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    
    # combine all tables into one df
    events_df = pd.concat(tables, ignore_index=True)

    # create or append to csv file
    os.makedirs(RAW_DIR, exist_ok=True)
    write_header = not os.path.exists(RAW_FILE)
    events_df.to_csv(RAW_FILE, mode="a", header=write_header, index=False)

    # summary output
    print(f"Received from GDELT: {len(events_df):,} rows from {len(tables)} files "
          f"({files[0][0]} to {last_loaded})")
    print(f"Raw data appended to {RAW_FILE}")
    return events_df


#! TEST ----------------------------------------------------------------
events_df = extract(None)       # None = first run, look back LOOKBACK_DAYS
print(events_df.shape)
print(events_df[["GLOBALEVENTID", "SQLDATE", "Actor1Name", "EventCode", "DATEADDED"]].head())

events_df.head()