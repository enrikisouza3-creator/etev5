# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import matplotlib.pyplot as plt
import io, requests, re, time, itertools
from matplotlib.ticker import FuncFormatter

# =========================
# CONFIGURAÇÃO DA PÁGINA
# =========================
st.set_page_config(page_title="Dashboard Operacional ETE", layout="wide")

# =========================
# GOOGLE SHEETS – ABA 1 (Respostas ao Formulário / Operacional)
# =========================
SHEET_ID = "1Gv0jhdQLaGkzuzDXWNkD0GD5OMM84Q_zkOkQHGBhLjU"
GID_FORM = "1283870792"
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID_FORM}"

df = pd.read_csv(CSV_URL)
df.columns = [str(c).strip() for c in df.columns]

# =========================
# NORMALIZAÇÃO / AUXILIARES
# =========================
def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _slug(s: str) -> str:
    return _strip_accents(str(s).lower()).replace(" ", "-").replace("–", "-").replace("/", "-")

cols_lower_noacc = [_strip_accents(c.lower()) for c in df.columns]
COLMAP = dict(zip(cols_lower_noacc, df.columns))

KW_CACAMBA   = ["cacamba", "caçamba"]
KW_NITR      = ["nitrificacao", "nitrificação", "nitrificac"]
KW_MBBR      = ["mbbr"]
KW_VALVULA   = ["valvula", "válvula"]
KW_SOPRADOR  = ["soprador"]
KW_OXIG      = ["oxigenacao", "oxigenação"]

KW_NIVEIS_OUTROS = ["nivel", "nível"]
KW_VAZAO         = ["vazao", "vazão"]
KW_PH            = ["ph ", " ph", "ph-", "ph_"]
KW_SST           = ["sst ", " sst", "ss "]
KW_DQO           = ["dqo ", " dqo"]
KW_ESTADOS       = ["decanter", "desvio", "tempo de desc", "volante"]

KW_EXCLUDE_GENERIC = KW_SST + KW_DQO + KW_PH + KW_VAZAO + KW_NIVEIS_OUTROS + KW_CACAMBA

def to_float_ptbr(x):
    if isinstance(x, pd.Series):
        xx = x.dropna()
        x = xx.iloc[-1] if not xx.empty else np.nan
    elif isinstance(x, pd.DataFrame):
        xx = x.stack().dropna()
        x = xx.iloc[-1] if not xx.empty else np.nan
    elif isinstance(x, (list, tuple, np.ndarray)):
        x = x[-1] if len(x) else np.nan
    if pd.isna(x):
        return np.nan
    s = str(x).strip().replace("%", "")
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan

def last_valid_raw(df_local, col):
    obj = df_local[col]
    if isinstance(obj, pd.DataFrame):
        s = obj.iloc[:, -1]
    else:
        s = obj
    s = s.replace(r"^\s*$", np.nan, regex=True)
    valid = s.dropna()
    if valid.empty:
        return None
    return valid.iloc[-1]

def _filter_columns_by_keywords(all_cols_norm_noacc, keywords):
    kws = [_strip_accents(k.lower()) for k in keywords]
    selected_norm = []
    for c_norm in all_cols_norm_noacc:
        if any(k in c_norm for k in kws):
            selected_norm.append(c_norm)
    return [COLMAP[c] for c in selected_norm]

def _extract_number(base: str) -> str:
    return "".join(ch for ch in base if ch.isdigit())

def _remove_brackets(text: str) -> str:
    return text.split("[", 1)[0].strip()

def _units_from_label(label: str) -> str:
    s = _strip_accents(label.lower())
    if "m3/h" in s or "m³/h" in label.lower():
        return " m³/h"
    if "mg/l" in s:
        return " mg/L"
    if "(%)" in label or "%" in label:
        return "%"
    return ""

def _filter_cols_intersection(all_cols_norm_noacc, must_any_1, must_any_2, forbid_any=None):
    kws1 = [_strip_accents(k.lower()) for k in must_any_1]
    kws2 = [_strip_accents(k.lower()) for k in must_any_2]
    forb = [_strip_accents(k.lower()) for k in (forbid_any or [])]
    selected_norm = []
    for c_norm in all_cols_norm_noacc:
        has1 = any(k in c_norm for k in kws1)
        has2 = any(k in c_norm for k in kws2)
        has_forb = any(k in c_norm for k in forb)
        if has1 and has2 and not has_forb:
            selected_norm.append(c_norm)
    return [COLMAP[c] for c in selected_norm]

# =========================
# PARÂMETROS DO SEMÁFORO (Sidebar)
# =========================
with st.sidebar.expander("⚙️ Parâmetros do Semáforo", expanded=True):
    st.caption("Ajuste os limites; os valores abaixo são padrões comuns e podem ser adaptados.")
    st.markdown("**Oxigenação (mg/L)**")
    do_ok_min_nitr = st.number_input("Nitrificação – DO mínimo (verde)", value=2.0, step=0.1)
    do_ok_max_nitr = st.number_input("Nitrificação – DO máximo (verde)", value=3.0, step=0.1)
    do_warn_low_nitr  = st.number_input("Nitrificação – abaixo disso é VERMELHO", value=1.0, step=0.1)
    do_warn_high_nitr = st.number_input("Nitrificação – acima disso é VERMELHO", value=4.0, step=0.1)
    do_ok_min_mbbr = st.number_input("MBBR – DO mínimo (verde)", value=2.0, step=0.1)
    do_ok_max_mbbr = st.number_input("MBBR – DO máximo (verde)", value=3.0, step=0.1)
    do_warn_low_mbbr  = st.number_input("MBBR – abaixo disso é VERMELHO", value=1.0, step=0.1)
    do_warn_high_mbbr = st.number_input("MBBR – acima disso é VERMELHO", value=4.0, step=0.1)
    st.markdown("---")
    st.markdown("**pH**")
    ph_ok_min_general = st.number_input("pH geral – mínimo (verde)", value=6.5, step=0.1)
    ph_ok_max_general = st.number_input("pH geral – máximo (verde)", value=8.5, step=0.1)
    ph_warn_low_general  = st.number_input("pH geral – abaixo disso é VERMELHO", value=6.0, step=0.1)
    ph_warn_high_general = st.number_input("pH geral – acima disso é VERMELHO", value=9.0, step=0.1)
    ph_ok_min_mab = st.number_input("pH MAB – mínimo (verde)", value=4.5, step=0.1)
    ph_ok_max_mab = st.number_input("pH MAB – máximo (verde)", value=6.5, step=0.1)
    ph_warn_low_mab  = st.number_input("pH MAB – abaixo disso é VERMELHO", value=4.0, step=0.1)
    ph_warn_high_mab = st.number_input("pH MAB – acima disso é VERMELHO", value=7.0, step=0.1)
    st.markdown("---")
    st.markdown("**Efluente – limites (Saída)**")
    sst_green_max = st.number_input("SST Saída – Máximo (verde) [mg/L]", value=30.0, step=1.0)
    sst_orange_max = st.number_input("SST Saída – Máximo (laranja) [mg/L]", value=50.0, step=1.0)
    dqo_green_max = st.number_input("DQO Saída – Máximo (verde) [mg/L]", value=150.0, step=10.0)
    dqo_orange_max = st.number_input("DQO Saída – Máximo (laranja) [mg/L]", value=300.0, step=10.0)

SEMAFORO_CFG = {
    "do": {
        "nitr": {"ok_min": do_ok_min_nitr, "ok_max": do_ok_max_nitr,
                 "red_low": do_warn_low_nitr, "red_high": do_warn_high_nitr},
        "mbbr": {"ok_min": do_ok_min_mbbr, "ok_max": do_ok_max_mbbr,
                 "red_low": do_warn_low_mbbr, "red_high": do_warn_high_mbbr},
    },
    "ph": {
        "general": {"ok_min": ph_ok_min_general, "ok_max": ph_ok_max_general,
                    "red_low": ph_warn_low_general, "red_high": ph_warn_high_general},
        "mab": {"ok_min": ph_ok_min_mab, "ok_max": ph_ok_max_mab,
                "red_low": ph_warn_low_mab, "red_high": ph_warn_high_mab},
    },
    "sst_saida": {"green_max": sst_green_max, "orange_max": sst_orange_max},
    "dqo_saida": {"green_max": dqo_green_max, "orange_max": dqo_orange_max},
}

# =========================
# CONTROLES VISUAIS DOS RÓTULOS (Sidebar)
# =========================
with st.sidebar.expander("📝 Rótulos das Cartas (visual)", expanded=False):
    cc_lbl_max_points = st.slider("Máximo de rótulos por carta", min_value=0, max_value=60, value=20, step=2)
    cc_lbl_out_of_control = st.checkbox("Rotular pontos fora de controle (LSC/LIC)", value=True)
    cc_lbl_local_extremes = st.checkbox("Rotular extremos locais (máx/mín)", value=True)
    cc_lbl_show_first_last = st.checkbox("Rotular 1º e último ponto", value=True)
    cc_lbl_compact_format = st.checkbox("Formatação compacta (mil/mi)", value=True)
    cc_lbl_fontsize = st.slider("Tamanho da fonte do rótulo", min_value=6, max_value=14, value=8)
    cc_lbl_angle = st.slider("Ângulo do rótulo (graus)", min_value=-90, max_value=90, value=0)
    cc_lbl_bbox = st.checkbox("Fundo no rótulo (melhora leitura)", value=True)

# =========================
# PADRONIZAÇÃO DE NOMES (TÍTULOS)
# =========================

def re_replace_case_insensitive(s, pattern, repl):
    return re.sub(pattern, repl, s, flags=re.IGNORECASE)


