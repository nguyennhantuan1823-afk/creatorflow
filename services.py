"""Nghiệp vụ: chuẩn hóa dữ liệu, đọc file, đối soát tự động, số liệu Dashboard/báo cáo."""
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime

import pandas as pd
import requests

import db

API_PORT = int(os.environ.get("CREATORFLOW_API_PORT", "8008"))
API_BASE = os.environ.get("CREATORFLOW_API_BASE", f"http://127.0.0.1:{API_PORT}")

# ---------- API giả lập ----------
_server_lock = threading.Lock()
_server_started = False


def api_alive():
    try:
        return requests.get(f"{API_BASE}/health", timeout=1.5).ok
    except Exception:
        return False


def ensure_api():
    """Tự khởi động API giả lập ở luồng nền nếu chưa chạy."""
    global _server_started
    if api_alive():
        return True
    with _server_lock:
        if not _server_started and not api_alive():
            import uvicorn
            from mock_api import app
            cfg = uvicorn.Config(app, host="127.0.0.1", port=API_PORT, log_level="error")
            server = uvicorn.Server(cfg)
            threading.Thread(target=server.run, daemon=True).start()
            _server_started = True
    for _ in range(40):
        if api_alive():
            return True
        time.sleep(0.15)
    return False


# Chuẩn hóa: mỗi nền tảng một cấu hình ánh xạ trường -> cấu trúc thống nhất
PLATFORMS = {
    "YouTube": dict(endpoint="/youtube/revenue", list_key="items", id="report_id", amount="estimated_revenue",
                    unit=1, date="report_date", raw_label="Estimated revenue", norm_label="Doanh thu ước tính",
                    source="YouTube", title="Doanh thu quảng cáo YouTube"),
    "TikTok Affiliate": dict(endpoint="/tiktok/affiliate", list_key="data", id="order_batch", amount="commission",
                             unit=1, date="created", raw_label="Commission", norm_label="Hoa hồng",
                             source="TikTok Affiliate", title="Hoa hồng TikTok Affiliate"),
    "Facebook": dict(endpoint="/facebook/payouts", list_key="payouts", id="payout_id", amount="payout_amount_k",
                     unit=1000, date="paid_on", raw_label="Payout amount (nghìn đồng)", norm_label="Khoản thanh toán",
                     source="Facebook", title="Payout Facebook"),
    "Freelancer": dict(endpoint="/freelancer/projects", list_key="projects", id="project_id",
                       amount="project_payment", unit=1, date="finished_at", raw_label="Project payment",
                       norm_label="Tiền hợp đồng", source="Freelancer", title="Thanh toán dự án freelance"),
}


def fetch_platform(name, random_data=False, n=2):
    cfg = PLATFORMS[name]
    r = requests.get(API_BASE + cfg["endpoint"], params={"random_data": random_data, "n": n}, timeout=5)
    r.raise_for_status()
    raw = r.json()[cfg["list_key"]]
    out = []
    for it in raw:  # chuẩn hóa về 1 cấu trúc
        out.append(dict(
            external_id=str(it[cfg["id"]]),
            amount=float(it[cfg["amount"]]) * cfg["unit"],
            generated_date=str(it[cfg["date"]]),
            title=cfg["title"],
            source_name=cfg["source"],
        ))
    return raw, out


def import_normalized(records, origin):
    """Ghi các bản ghi đã chuẩn hóa vào DB; bỏ qua bản ghi trùng external_id. Trả (số thêm, số trùng)."""
    added = dup = 0
    for r in records:
        s = db.source_by_name(r["source_name"])
        sid = s["id"] if s else db.add_source(r["source_name"], "Khác", 14, "")
        if r.get("external_id") and db.external_exists(sid, r["external_id"]):
            dup += 1
            continue
        db.add_income(sid, r["amount"], r["generated_date"], r.get("title", ""), "GENERATED",
                      None, None, origin, r.get("external_id"), "", f"Nhập từ {origin}")
        added += 1
    return added, dup


# ---------- Đọc file & chuẩn hóa ----------
def parse_money(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s or s in "-.,":
        return None
    if "," in s and "." in s:       # 1.234.567,89  hoặc 1,234,567.89
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(",") > 1 or s.count(".") > 1:
        s = s.replace(",", "").replace(".", "")
    elif "," in s:
        a, b = s.split(",")
        s = a + b if len(b) == 3 else a + "." + b
    elif "." in s:
        a, b = s.split(".")
        s = a + b if len(b) == 3 else s
    try:
        return float(s)
    except ValueError:
        return None


def parse_date(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (datetime, pd.Timestamp)):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s[:10] if fmt.startswith("%Y-") else s, fmt).date().isoformat()
        except ValueError:
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date().isoformat()
    except Exception:
        return None


