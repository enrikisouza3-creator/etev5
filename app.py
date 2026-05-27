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
GID_FORM = "1283870792"  # aba com o formulário operacional
# Corrigido: use &gid= (não &amp;amp;gid=)
CSV_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={GID_FORM}"

# -------------------------
# Carrega a planilha (df = operacional)
# -------------------------
df = pd.read_csv(CSV_URL)
df.columns = [str(c).strip() for c in df.columns]

# =========================
# NORMALIZAÇÃO / AUXILIARES
# =========================
def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def _slug(s: str) -> str:
    # gera chave curta para evitar IDs duplicados em gráficos (Plotly)
    return _strip_accents(str(s).lower()).replace(" ", "-").replace("–", "-").replace("/", "-")

cols_lower_noacc = [_strip_accents(c.lower()) for c in df.columns]
COLMAP = dict(zip(cols_lower_noacc, df.columns))  # normalizado -> original

# Palavras‑chave — refletem os nomes reais do Google Forms
KW_CACAMBA   = ["cacamba", "caçamba"]
KW_NITR      = ["nitrificacao", "nitrificação", "nitrificac"]
KW_MBBR      = ["mbbr"]
# Válvulas: no Forms são "Válvula Inferior Tq. MBBR1/2" — sem "nitrificação"
KW_VALVULA   = ["valvula", "válvula"]
KW_SOPRADOR  = ["soprador"]
# Oxigenação: no Forms são "Oxigenação MBBR1/2" e "Oxigenação 1/2 Nitrificação"
KW_OXIG      = ["oxigenacao", "oxigenação"]

# Grupos adicionais
KW_NIVEIS_OUTROS = ["nivel", "nível"]
KW_VAZAO         = ["vazao", "vazão"]
KW_PH            = ["ph ", " ph", "ph-", "ph_"]
KW_SST           = ["sst ", " sst", "ss "]
KW_DQO           = ["dqo ", " dqo"]
KW_ESTADOS       = ["decanter", "desvio", "tempo de desc", "volante"]

# Exclusões genéricas para não poluir cartões
KW_EXCLUDE_GENERIC = KW_SST + KW_DQO + KW_PH + KW_VAZAO + KW_NIVEIS_OUTROS + KW_CACAMBA

# -------------------------
# Conversões e utilidades
# -------------------------
def to_float_ptbr(x):
    """Converte string PT-BR (%, vírgula) para float. Aceita escalar ou Series/DataFrame/list."""
    # Se vier Series/DataFrame/array por engano, tenta extrair um escalar útil
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
    # "10,5" -> "10.5" ; "1.234,5" -> "1234.5"
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except:
        return np.nan

def last_valid_raw(df_local, col):
    """Retorna o último valor não vazio de uma coluna,
    tratando o caso de cabeçalhos duplicados (DataFrame) ao
    escolher a coluna mais à direita.
    """
    obj = df_local[col]
    # Se houver colunas duplicadas com o mesmo nome, obj será um DataFrame.
    if isinstance(obj, pd.DataFrame):
        s = obj.iloc[:, -1]  # prefere a última coluna (mais à direita)
    else:
        s = obj
    s = s.replace(r"^\s*$", np.nan, regex=True)
    valid = s.dropna()
    if valid.empty:
        return None
    return valid.iloc[-1]

def _filter_columns_by_keywords(all_cols_norm_noacc, keywords):
    """Retorna nomes originais das colunas que contenham QUALQUER keyword."""
    kws = [_strip_accents(k.lower()) for k in keywords]
    selected_norm = []
    for c_norm in all_cols_norm_noacc:
        if any(k in c_norm for k in kws):
            selected_norm.append(c_norm)
    return [COLMAP[c] for c in selected_norm]

def _extract_number(base: str) -> str:
    return "".join(ch for ch in base if ch.isdigit())

def _remove_brackets(text: str) -> str:
    # Remove qualquer coisa após '['
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
# ⚙️ PARÂMETROS DO SEMÁFORO (Sidebar)
# =========================
with st.sidebar.expander("⚙️ Parâmetros do Semáforo", expanded=True):
    st.caption("Ajuste os limites; os valores abaixo são padrões comuns e podem ser adaptados.")
    # Oxigenação (DO)
    st.markdown("**Oxigenação (mg/L)**")
    do_ok_min_nitr = st.number_input("Nitrificação – DO mínimo (verde)", value=2.0, step=0.1)
    do_ok_max_nitr = st.number_input("Nitrificação – DO máximo (verde)", value=3.0, step=0.1)
    do_warn_low_nitr  = st.number_input("Nitrificação – abaixo disso é VERMELHO", value=1.0, step=0.1)
    do_warn_high_nitr = st.number_input("Nitrificação – acima disso é VERMELHO", value=4.0, step=0.1)

    do_ok_min_mbbr = st.number_input("MBBR – DO mínimo (verde)", value=2.0, step=0.1)
    do_ok_max_mbbr = st.number_input("MBBR – DO máximo (verde)", value=3.0, step=0.1)
    do_warn_low_mbbr  = st.number_input("MBBR – abaixo disso é VERMELHO", value=1.0, step=0.1)
    do_warn_high_mbbr = st.number_input("MBBR – acima disso é VERMELHO", value=4.0, step=0.1)

    # pH
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

    # Qualidade do efluente
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
    import re
    return re.sub(pattern, repl, s, flags=re.IGNORECASE)


def _nome_exibicao(label_original: str) -> str:
    """
    Padroniza nomes para:
      - "Nível da caçamba X"
      - "Soprador de Nitrificação X" / "Soprador de MBBR X"
      - "Oxigenação Nitrificação X" / "Oxigenação MBBR X"
      - "Válvula ..." conforme área
    """
    base_clean = _remove_brackets(label_original)
    base = _strip_accents(base_clean.lower()).strip()
    num = _extract_number(base)

    # Caçambas
    if "cacamba" in base:
        return f"Nível da caçamba {num}" if num else "Nível da caçamba"

    # Oxigenação (DO) — NÃO chamar de "Soprador"
    if "oxigenacao" in base:
        if any(k in base for k in KW_NITR):
            return f"Oxigenação Nitrificação {num}".strip()
        if any(k in base for k in KW_MBBR):
            return f"Oxigenação MBBR {num}".strip()
        return f"Oxigenação {num}".strip()

    # Sopradores (status)
    if "soprador" in base:
        if any(k in base for k in KW_NITR):
            return f"Soprador de Nitrificação {num}" if num else "Soprador de Nitrificação"
        if any(k in base for k in KW_MBBR):
            return f"Soprador de MBBR {num}" if num else "Soprador de MBBR"
        return f"Soprador {num}" if num else "Soprador"

    # Válvulas
    if "valvula" in base:
        if any(k in base for k in KW_NITR):
            return f"Válvula de Nitrificação {num}" if num else "Válvula de Nitrificação"
        if any(k in base for k in KW_MBBR):
            return f"Válvula de MBBR {num}" if num else "Válvula de MBBR"
        return f"Válvula {num}" if num else "Válvula"

    # Ajustes de capitalização comuns
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
COLOR_OK = "#43A047"      # verde
COLOR_WARN = "#FB8C00"    # laranja
COLOR_BAD = "#E53935"     # vermelho
COLOR_NEUTRAL = "#546E7A" # cinza azulado
COLOR_NULL = "#9E9E9E"    # cinza (sem dado)

def semaforo_numeric_color(label: str, val: float):
    """
    Retorna cor baseada em regras por tipo (Oxigenação, pH, SST/DQO Saída, etc.)
    Se não houver regra aplicável, retorna None (para cair no padrão antigo).
    """
    if val is None or np.isnan(val):
        return COLOR_NULL

    base = _strip_accents(label.lower())

    # -------- Oxigenação (DO) — faixa fixa 1 a 5 mg/L --------
    if "oxigenacao" in base:
        if 1 <= val <= 5:
            return COLOR_OK
        else:
            return COLOR_BAD

    # -------- pH --------
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

    # -------- SST / SS — SAÍDA --------
    if "sst" in base or re.search(r"\bss\b", base):
        if "saida" in base or "saída" in label.lower():
            cfg = SEMAFORO_CFG["sst_saida"]
            if val <= cfg["green_max"]:
                return COLOR_OK
            if val <= cfg["orange_max"]:
                return COLOR_WARN
            return COLOR_BAD
        else:
            return COLOR_NEUTRAL  # internos -> neutro

    # -------- DQO — SAÍDA --------
    if "dqo" in base:
        if "saida" in base or "saída" in label.lower():
            cfg = SEMAFORO_CFG["dqo_saida"]
            if val <= cfg["green_max"]:
                return COLOR_OK
            if val <= cfg["orange_max"]:
                return COLOR_WARN
            return COLOR_BAD
        else:
            return COLOR_NEUTRAL  # internos -> neutro

    # Sem regra específica
    return None

