"""Runtime settings. Everything is overridable through environment variables."""
import os
import secrets


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


SEED = int(os.getenv("MULERING_SEED", "7"))
ACCOUNTS_PER_BANK = int(os.getenv("ACCOUNTS_PER_BANK", "900"))
N_DAYS = 30

# Secret used by member banks to tokenise identifiers. The hub never needs it.
DEFAULT_KEY = "dev-only-key-change-me"
HMAC_KEY = os.getenv("CONSORTIUM_HMAC_KEY", DEFAULT_KEY).encode()
USING_DEFAULT_KEY = HMAC_KEY.decode() == DEFAULT_KEY

DATA_DIR = os.getenv("DATA_DIR", "data")
# Label of the tokenisation key in use, recorded with every model so a rotation can be traced.
KEY_VERSION = os.getenv("CONSORTIUM_KEY_VERSION", "v1")

# Train on a bank's own records instead of the synthetic generator (see app/dataio.py).
TRAIN_DATA_DIR = os.getenv("TRAIN_DATA_DIR", "")
# Start from real history instead of a simulated one. New activity then arrives
# only through the ingestion API, and the simulator and demo attack are off.
LIVE_DATA_DIR = os.getenv("LIVE_DATA_DIR", "")
# Keys for bank-side adapters posting to the ingestion API, as "A:key,B:key,C:key".
INGEST_KEYS = {p.split(":", 1)[0].strip().upper(): p.split(":", 1)[1].strip()
               for p in os.getenv("INGEST_KEYS", "").split(",") if ":" in p}

# (code, display name, IFSC-style prefix, UPI handle). Deliberately fictional.
BANKS = [
    ("A", "Bank A", "BKAA0", "banka"),
    ("B", "Bank B", "BKBB0", "bankb"),
    ("C", "Bank C", "BKCC0", "bankc"),
]
BANK_CODES = [b[0] for b in BANKS]
ROLES = ["hub"] + BANK_CODES
ROLE_NAMES = {"hub": "Consortium hub operator", **{b[0]: f"{b[1]} analyst" for b in BANKS}}

# --- access ---------------------------------------------------------------
# Signs session cookies. Without it a fresh secret is made at start-up, which
# simply signs everyone out on restart.
SESSION_SECRET = (os.getenv("SESSION_SECRET") or secrets.token_hex(32)).encode()
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
# Optional shared code asked for at sign-in. Empty means an open demo.
ACCESS_CODE = os.getenv("ACCESS_CODE", "")
# Launch attack, pause, reset and the answer key. Turn off for anything but a demo.
DEMO_CONTROLS = _flag("DEMO_CONTROLS", True)
# Read the client address from proxy headers (true on Railway, false when exposed directly).
TRUST_PROXY = _flag("TRUST_PROXY", bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID")))

RATE_READ = int(os.getenv("RATE_READ_PER_MIN", "600"))
RATE_WRITE = int(os.getenv("RATE_WRITE_PER_MIN", "60"))
RATE_SIGNIN = int(os.getenv("RATE_SIGNIN_PER_5MIN", "20"))
RATE_INGEST = int(os.getenv("RATE_INGEST_PER_MIN", "600"))

# --- live stream ----------------------------------------------------------
STREAM_SPEED = float(os.getenv("STREAM_SPEED", "60"))        # simulated minutes per real minute
STREAM_TICK_SECONDS = float(os.getenv("STREAM_TICK_SECONDS", "2"))
MAX_LIVE_CLIENTS = int(os.getenv("MAX_LIVE_CLIENTS", "60"))
