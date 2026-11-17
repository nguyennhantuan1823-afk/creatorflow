"""Thành phần giao diện dùng chung: CSS, thẻ số liệu, biểu đồ, bảng."""
import pandas as pd
import plotly.express as px
import streamlit as st

import services as sv
from db import STATUS_LABEL
from services import vnd

STATUS_EMOJI = {
    "GENERATED": "⚪", "UNDER_REVIEW": "🟡", "APPROVED": "🔵", "PENDING_PAYMENT": "🟣",
    "PAID_UNRECONCILED": "🔷", "RECONCILED": "🟢", "PARTIALLY_PAID": "🟩", "OVERDUE": "🔴",
    "DISPUTED": "🟠", "CANCELLED": "⚫",
}
STATUS_COLORS = {
    "Phát sinh doanh thu": "#94a3b8", "Chờ xác nhận / nghiệm thu": "#f59e0b", "Được duyệt": "#3b82f6",
    "Chờ thanh toán": "#8b5cf6", "Đã thanh toán / chờ đối soát": "#06b6d4", "Đã nhận & đối soát": "#10b981",
    "Nhận một phần": "#84cc16", "Trễ hạn thanh toán": "#ef4444", "Tranh chấp": "#f97316", "Đã hủy": "#6b7280",
}
PALETTE = ["#4f46e5", "#0ea5e9", "#14b8a6", "#f59e0b", "#a855f7", "#64748b", "#ec4899", "#84cc16"]


def status_txt(code):
    return f"{STATUS_EMOJI.get(code, '')} {STATUS_LABEL.get(code, code)}"


CSS = """
<style>
.block-container{padding-top:3.4rem;padding-bottom:3rem;max-width:1320px}
#MainMenu{visibility:hidden} footer{visibility:hidden}
.stAppDeployButton,[data-testid="stAppDeployButton"]{display:none}
.cf-head{margin:0 0 16px}
.cf-head .t{font-size:1.75rem;font-weight:800;line-height:1.2;letter-spacing:-.3px}
.cf-head .s{margin-top:4px;font-size:.98rem;opacity:.68}
.cf-asof{padding-top:8px;font-size:.92rem;opacity:.7}
.cf-card{border:1px solid rgba(128,128,128,.26);border-radius:12px;padding:14px 16px;background:rgba(128,128,128,.055);height:100%}
.cf-card .l{font-size:.86rem;opacity:.78;font-weight:600}
.cf-card .v{font-size:1.5rem;font-weight:800;line-height:1.3;margin-top:3px}
.cf-card .h{font-size:.78rem;opacity:.62;margin-top:3px;line-height:1.35}
.cf-card.good{background:rgba(16,185,129,.1);border-color:rgba(16,185,129,.5)}
.cf-card.good .v{color:#059669}
.cf-card.bad{background:rgba(239,68,68,.08);border-color:rgba(239,68,68,.45)}
.cf-card.bad .v{color:#dc2626}
.cf-up{color:#059669;font-weight:700;font-size:.78rem;margin-top:3px} .cf-down{color:#dc2626;font-weight:700;font-size:.78rem;margin-top:3px}
.cf-sec{font-size:1.15rem;font-weight:700;margin:26px 0 2px}
.cf-sec-s{font-size:.88rem;opacity:.65;margin-bottom:8px}
.cf-fun{margin:10px 0}.cf-fun-h{display:flex;justify-content:space-between;font-size:.9rem}
.cf-bar{height:10px;border-radius:99px;background:rgba(128,128,128,.2);overflow:hidden;margin-top:5px}
.cf-bar>div{height:100%;border-radius:99px}
.cf-todo{display:flex;align-items:center;gap:12px;padding:9px 2px;border-bottom:1px solid rgba(128,128,128,.2);font-size:.93rem}
.cf-todo .n{font-size:1.35rem;font-weight:800;min-width:30px;text-align:center}
.cf-step{display:inline-block;background:#4f46e5;color:#fff;border-radius:99px;padding:1px 10px;font-size:.78rem;font-weight:700;margin-right:6px}
.cf-words{background:rgba(16,185,129,.12);border-left:4px solid #10b981;border-radius:8px;padding:8px 12px;font-size:.92rem;margin:2px 0 8px}
[data-testid="stSidebar"] .stRadio label{padding:6px 2px;font-size:1rem}
div[data-testid="stMetric"]{background:rgba(128,128,128,.055);border-radius:12px;padding:10px 14px;border:1px solid rgba(128,128,128,.22)}
button[kind="primary"]{font-weight:700}
</style>
"""


