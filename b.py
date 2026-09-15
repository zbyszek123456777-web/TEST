import streamlit as st
import pandas as pd
import pyodbc
import requests
import streamlit.components.v1 as components

from datetime import datetime, time


# --------------------------------------------------
# KONFIGURACJA STRONY
# --------------------------------------------------

st.set_page_config(layout="wide")


# --------------------------------------------------
# DSN
# --------------------------------------------------

DSNS = [
    "LOWVOLUME-kopia",
    "MEDICAL-kopia",
    "PCCI-kopia"
]


# --------------------------------------------------
# BACKFLUSH
# --------------------------------------------------

@st.cache_data(ttl=3600)
def load_backflush(dsn):

    conn = pyodbc.connect(f"DSN={dsn}")

    # Tabela vDISCR_LAST_NON_SKIDDED_BACKFLUSH_DETAILS
    # występuje w bazie PCCI-kopia.
    if dsn == "PCCI-kopia":

        backflush_source_sql = """
            SELECT
                CAST('BACKFLUSH' AS VARCHAR(50)) AS Source,
                [Timestamp],
                Panelnumber,
                Serialnumber,
                Reportingpoint,
                Reportingpartnumber
            FROM dbo.vLAST_NON_AUDIT_BACKFLUSH_DETAILS
            WHERE [Timestamp] >= DATEADD(YEAR, -1, GETDATE())

            UNION ALL

            SELECT
                CAST('NON_SKIDDED' AS VARCHAR(50)) AS Source,
                [Timestamp],
                Panelnumber,
                Serialnumber,
                ReportingPoint AS Reportingpoint,
                ReportingPartNumber AS Reportingpartnumber
            FROM dbo.vDISCR_LAST_NON_SKIDDED_BACKFLUSH_DETAILS
            WHERE [Timestamp] >= DATEADD(YEAR, -1, GETDATE())
              AND ScrapCodeId IS NULL
        """

    else:

        backflush_source_sql = """
            SELECT
                CAST('BACKFLUSH' AS VARCHAR(50)) AS Source,
                [Timestamp],
                Panelnumber,
                Serialnumber,
                Reportingpoint,
                Reportingpartnumber
            FROM dbo.vLAST_NON_AUDIT_BACKFLUSH_DETAILS
            WHERE [Timestamp] >= DATEADD(YEAR, -1, GETDATE())
        """

    sql = f"""
    WITH reg_latest AS
    (
        SELECT
            scannednumber,
            [timestamp],
            [description],

            ROW_NUMBER() OVER
            (
                PARTITION BY scannednumber
                ORDER BY [timestamp] DESC
            ) AS rn

        FROM dbo.vREG_OF_PROCESS

        WHERE [timestamp] >= DATEADD(YEAR, -1, GETDATE())
          AND (stepid IS NULL OR stepid <> 500)
    ),

    reg AS
    (
        SELECT
            scannednumber,
            [timestamp],
            [description]

        FROM reg_latest

        WHERE rn = 1
    ),

    bf AS
    (
        {backflush_source_sql}
    )

    SELECT
        bf.Source,
        bf.[Timestamp],
        bf.Panelnumber,
        bf.Serialnumber,
        bf.Reportingpoint,
        bf.Reportingpartnumber,

        CASE
            WHEN p.[timestamp] IS NULL
                THEN s.[timestamp]

            WHEN s.[timestamp] IS NULL
                THEN p.[timestamp]

            WHEN s.[timestamp] >= p.[timestamp]
                THEN s.[timestamp]

            ELSE p.[timestamp]
        END AS REG_timestamp,

        CASE
            WHEN p.[timestamp] IS NULL
                THEN s.[description]

            WHEN s.[timestamp] IS NULL
                THEN p.[description]

            WHEN s.[timestamp] >= p.[timestamp]
                THEN s.[description]

            ELSE p.[description]
        END AS REG_description

    FROM bf

    LEFT JOIN reg s
        ON s.scannednumber = LTRIM(RTRIM(bf.Serialnumber))

    LEFT JOIN reg p
        ON p.scannednumber = LTRIM(RTRIM(bf.Panelnumber))
    """

    try:
        df = pd.read_sql(
            sql,
            conn,
            parse_dates=[
                "Timestamp",
                "REG_timestamp"
            ]
        )

    finally:
        conn.close()

    # Usunięcie spacji z numerów seryjnych i paneli
    df["Serialnumber"] = (
        df["Serialnumber"]
        .astype("string")
        .str.strip()
    )

    df["Panelnumber"] = (
        df["Panelnumber"]
        .astype("string")
        .str.strip()
    )

    # Informacja, z którego DSN pochodzi rekord
    df["DSN"] = dsn

    return df
