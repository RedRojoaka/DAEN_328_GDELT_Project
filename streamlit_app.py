"""GDELT research dashboard in presentation style: one slide per research question, built for screenshots.

Reads the tables created by load.py (events, actors, actor_religions, locations,
event_types). Every date here is the day the event happened (SQLDATE), never
the day it was extracted (DATEADDED / ReportDate).

Run with Docker:  docker compose run --rm --no-deps --service-ports dashboard
Run locally:      streamlit run streamlit_app.py

Everything is event-level: GDELT codes who did what to whom, not article text.
Religion, human-rights and Iran/oil flags are heuristics.
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

st.set_page_config(page_title="GDELT Research Dashboard", page_icon="🌍", layout="wide",
                   initial_sidebar_state="collapsed")

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

# Q2b: separate "religious reporting" from "rights-violation reporting". Each event lands in one group.
CATEGORY_SQL = """CASE
    WHEN e.hasreligioncode IS TRUE AND e.isrightsrelatedevent IS TRUE THEN 'Religion + rights event'
    WHEN e.hasreligioncode IS TRUE THEN 'Religion-coded only'
    WHEN e.isrightsrelatedevent IS TRUE THEN 'Rights-related only'
    ELSE 'Neither' END"""
CAT_COLORS = {
    "Religion-coded only": "#6366f1", "Rights-related only": "#f43f5e",
    "Religion + rights event": "#f59e0b", "Neither": "#94a3b8",
}

QUAD_LABELS = {1: "Verbal cooperation", 2: "Material cooperation", 3: "Verbal conflict", 4: "Material conflict"}
QUAD_COLORS = {
    "Verbal cooperation": "#7dd3fc", "Material cooperation": "#0369a1",
    "Verbal conflict": "#fdba74", "Material conflict": "#b91c1c",
}

VIEWS = [
    "Q1 · Religions × rights orgs",
    "Q2a · Religion coverage",
    "Q2b · Religion vs. rights",
    "Q3 · Iran & oil",
    "Q4 · Protests",
]

# One look per question: banner gradient, accents, and the color scales its charts use.
THEMES = [
    dict(  # Q1 amethyst
        a="#7c3aed", b="#ec4899",
        banner="linear-gradient(120deg,#3b0764 0%,#7c3aed 55%,#ec4899 100%)",
        discrete=["#7c3aed", "#ec4899", "#06b6d4", "#f59e0b", "#10b981", "#6366f1", "#f43f5e", "#a3e635"],
        seq=["#f5f3ff", "#c4b5fd", "#7c3aed", "#3b0764"], bar=["#c4b5fd", "#7c3aed", "#3b0764"],
        div=["#be185d", "#f9a8d4", "#f5f3ff", "#c4b5fd", "#5b21b6"],
    ),
    dict(  # Q2a lagoon
        a="#0d9488", b="#22d3ee",
        banner="linear-gradient(120deg,#042f2e 0%,#0d9488 55%,#22d3ee 100%)",
        discrete=["#0d9488", "#f59e0b", "#0ea5e9", "#a3e635", "#6366f1", "#f43f5e", "#14b8a6", "#fb923c", "#38bdf8", "#84cc16"],
        seq=["#f0fdfa", "#99f6e4", "#14b8a6", "#134e4a"], bar=["#5eead4", "#0d9488", "#134e4a"],
        div=["#be123c", "#fda4af", "#f0fdfa", "#5eead4", "#0f766e"],
    ),
    dict(  # Q2b coral and indigo
        a="#6366f1", b="#f43f5e",
        banner="linear-gradient(120deg,#1e1b4b 0%,#6366f1 50%,#f43f5e 100%)",
        discrete=["#6366f1", "#f43f5e", "#f59e0b", "#14b8a6", "#a855f7", "#0ea5e9", "#84cc16", "#fb7185"],
        seq=["#eef2ff", "#c7d2fe", "#6366f1", "#1e1b4b"], bar=["#a5b4fc", "#6366f1", "#1e1b4b"],
        div=["#be123c", "#fda4af", "#eef2ff", "#a5b4fc", "#4338ca"],
    ),
    dict(  # Q3 crude
        a="#f59e0b", b="#dc2626",
        banner="linear-gradient(120deg,#1c1917 0%,#92400e 45%,#f59e0b 80%,#dc2626 100%)",
        discrete=["#f59e0b", "#dc2626", "#0ea5e9", "#78716c", "#16a34a", "#a855f7", "#fb923c", "#0f766e"],
        seq=["#fffbeb", "#fcd34d", "#f59e0b", "#78350f"], bar=["#fcd34d", "#f59e0b", "#78350f"],
        div=["#991b1b", "#f87171", "#fef3c7", "#7dd3fc", "#0369a1"],
    ),
    dict(  # Q4 electric
        a="#0ea5e9", b="#22d3ee",
        banner="linear-gradient(120deg,#082f49 0%,#0369a1 45%,#0ea5e9 75%,#22d3ee 100%)",
        discrete=["#0ea5e9", "#ef4444", "#22d3ee", "#f59e0b", "#8b5cf6", "#10b981", "#f472b6", "#84cc16"],
        seq=["#f0f9ff", "#7dd3fc", "#0ea5e9", "#082f49"], bar=["#7dd3fc", "#0ea5e9", "#082f49"],
        div=["#7f1d1d", "#ef4444", "#fde68a", "#7dd3fc", "#0369a1"],
        viol=["#bae6fd", "#38bdf8", "#f97316", "#b91c1c"],
    ),
]

BANNERS = [
    dict(kicker="Question 1", title="Correlation between certain religions and human-rights organizations",
         sub="Which faiths, which organizations, and which pairings dominate the conversation?",
         chips=["Most-mentioned religions", "Most-mentioned organizations", "Top religion / organization pairs"]),
    dict(kicker="Question 2a", title="Volume and themes of coverage of the largest religions",
         sub="How much is each religion covered, in what tone, and through which kinds of events?",
         chips=["Coverage over time", "Average tone trends", "Top 5 event types"]),
    dict(kicker="Question 2b", title="Religious reporting versus coverage of rights violations",
         sub="Do the two kinds of reporting differ in size, tone and subject matter?",
         chips=["Religion-only vs. rights-only", "How much they overlap", "Different themes"]),
    dict(kicker="Question 3", title="How the Iran oil crisis plays out across nations",
         sub="Who interacts with Iran most, and are they friendly or hostile?",
         chips=["Who interacts with Iran", "Friendly vs. hostile", "The oil angle over time"]),
    dict(kicker="Question 4", title="Where protests escalate, and where they lead to change",
         sub="Which countries see the most conflictual protests, and what usually follows them?",
         chips=["Goldstein score by country", "Escalation over time", "Concession, arrest or force"]),
]

CSS_TEMPLATE = """
@keyframes drift {0%{background-position:0% 50%} 50%{background-position:100% 50%} 100%{background-position:0% 50%}}
@keyframes rise {from{opacity:0; transform:translateY(16px)} to{opacity:1; transform:translateY(0)}}
@keyframes pulse {0%{box-shadow:0 0 0 0 rgba(255,255,255,.75)} 70%{box-shadow:0 0 0 10px rgba(255,255,255,0)} 100%{box-shadow:0 0 0 0 rgba(255,255,255,0)}}
.apptitle {letter-spacing:.22em; font-size:.8rem; text-transform:uppercase; opacity:.65; margin:0 0 6px}
.banner {background:__BANNER__; background-size:220% 220%; animation:drift 14s ease infinite, rise .7s ease both;
  border-radius:20px; padding:26px 32px 18px; color:#fff; margin:6px 0 18px; box-shadow:0 14px 34px -14px rgba(__ARGB__,.65)}
.banner .kicker {letter-spacing:.18em; font-size:.78rem; opacity:.9; text-transform:uppercase; display:flex; align-items:center; gap:10px}
.banner .kicker i {width:9px; height:9px; border-radius:50%; background:#fff; display:inline-block; animation:pulse 2s infinite}
.banner h2 {margin:.4rem 0 .25rem; font-size:1.8rem; line-height:1.2; color:#fff !important; padding:0}
.banner p {margin:0 0 12px; opacity:.92; color:#fff}
.chip {display:inline-block; background:rgba(255,255,255,.18); border:1px solid rgba(255,255,255,.4); border-radius:999px;
  padding:3px 13px; margin:0 8px 6px 0; font-size:.82rem; color:#fff}
[data-testid="stMetric"] {background:rgba(__ARGB__,.10); border-left:5px solid __A__; border-radius:14px; padding:14px 18px;
  animation:rise .6s ease both; transition:transform .2s ease, box-shadow .2s ease}
[data-testid="stMetric"]:hover {transform:translateY(-4px); box-shadow:0 10px 24px -14px rgba(__ARGB__,.9)}
[data-testid="stPlotlyChart"] {border:1px solid rgba(__ARGB__,.28); border-radius:16px; padding:6px 8px 2px;
  background:rgba(__ARGB__,.04); animation:rise .7s ease both}
.why {border-left:4px solid __B__; padding:7px 14px; margin:4px 0 22px; font-size:.9rem; opacity:.92;
  background:rgba(__BRGB__,.08); border-radius:0 10px 10px 0; animation:rise .8s ease both}
.why b {color:__A__}
h4 {color:__A__ !important}
/* presentation style: no motion, no toolbar, big type, so screenshots are clean and consistent */
*, *::before, *::after {animation:none !important; transition:none !important}
[data-testid="stDecoration"], [data-testid="stStatusWidget"], footer {display:none !important}
[data-testid="stHeader"] {background:transparent}
[data-testid="stExpandSidebarButton"], [data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"] {opacity:.25}
.block-container {max-width:1500px; padding-top:1.2rem; padding-bottom:2rem}
.banner {padding:30px 38px 22px; border-radius:22px}
.banner h2 {font-size:2.5rem}
.banner p {font-size:1.2rem}
.chip {font-size:.95rem; padding:5px 16px}
.scope {font-size:1rem; opacity:.8; margin:-6px 0 18px 4px; letter-spacing:.02em}
.scope b {color:__A__}
[data-testid="stMetric"] {padding:18px 22px}
[data-testid="stMetricValue"] {font-size:2.3rem}
[data-testid="stMetricLabel"] p {font-size:1rem}
.why {font-size:1.05rem; padding:10px 18px; margin:6px 0 30px}
h4 {font-size:1.6rem !important; margin-top:12px}
.settings {font-size:.9rem; opacity:.65; margin:0 0 12px}
.st-key-nav div[role="radiogroup"] {gap:8px; flex-wrap:wrap}
.st-key-nav div[role="radiogroup"] > label {background:rgba(__ARGB__,.12); border:1px solid rgba(__ARGB__,.35);
  padding:7px 18px; border-radius:999px; margin:0; transition:all .2s ease; cursor:pointer}
.st-key-nav div[role="radiogroup"] > label:hover {background:rgba(__ARGB__,.28); transform:translateY(-2px)}
.st-key-nav div[role="radiogroup"] > label > div:first-child {display:none}
.st-key-nav div[role="radiogroup"] > label:has(input:checked) {background:__A__; border-color:__A__}
.st-key-nav div[role="radiogroup"] > label:has(input:checked) p {color:#fff !important; font-weight:600}
"""


# ----------------------------------------------------------------------- helpers

def cname(code):
    return COUNTRY_NAMES.get(code, code)


def rgb(hex_color):
    h = hex_color.lstrip("#")
    return ",".join(str(int(h[i:i + 2], 16)) for i in (0, 2, 4))


def inject_css(t):
    css = (CSS_TEMPLATE.replace("__ARGB__", rgb(t["a"])).replace("__BRGB__", rgb(t["b"]))
           .replace("__BANNER__", t["banner"]).replace("__A__", t["a"]).replace("__B__", t["b"]))
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def banner(b):
    chips = "".join(f'<span class="chip">{chip}</span>' for chip in b["chips"])
    st.markdown(
        f'<div class="banner"><div class="kicker"><i></i>{b["kicker"]}</div>'
        f'<h2>{b["title"]}</h2><p>{b["sub"]}</p>{chips}</div>',
        unsafe_allow_html=True,
    )


def why(sentence):
    """One sentence under a chart saying how it helps answer the question."""
    st.markdown(f'<div class="why"><b>How this helps:</b> {sentence}</div>', unsafe_allow_html=True)


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
    clauses = ["e.sqldate BETWEEN :start AND :end"]  # SQLDATE = when the event happened
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


@st.cache_data(ttl=3600, show_spinner="Running query...")
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
    fig.update_layout(
        margin=dict(l=12, r=12, t=70, b=12), legend_title_text=None,
        title_font=dict(size=22, color=T["a"]), colorway=T["discrete"],
        font=dict(size=15), legend=dict(font=dict(size=14)),
    )
    fig.update_layout(height=max(height or 0, 460))
    fig.update_xaxes(tickfont=dict(size=14))
    fig.update_yaxes(tickfont=dict(size=14))
    st.plotly_chart(fig, config={
        "displayModeBar": "hover", "displaylogo": False,
        "modeBarButtons": [["toImage"]],  # just the camera: saves the chart as a sharp PNG
        "toImageButtonOptions": {"format": "png", "scale": 2, "filename": "gdelt_chart"},
    })


def round_bars(fig):
    try:
        fig.update_traces(marker_cornerradius=6, selector=dict(type="bar"))
    except Exception:
        pass


def hbar(df, x, y, title, height=None, **kwargs):
    """Horizontal ranked bars; shaded by value unless a color column is given."""
    gradient = "color" not in kwargs
    if gradient:
        kwargs["color"] = x
        kwargs.setdefault("color_continuous_scale", T["bar"])
    fig = px.bar(df.sort_values(x), x=x, y=y, orientation="h", title=title, **kwargs)
    if gradient:
        fig.update_coloraxes(showscale=False)
    fig.update_layout(yaxis_title=None)
    round_bars(fig)
    show(fig, height or max(320, 26 * len(df) + 110))


def table(df):
    """Plain HTML table. st.dataframe needs pyarrow, which can't import here (pyarrow 26 wants NumPy 2, pins say <2)."""
    st.markdown(df.to_html(index=False, border=0, float_format=lambda v: f"{v:,.2f}"), unsafe_allow_html=True)


def empty(df, message="No data for these filters. Widen the date range or clear a filter."):
    if df is None or df.empty:
        st.info(message)
        return True
    return False


def trend_area(df, x, y, title, labels=None, **kwargs):
    """Area chart; falls back to bars when there is a single period (an area needs two points to draw)."""
    if df[x].nunique() < 2:
        return px.bar(df, x=x, y=y, title=title + " (only one period: widen the dates)", labels=labels, **kwargs)
    return px.area(df, x=x, y=y, title=title, labels=labels, **kwargs)


# ------------------------------------------------------------------------ header

st.markdown('<div class="apptitle">GDELT research dashboard</div>', unsafe_allow_html=True)

try:
    bounds = run_query("SELECT min(sqldate) AS lo, max(sqldate) AS hi FROM events")
except Exception as exception:  # connection refused, missing table, bad password...
    st.error(f"Could not read from Postgres: {exception}")
    st.info("Make sure the ETL finished (`Load complete`) and the database is running.")
    st.stop()

if pd.isna(bounds.at[0, "lo"]):
    st.warning("The events table is empty. Run the ETL first.")
    st.stop()

data_max = pd.Timestamp(bounds.at[0, "hi"]).date()
# A few GDELT events carry very old SQLDATEs; keep the slider to the last ~13 months of event dates.
data_min = max(pd.Timestamp(bounds.at[0, "lo"]).date(), data_max - timedelta(days=400))

with st.sidebar:
    st.header("Filters")
    if data_min < data_max:
        start, end = st.slider(
            "Event dates (SQLDATE)",
            min_value=data_min,
            max_value=data_max,
            value=(max(data_min, data_max - timedelta(days=182)), data_max),
            format="YYYY-MM-DD",
        )
    else:
        start = end = data_min
        st.caption(f"Only one event date loaded: {data_min}")
    st.caption("Dates are when events happened (SQLDATE), not when they were extracted.")

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
    span_days = (end - start).days
    gran = st.selectbox(
        "Trend granularity (by event date)", ["day", "week", "month"],
        index=0 if span_days <= 21 else (1 if span_days <= 200 else 2), format_func=str.title,
        help="Ranges up to 3 weeks default to Day, longer ones to Week. Weeks start on Monday.",
    )
    if st.button("Refresh data"):
        st.cache_data.clear()
        st.rerun()

mcol = "events" if measure == "Events" else "mentions"
mlabel = "Events" if measure == "Events" else "Media mentions"
order_idx = 2 if mcol == "events" else 3  # column position of the measure in two-column rollups
where, params = build_where(start, end, countries, religions, orgs)

with st.container(key="nav"):
    view = st.radio("View", VIEWS, horizontal=True, label_visibility="collapsed", key="view")
view_index = VIEWS.index(view)
T = THEMES[view_index]
inject_css(T)
banner(BANNERS[view_index])
st.markdown(
    f'<div class="scope">Events from <b>{start:%b %d, %Y}</b> to <b>{end:%b %d, %Y}</b> by date the event happened '
    f'· trends per <b>{gran}</b> · counting <b>{mlabel.lower()}</b></div>',
    unsafe_allow_html=True,
)

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
st.write("")


# ---------------------------------------------------- Q1. religions x human-rights orgs

def view_religions_and_orgs():
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
            why("Ranks religions by how often events involve them, so you can see which faiths dominate the conversation before looking at any organization.")
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
            hbar(orgs_df, mcol, "org", f"Most-mentioned human-rights organizations ({mlabel.lower()})", labels={mcol: mlabel})
            why("Ranks the human-rights organizations that appear in events, showing whether a few organizations account for most of the activity.")

    st.markdown("#### Religions through time")
    race = run_query(
        f"""
        SELECT religion, period, count(*) AS events, sum(w)::bigint AS mentions
        FROM ({religion_rows(where, fam, "e.nummentions AS w, date_trunc(:gran, e.sqldate::timestamp)::date AS period", rel_extra)}) r
        GROUP BY 1, 2 ORDER BY 2
        """,
        {**params, "gran": gran},
    )
    if not empty(race):
        top8 = race.groupby("religion")[mcol].sum().nlargest(8).index
        wide = race[race["religion"].isin(top8)].pivot_table(
            index="period", columns="religion", values=mcol, aggfunc="sum", fill_value=0)
        full = wide.reset_index().melt(id_vars="period", var_name="religion", value_name=mcol)
        fig = trend_area(full, "period", mcol, f"{mlabel} per {gran}", labels={mcol: mlabel, "period": ""},
                         color="religion", color_discrete_sequence=T["discrete"],
                         category_orders={"religion": list(top8)})
        show(fig, height=440)
        why("Shows how each religion's coverage rises and falls over the selected dates, so you can tell whether the leaders are steady or only lead in certain periods.")

    st.markdown("#### Which religion and organization pairs appear together?")
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
    st.caption(
        "A pair is one event with a religion-coded actor on one side and a human-rights organization on the other. "
        "Co-occurrence only: it does not say who supports or criticizes whom."
    )

    top = pairs.nlargest(15, mcol).assign(pair=lambda d: d["religion"] + "  ×  " + d["org"])
    left, right = st.columns(2)
    with left:
        hbar(top, mcol, "pair", f"Top pairs ({mlabel.lower()})", labels={mcol: mlabel})
        why("Lists the religion and organization pairings that co-occur most, which is the direct answer to which pairs are mentioned the most.")
    with right:
        sun = pairs.nlargest(60, mcol)
        fig = px.sunburst(sun, path=["religion", "org"], values=mcol, color="religion",
                          color_discrete_sequence=T["discrete"], title="Which organizations appear with each religion")
        show(fig, height=max(320, 26 * 15 + 110))
        why("Lets you click a religion to see which organizations appear with it, so you can compare how concentrated each religion's organization mix is.")

    mode = "Pair counts"
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
    kwargs = dict(color_continuous_scale=T["seq"], text_auto=",.0f")
    if mode == "Share of each org's pairings":
        matrix = matrix.div(org_total.reindex(matrix.index), axis=0)
        kwargs = dict(color_continuous_scale=T["seq"], text_auto=".0%")
    elif mode == "Lift vs. independence":
        expected = np.outer(org_total.reindex(matrix.index), rel_total.reindex(matrix.columns)) / pairs[mcol].sum()
        matrix = (matrix / expected).where(raw >= 5)  # hide cells with fewer than 5 pairs
        kwargs = dict(color_continuous_scale=T["div"][::-1], color_continuous_midpoint=1, text_auto=".1f")
    fig = px.imshow(matrix, aspect="auto", labels=dict(color=mode), **kwargs)
    fig.update_xaxes(title=None, side="top")
    fig.update_yaxes(title=None)
    show(fig, height=max(380, 34 * len(matrix) + 130))
    why("Crosses every top religion with every top organization, and the lift view separates real affinity from pairs that are frequent only because both sides are frequent (cells with fewer than 5 pairs are hidden).")


# ------------------------------------------------------------ Q2a. religion coverage

def view_religion_coverage():
    fam = religion_case(group)
    rel_extra = " AND ar.religioncode = ANY(:religions)" if religions else ""
    top_n = 5  # the five largest religions

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
    trend = trend[trend["religion"].isin(keep)].copy()

    other = "mentions" if mcol == "events" else "events"
    bubble = trend.assign(tw=trend["tone"].fillna(0) * trend["events"]).groupby("religion", as_index=False).agg(
        events=("events", "sum"), mentions=("mentions", "sum"), tw=("tw", "sum"))
    bubble["tone"] = bubble["tw"] / bubble["events"]
    kwargs = dict(
        x=mcol, y="tone", size=other, color="religion", size_max=60, hover_name="religion",
        color_discrete_sequence=T["discrete"],
        title=f"Volume vs. tone by religion (bubble size = {other})",
        labels={mcol: mlabel, "tone": "Average tone"},
    )
    fig = px.scatter(bubble, **kwargs)
    fig.add_hline(y=0, line_dash="dot", opacity=0.5)
    show(fig, height=480)
    why("Plots each religion's coverage volume against its average tone, revealing which religions get the most coverage and whether it skews negative.")

    left, right = st.columns(2)
    with left:
        fig = trend_area(trend, "period", mcol, f"{mlabel} per {gran}", labels={mcol: mlabel, "period": ""},
                         color="religion", color_discrete_sequence=T["discrete"])
        show(fig)
        why("Stacks each religion's coverage over time so you can compare their relative sizes and see which trends are growing.")
    with right:
        fig = px.line(trend, x="period", y="tone", color="religion", markers=True, color_discrete_sequence=T["discrete"],
                      title=f"Average tone per {gran} (below 0 = negative coverage)", labels={"tone": "Average tone", "period": ""})
        fig.add_hline(y=0, line_dash="dot", opacity=0.5)
        show(fig)
        why("Tracks average tone per religion over time so you can spot religions whose coverage is consistently or increasingly negative.")

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
            why("Shows what kinds of events religion-coded coverage is made of, which answers what themes dominate it.")
    with right:
        if not empty(by_type):
            by_type = by_type[by_type["religion"].isin(keep)].copy()
            by_type["share"] = by_type[mcol] / by_type.groupby("religion")[mcol].transform("sum")
            by_type = by_type[by_type["event_type"].isin(top_types["event_type"])]
            fig = px.bar(by_type, x="event_type", y="share", color="religion", barmode="group",
                         color_discrete_sequence=T["discrete"],
                         title="Same five event types, as a share of each religion's coverage",
                         labels={"share": "Share of coverage", "event_type": ""})
            fig.update_yaxes(tickformat=".0%")
            round_bars(fig)
            show(fig)
            why("Compares those same five event types across religions, showing whether each faith is covered through different themes.")


# ------------------------------------------------------- Q2b. religion vs rights

def view_religion_vs_rights():
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
    why("These three numbers show how much religious and rights-related reporting overlap, which tells you whether they can really be separated.")

    share = cat.assign(period_total=cat.groupby("period")[mcol].transform("sum"))
    share["share"] = share[mcol] / share["period_total"]
    share = share[share["category"] != "Neither"].copy()
    fig = px.line(share, x="period", y="share", color="category", color_discrete_map=CAT_COLORS, markers=True,
                  category_orders={"category": ["Religion-coded only", "Religion + rights event", "Rights-related only"]},
                  title=f"Share of all {mlabel.lower()} per {gran}", labels={"share": "Share", "period": "", "category": ""})
    fig.update_yaxes(tickformat=".1%")
    show(fig, height=420)
    why("Shows how big each group is in every period, so you can see whether religious and rights reporting rise and fall together or independently.")

    left, right = st.columns(2)
    with left:
        weighted = cat.assign(t=cat["tone"] * cat["events"], g=cat["goldstein"] * cat["events"]).groupby("category")[
            ["t", "g", "events"]].sum()
        weighted["Average tone"] = weighted["t"] / weighted["events"]
        weighted["Average Goldstein"] = weighted["g"] / weighted["events"]
        long = weighted[["Average tone", "Average Goldstein"]].reset_index().melt(
            id_vars="category", var_name="measure", value_name="value")
        fig = px.bar(long, x="category", y="value", color="category", facet_col="measure",
                     color_discrete_map=CAT_COLORS, title="Tone and conflict level by group",
                     labels={"value": "", "category": ""})
        fig.update_yaxes(matches=None, showticklabels=True)
        fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
        fig.update_xaxes(showticklabels=False)
        round_bars(fig)
        show(fig)
        why("Compares the tone and conflict level of each group, which shows whether rights-violation coverage is harsher than religious coverage.")
    with right:
        tm = totals[mcol].reset_index()
        tm = tm[tm["category"] != "Neither"]
        fig = px.treemap(tm, path=[px.Constant("All events"), "category"], values=mcol, color="category",
                         color_discrete_map={"(?)": "#475569", **CAT_COLORS},
                         title="How the three groups divide these events")
        show(fig)
        why("Sizes the three groups against each other so the overlap between religion-coded and rights-related events is visible at a glance.")

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
                     barmode="group", color_discrete_map=CAT_COLORS,
                     title="Top event types, as a share of each group",
                     labels={"share": "Share of group", "event_type": ""})
        fig.update_yaxes(tickformat=".0%")
        round_bars(fig)
        show(fig)
        why("Shows which event types define each group, which makes the difference between religious reporting and rights reporting concrete.")


# ------------------------------------------------------------------- Q3. Iran & oil

def view_iran():
    oil_only, min_events = False, 20  # countries need 20+ events with Iran to be ranked
    oil = " AND e.iranoil IS TRUE" if oil_only else ""
    iran_pair = (
        "FROM events e JOIN actors a1 ON a1.actorid = e.actor1id JOIN actors a2 ON a2.actorid = e.actor2id "
        f"WHERE {where} AND e.isiranlinked IS TRUE{oil} "
        "AND ((a1.countrycode = 'IRN' AND a2.countrycode IS NOT NULL AND a2.countrycode <> 'IRN') "
        "OR (a2.countrycode = 'IRN' AND a1.countrycode IS NOT NULL AND a1.countrycode <> 'IRN'))"
    )
    partners = run_query(
        f"""
        SELECT partner, count(*) AS events, avg(goldsteinscale) AS goldstein, avg(avgtone) AS tone,
               count(*) FILTER (WHERE quadclass = 1) AS q1, count(*) FILTER (WHERE quadclass = 2) AS q2,
               count(*) FILTER (WHERE quadclass = 3) AS q3, count(*) FILTER (WHERE quadclass = 4) AS q4
        FROM (
            SELECT e.goldsteinscale, e.avgtone, e.quadclass,
                   CASE WHEN a1.countrycode = 'IRN' THEN a2.countrycode ELSE a1.countrycode END AS partner
            {iran_pair}
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
        why("Ranks countries by how many events they share with Iran, which answers who interacts with Iran the most.")
    with right:
        top = partners.head(15).melt(id_vars="country", value_vars=["q1", "q2", "q3", "q4"],
                                     var_name="quad", value_name="count")
        top["quad"] = top["quad"].map(lambda q: QUAD_LABELS[int(q[1])])
        fig = px.bar(top, x="count", y="country", color="quad", orientation="h",
                     title="Mix of cooperation and conflict (same countries)", color_discrete_map=QUAD_COLORS,
                     category_orders={"country": list(partners.head(15)["country"][::-1])},
                     labels={"count": "% of events", "country": ""})
        fig.update_layout(barnorm="percent")
        round_bars(fig)
        show(fig, height=max(320, 26 * 15 + 110))
        why("Splits each country's events into cooperation and conflict, so you can see whether heavy interaction is friendly or hostile.")

    ranked = partners[partners["events"] >= min_events]
    st.markdown("#### Friendly and hostile")
    st.caption("Average Goldstein score of events between Iran and each country: +10 most cooperative, -10 most conflictual.")
    if not empty(ranked, "No country reaches the minimum event count. Lower the minimum."):
        extremes = pd.concat([ranked.nlargest(10, "goldstein"), ranked.nsmallest(10, "goldstein")]).drop_duplicates("partner")
        left, right = st.columns(2)
        with left:
            fig = px.bar(extremes.sort_values("goldstein"), x="goldstein", y="country", orientation="h",
                         color="goldstein", color_continuous_scale=T["div"], color_continuous_midpoint=0,
                         title="10 friendliest and 10 most hostile", labels={"goldstein": "Avg Goldstein", "country": ""})
            fig.update_coloraxes(showscale=False)
            round_bars(fig)
            show(fig, height=max(320, 26 * len(extremes) + 110))
            why("Orders countries by average Goldstein score with Iran, putting the friendliest and the most hostile at the two ends.")
        with right:
            by_period = run_query(
                f"""
                SELECT period, partner, count(*) AS events, avg(goldsteinscale) AS goldstein
                FROM (
                    SELECT date_trunc(:gran, e.sqldate::timestamp)::date AS period, e.goldsteinscale,
                           CASE WHEN a1.countrycode = 'IRN' THEN a2.countrycode ELSE a1.countrycode END AS partner
                    {iran_pair}
                ) i GROUP BY 1, 2 ORDER BY 1
                """,
                {**params, "gran": gran},
            )
            by_period = by_period[by_period["partner"].isin(ranked["partner"])].copy()
            if not empty(by_period):
                by_period["gw"] = by_period["goldstein"] * by_period["events"]
                by_country = by_period.groupby("partner", as_index=False).agg(gw=("gw", "sum"), events=("events", "sum"))
                by_country["goldstein"] = by_country["gw"] / by_country["events"]
                by_country["country"] = by_country["partner"].map(cname)
                fig = px.choropleth(by_country, locations="partner", color="goldstein", hover_name="country",
                                    color_continuous_scale=T["div"], range_color=(-6, 6),
                                    title="Average Goldstein with Iran", labels={"goldstein": "Avg Goldstein"})
                fig.update_geos(showframe=False, projection_type="natural earth")
                show(fig, height=440)
                why("Maps each country's average Goldstein score with Iran, so friendly and hostile partners stand out by region.")

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
    with columns[0]:
        fig = trend_area(series, "period", "iran_events", f"Iran-linked events per {gran}",
                         labels={"period": "", "iran_events": "Events"}, color_discrete_sequence=[T["a"]])
        show(fig)
        why("Counts Iran-linked events over time as the baseline for how much attention Iran gets.")
    with columns[1]:
        fig = trend_area(series, "period", "oil_events", f"Iran + oil-URL events per {gran}",
                         labels={"period": "", "oil_events": "Events"}, color_discrete_sequence=[T["b"]])
        show(fig)
        why("Counts the subset whose articles mention oil, showing when the oil angle surges.")
    with columns[2]:
        tone = series.melt(id_vars="period", value_vars=["tone_all", "tone_oil"], var_name="series", value_name="tone")
        tone["series"] = tone["series"].map({"tone_all": "All Iran-linked", "tone_oil": "Iran + oil URL"})
        fig = px.line(tone, x="period", y="tone", color="series", markers=True, color_discrete_sequence=[T["a"], T["b"]],
                      title="Average tone", labels={"period": "", "tone": "Average tone"})
        fig.add_hline(y=0, line_dash="dot", opacity=0.5)
        show(fig)
        why("Compares the tone of oil-related events with all Iran coverage, showing whether the oil angle is more negative.")

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
                     color="tone", color_continuous_scale=T["div"], color_continuous_midpoint=0,
                     title="Where Iran + oil-URL events took place (color = average tone)",
                     labels={"events": "Events", "country": "", "tone": "Avg tone"})
        round_bars(fig)
        show(fig, height=max(320, 26 * len(where_events) + 110))
        why("Shows where Iran-and-oil events take place and how negative they are, pointing to the nations most affected.")


# ---------------------------------------------------------------------- Q4. protests

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
    st.markdown("#### 4a · Average Goldstein score of protest events, by country")
    min_protests = 50  # countries need 50+ protest events to be ranked
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
        protests["violent_share"] = protests["violent_share"].fillna(0)
        left, right = st.columns(2)
        with left:
            low = protests.nsmallest(20, "goldstein")
            fig = px.bar(low.sort_values("goldstein", ascending=False), x="goldstein", y="country", orientation="h",
                         color="violent_share", color_continuous_scale=T["viol"],
                         title="20 lowest averages (lower = more conflictual protests)",
                         labels={"goldstein": "Avg Goldstein", "country": "", "violent_share": "Violent share"})
            fig.update_coloraxes(colorbar_tickformat=".0%")
            round_bars(fig)
            show(fig, height=max(320, 26 * len(low) + 110))
            why("Ranks countries by how conflictual their protest events are, which identifies where protests tend to be higher-impact events.")
        with right:
            fig = px.choropleth(protests, locations="country_code", color="goldstein", hover_name="country",
                                color_continuous_scale=T["div"], color_continuous_midpoint=float(protests["goldstein"].median()),
                                title="Average Goldstein of protest events", labels={"goldstein": "Avg Goldstein"})
            fig.update_geos(showframe=False, projection_type="natural earth")
            show(fig, height=max(320, 26 * 20 + 110))
            why("Maps the same averages so regional clusters of escalation stand out.")
        st.caption(
            "Goldstein scores are assigned per event code, so a country's average mostly reflects its mix of protest types: "
            "riots (code 145) score lower than rallies. 'Violent share' is the share of protest events coded as violent protest."
        )

        over_time = run_query(
            f"""
            SELECT e.actiongeo_countryiso3 AS country_code, date_trunc(:gran, e.sqldate::timestamp)::date AS period,
                   count(*) AS protests, avg(e.goldsteinscale) AS goldstein,
                   avg((left(e.eventcode, 3) = '145')::int::float8) AS violent_share
            FROM events e
            WHERE {where} AND e.isprotest IS TRUE AND e.actiongeo_countryiso3 IS NOT NULL
            GROUP BY 1, 2 ORDER BY 2
            """,
            {**params, "gran": gran},
        )
        top_codes = protests.nlargest(30, "protests")["country_code"]
        over_time = over_time[over_time["country_code"].isin(top_codes)].copy()
        if not empty(over_time):
            over_time["vw"] = over_time["violent_share"].fillna(0) * over_time["protests"]
            over_time["gw"] = over_time["goldstein"] * over_time["protests"]
            agg = over_time.groupby("country_code", as_index=False).agg(
                protests=("protests", "sum"), vw=("vw", "sum"), gw=("gw", "sum"))
            agg["goldstein"] = agg["gw"] / agg["protests"]
            agg["violent_share"] = agg["vw"] / agg["protests"]
            agg["country"] = agg["country_code"].map(cname)
            fig = px.scatter(
                agg, x="protests", y="goldstein", size="protests", color="violent_share", hover_name="country",
                size_max=48, color_continuous_scale=T["viol"],
                title="Protest volume vs. conflict level by country",
                labels={"protests": "Protest events (log scale)", "goldstein": "Avg Goldstein", "violent_share": "Violent share"},
            )
            fig.update_xaxes(type="log")
            fig.update_coloraxes(colorbar_tickformat=".0%")
            show(fig, height=480)
            why("Plots each country's protest volume against its conflict level, so countries low on the chart with a high violent share are the most escalated.")

    st.markdown("#### 4b · What follows protests: concessions, arrests or force?")
    lag, min_per_day = 2, 5  # a protest day = 5+ protest events; responses counted that day and the next 2
    st.markdown('<div class="settings">A protest day is a country-day with at least 5 protest events. '
                'Responses are counted on that day and the 2 days after.</div>', unsafe_allow_html=True)
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
        why("Shows which responses most often follow protests overall, answering whether concessions, arrests or force are most typical.")
    with right:
        biggest = result.drop_duplicates("country").nlargest(12, "protests")["country"]
        sel = result[result["country"].isin(biggest)]
        fig = px.bar(sel, x="name", y="per100", color="response", barmode="group", color_discrete_sequence=T["discrete"],
                     title="Responses per 100 protest events, 12 countries with the most protests",
                     labels={"per100": "Per 100 protest events", "name": ""})
        round_bars(fig)
        show(fig)
        why("Breaks those responses down for the countries with the most protests, showing how responses differ between countries.")

    st.caption(
        "Lift = share of events that are this response in the protest window ÷ the same share on other days. "
        "Above 1 means more of that response than normal. Same-country timing only: it does not prove a protest caused it."
    )
    heat = result[result["country"].isin(result.drop_duplicates("country").nlargest(20, "protests")["country"])]
    matrix = heat.pivot(index="name", columns="response", values="lift")
    fig = px.imshow(matrix, aspect="auto", color_continuous_scale=T["div"][::-1], color_continuous_midpoint=1,
                    zmin=0, zmax=2, text_auto=".1f", labels=dict(color="Lift"),
                    title="Is each response more common than usual after protests? (lift)")
    fig.update_xaxes(title=None, side="top")
    fig.update_yaxes(title=None)
    show(fig, height=max(380, 30 * len(matrix) + 150))
    why("Compares each response with normal days, so cells above 1 show where protests are followed by more of that response than usual, a sign of change or crackdown.")


{
    VIEWS[0]: view_religions_and_orgs,
    VIEWS[1]: view_religion_coverage,
    VIEWS[2]: view_religion_vs_rights,
    VIEWS[3]: view_iran,
    VIEWS[4]: view_protests,
}[view]()