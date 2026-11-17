"""Nghiệp vụ: chuẩn hóa dữ liệu, đọc file, đối soát tự động, số liệu Dashboard/báo cáo."""
import os
import re
import threading
import time
import unicodedata
from datetime import date, datetime, timedelta

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


def _call_local(name, random_data, n):
    """Dự phòng: nếu không gọi được API qua mạng nội bộ thì gọi thẳng hàm của API giả lập."""
    import mock_api
    fn = {"YouTube": mock_api.youtube, "TikTok Affiliate": mock_api.tiktok,
          "Facebook": mock_api.facebook, "Freelancer": mock_api.freelancer}[name]
    return fn(random_data=random_data, n=n)


def fetch_platform(name, random_data=False, n=2):
    cfg = PLATFORMS[name]
    try:
        r = requests.get(API_BASE + cfg["endpoint"], params={"random_data": random_data, "n": n}, timeout=5)
        r.raise_for_status()
        data = r.json()
    except Exception:
        data = _call_local(name, random_data, n)
    raw = data[cfg["list_key"]]
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
        return dict(generated=0, approved=0, waiting=0, unreconciled=0, received=0, overdue=0, paid=0)
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
        paid=live["received_amount"].sum() + unrec["amount"].sum(),
    )


def vnd(x):
    try:
        return f"{float(x):,.0f}".replace(",", ".") + "đ"
    except Exception:
        return "-"


# ---------- Kiểm tra dữ liệu nhập ----------
MAX_AMOUNT = 100_000_000_000  # 100 tỷ đồng

_DIG = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def _read3(n, full):
    tram, chuc, dv = n // 100, n // 10 % 10, n % 10
    out = []
    if tram or full:
        out.append(_DIG[tram] + " trăm")
    if chuc > 1:
        out.append(_DIG[chuc] + " mươi")
        out.append("mốt" if dv == 1 else "lăm" if dv == 5 else (_DIG[dv] if dv else ""))
    elif chuc == 1:
        out.append("mười")
        out.append("lăm" if dv == 5 else (_DIG[dv] if dv else ""))
    elif dv:
        if tram or full:
            out.append("lẻ")
        out.append(_DIG[dv])
    return " ".join(x for x in out if x)


def vn_words(n):
    """Đọc số tiền bằng chữ tiếng Việt: 8000000 -> 'Tám triệu đồng'."""
    n = int(round(float(n or 0)))
    if n <= 0:
        return "Không đồng"
    units = ["", "nghìn", "triệu", "tỷ", "nghìn tỷ", "triệu tỷ"]
    groups = []
    while n:
        groups.append(n % 1000)
        n //= 1000
    parts = []
    for i in range(len(groups) - 1, -1, -1):
        g = groups[i]
        if g == 0:
            continue
        full = i != len(groups) - 1
        parts.append((_read3(g, full) + " " + units[i]).strip())
    text = " ".join(parts) + " đồng"
    return text[0].upper() + text[1:]


def find_duplicate_incomes(source_id, amount, gdate, exclude_id=None):
    with db.conn() as c:
        rows = c.execute(
            "SELECT code FROM incomes WHERE source_id=? AND amount=? AND generated_date=? AND id<>?",
            (source_id, float(amount or 0), str(gdate), exclude_id or -1)).fetchall()
    return [r["code"] for r in rows]


