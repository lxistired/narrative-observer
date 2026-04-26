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

REDDIT_USER_AGENT = "narrative-observer/0.1 (personal use)"

DATA_DIR.mkdir(exist_ok=True)
SITE_DIR.mkdir(exist_ok=True)
