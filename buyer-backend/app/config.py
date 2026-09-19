"""Environment settings. Plain os.getenv, no framework."""

import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

# --- DeepInfra ---------------------------------------------------------------
DEEPINFRA_API_KEY = os.getenv("DEEPINFRA_API_KEY", "")
DEEPINFRA_BASE_URL = os.getenv("DEEPINFRA_BASE_URL", "https://api.deepinfra.com/v1/openai")
DEEPINFRA_MODEL = os.getenv("DEEPINFRA_MODEL", "meta-llama/Llama-3.3-70B-Instruct-Turbo")

# --- Bright Data SERP --------------------------------------------------------
BRIGHTDATA_API_KEY = os.getenv("BRIGHTDATA_API_KEY", "")
BRIGHTDATA_SERP_ZONE = os.getenv("BRIGHTDATA_SERP_ZONE", "")
SERP_CACHE_TTL_HOURS = int(os.getenv("SERP_CACHE_TTL_HOURS", "24"))

# --- Business ----------------------------------------------------------------
LOCATION = os.getenv("LOCATION", "Mumbai, India")
NUMBER_OF_STORES = int(os.getenv("NUMBER_OF_STORES", "1"))
MAX_NEGOTIATION_ROUNDS = int(os.getenv("MAX_NEGOTIATION_ROUNDS", "3"))
# from the original GlobalConfig - used by the velocity formula
# one store's capacity (the original 1000 was the whole 8-store chain)
TOTAL_INVENTORY_CAPACITY = int(os.getenv("TOTAL_INVENTORY_CAPACITY", "125"))
SAFETY_STOCK_FACTOR = float(os.getenv("SAFETY_STOCK_FACTOR", "1.5"))
SEASONALITY_YEARS = int(os.getenv("SEASONALITY_YEARS", "3"))

# Simulated "today" the engines run against (matches the seed anchor).
SIMULATED_TODAY = date.fromisoformat(os.getenv("SIMULATED_TODAY", "2025-07-15"))

# --- App ---------------------------------------------------------------------
JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-buyer")

# Nothing runs on a timer. Stock Watch only fires when the console button
# is pressed. There is no background loop any more.

# Hard ceiling on a single order, in rupees. An agent can never recommend
# more than this much stock at once, whatever its reasoning says.
MAX_ORDER_VALUE = int(os.getenv("MAX_ORDER_VALUE", "100000"))

# Optional filter on which products the engines analyse. Blank = all of them.
ONLY_PRODUCTS = [int(x) for x in os.getenv("ONLY_PRODUCTS", "").replace(",", " ").split()]

# --- Cognee Cloud (negotiation memory) ---------------------------------------
# Hosted: nothing runs locally. Blank = memory off.
COGNEE_API_URL = os.getenv("COGNEE_API_URL", "")
COGNEE_API_KEY = os.getenv("COGNEE_API_KEY", "")

# --- UPI payment (no gateway, just a link people scan) -----------------------
UPI_VPA = os.getenv("UPI_VPA", "suryanshmalhotra90@okhdfcbank")
UPI_PAYEE_NAME = os.getenv("UPI_PAYEE_NAME", "WKC Store")

# --- talking to the seller service -------------------------------------------
SELLER_SERVICE_URL = os.getenv("SELLER_SERVICE_URL", "http://localhost:8002")
INTERNAL_SECRET = os.getenv("INTERNAL_SECRET", "dev-internal-secret")
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "*").split(",")
