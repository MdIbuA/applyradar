"""All settings, read once from the environment (.env is loaded if present)."""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


DB_PATH = Path(os.getenv("APPLYRADAR_DB", ROOT / "applyradar.db"))
REPORTS_DIR = Path(os.getenv("REPORTS_DIR", ROOT / "reports"))
LOG_DIR = Path(os.getenv("LOG_DIR", ROOT / "logs"))
TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata")

# Auth: tokens are signed with this secret. Must be long and random outside tests.
JWT_SECRET = os.getenv("JWT_SECRET", "")
JWT_TTL_HOURS = int(os.getenv("JWT_TTL_HOURS", "24"))

# Daily digest job: cron fields "minute hour", in APP_TIMEZONE.
DIGEST_CRON = os.getenv("DIGEST_CRON", "30 7")
SCHEDULER_ENABLED = _flag("SCHEDULER_ENABLED", "true")

# LLM (any OpenAI-compatible API; Ollama by default).
LLM_ENABLED = _flag("LLM_ENABLED", "true")
LLM_STUB = _flag("LLM_STUB", "0")
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