def validate_income(source_id, amount, gdate, exp, code="", title="", exclude_id=None):
    """Trả về (errors, warnings). Lỗi chặn không cho lưu; cảnh báo chỉ nhắc."""
    errors, warns = [], []
    if not source_id:
        errors.append("Chưa chọn nguồn thu.")
    amount = float(amount or 0)
    if amount <= 0:
        errors.append("Số tiền phải lớn hơn 0.")
    elif amount > MAX_AMOUNT:
        errors.append("Số tiền vượt quá 100 tỷ đồng, hãy kiểm tra lại số lượng chữ số 0.")
    elif amount >= 1_000_000_000:
        warns.append("Số tiền từ 1 tỷ đồng trở lên, hãy kiểm tra lại số lượng chữ số 0.")
    elif 0 < amount < 1000:
        warns.append("Số tiền dưới 1.000đ, có thể bạn đang nhập theo đơn vị nghìn/triệu?")
    if gdate and gdate > date.today():
        errors.append("Ngày phát sinh không được ở tương lai.")
    elif gdate and (date.today() - gdate).days > 730:
        warns.append("Ngày phát sinh cách đây hơn 2 năm, hãy kiểm tra lại năm.")
    if gdate and exp and exp < gdate:
        errors.append("Hạn thanh toán dự kiến không được trước ngày phát sinh.")
    code = (code or "").strip()
    if code:
        if not re.fullmatch(r"[A-Za-z0-9_\-]{2,20}", code):
            errors.append("Mã khoản thu chỉ gồm chữ không dấu, số, dấu - hoặc _ (2–20 ký tự).")
        else:
            with db.conn() as c:
                hit = c.execute("SELECT 1 FROM incomes WHERE lower(code)=lower(?) AND id<>?",
                                (code, exclude_id or -1)).fetchone()
            if hit:
                errors.append(f"Mã '{code.upper()}' đã tồn tại.")
    if len(title or "") > 120:
        errors.append("Nội dung tối đa 120 ký tự.")
    return errors, warns


def validate_txn(amount, d, desc):
    errors, warns = [], []
    amount = float(amount or 0)
    if amount <= 0:
        errors.append("Số tiền phải lớn hơn 0.")
    elif amount > MAX_AMOUNT:
        errors.append("Số tiền vượt quá 100 tỷ đồng.")
    if d and d > date.today():
        errors.append("Ngày giao dịch không được ở tương lai.")
    if not (desc or "").strip():
        warns.append("Nội dung chuyển khoản trống sẽ khó đối soát tự động. Nên nhập mã khoản thu (vd. YT001).")
    if amount > 0 and d:
        with db.conn() as c:
            hit = c.execute("SELECT 1 FROM bank_txns WHERE txn_date=? AND amount=? AND description=?",
                            (str(d), amount, (desc or "").strip())).fetchone()
        if hit:
            warns.append("Đã có giao dịch giống hệt (cùng ngày, số tiền, nội dung).")
    return errors, warns


def validate_source(name, pay_days, exclude_id=None):
    errors = []
    name = (name or "").strip()
    if len(name) < 2:
        errors.append("Tên nguồn thu cần ít nhất 2 ký tự.")
    elif len(name) > 40:
        errors.append("Tên nguồn thu tối đa 40 ký tự.")
    else:
        with db.conn() as c:
            hit = c.execute("SELECT 1 FROM sources WHERE lower(name)=lower(?) AND id<>?",
                            (name, exclude_id or -1)).fetchone()
        if hit:
            errors.append(f"Nguồn thu '{name}' đã tồn tại.")
    if pay_days is None or not (0 <= int(pay_days) <= 365):
        errors.append("Số ngày đến hạn phải từ 0 đến 365.")
    return errors


# ---------- Bộ lọc thời gian & chỉ số cho biểu đồ ----------
PERIODS = ["Tất cả", "Tháng này", "30 ngày qua", "90 ngày qua", "Năm nay", "Tùy chọn"]
METRICS = ["Doanh thu phát sinh", "Thu nhập được duyệt", "Tiền đã thực nhận", "Còn chờ thanh toán"]


def period_range(label, custom=None):
    t = date.today()
    if label == "Tháng này":
        return t.replace(day=1), t
    if label == "30 ngày qua":
        return t - timedelta(days=29), t
    if label == "90 ngày qua":
        return t - timedelta(days=89), t
    if label == "Năm nay":
        return date(t.year, 1, 1), t
    if label == "Tùy chọn" and isinstance(custom, (tuple, list)) and len(custom) == 2:
        return custom[0], custom[1]
    return None, None


