"""GDELT events dashboard.

Reads the tables created by load.py (events, event_types, locations, actors,
etl_runs) and shows filterable charts. Connection settings come from .env.

Run with Docker:  docker compose run --rm --no-deps --service-ports dashboard
Run locally:      streamlit run streamlit_app.py
"""

import os
from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

st.set_page_config(page_title="GDELT Dashboard", layout="wide")

# Label shown in the UI -> boolean column in the events table (whitelist for SQL).
FLAG_COLUMNS = {
    "Protest": "isprotest",
    "Material conflict": "ismaterialconflict",
    "Concession": "isconcessionevent",
    "Rights-related": "isrightsrelatedevent",
    "Iran-linked": "isiranlinked",
    "Iran + oil URL": "iranoil",
    "Has religion code": "hasreligioncode",
    "Religion + human-rights actor": "religionhumanrightscooccurrence",
}


# ---------------------------------------------------------------- database

@st.cache_resource
def get_engine():
    url = URL.create(
        "postgresql+psycopg2",
        username=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        database=os.getenv("POSTGRES_DB"),
    )
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=300, show_spinner=False)
def run_query(query, params=None):
    with get_engine().connect() as connection:
        return pd.read_sql(text(query), connection, params=params or {})


def build_where(start, end, countries, flag_columns):
    clauses = ["e.sqldate BETWEEN :start AND :end"]
    params = {"start": start, "end": end}
    if countries:
        clauses.append("e.actiongeo_countryiso3 = ANY(:countries)")
        params["countries"] = countries
    clauses += [f"e.{column} IS TRUE" for column in flag_columns]
    return " AND ".join(clauses), params


def fmt(value, digits=2):
    return "n/a" if pd.isna(value) else f"{value:,.{digits}f}"


# ------------------------------------------------------------------ header

st.title("GDELT Events Dashboard")

try:
    bounds = run_query("SELECT min(sqldate) AS lo, max(sqldate) AS hi, count(*) AS n FROM events")
except Exception as exception:  # connection refused, table missing, bad password...
    st.error(f"Could not read from Postgres: {exception}")
    st.info("Make sure the ETL finished (`Load complete.`) and the database is running.")
    st.stop()

if int(bounds.at[0, "n"]) == 0:
    st.warning("The events table is empty. Run the ETL first.")
    st.stop()

data_min = pd.Timestamp(bounds.at[0, "lo"]).date()
data_max = pd.Timestamp(bounds.at[0, "hi"]).date()

# ----------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Filters")
    # SQLDATE can include old dates, so default to the most recent 30 days of data.
    default_start = max(data_min, data_max - timedelta(days=30))
    date_range = st.date_input(
        "Event date (SQLDATE)",
        value=(default_start, data_max),
        min_value=data_min,
        max_value=data_max,
    )
    if not isinstance(date_range, (tuple, list)) or len(date_range) != 2:
        st.info("Pick an end date to continue.")
        st.stop()
    start, end = date_range

    country_options = run_query(
        "SELECT DISTINCT actiongeo_countryiso3 AS c FROM events "
        "WHERE actiongeo_countryiso3 IS NOT NULL ORDER BY 1"
    )["c"].tolist()
    countries = st.multiselect("Action country (ISO3)", country_options)
    selected_flags = st.multiselect("Only events flagged as", list(FLAG_COLUMNS))
    st.caption("Flags are keyword/code heuristics, not confirmed topics.")

    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

where, params = build_where(start, end, countries, [FLAG_COLUMNS[f] for f in selected_flags])

# --------------------------------------------------------------------- KPIs

kpi = run_query(
    f"""
    SELECT count(*) AS events,
           avg(e.avgtone) AS tone,
           avg(e.goldsteinscale) AS goldstein,
           count(DISTINCT e.sourcedomain) AS domains,
           avg((e.isprotest IS TRUE)::int) AS protest_share
    FROM events e
    WHERE {where}
    """,
    params,
).iloc[0]

if int(kpi["events"]) == 0:
    st.warning("No events match these filters.")
    st.stop()

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Events", f"{int(kpi['events']):,}")
c2.metric("Avg tone", fmt(kpi["tone"]))
c3.metric("Avg Goldstein", fmt(kpi["goldstein"]))
c4.metric("Source domains", f"{int(kpi['domains']):,}")
c5.metric("Protest share", "n/a" if pd.isna(kpi["protest_share"]) else f"{kpi['protest_share']:.1%}")

tab_overview, tab_topics, tab_geo, tab_sources, tab_quality = st.tabs(
    ["Overview", "Topics", "Geography", "Sources & events", "Data quality"]
)

# ---------------------------------------------------------------- overview