def _norm(s):
    s = unicodedata.normalize("NFD", str(s).lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn").replace("đ", "d").strip()


def read_table(uploaded):
    name = uploaded.name.lower()
    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded)
    for enc in ("utf-8-sig", "utf-8", "cp1258", "latin-1"):
        try:
            uploaded.seek(0)
            return pd.read_csv(uploaded, encoding=enc, sep=None, engine="python")
        except Exception:
            continue
    raise ValueError("Không đọc được file. Hãy dùng CSV (UTF-8) hoặc Excel .xlsx.")


def guess_column(columns, aliases):
    cols = {c: _norm(c) for c in columns}
    for a in aliases:
        for c, n in cols.items():
            if a in n:
                return c
    return None


INCOME_ALIASES = dict(
    external_id=["ma", "id", "code", "order", "report"],
    title=["noi dung", "tieu de", "mo ta", "title", "description", "ten"],
    amount=["so tien", "doanh thu", "hoa hong", "commission", "amount", "revenue", "payout", "tien"],
    generated_date=["ngay", "date", "created", "thoi gian"],
    source=["nguon", "nen tang", "source", "platform"],
)
BANK_ALIASES = dict(
    txn_date=["ngay", "date", "thoi gian"],
    amount=["ghi co", "so tien co", "credit", "so tien", "amount", "tien vao"],
    description=["noi dung", "dien giai", "mo ta", "description", "memo", "content"],
)


# ---------- Đối soát tự động ----------
def run_auto_reconcile(fee_tolerance_pct=3.0, date_window=10):
    """Duyệt mọi giao dịch NEW/REVIEW/UNMATCHED và ghép với khoản thu đang chờ tiền.
    Quy tắc an toàn: chỉ tự xác nhận khi khớp DUY NHẤT và đúng số tiền.
    Thiếu tiền (phí/thanh toán một phần) hoặc nhiều ứng viên -> đưa vào danh sách kiểm tra."""
    with db.conn() as c:
        txns = [dict(r) for r in c.execute(
            "SELECT * FROM bank_txns WHERE match_status IN ('NEW','REVIEW','UNMATCHED') ORDER BY txn_date, id")]
    stats = dict(matched=0, review=0, unmatched=0)
    for t in txns:
        cands = _candidates(t, fee_tolerance_pct, date_window)
        if not cands:
            db.set_txn_status(t["id"], "UNMATCHED", "Không tìm thấy khoản thu phù hợp")
            stats["unmatched"] += 1
            continue
        best = cands[0]
        unique = len(cands) == 1 or best["score"] - cands[1]["score"] >= 20
        if unique and best["exact"] and best["score"] >= 60:
            db.apply_match(t["id"], best["income"]["id"], "full", f"Tự động: điểm khớp {best['score']}")
            stats["matched"] += 1
        else:
            if not unique:
                why = "Có nhiều khoản thu phù hợp, cần người dùng chọn"
            elif best["diff"] > 0:
                why = (f"Nhận thiếu {best['diff']:,.0f}đ so với {best['income']['code']} "
                       f"({best['diff'] / best['income']['expected']:.1%}) - có thể do phí hoặc thanh toán một phần")
            else:
                why = "Độ tin cậy thấp, cần kiểm tra"
            db.set_txn_status(t["id"], "REVIEW", f"{why} | Gợi ý: {best['income']['code']}")
            stats["review"] += 1
    return stats


def _candidates(t, tol_pct, window):
    desc = _norm(t["description"])
    out = []
    with db.conn() as c:
        incs = [dict(r) for r in c.execute(
            f"""SELECT i.*, s.name AS source_name FROM incomes i JOIN sources s ON s.id=i.source_id
                WHERE i.status IN ({','.join('?' * len(db.AWAITING_MONEY_SET))})""", db.AWAITING_MONEY_SET)]
    for i in incs:
        expected = float(i["amount"]) - float(i["received_amount"])
        if expected <= 0:
            continue
        score = 0
        code_hit = _norm(i["code"]) in desc.replace(" ", "")
        if code_hit:
            score += 60
        src_norm = _norm(i["source_name"])
        if src_norm and (src_norm in desc or src_norm.split()[0] in desc):
            score += 10
        diff = expected - float(t["amount"])
        exact = abs(diff) < 0.5
        if exact:
            score += 30
        elif 0 < diff <= expected * tol_pct / 100:
            score += 15
        elif diff > 0 and code_hit:
            score += 5
        else:
            if not code_hit:
                continue
        try:
            gap = (date.fromisoformat(t["txn_date"]) - date.fromisoformat(i["expected_pay_date"] or i["generated_date"])).days
            if abs(gap) <= window:
                score += 10
            elif date.fromisoformat(t["txn_date"]) < date.fromisoformat(i["generated_date"]):
                score -= 30
        except Exception:
            pass
        if i["status"] == "PAID_UNRECONCILED":
            score += 5
        if score >= 30:
            i["expected"] = expected
            out.append(dict(income=i, score=score, exact=exact, diff=diff))
    return sorted(out, key=lambda x: -x["score"])