# =========================
# GAUGES — SOMENTE colunas com "cacamba" no nome (sem acento)
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
    """
    O Google Forms cria uma coluna por opcao de % para cada cacamba
    (ex: 'Cacamba 1 [1%]', 'Cacamba 1 [2%]', ...).
    Encontra todas as colunas da cacamba numero, olha a ultima linha
    preenchida e retorna o valor percentual da opcao marcada.
    """
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
    """
    Mostra EXATAMENTE um gauge por cacamba numerada (1, 2, 3...).
    Consolida as colunas de radio button do Google Forms em um unico valor.
    """
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
        rows=n_rows,
        cols=n_cols,
        specs=[[{"type": "indicator"}] * n_cols for _ in range(n_rows)],
        horizontal_spacing=0.05,
        vertical_spacing=0.15
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
    """Define cor e texto do card conforme tipo de dado + semáforo configurável."""
    if raw_value is None:
        return COLOR_NULL, "—"

    # 1) Texto (OK/NOK etc.)
    t = _strip_accents(str(raw_value).strip().lower())
    if t in ["ok", "ligado", "aberto", "rodando", "on"]:
        return COLOR_OK, str(raw_value).upper()
    if t in ["nok", "falha", "erro", "fechado", "off"]:
        return COLOR_BAD, str(raw_value).upper()

    # 2) Numérico
    if not np.isnan(val_num):
        units = _units_from_label(label)
        base = _strip_accents(label.lower())

        # ---- Vazão (0 a 200 m³/h) – regra fixa independente de force_neutral_numeric ----
        if "vazao" in base or "vazão" in base:
            if 0 <= val_num <= 200:
                return COLOR_OK, f"{val_num:.0f} m³/h"
            else:
                return COLOR_BAD, f"{val_num:.0f} m³/h"

        # Semáforo dedicado por regra
        color_by_rule = None if force_neutral_numeric else semaforo_numeric_color(label, val_num)
        if color_by_rule is not None:
            return color_by_rule, f"{val_num:.2f}{units}"

        # Caso neutro forçado
        if force_neutral_numeric:
            return COLOR_NEUTRAL, f"{val_num:.2f}{units}"

        # Padrão (mantém 70/30) — melhor para % (ex.: caçamba fora dos gauges)
        if units == "%":
            fill = COLOR_OK if val_num >= 70 else COLOR_WARN if val_num >= 30 else COLOR_BAD
            return fill, f"{val_num:.1f}%"

        # Sem regra específica → neutro
        return COLOR_NEUTRAL, f"{val_num:.2f}{units}"

    # 3) Texto que não bate com dicionário → laranja
    return COLOR_WARN, str(raw_value)

def _render_tiles_from_cols(title, cols_orig, n_cols=4, force_neutral_numeric=False):
    cols_orig = [c for c in cols_orig if c]
    cols_orig = sorted(cols_orig, key=lambda x: _nome_exibicao(x))
    if not cols_orig:
        st.info(f"Nenhum item encontrado para: {title}")
        return

    # Filtra antecipadamente colunas sem nenhum dado válido
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
    """Cards: Nitrificação e MBBR para Válvulas/Sopradores/Oxigenação — com interseção e exclusão."""
    excl = KW_EXCLUDE_GENERIC if exclude_generic else []
    # Nitrificação = (base_keywords) AND (KW_NITR)
    cols_nitr = _filter_cols_intersection(
        cols_lower_noacc, must_any_1=base_keywords, must_any_2=KW_NITR, forbid_any=excl
    )
    _render_tiles_from_cols(f"{title_base} – Nitrificação", cols_nitr, n_cols=n_cols)

    # MBBR = (base_keywords) AND (KW_MBBR)
    cols_mbbr = _filter_cols_intersection(
        cols_lower_noacc, must_any_1=base_keywords, must_any_2=KW_MBBR, forbid_any=excl
    )
    _render_tiles_from_cols(f"{title_base} – MBBR", cols_mbbr, n_cols=n_cols)

# -------------------------
# Grupos adicionais
# -------------------------

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
    """
    O Forms gera uma coluna por nome de operador (ex: 'Operador [Bruce]').
    Percorre a ultima linha preenchida e retorna o nome do operador marcado.
    """
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
                # Tenta extrair nome do colchete: 'Operador [Bruce]' -> 'Bruce'
                import re as _re
                m = _re.search(r"\[(.+?)\]", col)
                if m:
                    return m.group(1).strip()
                # Se nao tiver colchete, retorna o proprio valor
                return v
    return "—"


def header_info():
    # tenta achar campos de auditoria
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
    except:
        return v


def cc_fmt_brl_compacto(v: float) -> str:
    """Formata R$ de forma compacta (1.200 -> 1,2 mil; 1.200.000 -> 1,2 mi)."""
    try:
        n = float(v)
    except:
        return str(v)
    sinal = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000:
        return f"{sinal}R$ {n/1_000_000:.1f} mi".replace(".", ",")
    if n >= 1_000:
        return f"{sinal}R$ {n/1_000:.1f} mil".replace(".", ",")
    return (sinal + "R$ " + f"{n:,.0f}").replace(",", "X").replace(".", ",").replace("X", ".")


def _indices_extremos_locais(y: pd.Series) -> set[int]:
    """Encontra picos e vales simples (comparando com vizinhos imediatos)."""
    idxs = set()
    ys = y.reset_index(drop=True)
    for i in range(1, len(ys)-1):
        if pd.isna(ys[i-1]) or pd.isna(ys[i]) or pd.isna(ys[i+1]):
            continue
        # pico
        if ys[i] > ys[i-1] and ys[i] > ys[i+1]:
            idxs.add(y.index[i])
        # vale
        if ys[i] < ys[i-1] and ys[i] < ys[i+1]:
            idxs.add(y.index[i])
    return idxs


def _selecionar_indices_para_rotulo(x: pd.Series, y: pd.Series,
                                    LSC: float, LIC: float,
                                    max_labels: int,
                                    incluir_oor: bool,
                                    incluir_extremos: bool,
                                    incluir_primeiro_ultimo: bool) -> list[int]:
    """
    Seleciona índices a rotular priorizando:
      1) OOR (out-of-range: > LSC ou < LIC)
      2) Extremos locais
      3) Primeiro e último
      4) Preenche com últimos N restantes (mais recentes)
    """
    candidatos = []
    y_clean = y.dropna()
    if y_clean.empty or max_labels <= 0:
        return []

    # 1) Fora de controle
    if incluir_oor:
        oor_idx = y[(y > LSC) | (y < LIC)].dropna().index.tolist()
        candidatos.extend(oor_idx)

    # 2) Extremos locais
    if incluir_extremos:
        extremos = list(_indices_extremos_locais(y))
        candidatos.extend(extremos)

    # 3) Primeiro e último
    if incluir_primeiro_ultimo:
        candidatos.extend([y_clean.index[0], y_clean.index[-1]])

    # Remove duplicados preservando ordem
    seen = set()
    candidatos = [i for i in candidatos if (not (i in seen) and not seen.add(i))]

    # 4) Caso falte preencher até max_labels: pega os mais recentes
    if len(candidatos) < max_labels:
        faltam = max_labels - len(candidatos)
        resto = [idx for idx in y.index.tolist() if (idx not in candidatos) and pd.notna(y.loc[idx])]
        resto = resto[-faltam:]  # últimos
        candidatos.extend(resto)

    return sorted(set(candidatos), key=lambda i: x.loc[i])


def cc_desenhar_carta(x, y, titulo, ylabel, mostrar_rotulos=True):
    """
    Carta de controle com rótulos inteligentes (sem poluição visual).
    Usa controles da sidebar:
      cc_lbl_max_points, cc_lbl_out_of_control, cc_lbl_local_extremes,
      cc_lbl_show_first_last, cc_lbl_compact_format, cc_lbl_fontsize,
      cc_lbl_angle, cc_lbl_bbox
    """
    # Alinha séries e filtra datas inválidas (causa do erro matplotlib year 0001)
    x = pd.Series(x).reset_index(drop=True)
    y = pd.Series(y).reset_index(drop=True).astype(float)
    x_dt = pd.to_datetime(x, errors="coerce")
    mask_ok = x_dt.notna() & (x_dt.dt.year >= 1900) & (x_dt.dt.year <= 2100)
    if mask_ok.sum() == 0:
        st.warning(f"Sem datas válidas para: {titulo}")
        return
    x = x[mask_ok].reset_index(drop=True)
    y = y[mask_ok].reset_index(drop=True)
    # Série como float
    y = y.astype(float)
    # Remove NaN para estatística
    y_stats = y.dropna()
    media = y_stats.mean() if not y_stats.empty else 0.0
    desvio = y_stats.std(ddof=1) if len(y_stats) > 1 else 0.0
    LSC = media + 3*desvio
    LIC = media - 3*desvio

    fig, ax = plt.subplots(figsize=(12, 4.8))

    # Série
    ax.plot(x, y, marker="o", color="#1565C0", label="Série", linewidth=2, markersize=5)

    # Linhas de média/controle
    ax.axhline(media, color="#1565C0", linestyle="--", label="Média")
    if desvio > 0:
        ax.axhline(LSC, color="red", linestyle="--", label="LSC (+3σ)")
        ax.axhline(LIC, color="red", linestyle="--", label="LIC (−3σ)")

    # Formatação do eixo Y em R$
    ax.yaxis.set_major_formatter(FuncFormatter(cc_fmt_brl))

    # Rótulos inteligentes
    if mostrar_rotulos and len(y_stats) > 0:
        idx_rotulos = _selecionar_indices_para_rotulo(
            x=pd.Series(x),
            y=y,
            LSC=LSC, LIC=LIC,
            max_labels=cc_lbl_max_points,
            incluir_oor=cc_lbl_out_of_control,
            incluir_extremos=cc_lbl_local_extremes,
            incluir_primeiro_ultimo=cc_lbl_show_first_last,
        )

        # Remove zeros da lista de rótulos — evita amontoamento em séries com muitos R$0
        idx_rotulos = [i for i in idx_rotulos if not (pd.notna(y.loc[i]) and y.loc[i] == 0)]

        def _fmt(v):
            if cc_lbl_compact_format:
                return cc_fmt_brl_compacto(v)
            else:
                return ("R$ " + f"{v:,.0f}").replace(",", "X").replace(".", ",").replace("X", ".")

        # Converte índices para posições numéricas no eixo X para calcular distância
        x_series = pd.Series(x).reset_index(drop=True)
        x_num = pd.to_numeric(pd.to_datetime(x_series, errors="coerce"), errors="coerce")

        bbox = dict(boxstyle="round,pad=0.25", fc="white", ec="none", alpha=0.7) if cc_lbl_bbox else None

        # Offsets verticais: alterna cima/baixo com amplitude crescente
        # para pontos próximos no eixo X (evita sobreposição)
        OFFSET_BASE = 18
        OFFSET_STEP = 14
        prev_x_num = None
        acum = 0  # acumulador de proximidade para aumentar offset
        sinal = 1

        for k, idx in enumerate(idx_rotulos):
            if pd.isna(y.loc[idx]):
                continue

            # Calcula posição X numérica deste ponto
            try:
                pos_idx = list(y.index).index(idx)
                curr_x_num = x_num.iloc[pos_idx] if pos_idx < len(x_num) else None
            except Exception:
                curr_x_num = None

            # Se está muito próximo do anterior, aumenta o offset
            if prev_x_num is not None and curr_x_num is not None:
                diff = abs(curr_x_num - prev_x_num)
                total_range = x_num.max() - x_num.min() if x_num.max() != x_num.min() else 1
                proporcao = diff / total_range
                if proporcao < 0.04:   # pontos muito próximos (< 4% do range)
                    acum += 1
                else:
                    acum = 0
            else:
                acum = 0

            dy = sinal * (OFFSET_BASE + acum * OFFSET_STEP)
            sinal *= -1  # alterna cima/baixo
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

