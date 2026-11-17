"""CreatorFlow - Hệ thống quản lý và đối soát vòng đời thu nhập đa nền tảng.
Chạy:  streamlit run app.py"""
import io
import json
import os
import re
import uuid
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import db
import services as sv
import ui
from db import STATUS_LABEL, TRANSITIONS
from services import vnd
from ui import status_txt

st.set_page_config(page_title="CreatorFlow", page_icon="💸", layout="wide")

P_DASH, P_SRC, P_INC = "📊 Tổng quan", "🗂️ Nguồn thu", "💰 Khoản thu"
P_PAY, P_REC, P_REP, P_GUIDE = "⏳ Theo dõi thanh toán", "🏦 Đối soát", "📈 Báo cáo", "ℹ️ Hướng dẫn"
PAGES = [P_DASH, P_SRC, P_INC, P_PAY, P_REC, P_REP, P_GUIDE]
GROUPS = ["Tháng", "Tuần", "Ngày", "Nguồn thu", "Trạng thái"]
KINDS = ["Cột", "Đường", "Vùng"]


# ---------- Tiện ích chung ----------
def flash(msg, kind="success"):
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash():
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind)(msg, icon={"success": "✅", "error": "⛔", "warning": "⚠️", "info": "ℹ️"}.get(kind))


def go(page):
    st.session_state["nav"] = page


def bump(name):
    st.session_state[name] = st.session_state.get(name, 0) + 1


def income_label(r):
    return f"{r['code']} · {r['source']} · {vnd(r['amount'])} · {status_txt(r['status'])}"


def pick_income(df, key, label="Chọn khoản thu"):
    if df.empty:
        return None
    rows = {int(r["id"]): income_label(r) for _, r in df.iterrows()}
    return int(st.selectbox(label, list(rows), format_func=lambda i: rows[i], key=key))


def period_filter(prefix, cols=None):
    """Bộ lọc kỳ xem dùng chung; trả về (start, end)."""
    host = cols[0] if cols else st
    period = host.selectbox("📅 Kỳ xem", sv.PERIODS, key=f"{prefix}_period")
    custom = None
    if period == "Tùy chọn":
        custom = st.date_input("Chọn khoảng ngày", (date.today() - timedelta(days=30), date.today()),
                               format="DD/MM/YYYY", key=f"{prefix}_custom")
    return sv.period_range(period, custom)


@st.dialog("Xác nhận xóa")
def dlg_delete(kind, rid, label, extra=""):
    st.warning(f"Bạn sắp xóa **{label}**. {extra} Thao tác này không thể hoàn tác.", icon="⚠️")
    c1, c2 = st.columns(2)
    if c1.button("🗑️ Xóa", type="primary", key="dlg_yes"):
        try:
            if kind == "income":
                db.delete_income(rid)
                bump("tbl_ver")
            elif kind == "source":
                db.delete_source(rid, cascade=True)
            elif kind == "txn":
                db.delete_bank_txn(rid)
            flash(f"Đã xóa {label}.")
        except ValueError as e:
            flash(str(e), "error")
        st.rerun()
    if c2.button("Hủy", key="dlg_no"):
        st.rerun()


MANUAL_BLOCKED = {"RECONCILED", "PARTIALLY_PAID"}  # hai trạng thái này chỉ đến từ bước đối soát sao kê


def status_actions(iid, key):
    inc = db.get_income(iid)
    opts = [s for s in TRANSITIONS[inc["status"]] if s not in MANUAL_BLOCKED]
    st.markdown(f"**Trạng thái hiện tại:** {status_txt(inc['status'])}")
    if not opts:
        if inc["status"] in ("RECONCILED", "CANCELLED"):
            st.info("Khoản thu đã ở trạng thái cuối, không thể chuyển tiếp.")
        else:
            st.info("Bước tiếp theo của khoản này được cập nhật ở trang **Đối soát** khi có giao dịch ngân hàng.")
        return
    c1, c2, c3 = st.columns([2.2, 3, 1.2])
    new = c1.selectbox("Chuyển sang trạng thái", opts, format_func=status_txt, key=f"{key}_new")
    note = c2.text_input("Ghi chú (không bắt buộc)", key=f"{key}_note", max_chars=200)
    c3.write("")
    c3.write("")
    if c3.button("Cập nhật", key=f"{key}_btn", type="primary"):
        try:
            db.change_status(iid, new, note)
            flash(f"Đã chuyển **{inc['code']}** sang '{STATUS_LABEL[new]}'.")
        except ValueError as e:
            flash(str(e), "error")
        st.rerun()
    if "PAID_UNRECONCILED" in opts:
        st.caption("💡 Chọn *Đã thanh toán / chờ đối soát* khi nền tảng báo đã chuyển tiền, rồi sang trang "
                   "**Đối soát** để khớp với sao kê ngân hàng.")