def prev_period(start, end):
    if not start or not end:
        return None, None
    n = (end - start).days + 1
    pe = start - timedelta(days=1)
    return pe - timedelta(days=n - 1), pe


def filter_df(df, start=None, end=None, sources=None, statuses=None):
    if df.empty:
        return df
    f = df
    if start:
        f = f[f["generated_date"] >= start.isoformat()]
    if end:
        f = f[f["generated_date"] <= end.isoformat()]
    if sources:
        f = f[f["source"].isin(sources)]
    if statuses:
        f = f[f["status"].isin(statuses)]
    return f


def metric_value(df, metric):
    if metric == "Doanh thu phát sinh":
        return df["amount"].where(df["status"] != "CANCELLED", 0)
    if metric == "Thu nhập được duyệt":
        return df["amount"].where(df["status"].isin(db.APPROVED_SET), 0)
    if metric == "Tiền đã thực nhận":
        return df["received_amount"]
    return (df["amount"] - df["received_amount"]).where(df["status"].isin(db.WAITING_PAY_SET), 0)


# ---------- Nhập từ file (có kiểm tra từng dòng) ----------
def _income_row_check(row, colmap, source_id, seen):
    amount = parse_money(row[colmap["amount"]]) if colmap.get("amount") else None
    if not amount or amount <= 0:
        return None, "❌ Số tiền không hợp lệ"
    if amount > MAX_AMOUNT:
        return None, "❌ Số tiền quá lớn"
    gdate = None
    if colmap.get("generated_date"):
        gdate = parse_date(row[colmap["generated_date"]])
        if not gdate:
            return None, "❌ Ngày không đọc được"
        if gdate > db.today():
            return None, "❌ Ngày ở tương lai"
    gdate = gdate or db.today()
    ext = None
    if colmap.get("external_id"):
        ext = str(row[colmap["external_id"]]).strip()
        if ext.lower() in ("nan", "none", ""):
            ext = None
    title = ""
    if colmap.get("title"):
        title = str(row[colmap["title"]]).strip()
        if title.lower() == "nan":
            title = ""
    if ext and (ext in seen or db.external_exists(source_id, ext)):
        return None, "⏭️ Trùng mã, sẽ bỏ qua"
    if ext:
        seen.add(ext)
    return dict(amount=amount, gdate=gdate, ext=ext, title=title[:120]), "✅ Hợp lệ"


def preview_income_rows(df, colmap, source_id):
    seen, out = set(), []
    for idx, row in df.iterrows():
        rec, msg = _income_row_check(row, colmap, source_id, seen)
        out.append({"Dòng": idx + 2, "Mã": rec["ext"] if rec else "",
                    "Số tiền": rec["amount"] if rec else None, "Ngày": rec["gdate"] if rec else "", "Kết quả": msg})
    return pd.DataFrame(out)


def import_income_rows(df, colmap, source_id, status="GENERATED"):
    added = dup = bad = 0
    errors = []
    seen = set()
    for idx, row in df.iterrows():
        rec, msg = _income_row_check(row, colmap, source_id, seen)
        if rec is None:
            if msg.startswith("⏭️"):
                dup += 1
            else:
                bad += 1
                errors.append(f"Dòng {idx + 2}: {msg[2:]}")
            continue
        iid = db.add_income(source_id, rec["amount"], rec["gdate"], rec["title"], "GENERATED", None, None,
                            "File Excel/CSV", rec["ext"], "", "Nhập từ file Excel/CSV")
        path = {"UNDER_REVIEW": ["UNDER_REVIEW"], "APPROVED": ["APPROVED"],
                "PENDING_PAYMENT": ["APPROVED", "PENDING_PAYMENT"]}.get(status, [])
        for st_ in path:
            db.change_status(iid, st_, "Trạng thái ban đầu khi nhập file")
        added += 1
    return added, dup, bad, errors[:5]


