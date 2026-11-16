"""GDELT research dashboard.

Built around the team's research questions. Reads the tables created by
load.py (events, actors, actor_religions, locations, event_types, etl_runs).

Run with Docker:  docker compose run --rm --no-deps --service-ports dashboard
Run locally:      streamlit run streamlit_app.py

Everything here is event-level: GDELT codes who did what to whom, not the full
text of articles. Religion, human-rights and Iran/oil flags are heuristics.
"""

import os
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()

st.set_page_config(page_title="GDELT Research Dashboard", layout="wide")

# --------------------------------------------------------------- reference data

# CAMEO religion codes (Actor Religion1Code / Religion2Code).
RELIGION_LABELS = {
    "ATH": "Atheism", "BAH": "Bahai", "BUD": "Buddhism", "CHR": "Christianity (general)",
    "CON": "Confucianism", "CTH": "Catholic", "DOX": "Orthodox", "HIN": "Hinduism",
    "HSD": "Hasidic", "ITR": "Indigenous tribal", "JAN": "Jainism", "JEW": "Judaism",
    "LDS": "Latter-day Saints", "MOS": "Muslim (general)", "NRM": "New religious movement",
    "PAG": "Paganism", "PRO": "Protestant", "SHI": "Shia", "SHN": "Shinto",
    "SIK": "Sikhism", "SUF": "Sufi", "SUN": "Sunni", "TAO": "Taoism", "UNK": "Unknown",
    "ZRO": "Zoroastrianism",
}
# Grouped view: denominations roll up into the large world religions.
RELIGION_FAMILY = {
    **RELIGION_LABELS,
    "CHR": "Christianity", "CTH": "Christianity", "PRO": "Christianity", "DOX": "Christianity",
    "MOS": "Islam", "SUN": "Islam", "SHI": "Islam", "SUF": "Islam",
    "JEW": "Judaism", "HSD": "Judaism",
}

_COUNTRY_TEXT = """
ABW=Aruba;AFG=Afghanistan;AGO=Angola;AIA=Anguilla;ALB=Albania;AND=Andorra;ARE=United Arab Emirates;
ARG=Argentina;ARM=Armenia;ASM=American Samoa;ATA=Antarctica;ATF=French Southern Territories;
ATG=Antigua and Barbuda;AUS=Australia;AUT=Austria;AZE=Azerbaijan;BDI=Burundi;BEL=Belgium;BEN=Benin;
BFA=Burkina Faso;BGD=Bangladesh;BGR=Bulgaria;BHR=Bahrain;BHS=Bahamas;BIH=Bosnia and Herzegovina;
BLM=Saint Barthelemy;BLR=Belarus;BLZ=Belize;BMU=Bermuda;BOL=Bolivia;BRA=Brazil;BRB=Barbados;
BRN=Brunei;BTN=Bhutan;BVT=Bouvet Island;BWA=Botswana;CAF=Central African Republic;CAN=Canada;
CCK=Cocos Islands;CHE=Switzerland;CHL=Chile;CHN=China;CIV=Cote d'Ivoire;CMR=Cameroon;COD=DR Congo;
COG=Congo;COK=Cook Islands;COL=Colombia;COM=Comoros;CPV=Cape Verde;CRI=Costa Rica;CUB=Cuba;
CUW=Curacao;CXR=Christmas Island;CYM=Cayman Islands;CYP=Cyprus;CZE=Czechia;DEU=Germany;DJI=Djibouti;
DMA=Dominica;DNK=Denmark;DOM=Dominican Republic;DZA=Algeria;ECU=Ecuador;EGY=Egypt;ERI=Eritrea;
ESH=Western Sahara;ESP=Spain;EST=Estonia;ETH=Ethiopia;FIN=Finland;FJI=Fiji;FLK=Falkland Islands;
FRA=France;FRO=Faroe Islands;FSM=Micronesia;GAB=Gabon;GBR=United Kingdom;GEO=Georgia;GGY=Guernsey;
GHA=Ghana;GIB=Gibraltar;GIN=Guinea;GLP=Guadeloupe;GMB=Gambia;GNB=Guinea-Bissau;GNQ=Equatorial Guinea;
GRC=Greece;GRD=Grenada;GRL=Greenland;GTM=Guatemala;GUF=French Guiana;GUM=Guam;GUY=Guyana;
HKG=Hong Kong;HMD=Heard and McDonald Islands;HND=Honduras;HRV=Croatia;HTI=Haiti;HUN=Hungary;
IDN=Indonesia;IMN=Isle of Man;IND=India;IOT=British Indian Ocean Territory;IRL=Ireland;IRN=Iran;
IRQ=Iraq;ISL=Iceland;ISR=Israel;ITA=Italy;JAM=Jamaica;JEY=Jersey;JOR=Jordan;JPN=Japan;
KAZ=Kazakhstan;KEN=Kenya;KGZ=Kyrgyzstan;KHM=Cambodia;KIR=Kiribati;KNA=Saint Kitts and Nevis;
KOR=South Korea;KWT=Kuwait;LAO=Laos;LBN=Lebanon;LBR=Liberia;LBY=Libya;LCA=Saint Lucia;
LIE=Liechtenstein;LKA=Sri Lanka;LSO=Lesotho;LTU=Lithuania;LUX=Luxembourg;LVA=Latvia;MAC=Macao;
MAF=Saint Martin;MAR=Morocco;MCO=Monaco;MDA=Moldova;MDG=Madagascar;MDV=Maldives;MEX=Mexico;
MHL=Marshall Islands;MKD=North Macedonia;MLI=Mali;MLT=Malta;MMR=Myanmar;MNE=Montenegro;MNG=Mongolia;
MNP=Northern Mariana Islands;MOZ=Mozambique;MRT=Mauritania;MSR=Montserrat;MTQ=Martinique;
MUS=Mauritius;MWI=Malawi;MYS=Malaysia;MYT=Mayotte;NAM=Namibia;NCL=New Caledonia;NER=Niger;
NFK=Norfolk Island;NGA=Nigeria;NIC=Nicaragua;NIU=Niue;NLD=Netherlands;NOR=Norway;NPL=Nepal;
NRU=Nauru;NZL=New Zealand;OMN=Oman;PAK=Pakistan;PAN=Panama;PCN=Pitcairn Islands;PER=Peru;
PHL=Philippines;PLW=Palau;PNG=Papua New Guinea;POL=Poland;PRI=Puerto Rico;PRK=North Korea;
PRT=Portugal;PRY=Paraguay;PSE=Palestine;PYF=French Polynesia;QAT=Qatar;REU=Reunion;ROU=Romania;
RUS=Russia;RWA=Rwanda;SAU=Saudi Arabia;SDN=Sudan;SEN=Senegal;SGP=Singapore;
SGS=South Georgia and South Sandwich Islands;SHN=Saint Helena;SJM=Svalbard;SLB=Solomon Islands;
SLE=Sierra Leone;SLV=El Salvador;SMR=San Marino;SOM=Somalia;SPM=Saint Pierre and Miquelon;
SRB=Serbia;SSD=South Sudan;STP=Sao Tome and Principe;SUR=Suriname;SVK=Slovakia;SVN=Slovenia;
SWE=Sweden;SWZ=Eswatini;SXM=Sint Maarten;SYC=Seychelles;SYR=Syria;TCA=Turks and Caicos Islands;
TCD=Chad;TGO=Togo;THA=Thailand;TJK=Tajikistan;TKL=Tokelau;TKM=Turkmenistan;TLS=Timor-Leste;
TON=Tonga;TTO=Trinidad and Tobago;TUN=Tunisia;TUR=Turkey;TUV=Tuvalu;TWN=Taiwan;TZA=Tanzania;
UGA=Uganda;UKR=Ukraine;URY=Uruguay;USA=United States;UZB=Uzbekistan;VAT=Vatican City;
VCT=Saint Vincent and the Grenadines;VEN=Venezuela;VGB=British Virgin Islands;VIR=U.S. Virgin Islands;
VNM=Vietnam;VUT=Vanuatu;WLF=Wallis and Futuna;WSM=Samoa;YEM=Yemen;ZAF=South Africa;ZMB=Zambia;
ZWE=Zimbabwe
"""
COUNTRY_NAMES = dict(
    pair.strip().split("=", 1) for pair in _COUNTRY_TEXT.replace("\n", " ").split(";") if pair.strip()
)

