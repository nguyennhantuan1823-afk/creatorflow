"""API giả lập các nền tảng (FastAPI). Mỗi nền tảng trả về tên trường & đơn vị KHÁC NHAU
để chứng minh bước chuẩn hóa dữ liệu của CreatorFlow.

Chạy riêng:  uvicorn mock_api:app --port 8008
(App Streamlit cũng tự khởi động API này nên thường không cần chạy tay.)"""
import random
from datetime import date, timedelta

from fastapi import FastAPI

app = FastAPI(title="CreatorFlow - API nền tảng giả lập")


def _d(n):
    return (date.today() - timedelta(days=n)).isoformat()


def _rand(prefix, n, lo, hi, step=100_000):
    rnd = random.Random()
    out = []
    for _ in range(n):
        out.append((f"{prefix}-{rnd.randint(10000, 99999)}", rnd.randrange(lo, hi, step), _d(rnd.randint(1, 20))))
    return out


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/youtube/revenue")
def youtube(random_data: bool = False, n: int = 2):
    if random_data:
        rows = _rand("YT-API", n, 1_000_000, 9_000_000)
        return {"channel": "Kênh demo", "items": [
            {"report_id": r[0], "estimated_revenue": r[1], "currency": "VND", "report_date": r[2]} for r in rows]}
    return {"channel": "Kênh demo", "items": [
        {"report_id": "YT-API-0001", "estimated_revenue": 8_000_000, "currency": "VND", "report_date": _d(5)},
        {"report_id": "YT-API-0002", "estimated_revenue": 2_500_000, "currency": "VND", "report_date": _d(3)},
    ]}


@app.get("/tiktok/affiliate")
def tiktok(random_data: bool = False, n: int = 2):
    if random_data:
        rows = _rand("TT-API", n, 500_000, 5_000_000)
        return {"data": [{"order_batch": r[0], "commission": r[1], "created": r[2]} for r in rows]}
    return {"data": [
        {"order_batch": "TT-API-0001", "commission": 4_000_000, "created": _d(6)},
        {"order_batch": "TT-API-0002", "commission": 1_200_000, "created": _d(2)},
    ]}


@app.get("/facebook/payouts")
def facebook(random_data: bool = False, n: int = 2):
    # Facebook trả về đơn vị NGHÌN đồng
    if random_data:
        rows = _rand("FB-API", n, 1_000_000, 6_000_000)
        return {"payouts": [{"payout_id": r[0], "payout_amount_k": r[1] // 1000, "paid_on": r[2]} for r in rows]}
    return {"payouts": [{"payout_id": "FB-API-0001", "payout_amount_k": 3_000, "paid_on": _d(4)}]}


@app.get("/freelancer/projects")
def freelancer(random_data: bool = False, n: int = 2):
    if random_data:
        rows = _rand("FL-API", n, 2_000_000, 8_000_000, 500_000)
        return {"projects": [{"project_id": r[0], "project_payment": r[1], "finished_at": r[2]} for r in rows]}
    return {"projects": [{"project_id": "FL-API-0001", "project_payment": 6_000_000, "finished_at": _d(3)}]}
