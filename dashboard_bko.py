#!/usr/bin/env python3
"""
dashboard_bko.py  —  Dashboard BKO Streamlit

Pipeline: Upload CSV -> Validar -> Processar -> Exibir relatorio

Uso: streamlit run dashboard_bko.py
     (ou via rodar_dashboard.bat)
"""

import os
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from fpdf import FPDF

load_dotenv()  # carrega .env em desenvolvimento local (no-op no Streamlit Cloud)

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


def executar_pipeline(file_bytes: bytes, file_ext: str, status_ui) -> dict:
    """
    ETL completo em memoria com logs em cada etapa.
    status_ui: contexto st.status() para atualizacao visual em tempo real.
    """
    prd.AGORA = datetime.now()

    # ── Etapa 1: leitura e ETL ──────────────────────────────────────────
    status_ui.update(label="[1/6] Lendo arquivo e corrigindo encoding...")
    _log("INICIO pipeline")
    _log(f"Arquivo: {len(file_bytes):,} bytes | extensao: {file_ext}")

    suffix = f".{file_ext.lower()}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        df_raw, enc = vtk.read_any_file(tmp_path)
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

    # Cruzar com Departamentos.xlsx (opcional — se o arquivo existir)
    _dept_path = Path(__file__).parent / "Departamentos.xlsx"
    df_dept = prd.load_departamentos(_dept_path)
    if df_dept is not None:
        df_geral = df_geral.merge(df_dept, on="ticket_subject", how="left")
        df_geral["departamento"] = df_geral["departamento"].fillna("Sem Departamento")
        _log(f"Departamentos cruzados | {df_geral['departamento'].nunique()} departamentos")
    else:
        _log("Departamentos.xlsx nao encontrado — aba de departamento sera omitida")

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
    df_depto   = prd.make_por_departamento(df_geral)
    _log(f"Agrupamentos OK | {len(df_assunto)} assuntos / {len(df_escrit)} escritorios / {len(df_depto)} departamentos")

    # ── Etapa 6: gera XLSX ──────────────────────────────────────────────
    status_ui.update(label="[6/6] Gerando XLSX para download...")
    _log("Gerando XLSX...")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        xlsx_path = Path(tmp.name)

    sheets = {
        "Aba_Geral":         df_geral,
        "SLA_Em_Processo":   df_sla_p,
        "SLA_Resposta":      df_sla_r,
        "Funil_Mensal":      df_funil_m,
        "Funil_Semanal":     df_funil_s,
        "Por_Assunto":       df_assunto,
        "Por_Escritorio":    df_escrit,
    }
    if len(df_depto) > 0:
        sheets["Por_Departamento"] = df_depto

    prd.write_xlsx(str(xlsx_path), sheets)
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
        "depto":        df_depto,
        "xlsx":         xlsx_bytes,
        "ref_time":     prd.AGORA.strftime("%d/%m/%Y %H:%M"),
    }


# ---------------------------------------------------------------------------
# Exportação: PDF e E-mail
# ---------------------------------------------------------------------------

def _trunc(s: str, n: int) -> str:
    """Trunca string para n chars (latin-1 safe) e adiciona '...' se necessario."""
    s = str(s).encode("latin-1", errors="replace").decode("latin-1")
    return s[: n - 3] + "..." if len(s) > n else s


class _RelatorioPDF(FPDF):
    """PDF com cabecalho azul e rodape com numero de pagina (a partir da pag. 2)."""

    def __init__(self, ref: str):
        super().__init__()
        self._ref = ref

    def header(self):
        if self.page_no() == 1:
            return
        self.set_fill_color(31, 56, 100)
        self.rect(0, 0, self.w, 12, "F")
        self.set_font("Helvetica", "B", 8)
        self.set_text_color(255, 255, 255)
        self.set_xy(15, 2)
        self.cell(130, 8, text="Relatorio BKO - Tickets", new_x="RIGHT", new_y="TOP")
        self.cell(0, 8, text=self._ref, align="R")
        self.set_xy(self.l_margin, self.t_margin)

    def footer(self):
        self.set_y(-12)
        self.set_draw_color(180, 180, 180)
        self.line(self.l_margin, self.get_y(),
                  self.w - self.r_margin, self.get_y())
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, text=f"Pagina {self.page_no()}", align="C")


def _section_title(pdf, title: str) -> None:
    """Faixa colorida de titulo de secao."""
    pdf.set_fill_color(44, 62, 80)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 8, text=f"  {title}", fill=True,
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)
    pdf.set_text_color(0, 0, 0)


def _pdf_table_header(pdf, headers: list, widths: list,
                      aligns: list | None = None) -> None:
    if aligns is None:
        aligns = ["C"] * len(headers)
    pdf.set_fill_color(52, 73, 94)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 8)
    for h, w, a in zip(headers, widths, aligns):
        pdf.cell(w, 7, text=h, border=1, fill=True, align=a,
                 new_x="RIGHT", new_y="TOP")
    pdf.ln(7)


def _pdf_table_row(pdf, values: list, widths: list, idx: int,
                   aligns: list | None = None) -> None:
    if aligns is None:
        aligns = ["C"] * len(values)
    pdf.set_fill_color(245, 248, 252) if idx % 2 == 0 else pdf.set_fill_color(255, 255, 255)
    pdf.set_text_color(30, 30, 30)
    pdf.set_font("Helvetica", "", 8)
    for val, w, a in zip(values, widths, aligns):
        pdf.cell(w, 6, text=_trunc(str(val), 50), border="LRB",
                 fill=True, align=a, new_x="RIGHT", new_y="TOP")
    pdf.ln(6)


def _pdf_kpi_row(pdf, kpis: list) -> None:
    """
    Renderiza cards KPI com barra de acento colorida no topo.
    kpis = [(label, value, sub_text, color_hex), ...]
    """
    content_w = pdf.w - pdf.l_margin - pdf.r_margin
    gap  = 3
    cw   = content_w / len(kpis)
    card = cw - gap
    x0   = pdf.l_margin
    y0   = pdf.get_y()

    for i, (label, value, sub, color) in enumerate(kpis):
        r, g, b = int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        x = x0 + i * cw

        # Barra de acento (topo do card)
        pdf.set_fill_color(r, g, b)
        pdf.rect(x, y0, card, 3, "F")

        # Fundo do card
        pdf.set_fill_color(250, 251, 253)
        pdf.set_draw_color(210, 215, 220)
        pdf.rect(x, y0 + 3, card, 24, "FD")

        # Numero principal
        pdf.set_font("Helvetica", "B", 15)
        pdf.set_text_color(r, g, b)
        pdf.set_xy(x, y0 + 5)
        pdf.cell(card, 10, text=str(value), align="C",
                 new_x="RIGHT", new_y="TOP")

        # Texto secundario (ex: percentual)
        if sub:
            pdf.set_font("Helvetica", "", 7)
            pdf.set_text_color(110, 110, 110)
            pdf.set_xy(x, y0 + 15)
            pdf.cell(card, 5, text=str(sub), align="C",
                     new_x="RIGHT", new_y="TOP")

        # Label
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(70, 70, 70)
        pdf.set_xy(x, y0 + 21)
        pdf.cell(card, 6, text=label, align="C",
                 new_x="RIGHT", new_y="TOP")

    pdf.set_xy(x0, y0 + 30)
    pdf.set_text_color(0, 0, 0)
    pdf.set_draw_color(0, 0, 0)


