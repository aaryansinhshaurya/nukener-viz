import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
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


settings = Settings()