with tab_overview:
    daily = run_query(
        f"""
        SELECT e.sqldate AS day, count(*) AS events,
               avg(e.avgtone) AS tone, avg(e.goldsteinscale) AS goldstein
        FROM events e WHERE {where}
        GROUP BY 1 ORDER BY 1
        """,
        params,
    )
    st.plotly_chart(px.line(daily, x="day", y="events", title="Events per day", markers=True))

    left, right = st.columns(2)
    with left:
        st.plotly_chart(px.line(daily, x="day", y="tone", title="Average tone per day (negative = more negative coverage)"))
    with right:
        st.plotly_chart(px.line(daily, x="day", y="goldstein", title="Average Goldstein scale per day (-10 conflict, +10 cooperation)"))

    quad = run_query(
        f"""
        SELECT coalesce(e.quadclasslabel, 'Unknown') AS quad_class, count(*) AS events
        FROM events e WHERE {where}
        GROUP BY 1 ORDER BY 2 DESC
        """,
        params,
    )
    st.plotly_chart(px.bar(quad, x="quad_class", y="events", title="Events by quad class"))

# ------------------------------------------------------------------ topics

with tab_topics:
    flag_sql = ", ".join(
        f"count(*) FILTER (WHERE e.{column} IS TRUE) AS {column}" for column in FLAG_COLUMNS.values()
    )
    flags_daily = run_query(
        f"SELECT e.sqldate AS day, {flag_sql} FROM events e WHERE {where} GROUP BY 1 ORDER BY 1",
        params,
    )
    labels = {column: label for label, column in FLAG_COLUMNS.items()}
    long_flags = flags_daily.melt(id_vars="day", var_name="flag", value_name="events")
    long_flags["flag"] = long_flags["flag"].map(labels)
    st.plotly_chart(px.line(long_flags, x="day", y="events", color="flag", title="Flagged events per day"))

    roots = run_query(
        f"""
        SELECT coalesce(t.eventrootlabel, 'Unknown') AS event_type, count(*) AS events
        FROM events e LEFT JOIN event_types t ON t.eventcode = e.eventcode
        WHERE {where}
        GROUP BY 1 ORDER BY 2 DESC LIMIT 20
        """,
        params,
    )
    st.plotly_chart(
        px.bar(roots.sort_values("events"), x="events", y="event_type", orientation="h", title="Top event types (CAMEO root)")
    )

# --------------------------------------------------------------- geography

with tab_geo:
    top_countries = run_query(
        f"""
        SELECT e.actiongeo_countryiso3 AS country, count(*) AS events
        FROM events e
        WHERE {where} AND e.actiongeo_countryiso3 IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """,
        params,
    )
    st.plotly_chart(px.bar(top_countries, x="country", y="events", title="Top action countries"))

    places = run_query(
        f"""
        SELECT l.fullname AS place, l.lat, l."long" AS lon, count(*) AS events
        FROM events e JOIN locations l ON l.locationid = e.actiongeolocationid
        WHERE {where} AND l.lat IS NOT NULL AND l."long" IS NOT NULL
        GROUP BY 1, 2, 3 ORDER BY 4 DESC LIMIT 1000
        """,
        params,
    )
    if places.empty:
        st.info("No geocoded events for these filters.")
    else:
        fig = px.scatter_geo(
            places, lat="lat", lon="lon", size="events", hover_name="place",
            title="Where events happened (top 1,000 places)", projection="natural earth",
        )
        st.plotly_chart(fig)

# ----------------------------------------------------------------- sources

with tab_sources:
    domains = run_query(
        f"""
        SELECT e.sourcedomain AS domain, count(*) AS events
        FROM events e
        WHERE {where} AND e.sourcedomain IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """,
        params,
    )
    st.plotly_chart(px.bar(domains.sort_values("events"), x="events", y="domain", orientation="h", title="Top source domains"))

    st.subheader("Most-covered events")
    top_events = run_query(
        f"""
        SELECT e.globaleventid, e.sqldate, t.eventrootlabel AS event_type,
               l.fullname AS place, e.avgtone, e.goldsteinscale,
               e.numarticles, e.sourceurl
        FROM events e
        LEFT JOIN event_types t ON t.eventcode = e.eventcode
        LEFT JOIN locations l ON l.locationid = e.actiongeolocationid
        WHERE {where}
        ORDER BY e.numarticles DESC NULLS LAST LIMIT 200
        """,
        params,
    )
    st.dataframe(top_events, hide_index=True)

# ------------------------------------------------------------ data quality

with tab_quality:
    st.subheader("Rows in each table")
    counts = run_query(
        """
        SELECT 'events' AS table_name, count(*) AS rows FROM events
        UNION ALL SELECT 'actors', count(*) FROM actors
        UNION ALL SELECT 'locations', count(*) FROM locations
        UNION ALL SELECT 'event_types', count(*) FROM event_types
        UNION ALL SELECT 'actor_religions', count(*) FROM actor_religions
        """
    )
    st.dataframe(counts, hide_index=True)

    st.subheader("Transform quality per loaded day")
    runs = run_query("SELECT day, loaded_at, report FROM etl_runs ORDER BY day")
    if runs.empty:
        st.info("No quality reports loaded yet.")
    else:
        report = pd.json_normalize(runs["report"].tolist())
        wanted = [c for c in ("input_rows", "cleaned_rows", "duplicates_removed", "rejected_rows",
                              "outside_analysis_window", "window_not_evaluated") if c in report]
        table = pd.concat([runs[["day", "loaded_at"]], report[wanted]], axis=1)
        st.dataframe(table, hide_index=True)