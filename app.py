# app.py
# Requisitos: streamlit, pandas, openpyxl
# pip install streamlit pandas openpyxl

import io
import re
import math
import datetime as dt

import numpy as np
import pandas as pd
import streamlit as st

# =========================================================
# Configuração básica
# =========================================================
st.set_page_config(
    page_title="Regras por Cliente • R1, R2 e R3",
    page_icon="📊",
    layout="wide",
)
st.title("📊 Regras por Cliente • R1, R2 e R3")
st.caption("Envie a base e a MATRIZ ICMS (UF×UF). Depois defina, por cliente, a Regra (R1/R2/R3/—) e a UF de origem.")

# =========================================================
# Descrição das regras
# =========================================================
st.subheader("Descrição das regras e quando usar")
st.markdown("""
* **R1** — Lucro Real, origem SC  
  * Créditos (sobre o CUSTO): ICMS 12%, PIS 1,65%, COFINS 7,60%  
  * Débitos (sobre a VENDA): PIS 1,65%, COFINS 7,60%, ICMS interestadual da matriz (SC → destino)  
  * DIFAL (quando coluna DIFAL = COM): interna(destino) − interestadual(SC→destino), mínimo 0  
  * Frete: 6% da venda  
  * Aplicação: empresas do Lucro Real com origem SC.

* **R2** — Lucro Real, origem ES (empresas com COMPETE)  
  * Créditos (sobre o CUSTO): ICMS 0% (exceto destino ES = 7%), PIS 1,65% e COFINS 7,60%  
  * Débitos (sobre a VENDA): ICMS 1,14% (exceto destino ES = 17%), PIS 1,65%, COFINS 7,60%  
  * DIFAL (quando COM): interna(destino) − interestadual(ES→destino), mínimo 0  
  * Frete: 6% da venda  
  * Aplicação: empresas do Lucro Real com origem ES que tenham COMPETE.

* **R3** — Lucro Presumido, origem SC  
  * Créditos (sobre o CUSTO): ICMS 12%; sem crédito de PIS/COFINS  
  * Débitos (sobre a VENDA): PIS 0,65%, COFINS 3%, ICMS interestadual da matriz (SC → destino)  
  * DIFAL (quando COM): interna(destino) − interestadual(SC→destino), mínimo 0  
  * Frete: 6% da venda  
  * Aplicação: empresas do Lucro Presumido em SC.

**Observações gerais:**  
• O ICMS interestadual é lido da Matriz (UF origem em linhas, UF destino em colunas).  
• O DIFAL só aparece quando a coluna DIFAL for COM.  
• Status por linha: BOM se Lucro % ≥ 8%; RUIM caso contrário (linhas verdes e vermelhas, respectivamente).
""")

# =========================================================
# Colunas esperadas na base
# =========================================================
REQUIRED_COLS = [
    "CASO","ESFERA","ESTADO","DIFAL","MODELO","MARCA",
    "VALOR GANHO","CUSTO ATUAL","QUANTIDADE CARONA SALDO","CLIENTE"
]
RENAME_MAP = {
    "VALOR GANHO": "VALOR_VENDA",
    "CUSTO ATUAL": "CUSTO_ATUAL",
    "QUANTIDADE CARONA SALDO": "QTD_CARONA_SALDO",
}
NUMERIC_COLS = ["VALOR_VENDA","CUSTO_ATUAL","QTD_CARONA_SALDO"]

UFs = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MT","MS","MG","PA","PB","PR","PE","PI","RN","RS","RJ","RO","RR","SC","SP","SE","TO"]

# =========================================================
# Configuração de DIFAL por UF de destino
# =========================================================
DIFAL_CONFIG = {
    "AC": "COM", "AL": "COM", "AM": "COM", "AP": "COM", "BA": "SEM", "CE": "COM", "DF": "SEM",
    "ES": "SEM", "GO": "SEM", "MA": "SEM", "MT": "COM", "MS": "COM", "MG": "SEM", "PA": "COM",
    "PB": "COM", "PR": "SEM", "PE": "COM", "PI": "COM", "RJ": "COM", "RN": "COM", "RS": "SEM",
    "RO": "COM", "RR": "COM", "SC": "COM", "SP": "SEM", "SE": "SEM", "TO": "COM"
}

