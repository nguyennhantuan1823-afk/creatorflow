"""Lớp dữ liệu: SQLite + các hàm thao tác. Mọi trang trong app đều đọc/ghi qua file này."""
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "creatorflow.db"
SESSION_DIR = Path(tempfile.gettempdir()) / "creatorflow_sessions"

# Mỗi luồng (mỗi người truy cập) có thể trỏ tới CSDL riêng của mình
_PATH = ContextVar("creatorflow_db_path", default=None)


def current_path():
    return _PATH.get() or DB_PATH


def use_path(path):
    _PATH.set(Path(path))


def session_path(sid):
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    return SESSION_DIR / f"{sid}.db"


def cleanup_sessions(max_age_hours=72):
    """Xóa CSDL phiên không còn được dùng."""
    if not SESSION_DIR.exists():
        return
    limit = time.time() - max_age_hours * 3600
    for f in SESSION_DIR.glob("*.db"):
        try:
            if f.stat().st_mtime < limit:
                f.unlink()
        except OSError:
            pass

# ---------- Trạng thái vòng đời ----------
STATUS_LABEL = {
    "GENERATED": "Phát sinh doanh thu",
    "UNDER_REVIEW": "Chờ xác nhận / nghiệm thu",
    "APPROVED": "Được duyệt",
    "PENDING_PAYMENT": "Chờ thanh toán",
    "PAID_UNRECONCILED": "Đã thanh toán / chờ đối soát",
    "RECONCILED": "Đã nhận & đối soát",
    "PARTIALLY_PAID": "Nhận một phần",
    "OVERDUE": "Trễ hạn thanh toán",
    "DISPUTED": "Tranh chấp",
    "CANCELLED": "Đã hủy",
}
# Các bước chuyển hợp lệ (không phải khoản nào cũng đi qua mọi trạng thái)
TRANSITIONS = {
    "GENERATED": ["UNDER_REVIEW", "APPROVED", "CANCELLED"],
    "UNDER_REVIEW": ["APPROVED", "DISPUTED", "CANCELLED"],
    "APPROVED": ["PENDING_PAYMENT", "DISPUTED", "CANCELLED"],
    "PENDING_PAYMENT": ["PAID_UNRECONCILED", "PARTIALLY_PAID", "OVERDUE", "DISPUTED", "CANCELLED"],
    "OVERDUE": ["PAID_UNRECONCILED", "PARTIALLY_PAID", "DISPUTED", "CANCELLED"],
    "PARTIALLY_PAID": ["PAID_UNRECONCILED", "RECONCILED", "OVERDUE", "DISPUTED", "CANCELLED"],
    "PAID_UNRECONCILED": ["RECONCILED", "PARTIALLY_PAID", "DISPUTED"],
    "DISPUTED": ["UNDER_REVIEW", "APPROVED", "CANCELLED"],
    "RECONCILED": [],
    "CANCELLED": [],
}
APPROVED_SET = ["APPROVED", "PENDING_PAYMENT", "PAID_UNRECONCILED", "RECONCILED", "PARTIALLY_PAID", "OVERDUE"]
WAITING_PAY_SET = ["APPROVED", "PENDING_PAYMENT", "OVERDUE", "PARTIALLY_PAID"]
AWAITING_MONEY_SET = ["PENDING_PAYMENT", "OVERDUE", "PARTIALLY_PAID", "PAID_UNRECONCILED", "APPROVED"]

SOURCE_TYPES = ["Content Creator", "Affiliate Marketer", "Freelancer", "Hợp đồng quảng cáo", "Khác"]