def _nome_exibicao(label_original: str) -> str:
    base_clean = _remove_brackets(label_original)
    base = _strip_accents(base_clean.lower()).strip()
    num = _extract_number(base)

    if "cacamba" in base:
        return f"Nível da caçamba {num}" if num else "Nível da caçamba"

    if "oxigenacao" in base:
        if any(k in base for k in KW_NITR):
            return f"Oxigenação Nitrificação {num}".strip()
        if any(k in base for k in KW_MBBR):
            return f"Oxigenação MBBR {num}".strip()
        return f"Oxigenação {num}".strip()

    if "soprador" in base:
        if any(k in base for k in KW_NITR):
            return f"Soprador de Nitrificação {num}" if num else "Soprador de Nitrificação"
        if any(k in base for k in KW_MBBR):
            return f"Soprador de MBBR {num}" if num else "Soprador de MBBR"
        return f"Soprador {num}" if num else "Soprador"

    if "valvula" in base:
        if any(k in base for k in KW_NITR):
            return f"Válvula de Nitrificação {num}" if num else "Válvula de Nitrificação"
        if any(k in base for k in KW_MBBR):
            return f"Válvula de MBBR {num}" if num else "Válvula de MBBR"
        return f"Válvula {num}" if num else "Válvula"

    txt = base_clean
    replacements = {
        "ph": "pH", "dqo": "DQO", "sst": "SST", "ss ": "SS ",
        "vazao": "Vazão", "nível": "Nível", "nivel": "Nível",
        "mix": "MIX", "tq": "TQ", "mbbr": "MBBR",
        "nitrificacao": "Nitrificação", "nitrificação": "Nitrificação",
        "mab": "MAB",
    }
    for k, v in replacements.items():
        txt = re_replace_case_insensitive(txt, k, v)

    return txt.strip()

# =========================
# MOTOR DE SEMÁFORO (cores)
# =========================
COLOR_OK = "#43A047"
COLOR_WARN = "#FB8C00"
COLOR_BAD = "#E53935"
COLOR_NEUTRAL = "#546E7A"
COLOR_NULL = "#9E9E9E"

def semaforo_numeric_color(label: str, val: float):
    if val is None or np.isnan(val):
        return COLOR_NULL

    base = _strip_accents(label.lower())

    if "oxigenacao" in base:
        if 1 <= val <= 5:
            return COLOR_OK
        else:
            return COLOR_BAD

    if re.search(r"\bph\b", base):
        is_mab = "mab" in base
        cfg = SEMAFORO_CFG["ph"]["mab" if is_mab else "general"]
        ok_min, ok_max = cfg["ok_min"], cfg["ok_max"]
        red_low, red_high = cfg["red_low"], cfg["red_high"]
        if val < red_low or val > red_high:
            return COLOR_BAD
        if ok_min <= val <= ok_max:
            return COLOR_OK
        return COLOR_WARN

    if "sst" in base or re.search(r"\bss\b", base):
        if "saida" in base or "saída" in label.lower():
            cfg = SEMAFORO_CFG["sst_saida"]
            if val <= cfg["green_max"]:
                return COLOR_OK
            if val <= cfg["orange_max"]:
                return COLOR_WARN
            return COLOR_BAD
        else:
            return COLOR_NEUTRAL

    if "dqo" in base:
        if "saida" in base or "saída" in label.lower():
            cfg = SEMAFORO_CFG["dqo_saida"]
            if val <= cfg["green_max"]:
                return COLOR_OK
            if val <= cfg["orange_max"]:
                return COLOR_WARN
            return COLOR_BAD
        else:
            return COLOR_NEUTRAL

    return None

# =========================
# GAUGES — caçambas
# =========================

def make_speedometer(val, label):
    nome_exibicao = _nome_exibicao(label)
    if val is None or np.isnan(val):
        val = 0.0
    color = COLOR_OK if val >= 70 else COLOR_WARN if val >= 30 else COLOR_BAD
    return go.Indicator(
        mode="gauge+number",
        value=float(val),
        number={"suffix": "%"},
        title={"text": f"<b>{nome_exibicao}</b>", "font": {"size": 16}},
        gauge={"axis": {"range": [0, 100]}, "bar": {"color": color}},
        domain={"x": [0, 1], "y": [0, 1]},
    )


def _cacamba_valor_radio(numero: int) -> float:
    padrao = _strip_accents(f"cacamba {numero}").lower()
    cols_desta = [
        col for col in df.columns
        if padrao in _strip_accents(col.lower())
    ]
    if not cols_desta:
        return np.nan

    for idx in range(len(df) - 1, -1, -1):
        row = df.iloc[idx]
        for col in cols_desta:
            v = str(row[col]).strip()
            if v and v.lower() not in ("nan", ""):
                m = re.search(r"(\d+)\s*%", col)
                if m:
                    return float(m.group(1))
                m2 = re.search(r"(\d+)", v)
                if m2:
                    return float(m2.group(1))
    return np.nan


def render_cacambas_gauges(title, n_cols=4):
    numeros = set()
    for col in df.columns:
        col_norm = _strip_accents(col.lower())
        if "cacamba" in col_norm:
            m = re.search(r"cacamba\s*(\d+)", col_norm)
            if m:
                numeros.add(int(m.group(1)))

    cacambas = sorted(numeros)
    if not cacambas:
        st.info("Nenhuma cacamba encontrada.")
        return

    n_rows = int(np.ceil(len(cacambas) / n_cols))
    fig = make_subplots(
        rows=n_rows, cols=n_cols,
        specs=[[{"type": "indicator"}] * n_cols for _ in range(n_rows)],
        horizontal_spacing=0.05, vertical_spacing=0.15
    )

    for i, num in enumerate(cacambas):
        val = _cacamba_valor_radio(num)
        label = f"Nivel da cacamba {num}"
        r = i // n_cols + 1
        cc = i % n_cols + 1
        fig.add_trace(make_speedometer(val, label), row=r, col=cc)

    fig.update_layout(
        height=max(280 * n_rows, 280),
        margin=dict(l=10, r=10, t=10, b=10),
    )
    st.plotly_chart(fig, use_container_width=True, key=f"plot-gauges-{_slug(title)}")

# =========================
# TILES (cards genéricos com semáforo)
# =========================

def _tile_color_and_text(raw_value, val_num, label, force_neutral_numeric=False):
    if raw_value is None:
        return COLOR_NULL, "—"

    t = _strip_accents(str(raw_value).strip().lower())
    if t in ["ok", "ligado", "aberto", "rodando", "on"]:
        return COLOR_OK, str(raw_value).upper()
    if t in ["nok", "falha", "erro", "fechado", "off"]:
        return COLOR_BAD, str(raw_value).upper()

    if not np.isnan(val_num):
        units = _units_from_label(label)
        base = _strip_accents(label.lower())

        if "vazao" in base or "vazão" in base:
            if 0 <= val_num <= 200:
                return COLOR_OK, f"{val_num:.0f} m³/h"
            else:
                return COLOR_BAD, f"{val_num:.0f} m³/h"

        color_by_rule = None if force_neutral_numeric else semaforo_numeric_color(label, val_num)
        if color_by_rule is not None:
            return color_by_rule, f"{val_num:.2f}{units}"

        if force_neutral_numeric:
            return COLOR_NEUTRAL, f"{val_num:.2f}{units}"

        if units == "%":
            fill = COLOR_OK if val_num >= 70 else COLOR_WARN if val_num >= 30 else COLOR_BAD
            return fill, f"{val_num:.1f}%"

        return COLOR_NEUTRAL, f"{val_num:.2f}{units}"

    return COLOR_WARN, str(raw_value)

def _render_tiles_from_cols(title, cols_orig, n_cols=4, force_neutral_numeric=False):
    cols_orig = [c for c in cols_orig if c]
    cols_orig = sorted(cols_orig, key=lambda x: _nome_exibicao(x))
    if not cols_orig:
        st.info(f"Nenhum item encontrado para: {title}")
        return

    cols_orig = [c for c in cols_orig if last_valid_raw(df, c) not in (None, "")]
    if not cols_orig:
        st.info(f"Nenhum item encontrado para: {title}")
        return

    fig = go.Figure()
    n_rows = int(np.ceil(len(cols_orig) / n_cols))
    fig.update_xaxes(visible=False, range=[0, n_cols])
    fig.update_yaxes(visible=False, range=[0, n_rows])

    for i, c in enumerate(cols_orig):
        raw = last_valid_raw(df, c)
        val = to_float_ptbr(raw)
        fill, txt = _tile_color_and_text(raw, val, c, force_neutral_numeric=force_neutral_numeric)

        r = i // n_cols
        cc = i % n_cols
        x0, x1 = cc + 0.05, cc + 0.95
        y0, y1 = (n_rows - 1 - r) + 0.05, (n_rows - 1 - r) + 0.95

        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                      fillcolor=fill, line=dict(color="white", width=1))
        nome = _nome_exibicao(c)
        fig.add_annotation(x=(x0 + x1) / 2, y=(y0 + y1) / 2 + 0.15,
                           text=f"<b style='font-size:18px'>{txt}</b>",
                           showarrow=False, font=dict(color="white"))
        fig.add_annotation(x=(x0 + x1) / 2, y=(y0 + y1) / 2 - 0.15,
                           text=f"<span style='font-size:12px'>{nome}</span>",
                           showarrow=False, font=dict(color="white"))

    fig.update_layout(height=max(170 * n_rows, 170),
                      margin=dict(l=10, r=10, t=10, b=10))
    st.subheader(title)
    st.plotly_chart(fig, use_container_width=True, key=f"plot-tiles-{_slug(title)}")


