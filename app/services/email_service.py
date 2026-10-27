
import logging
from html import escape

from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.core.config import settings

logger = logging.getLogger(__name__)


async def send_password_reset_email(email: str, reset_link: str) -> None:
    """Send a password reset link using the configured SMTP server."""
    required_settings = (
        settings.MAIL_USERNAME,
        settings.MAIL_PASSWORD,
        settings.MAIL_FROM,
        settings.MAIL_SERVER,
    )

    if not all(value.strip() for value in required_settings):
        if settings.APP_ENV == "development":
            logger.warning(
                "Password reset email is not configured; reset link for %s: %s",
                email,
                reset_link,
            )
            return
        raise RuntimeError("SMTP is not configured for password reset emails.")

    config = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME,
        MAIL_PASSWORD=settings.MAIL_PASSWORD,
        MAIL_FROM=settings.MAIL_FROM,
        MAIL_PORT=settings.MAIL_PORT,
        MAIL_SERVER=settings.MAIL_SERVER,
        MAIL_STARTTLS=settings.MAIL_STARTTLS,
        MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )

    safe_reset_link = escape(reset_link, quote=True)
    message = MessageSchema(
        subject="[ICTU Academic Portal] Khôi phục mật khẩu tài khoản",
        recipients=[email],
        body=(
            "<!doctype html><html lang='vi'><body style='font-family:Arial,sans-serif;"
            "color:#28243a;line-height:1.6'><main style='max-width:560px;margin:32px auto;"
            "padding:28px;border:1px solid #eceaf3;border-radius:12px'>"
            "<h1 style='font-size:22px'>Khôi phục mật khẩu</h1>"
            "<p>Bạn vừa yêu cầu đặt lại mật khẩu tài khoản ICTU Academic Portal.</p>"
            "<p>Nhấn nút bên dưới để tạo mật khẩu mới. Liên kết chỉ có hiệu lực trong "
            f"{settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES} phút.</p>"
            f"<p style='margin:28px 0'><a href='{safe_reset_link}' "
            "style='display:inline-block;padding:12px 20px;border-radius:8px;"
            "background:#6553d9;color:white;text-decoration:none'>Đặt lại mật khẩu</a></p>"
            "<p>Nếu nút không hoạt động, sao chép liên kết sau vào trình duyệt:</p>"
            f"<p><a href='{safe_reset_link}'>{safe_reset_link}</a></p>"
            "<p>Nếu bạn không thực hiện yêu cầu này, hãy bỏ qua email.</p>"
            "<p>Trân trọng,<br>ICTU Academic Portal</p>"
            "</main></body></html>"
        ),
        subtype=MessageType.html,
    )

    mailer = FastMail(config)
    await mailer.send_message(message)


async def send_account_credentials_email(
    email: str,
    full_name: str,
    username: str,
    temporary_password: str,
) -> None:
    """Send initial account credentials using the configured SMTP server."""
    required_settings = (
        settings.MAIL_USERNAME,
        settings.MAIL_PASSWORD,
        settings.MAIL_FROM,
        settings.MAIL_SERVER,
    )
    if not all(value.strip() for value in required_settings):
        raise RuntimeError(
            "Email service is not configured. Check MAIL_* settings."
        )

    config = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME,
        MAIL_PASSWORD=settings.MAIL_PASSWORD,
        MAIL_FROM=settings.MAIL_FROM,
        MAIL_PORT=settings.MAIL_PORT,
        MAIL_SERVER=settings.MAIL_SERVER,
        MAIL_STARTTLS=settings.MAIL_STARTTLS,
        MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )
    message = MessageSchema(
        subject="Tài khoản hệ thống quản lý đào tạo",
        recipients=[email],
        body=(
            f"Xin chào {full_name},\n\n"
            "Tài khoản hệ thống của bạn đã được tạo.\n"
            f"Tên đăng nhập: {username}\n"
            f"Mật khẩu tạm thời: {temporary_password}\n\n"
            "Vui lòng đăng nhập và đổi mật khẩu ngay sau lần đăng nhập đầu tiên."
        ),
        subtype=MessageType.plain,
    )
    await FastMail(config).send_message(message)


async def send_tuition_due_reminder_email(
    email: str,
    *,
    student_name: str,
    class_code: str,
    installment_number: int,
    due_date: str,
    remaining_vnd: str,
    overdue: bool,
) -> None:
    """Send a tuition reminder; callers must explicitly enable this channel."""
    required_settings = (
        settings.MAIL_USERNAME,
        settings.MAIL_PASSWORD,
        settings.MAIL_FROM,
        settings.MAIL_SERVER,
    )
    if not all(value.strip() for value in required_settings):
        raise RuntimeError("SMTP is not configured for tuition reminders.")
    config = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME,
        MAIL_PASSWORD=settings.MAIL_PASSWORD,
        MAIL_FROM=settings.MAIL_FROM,
        MAIL_PORT=settings.MAIL_PORT,
        MAIL_SERVER=settings.MAIL_SERVER,
        MAIL_STARTTLS=settings.MAIL_STARTTLS,
        MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
        USE_CREDENTIALS=True,
        VALIDATE_CERTS=True,
    )
    safe_name = escape(student_name)
    safe_class = escape(class_code)
    subject = "Nhắc học phí quá hạn" if overdue else "Nhắc hạn đóng học phí"
    message = MessageSchema(
        subject=f"[ICTU Academic Portal] {subject}",
        recipients=[email],
        body=(
            f"Xin chào {safe_name},\n"
            f"Lớp {safe_class}, đợt học phí {installment_number} có hạn {due_date}.\n"
            f"Số dư cần thanh toán: {remaining_vnd} VND."
        ),
        subtype=MessageType.plain,
    )
    await FastMail(config).send_message(message)


async def send_notification_email(email: str, *, title: str, body: str) -> None:
    """Deliver one already-persisted outbox notification over configured SMTP."""
    required = (settings.MAIL_USERNAME, settings.MAIL_PASSWORD, settings.MAIL_FROM, settings.MAIL_SERVER)
    if not all(value.strip() for value in required):
        raise RuntimeError("SMTP is not configured for notifications.")
    config = ConnectionConfig(
        MAIL_USERNAME=settings.MAIL_USERNAME, MAIL_PASSWORD=settings.MAIL_PASSWORD,
        MAIL_FROM=settings.MAIL_FROM, MAIL_PORT=settings.MAIL_PORT,
        MAIL_SERVER=settings.MAIL_SERVER, MAIL_STARTTLS=settings.MAIL_STARTTLS,
        MAIL_SSL_TLS=settings.MAIL_SSL_TLS, USE_CREDENTIALS=True, VALIDATE_CERTS=True,
    )
    message = MessageSchema(
        subject=f"[ICTU Academic Portal] {escape(title)}",
        recipients=[email], body=escape(body), subtype=MessageType.plain,
    )
    await FastMail(config).send_message(message)
