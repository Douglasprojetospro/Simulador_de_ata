# app.py — Regras por cliente + Simulação + Percentuais (organizado)
# Requisitos: streamlit, pandas, openpyxl
# Instalar: .\venv\Scripts\python.exe -m pip install -U streamlit pandas openpyxl

import io, re, math, sys, datetime as dt
import numpy as np
import pandas as pd
import streamlit as st

# =========================================
# Config de página
# =========================================
st.set_page_config(
    page_title="Regras por Cliente • Original + Simulação + Percentuais",
    page_icon="📊",
    layout="wide"
)
st.title("📊 Regras por Cliente — Original + Simulação por UFs + Relatório Percentual")
st.caption("Envie a Base e a Matriz ICMS, configure **Regra/Origem por CLIENTE**, veja os resultados com cores e baixe um Excel com **6 abas**.")

# =========================================
# Constantes / Parâmetros
# =========================================
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

# DIFAL por UF destino
DIFAL_CONFIG = {
    "AC": "COM", "AL": "COM", "AM": "COM", "AP": "COM", "BA": "SEM", "CE": "COM", "DF": "SEM",
    "ES": "SEM", "GO": "SEM", "MA": "SEM", "MT": "COM", "MS": "COM", "MG": "SEM", "PA": "COM",
    "PB": "COM", "PR": "SEM", "PE": "COM", "PI": "COM", "RJ": "COM", "RN": "COM", "RS": "SEM",
    "RO": "COM", "RR": "COM", "SC": "COM", "SP": "SEM", "SE": "SEM", "TO": "COM"
}

# Parâmetros fiscais
FRETE_PCT = 6.0
CRED_PIS_PCT,  CRED_COFINS_PCT = 1.65, 7.60
DEB_PIS_PCT,   DEB_COFINS_PCT  = 1.65, 7.60

# R1 — Lucro Real (origem definida por cliente; tipicamente SC)
R1_CRED_ICMS_PCT = 12.0

# R2 — Lucro Real, origem ES (COMPETE)
R2_ORIGEM_FIXA = "ES"
R2_DEB_ICMS_PADRAO_PCT = 1.14      # % venda (destino ≠ ES)
R2_DEB_ICMS_DEST_ES_PCT = 17.00    # % venda (destino ES)
R2_CRED_ICMS_DEST_ES_PCT = 7.00    # % custo (destino ES); caso contrário 0%

# R3 — Lucro Presumido (origem definida por cliente; tipicamente SC)
R3_DEB_PIS_PCT, R3_DEB_COFINS_PCT = 0.65, 3.00  # % venda
R3_CRED_ICMS_PCT = 12.0                         # % custo (sem crédito PIS/COFINS)

# =========================================
# Utils
# =========================================
def sanitize_colnames(cols):
    return [re.sub(r"\s+", " ", c).strip() for c in cols]

def to_number(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    s = str(value).strip()
    if s == "": return np.nan
    s = s.replace("R$", "").replace("%", "").replace(" ", "")
    s = re.sub(r"\.(?=\d{3}(\D|$))", "", s)  # milhar
    s = s.replace(",", ".")
    try: return float(s)
    except Exception: return np.nan

def _parse_rate_cell(x) -> float:
    if pd.isna(x): return np.nan
    if isinstance(x, str):
        s = x.strip()
        if s == "": return np.nan
        if "%" in s:
            v = to_number(s.replace("%",""))
            return v/100.0 if pd.notna(v) else np.nan
        v = to_number(s)
    else:
        try: v = float(x)
        except Exception: v = to_number(str(x))
    if pd.isna(v): return np.nan
    return v if v <= 1.0 else v/100.0

def get_interstate_rate(matrix: pd.DataFrame, uf_origem: str, uf_destino: str) -> float:
    try:
        row = matrix.loc[matrix["UF_ORIGEM"] == uf_origem.upper(), uf_destino.upper()]
        if row.empty: return np.nan
        return float(row.values[0])
    except Exception:
        return np.nan

def get_internal_rate(matrix: pd.DataFrame, uf_destino: str) -> float:
    try:
        row = matrix.loc[matrix["UF_ORIGEM"] == uf_destino.upper(), uf_destino.upper()]
        if row.empty: return np.nan
        return float(row.values[0])
    except Exception:
        return np.nan

def df_to_excel_bytes_multi(dfs_by_sheet: dict) -> bytes:
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet, df in dfs_by_sheet.items():
            safe = (sheet or "Sheet1")[:31]
            df.to_excel(writer, index=False, sheet_name=safe)
    return output.getvalue()

# =========================================
# Carregamento de arquivos
# =========================================
@st.cache_data(show_spinner=False)
def load_main_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file, dtype=str)
    else:
        df = pd.read_csv(uploaded_file, dtype=str, sep=None, engine="python")
    df.columns = sanitize_colnames(df.columns.tolist())

    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(f"Colunas ausentes: {', '.join(missing)}.")

    df = df.rename(columns=RENAME_MAP)
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip()
    for c in NUMERIC_COLS:
        df[c] = df[c].apply(to_number)
    if "DIFAL" in df.columns:  df["DIFAL"] = df["DIFAL"].str.upper().str.strip()
    if "ESTADO" in df.columns: df["ESTADO"] = df["ESTADO"].str.upper().str.strip()
    return df