def preview_bank_rows(df, colmap):
    with db.conn() as c:
        existing = {(r["txn_date"], float(r["amount"]), (r["description"] or "").strip())
                    for r in c.execute("SELECT txn_date, amount, description FROM bank_txns")}
    out = []
    for idx, row in df.iterrows():
        amount = parse_money(row[colmap["amount"]])
        d = parse_date(row[colmap["txn_date"]])
        desc = str(row[colmap["description"]]).strip() if colmap.get("description") else ""
        desc = "" if desc.lower() == "nan" else desc
        if not amount or amount <= 0:
            msg = "➖ Không phải tiền vào, bỏ qua"
        elif not d:
            msg = "❌ Ngày không đọc được"
        elif d > db.today():
            msg = "❌ Ngày ở tương lai"
        elif (d, float(amount), desc) in existing:
            msg = "⏭️ Trùng giao dịch đã có"
        else:
            msg = "✅ Hợp lệ"
            existing.add((d, float(amount), desc))
        out.append({"Dòng": idx + 2, "Ngày": d or "", "Số tiền": amount, "Nội dung": desc, "Kết quả": msg})
    return pd.DataFrame(out)


def import_bank_rows(df, colmap):
    """Chỉ nhận giao dịch TIỀN VÀO hợp lệ, bỏ qua dòng ghi nợ và dòng trùng."""
    pv = preview_bank_rows(df, colmap)
    added = dup = skipped = 0
    for _, r in pv.iterrows():
        if r["Kết quả"].startswith("✅"):
            db.add_bank_txn(r["Ngày"], r["Số tiền"], r["Nội dung"])
            added += 1
        elif r["Kết quả"].startswith("⏭️"):
            dup += 1
        else:
            skipped += 1
    return added, dup, skipped


# ---------- File mẫu (ngày luôn tính theo hôm nay) ----------
def sample_bank_df():
    f = lambda n: (date.today() - pd.Timedelta(days=n)).strftime("%d/%m/%Y")
    return pd.DataFrame([
        [f(0), "", "8.000.000", "GOOGLE ADSENSE YT001 THANH TOAN"],
        [f(0), "", "3.000.000", "CK FREELANCER FR001 TAT TOAN"],
        [f(0), "", "3.104.000", "TIKTOK SHOP TI002 (da tru phi)"],
        [f(0), "", "6.000.000", "FREELANCER thanh toan du an"],
        [f(1), "250.000", "", "THANH TOAN HOA DON DIEN"],
        [f(1), "", "500.000", "CHUYEN KHOAN TIEN AN"],
    ], columns=["Ngày giao dịch", "Ghi nợ", "Ghi có", "Nội dung giao dịch"])


def sample_files():
    """Trả về {tên file: (bytes, mime)} để người dùng tải về thử chức năng nhập file."""
    import io
    f = lambda n: (date.today() - pd.Timedelta(days=n)).strftime("%d/%m/%Y")
    tiktok = pd.DataFrame([["TT-F-001", f(6), "4.000.000", "Hoa hồng đợt 1 tháng này"],
                           ["TT-F-002", f(3), "1.350.000", "Hoa hồng đợt 2"],
                           ["TT-F-003", f(1), "2.800.000", "Hoa hồng livestream"]],
                          columns=["Mã đơn", "Ngày", "Hoa hồng", "Nội dung"])
    fl = pd.DataFrame([["PRJ-101", date.today() - pd.Timedelta(days=4), 6_000_000, "Dự án thiết kế banner"],
                       ["PRJ-102", date.today() - pd.Timedelta(days=2), 3_500_000, "Viết kịch bản video"]],
                      columns=["Mã dự án", "Ngày nghiệm thu", "Số tiền", "Nội dung"])
    xb = io.BytesIO()
    with pd.ExcelWriter(xb, engine="openpyxl") as xw:
        fl.to_excel(xw, sheet_name="Dự án", index=False)
    enc = lambda d: d.to_csv(index=False).encode("utf-8-sig")
    return {
        "tiktok_revenue.csv": (enc(tiktok), "text/csv"),
        "freelancer_projects.xlsx": (xb.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "bank_statement.csv": (enc(sample_bank_df()), "text/csv"),
    }
