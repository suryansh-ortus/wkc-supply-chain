"""Environment settings for the SELLER service. No LLM, no search, no engines."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))
load_dotenv(APP_DIR / ".env")

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-seller")

# --- talking to the buyer service --------------------------------------------
BUYER_SERVICE_URL = os.getenv("BUYER_SERVICE_URL", "http://localhost:8001")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "dev-internal-secret")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")

# --- Sarvam AI (speech in any Indian language) -------------------------------
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")

# --- Demo scope --------------------------------------------------------------
# Which product lines this portal serves. Blank = every seller in the database.
PORTAL_PRODUCTS = [int(x) for x in os.getenv("PORTAL_PRODUCTS", "5005").replace(",", " ").split()]