# How protest "responses" are read from CAMEO event codes (Q4b).
RESPONSES = {
    "Concession (yield)": "left(e.eventcode, 2) = '08'",
    "Arrest / detain": "left(e.eventcode, 3) = '173'",
    "Violent repression": "left(e.eventcode, 3) = '175'",
    "Other coercion (sanctions, seizure, expulsion)":
        "left(e.eventcode, 2) = '17' AND left(e.eventcode, 3) NOT IN ('173', '175')",
    "Force (assault, fight)": "left(e.eventcode, 2) IN ('18', '19')",
}

# Q2b: separate "religious reporting" from "rights-violation reporting".
CATEGORY_SQL = """CASE
    WHEN e.hasreligioncode IS TRUE AND e.isrightsrelatedevent IS TRUE THEN 'Religion + rights event'
    WHEN e.hasreligioncode IS TRUE THEN 'Religion-coded only'
    WHEN e.isrightsrelatedevent IS TRUE THEN 'Rights-related only'
    ELSE 'Neither' END"""

QUAD_LABELS = {1: "Verbal cooperation", 2: "Material cooperation", 3: "Verbal conflict", 4: "Material conflict"}
QUAD_COLORS = {
    "Verbal cooperation": "#7bb6d9", "Material cooperation": "#2b6ca3",
    "Verbal conflict": "#f2b27a", "Material conflict": "#c0392b",
}

VIEWS = [
    "1 · Religions & rights orgs",
    "2a · Religion coverage",
    "2b · Religion vs. rights coverage",
    "3 · Iran & oil",
    "4 · Protests",
]


# ----------------------------------------------------------------------- helpers

def cname(code):
    return COUNTRY_NAMES.get(code, code)


def religion_case(group):
    """SQL CASE turning a religion code (alias ar) into a readable label."""
    mapping = RELIGION_FAMILY if group else RELIGION_LABELS
    whens = " ".join(f"WHEN '{code}' THEN '{label}'" for code, label in mapping.items())
    return f"CASE ar.religioncode {whens} ELSE ar.religioncode END"


def religion_rows(where_sql, fam, cols, extra=""):
    """One row per (event, religion), reading both actor slots.

    UNION (not UNION ALL) so an event whose two actors share a religion counts once.
    """
    side = (
        "SELECT e.globaleventid, {fam} AS religion, {cols} "
        "FROM events e JOIN actor_religions ar ON ar.actorid = e.actor{n}id "
        "WHERE {where} AND e.hasreligioncode IS TRUE{extra}"
    )
    return " UNION ".join(
        side.format(fam=fam, cols=cols, n=n, where=where_sql, extra=extra) for n in (1, 2)
    )


