# SCRUM-41 — Nhật ký chăm sóc và nhắc gọi lại

Lead có một lịch gọi lại hiện hành (`next_callback_at`, UTC, có thể để trống) và nhiều bản ghi tương tác. Mỗi bản ghi lưu loại `call` / `message` / `meeting`, nội dung, thời điểm tương tác, thời điểm ghi nhận và người thực hiện. Tên người thực hiện được lưu cùng nhật ký để giữ lịch sử khi tài khoản thay đổi.

## API

Các API dùng quyền `LEAD_MANAGE`. Tư vấn viên chỉ truy cập lead được giao; quản trị viên và quản lý đào tạo xem toàn bộ. Lead đã xóa hoặc ngoài phạm vi trả về 404. Việc chuyển giao giữ nguyên nhật ký và lịch nhắc.

- `GET /api/leads/{id}/care-logs?page=1&page_size=20`: lịch sử sắp xếp theo thời điểm tương tác mới nhất, rồi mã nhật ký; trả cả lịch nhắc hiện hành và cờ quá hạn. Tối đa 100 bản ghi mỗi trang.
- `POST /api/leads/{id}/care-logs`: thêm tương tác. Nội dung 1–5.000 ký tự; thời điểm có múi giờ, không ở tương lai. `callback_action` là `keep` (mặc định), `schedule` (bắt buộc có `next_callback_at` ở tương lai) hoặc `complete` (xóa lịch nhắc hiện hành). Nhật ký và lịch nhắc được lưu trong cùng giao dịch.
- `PUT /api/leads/{id}/callback`: đặt/đổi lịch bằng `{"next_callback_at":"2026-11-01T10:00:00+07:00"}`; gửi `null` để bỏ lịch. Không tạo tương tác giả khi chỉ đổi lịch.
- `GET /api/leads?callback_status=overdue|upcoming|none`: kết hợp với các bộ lọc lead hiện có. Quá hạn khi lịch nhắc **nhỏ hơn** thời điểm hiện tại. Cùng thời điểm vẫn thuộc `upcoming`.

Ví dụ ghi tương tác và hẹn tuần sau:

```json
{
  "interaction_type": "call",
  "content": "Khách đề nghị gọi lại tuần sau để chốt chương trình học.",
  "occurred_at": "2026-10-15T10:00:00+07:00",
  "callback_action": "schedule",
  "next_callback_at": "2026-10-22T10:00:00+07:00"
}
```

Thời điểm mới trả về có hậu tố `Z`. Cơ sở dữ liệu lưu UTC; giao diện nhập và hiển thị UTC+7.

## Triển khai và kiểm tra

Khởi động API chạy migration cộng thêm: tạo `lead_care_logs`, thêm cột nullable `consultation_leads.next_callback_at` và chỉ mục tra cứu. Migration hỗ trợ SQLite/MySQL, chạy lại an toàn và giữ dữ liệu cũ. Không cần cấp quyền mới.

Chạy `python -m pytest -q`. Các kiểm thử dùng SQLite tạm, không sử dụng dữ liệu ứng dụng đang chạy. `tests/test_lead_care.py` kiểm tra ba loại tương tác, validation, giao dịch thất bại, múi giờ, ranh giới quá hạn, đổi/hoàn tất lịch, phân quyền, chuyển giao, phân trang và migration.
