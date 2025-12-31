import os

class Settings:
    TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./local.db")
    BASE_URL = os.getenv("BASE_URL", "")
    TZ = os.getenv("TZ", "Europe/Madrid")

    YEAR_FROM = "2025-01-01"
    YEAR_TO = "2025-12-31"
    ABIERTAS_FROM = "2025-10-01"

settings = Settings()
