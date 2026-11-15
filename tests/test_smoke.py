import hashlib
import io
import json
import zipfile

import pytest

from scripts import gdelt_smoke as smoke
import extract as e


def zip_bytes(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zipped:
        zipped.writestr("sample.csv", text)
    return buf.getvalue()


def test_missing_sample_gives_actionable_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="--download"):
        smoke.prepare_archives(tmp_path / "archives")


def test_import_checks_published_checksum(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    name = f"{smoke.STAMPS[0]}.export.CSV.zip"
    (source / name).write_bytes(zip_bytes("sample"))
    monkeypatch.setattr(smoke, "PUBLISHED_MD5", {name: "wrong"})
    with pytest.raises(ValueError, match="checksum mismatch"):
        smoke.prepare_archives(tmp_path / "target", source=source)


def test_sample_runner_with_local_archives(tmp_path, monkeypatch, event_row):
    source = tmp_path / "source"
    source.mkdir()
    stamp = "20261113150000"
    monkeypatch.setattr(smoke, "STAMPS", [stamp])
    event_row.update(SQLDATE="20261113", DATEADDED=stamp)
    event = "\t".join(event_row.get(column, "") for column in e.EVENT_COLUMNS)
    mention = dict(GLOBALEVENTID="1", EventTimeDate=stamp, MentionTimeDate=stamp,
                   MentionIdentifier="https://news.test/a")
    text = "\t".join(mention.get(column, "") for column in e.MENTION_COLUMNS)
    hashes = {}
    for kind, content in [("export", event), ("mentions", text)]:
        name = f"{stamp}.{kind}.CSV.zip"
        payload = zip_bytes(content + "\n")
        (source / name).write_bytes(payload)
        hashes[name] = hashlib.md5(payload).hexdigest()
    monkeypatch.setattr(smoke, "PUBLISHED_MD5", hashes)
    folder = tmp_path / "run"
    evidence = smoke.prepare_archives(folder / "archives", source=source)
    # Restore extractor globals after the standalone runner changes them.
    for key in ("RAW_DIR", "BATCH_DIR", "REJECT_DIR"):
        monkeypatch.setattr(e, key, getattr(e, key))
    result = smoke.validate_sample(folder)
    assert all(item["published_md5_verified"] for item in evidence)
    assert result["status"] == "passed" and result["events"]["cleaned_rows"] == 1
    assert result["mentions"][0]["cleaned_rows"] == 1
    assert json.loads((folder / "validation.json").read_text())["status"] == "passed"
