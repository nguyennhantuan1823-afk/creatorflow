"""CreatorFlow - Hệ thống quản lý và đối soát vòng đời thu nhập đa nền tảng.
Chạy:  streamlit run app.py"""
import io
import json
from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

import db
import services as sv
from db import STATUS_LABEL, TRANSITIONS
from services import vnd

st.set_page_config(page_title="CreatorFlow", page_icon="💸", layout="wide")
db.init_db()

PAGES = ["📊 Dashboard", "🗂️ Nguồn thu", "💰 Quản lý khoản thu", "⏳ Theo dõi thanh toán",
         "🏦 Đối soát", "📈 Báo cáo"]
STATUS_COLORS = {
    "Phát sinh doanh thu": "#94a3b8", "Chờ xác nhận / nghiệm thu": "#f59e0b", "Được duyệt": "#3b82f6",
    "Chờ thanh toán": "#6366f1", "Đã thanh toán / chờ đối soát": "#06b6d4", "Đã nhận & đối soát": "#10b981",
    "Nhận một phần": "#84cc16", "Trễ hạn thanh toán": "#ef4444", "Tranh chấp": "#f97316", "Đã hủy": "#6b7280",
}


# ---------- Tiện ích giao diện ----------
def flash(msg, kind="success"):
    st.session_state.setdefault("_flash", []).append((kind, msg))


def show_flash():
    for kind, msg in st.session_state.pop("_flash", []):
        getattr(st, kind)(msg)


def label_df(df):
    """Bản hiển thị: đổi mã trạng thái -> tiếng Việt, định dạng tiền."""
    out = df.copy()
    if "status" in out:
        out["status"] = out["status"].map(STATUS_LABEL)
    for col in ("amount", "received_amount", "remaining"):
        if col in out:
            out[col] = out[col].map(vnd)
    return out


INCOME_COLS = {"code": "Mã", "source": "Nguồn", "title": "Nội dung", "amount": "Số tiền",
               "received_amount": "Đã thực nhận", "generated_date": "Ngày phát sinh",
               "expected_pay_date": "Hạn dự kiến", "status": "Trạng thái", "origin": "Cách nhập"}


def show_incomes(df, cols=None):
    if df.empty:
        st.info("Chưa có dữ liệu để hiển thị.")
        return
    cols = cols or list(INCOME_COLS)
    shown = label_df(df)[cols].rename(columns=INCOME_COLS)
    st.dataframe(shown, hide_index=True)


def income_label(r):
    return f"{r['code']} · {r['source']} · {vnd(r['amount'])} · {STATUS_LABEL[r['status']]}"


def pick_income(df, key, label="Chọn khoản thu"):
    if df.empty:
        return None
    rows = {int(r["id"]): income_label(r) for _, r in df.iterrows()}
    iid = st.selectbox(label, list(rows), format_func=lambda i: rows[i], key=key)
    return int(iid)


MANUAL_BLOCKED = {"RECONCILED", "PARTIALLY_PAID"}  # hai trạng thái này chỉ đến từ bước đối soát sao kê


def status_actions(iid, key):
    """Hộp thao tác đổi trạng thái cho một khoản thu."""
    inc = db.get_income(iid)
    opts = [s for s in TRANSITIONS[inc["status"]] if s not in MANUAL_BLOCKED]
    st.caption(f"Trạng thái hiện tại: **{STATUS_LABEL[inc['status']]}**")
    if not opts:
        if inc["status"] in ("RECONCILED", "CANCELLED"):
            st.info("Khoản thu đã ở trạng thái cuối, không thể chuyển tiếp.")
        else:
            st.info("Bước tiếp theo của khoản này được cập nhật qua trang **Đối soát** khi có giao dịch ngân hàng.")
        return
    c1, c2, c3 = st.columns([2, 3, 1])
    new = c1.selectbox("Chuyển sang", opts, format_func=lambda s: STATUS_LABEL[s], key=f"{key}_new")
    note = c2.text_input("Ghi chú (không bắt buộc)", key=f"{key}_note")
    c3.write("")
    c3.write("")
    if c3.button("Cập nhật", key=f"{key}_btn", type="primary"):
        try:
            db.change_status(iid, new, note)
            flash(f"Đã chuyển {inc['code']} sang '{STATUS_LABEL[new]}'.")
        except ValueError as e:
            flash(str(e), "error")
        st.rerun()
    if "PAID_UNRECONCILED" in opts:
        st.caption("Gợi ý: chọn *Đã thanh toán / chờ đối soát* khi nền tảng báo đã chuyển tiền, "
                   "sau đó tải sao kê ở trang Đối soát để xác nhận thực nhận.")


