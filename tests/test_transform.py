import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

import transform as t


def test_missing_tokens_preserve_codes():
    frame = pd.DataFrame({"code": [" NA ", "null", "", "None", "N/A", None]})
    result = t.normalize_missing_values(frame)
    assert result.code.iloc[0] == "NA"
    assert result.code.iloc[1:].isna().all()
    assert frame.code.iloc[0] == " NA "


def test_normalize_text():
    frame = pd.DataFrame({"Actor1Name": ["human   rights"], "Actor1CountryCode": ["gbr"],
                          "SOURCEURL": ["https://Example.test/Case"]}, dtype="string")
    result = t.normalize_text(frame)
    assert result.Actor1Name.iloc[0] == "HUMAN RIGHTS"
    assert result.Actor1CountryCode.iloc[0] == "GBR"
    assert result.SOURCEURL.iloc[0] == frame.SOURCEURL.iloc[0]


def test_convert_dates():
    result = t.convert_dates(pd.DataFrame({"SQLDATE": ["20240229", "20230229", "bad"],
                                         "DATEADDED": ["20261110001500", "bad", "20261110"]}))
    assert result.SQLDATE.iloc[0] == pd.Timestamp("2024-02-29")
    assert result.SQLDATE.iloc[1:].isna().all()
    assert str(result.DATEADDED.dt.tz) == "UTC"
    assert result.DATEADDED.iloc[1:].isna().all()


def test_numeric_ranges_and_exact_ids():
    result = t.convert_numeric_values(pd.DataFrame({
        "GLOBALEVENTID": ["9007199254740993", "-1", "1.5"],
        "NumArticles": ["0", "-1", "bad"], "AvgTone": ["100", "101", "bad"],
        "ActionGeo_Lat": ["90", "91", "-91"],
    }))
    assert result.GLOBALEVENTID.iloc[0] == 9007199254740993
    assert result.GLOBALEVENTID.iloc[1:].isna().all()
    assert result.NumArticles.iloc[0] == 0
    assert result.NumArticles.iloc[1:].isna().all()
    assert result.AvgTone.iloc[1:].isna().all()
    assert result.ActionGeo_Lat.iloc[1:].isna().all()


def test_event_codes_preserve_leading_zero():
    result = t.standardize_event_codes(pd.DataFrame({"EventCode": ["010", "1413", "999", None]}))
    assert result.EventCode.iloc[0] == "010"
    assert result.EventRootCode.iloc[1] == "14"
    assert result.EventBaseCode.iloc[1] == "141"
    assert result.EventCode.iloc[2:].isna().all()


def test_separate_invalid_events():
    frame = pd.DataFrame({"GLOBALEVENTID": pd.Series([1, None, 3], dtype="Int64"),
                          "SQLDATE": pd.to_datetime(["2026-11-10", "2026-11-10", None])})
    valid, rejected = t.separate_invalid_events(frame)
    assert valid.GLOBALEVENTID.tolist() == [1]
    assert "GLOBALEVENTID" in rejected.RejectionReason.iloc[0]
    assert "SQLDATE" in rejected.RejectionReason.iloc[1]


def test_dedup_latest_and_tie():
    frame = pd.DataFrame({"GLOBALEVENTID": [1, 1, 1],
                          "DATEADDED": pd.to_datetime([None, "2026-11-10", "2026-11-10"], utc=True),
                          "marker": ["unknown", "earlier", "last"]})
    assert t.remove_duplicates(frame).marker.tolist() == ["last"]


@pytest.mark.parametrize("types,expected", [("GOV", False), ("MIL", False), ("", True)])
def test_rights_cooccurrence(event_row, types, expected):
    event_row["Actor2Type2Code"] = types
    result = t.transform(pd.DataFrame([event_row])).cleaned_events.iloc[0]
    assert bool(result.Actor2IsHumanRightsCoded)
    assert bool(result.ReligionHumanRightsCooccurrence) == expected


@pytest.mark.parametrize("url,expected", [
    ("https://news.test/crude-oil", True), ("https://news.test/foil-boiling", False),
    ("https://news.test/ethanol-in-petrol", False), ("https://news.test/%6Fil", True),
])
def test_oil_flags(event_row, url, expected):
    event_row.update(SOURCEURL=url, Actor1CountryCode="IRN", Actor2KnownGroupCode="OPC")
    result = t.transform(pd.DataFrame([event_row])).cleaned_events.iloc[0]
    assert bool(result.OilURLCandidate) == expected
    assert bool(result.IranOil) == expected
    assert bool(result.HasOPCActor)


def test_url_parts():
    assert t._source_parts("https://www.Example.test/a%20b?q=oil") == ("example.test", "/a b?q=oil")
    assert all(pd.isna(x) for x in t._source_parts("not a url"))