def render_tiles_split(title_base, base_keywords, n_cols=4, exclude_generic=True):
    excl = KW_EXCLUDE_GENERIC if exclude_generic else []
    cols_nitr = _filter_cols_intersection(
        cols_lower_noacc, must_any_1=base_keywords, must_any_2=KW_NITR, forbid_any=excl
    )
    _render_tiles_from_cols(f"{title_base} – Nitrificação", cols_nitr, n_cols=n_cols)
    cols_mbbr = _filter_cols_intersection(
        cols_lower_noacc, must_any_1=base_keywords, must_any_2=KW_MBBR, forbid_any=excl
    )
    _render_tiles_from_cols(f"{title_base} – MBBR", cols_mbbr, n_cols=n_cols)

def render_outros_niveis():
    cols = _filter_columns_by_keywords(cols_lower_noacc, KW_NIVEIS_OUTROS)
    cols = [c for c in cols if not any(k in _strip_accents(c.lower()) for k in KW_CACAMBA)]
    if not cols:
        return
    _render_tiles_from_cols("Níveis (MAB/TQ de Lodo)", cols, n_cols=3, force_neutral_numeric=False)

def render_vazoes():
    cols = _filter_columns_by_keywords(cols_lower_noacc, KW_VAZAO)
    if not cols:
        return
    _render_tiles_from_cols("Vazões", cols, n_cols=3, force_neutral_numeric=True)

def render_ph():
    cols = _filter_columns_by_keywords(cols_lower_noacc, KW_PH)
    if not cols:
        return
    _render_tiles_from_cols("pH", cols, n_cols=4, force_neutral_numeric=False)

def render_sst():
    cols = _filter_columns_by_keywords(cols_lower_noacc, KW_SST)
    if not cols:
        return
    _render_tiles_from_cols("Sólidos (SS/SST)", cols, n_cols=4, force_neutral_numeric=False)

def render_dqo():
    cols = _filter_columns_by_keywords(cols_lower_noacc, KW_DQO)
    if not cols:
        return
    _render_tiles_from_cols("DQO", cols, n_cols=4, force_neutral_numeric=False)

def render_estados():
    cols = _filter_columns_by_keywords(cols_lower_noacc, KW_ESTADOS)
    if not cols:
        return
    _render_tiles_from_cols("Estados / Equipamentos", cols, n_cols=3, force_neutral_numeric=False)

# =========================
# CABEÇALHO (última medição)
# =========================

def _operador_valor_radio() -> str:
    cols_op = [
        col for col in df.columns
        if "operador" in _strip_accents(col.lower()) or "operardor" in _strip_accents(col.lower())
    ]
    if not cols_op:
        return "—"

    for idx in range(len(df) - 1, -1, -1):
        row = df.iloc[idx]
        for col in cols_op:
            v = str(row[col]).strip()
            if v and v.lower() not in ("nan", ""):
                m = re.search(r"\[(.+?)\]", col)
                if m:
                    return m.group(1).strip()
                return v
    return "—"


def header_info():
    cand = ["carimbo de data/hora", "data"]
    found = {}
    for c in df.columns:
        k = _strip_accents(c.lower())
        if k in [_strip_accents(x) for x in cand]:
            found[k] = c

    col0, col1, col2 = st.columns(3)
    if "carimbo de data/hora" in found:
        col0.metric("Último carimbo", str(last_valid_raw(df, found["carimbo de data/hora"])))
    elif "data" in found:
        col0.metric("Data", str(last_valid_raw(df, found["data"])))
    col1.metric("Operador", _operador_valor_radio())
    col2.metric("Registros", f"{len(df)} linhas")

# =========================
# CARTAS — Funções (rótulos inteligentes)
# =========================

def cc_fmt_brl(v, pos=None):
    try:
        return ("R$ " + f"{v:,.0f}").replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return v


def cc_fmt_brl_compacto(v: float) -> str:
    try:
        n = float(v)
    except Exception:
        return str(v)
    sinal = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        return f"{sinal}R$ {n/1_000_000:.1f} mi".replace(".", ",")
    if n >= 1_000:
        return f"{sinal}R$ {n/1_000:.1f} mil".replace(".", ",")
    return (sinal + "R$ " + f"{n:,.0f}").replace(",", "X").replace(".", ",").replace("X", ".")


def _indices_extremos_locais(y: pd.Series):
    # FIX: removido type hint set[int] — incompatível com Python < 3.9
    idxs = set()
    ys = y.reset_index(drop=True)
    for i in range(1, len(ys)-1):
        if pd.isna(ys[i-1]) or pd.isna(ys[i]) or pd.isna(ys[i+1]):
            continue
        if ys[i] > ys[i-1] and ys[i] > ys[i+1]:
            idxs.add(y.index[i])
        if ys[i] < ys[i-1] and ys[i] < ys[i+1]:
            idxs.add(y.index[i])
    return idxs


def _selecionar_indices_para_rotulo(x: pd.Series, y: pd.Series,
                                    LSC: float, LIC: float,
                                    max_labels: int,
                                    incluir_oor: bool,
                                    incluir_extremos: bool,
                                    incluir_primeiro_ultimo: bool) -> list:
    # FIX: removido type hint list[int] — incompatível com Python < 3.9
    candidatos = []
    y_clean = y.dropna()
    if y_clean.empty or max_labels <= 0:
        return []

    if incluir_oor:
        oor_idx = y[(y > LSC) | (y < LIC)].dropna().index.tolist()
        candidatos.extend(oor_idx)

    if incluir_extremos:
        extremos = list(_indices_extremos_locais(y))
        candidatos.extend(extremos)

    if incluir_primeiro_ultimo:
        candidatos.extend([y_clean.index[0], y_clean.index[-1]])

    seen = set()
    candidatos = [i for i in candidatos if (not (i in seen) and not seen.add(i))]

    if len(candidatos) < max_labels:
        faltam = max_labels - len(candidatos)
        resto = [idx for idx in y.index.tolist() if (idx not in candidatos) and pd.notna(y.loc[idx])]
        resto = resto[-faltam:]
        candidatos.extend(resto)

    return sorted(set(candidatos), key=lambda i: x.loc[i])


def cc_desenhar_carta(x, y, titulo, ylabel, mostrar_rotulos=True):
    x = pd.Series(x).reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True).astype(float)
    x_dt = pd.to_datetime(x, errors="coerce")
    mask_ok = x_dt.notna() & (x_dt.dt.year >= 1900) & (x_dt.dt.year <= 2100)
    if mask_ok.sum() == 0:
        st.warning(f"Sem datas válidas para: {titulo}")
        return
    x = x[mask_ok].reset_index(drop=True)
    y = y[mask_ok].reset_index(drop=True)
    y = y.astype(float)
    y_stats = y.dropna()
    media = y_stats.mean() if not y_stats.empty else 0.0
    desvio = y_stats.std(ddof=1) if len(y_stats) > 1 else 0.0
    LSC = media + 3*desvio
    LIC = media - 3*desvio

    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.plot(x, y, marker="o", color="#1565C0", label="Série", linewidth=2, markersize=5)
    ax.axhline(media, color="#1565C0", linestyle="--", label="Média")
    if desvio > 0:
        ax.axhline(LSC, color="red", linestyle="--", label="LSC (+3σ)")
        ax.axhline(LIC, color="red", linestyle="--", label="LIC (−3σ)")

    ax.yaxis.set_major_formatter(FuncFormatter(cc_fmt_brl))

    if mostrar_rotulos and len(y_stats) > 0:
        idx_rotulos = _selecionar_indices_para_rotulo(
            x=pd.Series(x), y=y,
            LSC=LSC, LIC=LIC,
            max_labels=cc_lbl_max_points,
            incluir_oor=cc_lbl_out_of_control,
            incluir_extremos=cc_lbl_local_extremes,
            incluir_primeiro_ultimo=cc_lbl_show_first_last,
        )
        idx_rotulos = [i for i in idx_rotulos if not (pd.notna(y.loc[i]) and y.loc[i] == 0)]

        def _fmt(v):
            if cc_lbl_compact_format:
                return cc_fmt_brl_compacto(v)
            else:
                return ("R$ " + f"{v:,.0f}").replace(",", "X").replace(".", ",").replace("X", ".")

        x_series = pd.Series(x).reset_index(drop=True)
        x_num = pd.to_numeric(pd.to_datetime(x_series, errors="coerce"), errors="coerce")
        bbox = dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.7) if cc_lbl_bbox else None

        OFFSET_BASE = 18
        OFFSET_STEP = 14
        prev_x_num = None
        acum = 0
        sinal = 1

        for k, idx in enumerate(idx_rotulos):
            if pd.isna(y.loc[idx]):
                continue
            try:
                pos_idx = list(y.index).index(idx)
                curr_x_num = x_num.iloc[pos_idx] if pos_idx < len(x_num) else None
            except Exception:
                curr_x_num = None

            if prev_x_num is not None and curr_x_num is not None:
                diff = abs(curr_x_num - prev_x_num)
                total_range = x_num.max() - x_num.min() if x_num.max() != x_num.min() else 1
                proporcao = diff / total_range
                if proporcao < 0.04:
                    acum += 1
                else:
                    acum = 0
            else:
                acum = 0

            dy = sinal * (OFFSET_BASE + acum * OFFSET_STEP)
            sinal *= -1
            prev_x_num = curr_x_num

            ax.annotate(
                _fmt(y.loc[idx]),
                (x.loc[idx] if hasattr(x, "loc") else pd.Series(x).iloc[list(y.index).index(idx)], y.loc[idx]),
                textcoords="offset points",
                xytext=(0, dy),
                ha="center",
                fontsize=cc_lbl_fontsize,
                rotation=cc_lbl_angle,
                bbox=bbox,
                color="#0D47A1",
                arrowprops=dict(arrowstyle="-", color="#90CAF9", lw=0.8) if abs(dy) > OFFSET_BASE else None,
            )

    ax.set_title(titulo)
    ax.set_ylabel(ylabel)
    ax.set_xlabel("Data")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend(loc="best", frameon=True)
    st.pyplot(fig)
    plt.close(fig)  # FIX: libera memória — evita warning de "too many open figures"