# =========================
# DASHBOARD (seções)
# =========================
st.title("Dashboard Operacional ETE")
header_info()

# Caçambas (gauge)
render_cacambas_gauges("Caçambas")

# Válvulas — no Forms: "Válvula Inferior Tq. MBBR1/2" (somente MBBR, sem nitrificação)
cols_valvulas = [col for col in df.columns if "valvula" in _strip_accents(col.lower()) or "válvula" in col.lower()]
cols_valvulas = [c for c in cols_valvulas if last_valid_raw(df, c) not in (None, "")]
if cols_valvulas:
    _render_tiles_from_cols("Válvulas – MBBR", cols_valvulas, n_cols=4)

# Sopradores — no Forms: "Sopradores MBBR" e "Sopradores Nitrificação" com radio por soprador
def _render_sopradores_radio(titulo, kw_area):
    """Sopradores do Forms: pergunta-mãe tem OK/NOK/OF e sub-colunas por número de soprador."""
    # Pega colunas cuja pergunta-mãe é o grupo (ex: "Sopradores MBBR [Soprador 1]")
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

# Oxigenação — no Forms: "Oxigenação MBBR1/2" e "Oxigenação 1/2 Nitrificação" (radio 1-10)
def _render_oxigenacao_radio(titulo, kw_area):
    """Lê colunas de oxigenação (radio 1-10) e consolida por sensor."""
    # Agrupa colunas por sensor (nome sem o valor do radio)
    import re as _re
    grupos = {}  # nome_sensor -> lista de colunas
    for col in df.columns:
        cn = _strip_accents(col.lower())
        if "oxigenac" not in cn:
            continue
        has_area = any(_strip_accents(k.lower()) in cn for k in kw_area)
        if not has_area:
            continue
        # Remove valor entre colchetes para agrupar: "Oxigenação MBBR1 [5]" -> "Oxigenação MBBR1"
        nome_base = _re.sub(r"\s*\[.*?\]", "", col).strip()
        if nome_base not in grupos:
            grupos[nome_base] = []
        grupos[nome_base].append(col)

    if not grupos:
        return

    # Para cada sensor, acha o valor marcado na última linha
    itens_com_valor = []
    for nome_base, cols_grupo in grupos.items():
        for idx in range(len(df) - 1, -1, -1):
            row = df.iloc[idx]
            for col in cols_grupo:
                v = str(row[col]).strip()
                if v and v.lower() not in ("nan", ""):
                    # Tenta extrair número do colchete
                    m = _re.search(r"\[(\d+)\]", col)
                    val = float(m.group(1)) if m else None
                    if val is None:
                        try:
                            val = float(v)
                        except:
                            val = None
                    if val is not None:
                        itens_com_valor.append((nome_base, val))
                    break
            else:
                continue
            break

    if not itens_com_valor:
        return

    # Renderiza como tiles
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

# ---- Indicadores adicionais
render_outros_niveis()
render_vazoes()
render_ph()
render_sst()
render_dqo()
render_estados()

# ============================================================
#        CARTAS DE CONTROLE — CUSTOS (R$)  [MULTI-ITEM]
# ============================================================
st.markdown("---")
st.header("🔴 Cartas de Controle — Custo (R$)")

# ---- CONFIG: GID da aba (pode trocar na sidebar) ----
with st.sidebar:
    gid_input = st.text_input("GID da aba de gastos", value="668859455")
CC_GID_GASTOS = gid_input.strip() or "668859455"
# Corrigido: use &gid=
CC_URL_GASTOS = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}/export?format=csv&gid={CC_GID_GASTOS}"

# Botão de recarregar (útil no Cloud)
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

def cc_find_header_row(df_txt: pd.DataFrame, max_scan: int = 120) -> int | None:
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
    s = s.str.replace("\u00A0", " ", regex=False)  # NBSP
    s = s.str.replace("R$", "", regex=False)
    s = s.str.replace(" ", "", regex=False)
    s = s.str.replace(".", "", regex=False)       # milhar
    s = s.str.replace(",", ".", regex=False)      # decimal
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
    st.error("❌ Não encontrei nenhuma coluna de CUSTO/GASTO/VALOR válida (excluídas: média, status, meta).")
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
            "is_valid_cost": [ cc_is_valid_cost_header(n) for n in cc_norm_cols ],
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
                          f"Custo Diário (R$) — {it['label']}", "R$", mostrar_rotulos=cc_mostrar_rotulos)

        st.subheader("🗓️ Carta Semanal (ISO)")
        cc_desenhar_carta(df_week["Data"], df_week["CUSTO"],
                          f"Custo Semanal (R$) — {it['label']}", "R$", mostrar_rotulos=cc_mostrar_rotulos)

        st.subheader("📆 Carta Mensal")
        cc_desenhar_carta(df_month["Data"], df_month["CUSTO"],
                          f"Custo Mensal (R$) — {it['label']}", "R$", mostrar_rotulos=cc_mostrar_rotulos)

        with st.expander("🔍 Debug do item"):
            st.write("Coluna de DATA original:", it["data_name"], " | índice:", it["data_idx"])
            st.write("Coluna de CUSTO original:", it["cost_name"], " | índice:", it["cost_idx"])
            st.dataframe(df_item.head(10))

# ------------------------------------------------------------
# 7) RESUMO TEXTO — Sopradores (para WhatsApp/Relatório)
# ------------------------------------------------------------

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


def _extract_first_int(text: str) -> int | None:
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
# =============================================================================
#        MICROBIOLOGIA — ANÁLISE DE VÍDEO VIA IA + REGRAS CETESB L1.025
#        Melhorias implementadas:
#          1. Extração de 1-8 frames com filtro de qualidade (Laplaciano)
#          2. Prompt estruturado para resposta JSON robusta
#          3. Agregação de resultados por voto majoritário
#          4. Pós-processamento baseado em regras CETESB
#          5. Interface melhorada com métricas e confiança
# =============================================================================

import base64, tempfile, json, os
import numpy as np
import subprocess

# ──────────────────────────────────────────────────────────────────────────────
# TABELA 6 CETESB — Mapeamento de microrganismos para diagnóstico
# ──────────────────────────────────────────────────────────────────────────────
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
    # Grupos adicionais para maior cobertura
    "cianobacterias":           {"semaforo": "vermelho", "icon": "🔴", "condicao": "Cianobactérias — qualidade ruim", "recomendacao": "ALERTA: possível toxicidade. Verificar condições do afluente."},
    "protozoa_livre":           {"semaforo": "laranja",  "icon": "🔶", "condicao": "Protozoários de vida livre — qualidade moderada", "recomendacao": "Monitorar evolução. Pode indicar lodo jovem ou perturbação."},
}

COR_SEMAFORO = {"verde": "#43A047", "laranja": "#FB8C00", "vermelho": "#E53935", "cinza": "#546E7A"}

# Chave da API lida dos secrets do Streamlit
_secrets = st.secrets if hasattr(st, "secrets") else {}
GOOGLE_API_KEY_MICRO = _secrets.get("GOOGLE_API_KEY", "")

# Suporte a múltiplas chaves (GOOGLE_API_KEY_1, _2, _3) para rotação e bypass de rate limit
_raw_keys = [
    _secrets.get("GOOGLE_API_KEY_1", ""),
    _secrets.get("GOOGLE_API_KEY_2", ""),
    _secrets.get("GOOGLE_API_KEY_3", ""),
]
GOOGLE_API_KEYS = [k for k in _raw_keys if k] or ([GOOGLE_API_KEY_MICRO] if GOOGLE_API_KEY_MICRO else [])
_api_key_cycle = itertools.cycle(GOOGLE_API_KEYS) if GOOGLE_API_KEYS else None

