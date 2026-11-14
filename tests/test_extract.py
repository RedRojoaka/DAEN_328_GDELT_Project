import hashlib
import io
import zipfile
from unittest.mock import Mock

import pandas as pd
import pytest
import requests

import extract as e


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setattr(e, "RAW_DIR", str(tmp_path))
    monkeypatch.setattr(e, "BATCH_DIR", str(tmp_path / "batches"))
    monkeypatch.setattr(e, "REJECT_DIR", str(tmp_path / "rejected"))
    monkeypatch.setattr(e.time, "sleep", lambda _: None)
    return tmp_path


def manifest(stamps):
    return [f"1 abc https://example.test/{stamp}.export.CSV.zip" for stamp in stamps]


def archive(text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zipped:
        zipped.writestr("events.csv", text)
    return buffer.getvalue()


def test_checkpoint_wins_over_cache():
    assert e.get_starting_timestamp("20261111000000", ["20261110001500"]) == "20261111000000"


@pytest.mark.parametrize("value", ["bad", "20260230000000", "20261110"])
def test_invalid_checkpoint(value):
    with pytest.raises(ValueError):
        e.get_starting_timestamp(value)


def test_selection_includes_cached_unloaded(cache):
    stamps = ["20261110001500", "20261110003000"]
    selected = e.identify_files_to_pull("events", manifest(stamps), "20261110000000", stamps[-1], [stamps[0]])
    assert [entry[0] for entry in selected] == stamps


def test_download_checksum(monkeypatch):
    payload = b"archive bytes"
    response = Mock(status_code=200, content=payload)
    monkeypatch.setattr(e.requests, "get", Mock(return_value=response))
    assert e.download_file("https://example.test", hashlib.md5(payload).hexdigest()) == ("ok", payload)


def test_checksum_failure_is_reported(cache, monkeypatch):
    get = Mock(return_value=Mock(status_code=200, content=b"wrong"))
    monkeypatch.setattr(e.requests, "get", get)
    assert e.download_file("https://example.test", "bad")[0] == "skip"
    assert get.call_count == e.MAX_RETRIES


def test_network_failure_stops_after_retries(cache, monkeypatch):
    get = Mock(side_effect=requests.exceptions.Timeout("timeout"))
    monkeypatch.setattr(e.requests, "get", get)
    assert e.download_file("https://example.test", "abc")[0] == "stop"
    assert get.call_count == e.MAX_RETRIES


def test_404_does_not_retry(monkeypatch):
    get = Mock(return_value=Mock(status_code=404))
    monkeypatch.setattr(e.requests, "get", get)
    assert e.download_file("https://example.test", "abc") == ("skip", "404 not found")
    assert get.call_count == 1


def test_archive_parser_preserves_duplicates_and_codes(cache, event_row):
    line = "\t".join(event_row.get(column, "") for column in e.EVENT_COLUMNS)
    counts = {"received_from_api": 0, "malformed_rows": 0}
    frame = e.read_in_file(archive(line + "\n" + line + "\nbad\trow"), "20261110001500", "events", counts)
    assert len(frame) == 2 and frame.EventCode.tolist() == ["1413", "1413"]
    assert counts == {"received_from_api": 3, "malformed_rows": 1}
    assert (cache / "rejected" / "events_20261110001500.csv").exists()
    assert e.read_in_file(b"not a zip", "20261110001500", "events") is None


def test_saved_unloaded_batches_and_checkpoint_replay(cache, monkeypatch, event_row):
    stamp = "20261110001500"
    e.save_batch(pd.DataFrame([event_row]), stamp, "events")
    download = Mock(side_effect=AssertionError("Cache should be reused"))
    monkeypatch.setattr(e, "download_file", download)
    first = e.extract(last="20261110000000", master_lines=manifest([stamp]), latest=stamp)
    assert first.ready == [stamp] and first.saved == []
    assert first.quality_report["cached_rows"] == 1
    second = e.extract(last=stamp, master_lines=manifest([stamp]), latest=stamp)
    assert second.status == "no_new_files" and second.ready == []
    download.assert_not_called()
    assert list(e.iter_batches(timestamps=first.ready))[0][0] == stamp


def test_failure_blocks_later_cached_archive(cache, monkeypatch, event_row):
    stamps = ["20261110001500", "20261110003000", "20261110004500"]
    for stamp in [stamps[0], stamps[2]]:
        e.save_batch(pd.DataFrame([event_row]), stamp, "events")
    monkeypatch.setattr(e, "download_file", lambda *args: ("stop", "offline"))
    result = e.extract(last="20261110000000", master_lines=manifest(stamps), latest=stamps[-1])
    assert result.status == "partial" and result.ready == [stamps[0]]
    assert result.stopped_at == stamps[1]


def test_logged_failure_blocks_progress(cache, monkeypatch):
    stamp = "20261110001500"
    e.mark_skipped("events", stamp, "404")
    monkeypatch.setattr(e, "download_file", Mock(side_effect=AssertionError("unexpected download")))
    result = e.extract(last="20261110000000", master_lines=manifest([stamp]), latest=stamp)
    assert result.status == "blocked" and result.ready == []
    assert result.skipped == [stamp]