# =========================
# DASHBOARD (seções)
# =========================
st.title("Dashboard Operacional ETE")
header_info()

render_cacambas_gauges("Caçambas")

cols_valvulas = [col for col in df.columns if "valvula" in _strip_accents(col.lower()) or "válvula" in col.lower()]
cols_valvulas = [c for c in cols_valvulas if last_valid_raw(df, c) not in (None, "")]
if cols_valvulas:
    _render_tiles_from_cols("Válvulas – MBBR", cols_valvulas, n_cols=4)

def _render_sopradores_radio(titulo, kw_area):
    cols = []
    for col in df.columns:
        cn = _strip_accents(col.lower())
        has_sop = "soprador" in cn
        has_area = any(_strip_accents(k.lower()) in cn for k in kw_area)
        has_oxig = "oxigenac" in cn
        if has_sop and has_area and not has_oxig:
            cols.append(col)
    cols = [c for c in cols if last_valid_raw(df, c) not in (None, "")]
    if cols:
        _render_tiles_from_cols(titulo, cols, n_cols=4)

_render_sopradores_radio("Sopradores – MBBR", KW_MBBR)
_render_sopradores_radio("Sopradores – Nitrificação", KW_NITR)

def _render_oxigenacao_radio(titulo, kw_area):
    grupos = {}
    for col in df.columns:
        cn = _strip_accents(col.lower())
        if "oxigenac" not in cn:
            continue
        has_area = any(_strip_accents(k.lower()) in cn for k in kw_area)
        if not has_area:
            continue
        nome_base = re.sub(r"\s*\[.*?\]", "", col).strip()
        if nome_base not in grupos:
            grupos[nome_base] = []
        grupos[nome_base].append(col)

    if not grupos:
        return

    itens_com_valor = []
    for nome_base, cols_grupo in grupos.items():
        for idx in range(len(df) - 1, -1, -1):
            row = df.iloc[idx]
            for col in cols_grupo:
                v = str(row[col]).strip()
                if v and v.lower() not in ("nan", ""):
                    m = re.search(r"\[(\d+)\]", col)
                    val = float(m.group(1)) if m else None
                    if val is None:
                        try:
                            val = float(v)
                        except Exception:
                            val = None
                    if val is not None:
                        itens_com_valor.append((nome_base, val))
                    break
            else:
                continue
            break

    if not itens_com_valor:
        return

    fig = go.Figure()
    n_cols = 4
    n_rows = int(np.ceil(len(itens_com_valor) / n_cols))
    fig.update_xaxes(visible=False, range=[0, n_cols])
    fig.update_yaxes(visible=False, range=[0, n_rows])

    for i, (nome, val) in enumerate(itens_com_valor):
        color = COLOR_OK if 1 <= val <= 5 else COLOR_BAD
        r = i // n_cols
        cc = i % n_cols
        x0, x1 = cc + 0.05, cc + 0.95
        y0, y1 = (n_rows - 1 - r) + 0.05, (n_rows - 1 - r) + 0.95
        fig.add_shape(type="rect", x0=x0, x1=x1, y0=y0, y1=y1,
                      fillcolor=color, line=dict(color="white", width=1))
        fig.add_annotation(x=(x0+x1)/2, y=(y0+y1)/2+0.15,
                           text=f"<b style='font-size:18px'>{val:.0f} mg/L</b>",
                           showarrow=False, font=dict(color="white"))
        fig.add_annotation(x=(x0+x1)/2, y=(y0+y1)/2-0.15,
                           text=f"<span style='font-size:12px'>{nome}</span>",
                           showarrow=False, font=dict(color="white"))

    fig.update_layout(height=max(170 * n_rows, 170), margin=dict(l=10, r=10, t=10, b=10))
    st.subheader(titulo)
    st.plotly_chart(fig, use_container_width=True, key=f"plot-oxig-{_slug(titulo)}")

_render_oxigenacao_radio("Oxigenação – MBBR", KW_MBBR)
_render_oxigenacao_radio("Oxigenação – Nitrificação", KW_NITR)

render_outros_niveis()
render_vazoes()
render_ph()
render_sst()
render_dqo()
render_estados()

# ============================================================
#        CARTAS DE CONTROLE — CUSTOS (R$)
# ============================================================
st.markdown("---")
st.header("🔴 Cartas de Controle — Custo (R$)")

with st.sidebar:
    gid_input = st.text_input("GID da aba de gastos", value="668859455")
CC_GID_GASTOS = gid_input.strip() or "668859455"
CC_URL_GASTOS = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={CC_GID_GASTOS}"

if st.button("🔄 Recarregar cartas"):
    st.rerun()

@st.cache_data(ttl=900, show_spinner=False)
def cc_baixar_csv_bruto(url: str, timeout: int = 20) -> pd.DataFrame:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    buf = io.StringIO(resp.text)
    df_txt = pd.read_csv(buf, dtype=str, keep_default_na=False, header=None)
    df_txt.columns = [str(c).strip() for c in df_txt.columns]
    return df_txt

