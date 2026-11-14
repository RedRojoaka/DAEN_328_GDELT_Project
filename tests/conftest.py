import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    # Unit tests should never download data.
    def blocked(*args, **kwargs):
        raise AssertionError("Unexpected HTTP request in a unit test")
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)


@pytest.fixture
def event_row():
    return dict(
        GLOBALEVENTID="1", SQLDATE="20261110", DATEADDED="20261110001500",
        EventCode="1413", QuadClass="3", GoldsteinScale="-6", AvgTone="0",
        NumMentions="2", NumSources="1", NumArticles="1",
        Actor1Name="CHURCH", Actor1Religion1Code="CHR",
        Actor2Name="HUMAN RIGHTS WATCH", Actor2Type1Code="HRI",
        ActionGeo_Type="1", ActionGeo_CountryCode="IS",
    )