def _pdf_chart_img(fig, scale: float = 1.5) -> str:
    """Exporta figura Plotly para PNG temporario. Retorna caminho do arquivo."""
    import plotly.io as pio
    img_bytes = pio.to_image(fig, format="png", scale=scale)
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.write(img_bytes)
    tmp.close()
    return tmp.name


def _pdf_chart_label(pdf, title: str) -> None:
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(44, 62, 80)
    pdf.cell(0, 5, text=title, new_x="LMARGIN", new_y="NEXT")


_CHART_STYLE = dict(plot_bgcolor="white", paper_bgcolor="white",
                    font=dict(size=11), bargap=0.25)


def _pdf_add_charts(pdf, result: dict) -> None:
    """Adiciona graficos PNG ao PDF. Silencioso se kaleido nao estiver disponivel."""
    try:
        # ── Funil Mensal ──────────────────────────────────────────────────
        df_m = result["funil_m"]
        fig1 = px.bar(
            df_m, y="periodo",
            x=["tickets_fechados", "em_processo"],
            barmode="group", orientation="h",
            title="Funil Mensal — Fechados e Em Processo",
            color_discrete_map={"tickets_fechados": "#2CA02C", "em_processo": "#FF7F0E"},
        )
        fig1.update_layout(height=260, width=520, margin=dict(t=40, b=20, l=10, r=10),
                           legend_title_text="", **_CHART_STYLE)
        fig1.update_traces(textposition="outside", cliponaxis=False, texttemplate="%{x}")

        # ── Volume por Departamento ───────────────────────────────────────
        df_dept = result.get("depto", pd.DataFrame())
        fig4 = None
        if not df_dept.empty and "departamento" in df_dept.columns:
            fig4 = px.bar(
                df_dept.sort_values("total_tickets", ascending=True),
                x="total_tickets", y="departamento", orientation="h",
                title="Volume por Departamento",
                labels={"total_tickets": "Total", "departamento": ""},
                color="pct_fechado",
                color_continuous_scale="RdYlGn", range_color=[0, 100],
                text="total_tickets",
            )
            fig4.update_layout(height=260, width=520, margin=dict(t=40, b=20, l=10, r=10),
                               coloraxis_colorbar_title="% Fechado", **_CHART_STYLE)
            fig4.update_traces(textposition="outside", cliponaxis=False)

        # ── Volume por Escritorio ─────────────────────────────────────────
        df_e = result["escrit"].sort_values("total_tickets", ascending=True).tail(15)
        fig2 = px.bar(
            df_e, x="total_tickets", y="escritorio", orientation="h",
            title="Volume por Escritorio (Top 15)",
            color="pct_fechado",
            color_continuous_scale="RdYlGn", range_color=[0, 100],
            text="total_tickets",
            labels={"total_tickets": "Total", "escritorio": ""},
        )
        fig2.update_layout(height=360, width=560, margin=dict(t=40, b=20, l=10, r=80),
                           coloraxis_colorbar_title="% Fechado", **_CHART_STYLE)
        fig2.update_traces(textposition="outside", cliponaxis=False)

        # ── Top 20 Tickets em Espera ──────────────────────────────────────
        fig3 = None
        df_sla = result.get("sla_p", pd.DataFrame())
        if not df_sla.empty and "tempo_processo_h" in df_sla.columns:
            df_top = df_sla.sort_values("tempo_processo_h", ascending=False).head(20).copy()
            df_top["label"] = (
                "#" + df_top["ticket_id"].astype(str) + "  " +
                df_top["ticket_subject"].str.slice(0, 28)
            )
            df_top["cor"] = df_top["tempo_processo_h"].apply(
                lambda h: "#D62728" if h > 24 else ("#FF7F0E" if h > 10 else "#2CA02C")
            )
            fig3 = px.bar(
                df_top.sort_values("tempo_processo_h", ascending=True),
                x="tempo_processo_h", y="label", orientation="h",
                title="Top 20 Tickets com Maior Tempo em Espera (h)",
                labels={"tempo_processo_h": "Horas", "label": ""},
                color="cor", color_discrete_map="identity",
                text_auto=".1f",
            )
            fig3.update_layout(height=400, width=580, margin=dict(t=40, b=20, l=10, r=20),
                               showlegend=False, **_CHART_STYLE)
            fig3.update_traces(textposition="outside", cliponaxis=False)

        # ── Montar paginas ────────────────────────────────────────────────
        pdf.add_page()
        _section_title(pdf, "Graficos")
        pdf.ln(2)

        # Pagina graficos 1: Funil + Departamento lado a lado
        tmp1 = _pdf_chart_img(fig1)
        tmp4 = _pdf_chart_img(fig4) if fig4 is not None else None
        try:
            col_w = 88
            x0 = pdf.l_margin
            y0 = pdf.get_y()
            _pdf_chart_label(pdf, "Funil Mensal de Tickets")
            pdf.image(tmp1, x=x0, w=col_w)
            if tmp4:
                pdf.set_xy(x0 + col_w + 4, y0)
                _pdf_chart_label(pdf, "Volume por Departamento")
                pdf.image(tmp4, x=x0 + col_w + 4, y=y0 + 6, w=col_w)
        finally:
            Path(tmp1).unlink(missing_ok=True)
            if tmp4:
                Path(tmp4).unlink(missing_ok=True)

        # Avanca apos os dois graficos lado a lado
        pdf.ln(4)

        # Volume por Escritorio — largura total
        pdf.add_page()
        _section_title(pdf, "Volume por Escritorio")
        pdf.ln(2)
        tmp2 = _pdf_chart_img(fig2)
        try:
            _pdf_chart_label(pdf, "Top 15 Escritorios por Volume")
            pdf.image(tmp2, w=170)
        finally:
            Path(tmp2).unlink(missing_ok=True)

        # Top 20 em espera — largura total
        if fig3 is not None:
            pdf.add_page()
            _section_title(pdf, "Tickets em Espera")
            pdf.ln(2)
            tmp3 = _pdf_chart_img(fig3)
            try:
                _pdf_chart_label(pdf, "Top 20 Tickets com Maior Tempo em Aberto")
                pdf.image(tmp3, w=170)
            finally:
                Path(tmp3).unlink(missing_ok=True)

    except Exception as exc:
        _log(f"Graficos no PDF ignorados: {exc}")