# ──────────────────────────────────────────────────────────────────────────────
# PROMPT ESTRUTURADO — Versão melhorada com instrução de JSON estrito
# ──────────────────────────────────────────────────────────────────────────────
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


# ──────────────────────────────────────────────────────────────────────────────
# 1. EXTRAÇÃO DE FRAMES COM FILTRO DE QUALIDADE (Variância do Laplaciano)
# ──────────────────────────────────────────────────────────────────────────────

def _calcular_nitidez_laplaciano(frame_bytes: bytes) -> float:
    """
    Calcula a nitidez de um frame usando variância do Laplaciano.
    Quanto maior o valor, mais nítida a imagem.
    Retorna 0.0 se não conseguir calcular (ex: PIL não disponível).
    """
    try:
        from PIL import Image, ImageFilter
        import io as _io
        img = Image.open(_io.BytesIO(frame_bytes)).convert("L")  # escala de cinza
        # Aplica filtro Laplaciano para detectar bordas
        lap = img.filter(ImageFilter.Kernel(
            size=3,
            kernel=[-1, -1, -1,
                    -1,  8, -1,
                    -1, -1, -1],
            scale=1, offset=0
        ))
        arr = np.array(lap, dtype=np.float32)
        return float(np.var(arr))  # variância = medida de nitidez
    except Exception:
        return 0.0


def _extrair_frames_video(video_bytes: bytes, max_frames: int = 8) -> list:
    """
    Extrai entre 1 e `max_frames` frames do vídeo usando ffmpeg.
    Aplica filtro de qualidade (Laplaciano) para selecionar apenas frames nítidos.

    Args:
        video_bytes: Bytes do arquivo de vídeo
        max_frames: Número máximo de frames a retornar (padrão: 2)

    Returns:
        Lista de strings base64 dos melhores frames
    """
    frames_candidatos = []  # lista de (nitidez, b64)

    with tempfile.TemporaryDirectory() as tmpdir:
        video_path = os.path.join(tmpdir, "video.mp4")
        with open(video_path, "wb") as f:
            f.write(video_bytes)

        # Obtém duração do vídeo
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True
        )
        try:
            duration = float(result.stdout.strip())
        except Exception:
            duration = 10.0

        # Extrai mais candidatos do que o necessário para poder filtrar
        n_candidatos = max(max_frames * 3, 6)  # extrai 3x mais para selecionar os melhores

        for i in range(n_candidatos):
            # Distribui timestamps ao longo do vídeo, evitando início e fim (mais borrados)
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

    # Ordena por nitidez (maior = mais nítido) e seleciona os melhores
    frames_candidatos.sort(key=lambda x: x[0], reverse=True)
    melhores = frames_candidatos[:max_frames]

    # Log de nitidez para debug
    nitidez_scores = [round(n, 1) for n, _ in frames_candidatos]
    print(f"[Microbiologia] Nitidez dos candidatos: {nitidez_scores}")
    print(f"[Microbiologia] Selecionados: {[round(n, 1) for n, _ in melhores]}")

    return [b64 for _, b64 in melhores]


def _selecionar_melhores_imagens(imagens_bytes: list, max_frames: int = 8) -> list:
    """
    Para imagens estáticas (JPG/PNG), aplica filtro de nitidez e retorna as melhores.

    Args:
        imagens_bytes: Lista de bytes das imagens
        max_frames: Número máximo de imagens a retornar

    Returns:
        Lista de strings base64 das imagens mais nítidas
    """
    if not imagens_bytes:
        return []

    # Se há poucas imagens, usa todas
    if len(imagens_bytes) <= max_frames:
        return [base64.b64encode(b).decode() for b in imagens_bytes]

    # Calcula nitidez de cada imagem
    scored = []
    for img_bytes in imagens_bytes:
        nitidez = _calcular_nitidez_laplaciano(img_bytes)
        scored.append((nitidez, img_bytes))

    # Seleciona as mais nítidas
    scored.sort(key=lambda x: x[0], reverse=True)
    melhores = scored[:max_frames]
    return [base64.b64encode(b).decode() for _, b in melhores]


# ──────────────────────────────────────────────────────────────────────────────
# 2. INTEGRAÇÃO COM IA — chamada à API com tratamento de erro robusto
# ──────────────────────────────────────────────────────────────────────────────