# --------------------------------------------------
# REG OF PROCESS
# --------------------------------------------------

@st.cache_data(ttl=3600)
def load_reg(dsn):

    conn = pyodbc.connect(f"DSN={dsn}")

    sql = """
        WITH cte AS
        (
            SELECT
                timestamp,
                description,
                scannednumber,
                ROW_NUMBER() OVER
                (
                    PARTITION BY scannednumber
                    ORDER BY timestamp DESC
                ) rn
            FROM dbo.vREG_OF_PROCESS
            WHERE timestamp >= DATEADD(YEAR,-1,GETDATE())
              AND (stepid IS NULL OR stepid <> 500)
        )
        SELECT
            timestamp,
            description,
            scannednumber
        FROM cte
        WHERE rn = 1
    """

    df = pd.read_sql(
        sql,
        conn,
        parse_dates=["timestamp"]
    )

    conn.close()

    df["scannednumber"] = (
        df["scannednumber"]
        .astype("string")
        .str.strip()
    )

    df["DSN"] = dsn

    return df


@st.cache_data(ttl=1800)
def load_weather():

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=52.4064"
        "&longitude=16.9252"
        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max"
        "&timezone=Europe/Warsaw"
        "&forecast_days=7"
    )

    response = requests.get(url, timeout=10)
    response.raise_for_status()

    return response.json()


def weather_icon(code):

    if code == 0:
        return "☀️"
    elif code in [1, 2, 3]:
        return "⛅"
    elif code in [45, 48]:
        return "🌫️"
    elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
        return "🌧️"
    elif code in [71, 73, 75, 77, 85, 86]:
        return "❄️"
    elif code in [95, 96, 99]:
        return "⛈️"

    return "🌤️"

# --------------------------------------------------
# ALL
# --------------------------------------------------
if "all_df" not in st.session_state:

    with st.spinner("Pobieranie danych produkcyjnych..."):

        st.session_state.all_df = pd.concat(
            [load_backflush(dsn) for dsn in DSNS],
            ignore_index=True
        )

all_df = st.session_state.all_df

# --------------------------------------------------
# DW
# --------------------------------------------------

dw_conn = pyodbc.connect(
    "DRIVER={SQL Server};"
    "SERVER=nts407;"
    "DATABASE=DW;"
    "Trusted_Connection=yes;"
)

# --------------------------------------------------
# KWZ GENERAL LIST
# --------------------------------------------------

kwz_general = pd.read_sql("""
    SELECT
        kwz,
        etap
    FROM dbo.kwzReportGeneralList
""", dw_conn)

kwz_general = kwz_general[
    kwz_general["etap"].fillna("").ne("")
]

kwz_general = kwz_general[
    kwz_general["etap"] != "Zamknięty"
]

lista_kwz = kwz_general["kwz"].unique()

# --------------------------------------------------
# KWZ SERIAL ARCHIVE
# --------------------------------------------------

kwz_serials = pd.read_sql("""
    SELECT
        kwz,
        SerialArchiveValue
    FROM dbo.kwzReportSerialsArchive
""", dw_conn)

kwz_serials = kwz_serials[
    kwz_serials["kwz"].isin(lista_kwz)
]

kwz_serials["SerialArchiveValue"] = (
    kwz_serials["SerialArchiveValue"]
    .astype(str)
    .str.strip()
)

serial_map = (
    kwz_serials
    .dropna(subset=["SerialArchiveValue"])
    .set_index("SerialArchiveValue")["kwz"]
    .to_dict()
)

# --------------------------------------------------
# KWZ / PRODUKCJA
# --------------------------------------------------

all_df["KWZ_PRODUKCJA"] = (
    all_df["Serialnumber"]
    .map(serial_map)
    .fillna(
        all_df["Panelnumber"].map(serial_map)
    )
    .fillna("produkcja")
)

# --------------------------------------------------
# SŁOWNIK
# --------------------------------------------------

