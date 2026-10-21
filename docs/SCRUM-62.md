# Scrum 62 — Danh sách bài tập của học viên

## Phạm vi đã thống nhất

Học viên mở **Bài tập của tôi** để xem bài đã được giao, theo bốn nhóm **Chưa nộp / Đã nộp / Đã chấm / Cần làm lại**. Mỗi nhóm hiển thị tổng số bài; phân trang 20 bài, sắp xếp hạn nộp tăng dần trước khi phân trang (bài quá hạn trước), hoặc chọn hạn xa nhất. Các bài cùng hạn có thứ tự ổn định theo mã bài.

Scrum này chỉ làm danh sách và dữ liệu trạng thái, chưa xây dựng luồng nộp/chấm bài. Bài chưa có bản ghi kết quả thuộc **Chưa nộp**. Mở đề, tải tài liệu hoặc đọc thông báo không đổi trạng thái. Không có API cho học viên tự sửa trạng thái kết quả.

## Hạn nộp và quyền xem

- Bài chưa nộp hoặc cần làm lại còn trên 0 đến hết 24 giờ được đánh dấu **Sắp hết hạn**; đến hạn hoặc quá hạn có nhãn riêng. Bài đã nộp/đã chấm không mang cảnh báo cần nộp.
- Ngày giờ hiển thị theo Việt Nam. Đếm ngược lấy mốc giờ UTC từ máy chủ và thời gian đã trôi qua, tránh lệch đồng hồ thiết bị; cập nhật mỗi 15 giây. Tải lại dữ liệu mỗi phút khi trang hiển thị và khi quay lại cửa sổ; có nút làm mới.
- Chỉ vai trò STUDENT có `ASSIGNMENT_VIEW` và thuộc danh sách người nhận của bài đã giao mới xem được. Màn hình cá nhân của tài khoản kiêm giảng viên cũng chỉ xem các bài bản thân nhận được.
- Giữ quyền đọc bài đã nhận sau khi rời lớp theo quy tắc Scrum 61. Không tự thêm học viên mới vào bài đã giao trước đó.
- `/my-assignments` là trang cá nhân. Đường dẫn thông báo cũ `/assignments?assignment_id=...` vẫn mở đề cho học viên. Giảng viên giữ màn hình soạn/giao bài; tài khoản kiêm hai vai trò có cả hai mục menu.

## API và dữ liệu

- `GET /api/assignments/mine?status=not_submitted&sort=due_asc&page=1&page_size=20`
- `GET /api/assignments/mine/{id}` trả đề và tài liệu chỉ đọc; bài không được giao cho người gọi trả 404.
- `status`: `not_submitted`, `submitted`, `graded`, `needs_revision`. `sort`: `due_asc`, `due_desc`. Giới hạn trang 1–100 bài. Tổng đếm bốn nhóm luôn tính trên toàn bộ bài của học viên.
- Danh sách trả `now`, `urgent_window_hours`, `remaining_seconds` và `urgency` để đồng bộ hiển thị thời gian.

Bảng mới `assignment_progress` có khóa chính/ngoại `recipient_id`, một trạng thái hiện tại cho một người nhận. Không có bản ghi nghĩa là `not_submitted`. Luồng nộp/chấm trong Scrum sau cần cập nhật bảng này cùng giao dịch lưu bài nộp/kết quả:

| Sự kiện | status | submitted_at | reviewed_at |
| --- | --- | --- | --- |
| Nộp hoặc nộp lại | submitted | Thời điểm nộp mới nhất | NULL |
| Chấm hoàn tất | graded | Giữ thời điểm nộp | Thời điểm chấm |
| Yêu cầu làm lại | needs_revision | Giữ thời điểm nộp | Thời điểm xét |

Mọi lần ghi cần `updated_at`; thời gian lưu UTC như bảng bài tập. Ràng buộc DB kiểm tra trạng thái và sự hiện diện thời điểm xét. Bảng này không thay thế lịch sử các lần nộp/chấm của luồng sau.

Khởi động lại backend để cơ chế `Base.metadata.create_all` tạo bảng mới. Không cần chuyển đổi dữ liệu bài tập cũ hoặc cấp quyền mới. Nhánh dựa trên Scrum 61 đã xuất bản.

## Kiểm tra

- Backend: `python -m pytest -q`, gồm kiểm thử quyền người nhận, vai trò kiêm nhiệm, thu hồi quyền, lọc/sắp xếp toàn danh sách, phân trang, thời điểm đúng hạn, chuyển trạng thái dữ liệu và tuyến `/mine` trên ứng dụng thật.
- Frontend: `node --test tests/my-assignments.test.cjs tests/assignments.test.cjs tests/notifications.test.cjs`; `npm run build`. Kiểm tra bốn nhóm, đếm ngược theo giờ máy chủ, xử lý phản hồi cũ, lỗi đăng nhập, chi tiết chỉ đọc và dọn timer.
- Kiểm thử tự động dùng SQLite và React/jsdom; chưa kiểm tra trên MySQL hoặc trình duyệt thật. Lint phần Scrum 62 đạt; `App.tsx` có lỗi/warning hook đã tồn tại từ trước. Build vẫn có cảnh báo kích thước bundle trên 500 kB.