# =========================================================
# Parâmetros FIXOS (comuns)
# =========================================================
FRETE_PCT = 6.0           # % sobre VENDA
CRED_PIS_PCT = 1.65       # % sobre CUSTO (R1 e R2)
CRED_COFINS_PCT = 7.60    # % sobre CUSTO (R1 e R2)
DEB_PIS_PCT = 1.65        # % sobre VENDA (R1 e R2)
DEB_COFINS_PCT = 7.60     # % sobre VENDA (R1 e R2)

# R1
R1_CRED_ICMS_PCT = 12.0   # % sobre CUSTO
# ICMS débito da R1 vem da MATRIZ (interestadual origem→destino)

# R2 (clientes com origem ES)
R2_ORIGEM_FIXA = "ES"
R2_DEB_ICMS_PADRAO_PCT = 1.14     # % sobre VENDA (qualquer destino ≠ ES)
R2_DEB_ICMS_DEST_ES_PCT = 17.00   # % sobre VENDA (se destino = ES)
R2_CRED_ICMS_DEST_ES_PCT = 7.00   # % sobre CUSTO (se destino = ES); caso contrário 0%

# R3 (igual R1, mas sem crédito PIS/COFINS e com débitos PIS/COFINS reduzidos)
R3_DEB_PIS_PCT = 0.65     # % sobre VENDA
R3_DEB_COFINS_PCT = 3.00  # % sobre VENDA
# ICMS: crédito 12% sobre CUSTO (como R1) e débito pela MATRIZ (origem por cliente → destino)

# =========================================================
# Helpers
# =========================================================
def build_template_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "CASO": ["312909"],
            "ESFERA": ["Municipal"],
            "ESTADO": ["MG"],  # destino
            "DIFAL": ["COM"],  # COM/SEM
            "MODELO": ["6L C/Termômetro Digital"],
            "MARCA": ["MOR"],
            "VALOR GANHO": ["130"],
            "CUSTO ATUAL": ["100"],
            "QUANTIDADE CARONA SALDO": ["6"],
            "CLIENTE": ["AMENA CLIMATIZAÇÃO LTDA"],
        },
        columns=REQUIRED_COLS,
    )

def df_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "dados") -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    return output.getvalue()

def sanitize_colnames(cols):
    return [re.sub(r"\s+", " ", c).strip() for c in cols]

def to_number(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    s = str(value).strip()
    if s == "":
        return np.nan
    s = s.replace("R$", "").replace("%", "").replace(" ", "")
    s = re.sub(r"\.(?=\d{3}(\D|$))", "", s)  # separador milhar
    s = s.replace(",", ".")
    try:
        return float(s)
    except Exception:
        return np.nan

@st.cache_data(show_spinner=False)
def load_main_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file, dtype=str)
    else:
        df = pd.read_csv(uploaded_file, dtype=str, sep=None, engine="python")

    # normaliza cabeçalhos
    df.columns = sanitize_colnames(df.columns.tolist())

    # checa colunas obrigatórias
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}. Baixe o modelo na barra lateral.")

    # renomeia p/ nomes internos
    df = df.rename(columns=RENAME_MAP)

    # limpa espaços, normaliza números
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
    for c in NUMERIC_COLS:
        df[c] = df[c].apply(to_number)

    # padroniza texto
    if "DIFAL" in df.columns:
        df["DIFAL"] = df["DIFAL"].astype(str).str.upper().str.strip()
    if "ESTADO" in df.columns:
        df["ESTADO"] = df["ESTADO"].astype(str).str.upper().str.strip()

    return df

