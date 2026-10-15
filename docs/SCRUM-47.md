# SCRUM-47 — Phân công giảng dạy theo môn trong lớp

## Quy tắc

- Một môn trong lớp có một giảng viên chính và không hoặc nhiều trợ giảng. Môn chưa được phân công có thể tồn tại; khi lưu phân công phải chọn giảng viên chính.
- Giảng viên phải có vai trò `INSTRUCTOR`/`TEACHER`; trợ giảng phải có vai trò `ASSISTANT`/`TA` trong `user_roles`. Không tin vai trò trong JWT hay trường `users.role_id` cũ.
- Không cho chọn tài khoản bị khóa hoặc đang bị khóa tạm thời, tài khoản sai vai trò, hay cùng người làm giảng viên chính và trợ giảng của một môn.
- Quyền phân công: `CLASS_MANAGE` cùng một trong các vai trò `ADMIN`, `TRAINING_MANAGER`, `MANAGER`, `ACADEMIC_AFFAIRS`.
- Giảng viên/trợ giảng xem được các lớp có phân công theo môn của mình, không cần quyền quản lý lớp. Thay người phụ trách cập nhật ngay phạm vi lớp được xem ở lần tải tiếp theo.
- Trường giảng viên chung của lớp được giữ để tương thích dữ liệu cũ; quyền xem lớp dựa vào phân công theo môn.

## API

| API | Người được sử dụng |
| --- | --- |
| `GET /api/classes` | Quản lý xem các lớp; giảng viên/trợ giảng xem lớp được phân công |
| `GET /api/classes/candidates` | Người có quyền phân công |
| `GET /api/classes/{class_id}/assignments` | Người có quyền phân công |
| `PUT /api/classes/{class_id}/subjects/{subject_id}/assignment` | Người có quyền phân công |
| `PUT /api/classes/{class_id}/assignments` | Người có quyền phân công |

Lưu từng môn: `{"instructor_id": 12, "assistant_ids": [24, 25]}`.

Lưu nhiều môn: `{"assignments": [{"subject_id": 3, "instructor_id": 12, "assistant_ids": [24, 25]}]}`.
Chỉ những môn trong payload được cập nhật. Toàn bộ payload được kiểm tra trước khi ghi; lỗi ở một môn không lưu dở các môn còn lại. Không chấp nhận môn trùng trong cùng payload. Lưu lại cùng dữ liệu không tạo bản ghi trợ giảng trùng.

## Cơ sở dữ liệu

Khởi động ứng dụng tự thêm `class_subjects.instructor_id` và bảng `class_subject_assistants`. Khi thêm cột mới, chuyển giảng viên cũ của lớp sang các môn tương ứng nếu có vai trò hợp lệ; chạy migration lại không ghi đè phân công.
Khởi tạo vai trò trợ giảng khi thiếu; không tạo tài khoản demo hay mật khẩu mặc định. Không khôi phục quyền đã bị quản trị viên thu hồi.

## Kiểm thử

```powershell
.venv/Scripts/python.exe -m pytest -q
```

`tests/conftest.py` dùng SQLite tạm, không dùng database trong `.env`. Các trường hợp riêng của Scrum 47 nằm trong `tests/test_class_assignments.py`; kiểm thử lớp/dữ liệu mẫu ở `tests/test_demo_data_and_classes.py`.