def _fig_to_b64(fig) -> str:
    """Exporta figura Plotly como PNG base64 para embed em HTML."""
    import plotly.io as pio, base64
    return base64.b64encode(pio.to_image(fig, format="png", scale=2.0)).decode()


def _html_to_pdf(html: str) -> bytes:
    """Converte HTML para PDF. Tenta WeasyPrint (Linux/Cloud); cai para xhtml2pdf se disponivel."""
    try:
        import weasyprint
        return weasyprint.HTML(string=html).write_pdf()
    except Exception:
        pass
    try:
        from xhtml2pdf import pisa
        from io import BytesIO
        buf = BytesIO()
        pisa.CreatePDF(html.encode("utf-8"), dest=buf)
        return buf.getvalue()
    except Exception:
        raise RuntimeError(
            "PDF nao pode ser gerado neste ambiente. "
            "No Streamlit Cloud o PDF e gerado automaticamente via WeasyPrint."
        )


def _build_pdf(result: dict) -> bytes:
    """Gera PDF com visual de dashboard (HTML -> PDF)."""

    df_g    = result["geral"]
    ref     = result["ref_time"]
    total   = len(df_g)
    fech    = int((df_g["status"] == "closed").sum())
    proc    = int((df_g["status"] == "processing").sum())
    abertos = int((df_g["status"] == "open").sum())
    alert   = int((df_g["sla_alerta"] == "SIM").sum())
    sla_p_med  = df_g["tempo_processo_h"].mean()
    sla_p_max  = df_g["tempo_processo_h"].max()
    sla_r_med  = df_g["tempo_resposta_h"].mean()
    pct_alerta = alert / total * 100 if total else 0

    # ── Gerar charts como base64 (cada um isolado) ───────────────────────
    _cs = dict(plot_bgcolor="white", paper_bgcolor="white",
               bargap=0.22, font=dict(family="Arial, sans-serif", size=11))
    b64 = {}

    def _try_chart(key, build_fn):
        try:
            b64[key] = _fig_to_b64(build_fn())
        except Exception as exc:
            _log(f"Chart '{key}' ignorado no PDF: {exc}")

    def _make_funil():
        fig = px.bar(result["funil_m"], y="periodo",
                     x=["tickets_fechados", "em_processo"],
                     barmode="group", orientation="h",
                     color_discrete_map={"tickets_fechados": "#2CA02C", "em_processo": "#FF7F0E"})
        fig.update_layout(height=280, width=620, legend_title_text="",
                          legend=dict(orientation="h", y=1.12),
                          margin=dict(t=10, b=10, l=10, r=10),
                          yaxis=dict(automargin=True), **_cs)
        fig.update_traces(textposition="outside", cliponaxis=False, texttemplate="%{x}")
        return fig

    def _make_escrit():
        df_e = result["escrit"].sort_values("total_tickets", ascending=True).tail(15)
        fig = px.bar(df_e, x="total_tickets", y="escritorio", orientation="h",
                     color="pct_fechado", color_continuous_scale="RdYlGn",
                     range_color=[0, 100], text="total_tickets",
                     labels={"total_tickets": "Total", "escritorio": ""})
        fig.update_layout(height=420, width=720, margin=dict(t=10, b=10, l=10, r=70),
                          coloraxis_colorbar_title="% Fechado",
                          yaxis=dict(automargin=True), **_cs)
        fig.update_traces(textposition="outside", cliponaxis=False)
        return fig

    def _make_top20():
        df_sla = result.get("sla_p", pd.DataFrame())
        if df_sla.empty or "tempo_processo_h" not in df_sla.columns:
            raise ValueError("sla_p vazio")
        df_top = df_sla.sort_values("tempo_processo_h", ascending=False).head(20).copy()
        df_top["label"] = ("#" + df_top["ticket_id"].astype(str) + "  " +
                           df_top["ticket_subject"].str.slice(0, 32))
        df_top["cor"] = df_top["tempo_processo_h"].apply(
            lambda h: "#D62728" if h > 24 else ("#FF7F0E" if h > 10 else "#2CA02C"))
        fig = px.bar(df_top.sort_values("tempo_processo_h", ascending=True),
                     x="tempo_processo_h", y="label", orientation="h",
                     color="cor", color_discrete_map="identity", text_auto=".1f",
                     labels={"tempo_processo_h": "Horas", "label": ""})
        fig.update_layout(height=460, width=740, showlegend=False,
                          margin=dict(t=10, b=10, l=10, r=30),
                          yaxis=dict(automargin=True), **_cs)
        fig.update_traces(textposition="outside", cliponaxis=False)
        return fig

    def _make_dept():
        df_d = result.get("depto", pd.DataFrame())
        if df_d.empty or "departamento" not in df_d.columns:
            raise ValueError("depto vazio")
        fig = px.bar(df_d.sort_values("total_tickets", ascending=True),
                     x="total_tickets", y="departamento", orientation="h",
                     color="pct_fechado", color_continuous_scale="RdYlGn",
                     range_color=[0, 100], text="total_tickets",
                     labels={"total_tickets": "Total", "departamento": ""})
        fig.update_layout(height=300, width=620, margin=dict(t=10, b=10, l=10, r=60),
                          coloraxis_colorbar_title="% Fechado",
                          yaxis=dict(automargin=True), **_cs)
        fig.update_traces(textposition="outside", cliponaxis=False)
        return fig

    _try_chart("funil",  _make_funil)
    _try_chart("escrit", _make_escrit)
    _try_chart("top20",  _make_top20)
    _try_chart("dept",   _make_dept)

    # ── Helpers ───────────────────────────────────────────────────────────
    def kpi(label, value, sub, color):
        sub_html = f'<div class="ks">{sub}</div>' if sub else ""
        return (f'<div class="kc" style="border-top:4px solid {color}">'
                f'<div class="kv" style="color:{color}">{value}</div>'
                f'{sub_html}<div class="kl">{label}</div></div>')

    def sec(title):
        return f'<div class="st">{title}</div>'

    def img(key, alt=""):
        if key not in b64:
            return ""
        return f'<div class="cb"><img src="data:image/png;base64,{b64[key]}" alt="{alt}"/></div>'

    def tbl_rows(df, cols, fmts=None):
        fmts = fmts or {}
        out = ""
        for i, (_, row) in enumerate(df.iterrows()):
            cls = ' class="z"' if i % 2 == 0 else ""
            cells = ""
            for c in cols:
                v = row.get(c, "")
                try:
                    v = fmts[c](v) if c in fmts else (str(v) if pd.notna(v) else "-")
                except Exception:
                    v = "-"
                cells += f"<td>{v}</td>"
            out += f"<tr{cls}>{cells}</tr>"
        return out

    # ── Dados tabelas ─────────────────────────────────────────────────────
    fm = {"tickets_fechados": lambda v: f"{int(v):,}",
          "em_processo":      lambda v: f"{int(v):,}",
          "pct_fechado":      lambda v: f"{v:.1f}%",
          "tempo_medio_resposta_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-"}
    funil_rows = tbl_rows(result["funil_m"].tail(12),
                          ["periodo","tickets_fechados","em_processo",
                           "pct_fechado","tempo_medio_resposta_h"], fm)

    am = {"total": lambda v: f"{int(v):,}", "fechados": lambda v: f"{int(v):,}",
          "em_processo": lambda v: f"{int(v):,}", "pct_fechado": lambda v: f"{v:.1f}%",
          "tempo_medio_resposta_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
          "assunto": lambda v: str(v)[:45] if pd.notna(v) else "-"}
    assunto_rows = tbl_rows(result["assunto"].head(15),
                            ["assunto","total","fechados","em_processo",
                             "pct_fechado","tempo_medio_resposta_h"], am)

    em = {"total_tickets": lambda v: f"{int(v):,}",
          "fechados":      lambda v: f"{int(v):,}",
          "pendentes":     lambda v: f"{int(v):,}",
          "pct_fechado":   lambda v: f"{v:.1f}%",
          "tempo_medio_resposta_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
          "escritorio": lambda v: str(v)[:35] if pd.notna(v) else "-"}
    escrit_rows = tbl_rows(result["escrit"].head(15),
                           ["escritorio","total_tickets","fechados",
                            "pendentes","pct_fechado","tempo_medio_resposta_h"], em)

    df_dept = result.get("depto", pd.DataFrame())
    dept_section = ""
    if not df_dept.empty:
        dm = {"total_tickets": lambda v: f"{int(v):,}",
              "fechados":      lambda v: f"{int(v):,}",
              "em_processo":   lambda v: f"{int(v):,}",
              "pct_fechado":   lambda v: f"{v:.1f}%",
              "tempo_medio_resposta_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
              "tempo_medio_processo_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
              "departamento": lambda v: str(v)[:30] if pd.notna(v) else "-"}
        dept_rows = tbl_rows(df_dept,
                             ["departamento","total_tickets","fechados","em_processo",
                              "pct_fechado","tempo_medio_resposta_h","tempo_medio_processo_h"], dm)
        dept_section = f"""
        <div class="pb"></div>
        {sec('Resumo por Departamento')}
        {img('dept')}
        <table><thead><tr>
          <th>Departamento</th><th>Total</th><th>Fechados</th><th>Em Proc.</th>
          <th>% Fech.</th><th>Resp. Med (h)</th><th>Proc. Med (h)</th>
        </tr></thead><tbody>{dept_rows}</tbody></table>"""

    df_top50 = (df_g[df_g["status"].isin(["open","processing"])]
                .sort_values("tempo_processo_h", ascending=False).head(50))
    gm = {"tempo_processo_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
          "tempo_resposta_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
          "ticket_subject":   lambda v: str(v)[:50] if pd.notna(v) else "-",
          "analista":         lambda v: str(v)[:28] if pd.notna(v) else "-",
          "status":           lambda v: str(v) if pd.notna(v) else "-"}
    top50_rows = tbl_rows(df_top50,
                          ["ticket_id","ticket_subject","status",
                           "tempo_processo_h","tempo_resposta_h","analista"], gm)

    # Tabela fallback para top20 quando grafico nao carrega
    df_sla_top20 = result.get("sla_p", pd.DataFrame())
    if not df_sla_top20.empty and "top20" not in b64:
        tm = {"tempo_processo_h": lambda v: f"{v:.1f}h" if pd.notna(v) else "-",
              "ticket_subject":   lambda v: str(v)[:50] if pd.notna(v) else "-",
              "analista":         lambda v: str(v)[:28] if pd.notna(v) else "-"}
        _t20_rows = tbl_rows(
            df_sla_top20.sort_values("tempo_processo_h", ascending=False).head(20),
            ["ticket_id","ticket_subject","status","tempo_processo_h","sla_alerta","analista"], tm)
        top20_fallback_tbl = (
            "<table><thead><tr>"
            "<th>Ticket ID</th><th>Assunto</th><th>Status</th>"
            "<th>Proc. (h)</th><th>SLA</th><th>Analista</th>"
            f"</tr></thead><tbody>{_t20_rows}</tbody></table>"
        )
    else:
        top20_fallback_tbl = ""

    sla_bar = "&nbsp;&nbsp;|&nbsp;&nbsp;".join([
        f"Proc. Médio: {sla_p_med:.1f}h"  if pd.notna(sla_p_med)  else "Proc. Médio: —",
        f"Proc. Máximo: {sla_p_max:.1f}h" if pd.notna(sla_p_max)  else "Proc. Máx: —",
        f"Resp. Média: {sla_r_med:.1f}h"  if pd.notna(sla_r_med)  else "Resp. Média: —",
        f"Em Alerta: {pct_alerta:.1f}% dos tickets",
    ])

    pct_f = fech/total*100 if total else 0
    pct_p = proc/total*100 if total else 0
    pct_a = abertos/total*100 if total else 0

    # ── HTML ──────────────────────────────────────────────────────────────
    html = f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8"/>