# ---------- Trang 1: Dashboard ----------
def page_dashboard():
    st.title("📊 Dashboard – Tổng quan thu nhập")
    df = sv.income_df()
    if df.empty:
        st.info("Chưa có khoản thu nào. Vào **Quản lý khoản thu** để thêm hoặc nhập dữ liệu, "
                "hoặc bấm *Khôi phục dữ liệu mẫu* ở thanh bên.")
        return
    k = sv.kpis(df)
    c = st.columns(5)
    c[0].metric("Doanh thu phát sinh", vnd(k["generated"]))
    c[1].metric("Thu nhập được duyệt", vnd(k["approved"]))
    c[2].metric("Còn chờ thanh toán", vnd(k["waiting"]))
    c[3].metric("Chờ đối soát", vnd(k["unreconciled"]))
    c[4].metric("Tiền đã thực nhận", vnd(k["received"]))
    st.caption("⚠️ Đây là **các lát cắt khác nhau của cùng một dòng tiền**, không cộng với nhau: "
               "khoản đã thực nhận nằm trong khoản đã duyệt, và khoản đã duyệt nằm trong doanh thu phát sinh.")
    if k["overdue"] > 0:
        st.warning(f"Có khoản **trễ hạn thanh toán** tổng {vnd(k['overdue'])}. Xem ở trang *Theo dõi thanh toán*.")

    left, right = st.columns([3, 2])
    with left:
        live = df[df["status"] != "CANCELLED"]
        g = live.groupby("source", as_index=False).agg(amount=("amount", "sum"), received=("received_amount", "sum"))
        g = g.melt(id_vars="source", value_vars=["amount", "received"], var_name="loại", value_name="VND")
        g["loại"] = g["loại"].map({"amount": "Doanh thu phát sinh", "received": "Đã thực nhận"})
        fig = px.bar(g, x="source", y="VND", color="loại", barmode="group", title="Doanh thu theo nền tảng",
                     labels={"source": "Nguồn thu"}, color_discrete_sequence=["#6366f1", "#10b981"])
        st.plotly_chart(fig)
    with right:
        s = live.assign(trang_thai=live["status"].map(STATUS_LABEL)).groupby("trang_thai", as_index=False)["amount"].sum()
        fig2 = px.pie(s, names="trang_thai", values="amount", hole=0.45, title="Cơ cấu theo trạng thái",
                      color="trang_thai", color_discrete_map=STATUS_COLORS)
        st.plotly_chart(fig2)

    st.subheader("Khoản thu gần đến hạn / đã trễ hạn")
    due = df[df["status"].isin(db.WAITING_PAY_SET)].copy()
    if due.empty:
        st.success("Không có khoản nào đang chờ thanh toán.")
    else:
        due["Còn (ngày)"] = (pd.to_datetime(due["expected_pay_date"]) - pd.Timestamp(date.today())).dt.days
        due = due.sort_values("expected_pay_date").head(8)
        due["remaining"] = due["amount"] - due["received_amount"]
        shown = label_df(due)[["code", "source", "remaining", "expected_pay_date", "Còn (ngày)", "status"]]
        st.dataframe(shown.rename(columns={**INCOME_COLS, "remaining": "Còn phải thu"}), hide_index=True)