def build_where(start, end, countries, religions, orgs):
    clauses = ["e.sqldate BETWEEN :start AND :end"]
    params = {"start": start, "end": end}
    if countries:
        clauses.append("e.actiongeo_countryiso3 = ANY(:countries)")
        params["countries"] = countries
    if religions:
        clauses.append(
            "EXISTS (SELECT 1 FROM actor_religions fr WHERE fr.actorid IN (e.actor1id, e.actor2id) "
            "AND fr.religioncode = ANY(:religions))"
        )
        params["religions"] = religions
    if orgs:
        clauses.append(
            "EXISTS (SELECT 1 FROM actors fo WHERE fo.actorid IN (e.actor1id, e.actor2id) "
            "AND coalesce(fo.name, fo.code) = ANY(:orgs))"
        )
        params["orgs"] = orgs
    return " AND ".join(clauses), params


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


@st.cache_data(ttl=300, show_spinner="Running query...")
def run_query(query, params=None):
    with get_engine().connect() as connection:
        df = pd.read_sql(text(query), connection, params=params or {})
    for column in df.columns:  # Postgres numeric/date columns arrive as Decimal/date objects
        if df[column].dtype == object and df[column].notna().any():
            sample = df[column].dropna().iloc[0]
            if isinstance(sample, Decimal):
                df[column] = df[column].astype(float)
            elif isinstance(sample, date):
                df[column] = pd.to_datetime(df[column])
    return df


def show(fig, height=None):
    fig.update_layout(margin=dict(l=0, r=0, t=50, b=0), legend_title_text=None)
    if height:
        fig.update_layout(height=height)
    st.plotly_chart(fig)


def hbar(df, x, y, title, **kwargs):
    fig = px.bar(df.sort_values(x), x=x, y=y, orientation="h", title=title, **kwargs)
    fig.update_layout(yaxis_title=None)
    show(fig, height=max(320, 26 * len(df) + 110))


def table(df):
    """Plain HTML table. st.dataframe needs pyarrow, which can't import here (pyarrow 26 wants NumPy 2, pins say <2)."""
    st.markdown(df.to_html(index=False, border=0, float_format=lambda v: f"{v:,.2f}"), unsafe_allow_html=True)


def empty(df, message="No data for these filters. Widen the date range or clear a filter."):
    if df is None or df.empty:
        st.info(message)
        return True
    return False


# ------------------------------------------------------------------------ header

st.title("GDELT research dashboard")

try:
    bounds = run_query("SELECT min(sqldate) AS lo, max(sqldate) AS hi FROM events")
except Exception as exception:  # connection refused, missing table, bad password...
    st.error(f"Could not read from Postgres: {exception}")
    st.info("Make sure the ETL finished (`Load complete`) and the database is running.")
    st.stop()

if pd.isna(bounds.at[0, "lo"]):
    st.warning("The events table is empty. Run the ETL first.")
    st.stop()

data_min = pd.Timestamp(bounds.at[0, "lo"]).date()
data_max = pd.Timestamp(bounds.at[0, "hi"]).date()
try:  # SQLDATE can contain a few very old event dates; start at the first loaded day
    first_day = run_query("SELECT min(day) AS d FROM etl_runs").at[0, "d"]
    data_min = max(data_min, pd.to_datetime(first_day, format="%Y%m%d").date())
except Exception:
    pass
data_min = min(data_min, data_max)

with st.sidebar:
    st.header("Filters")
    if data_min < data_max:
        start, end = st.slider(
            "Event dates (SQLDATE)",
            min_value=data_min,
            max_value=data_max,
            value=(max(data_min, data_max - timedelta(days=6)), data_max),
            format="YYYY-MM-DD",
        )
    else:
        start = end = data_min
        st.caption(f"Only one event date loaded: {data_min}")

    countries = st.multiselect(
        "Country (where the event happened)", sorted(COUNTRY_NAMES, key=cname), format_func=cname,
    )
    religions = st.multiselect(
        "Religion (either actor)", list(RELIGION_LABELS),
        format_func=lambda code: f"{RELIGION_LABELS[code]} ({code})",
    )
    org_options = run_query(
        """
        SELECT org, sum(n)::bigint AS n FROM (
            SELECT coalesce(a.name, a.code) AS org, count(*) AS n
            FROM events e JOIN actors a ON a.actorid = e.actor1id
            WHERE e.sqldate BETWEEN :start AND :end AND e.actor1ishumanrightsactor IS TRUE GROUP BY 1
            UNION ALL
            SELECT coalesce(a.name, a.code), count(*)
            FROM events e JOIN actors a ON a.actorid = e.actor2id
            WHERE e.sqldate BETWEEN :start AND :end AND e.actor2ishumanrightsactor IS TRUE GROUP BY 1
        ) s WHERE org IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 150
        """,
        {"start": start, "end": end},
    )["org"].tolist()
    orgs = st.multiselect("Human-rights organization (either actor)", org_options)

    st.divider()
    st.header("Display")
    group = st.toggle("Group denominations into world religions", value=True,
                      help="Sunni, Shia and Sufi roll up into Islam; Catholic, Protestant and Orthodox into Christianity.")
    measure = st.radio("Count by", ["Events", "Media mentions"], horizontal=True,
                       help="Media mentions sums GDELT's NumMentions for each event.")
    gran = st.selectbox("Trend granularity", ["week", "day", "month"], format_func=str.title)
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