def cc_strip_acc_lower(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return s.lower().strip()

def cc_find_header_row(df_txt: pd.DataFrame, max_scan: int = 120):
    # FIX: removido type hint "int | None" — incompatível com Python < 3.10
    kws_custo = ["custo", "custos", "gasto", "gastos", "valor", "$"]
    n = min(len(df_txt), max_scan)
    for i in range(n):
        row_vals = [cc_strip_acc_lower(x) for x in df_txt.iloc[i].tolist()]
        has_data  = any("data" in v for v in row_vals)
        has_custo = any(any(kw in v for v in row_vals) for kw in kws_custo)
        if has_data and has_custo:
            return i
    return None

def cc_parse_currency_br(series: pd.Series) -> pd.Series:
    s = series.astype(str)
    s = s.str.replace("\u00A0", " ", regex=False)
    s = s.str.replace("R$", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    s = s.str.replace(".", "", regex=False)
    s = s.str.replace(",", ".", regex=False)
    s = s.apply(lambda x: re.sub(r"[^0-9.\-]", "", x))
    return pd.to_numeric(s, errors="coerce")

def cc_guess_item_label(df_txt: pd.DataFrame, header_row: int, col_idx: int, fallback: str) -> str:
    label = ""
    if header_row - 1 >= 0:
        try:
            label = str(df_txt.iat[header_row - 1, col_idx]).strip()
        except Exception:
            label = ""
        if not label:
            for j in range(col_idx - 1, max(-1, col_idx - 8), -1):
                try:
                    v = str(df_txt.iat[header_row - 1, j]).strip()
                except Exception:
                    v = ""
                if v:
                    label = v
                    break
    if not label:
        label = fallback
    label = label.replace("\n", " ").strip()
    if len(label) > 80:
        label = label[:77] + "..."
    return label

with st.status("Carregando dados das cartas...", expanded=True) as status:
    try:
        st.write("• Baixando CSV do Google Sheets…")
        cc_df_raw = cc_baixar_csv_bruto(CC_URL_GASTOS, timeout=20)
        st.write(f"• CSV bruto: {cc_df_raw.shape[0]} linhas × {cc_df_raw.shape[1]} colunas")

        st.write("• Detectando linha de cabeçalho…")
        cc_hdr = cc_find_header_row(cc_df_raw, max_scan=120)
        if cc_hdr is None:
            st.error("❌ Não achei a linha de cabeçalho com DATA e CUSTOS na aba informada.")
            st.stop()

        cc_header_vals = [str(x).strip() for x in cc_df_raw.iloc[cc_hdr].tolist()]
        cc_df_all = cc_df_raw.iloc[cc_hdr + 1:].copy()
        cc_df_all.columns = cc_header_vals
        cc_df_all = cc_df_all.loc[:, [c.strip() != "" for c in cc_df_all.columns]]

        status.update(label="Dados carregados com sucesso ✅", state="complete")
    except requests.exceptions.Timeout:
        st.error("⏳ Timeout ao acessar o Google Sheets (20s). Tente novamente ou verifique sua conexão.")
        st.stop()
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Falha ao baixar o CSV: {e}")
        st.stop()
    except Exception as e:
        st.error(f"❌ Erro inesperado ao preparar dados: {e}")
        st.stop()

cc_norm_cols = [cc_strip_acc_lower(c) for c in cc_df_all.columns]
CC_KW_COST_INCLUDE = ["custo", "custos", "gasto", "gastos", "valor", "$"]
CC_KW_COST_EXCLUDE = ["media", "média", "status", "automatic", "automatico", "automático", "meta"]

def cc_is_valid_cost_header(nc: str) -> bool:
    has_include = any(k in nc for k in CC_KW_COST_INCLUDE)
    has_exclude = any(k in nc for k in CC_KW_COST_EXCLUDE)
    return has_include and not has_exclude

cc_cost_idx_list = [i for i, nc in enumerate(cc_norm_cols) if cc_is_valid_cost_header(nc)]
cc_data_idx_list = [i for i, nc in enumerate(cc_norm_cols) if "data" in nc]

if not cc_cost_idx_list:
    st.error("❌ Não encontrei nenhuma coluna de CUSTO/GASTO/VALOR válida.")
    st.write("Colunas disponíveis:", list(cc_df_all.columns))
    st.stop()
if not cc_data_idx_list:
    st.error("❌ Não encontrei nenhuma coluna de DATA.")
    st.write("Colunas disponíveis:", list(cc_df_all.columns))
    st.stop()

cc_items = []
cc_seen_labels = set()

for cost_idx in cc_cost_idx_list:
    cost_name = cc_df_all.columns[cost_idx]
    left_data = [i for i in cc_data_idx_list if i <= cost_idx]
    if left_data:
        data_idx = max(left_data)
    else:
        data_idx = min(cc_data_idx_list, key=lambda i: abs(i - cost_idx))
    data_name = cc_df_all.columns[data_idx]

    df_item = pd.DataFrame({
        "DATA": pd.to_datetime(cc_df_all.iloc[:, data_idx].astype(str), errors="coerce", dayfirst=True),
        "CUSTO": cc_parse_currency_br(cc_df_all.iloc[:, cost_idx]),
    }).dropna(subset=["DATA", "CUSTO"]).sort_values("DATA")

    if df_item.empty:
        continue

    label_guess = cc_guess_item_label(cc_df_raw, cc_hdr, cost_idx, fallback=cost_name)
    label_norm = cc_strip_acc_lower(label_guess)
    if label_norm in cc_seen_labels:
        continue
    cc_seen_labels.add(label_norm)

    cc_items.append({
        "label": label_guess,
        "cost_name": cost_name,
        "data_name": data_name,
        "data_idx": data_idx,
        "cost_idx": cost_idx,
        "df": df_item
    })

if not cc_items:
    st.warning("Nenhum item com dados válidos (DATA + CUSTO) foi encontrado após os filtros.")
    with st.expander("🔍 Debug de cabeçalhos de custo filtrados"):
        df_debug = pd.DataFrame({
            "col": list(cc_df_all.columns),
            "norm": cc_norm_cols,
            "is_valid_cost": [cc_is_valid_cost_header(n) for n in cc_norm_cols],
        })
        st.dataframe(df_debug)
    st.stop()

cc_labels_all = [it["label"] for it in cc_items]
cc_sel_labels = st.multiselect("Itens para exibir nas cartas", cc_labels_all, default=cc_labels_all)
cc_mostrar_rotulos = st.checkbox("Mostrar rótulos de dados nas cartas", value=True)

cc_items = [it for it in cc_items if it["label"] in cc_sel_labels]
if not cc_items:
    st.info("Selecione pelo menos um item para visualizar.")
    st.stop()

def cc_ultimo_valido_positivo(ser: pd.Series) -> float:
    s = pd.to_numeric(ser, errors="coerce")
    s = s[~s.isna()]
    if s.empty:
        return 0.0
    nz = s[s != 0]
    if not nz.empty:
        return float(nz.iloc[-1])
    return float(s.iloc[-1])


def cc_metricas_item(df_item: pd.DataFrame):
    ultimo = cc_ultimo_valido_positivo(df_item["CUSTO"])
    mask_nz = df_item["CUSTO"].fillna(0) != 0
    idx_ref = mask_nz[mask_nz].index[-1] if mask_nz.any() else df_item.index[-1]

    iso_week = df_item["DATA"].dt.isocalendar()
    df_tmp = df_item.copy()
    df_tmp["__sem__"]    = iso_week.week.astype(int)
    df_tmp["__anoiso__"] = iso_week.year.astype(int)
    ult_sem = int(df_tmp.loc[idx_ref, "__sem__"])
    ult_ano = int(df_tmp.loc[idx_ref, "__anoiso__"])
    custo_semana = df_tmp[(df_tmp["__sem__"] == ult_sem) & (df_tmp["__anoiso__"] == ult_ano)]["CUSTO"].sum()

    df_tmp["__mes__"] = df_tmp["DATA"].dt.month
    df_tmp["__ano__"] = df_tmp["DATA"].dt.year
    ult_mes  = int(df_tmp.loc[idx_ref, "__mes__"])
    ult_ano2 = int(df_tmp.loc[idx_ref, "__ano__"])
    custo_mes = df_tmp[(df_tmp["__mes__"] == ult_mes) & (df_tmp["__ano__"] == ult_ano2)]["CUSTO"].sum()

    return ultimo, custo_semana, custo_mes


cc_tabs = st.tabs([it["label"] for it in cc_items])
for tab, it in zip(cc_tabs, cc_items):
    with tab:
        df_item = it["df"]
        ultimo, custo_semana, custo_mes = cc_metricas_item(df_item)
        c1, c2, c3 = st.columns(3)
        c1.metric("Custo do Dia",
                  f"R$ {ultimo:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c2.metric("Custo da Semana",
                  f"R$ {custo_semana:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))
        c3.metric("Custo do Mês",
                  f"R$ {custo_mes:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."))

        df_day = df_item.groupby("DATA", as_index=False)["CUSTO"].sum().sort_values("DATA")
        df_week = (
            df_item.assign(semana=df_item["DATA"].dt.to_period("W-MON"))
                   .groupby("semana", as_index=False)["CUSTO"].sum()
        )
        df_week["Data"] = df_week["semana"].dt.start_time

        df_month = (
            df_item.assign(mes=df_item["DATA"].dt.to_period("M"))
                   .groupby("mes", as_index=False)["CUSTO"].sum()
        )
        df_month["Data"] = df_month["mes"].dt.to_timestamp()

        st.subheader("📅 Carta Diária")
        cc_desenhar_carta(df_day["DATA"], df_day["CUSTO"],
                          f"Custo Diário (R$) — {it['label']}", "R$",
                          mostrar_rotulos=cc_mostrar_rotulos)

        st.subheader("🗓️ Carta Semanal (ISO)")
        cc_desenhar_carta(df_week["Data"], df_week["CUSTO"],
                          f"Custo Semanal (R$) — {it['label']}", "R$",
                          mostrar_rotulos=cc_mostrar_rotulos)

        st.subheader("📆 Carta Mensal")
        cc_desenhar_carta(df_month["Data"], df_month["CUSTO"],
                          f"Custo Mensal (R$) — {it['label']}", "R$",
                          mostrar_rotulos=cc_mostrar_rotulos)

        with st.expander("🔍 Debug do item"):
            st.write("Coluna de DATA original:", it["data_name"], " | índice:", it["data_idx"])
            st.write("Coluna de CUSTO original:", it["cost_name"], " | índice:", it["cost_idx"])
            st.dataframe(df_item.head(10))

# ============================================================
#   RESUMO TEXTO — Sopradores
# ============================================================

def _col_matches_any(cnorm: str, kws):
    kws_norm = [_strip_accents(k.lower()) for k in kws]
    return any(k in cnorm for k in kws_norm)


def _select_soprador_cols(df_cols_norm, area_keywords):
    sel = []
    for c_norm in df_cols_norm:
        has_soprador = "soprador" in c_norm
        has_area = _col_matches_any(c_norm, area_keywords)
        has_excluded = _col_matches_any(c_norm, KW_EXCLUDE_GENERIC + KW_OXIG)
        if has_soprador and has_area and not has_excluded:
            sel.append(c_norm)
    return [COLMAP[c] for c in sel]


def _parse_status_ok_nok(raw):
    if raw is None or (isinstance(raw, float) and np.isnan(raw)):
        return "—"
    t = _strip_accents(str(raw).strip().lower())
    if t in ["ok", "ligado", "aberto", "rodando", "on"]:
        return "OK"
    if t in ["nok", "falha", "erro", "fechado", "off"]:
        return "NOK"
    return "—"


def _extract_first_int(text: str):
    # FIX: removido type hint "int | None" — incompatível com Python < 3.10
    m = re.search(r"\d+", _strip_accents(text.lower()))
    return int(m.group()) if m else None


def _coletar_status_area(df, area_keywords):
    cols_area = _select_soprador_cols(cols_lower_noacc, area_keywords)
    itens = []
    for col in cols_area:
        num = _extract_first_int(col)
        raw = last_valid_raw(df, col)
        stt = _parse_status_ok_nok(raw)
        itens.append((num, stt, col))
    itens.sort(key=lambda x: (9999 if x[0] is None else x[0], _strip_accents(x[2].lower())))
    pares = [f"{num} ({stt})" for num, stt, _ in itens if num is not None]
    return pares


def gerar_resumo_sopradores(df):
    mbbr_linha = _coletar_status_area(df, KW_MBBR)
    nitr_linha = _coletar_status_area(df, KW_NITR)
    linhas = []
    linhas.append("Sopradores MBBR:")
    linhas.append(" ".join(mbbr_linha) if mbbr_linha else "—")
    linhas.append("Sopradores Nitrificação:")
    linhas.append(" ".join(nitr_linha) if nitr_linha else "—")
    return "\n".join(linhas)

st.markdown("---")
st.subheader("🧾 Resumo — Sopradores (copiar e colar)")
texto_resumo = gerar_resumo_sopradores(df)
st.text_area("Texto", value=texto_resumo, height=110, label_visibility="collapsed")
st.caption("Selecione e copie o texto acima (Ctrl+C / Cmd+C) para colar no WhatsApp/relatório.")

# =============================================================================
#   MICROBIOLOGIA — ANÁLISE DE VÍDEO VIA IA + REGRAS CETESB L1.025
# =============================================================================

import base64, tempfile, json, os

MICRO_TABELA6 = {
    "flagelados":               {"semaforo": "vermelho", "icon": "🔴", "condicao": "Deficiência de aeração, má depuração e/ou sobrecarga orgânica", "recomendacao": "Verificar OD no tanque de aeração. Reduzir carga orgânica ou aumentar aeração."},
    "flagelados_rizopodes":     {"semaforo": "laranja",  "icon": "🔶", "condicao": "Lodo jovem — início de operação ou θc baixa", "recomendacao": "Verificar idade do lodo. Sistema em partida ou com sobrecarga hidráulica."},
    "ciliados_pedunculados":    {"semaforo": "verde",    "icon": "✅", "condicao": "Boas condições de depuração", "recomendacao": "Sistema operando bem. Manter parâmetros atuais."},
    "ciliados_livres":          {"semaforo": "verde",    "icon": "✅", "condicao": "Boas condições de depuração", "recomendacao": "Sistema operando bem."},
    "arcella":                  {"semaforo": "verde",    "icon": "✅", "condicao": "Boa depuração (Arcella sp.)", "recomendacao": "Indicador positivo. Manter condições atuais."},
    "aspidisca":                {"semaforo": "verde",    "icon": "🟢", "condicao": "Nitrificação ocorrendo (Aspidisca costata)", "recomendacao": "Nitrificação ativa. Monitorar amônia e nitrito."},
    "trachelophyllum":          {"semaforo": "laranja",  "icon": "🔶", "condicao": "θc (idade do lodo) alta — Trachelophyllum", "recomendacao": "Lodo velho. Avaliar descarte para rejuvenescer a biomassa."},
    "vorticella_microstoma":    {"semaforo": "vermelho", "icon": "🔴", "condicao": "Efluente de má qualidade (Vorticella microstoma)", "recomendacao": "Investigar causa: sobrecarga, tóxicos, aeração insuficiente."},
    "aelosoma":                 {"semaforo": "laranja",  "icon": "🔶", "condicao": "Excesso de OD (Aelosoma)", "recomendacao": "Reduzir aeração. OD provavelmente > 6 mg/L."},
    "rotiferos":                {"semaforo": "verde",    "icon": "✅", "condicao": "Lodo maduro, boa sedimentação (Rotíferos)", "recomendacao": "Indicador positivo de lodo aeróbio maduro."},
    "filamentos":               {"semaforo": "vermelho", "icon": "🔴", "condicao": "Intumescimento filamentoso — bulking", "recomendacao": "ALERTA: verificar IVL. Causas: baixo OD, sobrecarga, pH baixo, falta de nutrientes."},
    "nematoides":               {"semaforo": "laranja",  "icon": "🔶", "condicao": "θc elevada — Nematóides", "recomendacao": "Monitorar descarte de lodo."},
    "rizopodes_amebas":         {"semaforo": "laranja",  "icon": "🔶", "condicao": "Lodo jovem ou em transição (Amebas/Rizópodes)", "recomendacao": "Verificar idade do lodo e condições operacionais."},
    "flocos_bons":              {"semaforo": "verde",    "icon": "✅", "condicao": "Flocos bem formados — boa sedimentação", "recomendacao": "Morfologia do lodo adequada. Manter operação."},
    "flocos_dispersos":         {"semaforo": "vermelho", "icon": "🔴", "condicao": "Lodo disperso (pin-point) — má sedimentação", "recomendacao": "Verificar θc, toxicidade, variações de carga."},
    "cianobacterias":           {"semaforo": "vermelho", "icon": "🔴", "condicao": "Cianobactérias — qualidade ruim", "recomendacao": "ALERTA: possível toxicidade. Verificar condições do afluente."},
    "protozoa_livre":           {"semaforo": "laranja",  "icon": "🔶", "condicao": "Protozoários de vida livre — qualidade moderada", "recomendacao": "Monitorar evolução. Pode indicar lodo jovem ou perturbação."},
}

COR_SEMAFORO = {"verde": "#43A047", "laranja": "#FB8C00", "vermelho": "#E53935", "cinza": "#546E7A"}

_secrets = st.secrets if hasattr(st, "secrets") else {}
GOOGLE_API_KEY_MICRO = _secrets.get("GOOGLE_API_KEY", "")
_raw_keys = [
    _secrets.get("GOOGLE_API_KEY_1", ""),
    _secrets.get("GOOGLE_API_KEY_2", ""),
    _secrets.get("GOOGLE_API_KEY_3", ""),
]
GOOGLE_API_KEYS = [k for k in _raw_keys if k] or ([GOOGLE_API_KEY_MICRO] if GOOGLE_API_KEY_MICRO else [])
# FIX: _api_key_cycle só é criado se houver chaves — evita next(None) => TypeError
_api_key_cycle = itertools.cycle(GOOGLE_API_KEYS) if GOOGLE_API_KEYS else None

SYSTEM_PROMPT_MICRO = """Você é um especialista em microbiologia de sistemas de lodos ativados, seguindo a Norma Técnica CETESB L1.025.
Analise as imagens de microscópio fornecidas e identifique microrganismos visíveis.

Para cada organismo ou grupo identificado, classifique usando EXATAMENTE uma dessas chaves:
flagelados, flagelados_rizopodes, ciliados_pedunculados, ciliados_livres, arcella, aspidisca,
trachelophyllum, vorticella_microstoma, aelosoma, rotiferos, filamentos, nematoides,
rizopodes_amebas, flocos_bons, flocos_dispersos, cianobacterias, protozoa_livre

REGRAS IMPORTANTES:
1. Responda APENAS com JSON válido, sem texto antes ou depois, sem markdown, sem blocos de código
2. Não use aspas dentro de strings — use apenas aspas duplas no JSON
3. Se a imagem for de baixa qualidade, indique nos campos e liste o que conseguiu observar
4. Estime uma confiança (0.0 a 1.0) para cada organismo identificado

Formato OBRIGATÓRIO da resposta (JSON puro):
{
  "organismos": [
    {
      "chave": "chave_da_tabela",
      "nome": "nome cientifico ou grupo",
      "grupo": "grupo taxonomico",
      "descricao": "o que foi observado na imagem",
      "confianca": 0.85
    }
  ],
  "qualidade_imagem": "boa|regular|ruim",
  "nitidez_score": 0.75,
  "observacoes_gerais": "observacoes sobre as imagens"
}"""


def _calcular_nitidez_laplaciano(frame_bytes: bytes) -> float:
    try:
        from PIL import Image, ImageFilter
        import io as _io
        img = Image.open(_io.BytesIO(frame_bytes)).convert("L")
        lap = img.filter(ImageFilter.Kernel(
            size=3,
            kernel=[-1, -1, -1, -1, 8, -1, -1, -1, -1],
            scale=1, offset=0
        ))
        arr = np.array(lap, dtype=np.float32)
        return float(np.var(arr))
    except Exception:
        return 0.0


def _extrair_frames_video(video_bytes: bytes, max_frames: int = 8) -> list:
    import subprocess
    frames_candidatos = []

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "video.mp4")
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True
        )
        try:
            duration = float(result.stdout.strip())
        except Exception:
            duration = 10.0

        n_candidatos = max(max_frames * 3, 6)
        for i in range(n_candidatos):
            t = duration * 0.1 + (duration * 0.8) / (n_candidatos + 1) * (i + 1)
            frame_path = os.path.join(tmpdir, f"frame_{i:02d}.jpg")
            subprocess.run(
                ["ffmpeg", "-ss", str(t), "-i", video_path,
                 "-vframes", "1", "-vf", "scale=512:-1", "-q:v", "3",
                 frame_path, "-y"],
                capture_output=True
            )
            if os.path.exists(frame_path):
                with open(frame_path, "rb") as fimg:
                    frame_bytes_local = fimg.read()
                nitidez = _calcular_nitidez_laplaciano(frame_bytes_local)
                b64 = base64.b64encode(frame_bytes_local).decode()
                frames_candidatos.append((nitidez, b64))

    if not frames_candidatos:
        return []

    frames_candidatos.sort(key=lambda x: x[0], reverse=True)
    melhores = frames_candidatos[:max_frames]
    return [b64 for _, b64 in melhores]