# ---------- Trang 2: Nguồn thu ----------
def page_sources():
    st.title("🗂️ Income Sources – Quản lý nguồn thu")
    srcs = db.list_sources()
    df = sv.income_df()
    if srcs:
        rows = []
        for s in srcs:
            d = df[df["source"] == s["name"]] if not df.empty else df
            live = d[d["status"] != "CANCELLED"] if not d.empty else d
            rows.append({"Nguồn": s["name"], "Loại hình": s["source_type"], "Chu kỳ thanh toán": f"{s['pay_days']} ngày sau duyệt",
                         "Điều kiện nhận tiền": s["pay_rule"], "Số khoản": len(d),
                         "Tổng doanh thu": vnd(live["amount"].sum() if len(live) else 0),
                         "Đã thực nhận": vnd(live["received_amount"].sum() if len(live) else 0)})
        st.dataframe(pd.DataFrame(rows), hide_index=True)
    else:
        st.info("Chưa có nguồn thu nào. Hãy thêm nguồn đầu tiên bên dưới.")

    t1, t2 = st.tabs(["➕ Thêm nguồn thu", "✏️ Sửa / 🗑️ Xóa"])
    with t1:
        with st.form("add_source", clear_on_submit=True):
            c1, c2 = st.columns(2)
            name = c1.text_input("Tên nguồn thu *", placeholder="VD: Shopee Affiliate")
            typ = c2.selectbox("Loại hình", db.SOURCE_TYPES)
            days = c1.number_input("Số ngày từ lúc duyệt đến hạn thanh toán", 0, 365, 14)
            rule = c2.text_input("Điều kiện nhận tiền (không bắt buộc)")
            if st.form_submit_button("Thêm nguồn thu", type="primary"):
                try:
                    db.add_source(name, typ, days, rule)
                    flash(f"Đã thêm nguồn '{name.strip()}'.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    with t2:
        if not srcs:
            st.info("Chưa có nguồn nào để sửa.")
            return
        sid = st.selectbox("Chọn nguồn", [s["id"] for s in srcs],
                           format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i), key="src_edit_pick")
        s = next(x for x in srcs if x["id"] == sid)
        with st.form(f"edit_source_{sid}"):
            c1, c2 = st.columns(2)
            name = c1.text_input("Tên nguồn thu", s["name"])
            typ = c2.selectbox("Loại hình", db.SOURCE_TYPES,
                               index=db.SOURCE_TYPES.index(s["source_type"]) if s["source_type"] in db.SOURCE_TYPES else 4)
            days = c1.number_input("Số ngày đến hạn thanh toán", 0, 365, int(s["pay_days"]))
            rule = c2.text_input("Điều kiện nhận tiền", s["pay_rule"] or "")
            if st.form_submit_button("Lưu thay đổi"):
                try:
                    db.update_source(sid, name, typ, days, rule)
                    flash("Đã lưu thay đổi nguồn thu.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        n = db.count_incomes_of_source(sid)
        st.markdown("**Xóa nguồn thu**")
        cascade = st.checkbox(f"Xóa luôn {n} khoản thu thuộc nguồn này", key=f"cas_{sid}") if n else False
        if st.button("🗑️ Xóa nguồn này", key=f"del_src_{sid}"):
            try:
                db.delete_source(sid, cascade)
                flash(f"Đã xóa nguồn '{s['name']}'.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))


# ---------- Trang 3: Quản lý khoản thu ----------
def page_incomes():
    st.title("💰 Income Management – Quản lý khoản thu")
    srcs = db.list_sources()
    if not srcs:
        st.warning("Hãy tạo ít nhất một nguồn thu ở trang *Nguồn thu* trước.")
        return
    df = sv.income_df()
    tabs = st.tabs(["📋 Danh sách", "➕ Thêm khoản thu", "🔄 Chi tiết & trạng thái", "⬇️ Nhập dữ liệu (API / file)"])

    with tabs[0]:
        if df.empty:
            st.info("Chưa có khoản thu nào.")
        else:
            c1, c2, c3 = st.columns(3)
            fs = c1.multiselect("Nguồn", sorted(df["source"].unique()))
            fst = c2.multiselect("Trạng thái", list(STATUS_LABEL), format_func=lambda s: STATUS_LABEL[s])
            dmin, dmax = pd.to_datetime(df["generated_date"]).min().date(), pd.to_datetime(df["generated_date"]).max().date()
            rng = c3.date_input("Khoảng ngày phát sinh", (dmin, dmax), format="DD/MM/YYYY", key="flt_range")
            f = df
            if fs:
                f = f[f["source"].isin(fs)]
            if fst:
                f = f[f["status"].isin(fst)]
            if isinstance(rng, (tuple, list)) and len(rng) == 2:
                f = f[(f["generated_date"] >= rng[0].isoformat()) & (f["generated_date"] <= rng[1].isoformat())]
            st.caption(f"{len(f)} khoản · tổng {vnd(f['amount'].sum())}")
            show_incomes(f)

    with tabs[1]:
        with st.form("add_income", clear_on_submit=True):
            c1, c2 = st.columns(2)
            sid = c1.selectbox("Nguồn thu *", [s["id"] for s in srcs],
                               format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i))
            amount = c2.number_input("Số tiền (đ) *", min_value=0, value=1_000_000, step=100_000)
            title = c1.text_input("Nội dung / mô tả")
            gdate = c2.date_input("Ngày phát sinh", date.today(), format="DD/MM/YYYY")
            code = c1.text_input("Mã khoản thu (để trống = tự tạo)")
            status = c2.selectbox("Trạng thái ban đầu", ["GENERATED", "UNDER_REVIEW", "APPROVED"],
                                  format_func=lambda s: STATUS_LABEL[s])
            if st.form_submit_button("Thêm khoản thu", type="primary"):
                try:
                    iid = db.add_income(sid, amount, gdate, title, "GENERATED", code)
                    if status != "GENERATED":
                        db.change_status(iid, status, "Trạng thái ban đầu")
                    flash(f"Đã thêm khoản thu {db.get_income(iid)['code']} – {vnd(amount)}.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))

    with tabs[2]:
        if df.empty:
            st.info("Chưa có khoản thu nào.")
        else:
            iid = pick_income(df, "detail_pick")
            inc = db.get_income(iid)
            m = st.columns(4)
            m[0].metric("Số tiền", vnd(inc["amount"]))
            m[1].metric("Đã thực nhận", vnd(inc["received_amount"]))
            m[2].metric("Ngày phát sinh", inc["generated_date"])
            m[3].metric("Hạn dự kiến", inc["expected_pay_date"] or "-")
            status_actions(iid, f"inc_{iid}")
            st.markdown("**Lịch sử thay đổi trạng thái**")
            h = pd.DataFrame(db.history_of(iid))
            h["from_status"] = h["from_status"].map(lambda s: STATUS_LABEL.get(s, "—"))
            h["to_status"] = h["to_status"].map(STATUS_LABEL)
            st.dataframe(h[["changed_at", "from_status", "to_status", "note"]].rename(columns={
                "changed_at": "Thời điểm", "from_status": "Từ", "to_status": "Sang", "note": "Ghi chú"}), hide_index=True)
            with st.expander("✏️ Sửa thông tin khoản thu"):
                with st.form(f"edit_income_{iid}"):
                    c1, c2 = st.columns(2)
                    sid = c1.selectbox("Nguồn thu", [s["id"] for s in srcs], index=[s["id"] for s in srcs].index(inc["source_id"]),
                                       format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i))
                    amount = c2.number_input("Số tiền (đ)", min_value=0, value=int(inc["amount"]), step=100_000)
                    title = c1.text_input("Nội dung", inc["title"] or "")
                    gdate = c2.date_input("Ngày phát sinh", date.fromisoformat(inc["generated_date"]), format="DD/MM/YYYY")
                    exp = c1.date_input("Hạn thanh toán dự kiến", date.fromisoformat(inc["expected_pay_date"] or inc["generated_date"]), format="DD/MM/YYYY")
                    note = c2.text_input("Ghi chú", inc["note"] or "")
                    if st.form_submit_button("Lưu thay đổi"):
                        try:
                            db.update_income(iid, sid, title, amount, gdate, exp, note)
                            flash("Đã lưu thay đổi.")
                            st.rerun()
                        except ValueError as e:
                            st.error(str(e))
            with st.expander("🗑️ Xóa khoản thu"):
                ok = st.checkbox(f"Tôi chắc chắn muốn xóa {inc['code']}", key=f"delchk_{iid}")
                if st.button("Xóa khoản thu", key=f"delbtn_{iid}", disabled=not ok):
                    db.delete_income(iid)
                    flash(f"Đã xóa khoản thu {inc['code']}.")
                    st.rerun()

    with tabs[3]:
        import_section(srcs)


def import_section(srcs):
    st.subheader("A. Lấy dữ liệu từ API giả lập")
    if not sv.ensure_api():
        st.error("Không kết nối được API giả lập. Thử chạy riêng: `uvicorn mock_api:app --port 8008`")
    else:
        st.caption(f"API giả lập đang chạy tại {sv.API_BASE}  (xem tài liệu: {sv.API_BASE}/docs)")
        c1, c2, c3 = st.columns([2, 2, 1])
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
            st.markdown(f"**Dữ liệu thô từ {plat_p}** (trường `{cfg['amount']}` = *{cfg['raw_label']}*)")
            st.code(json.dumps(raw, ensure_ascii=False, indent=2), language="json")
            st.markdown(f"**Sau khi chuẩn hóa** (→ *{cfg['norm_label']}*, đơn vị VND)")
            nd = pd.DataFrame(norm)
            nd["amount"] = nd["amount"].map(vnd)
            st.dataframe(nd.rename(columns={"external_id": "Mã ngoài", "amount": "Số tiền", "generated_date": "Ngày",
                                            "title": "Nội dung", "source_name": "Nguồn thu"}), hide_index=True)
            if st.button("✅ Nhập vào hệ thống", key="api_commit", type="primary"):
                a, d = sv.import_normalized(norm, "API giả lập")
                st.session_state.pop("api_preview", None)
                flash(f"Đã nhập {a} khoản thu từ API" + (f" (bỏ qua {d} khoản trùng mã)" if d else "") + ".")
                st.rerun()

    st.divider()
    st.subheader("B. Tải file Excel / CSV")
    up = st.file_uploader("Chọn file doanh thu (.csv, .xlsx)", type=["csv", "xlsx", "xls"], key="inc_file")
    if up:
        try:
            df = sv.read_table(up)
        except Exception as e:
            st.error(str(e))
            return
        st.dataframe(df.head(10), hide_index=True)
        cols = ["(không có)"] + list(df.columns)
        g = lambda k: sv.guess_column(df.columns, sv.INCOME_ALIASES[k])
        idx = lambda k: cols.index(g(k)) if g(k) in cols else 0
        st.markdown("**Ánh xạ cột** (tự đoán, bạn có thể sửa):")
        c = st.columns(4)
        m_amt = c[0].selectbox("Cột số tiền *", cols, idx("amount"), key="m_amt")
        m_date = c[1].selectbox("Cột ngày", cols, idx("generated_date"), key="m_date")
        m_id = c[2].selectbox("Cột mã", cols, idx("external_id"), key="m_id")
        m_title = c[3].selectbox("Cột nội dung", cols, idx("title"), key="m_title")
        c2 = st.columns(2)
        sid = c2[0].selectbox("Gán vào nguồn thu", [s["id"] for s in srcs],
                              format_func=lambda i: next(s["name"] for s in srcs if s["id"] == i), key="file_src")
        st0 = c2[1].selectbox("Trạng thái ban đầu", ["GENERATED", "UNDER_REVIEW", "APPROVED", "PENDING_PAYMENT"],
                              format_func=lambda s: STATUS_LABEL[s], key="file_status")
        if st.button("✅ Nhập file vào hệ thống", key="file_commit", type="primary", disabled=m_amt == "(không có)"):
            n = lambda v: None if v == "(không có)" else v
            a, d, b, errs = sv.import_income_rows(df, dict(amount=n(m_amt), generated_date=n(m_date),
                                                           external_id=n(m_id), title=n(m_title)), sid, st0)
            flash(f"Đã nhập {a} khoản thu" + (f", bỏ qua {d} khoản trùng mã" if d else "") +
                  (f", {b} dòng lỗi ({'; '.join(errs)})" if b else "") + ".", "success" if a else "warning")
            st.rerun()
    st.caption("File mẫu nằm trong thư mục `sample_data/` (tiktok_revenue.csv, freelancer_projects.xlsx).")


# ---------- Trang 4: Theo dõi thanh toán ----------
def page_payments():
    st.title("⏳ Payment Tracking – Theo dõi thanh toán")
    n = db.refresh_overdue()
    if n:
        st.warning(f"Hệ thống vừa tự đánh dấu {n} khoản là **trễ hạn** do quá ngày thanh toán dự kiến.")
    df = sv.income_df()
    if df.empty:
        st.info("Chưa có khoản thu nào.")
        return
    df["remaining"] = df["amount"] - df["received_amount"]
    groups = {
        "✅ Đã được duyệt": df[df["status"] == "APPROVED"],
        "💤 Đang chờ thanh toán": df[df["status"].isin(["PENDING_PAYMENT", "PARTIALLY_PAID"])],
        "🗓️ Lịch thanh toán dự kiến": df[df["status"].isin(db.WAITING_PAY_SET)].sort_values("expected_pay_date"),
        "🚨 Trễ hạn": df[df["status"] == "OVERDUE"],
        "⚠️ Hủy / tranh chấp": df[df["status"].isin(["CANCELLED", "DISPUTED"])],
    }
    m = st.columns(len(groups))
    for col, (name, d) in zip(m, groups.items()):
        col.metric(name, len(d))
    tabs = st.tabs(list(groups))
    cols = ["code", "source", "amount", "received_amount", "expected_pay_date", "status"]
    for tab, (name, d) in zip(tabs, groups.items()):
        with tab:
            if d.empty:
                st.success("Không có khoản nào trong nhóm này.")
            else:
                if "Lịch" in name:
                    d = d.assign(**{"Còn (ngày)": (pd.to_datetime(d["expected_pay_date"]) - pd.Timestamp(date.today())).dt.days})
                shown = label_df(d)[cols + (["Còn (ngày)"] if "Lịch" in name else [])].rename(columns=INCOME_COLS)
                st.dataframe(shown, hide_index=True)
    st.divider()
    st.subheader("Cập nhật nhanh trạng thái")
    open_df = df[~df["status"].isin(["RECONCILED", "CANCELLED"])]
    iid = pick_income(open_df, "pay_pick")
    if iid:
        status_actions(iid, f"pay_{iid}")


# ---------- Trang 5: Đối soát ----------
def page_recon():
    st.title("🏦 Reconciliation – Đối soát sao kê ngân hàng")
    tabs = st.tabs(["1️⃣ Tải sao kê", "2️⃣ Đối soát tự động", "3️⃣ Cần kiểm tra", "📜 Lịch sử đối soát"])

    with tabs[0]:
        up = st.file_uploader("Tải sao kê ngân hàng (.csv, .xlsx)", type=["csv", "xlsx", "xls"], key="bank_file")
        if up:
            try:
                df = sv.read_table(up)
            except Exception as e:
                st.error(str(e))
                df = None
            if df is not None:
                st.dataframe(df.head(10), hide_index=True)
                cols = list(df.columns)
                g = lambda k: sv.guess_column(df.columns, sv.BANK_ALIASES[k])
                idx = lambda k: cols.index(g(k)) if g(k) in cols else 0
                c = st.columns(3)
                m_date = c[0].selectbox("Cột ngày giao dịch", cols, idx("txn_date"), key="b_date")
                m_amt = c[1].selectbox("Cột tiền vào (ghi có)", cols, idx("amount"), key="b_amt")
                m_desc = c[2].selectbox("Cột nội dung", cols, idx("description"), key="b_desc")
                st.caption("Chỉ giao dịch **tiền vào** (số tiền > 0) được đọc; dòng ghi nợ và dòng trùng sẽ bị bỏ qua.")
                if st.button("📥 Đọc giao dịch tiền vào", type="primary", key="bank_commit"):
                    a, d, s_ = sv.import_bank_rows(df, dict(txn_date=m_date, amount=m_amt, description=m_desc))
                    flash(f"Đã đọc {a} giao dịch tiền vào (bỏ qua {s_} dòng không phải tiền vào, {d} dòng trùng). "
                          "Sang bước 2 để đối soát.")
                    st.rerun()
        st.markdown("**Hoặc nhập tay một giao dịch tiền vào**")
        with st.form("add_txn", clear_on_submit=True):
            c = st.columns([1, 1, 3])
            d = c[0].date_input("Ngày", date.today(), format="DD/MM/YYYY")
            a = c[1].number_input("Số tiền (đ)", 0, value=1_000_000, step=100_000)
            desc = c[2].text_input("Nội dung chuyển khoản", placeholder="VD: GOOGLE ADSENSE YT001")
            if st.form_submit_button("Thêm giao dịch"):
                try:
                    db.add_bank_txn(d, a, desc)
                    flash("Đã thêm giao dịch.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
        bd = sv.bank_df()
        st.markdown(f"**Giao dịch đã có ({len(bd)})**")
        if not bd.empty:
            lab = {"NEW": "Chưa đối soát", "MATCHED": "Đã ghép", "REVIEW": "Cần kiểm tra",
                   "UNMATCHED": "Không khớp", "IGNORED": "Đã bỏ qua"}
            show = bd.assign(amount=bd["amount"].map(vnd), match_status=bd["match_status"].map(lab))
            st.dataframe(show[["id", "txn_date", "amount", "description", "match_status", "income_code"]].rename(columns={
                "id": "#", "txn_date": "Ngày", "amount": "Số tiền", "description": "Nội dung",
                "match_status": "Kết quả", "income_code": "Khớp với"}), hide_index=True)
            with st.expander("🗑️ Xóa một giao dịch"):
                tid = st.selectbox("Chọn giao dịch", bd["id"].tolist(), key="del_txn_pick", format_func=lambda i: (
                    f"#{i} · {bd[bd['id'] == i]['txn_date'].iloc[0]} · {vnd(bd[bd['id'] == i]['amount'].iloc[0])} · "
                    f"{bd[bd['id'] == i]['description'].iloc[0]}"))
                if st.button("Xóa giao dịch", key="del_txn_btn"):
                    db.delete_bank_txn(int(tid))
                    flash("Đã xóa giao dịch (nếu đã ghép, khoản thu được hoàn về trạng thái trước).")
                    st.rerun()

    with tabs[1]:
        st.markdown("""Hệ thống so sánh **mã khoản thu trong nội dung**, **số tiền**, **ngày giao dịch** và **tên nền tảng**.
Chỉ tự xác nhận khi khớp **duy nhất và đúng số tiền**. Nhận thiếu (phí / thanh toán một phần) hoặc có nhiều ứng viên
được chuyển sang danh sách kiểm tra để tránh xác nhận sai.""")
        c = st.columns(2)
        tol = c[0].slider("Ngưỡng nhận thiếu có thể do phí (%)", 0.0, 10.0, 3.0, 0.5)
        win = c[1].slider("Cửa sổ ngày so với hạn dự kiến (±ngày)", 1, 30, 10)
        pending = sv.bank_df()
        pending = pending[pending["match_status"].isin(["NEW", "REVIEW", "UNMATCHED"])] if not pending.empty else pending
        st.caption(f"{len(pending)} giao dịch đang chờ đối soát.")
        if st.button("🤖 Chạy đối soát tự động", type="primary", disabled=len(pending) == 0, key="run_recon"):
            r = sv.run_auto_reconcile(tol, win)
            flash(f"Kết quả: {r['matched']} giao dịch tự ghép · {r['review']} cần kiểm tra · {r['unmatched']} không tìm thấy khoản phù hợp.")
            st.rerun()

    with tabs[2]:
        bd = sv.bank_df()
        todo = bd[bd["match_status"].isin(["REVIEW", "UNMATCHED", "NEW"])] if not bd.empty else bd
        if todo.empty:
            st.success("Không có giao dịch nào cần kiểm tra.")
        incs = sv.income_df()
        open_incs = incs[incs["status"].isin(db.AWAITING_MONEY_SET)] if not incs.empty else incs
        for _, t in todo.iterrows():
            with st.container(border=True):
                st.markdown(f"**#{t['id']} · {t['txn_date']} · {vnd(t['amount'])}** — {t['description'] or '(không có nội dung)'}")
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
                c[0].write(f"#{t['id']} · {t['txn_date']} · {vnd(t['amount'])} → **{t['income_code']}** · {t['description']}")
                if c[1].button("Hủy ghép", key=f"un_{t['id']}"):
                    db.unmatch(int(t["id"]))
                    flash("Đã hủy đối soát, khoản thu được hoàn về trạng thái trước.")
                    st.rerun()
        st.markdown("**Nhật ký đối soát**")
        lg = sv.recon_log_df()
        if lg.empty:
            st.caption("Chưa có nhật ký.")
        else:
            lg = lg.assign(amount=lg["amount"].map(vnd), diff=lg["diff"].map(lambda x: "-" if pd.isna(x) else vnd(x)))
            st.dataframe(lg[["ts", "txn_id", "income_code", "action", "amount", "diff", "note"]].rename(columns={
                "ts": "Thời điểm", "txn_id": "Giao dịch #", "income_code": "Khoản thu", "action": "Hành động",
                "amount": "Số tiền", "diff": "Chênh lệch/còn lại", "note": "Ghi chú"}), hide_index=True)


# ---------- Trang 6: Báo cáo ----------
def page_reports():
    st.title("📈 Reports – Báo cáo thu nhập")
    df = sv.income_df()
    live = df[df["status"] != "CANCELLED"] if not df.empty else df
    if live.empty:
        st.info("Chưa có dữ liệu để lập báo cáo.")
        return
    k = sv.kpis(df)
    c = st.columns(4)
    c[0].metric("Doanh thu phát sinh", vnd(k["generated"]))
    c[1].metric("Được duyệt", vnd(k["approved"]))
    c[2].metric("Đã thực nhận", vnd(k["received"]))
    c[3].metric("Còn chờ thanh toán", vnd(k["waiting"]))

    monthly = live.groupby(["month", "source"], as_index=False)["amount"].sum()
    st.plotly_chart(px.bar(monthly, x="month", y="amount", color="source", title="Doanh thu theo tháng (theo nguồn)",
                           labels={"month": "Tháng", "amount": "VND", "source": "Nguồn"}))
    a, b = st.columns(2)
    by_src = live.groupby("source", as_index=False)["amount"].sum()
    a.plotly_chart(px.pie(by_src, names="source", values="amount", hole=0.4, title="Tỷ trọng nguồn thu"))
    ps = live.assign(tt=live["status"].map(STATUS_LABEL)).groupby("tt", as_index=False).agg(
        amount=("amount", "sum"), received=("received_amount", "sum"))
    b.plotly_chart(px.bar(ps, x="tt", y="amount", title="Tình trạng thanh toán (theo trạng thái)", color="tt",
                          color_discrete_map=STATUS_COLORS, labels={"tt": "Trạng thái", "amount": "VND"}))

    tm = live.groupby("month", as_index=False)["amount"].sum().sort_values("month")
    tm["Tăng trưởng (%)"] = (tm["amount"].pct_change() * 100).round(1)
    l, r = st.columns(2)
    with l:
        st.markdown("**Tốc độ tăng trưởng thu nhập theo tháng**")
        if len(tm) >= 2:
            st.plotly_chart(px.line(tm.dropna(), x="month", y="Tăng trưởng (%)", markers=True))
        st.dataframe(tm.assign(amount=tm["amount"].map(vnd)).rename(columns={"month": "Tháng", "amount": "Doanh thu"}),
                     hide_index=True)
        if len(tm) < 2:
            st.caption("Cần dữ liệu từ 2 tháng trở lên để tính tăng trưởng.")
    with r:
        st.markdown("**Nền tảng nào thanh toán chậm?**")
        d = live.dropna(subset=["paid_date", "expected_pay_date"]).copy()
        d["delay"] = (pd.to_datetime(d["paid_date"]) - pd.to_datetime(d["expected_pay_date"])).dt.days
        slow = d.groupby("source", as_index=False).agg(**{"Số ngày trễ trung bình": ("delay", "mean")})
        od = live[live["status"] == "OVERDUE"].groupby("source").size().rename("Số khoản đang trễ hạn")
        slow = slow.merge(od, left_on="source", right_index=True, how="outer").fillna(0).rename(columns={"source": "Nguồn"})
        st.dataframe(slow.round(1), hide_index=True)

    # xuất báo cáo
    summary = pd.DataFrame({"Chỉ tiêu": ["Doanh thu phát sinh", "Thu nhập được duyệt", "Còn chờ thanh toán",
                                          "Chờ đối soát", "Tiền đã thực nhận"],
                            "Giá trị (đ)": [k["generated"], k["approved"], k["waiting"], k["unreconciled"], k["received"]]})
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        summary.to_excel(xw, sheet_name="Tong hop", index=False)
        label_src = df.assign(status=df["status"].map(STATUS_LABEL)).drop(columns=["id"])
        label_src.to_excel(xw, sheet_name="Khoan thu", index=False)
        by_src.rename(columns={"source": "Nguon", "amount": "Doanh thu"}).to_excel(xw, sheet_name="Theo nguon", index=False)
        monthly.to_excel(xw, sheet_name="Theo thang", index=False)
        sv.bank_df().to_excel(xw, sheet_name="Sao ke", index=False)
        sv.recon_log_df().to_excel(xw, sheet_name="Lich su doi soat", index=False)
    d1, d2 = st.columns(2)
    d1.download_button("⬇️ Xuất báo cáo Excel", buf.getvalue(), f"creatorflow_bao_cao_{date.today()}.xlsx",
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", type="primary")
    d2.download_button("⬇️ Xuất danh sách khoản thu (CSV)", label_src.to_csv(index=False).encode("utf-8-sig"),
                       f"khoan_thu_{date.today()}.csv", "text/csv")


# ---------- Điều hướng ----------
def main():
    with st.sidebar:
        st.markdown("## 💸 CreatorFlow")
        st.caption("Quản lý & đối soát vòng đời thu nhập đa nền tảng")
        page = st.radio("Điều hướng", PAGES, label_visibility="collapsed")
        st.divider()
        with st.expander("⚙️ Dữ liệu"):
            if st.button("Khôi phục dữ liệu mẫu"):
                db.reset_all(True)
                flash("Đã khôi phục dữ liệu mẫu.")
                st.rerun()
            if st.button("Xóa toàn bộ dữ liệu (làm mới hoàn toàn)"):
                db.reset_all(False)
                flash("Đã xóa toàn bộ dữ liệu. Hãy tạo nguồn thu mới.", "warning")
                st.rerun()
    show_flash()
    {"📊 Dashboard": page_dashboard, "🗂️ Nguồn thu": page_sources, "💰 Quản lý khoản thu": page_incomes,
     "⏳ Theo dõi thanh toán": page_payments, "🏦 Đối soát": page_recon, "📈 Báo cáo": page_reports}[page]()


main()
