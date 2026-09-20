import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/ner_platform",
    )
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-this-secret-key-in-production")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # A lock older than this is treated as abandoned (e.g. the holder's
    # tab crashed without calling /unlock) and can be taken by someone
    # else instead of blocking the document forever.
    LOCK_TIMEOUT_MINUTES: int = 30
    MAX_UPLOAD_BYTES: int = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/")
    FRONTEND_ORIGINS: list[str] = [origin.strip() for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
    APPS_SCRIPT_MAIL_URL: str = os.getenv("APPS_SCRIPT_MAIL_URL", "")
    APPS_SCRIPT_MAIL_SECRET: str = os.getenv("APPS_SCRIPT_MAIL_SECRET", "")
    RESEND_API_KEY: str = os.getenv("RESEND_API_KEY", "")
    EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "")
    SMTP_TLS: bool = os.getenv("SMTP_TLS", "true").lower() == "true"


settings = Settings()
if settings.ENVIRONMENT == "production" and settings.JWT_SECRET_KEY == "change-this-secret-key-in-production":
    raise RuntimeError("JWT_SECRET_KEY must be configured in production")