<style>
@page {{
  size: A4; margin: 14mm 14mm 18mm 14mm;
  @bottom-center {{ content: "Página " counter(page) " de " counter(pages);
                    font-size: 8pt; color: #aaa; }}
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: Arial, Helvetica, sans-serif; font-size: 9.5pt;
        color: #1a1a2e; background: white; }}
.cover {{ background: #1F3864; color: white; padding: 26px 22px 20px;
          border-radius: 6px; margin-bottom: 16px; }}
.cover h1 {{ font-size: 24pt; font-weight: bold; margin-bottom: 3px; }}
.cover h2 {{ font-size: 12pt; font-weight: normal; opacity: .82; margin-bottom: 6px; }}
.cover .ref {{ font-size: 8.5pt; color: #90b8ff; }}
.kg {{ display: grid; grid-template-columns: repeat(5,1fr); gap: 8px; margin-bottom: 12px; }}
.kc {{ background: #f6f8fd; border-radius: 5px; padding: 9px 7px 7px;
       text-align: center; box-shadow: 0 1px 4px rgba(0,0,0,.1); }}
.kv {{ font-size: 19pt; font-weight: bold; line-height: 1.1; }}
.ks {{ font-size: 7pt; color: #888; margin: 1px 0 2px; }}
.kl {{ font-size: 7.5pt; color: #555; font-weight: bold; margin-top: 3px; }}
.sb {{ background: #eef2f7; border: 1px solid #cdd8e8; border-radius: 4px;
       padding: 5px 10px; font-size: 8pt; color: #444; margin-bottom: 16px; }}
.st {{ background: #2c3e50; color: white; padding: 5px 11px; font-size: 10.5pt;
       font-weight: bold; border-radius: 4px; margin: 16px 0 8px; }}
table {{ width: 100%; border-collapse: collapse; font-size: 7.8pt; margin-bottom: 10px; }}
thead th {{ background: #1F3864; color: white; padding: 5px 6px; text-align: left; }}
tbody tr.z {{ background: #f3f7fc; }}
tbody td {{ padding: 4px 6px; border-bottom: 1px solid #e2e8f0; }}
.cb {{ background: #f6f8fd; border-radius: 6px; padding: 8px;
       box-shadow: 0 1px 4px rgba(0,0,0,.08); margin-bottom: 10px; }}
.cb img {{ width: 100%; display: block; }}
.two {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 10px; }}
.pb {{ page-break-before: always; }}
.note {{ font-size: 7.5pt; color: #888; font-style: italic; margin-bottom: 8px; }}
</style></head><body>

<div class="cover">
  <h1>Relatorio BKO</h1>
  <h2>Dashboard de Tickets</h2>
  <div class="ref">Gerado em {ref}</div>
</div>

<div class="kg">
  {kpi("Total de Tickets", f"{total:,}", "", "#1F3864")}
  {kpi("Fechados",         f"{fech:,}",    f"{pct_f:.1f}% do total", "#2CA02C")}
  {kpi("Em Processamento", f"{proc:,}",    f"{pct_p:.1f}% do total", "#FF7F0E")}
  {kpi("Abertos",          f"{abertos:,}", f"{pct_a:.1f}% do total", "#1F77B4")}
  {kpi("SLA Alerta &gt;24h", f"{alert:,}", f"{pct_alerta:.1f}% do total", "#D62728")}
</div>
<div class="sb">{sla_bar}</div>

{sec('Funil Mensal')}
<table><thead><tr>
  <th>Periodo</th><th>Fechados</th><th>Em Processo</th><th>% Fechado</th><th>Resp. Media (h)</th>
</tr></thead><tbody>{funil_rows}</tbody></table>
{img('funil')}

<div class="pb"></div>
{sec('Top 15 Assuntos por Volume')}
<table><thead><tr>
  <th>Assunto</th><th>Total</th><th>Fechados</th><th>Em Proc.</th><th>% Fech.</th><th>Resp. Med (h)</th>
</tr></thead><tbody>{assunto_rows}</tbody></table>

{sec('Top 15 Escritorios por Volume')}
<table><thead><tr>
  <th>Escritorio</th><th>Total</th><th>Fechados</th><th>Pendentes</th><th>% Fech.</th><th>Resp. Med (h)</th>
</tr></thead><tbody>{escrit_rows}</tbody></table>
{img('escrit')}

{dept_section}

<div class="pb"></div>
{sec('Top 20 Tickets com Maior Tempo em Espera')}
{img('top20')}
{top20_fallback_tbl}

<div class="pb"></div>
{sec('Top 50 Tickets Ativos por Urgencia')}
<p class="note">Tickets com status aberto ou em processamento, ordenados por tempo em aberto.</p>
<table><thead><tr>
  <th>Ticket ID</th><th>Assunto</th><th>Status</th>
  <th>Proc. (h)</th><th>Resp. (h)</th><th>Analista</th>
</tr></thead><tbody>{top50_rows}</tbody></table>

</body></html>"""

    return _html_to_pdf(html)


def _enviar_email(destinatario: str, xlsx_bytes: bytes, ref_time: str) -> None:
    """Envia XLSX por e-mail usando credenciais configuradas nos Streamlit Secrets."""
    import smtplib
    from email import encoders as enc_mod
    from email.mime.base import MIMEBase
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    # Prioridade: Streamlit Secrets (Cloud) → variáveis de ambiente do .env (local)
    cfg       = st.secrets.get("email", {})
    smtp_host = cfg.get("smtp_host") or os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(cfg.get("smtp_port") or os.getenv("EMAIL_SMTP_PORT", "587"))
    smtp_user = cfg.get("smtp_user") or os.getenv("EMAIL_SMTP_USER", "")
    smtp_pass = cfg.get("smtp_password") or os.getenv("EMAIL_SMTP_PASSWORD", "")
    smtp_from = cfg.get("smtp_from") or os.getenv("EMAIL_SMTP_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        raise ValueError(
            "Credenciais SMTP nao configuradas.\n"
            "Adicione no Streamlit Secrets (Settings > Secrets):\n\n"
            "[email]\n"
            "smtp_host     = \"smtp.gmail.com\"\n"
            "smtp_port     = 587\n"
            "smtp_user     = \"seu@email.com\"\n"
            "smtp_password = \"sua_senha_de_app\"\n"
            "smtp_from     = \"seu@email.com\""
        )

    msg            = MIMEMultipart()
    msg["From"]    = smtp_from
    msg["To"]      = destinatario
    msg["Subject"] = f"Relatorio BKO - Tickets ({ref_time})"

    body = (
        f"Ola,\n\n"
        f"Segue em anexo o relatorio de tickets BKO gerado em {ref_time}.\n\n"
        "O arquivo XLSX contem:\n"
        "  - Aba Geral: todos os tickets consolidados\n"
        "  - SLA Em Processo: tickets ativos por tempo em aberto\n"
        "  - SLA Resposta: tempo de resposta BO > BP\n"
        "  - Funil Mensal e Semanal\n"
        "  - Por Assunto e Por Escritorio\n\n"
        "Atenciosamente,\n"
        "Dashboard BKO"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    safe_ref = ref_time.replace("/", "").replace(":", "").replace(" ", "_")
    fname    = f"relatorio_bko_{safe_ref}.xlsx"
    part     = MIMEBase("application",
                        "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    part.set_payload(xlsx_bytes)
    enc_mod.encode_base64(part)
    part.add_header("Content-Disposition", "attachment", filename=fname)
    msg.attach(part)

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)

    _log(f"Email enviado para {destinatario}")


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
    total    = len(df)
    fechados = int((df["status"] == "closed").sum())
    em_proc  = int((df["status"] == "processing").sum())
    abertos  = int((df["status"] == "open").sum())
    alertas  = int((df["sla_alerta"] == "SIM").sum())

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}%" if total else "—"

    cards = [
        ("Total de Tickets", f"{total:,}",    "",            COR_PRINCIPAL),
        ("Fechados",         f"{fechados:,}", pct(fechados), COR_FECHADO),
        ("Em Processamento", f"{em_proc:,}",  pct(em_proc),  COR_PROCESSO),
        ("Abertos",          f"{abertos:,}",  pct(abertos),  COR_ABERTO),
        ("SLA Alerta >24h",  f"{alertas:,}",  pct(alertas),  COR_ALERTA),
    ]

    items = ""
    for label, value, delta, color in cards:
        delta_html = (
            f'<span class="bko-delta" style="color:{color};">&#x25B2; {delta}</span>'
            if delta else '<span class="bko-delta">&nbsp;</span>'
        )
        items += f"""
        <div class="bko-card" style="--c:{color}; border-color:{color}; box-shadow:0 5px 0 {color};">
            <p class="bko-label">{label}</p>
            <p class="bko-value" style="color:{color};">{value}</p>
            {delta_html}
        </div>"""

    st.markdown(f"""
    <style>
    .bko-grid {{
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 18px;
        margin-bottom: 2rem;
    }}
    .bko-card {{
        border-radius: 0.75em;
        border: 2px solid;
        padding: 20px 22px 16px;
        background: #262730;
        cursor: default;
        transform: translateY(-3px);
        transition: transform 0.12s ease, box-shadow 0.12s ease;
    }}
    .bko-card:hover {{
        transform: translateY(-6px);
        box-shadow: 0 8px 0 var(--c) !important;
    }}
    .bko-card:active {{
        transform: translateY(2px);
        box-shadow: 0 1px 0 var(--c) !important;
    }}
    .bko-label {{
        font-size: 0.72rem;
        color: #aaaaaa;
        margin: 0 0 10px 0;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .07em;
    }}
    .bko-value {{
        font-size: 2.2rem;
        font-weight: 800;
        margin: 0 0 8px 0;
        line-height: 1;
    }}
    .bko-delta {{
        font-size: 0.8rem;
        font-weight: 700;
        letter-spacing: .03em;
    }}
    </style>
    <div class="bko-grid">{items}</div>
    """, unsafe_allow_html=True)


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
    st.dataframe(df, width='stretch', height=520, column_config=col_cfg)


# ---- SLA Em Processo ----

def tab_sla_processo(df: pd.DataFrame):
    st.subheader(f"Tickets Em Processo / Abertos — {len(df):,}")

    alertas  = int((df["sla_alerta"] == "SIM").sum())
    total    = len(df)
    max_h    = df["tempo_processo_h"].max()
    idx_max  = df["tempo_processo_h"].idxmax() if total else None
    id_antigo = str(df.loc[idx_max, "ticket_id"]) if idx_max is not None else "—"

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}% dos ativos" if total else "—"

    cards = [
        ("Tickets Ativos", f"{total:,}",    "",              COR_ALERTA),
        ("SLA Estourado",  f"{alertas:,}",  pct(alertas),    COR_ALERTA),
        ("Mais Antigo",    _fmt_h(max_h),   f"#{id_antigo}", COR_ALERTA),
    ]

    def _card_html(label, value, sub, color):
        sub_html = (
            f'<span class="sla-card-delta" style="color:{color};">{sub}</span>'
            if sub else '<span class="sla-card-delta">&nbsp;</span>'
        )
        return (
            f'<div class="sla-card" style="--c:{color}; border-color:{color}; box-shadow:0 5px 0 {color};">'
            f'<p class="sla-card-label">{label}</p>'
            f'<p class="sla-card-value" style="color:{color};">{value}</p>'
            f'{sub_html}</div>'
        )

    items = "".join(_card_html(*c) for c in cards)

    st.markdown(f"""
    <style>
    .sla-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 1.6rem; }}
    .sla-card {{ border-radius: 0.75em; border: 2px solid; padding: 20px 22px 16px; background: #262730;
        cursor: default; transform: translateY(-3px); transition: transform 0.12s ease, box-shadow 0.12s ease; }}
    .sla-card:hover  {{ transform: translateY(-6px); box-shadow: 0 8px 0 var(--c) !important; }}
    .sla-card:active {{ transform: translateY(2px);  box-shadow: 0 1px 0 var(--c) !important; }}
    .sla-card-label {{ font-size: 0.78rem; color: #aaaaaa; margin: 0 0 10px 0; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }}
    .sla-card-value {{ font-size: 2.2rem; font-weight: 800; margin: 0 0 8px 0; line-height: 1; }}
    .sla-card-delta {{ font-size: 0.8rem; font-weight: 700; letter-spacing: .03em; }}
    </style>
    <div class="sla-grid">{items}</div>
    """, unsafe_allow_html=True)

    st.markdown("---")

    col_graf, col_escrit = st.columns(2)

    with col_graf:
        df_top20 = df.head(20).copy()
        df_top20["label"] = df_top20["ticket_id"].astype(str) + " — " + df_top20["ticket_subject"].str[:30]
        df_top20["cor"] = df_top20["sla_alerta"].map({"SIM": COR_ALERTA}).fillna(COR_PROCESSO)
        df_top20["tempo_fmt"] = df_top20["tempo_processo_h"].apply(_fmt_h)

        fig = px.bar(
            df_top20.iloc[::-1],
            x="tempo_processo_h",
            y="label",
            orientation="h",
            title="Top 20 Tickets com Mais Tempo em Espera",
            labels={"tempo_processo_h": "Horas em Espera", "label": ""},
            color="cor",
            color_discrete_map="identity",
            text="tempo_fmt",
        )
        fig.add_vline(x=prd.SLA_ALERTA_H, line_dash="dash", line_color=COR_ALERTA,
                      annotation_text=f"{prd.SLA_ALERTA_H}h", annotation_position="top right")
        fig.update_traces(textposition="outside", cliponaxis=False)
        fig.update_layout(
            margin=dict(t=40, b=20, r=80),
            showlegend=False,
            height=480,
            yaxis=dict(tickfont=dict(size=11)),
        )
        st.plotly_chart(fig, width='stretch')

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
            text_auto=True,
        )
        fig2.update_traces(textposition="outside", cliponaxis=False)
        fig2.update_layout(margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig2, width='stretch')

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
    st.dataframe(df, width='stretch', height=420, column_config=col_cfg)


# ---- SLA Resposta ----

def tab_sla_resposta(df: pd.DataFrame):
    st.subheader(f"SLA Resposta BO -> BP — {len(df):,} tickets")

    desc   = df["tempo_resposta_h"].describe(percentiles=[.50, .75, .90, .95])
    total  = len(df)

    def _resp_color(h):
        if pd.isna(h): return "#aaaaaa"
        if h <= 10:    return COR_FECHADO
        if h <= 24:    return COR_PROCESSO
        return COR_ALERTA

    def _resp_card(label, value, color):
        return (
            f'<div class="resp-card" style="--c:{color}; border-color:{color}; box-shadow:0 5px 0 {color};">'
            f'<p class="resp-card-label">{label}</p>'
            f'<p class="resp-card-value" style="color:{color};">{value}</p>'
            f'</div>'
        )

    # linha de estatísticas
    stat_cards = [
        ("Media",    _fmt_h(desc["mean"]), _resp_color(desc["mean"])),
        ("Mediana",  _fmt_h(desc["50%"]),  _resp_color(desc["50%"])),
        ("P90",      _fmt_h(desc["90%"]),  _resp_color(desc["90%"])),
        ("Maximo",   _fmt_h(desc["max"]),  _resp_color(desc["max"])),
    ]

    # faixas de tempo de resposta
    f_ok   = int((df["tempo_resposta_h"] <= 10).sum())
    f_med  = int(((df["tempo_resposta_h"] > 10) & (df["tempo_resposta_h"] <= 24)).sum())
    f_crit = int((df["tempo_resposta_h"] > 24).sum())

    def pct(n: int) -> str:
        return f"{n / total * 100:.1f}% das respostas" if total else "—"

    faixa_cards = [
        ("✅  Respondido ate 10h",    f"{f_ok:,}",   pct(f_ok),   COR_FECHADO),
        ("⚠️  Respondido 10h — 24h", f"{f_med:,}",  pct(f_med),  COR_PROCESSO),
        ("🚨  Respondido acima 24h",  f"{f_crit:,}", pct(f_crit), COR_ALERTA),
    ]

    def _faixa_card(label, value, sub, color):
        return (
            f'<div class="resp-card" style="--c:{color}; border-color:{color}; box-shadow:0 5px 0 {color};">'
            f'<p class="resp-card-label">{label}</p>'
            f'<p class="resp-card-value" style="color:{color};">{value}</p>'
            f'<span class="resp-card-delta" style="color:{color};">{sub}</span>'
            f'</div>'
        )

    stat_html  = "".join(_resp_card(*c)    for c in stat_cards)
    faixa_html = "".join(_faixa_card(*c)   for c in faixa_cards)

    st.markdown(f"""
    <style>
    .resp-grid4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 18px; margin-bottom: 0.8rem; }}
    .resp-grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 18px; margin-bottom: 1.6rem; }}
    .resp-card {{
        border-radius: 0.75em; border: 2px solid; padding: 18px 20px 14px;
        background: #262730; cursor: default;
        transform: translateY(-3px); transition: transform 0.12s ease, box-shadow 0.12s ease;
    }}
    .resp-card:hover  {{ transform: translateY(-6px); box-shadow: 0 8px 0 var(--c) !important; }}
    .resp-card:active {{ transform: translateY(2px);  box-shadow: 0 1px 0 var(--c) !important; }}
    .resp-card-label {{ font-size: 0.75rem; color: #aaaaaa; margin: 0 0 8px 0; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; }}
    .resp-card-value {{ font-size: 2rem; font-weight: 800; margin: 0 0 6px 0; line-height: 1; }}
    .resp-card-delta {{ font-size: 0.8rem; font-weight: 700; }}
    .resp-faixa-titulo {{ font-size: 0.75rem; font-weight: 700; text-transform: uppercase;
        letter-spacing: .07em; color: #aaaaaa; margin: 0.2rem 0 0.6rem 0; }}
    </style>
    <div class="resp-grid4">{stat_html}</div>
    <p class="resp-faixa-titulo">&#x23F1; Distribuicao por Tempo de Resposta</p>
    <div class="resp-grid3">{faixa_html}</div>
    """, unsafe_allow_html=True)

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
        st.plotly_chart(fig_h, width='stretch')

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
        st.plotly_chart(fig_box, width='stretch')

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
    st.dataframe(df, width='stretch', height=420, column_config=col_cfg)


# ---- Funil ----

def tab_funil(df_m: pd.DataFrame, df_s: pd.DataFrame):
    st.subheader("Funil Mensal")

    fig_m = px.bar(
        df_m,
        y="periodo",
        x=["tickets_fechados", "em_processo"],
        barmode="group",
        orientation="h",
        title="Tickets por Mes",
        labels={"periodo": "Mes", "value": "Tickets", "variable": ""},
        color_discrete_map={
            "tickets_fechados": COR_FECHADO,
            "em_processo":      COR_PROCESSO,
        },
        text_auto=True,
    )
    fig_m.update_traces(textposition="outside", cliponaxis=False)
    fig_m.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_m, width='stretch')

    c1, c2 = st.columns(2)
    with c1:
        fig_rm = px.bar(
            df_m, x="periodo", y="tempo_medio_resposta_h",
            title="Tempo Medio de Resposta (h) por Mes",
            labels={"periodo": "Mes", "tempo_medio_resposta_h": "Horas"},
            color_discrete_sequence=[COR_PRINCIPAL],
            text_auto=".1f",
        )
        fig_rm.update_traces(textposition="outside", cliponaxis=False)
        fig_rm.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_rm, width='stretch')

    with c2:
        fig_pm = px.bar(
            df_m, x="periodo", y="tempo_medio_processo_h",
            title="Tempo Medio em Processo (h) por Mes",
            labels={"periodo": "Mes", "tempo_medio_processo_h": "Horas"},
            color_discrete_sequence=[COR_PROCESSO],
            text_auto=".1f",
        )
        fig_pm.update_traces(textposition="outside", cliponaxis=False)
        fig_pm.update_layout(margin=dict(t=40, b=20))
        st.plotly_chart(fig_pm, width='stretch')

    st.subheader("Funil Semanal")
    fig_s = px.bar(
        df_s,
        y="periodo",
        x=["tickets_fechados", "em_processo"],
        barmode="group",
        orientation="h",
        title="Tickets por Semana",
        labels={"periodo": "Semana", "value": "Tickets", "variable": ""},
        color_discrete_map={
            "tickets_fechados": COR_FECHADO,
            "em_processo":      COR_PROCESSO,
        },
        text_auto=True,
    )
    fig_s.update_traces(textposition="outside", cliponaxis=False)
    fig_s.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_s, width='stretch')

    st.subheader("Dados do Funil")
    c_esq, c_dir = st.columns(2)
    with c_esq:
        st.caption("Mensal")
        st.dataframe(df_m, width='stretch')
    with c_dir:
        st.caption("Semanal")
        st.dataframe(df_s, width='stretch')


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
        text="total",
    )
    fig.update_traces(textposition="outside", cliponaxis=False)
    fig.update_layout(height=600, coloraxis_colorbar_title="% Fechado",
                      margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig, width='stretch')

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
    st.dataframe(df, width='stretch', column_config=col_cfg)


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
            text="total_tickets",
        )
        fig_v.update_traces(textposition="outside", cliponaxis=False)
        fig_v.update_layout(coloraxis_colorbar_title="% Fechado",
                            margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig_v, width='stretch')

    with c_resp:
        fig_r = px.bar(
            df.sort_values("tempo_medio_resposta_h", ascending=True).tail(20),
            x="tempo_medio_resposta_h",
            y="escritorio",
            orientation="h",
            title="Tempo Medio de Resposta (h) por Escritorio",
            labels={"tempo_medio_resposta_h": "Horas", "escritorio": ""},
            color_discrete_sequence=[COR_PROCESSO],
            text_auto=".1f",
        )
        fig_r.update_traces(textposition="outside", cliponaxis=False)
        fig_r.update_layout(margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig_r, width='stretch')

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
        text_auto=True,
    )
    fig_comp.update_traces(textposition="outside", cliponaxis=False)
    fig_comp.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_comp, width='stretch')

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
    st.dataframe(df, width='stretch', column_config=col_cfg)


# ---- Por Departamento ----

def tab_por_departamento(df: pd.DataFrame):
    if df.empty:
        st.info("Arquivo Departamentos.xlsx não encontrado ou sem dados para cruzar.")
        return

    st.subheader(f"Distribuicao por Departamento — {len(df)} departamentos")

    col_vol, col_resp = st.columns(2)

    with col_vol:
        fig_v = px.bar(
            df.sort_values("total_tickets", ascending=True),
            x="total_tickets",
            y="departamento",
            orientation="h",
            title="Volume de Tickets por Departamento",
            labels={"total_tickets": "Total", "departamento": ""},
            color="pct_fechado",
            color_continuous_scale="RdYlGn",
            range_color=[0, 100],
            text="total_tickets",
        )
        fig_v.update_traces(textposition="outside", cliponaxis=False)
        fig_v.update_layout(coloraxis_colorbar_title="% Fechado",
                            margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig_v, width='stretch')

    with col_resp:
        fig_r = px.bar(
            df.sort_values("tempo_medio_resposta_h", ascending=True),
            x="tempo_medio_resposta_h",
            y="departamento",
            orientation="h",
            title="Tempo Medio de Resposta (h) por Departamento",
            labels={"tempo_medio_resposta_h": "Horas", "departamento": ""},
            color_discrete_sequence=[COR_PROCESSO],
            text_auto=".1f",
        )
        fig_r.update_traces(textposition="outside", cliponaxis=False)
        fig_r.update_layout(margin=dict(t=40, b=20, r=60))
        st.plotly_chart(fig_r, width='stretch')

    # Fechados vs Em Processo
    fig_comp = px.bar(
        df.sort_values("fechados", ascending=True),
        x=["fechados", "em_processo"],
        y="departamento",
        orientation="h",
        barmode="group",
        title="Fechados vs Em Processo por Departamento",
        labels={"value": "Qtd", "departamento": "", "variable": ""},
        color_discrete_map={"fechados": COR_FECHADO, "em_processo": COR_PROCESSO},
        text_auto=True,
    )
    fig_comp.update_traces(textposition="outside", cliponaxis=False)
    fig_comp.update_layout(legend_title_text="", margin=dict(t=40, b=20, r=60))
    st.plotly_chart(fig_comp, width='stretch')

    # Transferencias — dados em tratamento
    st.subheader("Transferencias por Departamento")
    st.info("🔧 Dados em tratamento.")

    st.subheader("Tabela por Departamento")
    col_cfg = {
        "departamento":           st.column_config.TextColumn("Departamento", width="medium"),
        "total_tickets":          st.column_config.NumberColumn("Total"),
        "fechados":               st.column_config.NumberColumn("Fechados"),
        "em_processo":            st.column_config.NumberColumn("Em Processo"),
        "pct_fechado":            st.column_config.NumberColumn("% Fechado",       format="%.1f%%"),
        "com_sla_alerta":         st.column_config.NumberColumn("SLA Alerta"),
        "tempo_medio_resposta_h": st.column_config.NumberColumn("Resp. Media (h)",  format="%.1f"),
        "tempo_medio_processo_h": st.column_config.NumberColumn("Proc. Medio (h)",  format="%.1f"),
    }
    df_display = df.drop(columns=[c for c in ["n_transferencias_total", "n_transferencias_medio"] if c in df.columns])
    st.dataframe(df_display, width='stretch', column_config=col_cfg)


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
    # ── Sidebar — bloco único (evita double-context no Streamlit 1.57+) ─────
    with st.sidebar:
        st.markdown("## 📊 BKO Dashboard")
        st.markdown("---")

        uploaded = st.file_uploader(
            "Carregar arquivo",
            type=["csv", "xlsx", "xls"],
            help="CSV (separador ;) ou XLSX exportado do sistema de tickets",
        )

        if uploaded:
            st.success(f"Arquivo: **{uploaded.name}**")

        st.markdown("---")
        st.caption(f"SLA alerta configurado: > {prd.SLA_ALERTA_H}h")

        # Exportar — lê do session_state (disponível após pipeline concluir)
        if uploaded:
            _ck  = f"result_{uploaded.file_id}"
            _pk  = f"pdf_{uploaded.file_id}"
            _res = st.session_state.get(_ck)
            _pdf = st.session_state.get(_pk, b"")

            if _res is not None:
                st.markdown("---")
                st.markdown("#### 📥 Exportar")

                st.download_button(
                    label="📊 Baixar XLSX completo",
                    data=_res["xlsx"],
                    file_name=f"relatorio_bko_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    width='stretch',
                )

                if _pdf:
                    st.download_button(
                        label="📄 Baixar PDF",
                        data=_pdf,
                        file_name=f"relatorio_bko_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
                        mime="application/pdf",
                        width='stretch',
                    )
                else:
                    st.caption("PDF indisponivel — verifique os logs.")

                st.markdown("---")
                st.markdown("#### 📧 Enviar por E-mail")
                dest = st.text_input(
                    "Destinatario",
                    placeholder="email@exemplo.com",
                    key="email_dest",
                )
                if st.button("Enviar Relatorio", width='stretch', key="btn_email"):
                    if not dest or "@" not in dest:
                        st.warning("Informe um e-mail valido.")
                    else:
                        _ph = st.empty()
                        _ph.info(f"Enviando para {dest}...")
                        try:
                            _enviar_email(dest, _res["xlsx"], _res["ref_time"])
                            _ph.success(f"E-mail enviado para **{dest}**!")
                        except Exception as e_mail:
                            _ph.error(str(e_mail))

    # ── Sem arquivo — tela inicial ───────────────────────────────────────────
    if uploaded is None:
        tela_inicial()
        return

    # ── Com arquivo — pipeline ───────────────────────────────────────────────
    file_ext  = uploaded.name.rsplit(".", 1)[-1]
    cache_key = f"result_{uploaded.file_id}"
    if cache_key not in st.session_state:
        with st.status("Processando arquivo...", expanded=True) as status_ui:
            try:
                result = executar_pipeline(uploaded.getvalue(), file_ext, status_ui)
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

    # PDF gerado fora do sidebar (evita conflito de contexto com st.spinner)
    pdf_key = f"pdf_{uploaded.file_id}"
    if pdf_key not in st.session_state:
        with st.spinner("Gerando PDF..."):
            try:
                st.session_state[pdf_key] = _build_pdf(result)
                _log("PDF gerado com sucesso")
            except Exception as e_pdf:
                _log(f"Erro ao gerar PDF: {e_pdf}")
                st.session_state[pdf_key] = b""
        st.rerun()  # atualiza sidebar para exibir botao PDF

    # Big numbers
    st.markdown("---")
    big_numbers(result["geral"])
    st.markdown("---")

    # Abas
    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
        "📋 Aba Geral",
        "⏱ SLA Em Processo",
        "📨 SLA Resposta",
        "📊 Funil",
        "🏷 Por Assunto",
        "🏢 Por Escritorio",
        "🏬 Por Departamento",
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

    with tab7:
        tab_por_departamento(result.get("depto", pd.DataFrame()))


if __name__ == "__main__":
    main()