# ---- normalização robusta da matriz ICMS (%, fração ou formato Excel) ----
def _parse_rate_cell(x) -> float:
    """Converte célula da matriz para fração (0.12 = 12%).
       - Se tiver '%' no texto: divide por 100.
       - Senão, se valor <= 1.0: já é fração → mantém.
       - Senão (>1.0): assume pontos percentuais → divide por 100.
    """
    if pd.isna(x):
        return np.nan
    if isinstance(x, str):
        s = x.strip()
        if s == "":
            return np.nan
        if "%" in s:
            v = to_number(s.replace("%", ""))
            return v/100.0 if pd.notna(v) else np.nan
        v = to_number(s)
    else:
        try:
            v = float(x)
        except Exception:
            v = to_number(str(x))
    if pd.isna(v):
        return np.nan
    return v if v <= 1.0 else v/100.0

def load_icms_matrix(uploaded_file) -> pd.DataFrame:
    """
    MATRIZ ICMS UF×UF:
      - 1ª coluna = UF_ORIGEM (linhas = origem)
      - Cabeçalhos das colunas = UFs destino
      - Células podem vir '12,00%', 12, 0,12: normaliza para fração (0.12).
    """
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        m = pd.read_excel(uploaded_file, dtype=object)
    else:
        m = pd.read_csv(uploaded_file, dtype=object, sep=None, engine="python")

    # normaliza cabeçalhos
    m.columns = [str(c).strip().upper() for c in m.columns]

    # primeira coluna vira UF_ORIGEM
    first_col = m.columns[0]
    if first_col not in ["UF","ORIGEM","UF_ORIGEM"]:
        m = m.rename(columns={first_col: "UF_ORIGEM"})
    else:
        m = m.rename(columns={first_col: "UF_ORIGEM"})
    m["UF_ORIGEM"] = m["UF_ORIGEM"].astype(str).str.upper().str.strip()

    # mantém colunas de UFs conhecidas e linhas válidas
    keep = ["UF_ORIGEM"] + [c for c in m.columns if c in UFs]
    m = m[keep]
    m = m[m["UF_ORIGEM"].isin(UFs)]

    # converte células para fração
    for c in UFs:
        if c in m.columns:
            m[c] = m[c].apply(_parse_rate_cell)

    return m.reset_index(drop=True)

def get_interstate_rate(matrix: pd.DataFrame, uf_origem: str, uf_destino: str) -> float:
    """Interestadual: célula linha=origem, coluna=destino."""
    try:
        row = matrix.loc[matrix["UF_ORIGEM"] == uf_origem.upper(), uf_destino.upper()]
        if row.empty:
            return np.nan
        return float(row.values[0])
    except Exception:
        return np.nan

def get_internal_rate(matrix: pd.DataFrame, uf_destino: str) -> float:
    """Interna do destino: diagonal (linha=destino, coluna=destino)."""
    try:
        row = matrix.loc[matrix["UF_ORIGEM"] == uf_destino.upper(), uf_destino.upper()]
        if row.empty:
            return np.nan
        return float(row.values[0])
    except Exception:
        return np.nan

