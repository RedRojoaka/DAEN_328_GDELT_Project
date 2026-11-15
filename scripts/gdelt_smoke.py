"""Download or import the fixed one-hour GDELT sample and check transformation.

Run from the project root: python scripts/gdelt_smoke.py --download
Or reuse browser downloads: python scripts/gdelt_smoke.py --archive-dir PATH
No database is used. Generated data stays under the git-ignored data/ directory.
"""
import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from pandas.testing import assert_frame_equal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import extract as e
import transform as t

STAMPS = ["20261113141500", "20261113143000", "20261113144500", "20261113150000"]
SAMPLE = ROOT / "data" / "smoke" / "20261113_1400_1500"
# Published lastupdate.txt hashes observed during download. Other archives use
# ZIP CRC checks and recorded SHA256s, not a claimed publisher-checksum match.
PUBLISHED_MD5 = {
    "20261113144500.export.CSV.zip": "2ac1694a4f52887f6798776fda7b1925",
    "20261113144500.mentions.CSV.zip": "2b50c1f84fe265fdab95c34ebfaee729",
    "20261113150000.export.CSV.zip": "c29c595477d65cc5ceaec5aff98612e6",
    "20261113150000.mentions.CSV.zip": "17b00f166741e72b1d5531ffeb03b065",
}


def prepare_archives(folder, source=None, download=False):
    folder.mkdir(parents=True, exist_ok=True)
    evidence = []
    for stamp in STAMPS:
        for kind in ("export", "mentions"):
            name = f"{stamp}.{kind}.CSV.zip"
            path = folder / name
            url = f"https://data.gdeltproject.org/gdeltv2/{name}"
            if not path.exists():
                if source is not None:
                    shutil.copyfile(Path(source) / name, path)
                elif download:
                    # Save atomically so interrupted downloads cannot look complete.
                    response = requests.get(url, timeout=60)
                    response.raise_for_status()
                    temporary = path.with_suffix(".tmp")
                    temporary.write_bytes(response.content)
                    temporary.replace(path)
                else:
                    raise FileNotFoundError(f"Missing {path}; use --download or --archive-dir PATH")
            payload = path.read_bytes()
            expected = PUBLISHED_MD5.get(name)
            if expected and hashlib.md5(payload).hexdigest() != expected:
                raise ValueError(f"Published checksum mismatch: {name}")
            with zipfile.ZipFile(io.BytesIO(payload)) as zipped:
                if zipped.testzip() is not None:
                    raise ValueError(f"ZIP integrity check failed: {name}")
            evidence.append(dict(filename=name, url=url, bytes=len(payload),
                                 sha256=hashlib.sha256(payload).hexdigest(),
                                 published_md5_verified=expected is not None))
    return evidence


def validate_sample(folder):
    started = time.perf_counter()
    report_path = folder / "validation.json"
    # An interrupted rerun must not leave a stale success report.
    report_path.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    e.RAW_DIR = str(folder)
    e.BATCH_DIR = str(folder / "batches")
    e.REJECT_DIR = str(folder / "rejected")
    counts = {}
    for stream, kind in (("events", "export"), ("mentions", "mentions")):
        counts[stream] = {"received_from_api": 0, "malformed_rows": 0}
        for stamp in STAMPS:
            payload = (folder / "archives" / f"{stamp}.{kind}.CSV.zip").read_bytes()
            frame = e.read_in_file(payload, stamp, stream, counts[stream])
            if frame is None:
                raise ValueError(f"Unusable {stream} archive: {stamp}")
            e.save_batch(frame, stamp, stream)
    through, events = next(t.iter_transformed_days(e.BATCH_DIR, timestamps=STAMPS,
                                                  start_date="2026-11-13", end_date="2026-11-13"))
    _, repeated = next(t.iter_transformed_days(e.BATCH_DIR, timestamps=STAMPS,
                                               start_date="2026-11-13", end_date="2026-11-13"))
    for name in events.tables:
        assert_frame_equal(events.tables[name], repeated.tables[name])
    del repeated
    for name, key in [("events", "GLOBALEVENTID"), ("actors", "ActorID"),
                      ("locations", "LocationID"), ("event_types", "EventCode")]:
        if not events.tables[name][key].is_unique:
            raise ValueError(f"Duplicate key in {name}")
    for fk, table, key in [("Actor1ID", "actors", "ActorID"), ("Actor2ID", "actors", "ActorID"),
                          ("ActionGeoLocationID", "locations", "LocationID")]:
        if not events.tables["events"][fk].dropna().isin(events.tables[table][key]).all():
            raise ValueError(f"Unmatched reference: {fk}")
    output = folder / "transformed"
    output.mkdir(exist_ok=True)
    for name, frame in events.tables.items():
        frame.to_csv(output / f"{name}.csv", index=False)
    events.rejected_events.to_csv(output / "rejected_events.csv", index=False)
    known = events.cleaned_events.GLOBALEVENTID.tolist()
    mention_reports = []
    for stamp, raw in e.iter_batches("mentions", timestamps=STAMPS):
        mentions = t.transform_mentions(raw, known)
        again = t.transform_mentions(raw, known)
        assert_frame_equal(mentions.cleaned_mentions, again.cleaned_mentions)
        if not mentions.cleaned_mentions.GLOBALEVENTID.isin(known).all():
            raise ValueError("Mention refers to an event outside the sample")
        mentions.cleaned_mentions.to_csv(output / f"{stamp}.mentions.csv", index=False)
        mentions.rejected_mentions.to_csv(output / f"{stamp}.rejected_mentions.csv", index=False)
        mention_reports.append(dict(timestamp=stamp, **mentions.quality_report))
    result = dict(status="passed", scope="Local archive-to-transform checks; no PostgreSQL or Docker",
                  timestamps=STAMPS, through=through, pandas=pd.__version__,
                  seconds=round(time.perf_counter() - started, 2), parsed_source_rows=counts,
                  events=events.quality_report, mentions=mention_reports)
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--archive-dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=SAMPLE)
    args = parser.parse_args()
    sample = args.output_dir.resolve()
    try:
        evidence = prepare_archives(sample / "archives", args.archive_dir, args.download)
        (sample / "manifest.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        with (sample / "validation.log").open("w", encoding="utf-8") as log, contextlib.redirect_stdout(log):
            result = validate_sample(sample)
        print(f"PASS: {result['events']['cleaned_rows']} events; "
              f"{sum(m['cleaned_rows'] for m in result['mentions'])} retained mentions")
        print(f"Sample and reports: {sample}")
    except (OSError, ValueError, requests.RequestException, zipfile.BadZipFile, AssertionError) as error:
        parser.exit(1, f"Sample validation failed: {error}\n")


if __name__ == "__main__":
    main()