def inject_css():
    st.markdown(CSS, unsafe_allow_html=True)


def hero(icon, title, subtitle):
    st.markdown(f'<div class="cf-head"><div class="t">{title}</div><div class="s">{subtitle}</div></div>',
                unsafe_allow_html=True)


def section(title, sub=""):
    st.markdown(f'<div class="cf-sec">{title}</div>' + (f'<div class="cf-sec-s">{sub}</div>' if sub else ""),
                unsafe_allow_html=True)


def kpi(col, label, value, hint="", color=None, delta=None):
    """Thẻ số liệu trung tính; chỉ xanh (tiền đã về) hoặc đỏ (trễ hạn) được tô màu."""
    tone = "good" if color == "#10b981" else "bad" if color == "#ef4444" else ""
    d = ""
    if delta is not None:
        cls = "cf-up" if delta >= 0 else "cf-down"
        d = f'<div class="{cls}">{"▲" if delta >= 0 else "▼"} {abs(delta):.1f}% so với kỳ trước</div>'
    col.markdown(f'<div class="cf-card {tone}"><div class="l">{label}</div><div class="v">{value}</div>'
                 f'<div class="h">{hint}</div>{d}</div>', unsafe_allow_html=True)


def pct_change(cur, prev):
    if prev is None or prev == 0:
        return None
    return (cur - prev) / prev * 100


def words_box(amount):
    st.markdown(f'<div class="cf-words">💬 Bằng chữ: <b>{sv.vn_words(amount)}</b>'
                f' &nbsp;·&nbsp; {vnd(amount)}</div>', unsafe_allow_html=True)


def show_msgs(errors=(), warns=()):
    for e in errors:
        st.error(e, icon="⛔")
    for w in warns:
        st.warning(w, icon="⚠️")


def funnel(rows):
    base = max(rows[0][1], 1)
    html = ""
    for label, val, color in rows:
        pct = val / base * 100
        html += (f'<div class="cf-fun"><div class="cf-fun-h"><span>{label}</span><b>{vnd(val)} '
                 f'<small style="opacity:.7">({pct:.0f}%)</small></b></div><div class="cf-bar">'
                 f'<div style="width:{min(pct, 100):.1f}%;background:{color}"></div></div></div>')
    st.markdown(html, unsafe_allow_html=True)


def style_fig(fig, title=None, unit="triệu đồng"):
    fig.update_layout(
        title=dict(text=title, x=0, font=dict(size=16)) if title else None,
        margin=dict(l=8, r=8, t=52 if title else 16, b=8), height=350,
        legend=dict(orientation="h", y=-0.22, title=None), hovermode="closest",
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family="Inter, Segoe UI, Arial, sans-serif", size=13),
        colorway=PALETTE,
    )
    fig.update_xaxes(showgrid=False, title=None)
    fig.update_yaxes(gridcolor="rgba(128,128,128,.2)", title=unit, zeroline=False)
    return fig


def empty_chart_note():
    st.info("Không có dữ liệu trong bộ lọc hiện tại. Hãy nới rộng kỳ xem hoặc bỏ bớt điều kiện lọc.", icon="🔎")


# ---------- Dữ liệu cho biểu đồ ----------
def build_chart_df(df, metric, group, split=False):
    d = df.copy()
    d["value"] = sv.metric_value(d, metric) / 1e6
    gd = pd.to_datetime(d["generated_date"])
    if group == "Tháng":
        d["key"] = gd.dt.strftime("%Y-%m")
        d["x"] = gd.dt.strftime("Th%m/%Y")
    elif group == "Tuần":
        mon = gd - pd.to_timedelta(gd.dt.weekday, unit="D")
        d["key"] = mon.dt.strftime("%Y-%m-%d")
        d["x"] = mon.dt.strftime("Tuần %d/%m")
    elif group == "Ngày":
        d["key"] = gd.dt.strftime("%Y-%m-%d")
        d["x"] = gd.dt.strftime("%d/%m")
    elif group == "Nguồn thu":
        d["key"] = d["x"] = d["source"]
    else:
        d["key"] = d["x"] = d["status"].map(STATUS_LABEL)
    color = "source" if (split and group in ("Tháng", "Tuần", "Ngày")) else None
    keys = ["key", "x"] + ([color] if color else [])
    g = d.groupby(keys, as_index=False)["value"].sum()
    if group in ("Nguồn thu", "Trạng thái"):
        g = g.sort_values("value", ascending=False)
    else:
        g = g.sort_values("key")
    return g, color