mcol = "events" if measure == "Events" else "mentions"
mlabel = "Events" if measure == "Events" else "Media mentions"
order_idx = 2 if mcol == "events" else 3  # column position of the measure in two-column rollups
where, params = build_where(start, end, countries, religions, orgs)

kpi = run_query(
    f"""
    SELECT count(*) AS events,
           count(*) FILTER (WHERE e.hasreligioncode IS TRUE) AS religious,
           count(*) FILTER (WHERE e.isrightsrelatedevent IS TRUE) AS rights,
           count(*) FILTER (WHERE e.isiranlinked IS TRUE) AS iran,
           count(*) FILTER (WHERE e.isprotest IS TRUE) AS protests
    FROM events e WHERE {where}
    """,
    params,
).iloc[0]
total_events = int(kpi["events"])
if total_events == 0:
    st.warning("No events match these filters. Widen the date range or clear a filter.")
    st.stop()

columns = st.columns(5)
columns[0].metric("Events in range", f"{total_events:,}")
for column, label, key in zip(
    columns[1:],
    ["Religion-coded", "Rights-related codes", "Iran-linked", "Protests"],
    ["religious", "rights", "iran", "protests"],
):
    column.metric(label, f"{int(kpi[key]):,}", f"{kpi[key] / total_events:.1%} of events", delta_color="off")

view = st.radio("View", VIEWS, horizontal=True, label_visibility="collapsed", key="view")


# ---------------------------------------------------- 1. religions x human-rights orgs

def view_religions_and_orgs():
    st.subheader("Do certain religions show up alongside human-rights organizations?")
    fam = religion_case(group)
    rel_extra = " AND ar.religioncode = ANY(:religions)" if religions else ""
    org_extra_a = " AND coalesce(a.name, a.code) = ANY(:orgs)" if orgs else ""

    left, right = st.columns(2)
    with left:
        rel = run_query(
            f"""
            SELECT religion, count(*) AS events, sum(w)::bigint AS mentions
            FROM ({religion_rows(where, fam, 'e.nummentions AS w', rel_extra)}) r
            GROUP BY 1 ORDER BY {order_idx} DESC LIMIT 15
            """,
            params,
        )
        if not empty(rel):
            hbar(rel, mcol, "religion", f"Most-mentioned religions ({mlabel.lower()})", labels={mcol: mlabel})
    with right:
        orgs_df = run_query(
            f"""
            SELECT org, count(*) AS events, sum(w)::bigint AS mentions FROM (
                SELECT e.globaleventid, coalesce(a.name, a.code, '(unnamed)') AS org, e.nummentions AS w
                FROM events e JOIN actors a ON a.actorid = e.actor1id
                WHERE {where} AND e.actor1ishumanrightsactor IS TRUE{org_extra_a}
                UNION
                SELECT e.globaleventid, coalesce(a.name, a.code, '(unnamed)'), e.nummentions
                FROM events e JOIN actors a ON a.actorid = e.actor2id
                WHERE {where} AND e.actor2ishumanrightsactor IS TRUE{org_extra_a}
            ) o GROUP BY 1 ORDER BY {order_idx} DESC LIMIT 15
            """,
            params,
        )
        if not empty(orgs_df):
            hbar(orgs_df, mcol, "org", f"Most-mentioned human-rights organizations ({mlabel.lower()})",
                 labels={mcol: mlabel})

    st.markdown("#### Which religion / organization pairs appear together?")
    st.caption(
        "A pair is one event with a religion-coded actor on one side and a human-rights organization on the other. "
        "Co-occurrence only: it does not say who supports or criticizes whom."
    )
    org_extra_o = " AND coalesce(o.name, o.code) = ANY(:orgs)" if orgs else ""
    pair_side = """
        SELECT e.globaleventid, {fam} AS religion, coalesce(o.name, o.code, '(unnamed)') AS org, e.nummentions AS w
        FROM events e
        JOIN actor_religions ar ON ar.actorid = e.actor{r}id
        JOIN actors o ON o.actorid = e.actor{o}id
        WHERE {where} AND e.religionhumanrightscooccurrence IS TRUE
          AND e.actor{o}ishumanrightsactor IS TRUE{rel_extra}{org_extra}"""
    pair_union = " UNION ".join(
        pair_side.format(fam=fam, where=where, r=r, o=o, rel_extra=rel_extra, org_extra=org_extra_o)
        for r, o in ((1, 2), (2, 1))
    )
    pairs = run_query(
        f"""
        SELECT religion, org, count(*) AS events, sum(w)::bigint AS mentions
        FROM ({pair_union}) p GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 3000
        """,
        params,
    )
    if empty(pairs, "No religion / human-rights-organization pairs in this selection. Try a wider date range."):
        return

    top = pairs.nlargest(15, mcol).assign(pair=lambda d: d["religion"] + "  ×  " + d["org"])
    left, right = st.columns([2, 3])
    with left:
        hbar(top, mcol, "pair", f"Top pairs ({mlabel.lower()})", labels={mcol: mlabel})
    with right:
        mode = st.radio(
            "Heatmap shows", ["Pair counts", "Share of each org's pairings", "Lift vs. independence"],
            horizontal=True,
        )
        rel_total = pairs.groupby("religion")[mcol].sum()
        org_total = pairs.groupby("org")[mcol].sum()
        keep_r, keep_o = rel_total.nlargest(10).index, org_total.nlargest(12).index
        matrix = (
            pairs[pairs["religion"].isin(keep_r) & pairs["org"].isin(keep_o)]
            .pivot_table(index="org", columns="religion", values=mcol, aggfunc="sum", fill_value=0)
            .reindex(index=list(keep_o), columns=list(keep_r))
            .fillna(0)
        )
        raw = matrix.copy()
        kwargs = dict(color_continuous_scale="Blues", text_auto=",.0f")
        if mode == "Share of each org's pairings":
            matrix = matrix.div(org_total.reindex(matrix.index), axis=0)
            kwargs = dict(color_continuous_scale="Blues", text_auto=".0%")
        elif mode == "Lift vs. independence":
            expected = np.outer(org_total.reindex(matrix.index), rel_total.reindex(matrix.columns)) / pairs[mcol].sum()
            matrix = (matrix / expected).where(raw >= 5)  # hide cells with fewer than 5 pairs
            kwargs = dict(color_continuous_scale="RdBu_r", color_continuous_midpoint=1, text_auto=".1f")
        fig = px.imshow(matrix, aspect="auto", labels=dict(color=mode), **kwargs)
        fig.update_xaxes(title=None, side="top")
        fig.update_yaxes(title=None)
        show(fig, height=max(360, 34 * len(matrix) + 120))
    st.caption(
        "Lift above 1 means the pair appears together more than you'd expect from how often each side appears "
        "overall (cells with fewer than 5 pairs are hidden). Lift removes the effect of a religion or organization "
        "that is simply covered a lot."
    )
    with st.expander("Pair table"):
        table(pairs.head(200))


