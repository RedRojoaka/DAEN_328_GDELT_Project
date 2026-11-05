"""This checks the Docker runtime and database without running our unfinished ETL.

What this does is it creates a PostgreSQL TEMP table which disappears when the connection closes to simulate how connecting our true table will be.
No API calls, project tables, or checkpoint updates occur.
"""
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import matplotlib
import numpy
import pandas
import plotly
import psycopg2
import pytest
import requests
import streamlit
from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text


def main():
    load_dotenv()
    # to check the writable directories without touching the ETL data yet, just testing
    for directory in (Path(__file__).resolve().parents[1], Path(os.environ["ETL_WORK_DIR"])):
        directory.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="docker-smoke-", dir=directory) as temporary:
            probe = Path(temporary) / "probe.txt"
            probe.write_text("writable", encoding="utf-8")
            if probe.read_text(encoding="utf-8") != "writable":
                raise RuntimeError(f"Filesystem write/read failed under {directory}")
    print("PASS: application and ETL work directories allow directory creation and file write/read.")

    url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ["POSTGRES_PORT"]),
        database=os.environ["POSTGRES_DB"],
    )

    engine = create_engine(url, connect_args={"connect_timeout": 10})
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TEMP TABLE docker_smoke (id INTEGER PRIMARY KEY)"))
            connection.execute(text("INSERT INTO docker_smoke VALUES (1)"))
            if connection.scalar(text("SELECT COUNT(*) FROM docker_smoke")) != 1:
                raise RuntimeError("Database write/read check failed")
        print("PASS: Python dependencies imported; PostgreSQL connection and write/read succeeded.")
        print("This checks Docker infrastructure only, not ETL or dashboard correctness.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
