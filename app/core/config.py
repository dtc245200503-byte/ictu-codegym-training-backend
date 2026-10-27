
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "ICTU-CodeGym Training Management API"
    APP_ENV: str = "development"
    DATABASE_URL: str

    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 30

    FRONTEND_URL: str = "http://localhost:5173"
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_PORT: int = 587
    MAIL_SERVER: str = "smtp.gmail.com"
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False

    ENABLE_TUITION_REMINDER_WORKER: bool = False
    TUITION_REMINDER_EMAIL_ENABLED: bool = False
    TUITION_REMINDER_MAX_ATTEMPTS: int = 3
    PRIVATE_FILE_STORAGE_DIR: str = "var/private-files"
    PRIVATE_FILE_MAX_BYTES: int = 20 * 1024 * 1024
    NOTIFICATION_EMAIL_ENABLED: bool = True
    CLASS_REMINDER_LEAD_HOURS: int = 12
    ENABLE_NOTIFICATION_WORKER: bool = False
    LEAD_SUBMISSION_LIMIT: int = 5
    LEAD_SUBMISSION_WINDOW_SECONDS: int = 3600

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),  # Đọc file .env ở thư mục hiện tại hoặc thư mục cha
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
