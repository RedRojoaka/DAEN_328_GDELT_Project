# Testing

From the project root:

```sh
python -m pytest -q
```

Tests use hand-built data and mocked HTTP responses. No GDELT downloads or
PostgreSQL connection are required. They cover the cleaning functions, daily
batching, checkpoint selection, cached-but-unloaded files, download failures,
and the sample-validation script. They do not prove database restart safety.

Run in the pinned project environment before presenting:

```sh
docker-compose run --rm etl pytest -q
```

## Real GDELT sample

The smoke script uses four consecutive 15-minute intervals ending at
2026-11-13 15:00 UTC, for both events and mentions. It saves the archive evidence,
parsed batches, transformed tables, rejected rows, and validation reports.
It does not create or load a database.

```sh
python scripts/gdelt_smoke.py --download
```

If Python cannot reach GDELT but a browser can, download the eight URLs listed
by STAMPS and the export/mentions filenames in scripts/gdelt_smoke.py, then run:

```powershell
python scripts/gdelt_smoke.py --archive-dir "$env:USERPROFILE\Downloads" --output-dir "$env:LOCALAPPDATA\Temp\gdelt-smoke-20261113"
```

Repeat validation with no network requests by running the same command again.
The default output is data/smoke/20261113_1400_1500 (ignored by Git). Use
--output-dir if the WSL path is not writable from the current runtime.

Four archives have published MD5 hashes captured from GDELT's lastupdate.txt;
all eight are ZIP-CRC checked and have local SHA256 hashes recorded in
manifest.json. A local hash alone is not independent source authentication.

Many mentions refer to events outside this one-hour sample. Their rejection
is expected for a sample-only event store; production must check against all
loaded events. Oil and human-rights classifications still need manual review.

Before the live demo, test a full day for memory/runtime, then run the completed
Docker stack twice, preserving its database volume. Check table row counts,
primary-key uniqueness, and checkpoints. The local smoke test does not replace
those database and dashboard checks.