# ----------------------------------------------------------- 2a. religion coverage

def view_religion_coverage():
    st.subheader("How is coverage of the largest religions changing?")
    fam = religion_case(group)
    rel_extra = " AND ar.religioncode = ANY(:religions)" if religions else ""
    top_n = st.slider("Religions to compare (largest first)", 2, 10, 5)

    cols = "e.nummentions AS w, e.avgtone AS tone, date_trunc(:gran, e.sqldate::timestamp)::date AS period"
    trend = run_query(
        f"""
        SELECT religion, period, count(*) AS events, sum(w)::bigint AS mentions, avg(tone) AS tone
        FROM ({religion_rows(where, fam, cols, rel_extra)}) r
        GROUP BY 1, 2 ORDER BY 2
        """,
        {**params, "gran": gran},
    )
    if empty(trend):
        return
    keep = trend.groupby("religion")[mcol].sum().nlargest(top_n).index
    trend = trend[trend["religion"].isin(keep)]

    left, right = st.columns(2)
    with left:
        fig = px.line(trend, x="period", y=mcol, color="religion", markers=True,
                      title=f"{mlabel} per {gran}", labels={mcol: mlabel, "period": ""})
        show(fig)
    with right:
        fig = px.line(trend, x="period", y="tone", color="religion", markers=True,
                      title=f"Average tone per {gran} (below 0 = negative coverage)", labels={"tone": "Avg tone", "period": ""})
        fig.add_hline(y=0, line_dash="dot", opacity=0.5)
        show(fig)

    st.markdown("#### Top 5 event types involving religion")
    top_types = run_query(
        f"""
        SELECT coalesce(t.eventrootlabel, 'Unknown') AS event_type, count(*) AS events,
               sum(e.nummentions)::bigint AS mentions
        FROM events e LEFT JOIN event_types t ON t.eventcode = e.eventcode
        WHERE {where} AND e.hasreligioncode IS TRUE
        GROUP BY 1 ORDER BY {order_idx} DESC LIMIT 5
        """,
        params,
    )
    by_type = run_query(
        f"""
        SELECT r.religion, coalesce(t.eventrootlabel, 'Unknown') AS event_type, count(*) AS events,
               sum(r.w)::bigint AS mentions
        FROM ({religion_rows(where, fam, 'e.nummentions AS w, e.eventcode', rel_extra)}) r
        LEFT JOIN event_types t ON t.eventcode = r.eventcode
        GROUP BY 1, 2
        """,
        params,
    )
    left, right = st.columns([2, 3])
    with left:
        if not empty(top_types):
            hbar(top_types, mcol, "event_type", "All religion-coded events", labels={mcol: mlabel})
    with right:
        if not empty(by_type):
            by_type = by_type[by_type["religion"].isin(keep)].copy()
            by_type["share"] = by_type[mcol] / by_type.groupby("religion")[mcol].transform("sum")
            by_type = by_type[by_type["event_type"].isin(top_types["event_type"])]
            fig = px.bar(by_type, x="event_type", y="share", color="religion", barmode="group",
                         title="Same five event types, as a share of each religion's coverage",
                         labels={"share": "Share of coverage", "event_type": ""})
            fig.update_yaxes(tickformat=".0%")
            show(fig)


# ------------------------------------------------ 2b. religion vs rights coverage