def _selecionar_melhores_imagens(imagens_bytes: list, max_frames: int = 8) -> list:
    if not imagens_bytes:
        return []
    if len(imagens_bytes) <= max_frames:
        return [base64.b64encode(b).decode() for b in imagens_bytes]
    scored = []
    for img_bytes in imagens_bytes:
        nitidez = _calcular_nitidez_laplaciano(img_bytes)
        scored.append((nitidez, img_bytes))
    scored.sort(key=lambda x: x[0], reverse=True)
    melhores = scored[:max_frames]
    return [base64.b64encode(b).decode() for _, b in melhores]


def _chamar_gemini_micro(frames_b64: list, params_operacionais: dict, api_key: str = None) -> dict:
    # FIX: guarda defensivo — não chama next() se _api_key_cycle for None
    if _api_key_cycle is None:
        raise ValueError("Nenhuma chave GOOGLE_API_KEY configurada nos Secrets.")

    ctx_params = ""
    if params_operacionais:
        ctx_params = "\n\nParâmetros operacionais do dia (use para contextualizar o diagnóstico):\n"
        for k, v in params_operacionais.items():
            if v:
                ctx_params += f"- {k}: {v}\n"

    prompt_usuario = (
        f"{SYSTEM_PROMPT_MICRO}\n\n"
        f"Analise estes {len(frames_b64)} frame(s) de microscópio de lodo ativado de ETE "
        f"com esgoto doméstico/industrial.{ctx_params}\n\n"
        "IMPORTANTE: Responda APENAS com JSON válido, sem texto adicional, sem markdown."
    )

    parts = []
    for b64 in frames_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
    parts.append({"text": prompt_usuario})

    MAX_TENTATIVAS = max(len(GOOGLE_API_KEYS) * 2, 4)
    resp = None
    for tentativa in range(MAX_TENTATIVAS):
        chave_atual = next(_api_key_cycle)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={chave_atual}"
        try:
            resp = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": parts}]},
                timeout=90
            )
        except requests.exceptions.Timeout:
            espera = 2 ** (tentativa // len(GOOGLE_API_KEYS))
            st.warning(f"⏳ Timeout na tentativa {tentativa+1}/{MAX_TENTATIVAS}. Aguardando {espera}s...")
            time.sleep(espera)
            continue

        if resp.status_code in (503, 429):
            espera = 2 ** (tentativa // len(GOOGLE_API_KEYS))
            motivo = "sobrecarga (503)" if resp.status_code == 503 else "rate limit (429)"
            st.warning(f"⏳ Gemini com {motivo} — tentativa {tentativa+1}/{MAX_TENTATIVAS}. Aguardando {espera}s...")
            time.sleep(espera)
            continue

        resp.raise_for_status()
        break
    else:
        resp.raise_for_status()

    data = resp.json()
    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return {
            "organismos": [], "qualidade_imagem": "ruim", "nitidez_score": 0.0,
            "observacoes_gerais": f"Resposta inesperada da API: {str(data)[:200]}",
            "_erro_parse": True
        }

    texto_clean = texto.strip()
    for marcador in ["```json", "```JSON", "```"]:
        texto_clean = texto_clean.replace(marcador, "")
    texto_clean = texto_clean.strip()

    try:
        return json.loads(texto_clean)
    except json.JSONDecodeError:
        json_match = re.search(r'\{.*\}', texto_clean, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {
            "organismos": [], "qualidade_imagem": "ruim", "nitidez_score": 0.0,
            "observacoes_gerais": f"Erro ao parsear resposta. Texto: {texto[:200]}",
            "_erro_parse": True
        }


def _agregar_resultados(resultados: list) -> dict:
    if not resultados:
        return {"organismos": [], "qualidade_imagem": "ruim", "observacoes_gerais": "Sem resultados"}
    if len(resultados) == 1:
        return resultados[0]

    votos = {}
    for resultado in resultados:
        for org in resultado.get("organismos", []):
            chave = org.get("chave", "")
            if not chave:
                continue
            if chave not in votos:
                votos[chave] = {"count": 0, "confianca_total": 0.0, "dados": org}
            votos[chave]["count"] += 1
            votos[chave]["confianca_total"] += org.get("confianca", 0.7)

    organismos_agregados = []
    total_analises = len(resultados)
    for chave, info in votos.items():
        confianca_media = info["confianca_total"] / info["count"]
        fator_consenso = info["count"] / total_analises
        confianca_final = min(confianca_media * (0.7 + 0.3 * fator_consenso), 1.0)
        org_final = dict(info["dados"])
        org_final["confianca"] = round(confianca_final, 2)
        org_final["votos"] = info["count"]
        organismos_agregados.append(org_final)

    organismos_agregados.sort(key=lambda x: x["confianca"], reverse=True)

    qualidades = [r.get("qualidade_imagem", "regular") for r in resultados]
    ordem_qualidade = {"ruim": 0, "regular": 1, "boa": 2}
    qualidade_final = min(qualidades, key=lambda q: ordem_qualidade.get(q, 1))
    confianca_geral = (
        sum(o["confianca"] for o in organismos_agregados) / len(organismos_agregados)
        if organismos_agregados else 0.0
    )

    return {
        "organismos": organismos_agregados,
        "qualidade_imagem": qualidade_final,
        "confianca_media": round(confianca_geral, 2),
        "observacoes_gerais": resultados[0].get("observacoes_gerais", ""),
        "n_analises": total_analises,
    }


def aplicar_regras_cetesb(organismos: list) -> dict:
    if not organismos:
        return {
            "qualidade": "indeterminada",
            "descricao": "Nenhum organismo identificado — não é possível classificar.",
            "cor": COR_SEMAFORO["cinza"], "icon": "❓",
            "acoes_recomendadas": ["Repetir análise com imagens de melhor qualidade."]
        }

    chaves = {org.get("chave", "") for org in organismos}

    if "cianobacterias" in chaves:
        return {"qualidade": "ruim", "descricao": "Cianobactérias detectadas — risco de toxicidade.",
                "cor": COR_SEMAFORO["vermelho"], "icon": "🔴",
                "acoes_recomendadas": ["Verificar origem do afluente.", "Monitorar toxicidade."]}

    if "filamentos" in chaves:
        return {"qualidade": "ruim", "descricao": "Filamentos detectados — risco de bulking filamentoso.",
                "cor": COR_SEMAFORO["vermelho"], "icon": "🔴",
                "acoes_recomendadas": ["Medir IVL.", "Verificar OD no tanque.", "Avaliar sobrecarga orgânica."]}

    if "flocos_dispersos" in chaves:
        return {"qualidade": "ruim", "descricao": "Lodo disperso — má sedimentação.",
                "cor": COR_SEMAFORO["vermelho"], "icon": "🔴",
                "acoes_recomendadas": ["Verificar θc.", "Investigar tóxicos no afluente."]}

    if "flagelados" in chaves or "vorticella_microstoma" in chaves:
        return {"qualidade": "ruim", "descricao": "Flagelados/Vorticella microstoma — indicadores de má depuração.",
                "cor": COR_SEMAFORO["vermelho"], "icon": "🔴",
                "acoes_recomendadas": ["Verificar OD.", "Reduzir carga orgânica ou aumentar aeração."]}

    chaves_moderadas = {"protozoa_livre", "nematoides", "trachelophyllum", "aelosoma", "rizopodes_amebas", "flagelados_rizopodes"}
    if chaves & chaves_moderadas:
        return {"qualidade": "moderada", "descricao": "Organismos de transição — equilíbrio instável.",
                "cor": COR_SEMAFORO["laranja"], "icon": "🔶",
                "acoes_recomendadas": ["Monitorar evolução.", "Verificar idade do lodo."]}

    return {"qualidade": "boa", "descricao": "Organismos indicadores de sistema estável.",
            "cor": COR_SEMAFORO["verde"], "icon": "✅",
            "acoes_recomendadas": ["Manter parâmetros operacionais atuais."]}


def render_microbiologia():
    st.markdown("---")
    st.header("🔬 Microbiologia do Lodo — Análise por IA (CETESB L1.025)")
    st.caption("Suba fotos ou vídeo do microscópio. A IA identifica os microrganismos e gera diagnóstico conforme a Norma Técnica L1.025.")
    st.info("🤖 **Análise baseada em IA + regras da CETESB** — Combinação de visão computacional com a Tabela 6 da Norma L1.025.", icon="ℹ️")

    def _pegar_ultimo(kws):
        for col in df.columns:
            if any(k in _strip_accents(col.lower()) for k in kws):
                v = last_valid_raw(df, col)
                if v:
                    return str(v)
        return ""

    params_dia = {
        "pH (último registro)": _pegar_ultimo(["ph mbbr", "ph mab"]),
        "OD Nitrificação": _pegar_ultimo(["oxigenac", "oxigenação"]),
        "SST Nitrificação": _pegar_ultimo(["sst nitrif"]),
        "DQO Saída": _pegar_ultimo(["dqo saida", "dqo saída"]),
    }
    params_filtrados = {k: v for k, v in params_dia.items() if v}

    if params_filtrados:
        st.caption("📋 Parâmetros do último registro:")
        cols_p = st.columns(len(params_filtrados))
        for i, (k, v) in enumerate(params_filtrados.items()):
            cols_p[i].metric(k, v)

    st.subheader("📸 Upload de Imagens ou Vídeo do Microscópio")
    modo = st.radio(
        "Como quer enviar?",
        ["📷 Fotos (JPG/PNG) — recomendado", "🎥 Vídeo (MP4/MOV) — requer ffmpeg"],
        horizontal=True
    )

    frames_b64 = []

    if "Fotos" in modo:
        imagens = st.file_uploader(
            "Selecione uma ou mais fotos do microscópio",
            type=["jpg", "jpeg", "png", "bmp", "tiff"],
            accept_multiple_files=True,
        )
        if imagens:
            todas_bytes = [img.read() for img in imagens]
            with st.spinner("🔍 Selecionando frames mais nítidos..."):
                frames_b64 = _selecionar_melhores_imagens(todas_bytes, max_frames=2)
            st.caption(f"✅ {len(imagens)} imagem(ns) → {len(frames_b64)} selecionada(s) pela nitidez:")
            cols_prev = st.columns(min(len(frames_b64), 2))
            for i, b64 in enumerate(frames_b64):
                cols_prev[i % 2].image(base64.b64decode(b64), caption=f"Frame {i+1}", use_container_width=True)
    else:
        video_file = st.file_uploader(
            "Selecione o vídeo",
            type=["mp4", "mov", "avi", "webm", "mkv"],
        )
        if video_file is not None:
            st.video(video_file)
            video_file.seek(0)
            video_bytes = video_file.read()
            st.caption(f"Tamanho: {len(video_bytes) / (1024*1024):.1f} MB")
            with st.spinner("🎞️ Extraindo frames..."):
                try:
                    frames_b64 = _extrair_frames_video(video_bytes, max_frames=2)
                    if frames_b64:
                        st.success(f"✅ {len(frames_b64)} frame(s) selecionado(s).")
                        cols_prev = st.columns(min(len(frames_b64), 2))
                        for i, b64 in enumerate(frames_b64):
                            cols_prev[i % 2].image(base64.b64decode(b64), caption=f"Frame {i+1}", use_container_width=True)
                    else:
                        st.error("❌ Não foi possível extrair frames. Use o modo Fotos.")
                except Exception as e:
                    st.error(f"❌ ffmpeg indisponível. Use o modo Fotos.\n\nDetalhes: {e}")

    if frames_b64:
        analisar = st.button("🔬 Analisar com IA + Regras CETESB", type="primary", use_container_width=True)

        if analisar:
            if not GOOGLE_API_KEYS:
                st.error("❌ Nenhuma chave API Google configurada nos Secrets.")
                st.stop()

            with st.status("Analisando...", expanded=True) as status_micro:
                try:
                    st.write(f"🤖 Enviando {len(frames_b64)} frame(s)...")
                    resultado_unico = _chamar_gemini_micro(frames_b64, params_filtrados)
                    resultados_por_frame = [resultado_unico] if not resultado_unico.get("_erro_parse") else []
                    resultado_consolidado = _agregar_resultados(resultados_por_frame)
                    st.write("📋 Aplicando regras CETESB L1.025...")
                    diagnostico_cetesb = aplicar_regras_cetesb(resultado_consolidado.get("organismos", []))
                    resultado_consolidado["diagnostico_cetesb"] = diagnostico_cetesb
                    st.session_state["micro_resultado"] = resultado_consolidado
                    status_micro.update(label="✅ Análise concluída!", state="complete")
                except requests.exceptions.HTTPError as e:
                    st.error(f"❌ Erro na API: {e.response.status_code} — {e.response.text[:300]}")
                    st.stop()
                except Exception as e:
                    st.error(f"❌ Erro inesperado: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.stop()

    resultado = st.session_state.get("micro_resultado")
    if not resultado:
        st.info("Faça upload de imagens e clique em **Analisar** para ver o diagnóstico.")
        return

    organismos    = resultado.get("organismos", [])
    qualidade     = resultado.get("qualidade_imagem", "regular")
    obs_gerais    = resultado.get("observacoes_gerais", "")
    confianca_med = resultado.get("confianca_media", 0.0)
    n_analises    = resultado.get("n_analises", 1)
    diag_cetesb   = resultado.get("diagnostico_cetesb", {})

    if qualidade == "ruim":
        st.warning("⚠️ Qualidade das imagens baixa — resultados podem ser imprecisos.")
    elif qualidade == "regular":
        st.info("ℹ️ Qualidade das imagens regular.")

    if obs_gerais:
        st.caption(f"🔎 {obs_gerais}")

    if not organismos:
        st.warning("Nenhum microrganismo identificado.")
        if st.button("🗑️ Limpar e tentar novamente"):
            del st.session_state["micro_resultado"]
            st.rerun()
        return

    st.subheader("📊 Resumo da Análise")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🦠 Microrganismos", len(organismos))
    m2.metric("🎯 Confiança Média", f"{confianca_med * 100:.0f}%" if confianca_med else "—")
    m3.metric("📷 Qualidade", qualidade.capitalize())
    m4.metric("🔬 Frames", n_analises)

    st.subheader("💧 Qualidade Estimada do Lodo / Processo")
    nivel_cor  = diag_cetesb.get("cor", COR_SEMAFORO["cinza"])
    nivel_icon = diag_cetesb.get("icon", "❓")
    nivel_qual = diag_cetesb.get("qualidade", "—").upper()
    nivel_desc = diag_cetesb.get("descricao", "")

    st.markdown(
        f"""<div style="background:{nivel_cor};border-radius:10px;padding:16px 20px;margin-bottom:12px;color:white;">
            <div style="font-size:22px;font-weight:700">{nivel_icon} Qualidade: {nivel_qual}</div>
            <div style="font-size:14px;margin-top:6px;opacity:0.92">{nivel_desc}</div>
            <div style="font-size:12px;margin-top:8px;opacity:0.8">📋 IA + regras CETESB L1.025</div>
        </div>""",
        unsafe_allow_html=True
    )

    acoes = diag_cetesb.get("acoes_recomendadas", [])
    if acoes:
        with st.expander("⚡ Ações Recomendadas", expanded=True):
            for acao in acoes:
                st.markdown(f"• {acao}")

    st.subheader(f"🦠 Microrganismos Detectados ({len(organismos)})")
    col_orgs = st.columns(2)
    for i, org in enumerate(organismos):
        with col_orgs[i % 2]:
            chave = org.get("chave", "")
            meta  = MICRO_TABELA6.get(chave, {"semaforo": "cinza", "icon": "🔍", "condicao": "", "recomendacao": ""})
            cor   = COR_SEMAFORO.get(meta["semaforo"], COR_SEMAFORO["cinza"])
            conf  = org.get("confianca", 0.0)
            barra = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))

            st.markdown(
                f"""<div style="background:{cor};border-radius:8px;padding:12px 14px;margin-bottom:8px;color:white;">
                    <div style="font-size:15px;font-weight:500">{meta['icon']} {org.get('nome','—')}</div>
                    <div style="font-size:12px;opacity:0.9;margin-top:2px">{org.get('grupo','')}</div>
                    <div style="font-size:12px;margin-top:6px">{org.get('descricao','')}</div>
                    {f'<div style="font-size:11px;margin-top:4px;opacity:0.85">📋 {meta["condicao"]}</div>' if meta.get('condicao') else ''}
                    <div style="font-size:11px;margin-top:6px;opacity:0.8">🎯 {barra} {conf*100:.0f}%</div>
                </div>""",
                unsafe_allow_html=True
            )

    st.subheader("📋 Diagnóstico do Processo (CETESB L1.025 — Tabela 6)")
    chaves_unicas = list({org.get("chave","") for org in organismos if org.get("chave","") in MICRO_TABELA6})
    diag_vermelho = [MICRO_TABELA6[c] for c in chaves_unicas if MICRO_TABELA6[c]["semaforo"] == "vermelho"]
    diag_laranja  = [MICRO_TABELA6[c] for c in chaves_unicas if MICRO_TABELA6[c]["semaforo"] == "laranja"]
    diag_verde    = [MICRO_TABELA6[c] for c in chaves_unicas if MICRO_TABELA6[c]["semaforo"] == "verde"]

    for d in diag_vermelho:
        st.error(f"**{d['icon']} {d['condicao']}**\n\n→ {d['recomendacao']}")
    for d in diag_laranja:
        st.warning(f"**{d['icon']} {d['condicao']}**\n\n→ {d['recomendacao']}")
    for d in diag_verde:
        st.success(f"**{d['icon']} {d['condicao']}**")

    st.subheader("📝 Resumo para WhatsApp / Relatório")
    linhas = ["🔬 Análise Microbiológica do Lodo (CETESB L1.025):", ""]
    linhas.append(f"Qualidade: {nivel_icon} {nivel_qual}")
    linhas.append(f"Confiança: {confianca_med*100:.0f}%" if confianca_med else "")
    linhas.append("")
    linhas.append("Microrganismos:")
    for org in organismos:
        linhas.append(f"• {org.get('nome','?')} ({org.get('grupo','')}): {org.get('descricao','')}")
    linhas.append("")
    for d in diag_vermelho + diag_laranja + diag_verde:
        linhas.append(f"{d['icon']} {d['condicao']}")
    if acoes:
        linhas.append("")
        linhas.append("Ações:")
        for acao in acoes:
            linhas.append(f"• {acao}")

    st.text_area("", value="\n".join(l for l in linhas if l is not None),
                 height=220, label_visibility="collapsed", key="ta_micro_resumo")
    st.caption("Ctrl+A → Ctrl+C para copiar.")

    if st.button("🗑️ Limpar e analisar novamente"):
        del st.session_state["micro_resultado"]
        st.rerun()

render_microbiologia()

# =============================================================================
#   CORREÇÃO DE pH — CALCULADORA DE DOSAGEM
# =============================================================================

def render_correcao_ph():
    st.markdown("---")
    st.header("⚗️ Correção de pH — Calculadora de Dosagem")
    st.caption(
        "Informe os parâmetros do seu tanque ou linha e receba a resposta direta: "
        "**quanto produto dosar** para atingir o pH alvo."
    )

    modo_op = st.radio(
        "Modo de operação",
        ["Tanque (dosagem única)", "Linha (dosagem contínua)"],
        horizontal=True,
    )

    modo_batelada = "Tanque" in modo_op

    col1, col2, col3 = st.columns(3)
    with col1:
        ph_atual = st.number_input("pH atual (medido)", 0.0, 14.0, 8.5, 0.1)
    with col2:
        ph_alvo = st.number_input("pH alvo (desejado)", 0.0, 14.0, 7.0, 0.1)
    with col3:
        if modo_batelada:
            volume_tanque = st.number_input("Volume do tanque (m³)", 0.1, value=100.0)
            vazao = None
        else:
            vazao = st.number_input("Vazão da linha (m³/h)", 0.01, value=10.0)
            volume_tanque = None

    reagente = st.selectbox("Reagente disponível", list(REAGENTES.keys()))
    alcalinidade = st.number_input("Alcalinidade (mg CaCO₃/L)", 0.0, value=200.0)

    calcular = st.button("Calcular dosagem", use_container_width=True)

    if not calcular:
        return

    delta_ph = ph_alvo - ph_atual
    cfg = REAGENTES[reagente]

    precisa_acido = delta_ph < 0

    conc_meq_mL = (cfg["pureza"] * cfg["densidade"] * 1000 / cfg["MM"]) * cfg["eq"]

    # cálculo simplificado mantido
    demanda_meq_L = abs(delta_ph) * 0.1 + (alcalinidade / 50)

    dose_L_por_m3 = demanda_meq_L / conc_meq_mL

    st.markdown("---")

    if modo_batelada:
        vol_produto_L = dose_L_por_m3 * volume_tanque

        st.subheader("📣 Resultado")
        st.success(f"Adicionar **{vol_produto_L:.2f} L** de {cfg['nome_curto']} no tanque")

    else:
        vazao_produto_L_h = dose_L_por_m3 * vazao

        st.subheader("📣 Resultado")
        st.success(f"Dosar **{vazao_produto_L_h:.3f} L/h** de {cfg['nome_curto']} na linha")