@st.cache_data(show_spinner=False)
def load_icms_matrix(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()
    if name.endswith((".xlsx", ".xls")):
        m = pd.read_excel(uploaded_file, dtype=object)
    else:
        m = pd.read_csv(uploaded_file, dtype=object, sep=None, engine="python")
    m.columns = [str(c).strip().upper() for c in m.columns]
    first = m.columns[0]
    m = m.rename(columns={first: "UF_ORIGEM"})
    m["UF_ORIGEM"] = m["UF_ORIGEM"].astype(str).str.upper().str.strip()
    keep = ["UF_ORIGEM"] + [c for c in m.columns if c in UFs]
    m = m[keep]
    m = m[m["UF_ORIGEM"].isin(UFs)]
    for c in UFs:
        if c in m.columns:
            m[c] = m[c].apply(_parse_rate_cell)
    return m.reset_index(drop=True)

# =========================================
# Cálculo com regras por CLIENTE
# =========================================
def compute_regras(df_in: pd.DataFrame, cfg_df: pd.DataFrame, icms_matrix: pd.DataFrame, default_origin: str) -> pd.DataFrame:
    d = df_in.copy()

    for c in ["VALOR_VENDA","CUSTO_ATUAL","QTD_CARONA_SALDO"]:
        if c in d.columns:
            d[c] = d[c].apply(to_number)

    # merge config cliente
    cfg = cfg_df.copy()
    cfg["REGRA"]  = cfg["REGRA"].fillna("R1").str.upper().str.strip()
    cfg["ORIGEM"] = cfg["ORIGEM"].fillna(default_origin).str.upper().str.strip()
    d = d.merge(cfg, on="CLIENTE", how="left")

    # flags
    d["_R1"] = d["REGRA"].eq("R1")
    d["_R2"] = d["REGRA"].eq("R2")
    d["_R3"] = d["REGRA"].eq("R3")
    d["REGRA_APLICADA"] = np.where(d["_R2"], "R2",
                            np.where(d["_R3"], "R3",
                                     np.where(d["_R1"], "R1", "—")))
    # origem usada
    d["ORIGEM_USADA"] = np.where(
        d["_R2"], R2_ORIGEM_FIXA,
        np.where(d["_R1"] | d["_R3"], d["ORIGEM"].fillna(default_origin), "")
    )

    # frações
    cred_pis_f, cred_cofins_f = CRED_PIS_PCT/100.0, CRED_COFINS_PCT/100.0
    deb_pis_f,  deb_cofins_f  = DEB_PIS_PCT/100.0,  DEB_COFINS_PCT/100.0
    r1_cred_icms_f = R1_CRED_ICMS_PCT/100.0
    r3_cred_icms_f = R3_CRED_ICMS_PCT/100.0
    r2_deb_icms_padrao_f = R2_DEB_ICMS_PADRAO_PCT/100.0
    r2_deb_icms_es_f     = R2_DEB_ICMS_DEST_ES_PCT/100.0
    r2_cred_icms_es_f    = R2_CRED_ICMS_DEST_ES_PCT/100.0
    r3_deb_pis_f, r3_deb_cofins_f = R3_DEB_PIS_PCT/100.0, R3_DEB_COFINS_PCT/100.0
    frete_f = FRETE_PCT/100.0

    # ICMS Matriz
    d["ICMS_INTER_MATRIZ"] = d.apply(
        lambda r: get_interstate_rate(icms_matrix, r["ORIGEM_USADA"], r["ESTADO"]) if r["REGRA_APLICADA"] in ["R1","R2","R3"] else np.nan,
        axis=1
    )
    d["INTERNA_DEST"] = d["ESTADO"].apply(lambda uf: get_internal_rate(icms_matrix, uf))
    d["DIFAL_ALIQ"] = (d["INTERNA_DEST"] - d["ICMS_INTER_MATRIZ"]).clip(lower=0).fillna(0.0)
    aplica_difal = d["DIFAL"].str.upper().eq("COM") & d["REGRA_APLICADA"].isin(["R1","R2","R3"])

    # Créditos
    d["CRED_ICMS"] = np.select(
        condlist=[
            d["_R1"],                          # R1: 12% custo
            d["_R3"],                          # R3: 12% custo
            d["_R2"] & d["ESTADO"].eq("ES"),   # R2: 7% custo se destino ES
            d["_R2"]                           # R2 demais: 0%
        ],
        choicelist=[
            d["CUSTO_ATUAL"] * r1_cred_icms_f,
            d["CUSTO_ATUAL"] * r3_cred_icms_f,
            d["CUSTO_ATUAL"] * r2_cred_icms_es_f,
            0.0
        ],
        default=0.0
    )
    d["CRED_PIS"]    = np.where(d["_R1"] | d["_R2"], d["CUSTO_ATUAL"] * cred_pis_f, 0.0)
    d["CRED_COFINS"] = np.where(d["_R1"] | d["_R2"], d["CUSTO_ATUAL"] * cred_cofins_f, 0.0)

    # Débitos
    d["DEB_PIS"] = np.where(d["_R3"], d["VALOR_VENDA"] * r3_deb_pis_f,
                      np.where(d["_R1"] | d["_R2"], d["VALOR_VENDA"] * deb_pis_f, 0.0))
    d["DEB_COFINS"] = np.where(d["_R3"], d["VALOR_VENDA"] * r3_deb_cofins_f,
                         np.where(d["_R1"] | d["_R2"], d["VALOR_VENDA"] * deb_cofins_f, 0.0))
    d["DEB_ICMS"] = np.select(
        condlist=[
            d["_R1"] | d["_R3"],             # R1/R3: ICMS matriz
            d["_R2"] & d["ESTADO"].eq("ES"), # R2 destino ES: 17%
            d["_R2"]                         # R2 demais: 1,14%
        ],
        choicelist=[
            d["VALOR_VENDA"] * d["ICMS_INTER_MATRIZ"].fillna(0.0),
            d["VALOR_VENDA"] * r2_deb_icms_es_f,
            d["VALOR_VENDA"] * r2_deb_icms_padrao_f
        ],
        default=0.0
    )

    # DIFAL & Frete
    d["DEB_DIFAL"] = np.where(aplica_difal, d["VALOR_VENDA"] * d["DIFAL_ALIQ"], np.nan)
    d["DEB_FRETE"] = np.where(d["REGRA_APLICADA"].isin(["R1","R2","R3"]), d["VALOR_VENDA"] * frete_f, 0.0)

    # Totais, lucro, status
    d["TOTAL_CREDITOS"] = d[["CRED_ICMS","CRED_PIS","CRED_COFINS"]].sum(axis=1)
    d["TOTAL_DEBITOS"]  = d[["DEB_PIS","DEB_COFINS","DEB_ICMS","DEB_DIFAL","DEB_FRETE"]].sum(axis=1, skipna=True)
    d["LUCRO_FINAL_R$"] = (d["VALOR_VENDA"] - d["CUSTO_ATUAL"]) + (d["TOTAL_CREDITOS"] - d["TOTAL_DEBITOS"])
    d["LUCRO_FINAL_%"]  = np.where(d["VALOR_VENDA"]>0, d["LUCRO_FINAL_R$"]/d["VALOR_VENDA"]*100.0, np.nan)

    money_cols = ["CRED_ICMS","CRED_PIS","CRED_COFINS","DEB_PIS","DEB_COFINS","DEB_ICMS",
                  "DEB_DIFAL","DEB_FRETE","TOTAL_CREDITOS","TOTAL_DEBITOS","LUCRO_FINAL_R$"]
    for c in money_cols: d[c] = d[c].round(2)
    d["LUCRO_FINAL_%"] = d["LUCRO_FINAL_%"].round(2)
    d["STATUS"] = np.where(d["LUCRO_FINAL_%"] >= 8, "BOM", "RUIM")

    d["MARGEM_BRUTA_%"] = np.where(
        (d["CUSTO_ATUAL"] > 0) & d["VALOR_VENDA"].notna() & d["CUSTO_ATUAL"].notna(),
        (d["VALOR_VENDA"]/d["CUSTO_ATUAL"] - 1.0)*100.0,
        np.nan
    ).round(2)

    return d

# ========== Estilo (cores BOM/RUIM) ==========
GREEN = "#d6f5d6"  # verde claro
RED   = "#ffd6d6"  # vermelho claro
def color_status(row):
    if "STATUS" not in row:
        return [''] * len(row)
    return ([f'background-color: {GREEN}'] * len(row)) if row["STATUS"] == "BOM" \
           else ([f'background-color: {RED}'] * len(row) if row["STATUS"] == "RUIM" else [''] * len(row))

# =========================================
# Sidebar — Uploads e opções
# =========================================
st.sidebar.header("📥 Arquivos")

def build_template_df():
    return pd.DataFrame({
        "CASO": ["312909"],
        "ESFERA": ["Municipal"],
        "ESTADO": ["MG"],
        "DIFAL": ["COM"],
        "MODELO": ["6L C/Termômetro Digital"],
        "MARCA": ["MOR"],
        "VALOR GANHO": ["130"],
        "CUSTO ATUAL": ["100"],
        "QUANTIDADE CARONA SALDO": ["6"],
        "CLIENTE": ["AMENA CLIMATIZAÇÃO LTDA"],
    }, columns=REQUIRED_COLS)

template_df = build_template_df()
st.sidebar.download_button(
    "⬇️ Baixar Modelo (XLSX)",
    data=df_to_excel_bytes_multi({"modelo": template_df}),
    file_name="modelo_base.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

uploaded_main = st.sidebar.file_uploader("Base principal (CSV/XLSX)", type=["csv","xlsx","xls"])
uploaded_icms = st.sidebar.file_uploader("Matriz ICMS (UF×UF) (CSV/XLSX)", type=["csv","xlsx","xls"])
default_origin = st.sidebar.selectbox("UF de origem padrão (p/ R1 e R3)", UFs, index=UFs.index("SC") if "SC" in UFs else 0)

# =========================================
# Fluxo inicial
# =========================================
if uploaded_main is None:
    st.info("Envie a **Base principal** na barra lateral. Você pode baixar um modelo acima.")
    st.dataframe(template_df, width="stretch")
    st.stop()

try:
    df_base = load_main_file(uploaded_main)
except Exception as e:
    st.error(f"Erro ao carregar base: {e}")
    st.stop()

if uploaded_icms is None:
    st.warning("Envie também a **Matriz ICMS (UF×UF)** para continuar.")
    st.stop()

try:
    icms_matrix = load_icms_matrix(uploaded_icms)
except Exception as e:
    st.error(f"Erro ao carregar Matriz ICMS: {e}")
    st.stop()

# =========================================
# TABS: Análise | Configuração | Regras
# =========================================
tab_analise, tab_cfg, tab_regras = st.tabs(["📄 Análise", "⚙️ Configuração por CLIENTE", "ℹ️ Regras (descrição)"])

# ---------- Configuração por CLIENTE (organizada) ----------
with tab_cfg:
    st.subheader("⚙️ Configuração por CLIENTE")
    st.caption("Em **R2**, a origem é sempre **ES** (COMPETE). Em **R1/R3**, usa a **UF de origem** definida abaixo.")
    clientes_all = sorted(df_base["CLIENTE"].dropna().unique().tolist())

    def sync_client_cfg(clients):
        if "client_cfg" not in st.session_state:
            st.session_state.client_cfg = pd.DataFrame({
                "CLIENTE": clients,
                "REGRA": ["R1"] * len(clients),
                "ORIGEM": [default_origin] * len(clients),
            })
        else:
            cfg = st.session_state.client_cfg.copy()
            novos = [c for c in clients if c not in cfg["CLIENTE"].values]
            if novos:
                cfg = pd.concat([cfg, pd.DataFrame({"CLIENTE": novos, "REGRA": ["R1"]*len(novos), "ORIGEM": [default_origin]*len(novos)})], ignore_index=True)
            cfg = cfg[cfg["CLIENTE"].isin(clients)].reset_index(drop=True)
            st.session_state.client_cfg = cfg

    sync_client_cfg(clientes_all)

    colA, colB = st.columns([2, 1])
    with colA:
        st.markdown("**Edite a tabela (menu ⋮ permite exportar CSV):**")
        st.session_state.client_cfg = st.data_editor(
            st.session_state.client_cfg,
            num_rows="dynamic",
            hide_index=True,
            column_config={
                "CLIENTE": st.column_config.TextColumn("CLIENTE", disabled=True),
                "REGRA": st.column_config.SelectboxColumn("Regra", options=["R1","R2","R3"]),
                "ORIGEM": st.column_config.SelectboxColumn("UF origem (R1/R3)", options=UFs),
            },
            key="client_cfg_editor",
        )
    with colB:
        st.markdown("**Ações em massa**")
        regra_bulk = st.selectbox("Regra p/ TODOS", ["— (não alterar)","R1","R2","R3"])
        origem_bulk = st.selectbox("Origem p/ TODOS (R1/R3)", ["— (não alterar)"] + UFs)
        if st.button("Aplicar"):
            cfg = st.session_state.client_cfg.copy()
            if regra_bulk != "— (não alterar)":
                cfg["REGRA"] = regra_bulk
            if origem_bulk != "— (não alterar)":
                cfg["ORIGEM"] = origem_bulk
            st.session_state.client_cfg = cfg
            st.success("Configuração aplicada.")

        st.markdown("---")
        st.download_button("⬇️ Exportar configuração (CSV)",
                           data=st.session_state.client_cfg.to_csv(index=False).encode("utf-8"),
                           file_name="config_clientes.csv",
                           mime="text/csv")
        cfg_up = st.file_uploader("⬆️ Importar configuração (CSV)", type=["csv"])
        if cfg_up is not None:
            try:
                cfg_csv = pd.read_csv(cfg_up, dtype=str)
                for c in ["CLIENTE","REGRA","ORIGEM"]:
                    if c not in cfg_csv.columns:
                        raise ValueError("CSV deve conter colunas CLIENTE, REGRA, ORIGEM.")
                    cfg_csv[c] = cfg_csv[c].astype(str).str.strip().str.upper()
                cfg_csv = cfg_csv[cfg_csv["CLIENTE"].isin(clientes_all)]
                if cfg_csv.empty:
                    st.warning("Nenhum CLIENTE do CSV coincide com a base atual.")
                else:
                    st.session_state.client_cfg = cfg_csv.reset_index(drop=True)
                    st.success("Configuração importada.")
            except Exception as e:
                st.error(f"Erro ao importar configuração: {e}")

# ---------- Regras (descrição) ----------
with tab_regras:
    st.subheader("ℹ️ Descrição das Regras e quando usar")
    st.markdown("""
**R1 — Lucro Real, origem (ex.: SC)**  
• **Créditos (CUSTO):** ICMS **12%**, PIS **1,65%**, COFINS **7,60%**  
• **Débitos (VENDA):** PIS **1,65%**, COFINS **7,60%**, **ICMS interestadual** da Matriz (origem→destino)  
• **DIFAL (quando `DIFAL = COM`):** interna(destino) − interestadual(origem→destino), mínimo 0  
• **Frete:** 6% da venda  
• **Aplicação:** empresas do **Lucro Real** com origem (tipicamente **SC**).

**R2 — Lucro Real, origem ES (COMPETE)**  
• **Créditos (CUSTO):** ICMS **0%** (*exceto destino ES = 7%*), PIS **1,65%**, COFINS **7,60%**  
• **Débitos (VENDA):** ICMS **1,14%** (*exceto destino ES = 17%*), PIS **1,65%**, COFINS **7,60%**  
• **DIFAL (quando `DIFAL = COM`):** interna(destino) − interestadual(**ES**→destino), mínimo 0  
• **Frete:** 6% da venda  
• **Aplicação:** empresas do **Lucro Real** com origem **ES** e **COMPETE**.

**R3 — Lucro Presumido, origem (ex.: SC)**  
• **Créditos (CUSTO):** ICMS **12%**; **sem** crédito de PIS/COFINS  
• **Débitos (VENDA):** PIS **0,65%**, COFINS **3%**, **ICMS interestadual** da Matriz (origem→destino)  
• **DIFAL (quando `DIFAL = COM`):** interna(destino) − interestadual(origem→destino), mínimo 0  
• **Frete:** 6% da venda  
• **Aplicação:** empresas do **Lucro Presumido** (tipicamente **SC**).

**Observações gerais**  
• O **ICMS interestadual** é lido da **Matriz** (linhas = UF origem; colunas = UF destino).  
• O **DIFAL** só aparece quando a coluna **DIFAL = COM**.  
• **STATUS** por linha: **BOM** se **Lucro % ≥ 8%**; caso contrário **RUIM** (cores em tela).
""")

# =========================================
# Cálculos (usando config atual)
# =========================================
cfg_df = st.session_state.get("client_cfg")
if cfg_df is None:
    cfg_df = pd.DataFrame({
        "CLIENTE": sorted(df_base["CLIENTE"].dropna().unique().tolist()),
        "REGRA": ["R1"] * df_base["CLIENTE"].nunique(),
        "ORIGEM": [default_origin] * df_base["CLIENTE"].nunique(),
    })

# Original
df_result = compute_regras(df_base, cfg_df, icms_matrix, default_origin)
cols_original = [
    "REGRA_APLICADA","ORIGEM_USADA","CASO","CLIENTE","ESFERA","ESTADO","DIFAL","MARCA","MODELO",
    "VALOR_VENDA","CUSTO_ATUAL","QTD_CARONA_SALDO",
    "CRED_ICMS","CRED_PIS","CRED_COFINS","DEB_PIS","DEB_COFINS","DEB_ICMS","DEB_DIFAL","DEB_FRETE",
    "TOTAL_CREDITOS","TOTAL_DEBITOS","LUCRO_FINAL_R$","LUCRO_FINAL_%","STATUS","MARGEM_BRUTA_%"
]
cols_original = [c for c in cols_original if c in df_result.columns]
styled_original = df_result[cols_original].style.apply(color_status, axis=1)

# Simulação (todas as UFs)
base_cols = ["CASO","ESFERA","MODELO","MARCA","VALOR_VENDA","CUSTO_ATUAL","QTD_CARONA_SALDO","CLIENTE","ESTADO","DIFAL"]
sim_rows = []
for _, r in df_base.iterrows():
    estado_original = str(r["ESTADO"]).strip().upper()
    for uf in UFs:
        sim = {k: r[k] for k in base_cols if k in r}
        sim["ESTADO_ORIGINAL"] = estado_original
        sim["ESTADO"] = uf
        sim["DIFAL"] = DIFAL_CONFIG.get(uf, "COM")
        sim_rows.append(sim)
df_sim = pd.DataFrame(sim_rows, columns=list(set(base_cols + ["ESTADO_ORIGINAL"])))
df_sim_result = compute_regras(df_sim, cfg_df, icms_matrix, default_origin)
cols_sim = [
    "ESTADO_ORIGINAL","ESTADO","REGRA_APLICADA","ORIGEM_USADA",
    "CASO","CLIENTE","MODELO","MARCA","VALOR_VENDA","CUSTO_ATUAL",
    "TOTAL_CREDITOS","TOTAL_DEBITOS","LUCRO_FINAL_R$","LUCRO_FINAL_%","STATUS"
]
cols_sim = [c for c in cols_sim if c in df_sim_result.columns]
styled_sim = df_sim_result[cols_sim].style.apply(color_status, axis=1)

# =========================================
# Resumo Percentual — 4 tabelas separadas
# =========================================
def percent_table(df_calc: pd.DataFrame, key: str) -> pd.DataFrame:
    # usa QTD_CARONA_SALDO; se não houver, usa contagem
    if "QTD_CARONA_SALDO" in df_calc.columns and not df_calc["QTD_CARONA_SALDO"].isna().all():
        qty = df_calc.groupby(key)["QTD_CARONA_SALDO"].sum(min_count=1).reset_index()
        total = float(qty["QTD_CARONA_SALDO"].sum() or 1.0)
        qty["Percentual"] = (qty["QTD_CARONA_SALDO"] / total * 100).round(2)
        qty = qty.rename(columns={key: key.upper(), "QTD_CARONA_SALDO": "QTD"})
    else:
        qty = df_calc.groupby(key).size().reset_index(name="QTD")
        total = float(qty["QTD"].sum() or 1.0)
        qty["Percentual"] = (qty["QTD"] / total * 100).round(2)
        qty = qty.rename(columns={key: key.upper()})
    return qty.sort_values("Percentual", ascending=False).reset_index(drop=True)

pct_modelo = percent_table(df_result, "MODELO")
pct_marca  = percent_table(df_result, "MARCA")
pct_esfera = percent_table(df_result, "ESFERA")
pct_estado = percent_table(df_result, "ESTADO")

# =========================================
# Tela — seções menores e bem separadas
# =========================================
with tab_analise:
    st.subheader("📄 Analise_Original")
    st.caption("Tabela compacta, role para ver mais linhas. Cores: **BOM** (verde claro), **RUIM** (vermelho claro).")
    st.dataframe(styled_original, width="stretch", height=260)  # altura reduzida
    st.divider()

    st.subheader("🧪 Simulacao_Todos_UFs")
    st.caption("Cada linha da base simulada para **todas as 27 UFs**.")
    st.dataframe(styled_sim, width="stretch", height=260)  # altura reduzida
    st.divider()

    st.subheader("📈 Resumo Percentual (4 tabelas)")
    r1c1, r1c2 = st.columns(2)
    with r1c1:
        st.markdown("**Percentual por MODELO**")
        st.dataframe(pct_modelo.style.format({"Percentual": "{:.2f}%"}), width="stretch", height=240)
    with r1c2:
        st.markdown("**Percentual por MARCA**")
        st.dataframe(pct_marca.style.format({"Percentual": "{:.2f}%"}), width="stretch", height=240)

    r2c1, r2c2 = st.columns(2)
    with r2c1:
        st.markdown("**Percentual por ESFERA**")
        st.dataframe(pct_esfera.style.format({"Percentual": "{:.2f}%"}), width="stretch", height=240)
    with r2c2:
        st.markdown("**Percentual por ESTADO**")
        st.dataframe(pct_estado.style.format({"Percentual": "{:.2f}%"}), width="stretch", height=240)

    # Resumo textual
    bom_orig  = int((df_result["STATUS"] == "BOM").sum())
    ruim_orig = int((df_result["STATUS"] == "RUIM").sum())
    bom_sim   = int((df_sim_result["STATUS"] == "BOM").sum())
    ruim_sim  = int((df_sim_result["STATUS"] == "RUIM").sum())
    st.info(f"Resumo — Original: **BOM {bom_orig}** / **RUIM {ruim_orig}** • "
            f"Simulações: **BOM {bom_sim}** / **RUIM {ruim_sim}**")

# =========================================
# Download — Excel com 6 abas
# =========================================
excel_bytes = df_to_excel_bytes_multi({
    "Analise_Original": df_result[cols_original],
    "Simulacao_Todos_UFs": df_sim_result[cols_sim],
    "Pct_Modelo": pct_modelo,
    "Pct_Marca": pct_marca,
    "Pct_Esfera": pct_esfera,
    "Pct_Estado": pct_estado,
})

st.download_button(
    "💾 Baixar Excel (6 abas: Original, Simulações e 4 Resumos)",
    data=excel_bytes,
    file_name=f"relatorio_completo_{dt.date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    width="stretch",
)

st.sidebar.markdown("---")
st.sidebar.download_button(
    "💾 Baixar Excel (6 abas)",
    data=excel_bytes,
    file_name=f"relatorio_completo_{dt.date.today().isoformat()}.xlsx",
    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
)

