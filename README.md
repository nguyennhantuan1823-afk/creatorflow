# CreatorFlow – Hệ thống quản lý và đối soát vòng đời thu nhập đa nền tảng

Web app Fintech (Python + Streamlit + SQLite + Pandas + Plotly + FastAPI + Requests) theo đúng đề cương trong file `Promt_fintech.docx`.

## 1. Cài đặt và chạy (làm một lần)

1. Cài **Python 3.10+** từ python.org (Windows: tick ô *Add Python to PATH* khi cài).
2. Giải nén thư mục `creatorflow`, mở terminal (hoặc VS Code → Terminal) **trong thư mục đó**.
3. Cài thư viện:
   ```
   pip install -r requirements.txt
   ```
4. Chạy:
   ```
   streamlit run app.py
   ```
   Trình duyệt tự mở `http://localhost:8501`. (Windows: có thể bấm đúp `run.bat`.)

API giả lập (FastAPI) **tự khởi động cùng app** ở cổng 8008, không cần chạy riêng.
Xem tài liệu API tại `http://127.0.0.1:8008/docs` khi app đang chạy.

## 2. Cấu trúc thư mục

| File | Vai trò |
|---|---|
| `app.py` | Lớp hiển thị: 6 trang Streamlit |
| `services.py` | Lớp nghiệp vụ: chuẩn hóa dữ liệu, đọc file, đối soát, số liệu |
| `db.py` | Lớp dữ liệu: SQLite, vòng đời trạng thái, lịch sử, dữ liệu mẫu |
| `mock_api.py` | API giả lập YouTube / TikTok / Facebook / Freelancer (FastAPI) |
| `sample_data/` | File mẫu: doanh thu TikTok (CSV), dự án freelancer (Excel), sao kê ngân hàng (CSV) |
| `creatorflow.db` | CSDL SQLite, tự tạo lần chạy đầu |

## 3. Kịch bản demo trước giảng viên (khoảng 5–7 phút)

Bấm **⚙️ Dữ liệu → Xóa toàn bộ dữ liệu** để bắt đầu từ trống, hoặc dùng dữ liệu mẫu có sẵn.

1. **Nguồn thu** → thêm nguồn mới (vd. *Shopee Affiliate*), chỉnh số ngày thanh toán, thử sửa và xóa.
2. **Quản lý khoản thu → Nhập dữ liệu**: gọi API YouTube (xem dữ liệu thô rồi dữ liệu đã chuẩn hóa), nhập `sample_data/tiktok_revenue.csv`, nhập `sample_data/freelancer_projects.xlsx`.
3. **Thêm khoản thu** bằng tay (giảng viên tự nhập số liệu bất kỳ).
4. **Chi tiết & trạng thái**: chuyển khoản thu qua Chờ xác nhận → Được duyệt → Chờ thanh toán → Đã thanh toán/chờ đối soát; xem **lịch sử trạng thái**.
5. **Theo dõi thanh toán**: xem khoản chờ, lịch dự kiến, khoản trễ hạn (tự phát hiện).
6. **Đối soát** → tải `sample_data/bank_statement.csv` (hoặc nhập tay giao dịch) → *Chạy đối soát tự động*:
   - khớp duy nhất, đúng tiền → tự xác nhận;
   - nhận thiếu do phí / thanh toán một phần / nhiều ứng viên → vào mục **Cần kiểm tra** để người dùng quyết định (Nhận đủ / Trừ phí / Một phần / Bỏ qua);
   - có thể **Hủy ghép** nếu ghép nhầm.
7. **Dashboard**: tiền thực nhận tăng, khoản chờ giảm. **Báo cáo**: biểu đồ + xuất Excel/CSV.

## 4. Nguyên tắc nghiệp vụ đã cài đặt

- Doanh thu phát sinh / được duyệt / chờ thanh toán / thực nhận là **các lát cắt khác nhau**, không cộng dồn (Dashboard có ghi chú).
- Mỗi khoản thu có **lịch sử trạng thái**, không ghi đè.
- Trạng thái *Đã nhận & đối soát* và *Nhận một phần* chỉ được tạo từ bước **đối soát sao kê**, để tiền thực nhận luôn có giao dịch ngân hàng làm bằng chứng.
- Nhập trùng mã ngoài (vd. gọi API hai lần) được bỏ qua tự động.

## 5. Đưa lên mạng (nếu cần nộp link)

Đẩy thư mục lên GitHub, rồi vào **share.streamlit.io** (Streamlit Community Cloud, miễn phí) → New app → chọn repo, file `app.py`. Lưu ý: dữ liệu SQLite trên dịch vụ này có thể bị reset khi app khởi động lại; muốn demo ổn định nhất hãy chạy trên máy cá nhân.

## 6. Định hướng phát triển (không thuộc bản demo)

Kết nối API nền tảng thật, kết nối ngân hàng thật (Open Banking), đăng nhập nhiều người dùng.