@st.cache_data(ttl=3600)
def load_dictionary():

    return pd.read_excel(
        r"M:\Production\3_OSOBISTE.___\Słownik.xlsx",
        sheet_name="OPISY"
    )


if "slownik" not in st.session_state:
    st.session_state.slownik = load_dictionary()

slownik = st.session_state.slownik

all_df["SCALONE"] = (
    all_df["Reportingpartnumber"].astype(str)
    +
    all_df["Reportingpoint"].astype(str)
)

if "SCALONE" in slownik.columns:

    all_df = all_df.merge(
        slownik,
        on="SCALONE",
        how="left"
    )


# --------------------------------------------------
# LICZNIK KOŃCA ZMIANY
# --------------------------------------------------

now = datetime.now()

zmiany = [
    ("I zmiana", 6, 14),
    ("II zmiana", 14, 22),
    ("III zmiana", 22, 6),
]

aktualna_zmiana = ""
koniec = None

godzina = now.hour

if 6 <= godzina < 14:
    aktualna_zmiana = "I zmiana"
    koniec = now.replace(
        hour=14,
        minute=0,
        second=0,
        microsecond=0
    )

elif 14 <= godzina < 22:
    aktualna_zmiana = "II zmiana"
    koniec = now.replace(
        hour=22,
        minute=0,
        second=0,
        microsecond=0
    )

else:
    aktualna_zmiana = "III zmiana"

    if godzina >= 22:
        koniec = now.replace(
            hour=6,
            minute=0,
            second=0,
            microsecond=0
        ) + pd.Timedelta(days=1)
    else:
        koniec = now.replace(
            hour=6,
            minute=0,
            second=0,
            microsecond=0
        )

pozostalo = koniec - now

godziny = int(
    pozostalo.total_seconds() // 3600
)

minuty = int(
    (pozostalo.total_seconds() % 3600) // 60
)

components.html(
    f"""
    <div id="shift-box" style="
        background: linear-gradient(135deg,#0078D4,#00BFFF);
        color:white;
        padding:8px;
        border-radius:20px;
        box-shadow:0px 4px 10px rgba(0,0,0,0.3);
        text-align:center;
        font-family:Arial;
    ">
        <div style="
            display:flex
            justify-content:space-between;
            align-items:center;
            font-size:20px;
            font-weight:bold;
         
        ">
            <span>🏭 {aktualna_zmiana}
            <span id="timer">00:00:00</span>
            <span>do końca zmiany</span>
        </div>

            </div>

    <script>

    function updateTimer() {{

        let end = new Date("{koniec.strftime('%Y-%m-%d %H:%M:%S')}");

        let now = new Date();

        let diff = end - now;

        if(diff < 0)
            diff = 0;

        let hours =
            Math.floor(diff / (1000 * 60 * 60));

        let minutes =
            Math.floor(
                (diff % (1000 * 60 * 60))
                / (1000 * 60)
            );

        let seconds =
            Math.floor(
                (diff % (1000 * 60))
                / 1000
            );

        document.getElementById("timer").innerHTML =
            String(hours).padStart(2,'0')
            + ":"
            + String(minutes).padStart(2,'0')
            + ":"
            + String(seconds).padStart(2,'0');
    }}

    updateTimer();

    setInterval(
        updateTimer,
        1000
    );

    </script>
    """,
    height=150
)



# --------------------------------------------------
# REG OF PROCESS
# --------------------------------------------------

if "reg_df" not in st.session_state:

    with st.spinner("Pobieranie REG OF PROCESS..."):

        st.session_state.reg_df = pd.concat(
            [load_reg(dsn) for dsn in DSNS],
            ignore_index=True
        )

reg = st.session_state.reg_df

reg["scannednumber"] = (
    reg["scannednumber"]
    .astype(str)
    .str.strip()
)

reg["timestamp"] = pd.to_datetime(
    reg["timestamp"],
    errors="coerce"
)


status_df = pd.DataFrame([
    {
        "DSN": dsn,
        "Ostatni Backflush":
            all_df.loc[
                all_df["DSN"] == dsn,
                "Timestamp"
            ].max(),
        "Ostatni REG OF PROCESS":
            reg.loc[
                reg["DSN"] == dsn,
                "timestamp"
            ].max()
    }
    for dsn in DSNS
])

st.dataframe(
    status_df,
    width="content",
    hide_index=True
)

