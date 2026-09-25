import os
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _load_local_env():
    """Load simple KEY=value settings without requiring another dependency."""
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_local_env()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "development-only-secret")
    DATABASE_PATH = os.getenv("DATABASE_PATH", str(ROOT / "data" / "vault.sqlite3"))
    STORAGE_ROOT = os.getenv("STORAGE_ROOT", str(ROOT / "storage"))
    NODE_COUNT = int(os.getenv("NODE_COUNT", "3"))
    DEFAULT_REPLICATION_FACTOR = int(os.getenv("DEFAULT_REPLICATION_FACTOR", "3"))
    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB limit for hackathon free tier