# ---------- Trang: Tổng quan ----------
def cashflow_chart(df):
    w = df[df["status"].isin(db.WAITING_PAY_SET)].copy()
    w["remaining"] = (w["amount"] - w["received_amount"]) / 1e6
    w = w[w["remaining"] > 0]
    if w.empty:
        st.info("Không có khoản nào đang chờ tiền về.", icon="🎉")
        return
    days = (pd.to_datetime(w["expected_pay_date"]) - pd.Timestamp(date.today())).dt.days

    def idx(d):
        return -1 if d < 0 else (6 if d >= 42 else d // 7)

    def lab(k):
        if k == -1:
            return "Đã quá hạn"
        if k == 6:
            return "Sau 6 tuần"
        return f"Tuần từ {(date.today() + timedelta(days=k * 7)):%d/%m}"
    w["Thời điểm"] = days.map(lambda d: lab(idx(d)))
    order = [lab(k) for k in sorted(set(days.map(idx)))]
    cf = w.groupby("Thời điểm", as_index=False)["remaining"].sum()
    cf["color"] = cf["Thời điểm"].map(lambda t: "Quá hạn" if t == "Đã quá hạn" else "Đúng hạn")
    fig = px.bar(cf, x="Thời điểm", y="remaining", color="color", category_orders={"Thời điểm": order},
                 color_discrete_map={"Quá hạn": "#ef4444", "Đúng hạn": "#6366f1"},
                 labels={"remaining": "Triệu đồng"})
    fig.update_traces(hovertemplate="%{x}<br><b>%{y:,.2f} triệu đồng</b> dự kiến về<extra></extra>")
    fig.update_layout(bargap=0.35, showlegend=False)
    st.plotly_chart(ui.style_fig(fig))


def todo_panel(df_all):
    bd = sv.bank_df()
    n_bank = int(bd["match_status"].isin(["REVIEW", "UNMATCHED"]).sum()) if not bd.empty else 0
    items = [
        (int((df_all["status"] == "OVERDUE").sum()), "khoản <b>trễ hạn</b> thanh toán", P_PAY),
        (int((df_all["status"] == "PAID_UNRECONCILED").sum()), "khoản chờ <b>đối soát</b> với sao kê", P_REC),
        (n_bank, "giao dịch ngân hàng cần <b>kiểm tra</b>", P_REC),
        (int((df_all["status"] == "APPROVED").sum()), "khoản đã duyệt, chưa chuyển sang <b>chờ thanh toán</b>", P_PAY),
        (int((df_all["status"] == "UNDER_REVIEW").sum()), "khoản đang <b>chờ xác nhận</b> từ nền tảng/khách", P_INC),
    ]
    items = [i for i in items if i[0] > 0]
    if not items:
        st.success("Mọi thứ đã gọn gàng, không có việc tồn đọng.", icon="🎉")
        return
    for n, (cnt, text, page) in enumerate(items):
        a, b, c = st.columns([0.6, 2.8, 1.6], vertical_alignment="center")
        a.markdown(f'<div class="cf-todo" style="border:0"><span class="n">{cnt}</span></div>', unsafe_allow_html=True)
        b.markdown(f'<div style="font-size:.93rem;line-height:1.35">{text}</div>', unsafe_allow_html=True)
        c.button("Xử lý", key=f"todo_{n}", on_click=go, args=(page,))


def intro_banner():
    with st.container(border=True):
        a, b, c = st.columns([7, 1.7, 0.8], vertical_alignment="center")
        a.markdown("👋 **Mới dùng?** Chỉ cần 3 bước: **① Thêm khoản thu** (trang Khoản thu) → **② Theo dõi thanh toán** "
                   "→ **③ Đối soát với sao kê**. Các con số bên dưới tự cập nhật theo từng bước.")
        b.button("Xem hướng dẫn", on_click=go, args=(P_GUIDE,), key="intro_guide")
        c.button("Ẩn", key="intro_hide", on_click=lambda: st.session_state.update(hide_intro=True))


def chart_controls(prefix, with_split=False):
    """Hàng điều khiển nhỏ đặt ngay phía trên biểu đồ mà nó điều chỉnh."""
    n = 4 if with_split else 3
    c = st.columns([2, 3.6, 1.8, 1.7][:n], vertical_alignment="bottom")
    metric = c[0].selectbox("Chỉ số", sv.METRICS, key=f"{prefix}_metric")
    return metric, c


def page_dashboard():
    ui.hero("", "Tổng quan thu nhập", "Tiền bạn đã kiếm, tiền đang chờ về và tiền thật sự đã vào tài khoản.")
    df_all = sv.income_df()
    if df_all.empty:
        st.info("Chưa có khoản thu nào. Vào **💰 Khoản thu** để thêm hoặc nhập dữ liệu, "
                "hoặc bấm *Khôi phục dữ liệu mẫu* ở thanh bên.", icon="👋")
        return
    if not st.session_state.get("hide_intro"):
        intro_banner()

    # Bộ lọc gọn: chỉ 2 ô, đặt ngay dưới tiêu đề
    c = st.columns([3, 1.5, 2.2], vertical_alignment="center")
    c[0].markdown(f'<div class="cf-asof">Số liệu tính đến <b>{date.today():%d/%m/%Y}</b></div>', unsafe_allow_html=True)
    period = c[1].selectbox("Kỳ xem", sv.PERIODS, key="d_period", format_func=lambda p: f"Kỳ xem: {p}",
                            label_visibility="collapsed")
    sources = c[2].multiselect("Nguồn thu", sorted(df_all["source"].unique()), key="d_src",
                               placeholder="Tất cả nguồn thu", label_visibility="collapsed")
    custom = None
    if period == "Tùy chọn":
        custom = st.date_input("Chọn khoảng ngày", (date.today() - timedelta(days=30), date.today()),
                               format="DD/MM/YYYY", key="d_custom")
    start, end = sv.period_range(period, custom)
    df = sv.filter_df(df_all, start, end, sources)
    k = sv.kpis(df)
    ps, pe = sv.prev_period(start, end)
    kp = sv.kpis(sv.filter_df(df_all, ps, pe, sources)) if ps else None

    # 1) KPI ở trên cùng
    cols = st.columns(5)
    ui.kpi(cols[0], "Doanh thu phát sinh", vnd(k["generated"]), "Tổng các khoản đã ghi nhận", None,
           ui.pct_change(k["generated"], kp["generated"]) if kp else None)
    ui.kpi(cols[1], "Thu nhập được duyệt", vnd(k["approved"]), "Đã được xác nhận sẽ trả")
    ui.kpi(cols[2], "Còn chờ thanh toán", vnd(k["waiting"]), "Đã duyệt, tiền chưa về")
    ui.kpi(cols[3], "Chờ đối soát", vnd(k["unreconciled"]), "Nền tảng báo đã trả, chưa khớp sao kê")
    ui.kpi(cols[4], "Tiền đã thực nhận", vnd(k["received"]), "Đã khớp với sao kê ngân hàng", "#10b981",
           ui.pct_change(k["received"], kp["received"]) if kp else None)
    st.caption("Lưu ý: các số trên là các bước của **cùng một dòng tiền**, **không cộng với nhau**.")
    if k["overdue"] > 0:
        st.warning(f"Có khoản **trễ hạn** tổng {vnd(k['overdue'])}. Bấm *Xử lý* ở mục Việc cần làm bên dưới.", icon="🚨")

    # 2) Biểu đồ chính + việc cần làm
    left, right = st.columns([2.3, 1])
    with left:
        ui.section("Biểu đồ thu nhập")
        cc = st.columns([2, 4], vertical_alignment="bottom")
        metric = cc[0].selectbox("Chỉ số", sv.METRICS, key="d_metric")
        group = cc[1].segmented_control("Xem theo", GROUPS, default="Tháng", key="d_group",
                                        format_func=lambda g: "Nguồn" if g == "Nguồn thu" else g) or "Tháng"
        kind = "Cột"
        if df.empty:
            ui.empty_chart_note()
        else:
            ui.main_chart(df, metric, group, kind, True)
    with right:
        ui.section("Việc cần làm", "Bấm Xử lý để tới đúng trang")
        todo_panel(df_all)

    # 3) Hành trình dòng tiền + dòng tiền dự kiến
    a, b = st.columns(2)
    with a:
        ui.section("Hành trình dòng tiền", "Từ lúc phát sinh đến khi tiền thật sự về tài khoản")
        ui.funnel([("1. Doanh thu phát sinh", k["generated"], "#6366f1"),
                   ("2. Được duyệt", k["approved"], "#3b82f6"),
                   ("3. Đã thanh toán (nền tảng báo)", k["paid"], "#06b6d4"),
                   ("4. Thực nhận (đã đối soát)", k["received"], "#10b981")])
    with b:
        ui.section("Tiền dự kiến về", "Theo hạn thanh toán của các khoản đang chờ")
        cashflow_chart(df)

    # 4) Bảng gọn
    ui.section("Khoản sắp đến hạn / đã trễ hạn")
    due = df[df["status"].isin(db.WAITING_PAY_SET)].copy() if not df.empty else df
    if due.empty:
        st.success("Không có khoản nào đang chờ thanh toán.", icon="🎉")
    else:
        due = due.sort_values("expected_pay_date").head(5)
        ui.income_table(due, "dash_due")


# ---------- Trang: Nguồn thu ----------
def page_sources():
    ui.hero("🗂️", "Nguồn thu", "Nơi tiền đến từ đâu: YouTube, TikTok, khách hàng freelance... "
            "Mỗi nguồn có chu kỳ thanh toán riêng. Số tiền cụ thể được nhập ở trang Khoản thu.")
    srcs = db.list_sources()
    df = sv.income_df()
    if srcs:
        rows = []
        for s in srcs:
            d = df[df["source"] == s["name"]] if not df.empty else df
            live = d[d["status"] != "CANCELLED"] if not d.empty else d
            rows.append({"Nguồn": s["name"], "Loại hình": s["source_type"],
                         "Chu kỳ": f"{s['pay_days']} ngày sau duyệt", "Điều kiện nhận tiền": s["pay_rule"],
                         "Số khoản": len(d), "Tổng doanh thu (đ)": float(live["amount"].sum()) if len(live) else 0.0,
                         "Đã thực nhận (đ)": float(live["received_amount"].sum()) if len(live) else 0.0})
        tbl = pd.DataFrame(rows)
        money = st.column_config.NumberColumn(format="localized")
        st.dataframe(tbl.drop(columns=["Điều kiện nhận tiền"]), hide_index=True,
                     column_config={"Tổng doanh thu (đ)": money, "Đã thực nhận (đ)": money})
    else:
        st.info("Chưa có nguồn thu nào. Hãy thêm nguồn đầu tiên ở tab bên dưới.", icon="👋")

    t1, t2 = st.tabs(["➕ Thêm nguồn thu", "✏️ Sửa / 🗑️ Xóa"])
    with t1:
        v = st.session_state.get("src_ver", 0)
        c1, c2 = st.columns(2)
        name = c1.text_input("Tên nguồn thu *", key=f"sn_{v}", max_chars=40, placeholder="VD: Shopee Affiliate")
        typ = c2.selectbox("Loại hình", db.SOURCE_TYPES, key=f"st_{v}")
        days = c1.number_input("Số ngày từ lúc duyệt đến hạn thanh toán", 0, 365, 14, key=f"sd_{v}",
                               help="Dùng để tự tính ngày dự kiến nhận tiền cho các khoản thu thuộc nguồn này.")
        rule = c2.text_input("Điều kiện nhận tiền (không bắt buộc)", key=f"sr_{v}", max_chars=120,
                             placeholder="VD: Đạt ngưỡng 100 USD mới được thanh toán")
        errs = sv.validate_source(name, days) if name.strip() else []
        ui.show_msgs(errs)
        if st.button("Thêm nguồn thu", type="primary", disabled=not name.strip() or bool(errs), key=f"sb_{v}"):
            try:
                db.add_source(name, typ, days, rule)
                flash(f"Đã thêm nguồn **{name.strip()}**. Giờ hãy thêm khoản thu cho nguồn này ở trang 💰 Khoản thu.")
                bump("src_ver")
            except ValueError as e:
                flash(str(e), "error")
            st.rerun()
    with t2:
        if not srcs:
            st.info("Chưa có nguồn nào để sửa.")
            return
        sid = st.selectbox("Chọn nguồn", [s["id"] for s in srcs],
                           format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i), key="src_edit_pick")
        s = next(x for x in srcs if x["id"] == sid)
        c1, c2 = st.columns(2)
        name = c1.text_input("Tên nguồn thu", s["name"], key=f"en_{sid}", max_chars=40)
        typ = c2.selectbox("Loại hình", db.SOURCE_TYPES, key=f"et_{sid}",
                           index=db.SOURCE_TYPES.index(s["source_type"]) if s["source_type"] in db.SOURCE_TYPES else 4)
        days = c1.number_input("Số ngày đến hạn thanh toán", 0, 365, int(s["pay_days"]), key=f"ed_{sid}")
        rule = c2.text_input("Điều kiện nhận tiền", s["pay_rule"] or "", key=f"er_{sid}", max_chars=120)
        errs = sv.validate_source(name, days, exclude_id=sid)
        ui.show_msgs(errs)
        if st.button("Lưu thay đổi", type="primary", disabled=bool(errs), key=f"esave_{sid}"):
            try:
                db.update_source(sid, name, typ, days, rule)
                flash("Đã lưu thay đổi nguồn thu. (Ngày dự kiến của các khoản đã có không bị đổi.)")
            except ValueError as e:
                flash(str(e), "error")
            st.rerun()
        st.divider()
        n = db.count_incomes_of_source(sid)
        if st.button("🗑️ Xóa nguồn này", key=f"del_src_{sid}"):
            dlg_delete("source", sid, f"nguồn '{s['name']}'",
                       f"Có {n} khoản thu thuộc nguồn này sẽ bị xóa theo." if n else "")