dw_conn.close()
from streamlit_autorefresh import st_autorefresh


st.sidebar.header("Dane")




if st.sidebar.button("🔄 Odswiez dane"):

    st.cache_data.clear()

    for key in [
        "all_df",
        "reg_df",
        "slownik",
        "final_df"
    ]:
        st.session_state.pop(key, None)

    st.session_state.pop("projekt", None)
    st.session_state.pop("grupa", None)

    st.rerun()

refresh_count = st_autorefresh(
    interval=600000,
    key="data_refresh"
)

if "last_refresh_count" not in st.session_state:
    st.session_state.last_refresh_count = refresh_count

if refresh_count > st.session_state.last_refresh_count:

    st.cache_data.clear()

    for key in [
        "all_df",
        "reg_df",
        "slownik",
        "final_df"
    ]:
        st.session_state.pop(key, None)

    st.session_state.last_refresh_count = refresh_count





# --------------------------------------------------
# POGODA
# --------------------------------------------------

@st.cache_data(ttl=1800)
def load_weather():

    url = (
        "https://api.open-meteo.com/v1/forecast"
        "?latitude=52.4064"
        "&longitude=16.9252"
        "&current="
        "temperature_2m,"
        "relative_humidity_2m,"
        "weather_code,"
        "wind_speed_10m"
        "&daily="
        "weather_code,"
        "temperature_2m_max,"
        "temperature_2m_min,"
        "sunrise,"
        "sunset,"
        "precipitation_probability_max"
        "&timezone=Europe/Warsaw"
        "&forecast_days=7"
    )

    return requests.get(url, timeout=10).json()


def weather_icon(code):

    if code == 0:
        return "☀️"

    if code in [1, 2, 3]:
        return "⛅"

    if code in [45, 48]:
        return "🌫️"

    if code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
        return "🌧️"

    if code in [71, 73, 75, 77, 85, 86]:
        return "❄️"

    if code in [95, 96, 99]:
        return "⛈️"

    return "🌤️"






# --------------------------------------------------
# FILTRY
# --------------------------------------------------


st.sidebar.header("Filtry")

df_temp = all_df.copy()