def _chamar_gemini_micro(frames_b64: list, params_operacionais: dict, api_key: str = None) -> dict:
    """
    Chama a API Google Gemini Vision com os frames selecionados.
    Usa o modelo gemini-2.5-flash. Envia TODOS os frames numa única requisição
    para economizar cota (limite gratuito: 5 RPM).
    Implementa rotação entre múltiplas chaves API e retry com backoff exponencial
    para contornar erros 503 (sobrecarga) e 429 (rate limit).

    Args:
        frames_b64: Lista de imagens em base64
        params_operacionais: Parâmetros do dia para contexto
        api_key: ignorado (mantido por compatibilidade) — usa GOOGLE_API_KEYS global

    Returns:
        Dicionário com resultado parseado ou dict com erro em caso de falha
    """
    # Monta o prompt completo (Gemini recebe system + user juntos nos "parts")
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

    # Monta a lista de "parts" — imagens + texto
    parts = []
    for b64 in frames_b64:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": b64
            }
        })
    parts.append({"text": prompt_usuario})

    # Chama a API Gemini (REST) com rotação de chaves e retry com backoff
    if not GOOGLE_API_KEYS:
        raise ValueError("Nenhuma chave GOOGLE_API_KEY configurada nos Secrets.")

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
            st.warning(f"⏳ Timeout na tentativa {tentativa+1}/{MAX_TENTATIVAS}. Aguardando {espera}s antes de tentar nova chave...")
            time.sleep(espera)
            continue

        if resp.status_code in (503, 429):
            espera = 2 ** (tentativa // len(GOOGLE_API_KEYS))  # 1s, 2s, 4s, 8s
            motivo = "sobrecarga (503)" if resp.status_code == 503 else "rate limit (429)"
            st.warning(f"⏳ Gemini com {motivo} — tentativa {tentativa+1}/{MAX_TENTATIVAS}. Trocando chave e aguardando {espera}s...")
            time.sleep(espera)
            continue

        resp.raise_for_status()
        break
    else:
        resp.raise_for_status()

    data = resp.json()

    # Extrai o texto da resposta do Gemini
    try:
        texto = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        return {
            "organismos": [],
            "qualidade_imagem": "ruim",
            "nitidez_score": 0.0,
            "observacoes_gerais": f"Resposta inesperada da API Gemini: {str(data)[:200]}",
            "_erro_parse": True
        }

    # Limpeza robusta antes de parsear
    texto_clean = texto.strip()
    for marcador in ["```json", "```JSON", "```"]:
        texto_clean = texto_clean.replace(marcador, "")
    texto_clean = texto_clean.strip()

    # Tenta parsear o JSON
    try:
        return json.loads(texto_clean)
    except json.JSONDecodeError:
        # Tenta encontrar o JSON dentro do texto (fallback)
        json_match = re.search(r'\{.*\}', texto_clean, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # Retorna estrutura mínima de erro
        return {
            "organismos": [],
            "qualidade_imagem": "ruim",
            "nitidez_score": 0.0,
            "observacoes_gerais": f"Erro ao parsear resposta da IA. Texto bruto: {texto[:200]}",
            "_erro_parse": True
        }


# ──────────────────────────────────────────────────────────────────────────────
# 3. AGREGAÇÃO DE RESULTADOS — Voto majoritário entre múltiplas análises
# ──────────────────────────────────────────────────────────────────────────────

def _agregar_resultados(resultados: list) -> dict:
    """
    Agrega resultados de múltiplas análises usando voto majoritário.
    Organismos que aparecem em mais de um resultado ganham mais peso.

    Args:
        resultados: Lista de dicts retornados por _chamar_claude_micro

    Returns:
        Resultado consolidado com confiança média
    """
    if not resultados:
        return {"organismos": [], "qualidade_imagem": "ruim", "observacoes_gerais": "Sem resultados"}

    if len(resultados) == 1:
        return resultados[0]

    # Conta votos por chave de organismo e soma confiança
    votos = {}  # chave -> {"count": int, "confianca_total": float, "dados": dict}
    for resultado in resultados:
        for org in resultado.get("organismos", []):
            chave = org.get("chave", "")
            if not chave:
                continue
            if chave not in votos:
                votos[chave] = {"count": 0, "confianca_total": 0.0, "dados": org}
            votos[chave]["count"] += 1
            votos[chave]["confianca_total"] += org.get("confianca", 0.7)

    # Seleciona organismos com pelo menos 1 voto, ordenados por confiança média
    organismos_agregados = []
    total_analises = len(resultados)
    for chave, info in votos.items():
        confianca_media = info["confianca_total"] / info["count"]
        # Bonus de confiança se apareceu em múltiplas análises
        fator_consenso = info["count"] / total_analises
        confianca_final = min(confianca_media * (0.7 + 0.3 * fator_consenso), 1.0)

        org_final = dict(info["dados"])
        org_final["confianca"] = round(confianca_final, 2)
        org_final["votos"] = info["count"]
        organismos_agregados.append(org_final)

    # Ordena por confiança (maior primeiro)
    organismos_agregados.sort(key=lambda x: x["confianca"], reverse=True)

    # Agrega qualidade da imagem (usa a pior)
    qualidades = [r.get("qualidade_imagem", "regular") for r in resultados]
    ordem_qualidade = {"ruim": 0, "regular": 1, "boa": 2}
    qualidade_final = min(qualidades, key=lambda q: ordem_qualidade.get(q, 1))

    # Confiança média geral
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


# ──────────────────────────────────────────────────────────────────────────────
# 4. REGRAS CETESB — Camada de pós-processamento baseada em regras
# ──────────────────────────────────────────────────────────────────────────────

def aplicar_regras_cetesb(organismos: list) -> dict:
    """
    Interpreta os microrganismos identificados e classifica a qualidade
    da água/lodo com base em regras da CETESB L1.025.

    Regras (ordem de prioridade — mais grave prevalece):
      1. Cianobactérias → qualidade "ruim"
      2. Filamentos (bulking) → qualidade "ruim"
      3. Lodo disperso → qualidade "ruim"
      4. Flagelados predominantes → qualidade "ruim"
      5. Protozoários livres / Vorticella microstoma → qualidade "moderada"
      6. Nematóides / Rotíferos / Trachelophyllum → qualidade "moderada"
      7. Amebas/Rizópodes → qualidade "moderada"
      8. Demais → qualidade "boa"

    Args:
        organismos: Lista de organismos identificados (com campo 'chave')

    Returns:
        Dict com: qualidade, descricao, cor, icon, acoes_recomendadas
    """
    if not organismos:
        return {
            "qualidade": "indeterminada",
            "descricao": "Nenhum organismo identificado — não é possível classificar.",
            "cor": COR_SEMAFORO["cinza"],
            "icon": "❓",
            "acoes_recomendadas": ["Repetir análise com imagens de melhor qualidade."]
        }

    chaves = {org.get("chave", "") for org in organismos}

    # Regra 1: Cianobactérias — qualidade ruim (prioridade máxima)
    if "cianobacterias" in chaves:
        return {
            "qualidade": "ruim",
            "descricao": "Cianobactérias detectadas — risco de toxicidade no sistema.",
            "cor": COR_SEMAFORO["vermelho"],
            "icon": "🔴",
            "acoes_recomendadas": [
                "Verificar origem do afluente e possível contaminação externa.",
                "Monitorar toxicidade e alertar equipe de operação.",
            ]
        }

    # Regra 2: Filamentos — bulking (qualidade ruim)
    if "filamentos" in chaves:
        return {
            "qualidade": "ruim",
            "descricao": "Filamentos detectados — risco de intumescimento (bulking filamentoso).",
            "cor": COR_SEMAFORO["vermelho"],
            "icon": "🔴",
            "acoes_recomendadas": [
                "Medir Índice Volumétrico do Lodo (IVL).",
                "Verificar OD no tanque — possível aeração insuficiente.",
                "Avaliar sobrecarga orgânica e pH.",
            ]
        }

    # Regra 3: Lodo disperso — sedimentação ruim
    if "flocos_dispersos" in chaves:
        return {
            "qualidade": "ruim",
            "descricao": "Lodo disperso (pin-point) — má sedimentação e efluente turvo.",
            "cor": COR_SEMAFORO["vermelho"],
            "icon": "🔴",
            "acoes_recomendadas": [
                "Verificar θc (idade do lodo) — pode estar muito baixa.",
                "Investigar presença de tóxicos no afluente.",
            ]
        }

    # Regra 4: Flagelados predominantes
    if "flagelados" in chaves or "vorticella_microstoma" in chaves:
        return {
            "qualidade": "ruim",
            "descricao": "Flagelados/Vorticella microstoma — indicadores de má depuração.",
            "cor": COR_SEMAFORO["vermelho"],
            "icon": "🔴",
            "acoes_recomendadas": [
                "Verificar OD no tanque de aeração.",
                "Reduzir carga orgânica ou aumentar tempo de aeração.",
            ]
        }

    # Regra 5: Protozoários livres / condição moderada
    chaves_moderadas = {
        "protozoa_livre", "nematoides", "trachelophyllum",
        "aelosoma", "rizopodes_amebas", "flagelados_rizopodes"
    }
    if chaves & chaves_moderadas:
        return {
            "qualidade": "moderada",
            "descricao": "Protozoários ou organismos de transição — sistema em equilíbrio instável.",
            "cor": COR_SEMAFORO["laranja"],
            "icon": "🔶",
            "acoes_recomendadas": [
                "Monitorar evolução dos organismos nas próximas análises.",
                "Verificar idade do lodo e parâmetros operacionais.",
            ]
        }

    # Regra 6: Demais (ciliados, rotíferos, arcella, aspidisca, flocos bons) → boa
    return {
        "qualidade": "boa",
        "descricao": "Organismos indicadores de sistema estável e boa depuração.",
        "cor": COR_SEMAFORO["verde"],
        "icon": "✅",
        "acoes_recomendadas": ["Manter parâmetros operacionais atuais."]
    }


# ──────────────────────────────────────────────────────────────────────────────
# 5. INTERFACE STREAMLIT — Seção de Microbiologia melhorada
# ──────────────────────────────────────────────────────────────────────────────

def render_microbiologia():
    st.markdown("---")
    st.header("🔬 Microbiologia do Lodo — Análise por IA (CETESB L1.025)")
    st.caption("Suba fotos ou vídeo do microscópio. A IA identifica os microrganismos e gera diagnóstico conforme a Norma Técnica L1.025.")

    # Badge indicando a metodologia
    st.info("🤖 **Análise baseada em IA + regras da CETESB** — Os resultados combinam visão computacional com a Tabela 6 da Norma L1.025.", icon="ℹ️")

    # --- Parâmetros do dia (contexto para a IA) ---
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
        st.caption("📋 Parâmetros do último registro (usados como contexto para a IA):")
        cols_p = st.columns(len(params_filtrados))
        for i, (k, v) in enumerate(params_filtrados.items()):
            cols_p[i].metric(k, v)

    # --- Modo de upload ---
    st.subheader("📸 Upload de Imagens ou Vídeo do Microscópio")

    modo = st.radio(
        "Como quer enviar?",
        ["📷 Fotos (JPG/PNG) — recomendado", "🎥 Vídeo (MP4/MOV) — requer ffmpeg"],
        horizontal=True
    )

    frames_b64 = []  # lista de imagens em base64 (já filtradas por qualidade)

    if "Fotos" in modo:
        imagens = st.file_uploader(
            "Selecione uma ou mais fotos do microscópio",
            type=["jpg", "jpeg", "png", "bmp", "tiff"],
            accept_multiple_files=True,
            help="Envie fotos tiradas pelo microscópio. O sistema seleciona automaticamente as mais nítidas (até 2)."
        )
        if imagens:
            todas_bytes = [img.read() for img in imagens]

            # Aplica filtro de qualidade — seleciona até 8 melhores
            with st.spinner("🔍 Selecionando frames mais nítidos..."):
                frames_b64 = _selecionar_melhores_imagens(todas_bytes, max_frames=2)

            st.caption(f"✅ {len(imagens)} imagem(ns) enviada(s) → {len(frames_b64)} selecionada(s) pela nitidez:")
            cols_prev = st.columns(min(len(frames_b64), 2))
            for i, b64 in enumerate(frames_b64):
                cols_prev[i % 2].image(base64.b64decode(b64), caption=f"Frame selecionado {i+1}", use_container_width=True)

    else:
        video_file = st.file_uploader(
            "Selecione o vídeo (.mp4, .mov, .avi, .webm)",
            type=["mp4", "mov", "avi", "webm", "mkv"],
            help="O sistema extrai automaticamente os frames mais nítidos (até 2)."
        )
        if video_file is not None:
            st.video(video_file)
            video_file.seek(0)
            video_bytes = video_file.read()
            st.caption(f"Tamanho: {len(video_bytes) / (1024*1024):.1f} MB")

            with st.spinner("🎞️ Extraindo e filtrando frames por nitidez (Laplaciano)..."):
                try:
                    frames_b64 = _extrair_frames_video(video_bytes, max_frames=2)
                    if frames_b64:
                        st.success(f"✅ {len(frames_b64)} frame(s) nítido(s) selecionado(s).")
                        cols_prev = st.columns(min(len(frames_b64), 2))
                        for i, b64 in enumerate(frames_b64):
                            cols_prev[i % 2].image(base64.b64decode(b64), caption=f"Frame nítido {i+1}", use_container_width=True)
                    else:
                        st.error("❌ Não foi possível extrair frames. Use o modo Fotos acima.")
                except Exception as e:
                    st.error(f"❌ ffmpeg não disponível neste servidor. Use o modo **Fotos** acima.\n\nDetalhes: {e}")

    # --- Botão de análise ---
    if frames_b64:
        analisar = st.button("🔬 Analisar com IA + Regras CETESB", type="primary", use_container_width=True)

        if analisar:
            if not GOOGLE_API_KEYS:
                st.error("❌ Nenhuma chave API Google configurada. Adicione GOOGLE_API_KEY (ou GOOGLE_API_KEY_1/2/3) nos Secrets do Streamlit.")
                st.stop()

            with st.status("Analisando frames selecionados...", expanded=True) as status_micro:
                try:
                    n_chaves = len(GOOGLE_API_KEYS)
                    st.write(f"🤖 Enviando {len(frames_b64)} frame(s) numa única requisição (economiza cota) — {n_chaves} chave(s) disponível(is)...")

                    # Envia TODOS os frames juntos numa única chamada à API
                    # Evita múltiplas requisições — importante com limite de 5 RPM
                    resultado_unico = _chamar_gemini_micro(
                        frames_b64, params_filtrados
                    )
                    resultados_por_frame = [resultado_unico] if not resultado_unico.get("_erro_parse") else []
                    resultado_consolidado = _agregar_resultados(resultados_por_frame)

                    # Aplica regras CETESB
                    st.write("📋 Aplicando regras CETESB L1.025...")
                    diagnostico_cetesb = aplicar_regras_cetesb(resultado_consolidado.get("organismos", []))

                    # Armazena resultado completo
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

    # ──────────────────────────────────────────────────────────────────────────
    # EXIBIÇÃO DOS RESULTADOS
    # ──────────────────────────────────────────────────────────────────────────
    resultado = st.session_state.get("micro_resultado")
    if not resultado:
        st.info("Faça upload de imagens do microscópio e clique em **Analisar** para ver o diagnóstico microbiológico.")
        return

    organismos     = resultado.get("organismos", [])
    qualidade      = resultado.get("qualidade_imagem", "regular")
    obs_gerais     = resultado.get("observacoes_gerais", "")
    confianca_med  = resultado.get("confianca_media", 0.0)
    n_analises     = resultado.get("n_analises", 1)
    diag_cetesb    = resultado.get("diagnostico_cetesb", {})

    # Avisos de qualidade de imagem
    if qualidade == "ruim":
        st.warning("⚠️ Qualidade das imagens baixa — resultados podem ser imprecisos. Tente com melhor iluminação/foco.")
    elif qualidade == "regular":
        st.info("ℹ️ Qualidade das imagens regular.")

    if obs_gerais:
        st.caption(f"🔎 {obs_gerais}")

    if not organismos:
        st.warning("Nenhum microrganismo identificado com as imagens fornecidas.")
        if st.button("🗑️ Limpar resultado e tentar novamente"):
            del st.session_state["micro_resultado"]
            st.rerun()
        return

    # ── Métricas de confiança e qualidade ──
    st.subheader("📊 Resumo da Análise")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🦠 Microrganismos", len(organismos))
    m2.metric("🎯 Confiança Média", f"{confianca_med * 100:.0f}%" if confianca_med else "—")
    m3.metric("📷 Qualidade da Imagem", qualidade.capitalize())
    m4.metric("🔬 Frames Analisados", n_analises)

    # ── Nível estimado da água (diagnóstico CETESB) ──
    st.subheader("💧 Qualidade Estimada do Lodo / Processo")
    nivel_cor   = diag_cetesb.get("cor", COR_SEMAFORO["cinza"])
    nivel_icon  = diag_cetesb.get("icon", "❓")
    nivel_qual  = diag_cetesb.get("qualidade", "—").upper()
    nivel_desc  = diag_cetesb.get("descricao", "")

    st.markdown(
        f"""<div style="background:{nivel_cor};border-radius:10px;padding:16px 20px;margin-bottom:12px;color:white;">
            <div style="font-size:22px;font-weight:700">{nivel_icon} Qualidade: {nivel_qual}</div>
            <div style="font-size:14px;margin-top:6px;opacity:0.92">{nivel_desc}</div>
            <div style="font-size:12px;margin-top:8px;opacity:0.8">
                📋 Análise baseada em IA + regras da CETESB L1.025
            </div>
        </div>""",
        unsafe_allow_html=True
    )

    # Ações recomendadas
    acoes = diag_cetesb.get("acoes_recomendadas", [])
    if acoes:
        with st.expander("⚡ Ações Recomendadas", expanded=True):
            for acao in acoes:
                st.markdown(f"• {acao}")

    # ── Lista detalhada de microrganismos detectados ──
    st.subheader(f"🦠 Microrganismos Detectados ({len(organismos)})")
    col_orgs = st.columns(2)
    for i, org in enumerate(organismos):
        with col_orgs[i % 2]:
            chave   = org.get("chave", "")
            meta    = MICRO_TABELA6.get(chave, {"semaforo": "cinza", "icon": "🔍", "condicao": "", "recomendacao": ""})
            cor     = COR_SEMAFORO.get(meta["semaforo"], COR_SEMAFORO["cinza"])
            conf    = org.get("confianca", 0.0)
            votos   = org.get("votos", 1)
            barra   = "█" * int(conf * 10) + "░" * (10 - int(conf * 10))

            st.markdown(
                f"""<div style="background:{cor};border-radius:8px;padding:12px 14px;margin-bottom:8px;color:white;">
                    <div style="font-size:15px;font-weight:500">{meta['icon']} {org.get('nome','—')}</div>
                    <div style="font-size:12px;opacity:0.9;margin-top:2px">{org.get('grupo','')}</div>
                    <div style="font-size:12px;margin-top:6px">{org.get('descricao','')}</div>
                    {f'<div style="font-size:11px;margin-top:4px;opacity:0.85">📋 {meta["condicao"]}</div>' if meta.get('condicao') else ''}
                    <div style="font-size:11px;margin-top:6px;opacity:0.8">
                        🎯 Confiança: {barra} {conf*100:.0f}%
                    </div>
                </div>""",
                unsafe_allow_html=True
            )

    # ── Diagnóstico detalhado pela Tabela 6 ──
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

    # ── Resumo copiável ──
    st.subheader("📝 Resumo para WhatsApp / Relatório")
    linhas = ["🔬 Análise Microbiológica do Lodo (CETESB L1.025):", ""]
    linhas.append(f"Qualidade do processo: {nivel_icon} {nivel_qual}")
    linhas.append(f"Confiança da análise: {confianca_med*100:.0f}%" if confianca_med else "")
    linhas.append("")
    linhas.append("Microrganismos identificados:")
    for org in organismos:
        conf_txt = f" (confiança: {org.get('confianca',0)*100:.0f}%)"
        linhas.append(f"• {org.get('nome','?')} ({org.get('grupo','')}){conf_txt}: {org.get('descricao','')}")
    linhas.append("")
    linhas.append("Diagnóstico:")
    for d in diag_vermelho + diag_laranja + diag_verde:
        linhas.append(f"{d['icon']} {d['condicao']}")
    if diag_vermelho:
        linhas.append("")
        linhas.append("⚠️ Ações urgentes:")
        for d in diag_vermelho:
            linhas.append(f"• {d['recomendacao']}")
    if acoes:
        linhas.append("")
        linhas.append("Ações recomendadas:")
        for acao in acoes:
            linhas.append(f"• {acao}")
    linhas.append("")
    linhas.append("📋 Análise baseada em IA + regras da CETESB L1.025")

    st.text_area("Copie o texto abaixo:", value="\n".join(l for l in linhas if l is not None),
                 height=220, label_visibility="collapsed", key="ta_micro_resumo_video")
    st.caption("Ctrl+A → Ctrl+C para copiar tudo.")

    if st.button("🗑️ Limpar e analisar novamente"):
        del st.session_state["micro_resultado"]
        st.rerun()
render_microbiologia()
f = df.drop_duplicates()
# =============================================================================
#   CORREÇÃO DE pH — CALCULADORA DE DOSAGEM  (substitui a função anterior)
#   Melhorias:
#     • Ácido Nítrico 53% adicionado (HNO₃)
#     • Cálculo por VOLUME DO TANQUE (modo batelada) e por VAZÃO CONTÍNUA
#     • Resposta direta: "Jogue X litros no tanque" ou "Dose Y L/h na linha"
#     • Jar test virtual: tabela de ensaio de bancada
#     • Custo estimado por dia
#     • Passo a passo simplificado para o operador
# =============================================================================

def render_correcao_ph():
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    import streamlit as st

    st.markdown("---")
    st.header("⚗️ Correção de pH — Calculadora de Dosagem")
    st.caption(
        "Informe os parâmetros do seu tanque ou linha e receba a resposta direta: "
        "**quanto produto jogar** para atingir o pH alvo."
    )

    # ── Reagentes disponíveis ─────────────────────────────────────────────────
    # Estrutura: nome_exibição -> {tipo, MM (g/mol), pureza (fração), densidade (g/mL), eq (equivalentes ácido-base)}
    REAGENTES = {
        "HNO₃ 53% (ácido nítrico — padrão da ETE)": {
            "tipo": "acido", "MM": 63.01, "pureza": 0.53,
            "densidade": 1.33, "eq": 1, "nome_curto": "HNO₃ 53%",
            "custo_ref": 3.50,   # R$/L — ajuste conforme contrato
            "cor": "#FF7043",
        },
        "H₂SO₄ 98% (ácido sulfúrico)": {
            "tipo": "acido", "MM": 98.08, "pureza": 0.98,
            "densidade": 1.84, "eq": 2, "nome_curto": "H₂SO₄ 98%",
            "custo_ref": 2.00,
            "cor": "#EF5350",
        },
        "HCl 33% (ácido clorídrico)": {
            "tipo": "acido", "MM": 36.46, "pureza": 0.33,
            "densidade": 1.16, "eq": 1, "nome_curto": "HCl 33%",
            "custo_ref": 4.00,
            "cor": "#AB47BC",
        },
        "NaOH 50% (soda cáustica líquida)": {
            "tipo": "base", "MM": 40.00, "pureza": 0.50,
            "densidade": 1.525, "eq": 1, "nome_curto": "NaOH 50%",
            "custo_ref": 3.00,
            "cor": "#42A5F5",
        },
        "NaOH 99% (soda cáustica sólida)": {
            "tipo": "base", "MM": 40.00, "pureza": 0.99,
            "densidade": 1.00, "eq": 1, "nome_curto": "NaOH 99%",
            "custo_ref": 8.00,
            "cor": "#26C6DA",
        },
        "Ca(OH)₂ (cal hidratada — pó 70%)": {
            "tipo": "base", "MM": 74.09, "pureza": 0.70,
            "densidade": 1.00, "eq": 2, "nome_curto": "Cal 70%",
            "custo_ref": 1.50,
            "cor": "#66BB6A",
        },
    }

    # ── Modo de operação ─────────────────────────────────────────────────────
    modo_op = st.radio(
        "Modo de operação",
        ["🪣 Batelada (corrigir um tanque cheio)", "🔄 Contínuo (linha / bomba dosadora)"],
        horizontal=True,
    )
    modo_batelada = "Batelada" in modo_op

    # ── Inputs principais ────────────────────────────────────────────────────
    col1, col2, col3 = st.columns(3)
    with col1:
        ph_atual = st.number_input("pH atual (medido)", min_value=0.0, max_value=14.0,
                                   value=8.5, step=0.1, format="%.1f")
    with col2:
        ph_alvo = st.number_input("pH alvo (desejado)", min_value=0.0, max_value=14.0,
                                  value=7.0, step=0.1, format="%.1f")
    with col3:
        if modo_batelada:
            volume_tanque = st.number_input("Volume do tanque (m³)", min_value=0.1,
                                            value=100.0, step=10.0)
            vazao = None
        else:
            vazao = st.number_input("Vazão da linha (m³/h)", min_value=0.01,
                                    value=10.0, step=0.5)
            volume_tanque = None

    col4, col5 = st.columns(2)
    with col4:
        reagente = st.selectbox("Reagente disponível", list(REAGENTES.keys()), index=0)
    with col5:
        alcalinidade = st.number_input(
            "Alcalinidade total (mg CaCO₃/L)",
            min_value=0.0, value=200.0, step=10.0,
            help=(
                "Alcalinidade medida no laboratório (ou estimada). "
                "Quanto maior, mais reagente ácido será necessário para vencer o tampão. "
                "Efluente doméstico típico: 150–350 mg/L."
            ),
        )

    # Opção avançada: fator de segurança
    with st.expander("⚙️ Configurações avançadas", expanded=False):
        fator_seg = st.slider(
            "Fator de segurança (multiplicador da dose calculada)",
            min_value=1.0, max_value=2.0, value=1.15, step=0.05,
            help="1.0 = dose exata teórica. 1.15 = +15% de margem (recomendado para primeira dosagem).",
        )
        eficiencia_mistura = st.slider(
            "Eficiência de mistura estimada (%)",
            min_value=50, max_value=100, value=85, step=5,
            help="100% = mistura perfeita. Tanques sem agitação ≈ 70–80%.",
        )
        custo_kwh = st.number_input("Custo da bomba dosadora (R$/h)", value=0.30, step=0.05,
                                    help="Para incluir no custo total estimado.")

    calcular = st.button("🧮 Calcular — quanto produto jogar?", type="primary", use_container_width=True)
    if not calcular:
        st.info("💡 Preencha os campos e clique em **Calcular** para receber a resposta direta.")
        return

    # ── Validações ───────────────────────────────────────────────────────────
    delta_ph = ph_alvo - ph_atual
    cfg = REAGENTES[reagente]

    if abs(delta_ph) < 0.05:
        st.success("✅ pH já está no alvo. Nenhuma ação necessária.")
        return

    precisa_acido = delta_ph < 0
    if precisa_acido and cfg["tipo"] == "base":
        st.error(
            "❌ Para **descer** o pH você precisa de um **ácido**, "
            "mas selecionou uma base. Mude o reagente."
        )
        return
    if not precisa_acido and cfg["tipo"] == "acido":
        st.error(
            "❌ Para **subir** o pH você precisa de uma **base**, "
            "mas selecionou um ácido. Mude o reagente."
        )
        return

    # ── Cálculo da demanda (meq/L) ───────────────────────────────────────────
    # Concentração do reagente em meq/mL
    conc_meq_mL = (cfg["pureza"] * cfg["densidade"] * 1000.0 / cfg["MM"]) * cfg["eq"]

    if cfg["tipo"] == "acido":
        # Demanda = neutralizar alcalinidade + ajuste de [H⁺]
        alc_meq_L = alcalinidade / 50.0          # 1 meq CaCO₃ = 50 mg
        h_alvo    = 10 ** (-ph_alvo)
        h_atual   = 10 ** (-ph_atual)
        demanda_meq_L = alc_meq_L + max((h_alvo - h_atual) * 1000.0, 0.0)
    else:
        # Para base: estimativa baseada no delta de pOH + fator tampão residual
        oh_alvo  = 10 ** (-(14.0 - ph_alvo))
        oh_atual = 10 ** (-(14.0 - ph_atual))
        fator_buffer = max(1.0 - alcalinidade / 600.0, 0.15)
        demanda_meq_L = max((oh_alvo - oh_atual) * 1000.0, 0.0) * fator_buffer + 0.08 * abs(delta_ph)

    # Aplica eficiência de mistura e fator de segurança
    efic = eficiencia_mistura / 100.0
    demanda_corrigida = demanda_meq_L / efic * fator_seg

    # ── Dose por litro de efluente (mL produto / L efluente) ────────────────
    dose_mL_por_L = demanda_corrigida / conc_meq_mL       # mL produto / L efluente
    dose_L_por_m3 = dose_mL_por_L                         # L produto / m³  (numericamente igual: 1 mL/L = 1 L/m³)

    # ── Resultado final ──────────────────────────────────────────────────────
    if modo_batelada:
        vol_produto_L  = dose_L_por_m3 * volume_tanque
        vol_produto_mL = vol_produto_L * 1000.0
        massa_produto_kg = vol_produto_L * cfg["densidade"] * cfg["pureza"]  # kg de reagente puro
    else:
        vazao_produto_L_h  = dose_L_por_m3 * vazao
        vazao_produto_mL_h = vazao_produto_L_h * 1000.0
        vol_dia_L          = vazao_produto_L_h * 24.0
        massa_dia_kg       = vol_dia_L * cfg["densidade"] * cfg["pureza"]

    # ── CAIXA DE RESPOSTA DIRETA (destaque máximo) ───────────────────────────
    st.markdown("---")
    acao_txt = "DESCER" if precisa_acido else "SUBIR"
    st.subheader(f"📣 Resposta Direta — para {acao_txt} o pH de {ph_atual} → {ph_alvo}")

    cor_destaque = cfg["cor"]

    if modo_batelada:
        st.markdown(
            f"""
<div style="background:{cor_destaque};border-radius:12px;padding:22px 26px;color:white;margin-bottom:16px;">
  <div style="font-size:13px;opacity:0.85;letter-spacing:1px;text-transform:uppercase">
    Tanque de {volume_tanque:.0f} m³ · {cfg['nome_curto']}
  </div>
  <div style="font-size:36px;font-weight:800;margin:8px 0">
    Adicione {vol_produto_L:.2f} L
    <span style="font-size:18px;opacity:0.85">({vol_produto_mL:.0f} mL)</span>
  </div>
  <div style="font-size:14px;opacity:0.9">
    ≈ {vol_produto_L / 1.0:.2f} L de produto comercial diretamente no tanque
    &nbsp;·&nbsp; misture bem por pelo menos 5 min antes de medir o pH novamente
  </div>
</div>
""",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
<div style="background:{cor_destaque};border-radius:12px;padding:22px 26px;color:white;margin-bottom:16px;">
  <div style="font-size:13px;opacity:0.85;letter-spacing:1px;text-transform:uppercase">
    Linha contínua · {vazao:.1f} m³/h · {cfg['nome_curto']}
  </div>
  <div style="font-size:36px;font-weight:800;margin:8px 0">
    Dosar {vazao_produto_L_h:.3f} L/h
    <span style="font-size:18px;opacity:0.85">({vazao_produto_mL_h:.1f} mL/h)</span>
  </div>
  <div style="font-size:14px;opacity:0.9">
    = {dose_L_por_m3 * 1000:.1f} mL por m³ tratado
    &nbsp;·&nbsp; configure a bomba dosadora para essa vazão
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

    # ── Métricas resumidas ───────────────────────────────────────────────────
    if modo_batelada:
        custo_total = vol_produto_L * cfg["custo_ref"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Volume a adicionar",    f"{vol_produto_L:.2f} L")
        c2.metric("Em mL",                 f"{vol_produto_mL:.0f} mL")
        c3.metric("Massa de reagente puro", f"{massa_produto_kg:.2f} kg")
        c4.metric("Custo estimado (produto)", f"R$ {custo_total:.2f}")
    else:
        custo_produto_dia = vol_dia_L * cfg["custo_ref"]
        custo_total_dia   = custo_produto_dia + custo_kwh * 24.0
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Vazão dosadora",      f"{vazao_produto_L_h:.3f} L/h")
        c2.metric("Dose por m³",         f"{dose_L_por_m3 * 1000:.1f} mL/m³")
        c3.metric("Volume / dia",        f"{vol_dia_L:.1f} L/dia")
        c4.metric("Custo total / dia",   f"R$ {custo_total_dia:.2f}/dia")

    # ── Passo a passo para o operador ────────────────────────────────────────
    st.markdown("---")
    st.subheader("🧑‍🔧 Passo a Passo para o Operador")

    if modo_batelada:
        passos = [
            f"1️⃣  Meça o pH atual com o pHmetro calibrado — confirme que está em **{ph_atual}**.",
            f"2️⃣  Use um recipiente graduado e separe **{vol_produto_L:.2f} L** de {cfg['nome_curto']}.",
            "3️⃣  Com EPI completo (luvas, óculos, avental), adicione o produto **lentamente** pela borda do tanque.",
            "4️⃣  Aguarde a mistura se homogeneizar (**mínimo 5 min** com aeração ligada).",
            f"5️⃣  Meça o pH novamente. O alvo é **{ph_alvo}**.",
            f"6️⃣  Se necessário, adicione mais **10–20%** (≈ {vol_produto_L * 0.15:.2f} L) e repita a medição.",
            "7️⃣  Registre pH final, volume total utilizado e horário no formulário.",
        ]
    else:
        passos = [
            f"1️⃣  Verifique o pH de entrada da linha — deve estar em torno de **{ph_atual}**.",
            f"2️⃣  Configure a bomba dosadora para **{vazao_produto_mL_h:.1f} mL/h** ({vazao_produto_L_h:.3f} L/h).",
            "3️⃣  Confirme que a linha de dosagem está antes do ponto de mistura e que há agitação adequada.",
            "4️⃣  Ligue a bomba e aguarde **10–15 min** para estabilizar.",
            f"5️⃣  Meça o pH na saída do ponto de mistura — deve estar próximo de **{ph_alvo}**.",
            "6️⃣  Ajuste a vazão da bomba em ±10% conforme necessário.",
            "7️⃣  Registre os valores no formulário de controle operacional.",
        ]

    for p in passos:
        st.markdown(p)

    # ── Jar test virtual (tabela de dosagens para testar em bancada) ─────────
    st.markdown("---")
    st.subheader("🧪 Jar Test Virtual — Tabela de Dosagens para Bancada")
    st.caption(
        "Antes de dosagem em escala real, simule em bancada com 1 L de amostra. "
        "A tabela mostra quanto produto adicionar para diferentes doses."
    )

    fatores = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
    jar_data = []
    for f in fatores:
        dose_1L_mL = dose_mL_por_L * f
        if dose_1L_mL < 1.0:
            dose_fmt = f"{dose_1L_mL * 1000:.1f} µL"
        else:
            dose_fmt = f"{dose_1L_mL:.2f} mL"
        jar_data.append({
            "Fator": f"× {f:.2f}",
            "Dose em 1 L de amostra": dose_fmt,
            "Dose em 10 L de amostra": f"{dose_1L_mL * 10:.2f} mL",
            f"Equivalente p/ {volume_tanque if modo_batelada else vazao} {'m³ (tanque)' if modo_batelada else 'm³/h (linha)'}":
                f"{dose_L_por_m3 * f * (volume_tanque if modo_batelada else vazao):.2f} L",
            "pH esperado (estimado)": f"~{min(max(ph_atual + delta_ph * f, 0), 14):.1f}",
        })

    st.dataframe(pd.DataFrame(jar_data), use_container_width=True, hide_index=True)
    st.caption(
        "💡 Dica: adicione a dose do fator 1.0 em 1 L de amostra, meça o pH após 2 min de agitação. "
        "Se ficou acima do alvo, use fator menor; se ficou abaixo, use fator maior."
    )

    # ── Gráfico: curva pH × dose ──────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📈 Curva pH × Dose (estimada)")

    doses = np.linspace(0, dose_L_por_m3 * 2.5 * 1000, 300)  # mL/m³
    phs_curva = []
    for d_mLm3 in doses:
        d_meq_L = (d_mLm3 / 1000.0) * conc_meq_mL
        if cfg["tipo"] == "acido":
            alc_rest = max(alcalinidade / 50.0 - d_meq_L, 0.0)
            if d_meq_L > alcalinidade / 50.0:
                h_exc = max((d_meq_L - alcalinidade / 50.0) * 1e-3, 1e-14)
                ph_est = -np.log10(h_exc)
            else:
                frac = d_meq_L / (alcalinidade / 50.0 + 1e-9)
                ph_est = ph_atual - abs(delta_ph) * (frac ** 0.55)
        else:
            demanda_total = max(demanda_meq_L, 1e-6)
            frac = min(d_meq_L / demanda_total, 1.0)
            ph_est = ph_atual + abs(delta_ph) * (frac ** 0.6)
        phs_curva.append(float(np.clip(ph_est, 0, 14)))

    dose_calc_mLm3 = dose_L_por_m3 * 1000.0

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=doses, y=phs_curva, mode="lines",
        line=dict(color=cfg["cor"], width=3), name="pH estimado"
    ))
    fig.add_hline(y=ph_alvo,  line_dash="dash", line_color="#43A047",
                  annotation_text=f"pH alvo ({ph_alvo})", annotation_position="right")
    fig.add_hline(y=ph_atual, line_dash="dot",  line_color="#9E9E9E",
                  annotation_text=f"pH atual ({ph_atual})", annotation_position="right")
    fig.add_vline(x=dose_calc_mLm3, line_dash="dash", line_color="#E53935",
                  annotation_text=f"Dose calculada ({dose_calc_mLm3:.1f} mL/m³)",
                  annotation_position="top left")

    fig.update_layout(
        xaxis_title=f"Dose de {cfg['nome_curto']} (mL por m³ de efluente)",
        yaxis_title="pH estimado",
        height=360,
        yaxis=dict(range=[0, 14]),
        margin=dict(l=10, r=10, t=20, b=10),
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True, key="plot-titulacao-ph-v2")

    # ── Alertas de segurança ──────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⚠️ Segurança e Cuidados")

    alertas_acido = {
        "HNO₃ 53% (ácido nítrico — padrão da ETE)": [
            "🔴 Ácido nítrico é **oxidante forte** — nunca misture com materiais orgânicos ou outros ácidos.",
            "🧤 EPI obrigatório: luvas nitrílicas duplas, óculos de proteção, avental impermeável.",
            "💨 Use em local ventilado — vapores de NO₂ são tóxicos.",
            "🚿 Em caso de contato com pele: lavar com água corrente por no mínimo 15 min.",
        ],
        "H₂SO₄ 98% (ácido sulfúrico)": [
            "🔴 Ácido sulfúrico concentrado é **extremamente corrosivo** e reage violentamente com água.",
            "🧤 EPI: luvas de borracha espessa, óculos de proteção, face shield.",
            "💧 SEMPRE adicione ácido na água, NUNCA água no ácido.",
        ],
        "HCl 33% (ácido clorídrico)": [
            "🟠 Libera vapores de HCl — use em área ventilada.",
            "🧤 EPI: luvas nitrílicas, óculos de proteção.",
        ],
        "NaOH 50% (soda cáustica líquida)": [
            "🟠 Soda cáustica causa queimaduras severas — evite contato com pele e olhos.",
            "🧤 EPI: luvas de borracha, óculos de proteção.",
        ],
        "NaOH 99% (soda cáustica sólida)": [
            "🟠 Sólido higroscópico — reage com umidade do ar liberando calor.",
            "🧤 EPI: luvas de borracha, máscara de pó.",
        ],
        "Ca(OH)₂ (cal hidratada — pó 70%)": [
            "🟡 Pó irritante para vias respiratórias — use máscara PFF2 ao manusear.",
            "🧤 EPI: luvas de borracha, óculos de proteção.",
        ],
    }
    for alerta in alertas_acido.get(reagente, []):
        st.markdown(alerta)

    # ── Resumo copiável ───────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📝 Resumo para WhatsApp / Relatório")

    if modo_batelada:
        resumo_linhas = [
            f"⚗️ Correção de pH — {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}",
            f"Tanque: {volume_tanque:.0f} m³  |  pH: {ph_atual} → {ph_alvo}",
            f"Reagente: {cfg['nome_curto']}",
            f"",
            f"✅ ADICIONAR: {vol_produto_L:.2f} L  ({vol_produto_mL:.0f} mL)",
            f"Massa de reagente puro: {massa_produto_kg:.2f} kg",
            f"Custo estimado: R$ {vol_produto_L * cfg['custo_ref']:.2f}",
            f"",
            f"Procedimento: adicionar lentamente com EPI, misturar por 5 min, medir pH.",
            f"Fator de segurança aplicado: {fator_seg:.0%}  |  Eficiência de mistura: {eficiencia_mistura}%",
        ]
    else:
        resumo_linhas = [
            f"⚗️ Correção de pH — {pd.Timestamp.now().strftime('%d/%m/%Y %H:%M')}",
            f"Linha: {vazao:.1f} m³/h  |  pH: {ph_atual} → {ph_alvo}",
            f"Reagente: {cfg['nome_curto']}",
            f"",
            f"✅ DOSAGEM: {vazao_produto_L_h:.3f} L/h  ({vazao_produto_mL_h:.1f} mL/h)",
            f"Dose por m³: {dose_L_por_m3 * 1000:.1f} mL/m³",
            f"Consumo diário: {vol_dia_L:.1f} L/dia",
            f"Custo produto/dia: R$ {custo_produto_dia:.2f}",
            f"",
            f"Configurar bomba dosadora para {vazao_produto_mL_h:.1f} mL/h.",
            f"Fator de segurança: {fator_seg:.0%}  |  Eficiência de mistura: {eficiencia_mistura}%",
        ]

    st.text_area(
        "",
        value="\n".join(resumo_linhas),
        height=200,
        label_visibility="collapsed",
        key="ta_ph_resumo_v2",
    )
    st.caption("Ctrl+A → Ctrl+C para copiar tudo.")