# ---------- Trang: Khoản thu ----------
def page_incomes():
    ui.hero("💰", "Khoản thu", "Mỗi lần có tiền phát sinh là một khoản thu. Theo dõi nó từ lúc phát sinh đến khi tiền "
            "về tài khoản; thêm bằng tay, hoặc nhập nhanh từ API / file Excel.")
    srcs = db.list_sources()
    if not srcs:
        st.warning("Hãy tạo ít nhất một nguồn thu ở trang **🗂️ Nguồn thu** trước.", icon="👉")
        return
    df = sv.income_df()
    tabs = st.tabs(["📋 Danh sách & chi tiết", "➕ Thêm khoản thu", "⬇️ Nhập từ API / file"])
    with tabs[0]:
        incomes_list(df)
    with tabs[1]:
        income_add_form(srcs)
    with tabs[2]:
        import_section(srcs)


def incomes_list(df):
    if df.empty:
        st.info("Chưa có khoản thu nào. Sang tab **➕ Thêm khoản thu** hoặc **⬇️ Nhập từ API / file**.", icon="📭")
        return
    c = st.columns([3, 2, 2], vertical_alignment="center")
    q = c[0].text_input("Tìm kiếm", key="l_q", placeholder="🔎 Tìm theo mã hoặc nội dung (VD: YT001)",
                        label_visibility="collapsed")
    fs = c[1].multiselect("Nguồn", sorted(df["source"].unique()), key="l_src", placeholder="Tất cả nguồn thu",
                          label_visibility="collapsed")
    fst = c[2].multiselect("Trạng thái", list(STATUS_LABEL), format_func=status_txt, key="l_st",
                           placeholder="Tất cả trạng thái", label_visibility="collapsed")
    with st.expander("Lọc thêm: kỳ xem, sắp xếp"):
        c2 = st.columns([1.3, 1.3, 2])
        start, end = period_filter("l", c2)
        sort = c2[1].selectbox("Sắp xếp", ["Mới nhất", "Số tiền giảm dần", "Hạn thanh toán gần nhất"], key="l_sort")
    f = sv.filter_df(df, start, end, fs, fst)
    if q.strip():
        m = (f["code"].str.contains(q.strip(), case=False, na=False, regex=False)
             | f["title"].fillna("").str.contains(q.strip(), case=False, na=False, regex=False))
        f = f[m]
    if sort == "Số tiền giảm dần":
        f = f.sort_values("amount", ascending=False)
    elif sort == "Hạn thanh toán gần nhất":
        f = f.sort_values("expected_pay_date")
    else:
        f = f.sort_values(["generated_date", "id"], ascending=False)
    live = f[f["status"] != "CANCELLED"]
    st.markdown(f"**{len(f)} khoản** · Tổng **{vnd(live['amount'].sum())}** · Đã nhận **{vnd(live['received_amount'].sum())}** · "
                f"Còn phải thu **{vnd((live['amount'] - live['received_amount']).sum())}**")
    ev = ui.income_table(f, f"inc_tbl_{st.session_state.get('tbl_ver', 0)}", selectable=True)
    sel = ev.selection.rows if ev is not None else []
    if sel and sel[0] < len(f):
        income_detail(int(f.iloc[sel[0]]["id"]))
    elif not f.empty:
        st.caption("👆 **Bấm vào một dòng** trong bảng để xem chi tiết, đổi trạng thái, sửa hoặc xóa khoản thu đó.")


def income_detail(iid):
    inc = db.get_income(iid)
    if not inc:
        return
    srcs = db.list_sources()
    with st.container(border=True):
        st.markdown(f"#### 🧾 {inc['code']} · {inc['source_name']}")
        if inc["title"]:
            st.caption(inc["title"])
        c = st.columns(4)
        remaining = inc["amount"] - inc["received_amount"]
        ui.kpi(c[0], "Số tiền", vnd(inc["amount"]), "Giá trị khoản thu", "#6366f1")
        ui.kpi(c[1], "Đã thực nhận", vnd(inc["received_amount"]), "Đã khớp sao kê", "#10b981" if inc["received_amount"] > 0 else None)
        ui.kpi(c[2], "Còn lại", vnd(max(remaining, 0)), "Chưa về tài khoản", "#f59e0b")
        exp = date.fromisoformat(inc["expected_pay_date"]).strftime("%d/%m/%Y") if inc["expected_pay_date"] else "-"
        ui.kpi(c[3], "Hạn dự kiến", exp, f"Phát sinh {date.fromisoformat(inc['generated_date']):%d/%m/%Y}", "#8b5cf6")
        pct = min(inc["received_amount"] / inc["amount"], 1.0) if inc["amount"] else 0
        st.progress(pct, text=f"Đã nhận {pct:.0%} giá trị khoản thu")
        status_actions(iid, f"inc_{iid}")
        t = st.tabs(["🕘 Lịch sử trạng thái", "✏️ Sửa thông tin", "🗑️ Xóa"])
        with t[0]:
            h = pd.DataFrame(db.history_of(iid))
            h["from_status"] = h["from_status"].map(lambda s_: status_txt(s_) if isinstance(s_, str) and s_ else "—")
            h["to_status"] = h["to_status"].map(status_txt)
            st.dataframe(h[["changed_at", "from_status", "to_status", "note"]].rename(columns={
                "changed_at": "Thời điểm", "from_status": "Từ", "to_status": "Sang", "note": "Ghi chú"}),
                hide_index=True)
        with t[1]:
            c1, c2 = st.columns(2)
            ids = [s["id"] for s in srcs]
            sid = c1.selectbox("Nguồn thu", ids, index=ids.index(inc["source_id"]), key=f"ei_src_{iid}",
                               format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i))
            amount = c2.number_input("Số tiền (đồng)", 0, sv.MAX_AMOUNT, int(inc["amount"]), 100_000, key=f"ei_amt_{iid}")
            ui.words_box(amount)
            title = c1.text_input("Nội dung", inc["title"] or "", key=f"ei_title_{iid}", max_chars=120)
            gd = c2.date_input("Ngày phát sinh", date.fromisoformat(inc["generated_date"]), max_value=date.today(),
                               format="DD/MM/YYYY", key=f"ei_gd_{iid}")
            ex = c1.date_input("Hạn thanh toán dự kiến", date.fromisoformat(inc["expected_pay_date"] or inc["generated_date"]),
                               format="DD/MM/YYYY", key=f"ei_ex_{iid}")
            note = c2.text_input("Ghi chú", inc["note"] or "", key=f"ei_note_{iid}", max_chars=200)
            errs, warns = sv.validate_income(sid, amount, gd, ex, "", title, exclude_id=iid)
            if amount < inc["received_amount"]:
                errs.append(f"Số tiền không được nhỏ hơn số đã thực nhận ({vnd(inc['received_amount'])}).")
            ui.show_msgs(errs, warns)
            if st.button("Lưu thay đổi", type="primary", disabled=bool(errs), key=f"ei_save_{iid}"):
                db.update_income(iid, sid, title, amount, gd, ex, note)
                flash(f"Đã lưu thay đổi khoản thu **{inc['code']}**.")
                st.rerun()
        with t[2]:
            st.caption("Xóa vĩnh viễn khoản thu cùng lịch sử trạng thái của nó.")
            if st.button("🗑️ Xóa khoản thu này", key=f"ei_del_{iid}"):
                dlg_delete("income", iid, f"khoản thu {inc['code']}")


