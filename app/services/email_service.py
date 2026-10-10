
from fastapi_mail import ConnectionConfig, FastMail, MessageSchema, MessageType

from app.core.config import settings


async def send_password_reset_email(email: str, reset_link: str) -> None:
    """Send a password reset link using the configured SMTP server."""
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
        subject="Đặt lại mật khẩu tài khoản",
        recipients=[email],
        body=(
            "Bạn vừa yêu cầu đặt lại mật khẩu.\n\n"
            f"Mở liên kết sau để tiếp tục: {reset_link}\n\n"
            "Liên kết có hiệu lực trong 30 phút. "
            "Nếu bạn không thực hiện yêu cầu này, hãy bỏ qua email."
        ),
        subtype=MessageType.plain,
    )

    mailer = FastMail(config)
    await mailer.send_message(message)
