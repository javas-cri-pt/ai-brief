"""Configurazione da variabili d'ambiente (.env in locale, Environment Variables su Vercel)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# Gateway LLM compatibile OpenAI.
# - produzione demo: OpenRouter (https://openrouter.ai/api/v1) con modelli free e fallback nativo
# - test locali: modello self-hosted (es. vLLM con Gemma) sullo stesso protocollo
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
# Elenco modelli separati da virgola, oppure "auto:free" = scoperta dinamica dei modelli free su OpenRouter
LLM_MODELS = [m.strip() for m in os.getenv("LLM_MODELS", "auto:free").split(",") if m.strip()]
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))
WRITE_TIMEOUT = float(os.getenv("WRITE_TIMEOUT", "200"))  # la redazione genera ~2.000 parole
STEP_BUDGET = float(os.getenv("STEP_BUDGET", "280"))      # sotto il maxDuration (300 s) della funzione Vercel
IS_OPENROUTER = "openrouter.ai" in LLM_BASE_URL

# Codici di accesso alla modalità live: "codice:tetto_giornaliero,codice2:tetto"
ACCESS_CODES = {}
for pair in os.getenv("ACCESS_CODES", "").split(","):
    if ":" in pair:
        code, cap = pair.split(":", 1)
        ACCESS_CODES[code.strip()] = int(cap)
GLOBAL_DAILY_CAP = int(os.getenv("GLOBAL_DAILY_CAP", "120"))
CALLS_PER_RUN = 9  # pianificazione + 3 lotti + raggruppamento + redazione + 3 varianti

# Storage: Upstash Redis (REST) se configurato, altrimenti SQLite locale
KV_URL = os.getenv("KV_REST_API_URL", "")
KV_TOKEN = os.getenv("KV_REST_API_TOKEN", "")
SQLITE_PATH = os.getenv("SQLITE_PATH", str(ROOT / "data" / "demo.sqlite3") if not os.getenv("VERCEL") else "/tmp/demo.sqlite3")

# Marchio (white-label): ogni editore configura il proprio
BRAND_NAME = os.getenv("BRAND_NAME", "AI Brief")
PUBLISHER_NAME = os.getenv("PUBLISHER_NAME", "Editore Demo")
PUBLISHER_DESC = os.getenv("PUBLISHER_DESC", PUBLISHER_NAME)  # descrizione estesa usata nei prompt
PUBLISHER_SECTION = os.getenv("PUBLISHER_SECTION", "Dall'editore")  # titolo dello slot con i contenuti dell'editore

WINDOW_DAYS = 14  # uscita quindicinale -> finestra di raccolta 14 giorni
MAX_ARTICLES = int(os.getenv("MAX_ARTICLES", "40"))
EXTRACT_BATCH = 8
