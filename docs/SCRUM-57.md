# Scrum 57 — Bảng chuyên cần của lớp

## Sử dụng

Mở **Bảng chuyên cần** trong menu, chọn lớp, chọn **Tỷ lệ chuyên cần tăng dần** hoặc bấm tiêu đề cột Chuyên cần. Ma trận hiển thị toàn bộ buổi của lớp, theo ngày và giờ; mỗi ô có màu và ký hiệu kèm tên trạng thái để không phụ thuộc vào màu sắc.

- Có mặt (CM): xanh lá; Đi muộn (M): vàng; Vắng có phép (CP): xanh lam; Vắng không phép (KP): đỏ.
- `?`: chưa điểm danh; `—`: không thuộc lớp vào ngày học; Sắp: buổi tương lai; Huỷ: buổi đã huỷ.
- Cột tổng vắng gồm cả CP và KP, kèm số lượng từng loại.
- Tỷ lệ = (Có mặt + Đi muộn) / số buổi có kết quả điểm danh của học viên × 100%, làm tròn hai chữ số thập phân. Bỏ qua buổi huỷ, tương lai, chưa điểm danh và khoảng thời gian ngoài lớp. Ngày hiện tại tính theo giờ Việt Nam.
- Chưa có kết quả: hiện `—`, không coi là 0%; xếp cuối khi sắp theo tỷ lệ. Sắp xếp trên toàn bộ học viên trước khi phân trang 50 người, dùng tỷ lệ chính xác trước khi làm tròn.
- Học viên bảo lưu/chuyển lớp/thôi học giữ lịch sử trước ngày hiệu lực; các bản ghi ngoài thời gian tham gia không được tính vào tỷ lệ. Tài khoản đã xoá vẫn hiện tên/mã đã lưu trong lịch sử.
- Cuộn ngang bảng trên điện thoại; cột học viên và hàng tiêu đề cố định. Vùng bảng nhận bàn phím, ô có nhãn đầy đủ.

## Quyền và API

Cần quyền `ATTENDANCE_VIEW` và vai trò INSTRUCTOR/TEACHER/TRAINING_MANAGER/MANAGER/ADMIN. Giảng viên chỉ xem lớp có môn được phân công hoặc có buổi dạy thay chưa huỷ; được đọc tổng hợp toàn bộ các môn của lớp đó. Quản lý và admin xem toàn bộ lớp. API kiểm tra lại quyền mỗi lần tải; học viên và trợ giảng không được dùng báo cáo này.

- `GET /api/attendance-summary/classes`
- `GET /api/attendance-summary/classes/{class_id}?sort=name|rate_asc&page=1&page_size=50`
- Giao diện `/attendance-summary`, hỗ trợ `?class_id=...`.

## Phạm vi nhánh GitHub

Nhánh `feature/scrum-57-attendance-summary` chỉ chứa báo cáo Scrum 57 và các model cần đọc dữ liệu: buổi học/điểm danh, thời gian tham gia lớp và phòng học (khoá ngoại của buổi). Không đưa các luồng chỉnh sửa điểm danh, ghi danh, lịch học, lead hay các Scrum khác đang chưa commit vào nhánh này. Báo cáo tương thích với dữ liệu các luồng đó khi chạy cùng database.

Khởi động lại backend để đăng ký API và bổ sung các bảng chưa tồn tại. `ensure_attendance_summary_schema` thêm trạng thái buổi và giảng viên dạy thay nếu database điểm danh cũ thiếu cột; có thể chạy lại, giữ dữ liệu cũ. Chỉ tạo quyền ATTENDANCE_VIEW khi chưa có; không khôi phục các quyền đã bị quản trị viên thu hồi.

## Kiểm thử

Backend: `python -m pytest tests/test_attendance_summary.py -q`. Frontend: `node --test tests/attendance-summary.test.cjs` và `npm run build`.

Kiểm tra công thức và mọi trạng thái, sắp xếp trước phân trang, quyền và thu hồi quyền, học viên chuyển/bảo lưu/thôi học, đơn được duyệt, tài khoản lịch sử, phản hồi cũ, lỗi tải và 60 buổi không gây truy vấn theo từng ô. Kiểm thử sử dụng SQLite và React/jsdom; chưa xác nhận trên MySQL thực tế.