def main_chart(df, metric, group, kind, split):
    g, color = build_chart_df(df, metric, group, split)
    if g["value"].sum() == 0:
        st.info(f"Chưa có số liệu **{metric.lower()}** trong bộ lọc này.", icon="ℹ️")
        return
    order = list(dict.fromkeys(g["x"]))
    time_group = group in ("Tháng", "Tuần", "Ngày")
    cmap = STATUS_COLORS if group == "Trạng thái" else None
    ckey = color or ("x" if not time_group else None)
    kw = dict(x="x", y="value", color=ckey, category_orders={"x": order}, color_discrete_map=cmap,
              color_discrete_sequence=PALETTE,
              labels={"x": "", "value": "Triệu đồng", "source": "Nguồn"})
    if kind == "Đường":
        fig = px.line(g, markers=True, **kw)
    elif kind == "Vùng" and time_group:
        fig = px.area(g, **kw)
    else:
        fig = px.bar(g, **kw)
        fig.update_layout(bargap=0.3)
    fig.update_traces(hovertemplate="%{x}<br><b>%{y:,.2f} triệu đồng</b><extra>%{fullData.name}</extra>")
    if ckey == "x":
        fig.update_layout(showlegend=False)
    st.plotly_chart(style_fig(fig, f"{metric} theo {group.lower()}"))


def share_chart(df, metric):
    d = df.copy()
    d["value"] = sv.metric_value(d, metric) / 1e6
    g = d.groupby("source", as_index=False)["value"].sum()
    g = g[g["value"] > 0]
    if g.empty:
        st.info("Chưa có số liệu để vẽ tỷ trọng.", icon="ℹ️")
        return
    fig = px.pie(g, names="source", values="value", hole=0.55, color_discrete_sequence=PALETTE)
    fig.update_traces(textposition="inside", textinfo="percent",
                      hovertemplate="%{label}<br><b>%{value:,.2f} triệu đồng</b> (%{percent})<extra></extra>")
    fig.update_layout(showlegend=True, legend=dict(orientation="v", x=1.02, y=0.5))
    st.plotly_chart(style_fig(fig, f"Tỷ trọng nguồn thu · {metric.lower()}"))


# ---------- Bảng ----------
def income_table(df, key, selectable=False, height=None):
    """Hiển thị bảng khoản thu: số tiền dạng số (sắp xếp đúng), ngày chuẩn, trạng thái có màu."""
    if df.empty:
        st.info("Chưa có khoản thu nào phù hợp.", icon="📭")
        return None
    d = pd.DataFrame({
        "Mã": df["code"], "Nguồn": df["source"], "Nội dung": df["title"],
        "Số tiền (đ)": df["amount"], "Trạng thái": df["status"].map(status_txt),
        "Đã nhận (đ)": df["received_amount"], "Còn lại (đ)": df["amount"] - df["received_amount"],
        "Hạn dự kiến": pd.to_datetime(df["expected_pay_date"]),
        "Ngày phát sinh": pd.to_datetime(df["generated_date"]), "Cách nhập": df["origin"],
    })
    money = st.column_config.NumberColumn(format="localized")
    cfg = {"Số tiền (đ)": money, "Đã nhận (đ)": money, "Còn lại (đ)": money,
           "Ngày phát sinh": st.column_config.DateColumn(format="DD/MM/YYYY"),
           "Hạn dự kiến": st.column_config.DateColumn(format="DD/MM/YYYY"),
           "Mã": st.column_config.TextColumn(width="small"),
           "Trạng thái": st.column_config.TextColumn(width="medium")}
    kw = dict(hide_index=True, column_config=cfg, key=key)
    if height:
        kw["height"] = height
    if selectable:
        return st.dataframe(d, on_select="rerun", selection_mode="single-row", **kw)
    st.dataframe(d, **kw)
    return None
