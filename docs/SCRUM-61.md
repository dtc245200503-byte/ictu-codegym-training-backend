# Scrum 61 — Soạn và giao bài tập cho lớp

## Sử dụng

Giảng viên vào **Bài tập → Tạo bài tập**, chọn lớp/môn/buổi trong các buổi được phân công; nhập tiêu đề, mô tả, hạn nộp (giờ Việt Nam) và điểm tối đa. **Lưu nháp** để lưu riêng tư. Sau khi lưu có thể thêm/xoá tệp rồi **Giao cho lớp**. Học viên nhận thông báo trong ứng dụng, mở liên kết **Xem bài tập** hoặc menu **Bài tập** để đọc đề và tải tài liệu.

- Mô tả hỗ trợ Markdown qua thanh công cụ và xem trước: đậm, nghiêng, tiêu đề, danh sách, mã nội dòng, liên kết HTTP(S). Raw HTML không được thực thi; không có trình soạn HTML tuỳ ý.
- Tiêu đề tối đa 200 ký tự; mô tả tối đa 20.000; điểm tối đa lớn hơn 0 đến 10.000, tối đa 2 chữ số thập phân.
- Hạn nộp bắt buộc kèm múi giờ trên API, lưu UTC. Kiểm tra lại khi lưu/sửa và khi giao; bản nháp để lâu qua hạn không thể giao.
- Tối đa 5 tệp, mỗi tệp 10 MB, tổng 25 MB. Tệp có thể tải sau khi lưu nháp; học viên chỉ tải được sau khi nhận bài. Nội dung lưu trong DB (`MEDIUMBLOB` trên MySQL), tải dưới dạng attachment với `nosniff`, không phục vụ nội tuyến.
- Lớp/môn/buổi được cố định sau khi tạo nháp. Bài đã giao là chỉ đọc trong phạm vi Scrum này; chưa có nộp bài/chấm điểm.

## Quyền và người nhận

Khởi động lại backend để tạo bảng và seed `ASSIGNMENT_VIEW` cho INSTRUCTOR/TEACHER/STUDENT; `ASSIGNMENT_MANAGE` cho INSTRUCTOR/TEACHER. Seed chỉ tạo quyền chưa có, không khôi phục quyền đã thu hồi. Đăng nhập lại để menu cập nhật quyền.

Tác giả phải có cả hai quyền, vai trò giảng viên, và đang được phân công buổi đã chọn (giảng viên dạy thay có hiệu lực thay giảng viên chính tại buổi đó). Giảng viên khác không xem được nháp. Không tạo/giao bài ở buổi huỷ hoặc lớp đã kết thúc/huỷ.

Khi giao, chốt học viên có vai trò STUDENT còn thuộc lớp tại ngày giao theo giờ Việt Nam, gồm ghi danh cũ và khoảng ghi danh có hiệu lực. Loại người bảo lưu/thôi học/chuyển ra có hiệu lực và người chưa đến ngày vào lớp. Lớp trống bị chặn giao. Những người nhận giữ quyền đọc lịch sử bài sau khi rời lớp; học viên vào sau không tự được thêm vào lần giao cũ.

Chuyển trạng thái và tạo thông báo được commit cùng giao dịch. Khóa lớp/buổi, kiểm tra phiên bản và ràng buộc duy nhất `(assignment_id, student_id)` ngăn giao trùng. Gọi lại publish đã thành công không gửi lại. Khi có lỗi lưu thông báo, cả lần giao được rollback. Các thay đổi tệp cũng tăng phiên bản để không giao nhầm bản nháp cũ.

Thông báo dùng `assignment:<recipient_id>`, đánh dấu đọc chỉ cho đúng người nhận, kèm `target_url` từ ID bản ghi. Không gửi email hoặc Zalo.

## API và kiểm thử

`GET /api/assignments/options`, `GET/POST /api/assignments`, `GET/PUT /api/assignments/{id}`, `POST /{id}/publish`, `POST /{id}/attachments`, `GET/DELETE /{id}/attachments/{attachment_id}`.

Sửa/giao/xoá/tải lên cần `expected_version`. API upload nhận multipart; các API còn lại dùng JSON. Danh sách phân trang 20 bài.

Backend: `python -m pytest tests/test_assignments.py -q`. Bao phủ phân quyền, hết hạn, sai môn/buổi, học viên theo ngày hiệu lực, tệp, rollback, giao đồng thời và thông báo chỉ một lần. Kiểm thử DB dùng SQLite; chưa xác nhận trên máy chủ MySQL thực tế.

Frontend: `node --test tests/assignments.test.cjs tests/notifications.test.cjs`, `npm run build`; kiểm thử bằng React/jsdom. Bố cục co về một cột trên điện thoại. Nhánh xuất bản dựa trên Scrum 60 và chỉ thêm Scrum 61.
