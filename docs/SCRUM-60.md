# Scrum 60 — Xuất bảng chuyên cần Excel

Trong **Bảng chuyên cần**, Quản lý đào tạo chọn lớp và cách sắp xếp rồi bấm **Xuất Excel**. Tệp `.xlsx` chứa toàn bộ học viên của lớp, kể cả các trang chưa mở; thứ tự học viên và các buổi giống bảng chuyên cần.

- Giữ các cột **Học viên → Tổng vắng → Chuyên cần → các buổi**. Ô học viên chứa tên, mã và trạng thái tham gia lớp.
- Trạng thái được tô màu và có ký hiệu; có trang chú giải. Nhận xét trên ô tổng chứa số vắng có phép/không phép và số buổi tham dự/có kết quả.
- Tổng vắng là số; chuyên cần là số có định dạng phần trăm. Chưa có kết quả hiển thị `—`. Dùng chung cách tính với Scrum 57, không tự tính theo trang đang xem.
- Cố định hàng tiêu đề và ba cột đầu; có bộ lọc và cấu hình in ngang.
- Tên tệp: `Chuyen-can_<mã lớp>_<YYYY-MM-DD>.xlsx`, ngày theo giờ Việt Nam. Mã lớp cũ chưa có dùng `LOP-<id>`; ký tự không hợp lệ trong tên tệp được thay bằng `_`.

API: `GET /api/attendance-summary/classes/{class_id}/export?sort=name|rate_asc`.
Yêu cầu quyền `ATTENDANCE_VIEW` và vai trò `TRAINING_MANAGER` hoặc `MANAGER` hiện còn hiệu lực. UI nhận `can_export` từ API; backend kiểm tra lại mỗi lần tải. Không thay đổi dữ liệu điểm danh khi xuất.

Nội dung từ người dùng được ghi dưới dạng chuỗi, không thực thi như công thức Excel. Tệp trả về có `Content-Disposition` UTF-8 và `Cache-Control: no-store`. CORS cho phép frontend đọc tên tệp; xử lý refresh token và lỗi tải giống bảng hiện tại.

Kiểm tra: `python -m pytest tests/test_attendance_export.py tests/test_attendance_summary.py -q`; frontend `node --test tests/attendance-summary.test.cjs tests/attendance-export-service.test.cjs`, `npm run build`.