def view_religion_vs_rights():
    st.subheader("Is religious reporting different from reporting on rights violations?")
    st.caption(
        "Religion-coded: an actor carries a religion code. Rights-related: the event code is one of a short list of "
        "human-rights CAMEO codes (appeals, demands, accusations and investigations about rights abuses). "
        "The two overlap, so each event is placed in exactly one group."
    )
    cat = run_query(
        f"""
        SELECT date_trunc(:gran, e.sqldate::timestamp)::date AS period, {CATEGORY_SQL} AS category,
               count(*) AS events, sum(e.nummentions)::bigint AS mentions,
               avg(e.avgtone) AS tone, avg(e.goldsteinscale) AS goldstein
        FROM events e WHERE {where}
        GROUP BY 1, 2 ORDER BY 1
        """,
        {**params, "gran": gran},
    )
    if empty(cat):
        return

    totals = cat.groupby("category")[["events", "mentions"]].sum()
    both = totals[mcol].get("Religion + rights event", 0)
    religious = both + totals[mcol].get("Religion-coded only", 0)
    rights = both + totals[mcol].get("Rights-related only", 0)
    columns = st.columns(3)
    columns[0].metric("Religion-coded that are also rights events", f"{both / religious:.1%}" if religious else "n/a")
    columns[1].metric("Rights events that involve a religion-coded actor", f"{both / rights:.1%}" if rights else "n/a")
    columns[2].metric("Neither (baseline)", f"{totals[mcol].get('Neither', 0) / totals[mcol].sum():.1%}")

    share = cat.assign(period_total=cat.groupby("period")[mcol].transform("sum"))
    share["share"] = share[mcol] / share["period_total"]
    share = share[share["category"] != "Neither"]
    left, right = st.columns(2)
    with left:
        fig = px.line(share, x="period", y="share", color="category", markers=True,
                      title=f"Share of all {mlabel.lower()} per {gran}", labels={"share": "Share", "period": ""})
        fig.update_yaxes(tickformat=".1%")
        show(fig)
    with right:
        weighted = cat.assign(t=cat["tone"] * cat["events"], g=cat["goldstein"] * cat["events"]).groupby("category")[
            ["t", "g", "events"]].sum()
        weighted["Average tone"] = weighted["t"] / weighted["events"]
        weighted["Average Goldstein"] = weighted["g"] / weighted["events"]
        long = weighted[["Average tone", "Average Goldstein"]].reset_index().melt(
            id_vars="category", var_name="measure", value_name="value")
        fig = px.bar(long, x="category", y="value", color="category", facet_col="measure",
                     title="Tone and conflict level by group", labels={"value": "", "category": ""})
        fig.update_yaxes(matches=None, showticklabels=True)
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        fig.update_layout(showlegend=False)
        show(fig)

    st.markdown("#### What each group is about")
    types = run_query(
        f"""
        SELECT {CATEGORY_SQL} AS category, coalesce(t.eventrootlabel, 'Unknown') AS event_type, count(*) AS events,
               sum(e.nummentions)::bigint AS mentions
        FROM events e LEFT JOIN event_types t ON t.eventcode = e.eventcode
        WHERE {where} AND (e.hasreligioncode IS TRUE OR e.isrightsrelatedevent IS TRUE)
        GROUP BY 1, 2
        """,
        params,
    )
    if not empty(types):
        types["share"] = types[mcol] / types.groupby("category")[mcol].transform("sum")
        keep = types.groupby("event_type")["share"].max().nlargest(8).index
        fig = px.bar(types[types["event_type"].isin(keep)], x="event_type", y="share", color="category",
                     barmode="group", title="Top event types, as a share of each group",
                     labels={"share": "Share of group", "event_type": ""})
        fig.update_yaxes(tickformat=".0%")
        show(fig)


# ------------------------------------------------------------------ 3. Iran & oil