if "PROJEKT" in df_temp.columns:

    projekt_lista = ["Wszystkie"] + sorted(
        df_temp["PROJEKT"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    wybrany_projekt = st.sidebar.selectbox(
        "PROJEKT",
        projekt_lista,
        key="projekt"
    )

    if wybrany_projekt != "Wszystkie":

        df_temp = df_temp[
            df_temp["PROJEKT"].astype(str)
            == wybrany_projekt
        ]

if "OPISY.GRUPA" in df_temp.columns:

    grupa_lista = ["Wszystkie"] + sorted(
        df_temp["OPISY.GRUPA"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    wybrana_grupa = st.sidebar.selectbox(
        "OPISY.GRUPA",
        grupa_lista,
        key="grupa"
    )

    if wybrana_grupa != "Wszystkie":

        df_temp = df_temp[
            df_temp["OPISY.GRUPA"]
            .astype(str)
            == wybrana_grupa
        ]

all_df_filtr = df_temp.copy()


# --------------------------------------------------
# PODSUMOWANIE SIDEBAR
# --------------------------------------------------

ilosc_produkcja = (
    all_df_filtr["KWZ_PRODUKCJA"]
    .eq("produkcja")
    .sum()
)

ilosc_kwz = (
    all_df_filtr["KWZ_PRODUKCJA"]
    .ne("produkcja")
    .sum()
)

st.sidebar.markdown("---")
st.sidebar.subheader("Podsumowanie")

col1, col2 = st.sidebar.columns(2)

with col1:
    st.metric("Produkcja", ilosc_produkcja)

with col2:
    st.metric("KWZ", ilosc_kwz)

st.sidebar.metric("Razem", len(all_df_filtr))

# --------------------------------------------------
# DO ŚWIĄT BOŻEGO NARODZENIA
# --------------------------------------------------

today = datetime.now()

swieta = datetime(
    today.year,
    12,
    25
)

if today.date() > swieta.date():
    swieta = datetime(
        today.year + 1,
        12,
        25
    )

pozostalo = swieta - today

dni = pozostalo.days

st.sidebar.markdown("---")

st.sidebar.markdown(
    f"""
    <div style="
        background:#B22222;
        color:white;
        padding:5px;
        border-radius:10px;
        text-align:center;
    ">
        <h2>🎄 Do Świąt</h2>
        <h1>{dni}</h1>
        <p>dni</p>
    </div>
    """,
    unsafe_allow_html=True
)





try:

    weather = load_weather()

    current = weather["current"]
    daily = weather["daily"]

    icon = weather_icon(
        current["weather_code"]
    )

    st.sidebar.markdown("---")
    st.sidebar.subheader("🌤️ Poznań")

    st.sidebar.markdown(
        f"""
        <div style="
        background:#0084ff;
        color:white;
        padding:5px;
        border-radius:5px;
        text-align:center;">
            <h1>{icon}</h1>
            <h2>{current['temperature_2m']}°C</h2>
        </div>
        """,
        unsafe_allow_html=True
    )

    st.sidebar.metric(
        "💨 Wiatr",
        f"{current['wind_speed_10m']} km/h"
    )

    st.sidebar.metric(
        "💧 Wilgotność",
        f"{current['relative_humidity_2m']}%"
    )

    sunrise = pd.to_datetime(
        daily["sunrise"][0]
    ).strftime("%H:%M")

    sunset = pd.to_datetime(
        daily["sunset"][0]
    ).strftime("%H:%M")

    st.sidebar.info(
        f"🌅 {sunrise}\n🌇 {sunset}"
    )

    rain = daily[
        "precipitation_probability_max"
    ][0]

    if rain >= 70:
        st.sidebar.error(
            f"🌧️ Ostrzeżenie: opady {rain}%"
        )

    elif rain >= 40:
        st.sidebar.warning(
            f"☔ Możliwe opady {rain}%"
        )

    prognoza = pd.DataFrame({
        "Dzień": daily["time"],
        "🌤️": [
            weather_icon(x)
            for x in daily["weather_code"]
        ],
        "Max": daily["temperature_2m_max"],
        "Min": daily["temperature_2m_min"]
    })

    st.sidebar.dataframe(
        prognoza,
        hide_index=True
    )

except Exception as e:
    st.sidebar.warning(
        f"Błąd pogody: {e}"
    )



## --------------------------------------------------
# WIDOK
# --------------------------------------------------

kolumny_widok = [
    "OPISY.GRUPA",
    "KROKI",
    "KWZ_PRODUKCJA",
    "REG_timestamp",
    "REG_description",
    "Serialnumber",
    "Panelnumber"
]

kolumny_widok = [
    col
    for col in kolumny_widok
    if col in all_df_filtr.columns
]


slownik_filtr = slownik.copy()

if wybrany_projekt != "Wszystkie":
    slownik_filtr = slownik_filtr[
        slownik_filtr["PROJEKT"].astype(str)
        == wybrany_projekt
    ]

if wybrana_grupa != "Wszystkie":
    slownik_filtr = slownik_filtr[
        slownik_filtr["OPISY.GRUPA"].astype(str)
        == wybrana_grupa
    ]

tab1, tab2, tab3 = st.tabs([
    "Dane",
    "Podsumowanie",
    "Kolejny krok"
])

with tab1:

    st.metric(
        "Liczba rekordów",
        f"{len(all_df_filtr):,}"
    )

    st.dataframe(
        all_df_filtr[kolumny_widok],
        use_container_width=True,
        hide_index=True,
        height=900
    )

    csv = all_df_filtr.to_csv(
        index=False,
        sep=";"
    ).encode("utf-8-sig")

    st.download_button(
        "📥 Pobierz CSV",
        data=csv,
        file_name="ALL.csv",
        mime="text/csv"
    )


with tab2:

    wszystkie_kroki = (
        slownik_filtr[
            ["OPISY.GRUPA", "KROKI"]
        ]
        .drop_duplicates()
    )

    podsumowanie = (
        all_df_filtr
        .groupby(
            [
                "OPISY.GRUPA",
                "KROKI",
                "REG_description"
            ],
            dropna=False
        )
        .agg(
            Produkcja=(
                "KWZ_PRODUKCJA",
                lambda x: (x == "produkcja").sum()
            ),
            KWZ=(
                "KWZ_PRODUKCJA",
                lambda x: (x != "produkcja").sum()
            )
        )
        .reset_index()
    )

    podsumowanie = wszystkie_kroki.merge(
        podsumowanie,
        on=["OPISY.GRUPA", "KROKI"],
        how="left"
    )

    podsumowanie["Produkcja"] = (
        podsumowanie["Produkcja"]
        .fillna(0)
        .astype("Int64")
    )

    podsumowanie["KWZ"] = (
        podsumowanie["KWZ"]
        .fillna(0)
        .astype("Int64")
    )

    podsumowanie["REG_description"] = (
        podsumowanie["REG_description"]
        .fillna("")
    )

    # --------------------------------------------------
    # NUMERY KWZ JAKO KOLUMNY
    # --------------------------------------------------

    all_df_kwz = all_df_filtr[
        all_df_filtr["KWZ_PRODUKCJA"] != "produkcja"
    ].copy()

    kwz_numery = (
        all_df_kwz
        .groupby(
            [
                "OPISY.GRUPA",
                "KROKI",
                "REG_description",
                "KWZ_PRODUKCJA"
            ],
            dropna=False
        )
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    podsumowanie = podsumowanie.merge(
        kwz_numery,
        on=[
            "OPISY.GRUPA",
            "KROKI",
            "REG_description"
        ],
        how="left"
    )

    podsumowanie = podsumowanie.fillna(0)

    # --------------------------------------------------
    # KONWERSJA KOLUMN KWZ-* DO INT
    # --------------------------------------------------

    kol_kwz = sorted(
        [
            c
            for c in podsumowanie.columns
            if str(c).startswith("KWZ-")
        ],
        reverse=True
    )

    for col in kol_kwz:
        podsumowanie[col] = (
            pd.to_numeric(
                podsumowanie[col],
                errors="coerce"
            )
            .fillna(0)
            .astype("Int64")
        )

    # --------------------------------------------------
    # KOLEJNOŚĆ KOLUMN
    # --------------------------------------------------

    podsumowanie = podsumowanie[
        [
            "OPISY.GRUPA",
            "KROKI",
            "Produkcja",
            "KWZ"
        ]
        + ["REG_description"]
        + kol_kwz
    ]

    podsumowanie = podsumowanie.sort_values(
        ["OPISY.GRUPA", "KROKI"]
    )

    # --------------------------------------------------
    # ZERA JAKO PUSTE
    # --------------------------------------------------

    podsumowanie_widok = podsumowanie.copy()

    kolumny_liczbowe = [
        c
        for c in podsumowanie_widok.columns
        if c not in [
            "OPISY.GRUPA",
            "KROKI",
            "REG_description"
        ]
    ]

    for col in kolumny_liczbowe:
        podsumowanie_widok[col] = (
            podsumowanie_widok[col]
            .astype(object)
            .apply(
                lambda x: "" if x == 0 else x
            )
        )

    st.dataframe(
        podsumowanie_widok,
        width="content",
        hide_index=True,
        height=1400
    )

with tab3:

    df_krok = all_df.copy()

    # domyślne wartości filtrów

    if "kolejny_krok" not in st.session_state:
        st.session_state.kolejny_krok = "Wszystkie"

    if "projekt_krok" not in st.session_state:
        st.session_state.projekt_krok = "Wszystkie"

    if "grupa_krok" not in st.session_state:
        st.session_state.grupa_krok = "Wszystkie"

    if "typ_krok" not in st.session_state:
        st.session_state.typ_krok = "Produkcja"

    col1, col2, col3, col4 = st.columns(4)

    with col1:

        lista_kolejny_krok = ["Wszystkie"] + sorted(
            df_krok["KOLEJNY KROK"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        wybrany_kolejny_krok = st.selectbox(
            "KOLEJNY KROK",
            lista_kolejny_krok,
            key="kolejny_krok"
        )

    df_projekt = df_krok.copy()

    if wybrany_kolejny_krok != "Wszystkie":
        df_projekt = df_projekt[
            df_projekt["KOLEJNY KROK"].astype(str)
            == wybrany_kolejny_krok
        ]

    with col2:

        lista_projekt = ["Wszystkie"] + sorted(
            df_projekt["PROJEKT"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        wybrany_projekt_krok = st.selectbox(
            "PROJEKT",
            lista_projekt,
            key="projekt_krok"
        )

    df_grupa = df_projekt.copy()

    if wybrany_projekt_krok != "Wszystkie":
        df_grupa = df_grupa[
            df_grupa["PROJEKT"].astype(str)
            == wybrany_projekt_krok
        ]

    with col3:

        lista_grupa = ["Wszystkie"] + sorted(
            df_grupa["OPISY.GRUPA"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )

        wybrana_grupa_krok = st.selectbox(
            "OPISY.GRUPA",
            lista_grupa,
            key="grupa_krok"
        )

    with col4:

        wybrany_typ = st.selectbox(
            "Typ",
            [
                "Produkcja",
                "KWZ",
                "Wszystkie"
            ],
            index=0,
            key="typ_krok"
        )

    if st.button(
        "🔄 Reset filtrów Tab3",
        key="reset_tab3"
    ):

        st.session_state.pop("kolejny_krok", None)
        st.session_state.pop("projekt_krok", None)
        st.session_state.pop("grupa_krok", None)
        st.session_state.pop("typ_krok", None)

        st.rerun()

    # FILTRY

    if wybrany_kolejny_krok != "Wszystkie":
        df_krok = df_krok[
            df_krok["KOLEJNY KROK"].astype(str)
            == wybrany_kolejny_krok
        ]

    if wybrany_projekt_krok != "Wszystkie":
        df_krok = df_krok[
            df_krok["PROJEKT"].astype(str)
            == wybrany_projekt_krok
        ]

    if wybrana_grupa_krok != "Wszystkie":
        df_krok = df_krok[
            df_krok["OPISY.GRUPA"].astype(str)
            == wybrana_grupa_krok
        ]

    if wybrany_typ == "Produkcja":
        df_krok = df_krok[
            df_krok["KWZ_PRODUKCJA"] == "produkcja"
        ]

    elif wybrany_typ == "KWZ":
        df_krok = df_krok[
            df_krok["KWZ_PRODUKCJA"] != "produkcja"
        ]

    df_krok["MIESIAC"] = (
        pd.to_datetime(df_krok["Timestamp"])
        .dt.strftime("%Y-%m")
    )

    miesiace = sorted(
        df_krok["MIESIAC"]
        .dropna()
        .unique(),
        reverse=True
    )

    pivot = (
        df_krok
        .groupby(
            [
                "PROJEKT",
                "OPISY.GRUPA",
                "KOLEJNY KROK",
                "REG_description",
                "MIESIAC"
            ],
            dropna=False
        )
        .size()
        .unstack(fill_value=0)
        .reset_index()
    )

    statystyki = (
        df_krok
        .groupby(
            [
                "PROJEKT",
                "OPISY.GRUPA",
                "KOLEJNY KROK",
                "REG_description"
            ],
            dropna=False
        )
        .agg(
            Produkcja=(
                "KWZ_PRODUKCJA",
                lambda x: (x == "produkcja").sum()
            ),
            KWZ=(
                "KWZ_PRODUKCJA",
                lambda x: (x != "produkcja").sum()
            )
        )
        .reset_index()
    )

    wynik = statystyki.merge(
        pivot,
        on=[
            "PROJEKT",
            "OPISY.GRUPA",
            "KOLEJNY KROK",
            "REG_description"
        ],
        how="left"
    )

    wynik = wynik.fillna(0)

    wynik = wynik[
        [
            "PROJEKT",
            "OPISY.GRUPA",
            "KOLEJNY KROK",
            "REG_description",
            "Produkcja",
            "KWZ"
        ] + miesiace
    ]

    wynik = wynik.sort_values(
        by=["OPISY.GRUPA", "Produkcja"],
        ascending=[True, False]
    )

    wynik_widok = wynik.copy()

    kolumny_liczbowe = [
        c for c in wynik_widok.columns
        if c not in [
            "PROJEKT",
            "OPISY.GRUPA",
            "KOLEJNY KROK",
            "REG_description"
        ]
    ]

    for col in kolumny_liczbowe:
        wynik_widok[col] = wynik_widok[col].apply(
            lambda x: "" if x == 0 else x
        )

    st.dataframe(
        wynik_widok,
        width="content",
        hide_index=True,
        height=2000
    )

    csv = wynik.to_csv(
        index=False,
        sep=";"
    ).encode("utf-8-sig")

    st.download_button(
        "📥 Pobierz CSV",
        data=csv,
        file_name="Kolejny_Krok.csv",
        mime="text/csv"
    )