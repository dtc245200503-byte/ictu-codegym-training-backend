# Scrum 58 — Cảnh báo vắng vượt ngưỡng

## Sử dụng

Trong **Chương trình đào tạo**, chọn **Ngưỡng vắng** ở dòng chương trình. Quản lý đào tạo có quyền `CURRICULUM_MANAGE` được đặt ngưỡng 0–100%, tối đa hai chữ số thập phân; mặc định 20%. Người có quyền xem chương trình nhưng không có vai trò Quản lý đào tạo chỉ đọc cấu hình.

Ngưỡng áp dụng cho các lớp của chương trình. Các lớp cũ chưa liên kết chương trình dùng mặc định 20%. Lưu ngưỡng sẽ tính lại cờ và thông báo trong cùng giao dịch; cập nhật từ biểu mẫu cũ bị chặn để không ghi đè cấu hình của người khác.

## Công thức đã thống nhất

`(Vắng có phép + Vắng không phép) × 100 > tổng buổi đã xếp lịch của lớp × ngưỡng (%)`.

- Mẫu số gồm toàn bộ buổi đã xếp lịch, kể cả buổi tương lai, bỏ buổi huỷ.
- Tử số chỉ gồm kết quả vắng của buổi đến ngày hiện tại (giờ Việt Nam), không tính buổi huỷ, chưa điểm danh hay thời gian học viên chưa vào/đã rời lớp. Đơn được duyệt cho buổi tương lai chỉ được tính khi đến ngày học.
- Dùng phép so sánh chính xác trước khi làm tròn để hiển thị. **Bằng ngưỡng chưa bật cờ**: lớp 10 buổi, ngưỡng 20%, cảnh báo từ buổi vắng thứ 3.
- Chỉ bật cờ cho học viên còn tham gia lớp đang chuẩn bị/đang học đã đến ngày khai giảng. Bảo lưu, chuyển ra, thôi học có hiệu lực hoặc lớp kết thúc/huỷ sẽ tắt cờ; giữ nguyên thông báo cũ để tra cứu.
- Tỷ lệ này khác tỷ lệ chuyên cần Scrum 57: Scrum 57 tính trên số buổi có kết quả điểm danh, còn Scrum 58 so sánh số vắng với tổng lịch lớp theo lựa chọn của người dùng.

## Hiển thị và thông báo

Cờ kèm tên/mã học viên, lớp, số vắng/tổng buổi, tỷ lệ và ngưỡng xuất hiện trên danh sách lớp, danh sách/hồ sơ học viên và bảng chuyên cần.

Khi cờ chuyển từ tắt sang bật, gửi thông báo trong hộp **Thông báo** cho các giảng viên được phân công môn hoặc buổi dạy thay chưa huỷ của lớp và những người có vai trò TRAINING_MANAGER/MANAGER. Không gửi cho học viên, trợ giảng hoặc giảng viên lớp khác. Mỗi người nhận một thông báo cho mỗi lần bật cờ dù có nhiều vai trò/phân công. Cờ còn bật không gửi lặp; khi đã tắt rồi vượt lại sẽ có một lần thông báo mới. Đánh dấu đã đọc kiểm tra đúng người nhận.

Sau khi lưu điểm danh hoặc cập nhật trạng thái tham gia lớp, hệ thống đồng bộ ngay. Đọc danh sách lớp, hồ sơ, bảng chuyên cần hoặc hộp thông báo cũng làm mới cờ. Worker chạy mỗi 60 giây khi backend hoạt động để xử lý thay đổi lịch, ngày hiệu lực, cấu hình và các lỗi đồng bộ tạm thời. Các thay đổi xảy ra rồi đảo lại hoàn toàn giữa hai lần đồng bộ được đánh giá theo dữ liệu mới nhất, không tạo cảnh báo cho trạng thái thoáng qua chưa quan sát được.

## Dữ liệu và giao dịch

Khởi động lại backend để tự tạo bốn bảng: `program_absence_policies`, `attendance_risks`, `attendance_risk_notifications`, `attendance_risk_sync`. Không cần sửa dữ liệu chương trình cũ; chưa có cấu hình nghĩa là 20%.

Khoá đồng bộ ở database đảm bảo nhiều worker/request không bật cờ và gửi trùng thông báo. Cờ và thông báo lưu trong cùng giao dịch. Đồng bộ bắt đầu ở giao dịch riêng sau khi thay đổi nghiệp vụ đã lưu; lỗi đồng bộ không báo sai rằng điểm danh chưa được lưu, mà ghi log và được thử lại ở lần đồng bộ tiếp theo.

API cấu hình: `GET/PUT /api/attendance-risks/programs/{course_id}/policy`. PUT nhận `threshold_percent` và `expected_threshold_percent`.

Kiểm thử: `python -m pytest tests/test_attendance_risk.py -q`, `node --test tests/attendance-risk.test.cjs`; có kiểm thử đồng thời, rollback, cấu hình cũ, quyền, hiệu lực theo ngày và các màn hình liên quan. Kiểm thử dùng SQLite và React/jsdom, chưa xác nhận trên MySQL thực tế.


## Phạm vi nhánh xuất bản

Nhánh `feature/SCRUM-58-attendance-risk` dựa trên Scrum 57. Kèm các phụ thuộc cần cho luồng cảnh báo: cập nhật điểm danh, hồ sơ và trạng thái tham gia lớp, dữ liệu phòng/lịch phục vụ nhập buổi, cùng hộp thông báo. Danh sách lớp chỉ bổ sung cờ; không đưa toàn bộ các Scrum khác đang làm trong workspace vào nhánh. Dữ liệu nguồn lead trên hồ sơ được đọc bằng bộ chuyển đổi riêng; không kèm luồng chuyển đổi lead hay gửi email.

Frontend có menu **Hồ sơ học viên**, **Thông báo** và trang **Điểm danh**. Những dữ liệu đơn xin phép hoặc thông báo đổi lịch đã có vẫn được đọc, nhưng nhánh này không xuất bản giao diện/luồng tạo các nghiệp vụ đó.