@pytest.mark.parametrize("code,expected", [("UK", "GBR"), ("IS", "ISR"), ("IC", "ISL"), ("ZZ", None)])
def test_country_mapping(event_row, code, expected):
    event_row["ActionGeo_CountryCode"] = code
    result = t.transform(pd.DataFrame([event_row]))
    value = result.cleaned_events.ActionGeo_CountryISO3.iloc[0]
    assert pd.isna(value) if expected is None else value == expected
    if expected is None:
        assert result.quality_report["unmapped_action_country_codes"] == ["ZZ"]


def test_dates_keep_historical_month(event_row):
    event_row["SQLDATE"] = "20160101"
    bounded = t.transform(pd.DataFrame([event_row]), start_date="2026-11-01", end_date="2026-11-30")
    unbounded = t.transform(pd.DataFrame([event_row]))
    assert bounded.cleaned_events.EventMonth.iloc[0] == "2016-01"
    assert not bounded.cleaned_events.IsInAnalysisWindow.iloc[0]
    assert unbounded.cleaned_events.IsInAnalysisWindow.isna().all()


@pytest.mark.parametrize("start,end", [("NaT", None), ("2026-11-10", "2026-01-01"),
                                        ("2026-11-10T01:00:00", None)])
def test_invalid_study_window(event_row, start, end):
    with pytest.raises(ValueError):
        t.transform(pd.DataFrame([event_row]), start_date=start, end_date=end)


def test_tables_have_unique_keys_and_valid_references(event_row):
    tables = t.transform(pd.DataFrame([event_row])).tables
    for table, key in [("events", "GLOBALEVENTID"), ("actors", "ActorID"),
                       ("locations", "LocationID"), ("event_types", "EventCode")]:
        assert tables[table][key].is_unique
        assert tables[table][key].notna().all()
    for fk, table, key in [("Actor1ID", "actors", "ActorID"), ("Actor2ID", "actors", "ActorID"),
                            ("ActionGeoLocationID", "locations", "LocationID")]:
        assert tables["events"][fk].dropna().isin(tables[table][key]).all()
    assert not tables["actor_religions"].duplicated(["ActorID", "ReligionCode"]).any()


def test_stable_ids():
    frame = pd.DataFrame({"name": ["A", "A", "B", None]})
    ids = t._stable_ids(frame)
    assert ids.iloc[0] == ids.iloc[1] and ids.iloc[0] != ids.iloc[2]
    assert pd.isna(ids.iloc[3])


def test_transform_report_and_repeatability(event_row, capsys):
    raw = pd.DataFrame([event_row, event_row, dict(event_row, GLOBALEVENTID="bad")])
    first, second = t.transform(raw), t.transform(raw)
    assert first.quality_report["duplicates_removed"] == 1
    assert len(first.rejected_events) == 1
    assert "NumArticles:" in capsys.readouterr().out
    for key in first.tables:
        assert_frame_equal(first.tables[key], second.tables[key])


def test_empty_and_missing_schema():
    assert t.transform(pd.DataFrame(columns=list(t.REQUIRED_COLUMNS))).cleaned_events.empty
    with pytest.raises(ValueError, match="Missing required"):
        t.transform(pd.DataFrame())


def test_daily_iterator_selects_only_ready(tmp_path, event_row):
    for stamp in ["20261110001500", "20261110003000", "20261111001500"]:
        pd.DataFrame([event_row]).to_csv(tmp_path / f"{stamp}.csv", index=False)
    ready = ["20261110001500", "20261111001500"]
    batches = list(t.iter_transformed_days(tmp_path, timestamps=ready))
    assert [stamp for stamp, _ in batches] == ready
    assert list(t.iter_transformed_days(tmp_path, timestamps=[])) == []
    assert len(t.batch_paths(tmp_path)) == 3


def test_mentions_reject_orphans_and_bad_dates():
    base = dict(GLOBALEVENTID="1", EventTimeDate="20261110001500",
                MentionTimeDate="20261110003000", MentionIdentifier="https://news.test/a", Confidence="101")
    raw = pd.DataFrame([base, base, dict(base, GLOBALEVENTID="999"), dict(base, MentionTimeDate="bad")])
    result = t.transform_mentions(raw, [1])
    assert len(result.cleaned_mentions) == 1 and len(result.rejected_mentions) == 2
    assert "MentionID" not in result.cleaned_mentions
    assert result.cleaned_mentions.Confidence.isna().all()
    assert result.quality_report["duplicates_removed"] == 1
    assert "bad" in result.rejected_mentions.MentionTimeDate.tolist()
    assert t.transform_mentions(raw, []).cleaned_mentions.empty