def view_iran():
    st.subheader("Which countries deal with Iran, and how friendly or hostile is it?")
    left, right = st.columns(2)
    oil_only = left.checkbox("Only events whose article URL mentions oil, crude, petroleum or Hormuz", value=False)
    min_events = right.slider("Minimum events per country for the friendly / hostile ranking", 1, 500, 20)
    oil = " AND e.iranoil IS TRUE" if oil_only else ""

    partners = run_query(
        f"""
        SELECT partner, count(*) AS events, avg(goldsteinscale) AS goldstein, avg(avgtone) AS tone,
               count(*) FILTER (WHERE quadclass = 1) AS q1, count(*) FILTER (WHERE quadclass = 2) AS q2,
               count(*) FILTER (WHERE quadclass = 3) AS q3, count(*) FILTER (WHERE quadclass = 4) AS q4
        FROM (
            SELECT e.goldsteinscale, e.avgtone, e.quadclass,
                   CASE WHEN a1.countrycode = 'IRN' THEN a2.countrycode ELSE a1.countrycode END AS partner
            FROM events e
            JOIN actors a1 ON a1.actorid = e.actor1id
            JOIN actors a2 ON a2.actorid = e.actor2id
            WHERE {where} AND e.isiranlinked IS TRUE{oil}
              AND ((a1.countrycode = 'IRN' AND a2.countrycode IS NOT NULL AND a2.countrycode <> 'IRN')
                OR (a2.countrycode = 'IRN' AND a1.countrycode IS NOT NULL AND a1.countrycode <> 'IRN'))
        ) i GROUP BY 1 ORDER BY 2 DESC
        """,
        params,
    )
    if empty(partners, "No Iran interactions in this selection. Widen the dates or clear filters."):
        return
    partners["country"] = partners["partner"].map(cname)

    left, right = st.columns(2)
    with left:
        hbar(partners.head(15), "events", "country", "Countries interacting with Iran the most (events)")
    with right:
        top = partners.head(15).melt(id_vars="country", value_vars=["q1", "q2", "q3", "q4"],
                                     var_name="quad", value_name="count")
        top["quad"] = top["quad"].map(lambda q: QUAD_LABELS[int(q[1])])
        fig = px.bar(top, x="count", y="country", color="quad", orientation="h",
                     title="Mix of cooperation and conflict (same countries)", color_discrete_map=QUAD_COLORS,
                     category_orders={"country": list(partners.head(15)["country"][::-1])},
                     labels={"count": "% of events", "country": ""})
        fig.update_layout(barnorm="percent")
        show(fig, height=max(320, 26 * 15 + 110))

    ranked = partners[partners["events"] >= min_events]
    st.markdown("#### Friendly and hostile")
    st.caption("Average Goldstein score of events between Iran and each country: +10 most cooperative, -10 most conflictual.")
    if empty(ranked, "No country reaches the minimum event count. Lower the minimum."):
        pass
    else:
        extremes = pd.concat([ranked.nlargest(10, "goldstein"), ranked.nsmallest(10, "goldstein")]).drop_duplicates("partner")
        left, right = st.columns(2)
        with left:
            fig = px.bar(extremes.sort_values("goldstein"), x="goldstein", y="country", orientation="h",
                         color="goldstein", color_continuous_scale="RdBu", color_continuous_midpoint=0,
                         title="10 friendliest and 10 most hostile", labels={"goldstein": "Avg Goldstein", "country": ""})
            fig.update_coloraxes(showscale=False)
            show(fig, height=max(320, 26 * len(extremes) + 110))
        with right:
            fig = px.choropleth(ranked, locations="partner", color="goldstein", hover_name="country",
                                color_continuous_scale="RdBu", color_continuous_midpoint=0,
                                title="Average Goldstein with Iran", labels={"goldstein": "Avg Goldstein"})
            fig.update_geos(showframe=False, projection_type="natural earth")
            show(fig)

    st.markdown("#### The oil angle over time")
    st.caption("Oil is a URL keyword match (oil, crude, petroleum, Hormuz), so these are candidate articles, not confirmed oil-crisis coverage.")
    series = run_query(
        f"""
        SELECT date_trunc(:gran, e.sqldate::timestamp)::date AS period, count(*) AS iran_events,
               count(*) FILTER (WHERE e.iranoil IS TRUE) AS oil_events,
               avg(e.avgtone) AS tone_all, avg(e.avgtone) FILTER (WHERE e.iranoil IS TRUE) AS tone_oil
        FROM events e WHERE {where} AND e.isiranlinked IS TRUE
        GROUP BY 1 ORDER BY 1
        """,
        {**params, "gran": gran},
    )
    columns = st.columns(3)
    for column, y, title in zip(
        columns, ["iran_events", "oil_events", None],
        [f"Iran-linked events per {gran}", f"Iran + oil-URL events per {gran}", "Average tone"],
    ):
        with column:
            if y:
                show(px.line(series, x="period", y=y, markers=True, title=title, labels={"period": ""}))
            else:
                tone = series.melt(id_vars="period", value_vars=["tone_all", "tone_oil"], var_name="series", value_name="tone")
                tone["series"] = tone["series"].map({"tone_all": "All Iran-linked", "tone_oil": "Iran + oil URL"})
                fig = px.line(tone, x="period", y="tone", color="series", markers=True, title=title, labels={"period": ""})
                fig.add_hline(y=0, line_dash="dot", opacity=0.5)
                show(fig)

    where_events = run_query(
        f"""
        SELECT e.actiongeo_countryiso3 AS country_code, count(*) AS events, avg(e.avgtone) AS tone
        FROM events e
        WHERE {where} AND e.iranoil IS TRUE AND e.actiongeo_countryiso3 IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT 15
        """,
        params,
    )
    if not empty(where_events, "No Iran + oil-URL events with a known location in this selection."):
        where_events["country"] = where_events["country_code"].map(cname)
        fig = px.bar(where_events.sort_values("events"), x="events", y="country", orientation="h",
                     color="tone", color_continuous_scale="RdBu", color_continuous_midpoint=0,
                     title="Where Iran + oil-URL events took place (color = average tone)",
                     labels={"events": "Events", "country": "", "tone": "Avg tone"})
        show(fig, height=max(320, 26 * len(where_events) + 110))


# ---------------------------------------------------------------------- 4. protests

def protest_responses(daily, min_per_day, lag):
    """Responses seen in each country on protest days and the `lag` days after them.

    A protest day is a country-day with at least `min_per_day` protest events. Lift compares the share of
    events that are responses inside those windows with the share on all other days.
    """
    labels = list(RESPONSES)
    records = []
    for country, group_df in daily.groupby("country"):
        group_df = group_df.set_index("day").sort_index()
        group_df = group_df.reindex(pd.date_range(group_df.index.min(), group_df.index.max(), freq="D"), fill_value=0)
        protest_day = (group_df["protests"] >= min_per_day).astype(int)
        if protest_day.sum() == 0:
            continue
        in_window = protest_day.rolling(lag + 1, min_periods=1).max().astype(bool)
        inside, outside = group_df[in_window], group_df[~in_window]
        protests = int(inside["protests"].sum())
        for i, label in enumerate(labels):
            col = f"r{i}"
            inside_rate = inside[col].sum() / max(inside["events"].sum(), 1)
            outside_rate = outside[col].sum() / outside["events"].sum() if outside["events"].sum() else np.nan
            lift = inside_rate / outside_rate if outside_rate == outside_rate and outside_rate > 0 else np.nan
            records.append({
                "country": country, "response": label, "count": int(inside[col].sum()),
                "per100": 100 * inside[col].sum() / max(protests, 1), "lift": lift,
                "protest_days": int(protest_day.sum()), "protests": protests,
            })
    return pd.DataFrame(records)