@contextmanager
def conn():
    """Mở kết nối, tự commit khi xong và LUÔN đóng (quan trọng trên Windows)."""
    c = sqlite3.connect(current_path())
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sources(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL DEFAULT 'Content Creator',
    pay_days INTEGER NOT NULL DEFAULT 14,      -- số ngày từ lúc duyệt đến hạn thanh toán
    pay_rule TEXT DEFAULT '',                   -- điều kiện nhận tiền
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS incomes(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    title TEXT DEFAULT '',
    amount REAL NOT NULL,
    received_amount REAL NOT NULL DEFAULT 0,    -- tiền thực nhận đã đối soát với sao kê
    generated_date TEXT NOT NULL,
    expected_pay_date TEXT,
    paid_date TEXT,
    status TEXT NOT NULL DEFAULT 'GENERATED',
    origin TEXT NOT NULL DEFAULT 'Thủ công',    -- Thủ công / API giả lập / File
    external_id TEXT,
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_history(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    income_id INTEGER NOT NULL REFERENCES incomes(id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    note TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bank_txns(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date TEXT NOT NULL,
    amount REAL NOT NULL,
    description TEXT DEFAULT '',
    match_status TEXT NOT NULL DEFAULT 'NEW',   -- NEW / MATCHED / REVIEW / UNMATCHED / IGNORED
    income_id INTEGER REFERENCES incomes(id) ON DELETE SET NULL,
    suggestion TEXT DEFAULT '',
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recon_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    txn_id INTEGER,
    income_code TEXT,
    action TEXT NOT NULL,
    amount REAL,
    diff REAL,
    note TEXT DEFAULT ''
);
"""


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today():
    return date.today().isoformat()


def init_db():
    with conn() as c:
        c.executescript(SCHEMA)
        n = c.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    if n == 0:
        seed_sample()


def reset_all(with_sample=True):
    with conn() as c:
        c.executescript("DROP TABLE IF EXISTS recon_log; DROP TABLE IF EXISTS bank_txns; "
                        "DROP TABLE IF EXISTS status_history; DROP TABLE IF EXISTS incomes; DROP TABLE IF EXISTS sources;")
    init_db_empty()
    if with_sample:
        seed_sample()


def init_db_empty():
    with conn() as c:
        c.executescript(SCHEMA)


# ---------- Nguồn thu ----------
def list_sources():
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM sources ORDER BY name")]


def source_by_name(name):
    with conn() as c:
        r = c.execute("SELECT * FROM sources WHERE lower(name)=lower(?)", (name,)).fetchone()
        return dict(r) if r else None


def add_source(name, source_type, pay_days, pay_rule=""):
    name = name.strip()
    if not name:
        raise ValueError("Tên nguồn thu không được để trống.")
    with conn() as c:
        try:
            cur = c.execute(
                "INSERT INTO sources(name,source_type,pay_days,pay_rule,created_at) VALUES(?,?,?,?,?)",
                (name, source_type, int(pay_days), pay_rule, now()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Nguồn thu '{name}' đã tồn tại.")
        return cur.lastrowid


def update_source(sid, name, source_type, pay_days, pay_rule):
    name = name.strip()
    if not name:
        raise ValueError("Tên nguồn thu không được để trống.")
    with conn() as c:
        try:
            c.execute(
                "UPDATE sources SET name=?, source_type=?, pay_days=?, pay_rule=? WHERE id=?",
                (name, source_type, int(pay_days), pay_rule, sid),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Tên '{name}' đã được dùng cho nguồn khác.")


def count_incomes_of_source(sid):
    with conn() as c:
        return c.execute("SELECT COUNT(*) FROM incomes WHERE source_id=?", (sid,)).fetchone()[0]


def delete_source(sid, cascade=False):
    n = count_incomes_of_source(sid)
    if n and not cascade:
        raise ValueError(f"Nguồn này còn {n} khoản thu. Hãy tick 'xóa cả các khoản thu' nếu muốn xóa hết.")
    with conn() as c:
        c.execute("DELETE FROM incomes WHERE source_id=?", (sid,))
        c.execute("DELETE FROM sources WHERE id=?", (sid,))


# ---------- Khoản thu ----------
def next_code(source_name):
    prefix = "".join(ch for ch in source_name.upper() if ch.isalpha())[:2] or "TH"
    with conn() as c:
        rows = c.execute("SELECT code FROM incomes WHERE code LIKE ?", (prefix + "%",)).fetchall()
    nums = []
    for r in rows:
        tail = r["code"][len(prefix):]
        if tail.isdigit():
            nums.append(int(tail))
    return f"{prefix}{(max(nums) + 1) if nums else 1:03d}"


def add_income(source_id, amount, generated_date, title="", status="GENERATED", code=None,
               expected_pay_date=None, origin="Thủ công", external_id=None, note="", history_note="Tạo khoản thu"):
    if amount is None or float(amount) <= 0:
        raise ValueError("Số tiền phải lớn hơn 0.")
    with conn() as c:
        s = c.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
    if not s:
        raise ValueError("Chưa chọn nguồn thu.")
    code = (code or "").strip() or next_code(s["name"])
    gd = str(generated_date)
    if not expected_pay_date:
        expected_pay_date = (date.fromisoformat(gd) + timedelta(days=int(s["pay_days"]))).isoformat()
    with conn() as c:
        try:
            cur = c.execute(
                """INSERT INTO incomes(code,source_id,title,amount,generated_date,expected_pay_date,status,
                   origin,external_id,note,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (code, source_id, title, float(amount), gd, str(expected_pay_date), status, origin,
                 external_id, note, now()),
            )
        except sqlite3.IntegrityError:
            raise ValueError(f"Mã khoản thu '{code}' đã tồn tại.")
        iid = cur.lastrowid
        c.execute(
            "INSERT INTO status_history(income_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)",
            (iid, None, status, now(), history_note),
        )
    return iid


def get_income(iid):
    with conn() as c:
        r = c.execute(
            "SELECT i.*, s.name AS source_name FROM incomes i JOIN sources s ON s.id=i.source_id WHERE i.id=?",
            (iid,),
        ).fetchone()
        return dict(r) if r else None


def update_income(iid, source_id, title, amount, generated_date, expected_pay_date, note):
    if float(amount) <= 0:
        raise ValueError("Số tiền phải lớn hơn 0.")
    with conn() as c:
        c.execute(
            """UPDATE incomes SET source_id=?, title=?, amount=?, generated_date=?, expected_pay_date=?, note=?
               WHERE id=?""",
            (source_id, title, float(amount), str(generated_date), str(expected_pay_date), note, iid),
        )


def delete_income(iid):
    with conn() as c:
        c.execute("DELETE FROM incomes WHERE id=?", (iid,))


def external_exists(source_id, external_id):
    with conn() as c:
        return c.execute(
            "SELECT 1 FROM incomes WHERE source_id=? AND external_id=?", (source_id, external_id)
        ).fetchone() is not None


def change_status(iid, new_status, note=""):
    inc = get_income(iid)
    if not inc:
        raise ValueError("Không tìm thấy khoản thu.")
    old = inc["status"]
    if new_status == old:
        return
    if new_status not in TRANSITIONS.get(old, []):
        raise ValueError(f"Không thể chuyển từ '{STATUS_LABEL[old]}' sang '{STATUS_LABEL[new_status]}'.")
    with conn() as c:
        s = c.execute("SELECT pay_days FROM sources WHERE id=?", (inc["source_id"],)).fetchone()
        extra = ""
        params = [new_status]
        if new_status == "APPROVED":
            extra = ", expected_pay_date=?"
            params.append((date.today() + timedelta(days=int(s["pay_days"]))).isoformat())
        params.append(iid)
        c.execute(f"UPDATE incomes SET status=?{extra} WHERE id=?", params)
        c.execute(
            "INSERT INTO status_history(income_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)",
            (iid, old, new_status, now(), note),
        )


def refresh_overdue():
    """Tự đánh dấu khoản 'Chờ thanh toán' đã quá hạn dự kiến thành OVERDUE. Trả về số khoản đổi."""
    with conn() as c:
        rows = c.execute(
            "SELECT id FROM incomes WHERE status='PENDING_PAYMENT' AND expected_pay_date < ?", (today(),)
        ).fetchall()
    for r in rows:
        change_status(r["id"], "OVERDUE", "Hệ thống tự động: quá ngày thanh toán dự kiến")
    return len(rows)


def history_of(iid):
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM status_history WHERE income_id=? ORDER BY id", (iid,))]


# ---------- Sao kê ----------
def add_bank_txn(txn_date, amount, description):
    if float(amount) <= 0:
        raise ValueError("Số tiền giao dịch tiền vào phải lớn hơn 0.")
    with conn() as c:
        c.execute(
            "INSERT INTO bank_txns(txn_date,amount,description,match_status,imported_at) VALUES(?,?,?,?,?)",
            (str(txn_date), float(amount), description or "", "NEW", now()),
        )


def delete_bank_txn(tid):
    unmatch(tid, note="Xóa giao dịch", silent=True)
    with conn() as c:
        c.execute("DELETE FROM bank_txns WHERE id=?", (tid,))


def log_recon(txn_id, income_code, action, amount=None, diff=None, note=""):
    with conn() as c:
        c.execute(
            "INSERT INTO recon_log(ts,txn_id,income_code,action,amount,diff,note) VALUES(?,?,?,?,?,?,?)",
            (now(), txn_id, income_code, action, amount, diff, note),
        )


def apply_match(txn_id, income_id, mode="full", note=""):
    """Ghép giao dịch với khoản thu.
    mode: full   = nhận đủ;
          fee    = nhận thiếu do phí -> chấp nhận chênh lệch, coi là đã đối soát;
          partial= thanh toán một phần, vẫn chờ phần còn lại."""
    with conn() as c:
        t = c.execute("SELECT * FROM bank_txns WHERE id=?", (txn_id,)).fetchone()
    inc = get_income(income_id)
    if not t or not inc:
        raise ValueError("Không tìm thấy giao dịch hoặc khoản thu.")
    new_received = float(inc["received_amount"]) + float(t["amount"])
    remaining = float(inc["amount"]) - new_received
    if mode == "partial" and remaining > 0:
        target = "PARTIALLY_PAID"
    elif mode == "fee":
        target = "RECONCILED"
    else:
        target = "RECONCILED" if remaining <= 0.5 else "PARTIALLY_PAID"
    with conn() as c:
        c.execute("UPDATE incomes SET received_amount=?, paid_date=? WHERE id=?",
                  (new_received, t["txn_date"], income_id))
        c.execute("UPDATE bank_txns SET match_status='MATCHED', income_id=?, suggestion=? WHERE id=?",
                  (income_id, note, txn_id))
    # Cho phép đi từ bất kỳ trạng thái 'đang chờ tiền' sang đích, ghi lịch sử
    cur = inc["status"]
    if cur != target:
        with conn() as c:
            c.execute("UPDATE incomes SET status=? WHERE id=?", (target, income_id))
            c.execute(
                "INSERT INTO status_history(income_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)",
                (income_id, cur, target, now(), f"Đối soát giao dịch #{txn_id} ({mode}). {note}"),
            )
    diff = float(inc["amount"]) - new_received if target == "RECONCILED" else remaining
    log_recon(txn_id, inc["code"], {"full": "Khớp đủ", "fee": "Khớp, chênh lệch phí", "partial": "Nhận một phần"}[mode],
              float(t["amount"]), diff, note)


def unmatch(txn_id, note="Hủy đối soát", silent=False):
    with conn() as c:
        t = c.execute("SELECT * FROM bank_txns WHERE id=?", (txn_id,)).fetchone()
    if not t or not t["income_id"] or t["match_status"] != "MATCHED":
        return
    inc = get_income(t["income_id"])
    if inc:
        new_received = max(0.0, float(inc["received_amount"]) - float(t["amount"]))
        with conn() as c:
            h = c.execute(
                "SELECT from_status FROM status_history WHERE income_id=? AND note LIKE ? ORDER BY id DESC LIMIT 1",
                (inc["id"], f"Đối soát giao dịch #{txn_id} %")).fetchone()
        before = h["from_status"] if h and h["from_status"] else "PAID_UNRECONCILED"
        new_status = "PARTIALLY_PAID" if new_received > 0 else before
        with conn() as c:
            c.execute("UPDATE incomes SET received_amount=?, status=?, paid_date=NULL WHERE id=?",
                      (new_received, new_status, inc["id"]))
            c.execute(
                "INSERT INTO status_history(income_id,from_status,to_status,changed_at,note) VALUES(?,?,?,?,?)",
                (inc["id"], inc["status"], new_status, now(), f"{note} (giao dịch #{txn_id})"),
            )
        if not silent:
            log_recon(txn_id, inc["code"], "Hủy đối soát", float(t["amount"]), None, note)
    with conn() as c:
        c.execute("UPDATE bank_txns SET match_status='NEW', income_id=NULL, suggestion='' WHERE id=?", (txn_id,))


def set_txn_status(txn_id, status, suggestion=""):
    with conn() as c:
        c.execute("UPDATE bank_txns SET match_status=?, suggestion=? WHERE id=?", (status, suggestion, txn_id))


# ---------- Dữ liệu mẫu ----------
def seed_sample():
    """Kịch bản trong đề: YouTube 8tr, TikTok Affiliate 4tr, Freelancer 6tr + vài khoản khác. Ngày tính theo hôm nay."""
    init_db_empty()
    d = lambda n: (date.today() - timedelta(days=n)).isoformat()
    src = {}
    for name, typ, days, rule in [
        ("YouTube", "Content Creator", 21, "Thanh toán sau khi đạt ngưỡng 100 USD, khoảng ngày 21 hằng tháng"),
        ("TikTok Affiliate", "Affiliate Marketer", 15, "Hoa hồng được duyệt sau khi hết thời gian hoàn/hủy đơn"),
        ("Facebook", "Content Creator", 10, "Thanh toán theo chu kỳ 2 lần/tháng"),
        ("Hợp đồng quảng cáo", "Hợp đồng quảng cáo", 30, "Thanh toán sau nghiệm thu, công nợ 30 ngày"),
        ("Freelancer", "Freelancer", 7, "Thanh toán theo từng mốc dự án"),
    ]:
        src[name] = add_source(name, typ, days, rule)
    # (nguồn, tiêu đề, số tiền, ngày phát sinh cách đây, trạng thái đích, ngày hạn cách đây(âm=tương lai))
    rows = [
        ("YouTube", "Doanh thu quảng cáo tháng trước", 8_000_000, 20, "PENDING_PAYMENT", -1, "YT001"),
        ("YouTube", "Doanh thu quảng cáo tháng trước nữa", 6_500_000, 50, "RECONCILED", 20, "YT002"),
        ("TikTok Affiliate", "Hoa hồng đợt 1", 4_000_000, 18, "UNDER_REVIEW", None, "TI001"),
        ("TikTok Affiliate", "Hoa hồng đợt trước", 3_200_000, 45, "OVERDUE", 5, "TI002"),
        ("Facebook", "Payout Facebook", 3_000_000, 40, "RECONCILED", 25, "FA001"),
        ("Hợp đồng quảng cáo", "Hợp đồng review sản phẩm", 6_000_000, 12, "UNDER_REVIEW", None, "HO001"),
        ("Freelancer", "Dự án thiết kế logo", 5_000_000, 30, "PARTIALLY_PAID", 3, "FR001"),
        ("Freelancer", "Dự án dựng video", 6_000_000, 8, "APPROVED", None, "FR002"),
    ]
    for sname, title, amt, ago, status, due_ago, code in rows:
        exp = d(due_ago) if due_ago is not None else None
        iid = add_income(src[sname], amt, d(ago), title, "GENERATED", code, exp, "Dữ liệu mẫu", None, "",
                         "Phát sinh doanh thu")
        path = {
            "UNDER_REVIEW": ["UNDER_REVIEW"],
            "APPROVED": ["UNDER_REVIEW", "APPROVED"],
            "PENDING_PAYMENT": ["UNDER_REVIEW", "APPROVED", "PENDING_PAYMENT"],
            "OVERDUE": ["APPROVED", "PENDING_PAYMENT", "OVERDUE"],
            "PARTIALLY_PAID": ["APPROVED", "PENDING_PAYMENT", "PARTIALLY_PAID"],
            "RECONCILED": ["APPROVED", "PENDING_PAYMENT", "PAID_UNRECONCILED", "RECONCILED"],
        }[status]
        for st in path:
            change_status(iid, st, "Dữ liệu mẫu")
        # change_status(APPROVED) ghi đè ngày hạn -> đặt lại theo kịch bản
        with conn() as c:
            if exp:
                c.execute("UPDATE incomes SET expected_pay_date=? WHERE id=?", (exp, iid))
            if status == "RECONCILED":
                c.execute("UPDATE incomes SET received_amount=amount, paid_date=? WHERE id=?",
                          (d(due_ago - 1 if due_ago else 0), iid))
            if status == "PARTIALLY_PAID":
                c.execute("UPDATE incomes SET received_amount=2000000, paid_date=? WHERE id=?", (d(2), iid))
    return True