# =========================================================
# Sidebar: downloads + uploads
# =========================================================
st.sidebar.header("📥 Arquivos")
template_df = build_template_df()
st.sidebar.download_button(
    "⬇️ Modelo base (XLSX)",
    data=df_to_excel_bytes(template_df, "modelo"),
    file_name="modelo_base.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)
st.sidebar.download_button(
    "⬇️ Modelo base (CSV)",
    data=template_df.to_csv(index=False).encode("utf-8"),
    file_name="modelo_base.csv",
    mime="text/csv",
)

uploaded_main = st.sidebar.file_uploader("⬆️ Base principal (CSV/XLSX)", type=["csv","xlsx","xls"])
uploaded_icms = st.sidebar.file_uploader("⬆️ MATRIZ ICMS (UF×UF)", type=["csv","xlsx","xls"])

# UF padrão para novos clientes na configuração
default_origin = st.sidebar.selectbox("UF padrão ao criar clientes na tabela", UFs, index=UFs.index("SC") if "SC" in UFs else 0)

# =========================================================
# Fluxo principal
# =========================================================
if uploaded_main is None:
    st.info("Baixe o modelo, preencha e faça o upload da base principal para continuar.")
    st.dataframe(template_df, use_container_width=True)
    st.stop()

try:
    df_raw = load_main_file(uploaded_main)
except Exception as e:
    st.error(f"Erro ao carregar base: {e}")
    st.stop()

if uploaded_icms is None:
    st.error("Envie a **MATRIZ ICMS (UF×UF)** para calcular ICMS e DIFAL conforme as regras.")
    st.stop()

try:
    icms_matrix = load_icms_matrix(uploaded_icms)
    st.success("Matriz ICMS carregada.")
except Exception as e:
    st.error(f"Erro ao carregar Matriz ICMS: {e}")
    st.stop()

# =========================================================
# Configuração por CLIENTE (Regra e ORIGEM)
# =========================================================
st.subheader("⚙️ Configuração por CLIENTE (Regra e UF de origem)")
clientes_all = sorted(df_raw["CLIENTE"].dropna().unique().tolist())

def sync_client_cfg(clients):
    # cria/atualiza a tabela de configuração com base nos clientes da base
    if "client_cfg" not in st.session_state:
        st.session_state.client_cfg = pd.DataFrame({
            "CLIENTE": clients,
            "REGRA": ["R1"] * len(clients),        # padrão R1
            "ORIGEM": [default_origin] * len(clients),
        })
    else:
        cfg = st.session_state.client_cfg.copy()
        # adiciona clientes novos
        missing = [c for c in clients if c not in cfg["CLIENTE"].values]
        if missing:
            cfg = pd.concat([
                cfg,
                pd.DataFrame({"CLIENTE": missing, "REGRA": ["R1"]*len(missing), "ORIGEM": [default_origin]*len(missing)})
            ], ignore_index=True)
        # remove clientes que não estão na base
        cfg = cfg[cfg["CLIENTE"].isin(clients)].reset_index(drop=True)
        st.session_state.client_cfg = cfg

sync_client_cfg(clientes_all)

# editor da tabela (Regra: R1/R2/R3/—; Origem: UF)
client_cfg = st.data_editor(
    st.session_state.client_cfg,
    use_container_width=True,
    num_rows="dynamic",
    column_config={
        "REGRA": st.column_config.SelectboxColumn(
            "Regra", options=["R1","R2","R3","—"],
            help="R2 usa origem ES sempre; R1 e R3 usam a ORIGEM definida."
        ),
        "ORIGEM": st.column_config.SelectboxColumn(
            "UF de origem", options=UFs,
            help="Usada na R1 e na R3. Na R2 a origem é ES (forçada)."
        ),
        "CLIENTE": st.column_config.TextColumn("CLIENTE", disabled=True),
    },
    hide_index=True,
    key="client_cfg_editor",
)
st.session_state.client_cfg = client_cfg.copy()

st.caption("Dica: exporte essa tabela (menu ⋮ do Data Editor) para reutilizar as configurações.")

# =========================================================
# Filtros (mantidos)
# =========================================================
st.subheader("🔎 Filtros")
def ms(label, series):
    vals = sorted([v for v in series.dropna().unique().tolist() if str(v).strip() != ""])
    return st.multiselect(label, vals, default=[])

colA, colB, colC, colD = st.columns(4)
with colA:
    sel_esfera  = ms("ESFERA", df_raw["ESFERA"])
    sel_estado  = ms("ESTADO", df_raw["ESTADO"])
with colB:
    sel_difal   = ms("DIFAL",  df_raw["DIFAL"])
    sel_cliente = ms("CLIENTE",df_raw["CLIENTE"])
with colC:
    sel_marca   = ms("MARCA",  df_raw["MARCA"])
    sel_modelo  = ms("MODELO", df_raw["MODELO"])
with colD:
    sel_caso    = ms("CASO",   df_raw["CASO"])
    txt_busca   = st.text_input("Busca livre (MODELO/CLIENTE)", "")

df = df_raw.copy()
def apply_in(df, col, values):
    return df[df[col].isin(values)] if values else df

df = apply_in(df, "ESFERA", sel_esfera)
df = apply_in(df, "ESTADO", sel_estado)
df = apply_in(df, "DIFAL",  sel_difal)
df = apply_in(df, "CLIENTE",sel_cliente)
df = apply_in(df, "MARCA",  sel_marca)
df = apply_in(df, "MODELO", sel_modelo)
df = apply_in(df, "CASO",   sel_caso)

if txt_busca.strip():
    q = txt_busca.strip().lower()
    df = df[
        df["MODELO"].str.lower().str.contains(q, na=False)
        | df["CLIENTE"].str.lower().str.contains(q, na=False)
    ]

if df.empty:
    st.warning("Nenhum dado após aplicar os filtros.")
    st.stop()

# =========================================================
# Cálculo: R1, R2 e R3
# =========================================================
def compute_regras(df_in: pd.DataFrame, cfg_df: pd.DataFrame) -> pd.DataFrame:
    d = df_in.copy()
    d["VALOR_VENDA"] = d["VALOR_VENDA"].apply(to_number)
    d["CUSTO_ATUAL"] = d["CUSTO_ATUAL"].apply(to_number)
    d["QTD_CARONA_SALDO"] = d["QTD_CARONA_SALDO"].apply(to_number)

    # junta configuração por cliente
    cfg = cfg_df.copy()
    cfg["REGRA"] = cfg["REGRA"].fillna("R1").str.upper().str.strip()
    cfg["ORIGEM"] = cfg["ORIGEM"].fillna(default_origin).str.upper().str.strip()
    d = d.merge(cfg, on="CLIENTE", how="left")

    # flags
    d["_R2"] = d["REGRA"].eq("R2")
    d["_R1"] = d["REGRA"].eq("R1")
    d["_R3"] = d["REGRA"].eq("R3")
    d["REGRA_APLICADA"] = np.where(d["_R2"], "R2", np.where(d["_R3"], "R3", np.where(d["_R1"], "R1", "—")))

    # frações fixas
    cred_pis_f    = CRED_PIS_PCT/100.0
    cred_cofins_f = CRED_COFINS_PCT/100.0
    deb_pis_f     = DEB_PIS_PCT/100.0
    deb_cofins_f  = DEB_COFINS_PCT/100.0
    r1_cred_icms_f = R1_CRED_ICMS_PCT/100.0
    r2_deb_icms_padrao_f = R2_DEB_ICMS_PADRAO_PCT/100.0
    r2_deb_icms_dest_es_f = R2_DEB_ICMS_DEST_ES_PCT/100.0
    r2_cred_icms_dest_es_f = R2_CRED_ICMS_DEST_ES_PCT/100.0
    r3_deb_pis_f = R3_DEB_PIS_PCT/100.0
    r3_deb_cofins_f = R3_DEB_COFINS_PCT/100.0
    frete_f       = FRETE_PCT/100.0

    # ORIGEM usada por linha
    d["ORIGEM_USADA"] = np.where(d["_R2"], R2_ORIGEM_FIXA,
                           np.where(d["_R1"] | d["_R3"], d["ORIGEM"].fillna(""), ""))

    # taxas para DIFAL (matriz com a ORIGEM_USADA quando houver regra)
    d["ICMS_INTER_MATRIZ"] = d.apply(
        lambda r: get_interstate_rate(icms_matrix, r["ORIGEM_USADA"], r["ESTADO"]) if r["REGRA_APLICADA"] in ["R1","R2","R3"] else np.nan,
        axis=1
    )
    d["INTERNA_DEST"] = d["ESTADO"].apply(lambda uf_dest: get_internal_rate(icms_matrix, uf_dest))
    d["DIFAL_ALIQ"] = (d["INTERNA_DEST"] - d["ICMS_INTER_MATRIZ"]).clip(lower=0).fillna(0.0)
    aplica_difal = d["DIFAL"].str.upper().eq("COM") & d["REGRA_APLICADA"].isin(["R1","R2","R3"])

    # Créditos
    # - ICMS: R1 e R3 = 12% custo; R2 = 7% custo apenas se destino ES
    d["CRED_ICMS"] = np.where(
        d["_R1"] | d["_R3"], d["CUSTO_ATUAL"] * r1_cred_icms_f,
        np.where(d["_R2"] & d["ESTADO"].eq("ES"), d["CUSTO_ATUAL"] * r2_cred_icms_dest_es_f, 0.0)
    )
    # - PIS/COFINS: R1 e R2 têm crédito; R3 não tem
    d["CRED_PIS"] = np.where(d["_R1"] | d["_R2"], d["CUSTO_ATUAL"] * cred_pis_f, 0.0)
    d["CRED_COFINS"] = np.where(d["_R1"] | d["_R2"], d["CUSTO_ATUAL"] * cred_cofins_f, 0.0)

    # Débitos
    # - PIS/COFINS: R1/R2 padrão; R3 reduzidos
    d["DEB_PIS"] = np.where(d["_R3"], d["VALOR_VENDA"] * r3_deb_pis_f,
                      np.where(d["_R1"] | d["_R2"], d["VALOR_VENDA"] * deb_pis_f, 0.0))
    d["DEB_COFINS"] = np.where(d["_R3"], d["VALOR_VENDA"] * r3_deb_cofins_f,
                         np.where(d["_R1"] | d["_R2"], d["VALOR_VENDA"] * deb_cofins_f, 0.0))
    # - ICMS: R1 e R3 = matriz; R2 = regras próprias
    d["DEB_ICMS"] = np.where(
        d["_R1"] | d["_R3"], d["VALOR_VENDA"] * d["ICMS_INTER_MATRIZ"].fillna(0.0),
        np.where(d["_R2"] & d["ESTADO"].eq("ES"),
                 d["VALOR_VENDA"] * r2_deb_icms_dest_es_f,
                 np.where(d["_R2"],
                          d["VALOR_VENDA"] * r2_deb_icms_padrao_f,
                          0.0))
    )

    # DIFAL (só quando COM; NaN para não exibir quando SEM)
    d["DEB_DIFAL"] = np.where(aplica_difal, d["VALOR_VENDA"] * d["DIFAL_ALIQ"], np.nan)

    # Frete (só quando há regra aplicada)
    d["DEB_FRETE"] = np.where(d["REGRA_APLICADA"].isin(["R1","R2","R3"]), d["VALOR_VENDA"] * frete_f, 0.0)

    # Totais e Lucro Final
    d["TOTAL_CREDITOS"] = d[["CRED_ICMS","CRED_PIS","CRED_COFINS"]].sum(axis=1)
    d["TOTAL_DEBITOS"]  = d[["DEB_PIS","DEB_COFINS","DEB_ICMS","DEB_DIFAL","DEB_FRETE"]].sum(axis=1, skipna=True)
    d["LUCRO_FINAL_R$"] = (d["VALOR_VENDA"] - d["CUSTO_ATUAL"]) + (d["TOTAL_CREDITOS"] - d["TOTAL_DEBITOS"])
    d["LUCRO_FINAL_%"]  = np.where(d["VALOR_VENDA"]>0, d["LUCRO_FINAL_R$"]/d["VALOR_VENDA"]*100.0, np.nan)

    # Arredonda valores
    money_cols = [
        "CRED_ICMS","CRED_PIS","CRED_COFINS",
        "DEB_PIS","DEB_COFINS","DEB_ICMS","DEB_DIFAL","DEB_FRETE",
        "TOTAL_CREDITOS","TOTAL_DEBITOS","LUCRO_FINAL_R$"
    ]
    for c in money_cols:
        d[c] = d[c].round(2)
    d["LUCRO_FINAL_%"] = d["LUCRO_FINAL_%"].round(2)

    # STATUS por linha
    d["STATUS"] = np.where(d["LUCRO_FINAL_%"] >= 8, "BOM", "RUIM")

    # Margem bruta (%), referência
    d["MARGEM_BRUTA_%"] = np.where(
        (d["CUSTO_ATUAL"] > 0) & d["VALOR_VENDA"].notna() & d["CUSTO_ATUAL"].notna(),
        (d["VALOR_VENDA"]/d["CUSTO_ATUAL"] - 1.0)*100.0,
        np.nan
    ).round(2)

    return d

df_calc = compute_regras(df, st.session_state.client_cfg)

# =========================================================
# Visualização (sem alíquotas na tela)
# =========================================================
st.subheader("📄 Base calculada")
cols_show = [
    "REGRA_APLICADA","ORIGEM_USADA","CASO","CLIENTE","ESFERA","ESTADO","DIFAL","MARCA","MODELO",
    "VALOR_VENDA","CUSTO_ATUAL","QTD_CARONA_SALDO",
    "CRED_ICMS","CRED_PIS","CRED_COFINS",
    "DEB_PIS","DEB_COFINS","DEB_ICMS","DEB_DIFAL","DEB_FRETE",
    "TOTAL_CREDITOS","TOTAL_DEBITOS","LUCRO_FINAL_R$","LUCRO_FINAL_%","STATUS","MARGEM_BRUTA_%"
]
cols_show = [c for c in cols_show if c in df_calc.columns]

# Função para colorir linhas com base no STATUS
def color_status(row):
    if row['STATUS'] == 'BOM':
        return ['background-color: lightgreen'] * len(row)
    elif row['STATUS'] == 'RUIM':
        return ['background-color: lightcoral'] * len(row)
    else:
        return [''] * len(row)

styled_df = df_calc[cols_show].style.apply(color_status, axis=1)
st.dataframe(styled_df, use_container_width=True)

st.download_button(
    "💾 Baixar base calculada (XLSX)",
    data=df_to_excel_bytes(df_calc[cols_show], sheet_name="base_calculada"),
    file_name=f"base_calculada_{dt.date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

# =========================================================
# Mini Dashboard
# =========================================================
st.subheader("📈 Mini Dashboard: Quantidade por Modelo, Marca, Estado e Esfera (em %)")
with st.expander("Mostrar detalhes do dashboard"):
    if 'QTD_CARONA_SALDO' in df_calc.columns:
        total_qty = df_calc['QTD_CARONA_SALDO'].sum()
        
        # Agrupar por Modelo
        grouped_modelo = df_calc.groupby('MODELO')['QTD_CARONA_SALDO'].sum().reset_index()
        grouped_modelo['Percentual'] = (grouped_modelo['QTD_CARONA_SALDO'] / total_qty * 100).round(2)
        grouped_modelo = grouped_modelo.sort_values(by='Percentual', ascending=False)
        
        # Agrupar por Marca
        grouped_marca = df_calc.groupby('MARCA')['QTD_CARONA_SALDO'].sum().reset_index()
        grouped_marca['Percentual'] = (grouped_marca['QTD_CARONA_SALDO'] / total_qty * 100).round(2)
        grouped_marca = grouped_marca.sort_values(by='Percentual', ascending=False)
        
        # Agrupar por Estado
        grouped_estado = df_calc.groupby('ESTADO')['QTD_CARONA_SALDO'].sum().reset_index()
        grouped_estado['Percentual'] = (grouped_estado['QTD_CARONA_SALDO'] / total_qty * 100).round(2)
        grouped_estado = grouped_estado.sort_values(by='Percentual', ascending=False)
        
        # Agrupar por Esfera
        grouped_esfera = df_calc.groupby('ESFERA')['QTD_CARONA_SALDO'].sum().reset_index()
        grouped_esfera['Percentual'] = (grouped_esfera['QTD_CARONA_SALDO'] / total_qty * 100).round(2)
        grouped_esfera = grouped_esfera.sort_values(by='Percentual', ascending=False)
        
        # Maior quantidade
        grouped_all = df_calc.groupby(['MODELO', 'MARCA', 'ESTADO', 'ESFERA'])['QTD_CARONA_SALDO'].sum().reset_index()
        grouped_all['Percentual'] = (grouped_all['QTD_CARONA_SALDO'] / total_qty * 100).round(2)
        max_row = grouped_all.loc[grouped_all['Percentual'].idxmax()]
        max_description = f"Maior percentual: {max_row['MARCA']}, {max_row['MODELO']}, {max_row['ESTADO']}, {max_row['ESFERA']}: {max_row['Percentual']:.2f}%"

        # Exibir tabelas lado a lado
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown("**Por Modelo**")
            st.dataframe(grouped_modelo[['MODELO', 'Percentual']].style.format({'Percentual': '{:.2f}%'}), use_container_width=True)
        
        with col2:
            st.markdown("**Por Marca**")
            st.dataframe(grouped_marca[['MARCA', 'Percentual']].style.format({'Percentual': '{:.2f}%'}), use_container_width=True)
        
        with col3:
            st.markdown("**Por Estado**")
            st.dataframe(grouped_estado[['ESTADO', 'Percentual']].style.format({'Percentual': '{:.2f}%'}), use_container_width=True)
        
        with col4:
            st.markdown("**Por Esfera**")
            st.dataframe(grouped_esfera[['ESFERA', 'Percentual']].style.format({'Percentual': '{:.2f}%'}), use_container_width=True)
        
        st.markdown(f"**{max_description}**")
    else:
        st.warning("Coluna 'QTD_CARONA_SALDO' não encontrada para o dashboard.")

# Destaque geral (somente linhas com regra aplicada)
mask_aplicada = df_calc["REGRA_APLICADA"].isin(["R1","R2","R3"])
if mask_aplicada.any():
    venda_total  = float(df_calc.loc[mask_aplicada, "VALOR_VENDA"].sum())
    custo_total  = float(df_calc.loc[mask_aplicada, "CUSTO_ATUAL"].sum())
    tot_creditos = float(df_calc.loc[mask_aplicada, "TOTAL_CREDITOS"].sum())
    tot_debitos  = float(df_calc.loc[mask_aplicada, "TOTAL_DEBITOS"].sum())
    lucro_rs     = (venda_total - custo_total) + (tot_creditos - tot_debitos)
    lucro_pct    = (lucro_rs / venda_total * 100.0) if venda_total else float("nan")
    status_total = "BOM" if lucro_pct >= 8 else "RUIM"

    def br(x):
        try:
            return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return "-"

    st.success(
        f"📌 **Vendas**: R$ {br(venda_total)} • **Custos**: R$ {br(custo_total)} • "
        f"**Créditos**: R$ {br(tot_creditos)} • **Débitos**: R$ {br(tot_debitos)} • "
        f"**Lucro final**: R$ {br(lucro_rs)} (**{br(lucro_pct)}%**, {status_total})"
    )

# =========================================================
# Simulações por linha
# =========================================================
st.subheader("🧪 Simulações por Linha (todos os estados)")
for i, row in df_calc.iterrows():
    with st.expander(f"{row['CASO']} | {row['CLIENTE']} | {row['MODELO']} | Original: {row['ESTADO']} | Lucro: {row['LUCRO_FINAL_%']}% ({row['STATUS']})"):
        if st.button("Simular para todos os estados", key=f"sim_btn_{i}"):
            fixed_cols = ['CASO', 'ESFERA', 'MODELO', 'MARCA', 'VALOR_VENDA', 'CUSTO_ATUAL', 'QTD_CARONA_SALDO', 'CLIENTE']
            fixed = row[fixed_cols].to_dict()
            sim_rows = []
            for uf in UFs:
                sim = fixed.copy()
                sim['ESTADO'] = uf
                sim['DIFAL'] = DIFAL_CONFIG.get(uf, 'COM')  # Aplica DIFAL conforme configuração
                sim_rows.append(sim)
            df_sim = pd.DataFrame(sim_rows)
            df_sim_calc = compute_regras(df_sim, st.session_state.client_cfg)
            styled_sim = df_sim_calc[cols_show].style.apply(color_status, axis=1)
            st.dataframe(styled_sim, use_container_width=True)