def view_protests():
    st.subheader("Where do protests escalate, and where do they lead to change?")

    st.markdown("#### 4a · Average Goldstein score of protest events, by country")
    min_protests = st.slider("Minimum protest events per country", 5, 1000, 50)
    protests = run_query(
        f"""
        SELECT e.actiongeo_countryiso3 AS country_code, count(*) AS protests,
               avg(e.goldsteinscale) AS goldstein, avg(e.avgtone) AS tone,
               avg((left(e.eventcode, 3) = '145')::int::float8) AS violent_share
        FROM events e
        WHERE {where} AND e.isprotest IS TRUE AND e.actiongeo_countryiso3 IS NOT NULL
        GROUP BY 1
        """,
        params,
    )
    protests = protests[protests["protests"] >= min_protests].copy()
    if not empty(protests, "No country reaches that many protest events. Lower the minimum or widen the dates."):
        protests["country"] = protests["country_code"].map(cname)
        left, right = st.columns(2)
        with left:
            low = protests.nsmallest(20, "goldstein")
            fig = px.bar(low.sort_values("goldstein", ascending=False), x="goldstein", y="country", orientation="h",
                         color="violent_share", color_continuous_scale="Reds",
                         title="20 lowest averages (lower = more conflictual protests)",
                         labels={"goldstein": "Avg Goldstein", "country": "", "violent_share": "Violent share"})
            fig.update_coloraxes(colorbar_tickformat=".0%")
            show(fig, height=max(320, 26 * len(low) + 110))
        with right:
            fig = px.choropleth(protests, locations="country_code", color="goldstein", hover_name="country",
                                color_continuous_scale="RdBu", color_continuous_midpoint=protests["goldstein"].median(),
                                title="Average Goldstein of protest events",
                                labels={"goldstein": "Avg Goldstein"})
            fig.update_geos(showframe=False, projection_type="natural earth")
            show(fig)
        st.caption(
            "Goldstein scores are assigned per event code, so a country's average mostly reflects its mix of protest types: "
            "riots (code 145) score lower than rallies. 'Violent share' is the share of protest events coded as violent protest."
        )

    st.markdown("#### 4b · What follows protests: concessions, arrests or force?")
    left, right = st.columns(2)
    lag = left.slider("Look this many days after a protest day", 0, 7, 2)
    min_per_day = right.slider("A protest day has at least this many protest events", 1, 100, 5)
    resp_sql = ", ".join(f"count(*) FILTER (WHERE {cond}) AS r{i}" for i, cond in enumerate(RESPONSES.values()))
    daily = run_query(
        f"""
        SELECT e.actiongeo_countryiso3 AS country, e.sqldate AS day, count(*) AS events,
               count(*) FILTER (WHERE e.isprotest IS TRUE) AS protests, {resp_sql}
        FROM events e
        WHERE {where} AND e.actiongeo_countryiso3 IS NOT NULL
        GROUP BY 1, 2
        """,
        params,
    )
    if empty(daily):
        return
    result = protest_responses(daily, min_per_day, lag)
    if empty(result, "No country has a protest day under these settings. Lower the protest threshold."):
        return
    result["name"] = result["country"].map(cname)

    pooled = result.groupby("response").agg(count=("count", "sum"))
    protest_total = result.drop_duplicates("country")["protests"].sum()
    pooled["per100"] = 100 * pooled["count"] / max(protest_total, 1)
    left, right = st.columns([2, 3])
    with left:
        hbar(pooled.reset_index(), "per100", "response", "Most common responses (per 100 protest events)",
             labels={"per100": "Per 100 protest events"})
    with right:
        biggest = result.drop_duplicates("country").nlargest(12, "protests")["country"]
        sel = result[result["country"].isin(biggest)]
        fig = px.bar(sel, x="name", y="per100", color="response", barmode="group",
                     title="Responses per 100 protest events, 12 countries with the most protests",
                     labels={"per100": "Per 100 protest events", "name": ""})
        show(fig)

    st.markdown("**Is it more than usual?**")
    st.caption(
        "Lift = share of events that are this response in the protest window ÷ the same share on other days. "
        "Above 1 means more of that response than normal. Same-country timing only: it does not prove a protest caused it."
    )
    heat = result[result["country"].isin(result.drop_duplicates("country").nlargest(20, "protests")["country"])]
    matrix = heat.pivot(index="name", columns="response", values="lift")
    fig = px.imshow(matrix, aspect="auto", color_continuous_scale="RdBu_r", color_continuous_midpoint=1,
                    zmin=0, zmax=2, text_auto=".1f", labels=dict(color="Lift"))
    fig.update_xaxes(title=None, side="top")
    fig.update_yaxes(title=None)
    show(fig, height=max(360, 30 * len(matrix) + 140))


{
    VIEWS[0]: view_religions_and_orgs,
    VIEWS[1]: view_religion_coverage,
    VIEWS[2]: view_religion_vs_rights,
    VIEWS[3]: view_iran,
    VIEWS[4]: view_protests,
}[view]()

with st.expander("How to read this dashboard"):
    st.markdown(
        """
- **Event-level data.** Each row is one coded event. "Mentions" are GDELT's count of articles repeating that event, not text analysis.
- **Religion** comes from the CAMEO religion codes on either actor. **Human-rights organization** means an actor typed `HRI`
  that is not also coded as government, military, police, judiciary or intelligence.
- **Rights-related events** are a short list of human-rights event codes, not all coverage of rights.
- **Country** filters on where the event happened (ActionGeo). In the Iran view, partner country is the other actor's country.
- **Iran + oil** is an Iran-linked event whose source URL contains an oil keyword. Treat it as a candidate list.
- **Tone** is GDELT's average tone (negative = more negative coverage). **Goldstein** runs from -10 (conflict) to +10 (cooperation).
        """
    )