def income_add_form(srcs):
    v = st.session_state.get("inc_ver", 0)
    k = lambda n: f"ia_{n}_{v}"
    ids = [s["id"] for s in srcs]
    st.markdown('<span class="cf-step">1</span> **Nguồn thu và số tiền**', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    sid = c1.selectbox("Nguồn thu *", ids, format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i), key=k("src"))
    src = next(s for s in srcs if s["id"] == sid)
    amount = c2.number_input("Số tiền (đồng) *", 0, sv.MAX_AMOUNT, 0, 100_000, key=k("amt"),
                             help="Nhập số nguyên, không dùng dấu chấm hay phẩy. VD: 8000000")
    if amount > 0:
        ui.words_box(amount)
    else:
        c2.caption("Nhập bằng chữ số, ví dụ 8000000. Hệ thống sẽ đọc lại thành chữ để bạn kiểm tra.")
    st.markdown('<span class="cf-step">2</span> **Thời gian**', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    gdate = c1.date_input("Ngày phát sinh *", date.today(), max_value=date.today(), format="DD/MM/YYYY", key=k("gd"))
    auto = c2.toggle(f"Tự tính hạn thanh toán ({src['pay_days']} ngày theo nguồn)", value=True, key=k("auto"))
    if auto:
        exp = gdate + timedelta(days=int(src["pay_days"]))
        c2.caption(f"Hạn thanh toán dự kiến: **{exp:%d/%m/%Y}**")
    else:
        exp = c2.date_input("Hạn thanh toán dự kiến", gdate + timedelta(days=14), format="DD/MM/YYYY", key=k("exp"))
    st.markdown('<span class="cf-step">3</span> **Thông tin thêm**', unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    title = c1.text_input("Nội dung / mô tả", max_chars=120, key=k("title"), placeholder="VD: Doanh thu quảng cáo tháng 10")
    code = c2.text_input("Mã khoản thu (để trống = tự tạo)", max_chars=20, key=k("code"),
                         placeholder=f"Mã tiếp theo: {db.next_code(src['name'])}",
                         help="Mã dùng để đối soát. Nên đưa mã này vào nội dung chuyển khoản.")
    status = c3.selectbox("Trạng thái ban đầu", ["GENERATED", "UNDER_REVIEW", "APPROVED"], format_func=status_txt, key=k("status"))
    errs, warns = sv.validate_income(sid, amount, gdate, exp, code, title)
    if amount == 0:
        errs = [e for e in errs if e != "Số tiền phải lớn hơn 0."]  # chưa nhập thì chưa báo lỗi
    dups = sv.find_duplicate_incomes(sid, amount, gdate) if amount > 0 else []
    ui.show_msgs(errs, warns)
    confirm = True
    if dups:
        st.warning(f"Đã có khoản **cùng nguồn, cùng số tiền, cùng ngày**: {', '.join(dups)}. Có thể bạn đang nhập trùng.", icon="🔁")
        confirm = st.checkbox("Không phải khoản trùng, vẫn thêm", key=k("dup"))
    ready = amount > 0 and not errs and confirm
    if ready:
        st.success(f"Sẵn sàng thêm: **{src['name']}** · **{vnd(amount)}** · phát sinh {gdate:%d/%m/%Y} · "
                   f"hạn {exp:%d/%m/%Y} · {STATUS_LABEL[status]}", icon="✅")
    if st.button("➕ Thêm khoản thu", type="primary", disabled=not ready, key=k("btn")):
        try:
            iid = db.add_income(sid, amount, gdate, title, "GENERATED", code.strip().upper() or None, exp.isoformat())
            if status != "GENERATED":
                db.change_status(iid, status, "Trạng thái ban đầu")
                with db.conn() as c:
                    c.execute("UPDATE incomes SET expected_pay_date=? WHERE id=?", (exp.isoformat(), iid))
            flash(f"Đã thêm khoản thu **{db.get_income(iid)['code']}** · {vnd(amount)}. Xem ở tab 📋 Danh sách.")
            bump("inc_ver")
            bump("tbl_ver")
        except ValueError as e:
            flash(str(e), "error")
        st.rerun()


def import_section(srcs):
    st.markdown('<span class="cf-step">A</span> **Lấy dữ liệu từ API giả lập** (mô phỏng YouTube, TikTok, Facebook, Freelancer)',
                unsafe_allow_html=True)
    if sv.ensure_api():
        st.caption(f"API giả lập đang chạy tại {sv.API_BASE} (tài liệu API: {sv.API_BASE}/docs)")
    else:
        st.caption("API giả lập đang chạy ở chế độ nội bộ.")
    c1, c2, c3 = st.columns([2, 3, 1])
    plat = c1.selectbox("Nền tảng", list(sv.PLATFORMS), key="api_plat")
    rnd = c2.checkbox("Tạo dữ liệu mới ngẫu nhiên (để thử nhập nhiều lần)", key="api_rnd")
    n = c3.number_input("Số bản ghi", 1, 10, 2, key="api_n", disabled=not rnd)
    if st.button("📡 Gọi API", key="api_call"):
        try:
            raw, norm = sv.fetch_platform(plat, rnd, int(n))
            st.session_state["api_preview"] = (plat, raw, norm)
        except Exception as e:
            st.error(f"Lỗi gọi API: {e}")
    prev = st.session_state.get("api_preview")
    if prev:
        plat_p, raw, norm = prev
        cfg = sv.PLATFORMS[plat_p]
        a, b = st.columns(2)
        with a:
            st.markdown(f"**Dữ liệu thô từ {plat_p}** · trường `{cfg['amount']}` = *{cfg['raw_label']}*")
            st.code(json.dumps(raw, ensure_ascii=False, indent=2), language="json")
        with b:
            st.markdown(f"**Sau khi chuẩn hóa** → *{cfg['norm_label']}*, đơn vị đồng")
            nd = pd.DataFrame(norm)
            nd["Trùng?"] = [("⏭️ Đã có, sẽ bỏ qua" if (db.source_by_name(r["source_name"]) and db.external_exists(
                db.source_by_name(r["source_name"])["id"], r["external_id"])) else "✅ Mới") for r in norm]
            st.dataframe(nd.rename(columns={"external_id": "Mã ngoài", "amount": "Số tiền (đ)", "generated_date": "Ngày",
                                            "title": "Nội dung", "source_name": "Nguồn thu"}), hide_index=True,
                         column_config={"Số tiền (đ)": st.column_config.NumberColumn(format="localized")})
        if st.button("✅ Nhập vào hệ thống", key="api_commit", type="primary"):
            a_, d_ = sv.import_normalized(norm, "API giả lập")
            st.session_state.pop("api_preview", None)
            bump("tbl_ver")
            flash(f"Đã nhập {a_} khoản thu từ API" + (f" (bỏ qua {d_} khoản trùng mã)" if d_ else "") + ".")
            st.rerun()

    st.divider()
    st.markdown('<span class="cf-step">B</span> **Nhập từ file Excel / CSV**', unsafe_allow_html=True)
    with st.expander("📎 Chưa có file? Tải file mẫu về để thử"):
        files = sv.sample_files()
        cc = st.columns(2)
        for col, name in zip(cc, ["tiktok_revenue.csv", "freelancer_projects.xlsx"]):
            col.download_button(f"⬇️ {name}", files[name][0], name, files[name][1], key=f"dl_{name}")
        st.caption("Tải về rồi chọn lại ở ô bên dưới. Nhớ gán đúng nguồn thu (TikTok Affiliate / Freelancer).")
    up = st.file_uploader("Chọn file doanh thu (.csv, .xlsx)", type=["csv", "xlsx", "xls"], key="inc_file")
    if up:
        try:
            df = sv.read_table(up)
        except Exception as e:
            st.error(str(e))
            return
        cols = ["(không có)"] + list(df.columns)
        g = lambda k_: sv.guess_column(df.columns, sv.INCOME_ALIASES[k_])
        idx = lambda k_: cols.index(g(k_)) if g(k_) in cols else 0
        st.markdown("**Ánh xạ cột** (hệ thống tự đoán, bạn có thể sửa):")
        c = st.columns(4)
        m_amt = c[0].selectbox("Cột số tiền *", cols, idx("amount"), key="m_amt")
        m_date = c[1].selectbox("Cột ngày", cols, idx("generated_date"), key="m_date")
        m_id = c[2].selectbox("Cột mã", cols, idx("external_id"), key="m_id")
        m_title = c[3].selectbox("Cột nội dung", cols, idx("title"), key="m_title")
        c2 = st.columns(2)
        sid = c2[0].selectbox("Gán vào nguồn thu", [s["id"] for s in srcs],
                              format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i), key="file_src")
        st0 = c2[1].selectbox("Trạng thái ban đầu", ["GENERATED", "UNDER_REVIEW", "APPROVED", "PENDING_PAYMENT"],
                              format_func=status_txt, key="file_status")
        if m_amt == "(không có)":
            st.warning("Hãy chọn cột chứa số tiền.", icon="👆")
            return
        nn = lambda v_: None if v_ == "(không có)" else v_
        cmap = dict(amount=nn(m_amt), generated_date=nn(m_date), external_id=nn(m_id), title=nn(m_title))
        pv = sv.preview_income_rows(df, cmap, sid)
        ok = int(pv["Kết quả"].str.startswith("✅").sum())
        dup = int(pv["Kết quả"].str.startswith("⏭️").sum())
        bad = int(pv["Kết quả"].str.startswith("❌").sum())
        m = st.columns(3)
        m[0].metric("✅ Hợp lệ", ok)
        m[1].metric("⏭️ Trùng, bỏ qua", dup)
        m[2].metric("❌ Lỗi, bỏ qua", bad)
        st.dataframe(pv, hide_index=True, column_config={"Số tiền": st.column_config.NumberColumn(format="localized")})
        if bad:
            st.caption("Các dòng lỗi sẽ không được nhập. Sửa file rồi tải lại nếu cần giữ chúng.")
        if st.button(f"✅ Nhập {ok} dòng hợp lệ", key="file_commit", type="primary", disabled=ok == 0):
            a_, d_, b_, _ = sv.import_income_rows(df, cmap, sid, st0)
            bump("tbl_ver")
            flash(f"Đã nhập {a_} khoản thu" + (f", bỏ qua {d_} khoản trùng" if d_ else "") +
                  (f", {b_} dòng lỗi" if b_ else "") + ".")
            st.rerun()


# ---------- Trang: Theo dõi thanh toán ----------
def page_payments():
    ui.hero("⏳", "Theo dõi thanh toán", "Khoản nào đã được duyệt, khoản nào đang chờ tiền về, khoản nào đã quá hạn. "
            "Hệ thống tự đánh dấu trễ hạn khi quá ngày dự kiến.")
    n = db.refresh_overdue()
    if n:
        st.warning(f"Hệ thống vừa tự đánh dấu {n} khoản là **trễ hạn** do quá ngày thanh toán dự kiến.", icon="🚨")
    df = sv.income_df()
    if df.empty:
        st.info("Chưa có khoản thu nào.", icon="📭")
        return
    df["remaining"] = df["amount"] - df["received_amount"]
    c = st.columns([3, 2], vertical_alignment="center")
    fs = c[0].multiselect("Nguồn thu", sorted(df["source"].unique()), key="p_src", placeholder="Tất cả nguồn thu",
                          label_visibility="collapsed")
    hz = c[1].selectbox("Hạn thanh toán", ["Tất cả", "Đã quá hạn", "Trong 7 ngày tới", "Trong 30 ngày tới"], key="p_hz",
                        format_func=lambda h: f"Hạn thanh toán: {h}", label_visibility="collapsed")
    f = df[df["source"].isin(fs)] if fs else df
    t0 = date.today().isoformat()
    if hz == "Đã quá hạn":
        f = f[f["expected_pay_date"] < t0]
    elif hz == "Trong 7 ngày tới":
        f = f[(f["expected_pay_date"] >= t0) & (f["expected_pay_date"] <= (date.today() + timedelta(days=7)).isoformat())]
    elif hz == "Trong 30 ngày tới":
        f = f[(f["expected_pay_date"] >= t0) & (f["expected_pay_date"] <= (date.today() + timedelta(days=30)).isoformat())]
    groups = {
        "✅ Đã duyệt": (f[f["status"] == "APPROVED"], "#3b82f6"),
        "💤 Đang chờ": (f[f["status"].isin(["PENDING_PAYMENT", "PARTIALLY_PAID"])], "#8b5cf6"),
        "🚨 Trễ hạn": (f[f["status"] == "OVERDUE"], "#ef4444"),
        "⚠️ Hủy / tranh chấp": (f[f["status"].isin(["CANCELLED", "DISPUTED"])], "#f97316"),
    }
    cols = st.columns(len(groups))
    for col, (name, (d, color)) in zip(cols, groups.items()):
        ui.kpi(col, name, f"{len(d)} khoản", f"Còn phải thu {vnd(d['remaining'].sum())}",
               "#ef4444" if (color == "#ef4444" and len(d) > 0) else None)
    tabs = st.tabs(["🗓️ Lịch thanh toán dự kiến"] + list(groups))
    with tabs[0]:
        w = f[f["status"].isin(db.WAITING_PAY_SET) & (f["remaining"] > 0)].sort_values("expected_pay_date")
        if w.empty:
            st.success("Không có khoản nào đang chờ tiền về trong bộ lọc này.", icon="🎉")
        else:
            w = w.assign(day=pd.to_datetime(w["expected_pay_date"]), v=w["remaining"] / 1e6,
                         tt=w["status"].map(STATUS_LABEL))
            fig = px.bar(w, x="day", y="v", color="tt", color_discrete_map=ui.STATUS_COLORS,
                         hover_data={"code": True, "source": True, "day": False, "v": ":,.2f", "tt": False},
                         labels={"day": "", "v": "Triệu đồng", "tt": "Trạng thái"})
            fig.add_shape(type="line", x0=pd.Timestamp(date.today()), x1=pd.Timestamp(date.today()), y0=0, y1=1,
                          yref="paper", line=dict(color="#ef4444", dash="dash", width=2))
            fig.add_annotation(x=pd.Timestamp(date.today()), y=1, yref="paper", text="Hôm nay", showarrow=False,
                               font=dict(color="#ef4444"), yshift=10)
            fig.update_layout(bargap=0.5)
            st.plotly_chart(ui.style_fig(fig, "Mỗi cột là một khoản chờ tiền, đặt theo ngày dự kiến nhận"))
            ui.income_table(w, "pay_sched")
    for tab, (name, (d, _)) in zip(tabs[1:], groups.items()):
        with tab:
            if d.empty:
                st.success("Không có khoản nào trong nhóm này.", icon="🎉")
            else:
                ui.income_table(d, f"pay_{name}")
    with st.expander("Cập nhật nhanh trạng thái một khoản"):
        open_df = df[~df["status"].isin(["RECONCILED", "CANCELLED"])]
        iid = pick_income(open_df, "pay_pick")
        if iid:
            status_actions(iid, f"pay_{iid}")


# ---------- Trang: Đối soát ----------
def page_recon():
    ui.hero("🏦", "Đối soát sao kê ngân hàng", "Kiểm tra tiền thật sự đã vào tài khoản chưa. Hệ thống tự ghép giao dịch "
            "ngân hàng với khoản thu; trường hợp chưa chắc chắn sẽ để bạn quyết định, không tự xác nhận sai.")
    bd = sv.bank_df()
    n_all = len(bd)
    n_wait = int(bd["match_status"].isin(["NEW"]).sum()) if n_all else 0
    n_rev = int(bd["match_status"].isin(["REVIEW", "UNMATCHED"]).sum()) if n_all else 0
    n_ok = int((bd["match_status"] == "MATCHED").sum()) if n_all else 0
    c = st.columns(4)
    ui.kpi(c[0], "Giao dịch tiền vào", f"{n_all}", "Đã tải / nhập vào hệ thống", "#6366f1")
    ui.kpi(c[1], "Chờ đối soát", f"{n_wait}", "Chưa chạy đối soát", "#f59e0b")
    ui.kpi(c[2], "Cần bạn kiểm tra", f"{n_rev}", "Thiếu tiền, trùng hoặc không khớp", "#ef4444" if n_rev else None)
    ui.kpi(c[3], "Đã khớp", f"{n_ok}", "Đã ghi nhận thực nhận", "#10b981" if n_ok else None)
    tabs = st.tabs(["1️⃣ Tải sao kê", "2️⃣ Đối soát tự động", f"3️⃣ Cần kiểm tra ({n_rev})", "📜 Lịch sử đối soát"])

    with tabs[0]:
        cs = st.columns([2, 2, 3])
        if cs[0].button("🧪 Dùng sao kê mẫu", key="use_sample_bank",
                        help="Nạp nhanh 6 giao dịch mẫu (khớp với dữ liệu mẫu) để thử đối soát"):
            bdf = sv.sample_bank_df()
            a, d, s_ = sv.import_bank_rows(bdf, dict(txn_date="Ngày giao dịch", amount="Ghi có",
                                                     description="Nội dung giao dịch"))
            flash(f"Đã nạp {a} giao dịch tiền vào từ sao kê mẫu" + (f" (bỏ qua {d} dòng trùng)" if d else "") +
                  ". Sang bước 2 để đối soát.")
            st.rerun()
        cs[1].download_button("⬇️ Tải sao kê mẫu (CSV)", sv.sample_files()["bank_statement.csv"][0],
                              "bank_statement.csv", "text/csv", key="dl_bank")
        cs[2].caption("Sao kê mẫu dùng với **dữ liệu mẫu** (khớp các mã YT001, FR001, TI002...).")
        up = st.file_uploader("Hoặc tải sao kê của bạn (.csv, .xlsx)", type=["csv", "xlsx", "xls"], key="bank_file")
        if up:
            try:
                df = sv.read_table(up)
            except Exception as e:
                st.error(str(e))
                df = None
            if df is not None:
                cols = list(df.columns)
                g = lambda k_: sv.guess_column(df.columns, sv.BANK_ALIASES[k_])
                idx = lambda k_: cols.index(g(k_)) if g(k_) in cols else 0
                c = st.columns(3)
                m_date = c[0].selectbox("Cột ngày giao dịch", cols, idx("txn_date"), key="b_date")
                m_amt = c[1].selectbox("Cột tiền vào (ghi có)", cols, idx("amount"), key="b_amt")
                m_desc = c[2].selectbox("Cột nội dung", cols, idx("description"), key="b_desc")
                cmap = dict(txn_date=m_date, amount=m_amt, description=m_desc)
                pv = sv.preview_bank_rows(df, cmap)
                ok = int(pv["Kết quả"].str.startswith("✅").sum())
                m = st.columns(3)
                m[0].metric("✅ Tiền vào hợp lệ", ok)
                m[1].metric("⏭️ Trùng", int(pv["Kết quả"].str.startswith("⏭️").sum()))
                m[2].metric("➖/❌ Bỏ qua", int(pv["Kết quả"].str.startswith(("➖", "❌")).sum()))
                st.dataframe(pv, hide_index=True,
                             column_config={"Số tiền": st.column_config.NumberColumn(format="localized")})
                if st.button(f"📥 Đọc {ok} giao dịch tiền vào", type="primary", key="bank_commit", disabled=ok == 0):
                    a, d, s_ = sv.import_bank_rows(df, cmap)
                    flash(f"Đã đọc {a} giao dịch tiền vào (bỏ qua {s_} dòng không dùng được, {d} dòng trùng). "
                          "Sang bước 2 để đối soát.")
                    st.rerun()
        st.markdown("**Hoặc nhập tay một giao dịch tiền vào**")
        v = st.session_state.get("txn_ver", 0)
        c = st.columns([1, 1.4, 3])
        d = c[0].date_input("Ngày *", date.today(), max_value=date.today(), format="DD/MM/YYYY", key=f"tx_d_{v}")
        a = c[1].number_input("Số tiền (đồng) *", 0, sv.MAX_AMOUNT, 0, 100_000, key=f"tx_a_{v}")
        desc = c[2].text_input("Nội dung chuyển khoản", key=f"tx_s_{v}", max_chars=200,
                               placeholder="VD: GOOGLE ADSENSE YT001")
        if a > 0:
            ui.words_box(a)
            errs, warns = sv.validate_txn(a, d, desc)
            ui.show_msgs(errs, warns)
        else:
            errs = ["x"]
        if st.button("➕ Thêm giao dịch", key=f"tx_b_{v}", type="primary", disabled=bool(errs)):
            try:
                db.add_bank_txn(d, a, desc)
                flash(f"Đã thêm giao dịch {vnd(a)}. Sang bước 2 để đối soát.")
                bump("txn_ver")
            except ValueError as e:
                flash(str(e), "error")
            st.rerun()
        bd = sv.bank_df()
        if not bd.empty:
            st.markdown(f"**Giao dịch đã có ({len(bd)})**")
            lab = {"NEW": "⚪ Chưa đối soát", "MATCHED": "🟢 Đã ghép", "REVIEW": "🟠 Cần kiểm tra",
                   "UNMATCHED": "🔴 Không khớp", "IGNORED": "⚫ Đã bỏ qua"}
            show = pd.DataFrame({"#": bd["id"], "Ngày": pd.to_datetime(bd["txn_date"]), "Số tiền (đ)": bd["amount"],
                                 "Nội dung": bd["description"], "Kết quả": bd["match_status"].map(lab),
                                 "Khớp với": bd["income_code"]})
            st.dataframe(show, hide_index=True, column_config={
                "Số tiền (đ)": st.column_config.NumberColumn(format="localized"),
                "Ngày": st.column_config.DateColumn(format="DD/MM/YYYY")})
            with st.expander("🗑️ Xóa một giao dịch"):
                tid = st.selectbox("Chọn giao dịch", bd["id"].tolist(), key="del_txn_pick", format_func=lambda i: (
                    f"#{i} · {bd[bd['id'] == i]['txn_date'].iloc[0]} · {vnd(bd[bd['id'] == i]['amount'].iloc[0])} · "
                    f"{bd[bd['id'] == i]['description'].iloc[0]}"))
                if st.button("Xóa giao dịch", key="del_txn_btn"):
                    dlg_delete("txn", int(tid), f"giao dịch #{tid}", "Nếu đã ghép, khoản thu sẽ được hoàn về trạng thái trước.")

    with tabs[1]:
        st.info("**Quy tắc an toàn:** hệ thống so sánh *mã khoản thu trong nội dung*, *số tiền*, *ngày* và *tên nền tảng*. "
                "Chỉ **tự xác nhận** khi khớp duy nhất và đúng số tiền. Nhận thiếu (phí / thanh toán một phần) hoặc có "
                "nhiều khoản phù hợp sẽ được chuyển sang bước 3 để bạn quyết định.", icon="🛡️")
        c = st.columns(2)
        tol = c[0].slider("Ngưỡng nhận thiếu có thể do phí (%)", 0.0, 10.0, 3.0, 0.5,
                          help="Giao dịch thấp hơn số phải thu trong ngưỡng này được gợi ý là 'trừ phí'.")
        win = c[1].slider("Cửa sổ ngày so với hạn dự kiến (± ngày)", 1, 30, 10)
        pending = bd[bd["match_status"].isin(["NEW", "REVIEW", "UNMATCHED"])] if not bd.empty else bd
        st.caption(f"{len(pending)} giao dịch đang chờ đối soát.")
        if st.button("🤖 Chạy đối soát tự động", type="primary", disabled=len(pending) == 0, key="run_recon"):
            r = sv.run_auto_reconcile(tol, win)
            flash(f"Kết quả: **{r['matched']}** tự ghép · **{r['review']}** cần kiểm tra · **{r['unmatched']}** không tìm thấy khoản phù hợp.")
            st.rerun()

    with tabs[2]:
        bd = sv.bank_df()
        todo = bd[bd["match_status"].isin(["REVIEW", "UNMATCHED", "NEW"])] if not bd.empty else bd
        if todo.empty:
            st.success("Không có giao dịch nào cần kiểm tra.", icon="🎉")
        incs = sv.income_df()
        open_incs = incs[incs["status"].isin(db.AWAITING_MONEY_SET)] if not incs.empty else incs
        for _, t in todo.iterrows():
            with st.container(border=True):
                st.markdown(f"**#{t['id']} · {pd.to_datetime(t['txn_date']):%d/%m/%Y} · {vnd(t['amount'])}** — "
                            f"{t['description'] or '(không có nội dung)'}")
                if t["suggestion"]:
                    st.caption(f"💡 {t['suggestion']}")
                if open_incs.empty:
                    st.info("Chưa có khoản thu nào đang chờ tiền để ghép.")
                    continue
                opts = {int(r["id"]): f"{income_label(r)} · còn {vnd(r['amount'] - r['received_amount'])}"
                        for _, r in open_incs.iterrows()}
                c = st.columns([4, 1, 1, 1, 1])
                sel = c[0].selectbox("Ghép với", list(opts), format_func=lambda i: opts[i], key=f"sel_{t['id']}",
                                     label_visibility="collapsed")
                if c[1].button("Nhận đủ", key=f"full_{t['id']}", help="Số tiền khớp, xác nhận đã nhận"):
                    db.apply_match(int(t["id"]), sel, "full", "Người dùng xác nhận")
                    flash("Đã đối soát.")
                    st.rerun()
                if c[2].button("Trừ phí", key=f"fee_{t['id']}", help="Nhận thiếu do phí: chấp nhận chênh lệch, coi là đã nhận"):
                    db.apply_match(int(t["id"]), sel, "fee", "Người dùng chấp nhận chênh lệch (phí)")
                    flash("Đã đối soát (ghi nhận chênh lệch do phí).")
                    st.rerun()
                if c[3].button("Một phần", key=f"part_{t['id']}", help="Chỉ là một phần thanh toán, vẫn chờ phần còn lại"):
                    db.apply_match(int(t["id"]), sel, "partial", "Người dùng ghi nhận thanh toán một phần")
                    flash("Đã ghi nhận thanh toán một phần.")
                    st.rerun()
                if c[4].button("Bỏ qua", key=f"ign_{t['id']}", help="Giao dịch không liên quan đến thu nhập"):
                    db.set_txn_status(int(t["id"]), "IGNORED", "Người dùng bỏ qua")
                    st.rerun()

    with tabs[3]:
        bd = sv.bank_df()
        matched = bd[bd["match_status"] == "MATCHED"] if not bd.empty else bd
        st.markdown("**Giao dịch đã ghép** (có thể hủy nếu ghép nhầm)")
        if matched.empty:
            st.info("Chưa có giao dịch nào được ghép.")
        else:
            for _, t in matched.iterrows():
                c = st.columns([5, 1])
                c[0].write(f"#{t['id']} · {pd.to_datetime(t['txn_date']):%d/%m/%Y} · {vnd(t['amount'])} → "
                           f"**{t['income_code']}** · {t['description']}")
                if c[1].button("Hủy ghép", key=f"un_{t['id']}"):
                    db.unmatch(int(t["id"]))
                    flash("Đã hủy đối soát, khoản thu được hoàn về trạng thái trước.")
                    st.rerun()
        st.markdown("**Nhật ký đối soát**")
        lg = sv.recon_log_df()
        if lg.empty:
            st.caption("Chưa có nhật ký.")
        else:
            st.dataframe(lg[["ts", "txn_id", "income_code", "action", "amount", "diff", "note"]].rename(columns={
                "ts": "Thời điểm", "txn_id": "Giao dịch #", "income_code": "Khoản thu", "action": "Hành động",
                "amount": "Số tiền (đ)", "diff": "Chênh lệch / còn lại (đ)", "note": "Ghi chú"}), hide_index=True,
                column_config={"Số tiền (đ)": st.column_config.NumberColumn(format="localized"),
                               "Chênh lệch / còn lại (đ)": st.column_config.NumberColumn(format="localized")})


# ---------- Trang: Báo cáo ----------
def page_reports():
    ui.hero("📈", "Báo cáo thu nhập", "Phân tích thu nhập theo thời gian, nguồn và trạng thái. Chọn bộ lọc, biểu đồ đổi ngay; "
            "cuối trang có nút xuất Excel/CSV.")
    df_all = sv.income_df()
    if df_all.empty:
        st.info("Chưa có dữ liệu để lập báo cáo.", icon="📭")
        return
    c = st.columns([3, 1.5, 2.2], vertical_alignment="center")
    c[0].markdown('<div class="cf-asof">Bộ lọc áp dụng cho toàn bộ báo cáo bên dưới</div>', unsafe_allow_html=True)
    period = c[1].selectbox("Kỳ báo cáo", sv.PERIODS, key="r_period", format_func=lambda p: f"Kỳ: {p}",
                            label_visibility="collapsed")
    sources = c[2].multiselect("Nguồn thu", sorted(df_all["source"].unique()), key="r_src",
                               placeholder="Tất cả nguồn thu", label_visibility="collapsed")
    custom = None
    if period == "Tùy chọn":
        custom = st.date_input("Chọn khoảng ngày", (date.today() - timedelta(days=30), date.today()),
                               format="DD/MM/YYYY", key="r_custom")
    with st.expander("Lọc thêm theo trạng thái"):
        status_f = st.multiselect("Trạng thái", list(STATUS_LABEL), format_func=status_txt, key="r_st",
                                  placeholder="Tất cả trạng thái")
    start, end = sv.period_range(period, custom)
    df = sv.filter_df(df_all, start, end, sources, status_f)
    if df.empty:
        ui.empty_chart_note()
        return
    live = df[df["status"] != "CANCELLED"]
    k = sv.kpis(df)
    c = st.columns(4)
    ui.kpi(c[0], "Doanh thu phát sinh", vnd(k["generated"]), f"{len(live)} khoản thu")
    ui.kpi(c[1], "Được duyệt", vnd(k["approved"]), "Đã xác nhận sẽ trả")
    ui.kpi(c[2], "Đã thực nhận", vnd(k["received"]), "Đã khớp sao kê", "#10b981")
    ui.kpi(c[3], "Còn chờ thanh toán", vnd(k["waiting"]), "Chưa về tài khoản")

    ui.section("Xu hướng theo thời gian")
    cc = st.columns([1.9, 2.2, 2, 1.6], vertical_alignment="bottom")
    metric = cc[0].selectbox("Chỉ số", sv.METRICS, key="r_metric")
    gran = cc[1].segmented_control("Độ chi tiết", ["Tháng", "Tuần", "Ngày"], default="Tháng", key="r_gran") or "Tháng"
    kind = cc[2].segmented_control("Kiểu", KINDS, default="Cột", key="r_kind") or "Cột"
    split = cc[3].toggle("Chia màu theo nguồn", value=True, key="r_split")
    ui.main_chart(df, metric, gran, kind, split)
    a, b = st.columns(2)
    with a:
        ui.share_chart(df, metric)
    with b:
        ui.main_chart(df, metric, "Trạng thái", "Cột", False)

    ui.section("Tốc độ tăng trưởng và nền tảng trả chậm")
    l, r = st.columns(2)
    with l:
        g, _ = ui.build_chart_df(df, metric, "Tháng", False)
        tm = g.groupby(["key", "x"], as_index=False)["value"].sum().sort_values("key")
        tm["Tăng trưởng (%)"] = (tm["value"].pct_change() * 100).round(1)
        if len(tm) >= 2:
            fig = px.line(tm.dropna(), x="x", y="Tăng trưởng (%)", markers=True)
            fig.update_traces(hovertemplate="%{x}<br><b>%{y:+.1f}%</b> so với tháng trước<extra></extra>")
            st.plotly_chart(ui.style_fig(fig, f"Tăng trưởng {metric.lower()} theo tháng", unit="%"))
        else:
            st.info("Cần dữ liệu từ 2 tháng trở lên để tính tăng trưởng.", icon="ℹ️")
        tshow = tm[["x", "value", "Tăng trưởng (%)"]].copy()
        tshow["value"] = tshow["value"].round(1)
        tshow["Tăng trưởng (%)"] = tshow["Tăng trưởng (%)"].map(lambda v_: "—" if pd.isna(v_) else f"{v_:+.1f}%")
        st.dataframe(tshow.rename(columns={"x": "Tháng", "value": "Giá trị (triệu đ)"}), hide_index=True)
    with r:
        d = live.dropna(subset=["paid_date", "expected_pay_date"]).copy()
        d["delay"] = (pd.to_datetime(d["paid_date"]) - pd.to_datetime(d["expected_pay_date"])).dt.days
        slow = d.groupby("source", as_index=False).agg(delay=("delay", "mean"))
        od = live[live["status"] == "OVERDUE"].groupby("source").size().rename("od")
        slow = slow.merge(od, left_on="source", right_index=True, how="outer").fillna(0)
        if slow.empty:
            st.info("Chưa có dữ liệu về độ trễ thanh toán.", icon="ℹ️")
        else:
            slow["Mức"] = slow["delay"].map(lambda d_: "Đúng hạn / sớm" if d_ <= 0 else "Trễ nhẹ (1–7 ngày)" if d_ <= 7 else "Trễ nhiều (>7 ngày)")
            fig = px.bar(slow, x="source", y="delay", color="Mức", labels={"source": "", "delay": "Số ngày"},
                         color_discrete_map={"Đúng hạn / sớm": "#10b981", "Trễ nhẹ (1–7 ngày)": "#f59e0b", "Trễ nhiều (>7 ngày)": "#ef4444"})
            fig.update_traces(hovertemplate="%{x}<br><b>%{y:.1f} ngày</b> so với hạn dự kiến<extra></extra>")
            st.plotly_chart(ui.style_fig(fig, "Số ngày trễ trung bình so với hạn (âm = trả sớm)", unit="ngày"))
            st.dataframe(slow.rename(columns={"source": "Nguồn", "delay": "Số ngày trễ TB", "od": "Khoản đang trễ hạn"}).round(1),
                         hide_index=True)

    ui.section("Xuất báo cáo")
    summary = pd.DataFrame({"Chỉ tiêu": ["Doanh thu phát sinh", "Thu nhập được duyệt", "Còn chờ thanh toán",
                                          "Chờ đối soát", "Tiền đã thực nhận"],
                            "Giá trị (đ)": [k["generated"], k["approved"], k["waiting"], k["unreconciled"], k["received"]]})
    monthly = live.groupby(["month", "source"], as_index=False)["amount"].sum()
    by_src = live.groupby("source", as_index=False)["amount"].sum()
    out = df.assign(status=df["status"].map(STATUS_LABEL)).drop(columns=["id"])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Tong hop", index=False)
        out.to_excel(xw, sheet_name="Khoan thu", index=False)
        by_src.rename(columns={"source": "Nguon", "amount": "Doanh thu"}).to_excel(xw, sheet_name="Theo nguon", index=False)
        monthly.to_excel(xw, sheet_name="Theo thang", index=False)
        sv.bank_df().to_excel(xw, sheet_name="Sao ke", index=False)
        sv.recon_log_df().to_excel(xw, sheet_name="Lich su doi soat", index=False)
    d1, d2, _ = st.columns([1.3, 1.6, 3])
    d1.download_button("⬇️ Xuất Excel", buf.getvalue(), f"creatorflow_bao_cao_{date.today()}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    d2.download_button("⬇️ Xuất danh sách khoản thu (CSV)", out.to_csv(index=False).encode("utf-8-sig"),
                       f"khoan_thu_{date.today()}.csv", "text/csv")
    st.caption("File xuất theo đúng bộ lọc đang chọn ở trên (trừ các sheet sao kê và lịch sử đối soát).")


# ---------- Trang hướng dẫn ----------
def page_guide():
    ui.hero("ℹ️", "Giới thiệu & hướng dẫn", "Hệ thống làm gì, dòng đời của một khoản thu và cách demo trong vài phút.")
    st.markdown("""
**CreatorFlow** là nền tảng Fintech giúp người làm việc trong nền kinh tế số (**Content Creator, Affiliate Marketer,
Freelancer**) quản lý tập trung thu nhập từ nhiều nền tảng, theo dõi **toàn bộ vòng đời** của từng khoản thu và
**tự động đối soát** với sao kê ngân hàng. Báo cáo không chỉ cho biết kiếm được bao nhiêu mà còn cho biết
*khoản nào thực sự đã về tài khoản*.

> CreatorFlow **không** phải ví điện tử, cổng thanh toán hay ngân hàng số: hệ thống không giữ tiền và không chuyển tiền.
""")
    st.subheader("Vòng đời của một khoản thu")
    st.graphviz_chart("""
digraph G { rankdir=LR; bgcolor="transparent"; nodesep=0.35; ranksep=0.35;
 node [shape=box, style="rounded,filled", fillcolor="#eef2ff", color="#6366f1", fontname="Arial", fontsize=15, margin="0.2,0.12"];
 edge [color="#64748b"];
 A [label="Phát sinh"]; B [label="Chờ xác nhận"]; C [label="Được duyệt"]; D [label="Chờ thanh toán"];
 E [label="Nền tảng báo\nđã trả"]; F [label="Đã nhận\n& đối soát", fillcolor="#d1fae5", color="#10b981"];
 X [label="Trễ hạn / Một phần /\nTranh chấp / Hủy", fillcolor="#fee2e2", color="#ef4444"];
 A -> B -> C -> D -> E -> F; A -> C; D -> X; E -> X [style=dashed]; }
""")
    st.subheader("Các từ bạn sẽ gặp trong hệ thống")
    st.table(pd.DataFrame([
        ["Phát sinh", "Bạn vừa kiếm được một khoản, đã ghi vào hệ thống."],
        ["Được duyệt", "Nền tảng hoặc khách hàng đã xác nhận sẽ trả khoản này."],
        ["Chờ thanh toán", "Đã duyệt, đang chờ họ chuyển tiền theo hạn."],
        ["Chờ đối soát", "Nền tảng báo đã chuyển, nhưng bạn chưa kiểm tra sao kê ngân hàng."],
        ["Đối soát", "So khớp khoản thu với giao dịch trên sao kê để chắc chắn tiền đã về."],
        ["Đã nhận & đối soát", "Tiền đã về tài khoản và khớp với sao kê. Đây mới là tiền thực nhận."],
        ["Trễ hạn", "Đã quá ngày dự kiến mà tiền chưa về."],
    ], columns=["Từ", "Nghĩa là gì"]).set_index("Từ"))
    c = st.columns(2)
    with c[0]:
        st.subheader("Hệ thống giúp bạn 4 việc")
        st.markdown("""
1. **Tổng hợp & chuẩn hóa** dữ liệu thu nhập (API giả lập + Excel/CSV; mỗi nền tảng một tên trường, một đơn vị).
2. **Quản lý công nợ phải thu** và trạng thái thanh toán, có lịch sử thay đổi.
3. **Đối soát tự động** với sao kê; thiếu tiền/nhiều ứng viên → người dùng quyết định, tránh xác nhận sai.
4. **Phân tích & báo cáo** theo nguồn, thời gian, trạng thái; nền tảng nào trả chậm.
""")
    with c[1]:
        st.subheader("Kịch bản demo gợi ý")
        st.markdown("""
1. *🗂️ Nguồn thu*: thêm một nguồn mới.
2. *💰 Khoản thu*: gọi API YouTube, tải file TikTok, thêm tay một khoản Freelancer (gõ số tiền sẽ thấy hệ thống đọc lại bằng chữ).
3. *📋 Danh sách*: bấm một dòng, chuyển trạng thái, xem lịch sử.
4. *🏦 Đối soát*: bấm **Dùng sao kê mẫu** → **Chạy đối soát tự động** → xử lý mục *Cần kiểm tra*.
5. *📊 Tổng quan & 📈 Báo cáo*: đổi bộ lọc xem biểu đồ cập nhật ngay, xuất Excel.
""")
    st.info("💡 Doanh thu phát sinh, thu nhập được duyệt, khoản đến hạn và tiền thực nhận là **bốn thông tin khác nhau**, "
            "không cộng dồn thành một con số doanh thu để tránh tính trùng.")
    st.caption("Công nghệ: Python · Streamlit · SQLite · Pandas · Plotly · FastAPI · Requests. "
               "Chưa triển khai: kết nối API nền tảng thật và ngân hàng thật (định hướng phát triển).")


# ---------- Phiên làm việc riêng cho từng người truy cập ----------
def is_isolated():
    mode = os.environ.get("CREATORFLOW_MODE", "").lower()
    if mode in ("isolated", "local"):
        return mode == "isolated"
    return str(Path(__file__).resolve()).startswith("/mount/src")  # chạy trên Streamlit Cloud


def setup_session():
    if not is_isolated():
        db.init_db()
        return None
    sid = st.query_params.get("s", "")
    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", sid or ""):
        sid = uuid.uuid4().hex[:10]
        st.query_params["s"] = sid
    if not st.session_state.get("_cleaned"):
        db.cleanup_sessions()
        st.session_state["_cleaned"] = True
    db.use_path(db.session_path(sid))
    db.init_db()
    return sid


# ---------- Điều hướng ----------
def main():
    sid = setup_session()
    ui.inject_css()
    if "nav" not in st.session_state:
        st.session_state["nav"] = P_DASH
    with st.sidebar:
        st.markdown("## 💸 CreatorFlow")
        st.caption("Quản lý & đối soát vòng đời thu nhập đa nền tảng")
        st.radio("Điều hướng", PAGES, key="nav", label_visibility="collapsed")
        st.divider()
        with st.expander("⚙️ Dữ liệu & phiên làm việc"):
            if sid:
                st.caption(f"🔒 Bạn đang dùng **phiên riêng** (mã `{sid}`): dữ liệu của bạn không ảnh hưởng người khác "
                           "và giữ nguyên khi tải lại trang. Muốn người khác xem **cùng dữ liệu**, gửi cho họ "
                           "đúng link đang hiện trên thanh địa chỉ (có đuôi `?s=...`).")
            ok = st.checkbox("Tôi hiểu dữ liệu hiện tại sẽ bị thay thế", key="reset_ok")
            if st.button("Khôi phục dữ liệu mẫu", disabled=not ok):
                db.reset_all(True)
                flash("Đã khôi phục dữ liệu mẫu.")
                st.rerun()
            if st.button("Xóa toàn bộ dữ liệu", disabled=not ok):
                db.reset_all(False)
                flash("Đã xóa toàn bộ dữ liệu. Hãy tạo nguồn thu mới.", "warning")
                st.rerun()
            if sid and st.button("🆕 Mở phiên mới (dữ liệu mẫu mới)"):
                st.query_params.clear()
                st.rerun()
    show_flash()
    {P_DASH: page_dashboard, P_SRC: page_sources, P_INC: page_incomes, P_PAY: page_payments,
     P_REC: page_recon, P_REP: page_reports, P_GUIDE: page_guide}[st.session_state["nav"]]()


main()
