#!/usr/bin/env python3
"""
dashboard_bko.py  —  Dashboard BKO Streamlit

Pipeline: Upload CSV -> Validar -> Processar -> Exibir relatorio

Uso: streamlit run dashboard_bko.py
     (ou via rodar_dashboard.bat)
"""

import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))
import validador_tabela_ticket as vtk
import processador_relatorio_data as prd

# ---------------------------------------------------------------------------
# Configuracao da pagina
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="BKO | Dashboard de Tickets",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
[data-testid="stMetricValue"] { font-size: 1.8rem; font-weight: 700; }
[data-testid="stMetricLabel"] { font-size: 0.85rem; color: #666; }
.block-container { padding-top: 1.5rem; }
thead tr th { background-color: #1F3864 !important; color: white !important; }
</style>
""", unsafe_allow_html=True)

# Cores
COR_FECHADO   = "#2CA02C"
COR_PROCESSO  = "#FF7F0E"
COR_ABERTO    = "#1F77B4"
COR_ALERTA    = "#D62728"
COR_PRINCIPAL = "#1F3864"


# ---------------------------------------------------------------------------
# Pipeline com logs detalhados
# ---------------------------------------------------------------------------

def _log(msg: str):
    """Loga no terminal (visivel no Streamlit Cloud logs) com timestamp."""
    print(f"[BKO {datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def executar_pipeline(file_bytes: bytes, status_ui) -> dict:
    """
    ETL completo em memoria com logs em cada etapa.
    status_ui: contexto st.status() para atualizacao visual em tempo real.
    """
    prd.AGORA = datetime.now()

    # ── Etapa 1: leitura e ETL ──────────────────────────────────────────
    status_ui.update(label="[1/6] Lendo arquivo e corrigindo encoding...")
    _log("INICIO pipeline")
    _log(f"Arquivo: {len(file_bytes):,} bytes")

    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        df_raw, enc = vtk.read_csv_auto(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    _log(f"Leitura OK | encoding={enc} | linhas={len(df_raw):,} | colunas={len(df_raw.columns)}")
    _log(f"Colunas encontradas: {list(df_raw.columns)}")

    # Valida colunas minimas obrigatorias
    COLUNAS_OBRIGATORIAS = {"ticket_id", "ticket_subject", "open_at", "status"}
    faltando = COLUNAS_OBRIGATORIAS - set(df_raw.columns)
    if faltando:
        msg = (
            f"Colunas nao encontradas: {faltando}\n\n"
            f"Colunas presentes no arquivo ({len(df_raw.columns)}): "
            f"{list(df_raw.columns)}\n\n"
            "Verifique se o arquivo usa separador ';' e foi gerado pelo sistema de tickets."
        )
        _log(f"ERRO validacao colunas: {msg}")
        raise ValueError(msg)

    # ── Etapa 2: limpeza ────────────────────────────────────────────────
    status_ui.update(label="[2/6] Limpando caracteres especiais...")
    _log("Iniciando limpeza de strings...")

    str_cols = df_raw.select_dtypes(include=["object", "str"]).columns
    df_raw[str_cols] = df_raw[str_cols].apply(lambda s: s.str.strip())

    if "ticket_subject" in df_raw.columns:
        df_raw["ticket_subject"] = vtk._fix_residual_chars(df_raw["ticket_subject"])

    df_raw, cols_criadas = vtk.split_datetime_columns(df_raw)
    avisos = vtk.validate(df_raw)
    _log(f"Limpeza OK | cols_criadas={cols_criadas} | avisos={len(avisos)}")

    # ── Etapa 3: parse de datetimes ─────────────────────────────────────
    status_ui.update(label="[3/6] Convertendo colunas de data e hora...")
    _log("Parse de datetimes...")

    df_raw = prd._parse_datetimes(df_raw)
    _log("Parse OK")

    # ── Etapa 4: consolidacao por ticket ────────────────────────────────
    status_ui.update(label="[4/6] Consolidando tickets (pode demorar)...")
    _log("Consolidando tickets por ticket_id...")

    df_geral = prd.consolidate(df_raw)
    _log(f"Consolidacao OK | tickets={len(df_geral):,} | status={df_geral['status'].value_counts().to_dict()}")

    # ── Etapa 5: analises ───────────────────────────────────────────────
    status_ui.update(label="[5/6] Calculando SLAs, funil e agrupamentos...")
    _log("Calculando analises...")

    df_sla_p   = prd.make_sla_em_processo(df_geral)
    _log(f"SLA em processo OK | {len(df_sla_p):,} tickets")

    df_sla_r   = prd.make_sla_resposta(df_geral)
    _log(f"SLA resposta OK | {len(df_sla_r):,} tickets")

    df_funil_m = prd.make_funil_mensal(df_geral)
    df_funil_s = prd.make_funil_semanal(df_geral)
    _log(f"Funil OK | {len(df_funil_m)} meses / {len(df_funil_s)} semanas")

    df_assunto = prd.make_por_assunto(df_geral)
    df_escrit  = prd.make_por_escritorio(df_geral)
    _log(f"Agrupamentos OK | {len(df_assunto)} assuntos / {len(df_escrit)} escritorios")

    # ── Etapa 6: gera XLSX ──────────────────────────────────────────────
    status_ui.update(label="[6/6] Gerando XLSX para download...")
    _log("Gerando XLSX...")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        xlsx_path = Path(tmp.name)

    prd.write_xlsx(str(xlsx_path), {
        "Aba_Geral":       df_geral,
        "SLA_Em_Processo": df_sla_p,
        "SLA_Resposta":    df_sla_r,
        "Funil_Mensal":    df_funil_m,
        "Funil_Semanal":   df_funil_s,
        "Por_Assunto":     df_assunto,
        "Por_Escritorio":  df_escrit,
    })
    xlsx_bytes = xlsx_path.read_bytes()
    xlsx_path.unlink(missing_ok=True)
    _log(f"XLSX OK | {len(xlsx_bytes):,} bytes")
    _log("PIPELINE CONCLUIDO")

    return {
        "enc":          enc,
        "avisos":       avisos,
        "cols_criadas": cols_criadas,
        "geral":        df_geral,
        "sla_p":        df_sla_p,
        "sla_r":        df_sla_r,
        "funil_m":      df_funil_m,
        "funil_s":      df_funil_s,
        "assunto":      df_assunto,
        "escrit":       df_escrit,
        "xlsx":         xlsx_bytes,
        "ref_time":     prd.AGORA.strftime("%d/%m/%Y %H:%M"),
    }


# ---------------------------------------------------------------------------
# Componentes visuais
# ---------------------------------------------------------------------------

def _fmt_h(h) -> str:
    """Formata horas como '2d 3h' ou '45min'."""
    if h is None or pd.isna(h):
        return "—"
    h = float(h)
    if h >= 24:
        d = int(h // 24)
        r = int(h % 24)
        return f"{d}d {r}h"
    if h >= 1:
        return f"{h:.1f}h"
    return f"{int(h * 60)}min"


def big_numbers(df: pd.DataFrame):
    total      = len(df)
    fechados   = (df["status"] == "closed").sum()
    em_proc    = df["status"].isin(["open", "processing"]).sum()
    abertos    = (df["status"] == "open").sum()
    alertas    = (df["sla_alerta"] == "SIM").sum()

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total de Tickets", f"{total:,}")
    c2.metric("Fechados",         f"{fechados:,}",
              delta=f"{fechados/total*100:.1f}%")
    c3.metric("Em Processo",      f"{em_proc:,}",
              delta=f"{em_proc/total*100:.1f}%",   delta_color="inverse")
    c4.metric("Abertos",          f"{abertos:,}",
              delta_color="inverse")
    c5.metric("SLA Alerta >48h",  f"{alertas:,}",
              delta_color="inverse")


# ---- Aba Geral ----

def tab_geral(df: pd.DataFrame):
    st.subheader(f"Todos os Tickets — {len(df):,} registros")

    col_cfg = {
        "ticket_id":           st.column_config.TextColumn("ID"),
        "ticket_subject":      st.column_config.TextColumn("Assunto", width="large"),
        "status":              st.column_config.TextColumn("Status"),
        "sla_alerta":          st.column_config.TextColumn("SLA Alerta"),
        "dt_abertura":         st.column_config.DatetimeColumn("Abertura",         format="DD/MM/YYYY HH:mm"),
        "dt_resposta":         st.column_config.DatetimeColumn("1a Resposta",      format="DD/MM/YYYY HH:mm"),
        "dt_ultima_atividade": st.column_config.DatetimeColumn("Ultima Atividade", format="DD/MM/YYYY HH:mm"),
        "tempo_processo_h":    st.column_config.NumberColumn("Processo (h)",  format="%.1f"),
        "tempo_resposta_h":    st.column_config.NumberColumn("Resposta (h)",  format="%.1f"),
        "n_mensagens":         st.column_config.NumberColumn("Mensagens"),
        "n_transferencias":    st.column_config.NumberColumn("Transferencias"),
        "analista":            st.column_config.TextColumn("Analista BO"),
        "consultor":           st.column_config.TextColumn("Consultor BP"),
        "escritorio":          st.column_config.TextColumn("Escritorio"),
        "mes_abertura":        st.column_config.TextColumn("Mes"),
        "semana_abertura":     st.column_config.TextColumn("Semana"),
    }
    st.dataframe(df, use_container_width=True, height=520, column_config=col_cfg)


# ---- SLA Em Processo ----

def tab_sla_processo(df: pd.DataFrame):
    st.subheader(f"Tickets Em Processo / Abertos — {len(df):,}")

    alertas = (df["sla_alerta"] == "SIM").sum()
    c1, c2, c3 = st.columns(3)
    c1.metric("Tickets ativos",    f"{len(df):,}")
    c2.metric("SLA estourado",     f"{alertas:,}", delta_color="inverse")
    c3.metric("Mais antigo",       _fmt_h(df["tempo_processo_h"].max()))

    st.markdown("---")

    col_graf, col_escrit = st.columns(2)

    with col_graf:
        fig = px.histogram(
            df,
            x="tempo_processo_h",
            nbins=30,
            title="Distribuicao do Tempo em Processo (h)",
            labels={"tempo_processo_h": "Horas"},
            color_discrete_sequence=[COR_PROCESSO],
        )
        fig.add_vline(x=48, line_dash="dash", line_color=COR_ALERTA,
                      annotation_text="48h (alerta)", annotation_position="top right")
        fig.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig, use_container_width=True)

    with col_escrit:
        df_top = (
            df.groupby("escritorio", as_index=False)["ticket_id"]
            .count()
            .rename(columns={"ticket_id": "qtd"})
            .sort_values("qtd")
            .tail(20)
        )
        fig2 = px.bar(
            df_top, x="qtd", y="escritorio", orientation="h",
            title="Escritorios com Mais Tickets Ativos",
            labels={"qtd": "Qtd", "escritorio": ""},
            color_discrete_sequence=[COR_PRINCIPAL],
        )
        fig2.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig2, use_container_width=True)

    st.subheader("Tabela Detalhada")
    col_cfg = {
        "ticket_id":        st.column_config.TextColumn("ID"),
        "ticket_subject":   st.column_config.TextColumn("Assunto", width="large"),
        "status":           st.column_config.TextColumn("Status"),
        "sla_alerta":       st.column_config.TextColumn("SLA Alerta"),
        "dt_abertura":      st.column_config.DatetimeColumn("Abertura", format="DD/MM/YYYY HH:mm"),
        "tempo_processo_h": st.column_config.NumberColumn("Tempo (h)", format="%.1f"),
        "n_transferencias": st.column_config.NumberColumn("Transferencias"),
        "analista":         st.column_config.TextColumn("Analista BO"),
        "consultor":        st.column_config.TextColumn("Consultor BP"),
        "escritorio":       st.column_config.TextColumn("Escritorio"),
    }
    st.dataframe(df, use_container_width=True, height=420, column_config=col_cfg)


# ---- SLA Resposta ----

def tab_sla_resposta(df: pd.DataFrame):
    st.subheader(f"SLA Resposta BO -> BP — {len(df):,} tickets")

    desc = df["tempo_resposta_h"].describe(percentiles=[.50, .75, .90, .95])
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Media",   _fmt_h(desc["mean"]))
    c2.metric("Mediana", _fmt_h(desc["50%"]))
    c3.metric("P90",     _fmt_h(desc["90%"]))
    c4.metric("Maximo",  _fmt_h(desc["max"]))

    st.markdown("---")

    p95 = df["tempo_resposta_h"].quantile(0.95)
    col_hist, col_box = st.columns(2)

    with col_hist:
        fig_h = px.histogram(
            df[df["tempo_resposta_h"] <= p95],
            x="tempo_resposta_h",
            nbins=40,
            title="Distribuicao do Tempo de Resposta (ate P95)",
            labels={"tempo_resposta_h": "Horas"},
            color_discrete_sequence=[COR_PRINCIPAL],
        )
        fig_h.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_h, use_container_width=True)

    with col_box:
        df_escrit_resp = df[df["escritorio"].ne("") & df["tempo_resposta_h"].notna()]
        top_escrit = (
            df_escrit_resp.groupby("escritorio")["tempo_resposta_h"]
            .median()
            .sort_values(ascending=False)
            .head(15)
            .index
        )
        fig_box = px.box(
            df_escrit_resp[df_escrit_resp["escritorio"].isin(top_escrit)],
            x="tempo_resposta_h",
            y="escritorio",
            orientation="h",
            title="Box-plot Resposta por Escritorio (Top 15 mediana)",
            labels={"tempo_resposta_h": "Horas", "escritorio": ""},
            color_discrete_sequence=[COR_PRINCIPAL],
        )
        fig_box.update_layout(margin=dict(t=40, b=20), height=420)
        st.plotly_chart(fig_box, use_container_width=True)

    st.subheader("Tabela Detalhada")
    col_cfg = {
        "ticket_id":        st.column_config.TextColumn("ID"),
        "ticket_subject":   st.column_config.TextColumn("Assunto", width="large"),
        "status":           st.column_config.TextColumn("Status"),
        "dt_abertura":      st.column_config.DatetimeColumn("Abertura",    format="DD/MM/YYYY HH:mm"),
        "dt_resposta":      st.column_config.DatetimeColumn("1a Resposta", format="DD/MM/YYYY HH:mm"),
        "tempo_resposta_h": st.column_config.NumberColumn("Resposta (h)", format="%.1f"),
        "analista":         st.column_config.TextColumn("Analista BO"),
        "consultor":        st.column_config.TextColumn("Consultor BP"),
        "escritorio":       st.column_config.TextColumn("Escritorio"),
    }
    st.dataframe(df, use_container_width=True, height=420, column_config=col_cfg)


# ---- Funil ----

def tab_funil(df_m: pd.DataFrame, df_s: pd.DataFrame):
    st.subheader("Funil Mensal")

    fig_m = px.bar(
        df_m,
        x="periodo",
        y=["tickets_abertos", "tickets_fechados", "em_processo"],
        barmode="group",
        title="Tickets por Mes",
        labels={"periodo": "Mes", "value": "Tickets", "variable": ""},
        color_discrete_map={
            "tickets_abertos":  COR_ABERTO,
            "tickets_fechados": COR_FECHADO,
            "em_processo":      COR_PROCESSO,
        },
    )
    fig_m.update_layout(legend_title_text="", margin=dict(t=40, b=20))
    st.plotly_chart(fig_m, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        fig_rm = px.bar(
            df_m, x="periodo", y="tempo_medio_resposta_h",
            title="Tempo Medio de Resposta (h) por Mes",
            labels={"periodo": "Mes", "tempo_medio_resposta_h": "Horas"},
            color_discrete_sequence=[COR_PRINCIPAL],
        )
        fig_rm.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_rm, use_container_width=True)

    with c2:
        fig_pm = px.bar(
            df_m, x="periodo", y="tempo_medio_processo_h",
            title="Tempo Medio em Processo (h) por Mes",
            labels={"periodo": "Mes", "tempo_medio_processo_h": "Horas"},
            color_discrete_sequence=[COR_PROCESSO],
        )
        fig_pm.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_pm, use_container_width=True)

    st.subheader("Funil Semanal")
    fig_s = px.bar(
        df_s,
        x="periodo",
        y=["tickets_abertos", "tickets_fechados", "em_processo"],
        barmode="group",
        title="Tickets por Semana",
        labels={"periodo": "Semana", "value": "Tickets", "variable": ""},
        color_discrete_map={
            "tickets_abertos":  COR_ABERTO,
            "tickets_fechados": COR_FECHADO,
            "em_processo":      COR_PROCESSO,
        },
    )
    fig_s.update_layout(legend_title_text="", margin=dict(t=40, b=20))
    st.plotly_chart(fig_s, use_container_width=True)

    st.subheader("Dados do Funil")
    c_esq, c_dir = st.columns(2)
    with c_esq:
        st.caption("Mensal")
        st.dataframe(df_m, use_container_width=True)
    with c_dir:
        st.caption("Semanal")
        st.dataframe(df_s, use_container_width=True)


# ---- Por Assunto ----

def tab_por_assunto(df: pd.DataFrame):
    st.subheader(f"Distribuicao por Assunto — {len(df)} assuntos")

    fig = px.bar(
        df.sort_values("total", ascending=True).tail(25),
        x="total",
        y="assunto",
        orientation="h",
        title="Top 25 Assuntos por Volume",
        labels={"total": "Qtd Tickets", "assunto": ""},
        color="pct_fechado",
        color_continuous_scale="RdYlGn",
        range_color=[0, 100],
    )
    fig.update_layout(height=600, coloraxis_colorbar_title="% Fechado",
                      margin=dict(t=40, b=20))
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Tabela Completa")
    col_cfg = {
        "assunto":                  st.column_config.TextColumn("Assunto", width="large"),
        "total":                    st.column_config.NumberColumn("Total"),
        "fechados":                 st.column_config.NumberColumn("Fechados"),
        "em_processo":              st.column_config.NumberColumn("Em Processo"),
        "com_sla_alerta":           st.column_config.NumberColumn("SLA Alerta"),
        "pct_fechado":              st.column_config.NumberColumn("% Fechado",  format="%.1f%%"),
        "tempo_medio_resposta_h":   st.column_config.NumberColumn("Resp. Media (h)", format="%.1f"),
        "tempo_medio_processo_h":   st.column_config.NumberColumn("Proc. Medio (h)", format="%.1f"),
        "n_transferencias_medio":   st.column_config.NumberColumn("Transfer. Medio", format="%.1f"),
    }
    st.dataframe(df, use_container_width=True, column_config=col_cfg)


# ---- Por Escritorio ----

def tab_por_escritorio(df: pd.DataFrame):
    st.subheader(f"Distribuicao por Escritorio — {len(df)} escritorios")

    c_vol, c_resp = st.columns(2)

    with c_vol:
        fig_v = px.bar(
            df.sort_values("total_tickets", ascending=True).tail(20),
            x="total_tickets",
            y="escritorio",
            orientation="h",
            title="Volume de Tickets por Escritorio",
            labels={"total_tickets": "Total", "escritorio": ""},
            color="pct_fechado",
            color_continuous_scale="RdYlGn",
            range_color=[0, 100],
        )
        fig_v.update_layout(coloraxis_colorbar_title="% Fechado",
                            margin=dict(t=40, b=20))
        st.plotly_chart(fig_v, use_container_width=True)

    with c_resp:
        fig_r = px.bar(
            df.sort_values("tempo_medio_resposta_h", ascending=True).tail(20),
            x="tempo_medio_resposta_h",
            y="escritorio",
            orientation="h",
            title="Tempo Medio de Resposta (h) por Escritorio",
            labels={"tempo_medio_resposta_h": "Horas", "escritorio": ""},
            color_discrete_sequence=[COR_PROCESSO],
        )
        fig_r.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_r, use_container_width=True)

    # Pendentes vs Fechados lado a lado
    df_status = df[["escritorio", "fechados", "pendentes"]].sort_values(
        "fechados", ascending=True
    ).tail(20)
    fig_comp = px.bar(
        df_status,
        x=["fechados", "pendentes"],
        y="escritorio",
        orientation="h",
        barmode="group",
        title="Fechados vs Pendentes por Escritorio (Top 20 fechados)",
        labels={"value": "Qtd", "escritorio": "", "variable": ""},
        color_discrete_map={"fechados": COR_FECHADO, "pendentes": COR_ALERTA},
    )
    fig_comp.update_layout(legend_title_text="", margin=dict(t=40, b=20))
    st.plotly_chart(fig_comp, use_container_width=True)

    st.subheader("Tabela Detalhada por Escritorio")
    col_cfg = {
        "escritorio":              st.column_config.TextColumn("Escritorio", width="medium"),
        "total_tickets":           st.column_config.NumberColumn("Total"),
        "fechados":                st.column_config.NumberColumn("Fechados"),
        "pct_fechado":             st.column_config.NumberColumn("% Fechado",  format="%.1f%%"),
        "pendentes":               st.column_config.NumberColumn("Pendentes"),
        "pct_pendente":            st.column_config.NumberColumn("% Pendente", format="%.1f%%"),
        "com_sla_alerta":          st.column_config.NumberColumn("SLA Alerta"),
        "tempo_medio_resposta_h":  st.column_config.NumberColumn("Resp. Media (h)",  format="%.1f"),
        "tempo_min_resposta_h":    st.column_config.NumberColumn("Resp. Min (h)",    format="%.1f"),
        "tempo_max_resposta_h":    st.column_config.NumberColumn("Resp. Max (h)",    format="%.1f"),
        "tempo_medio_processo_h":  st.column_config.NumberColumn("Proc. Medio (h)", format="%.1f"),
    }
    st.dataframe(df, use_container_width=True, column_config=col_cfg)


# ---------------------------------------------------------------------------
# Tela de boas-vindas
# ---------------------------------------------------------------------------

def tela_inicial():
    st.title("📊 Dashboard BKO — Tickets")
    st.markdown("---")

    c1, c2, c3 = st.columns(3)
    c1.info("**1. Carregar**\nEnvie o CSV exportado do sistema de tickets pela barra lateral.")
    c2.info("**2. Processar**\nO sistema executa automaticamente: Validar → ETL → Analisar.")
    c3.info("**3. Explorar**\nNavegue pelas abas e baixe o XLSX completo.")

    st.markdown("---")
    st.markdown("""
    #### Abas do Relatorio
    | Aba | Conteudo |
    |-----|----------|
    | 📋 Aba Geral | Um registro por ticket com todos os indicadores |
    | ⏱ SLA Em Processo | Tickets ativos ordenados por tempo em aberto |
    | 📨 SLA Resposta | Tempo de resposta BO → BP por ticket |
    | 📊 Funil | Mensal e semanal: abertos / em processo / fechados |
    | 🏷 Por Assunto | Volume e SLA por categoria do ticket |
    | 🏢 Por Escritorio | Fechados, pendentes e tempo de resposta por escritorio |

    #### Formato do arquivo CSV
    Separador `;` — encoding Windows-1252 ou UTF-8.
    Colunas esperadas: `ticket_id`, `ticket_subject`, `open_at`, `answered_at`,
    `status`, `sender`, `consultant`, `office` ...
    """)


# ---------------------------------------------------------------------------
# App principal
# ---------------------------------------------------------------------------

def main():
    # Sidebar
    with st.sidebar:
        st.markdown("## 📊 BKO Dashboard")
        st.markdown("---")

        uploaded = st.file_uploader(
            "Carregar arquivo CSV",
            type=["csv"],
            help="CSV exportado do sistema de tickets (separador ;)",
        )

        if uploaded:
            st.success(f"Arquivo: **{uploaded.name}**")

        st.markdown("---")
        st.caption(f"SLA alerta configurado: > {prd.SLA_ALERTA_H}h")

    # Sem arquivo — tela inicial
    if uploaded is None:
        tela_inicial()
        return

    # Com arquivo — processa (usa session_state para nao reprocessar ao mudar de aba)
    cache_key = f"result_{uploaded.file_id}"
    if cache_key not in st.session_state:
        with st.status("Processando arquivo...", expanded=True) as status_ui:
            try:
                result = executar_pipeline(uploaded.getvalue(), status_ui)
                status_ui.update(label="Processamento concluido!", state="complete", expanded=False)
                st.session_state[cache_key] = result
            except Exception as e:
                status_ui.update(label=f"Erro: {e}", state="error", expanded=True)
                _log(f"ERRO: {e}")
                _log(traceback.format_exc())
                st.error(f"Erro no processamento: {e}")
                st.exception(e)
                return

    result = st.session_state[cache_key]

    # Cabecalho
    st.title("📊 Dashboard BKO — Tickets")
    st.caption(
        f"Arquivo: **{uploaded.name}** | "
        f"Referencia: **{result['ref_time']}** | "
        f"Encoding: `{result['enc']}`"
    )

    # Avisos de qualidade
    if result["avisos"]:
        with st.expander(f"⚠️ {len(result['avisos'])} aviso(s) de qualidade dos dados", expanded=False):
            for av in result["avisos"]:
                st.warning(av)

    # Download na sidebar
    with st.sidebar:
        st.download_button(
            label="📥 Baixar XLSX completo",
            data=result["xlsx"],
            file_name=f"relatorio_bko_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

    # Big numbers
    st.markdown("---")
    big_numbers(result["geral"])
    st.markdown("---")

    # Abas
    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs([
        "📋 Aba Geral",
        "⏱ SLA Em Processo",
        "📨 SLA Resposta",
        "📊 Funil",
        "🏷 Por Assunto",
        "🏢 Por Escritorio",
    ])

    with tab1:
        tab_geral(result["geral"])

    with tab2:
        tab_sla_processo(result["sla_p"])

    with tab3:
        tab_sla_resposta(result["sla_r"])

    with tab4:
        tab_funil(result["funil_m"], result["funil_s"])

    with tab5:
        tab_por_assunto(result["assunto"])

    with tab6:
        tab_por_escritorio(result["escrit"])


if __name__ == "__main__":
    main()
