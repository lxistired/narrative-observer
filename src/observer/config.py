import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SITE_DIR = ROOT / "site"

XAI_API_KEY = os.getenv("XAI_API_KEY")
XAI_BASE_URL = "https://api.x.ai/v1"
XAI_MODEL = os.getenv("XAI_MODEL", "grok-4-1-fast-reasoning")


def require_xai_key() -> str:
    """Fail loudly with a clear message if XAI_API_KEY is missing.

    Call from CLI entry points so the error happens before any work is done.
    """
    if not XAI_API_KEY:
        raise SystemExit(
            "XAI_API_KEY is not set. Put it in .env (local) or in repo Secrets (CI)."
        )
    return XAI_API_KEY

REDDIT_USER_AGENT = "narrative-observer/0.1 (personal use)"

DATA_DIR.mkdir(exist_ok=True)
SITE_DIR.mkdir(exist_ok=True)