# ---------- Số liệu ----------
def income_df():
    with db.conn() as c:
        df = pd.read_sql_query(
            """SELECT i.id, i.code, s.name AS source, s.source_type, i.title, i.amount, i.received_amount,
                      i.generated_date, i.expected_pay_date, i.paid_date, i.status, i.origin, i.note
               FROM incomes i JOIN sources s ON s.id=i.source_id ORDER BY i.generated_date DESC, i.id DESC""", c)
    if not df.empty:
        df["month"] = df["generated_date"].str[:7]
    return df


def bank_df():
    with db.conn() as c:
        return pd.read_sql_query(
            """SELECT b.id, b.txn_date, b.amount, b.description, b.match_status, b.suggestion,
                      i.code AS income_code FROM bank_txns b LEFT JOIN incomes i ON i.id=b.income_id
               ORDER BY b.txn_date DESC, b.id DESC""", c)


def recon_log_df():
    with db.conn() as c:
        return pd.read_sql_query("SELECT * FROM recon_log ORDER BY id DESC", c)


def kpis(df):
    """Bốn thông tin KHÁC NHAU, không cộng dồn: phát sinh / được duyệt / chờ thanh toán / thực nhận."""
    if df.empty:
        return dict(generated=0, approved=0, waiting=0, unreconciled=0, received=0, overdue=0)
    live = df[df["status"] != "CANCELLED"]
    approved = live[live["status"].isin(db.APPROVED_SET)]
    waiting = live[live["status"].isin(db.WAITING_PAY_SET)]
    unrec = live[live["status"] == "PAID_UNRECONCILED"]
    return dict(
        generated=live["amount"].sum(),
        approved=approved["amount"].sum(),
        waiting=(waiting["amount"] - waiting["received_amount"]).sum(),
        unreconciled=unrec["amount"].sum(),
        received=live["received_amount"].sum(),
        overdue=live[live["status"] == "OVERDUE"]["amount"].sum(),
    )


def vnd(x):
    try:
        return f"{float(x):,.0f}".replace(",", ".") + "đ"
    except Exception:
        return "-"


# ---------- Nhập từ file ----------
def import_income_rows(df, colmap, source_id, status="GENERATED"):
    """colmap: {'external_id','title','amount','generated_date'} -> tên cột (hoặc None)."""
    added = dup = bad = 0
    errors = []
    for idx, row in df.iterrows():
        amount = parse_money(row[colmap["amount"]]) if colmap.get("amount") else None
        if not amount or amount <= 0:
            bad += 1
            errors.append(f"Dòng {idx + 2}: số tiền không hợp lệ")
            continue
        gdate = parse_date(row[colmap["generated_date"]]) if colmap.get("generated_date") else None
        gdate = gdate or db.today()
        ext = str(row[colmap["external_id"]]).strip() if colmap.get("external_id") else None
        if ext and ext.lower() == "nan":
            ext = None
        title = str(row[colmap["title"]]).strip() if colmap.get("title") else ""
        if title.lower() == "nan":
            title = ""
        if ext and db.external_exists(source_id, ext):
            dup += 1
            continue
        db.add_income(source_id, amount, gdate, title, "GENERATED", None, None, "File Excel/CSV", ext, "",
                      "Nhập từ file Excel/CSV")
        iid_status = status
        if iid_status != "GENERATED":
            with db.conn() as c:
                iid = c.execute("SELECT MAX(id) FROM incomes").fetchone()[0]
            path = {"UNDER_REVIEW": ["UNDER_REVIEW"], "APPROVED": ["APPROVED"],
                    "PENDING_PAYMENT": ["APPROVED", "PENDING_PAYMENT"]}.get(iid_status, [])
            for st_ in path:
                db.change_status(iid, st_, "Trạng thái ban đầu khi nhập file")
        added += 1
    return added, dup, bad, errors[:5]


def import_bank_rows(df, colmap):
    """Chỉ nhận giao dịch TIỀN VÀO (số tiền > 0). Bỏ qua dòng ghi nợ và dòng trùng."""
    added = dup = skipped = 0
    with db.conn() as c:
        existing = {(r["txn_date"], float(r["amount"]), (r["description"] or "").strip())
                    for r in c.execute("SELECT txn_date, amount, description FROM bank_txns")}
    for _, row in df.iterrows():
        amount = parse_money(row[colmap["amount"]])
        d = parse_date(row[colmap["txn_date"]])
        desc = str(row[colmap["description"]]).strip() if colmap.get("description") else ""
        if desc.lower() == "nan":
            desc = ""
        if not amount or amount <= 0 or not d:
            skipped += 1
            continue
        key = (d, float(amount), desc)
        if key in existing:
            dup += 1
            continue
        db.add_bank_txn(d, amount, desc)
        existing.add(key)
        added += 1
    return added, dup, skipped
