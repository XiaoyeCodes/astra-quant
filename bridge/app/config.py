"""Runtime configuration, read from environment variables (see .env.example)."""
import os


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings:
    def __init__(self) -> None:
        # Where OctoBot's web interface answers (inside docker-compose: http://octobot:5001)
        self.octobot_url = os.getenv("OCTOBOT_URL", "http://127.0.0.1:5001").rstrip("/")
        # OctoBot's user folder (mounted read-only) so the bridge can read the active profile
        self.octobot_user_dir = os.getenv("OCTOBOT_USER_DIR", "/octobot-user")
        # Public URL of OctoBot for the browser (link button in the cockpit)
        self.octobot_public_url = os.getenv("OCTOBOT_PUBLIC_URL", "http://localhost:5001")

        # Bitget API key for the bridge. Read-only permission is enough and recommended.
        self.bitget_key = os.getenv("BITGET_API_KEY", "").strip()
        self.bitget_secret = os.getenv("BITGET_API_SECRET", "").strip()
        self.bitget_passphrase = os.getenv("BITGET_API_PASSPHRASE", "").strip()
        # Optional proxy for reaching Bitget (e.g. http://host.docker.internal:7890)
        self.bitget_proxy = os.getenv("BITGET_PROXY", "").strip()

        # Symbols always watched, in ccxt notation. Futures use "BTC/USDT:USDT".
        self.watch_symbols = _list("WATCH_SYMBOLS", "BTC/USDT,ETH/USDT,BTC/USDT:USDT")
        self.default_focus = os.getenv("FOCUS_SYMBOL", "").strip() or (self.watch_symbols[0] if self.watch_symbols else "BTC/USDT")

        # Emergency controls (cancel all orders + stop OctoBot). Off by default.
        self.enable_controls = _bool("ENABLE_CONTROLS", False)

        # Strategy radar and auto switching (paper trading only)
        self.regime_symbol = os.getenv("REGIME_SYMBOL", "BTC/USDT").strip() or "BTC/USDT"
        self.regime_period = float(os.getenv("REGIME_PERIOD", "") or 600)
        # same verdict this many times in a row before an automatic switch
        self.auto_confirm = max(1, int(os.getenv("AUTO_SWITCH_CONFIRM", "") or 3))
        # minimum time a strategy runs before an automatic switch away from it
        self.auto_min_hold_hours = float(os.getenv("AUTO_SWITCH_MIN_HOLD_HOURS", "") or 12)

        # Large language model for the AI market read (DeepSeek or any OpenAI compatible API)
        self.llm_api_key = os.getenv("LLM_API_KEY", "").strip()
        self.llm_base_url = (os.getenv("LLM_BASE_URL", "").strip() or "https://api.deepseek.com").rstrip("/")
        self.llm_model = os.getenv("LLM_MODEL", "").strip() or "deepseek-flash"
        self.llm_proxy = os.getenv("LLM_PROXY", "").strip()
        self.ai_regime_period = float(os.getenv("AI_REGIME_PERIOD", "") or 1800)

        # Polling periods (seconds)
        self.ticker_period = float(os.getenv("TICKER_PERIOD", "1.0"))
        self.octobot_period = float(os.getenv("OCTOBOT_PERIOD", "3.0"))
        self.private_period = float(os.getenv("PRIVATE_PERIOD", "8.0"))
        self.broadcast_period = float(os.getenv("BROADCAST_PERIOD", "1.0"))

        # Where the bridge keeps its small equity history file
        self.data_dir = os.getenv("DATA_DIR", "/data")
        self.web_dir = os.getenv("WEB_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "web"))
        self.host = os.getenv("HOST", "0.0.0.0")
        self.port = int(os.getenv("PORT", "8080"))

    @property
    def bitget_private(self) -> bool:
        return bool(self.bitget_key and self.bitget_secret and self.bitget_passphrase)


settings = Settings()
VERSION = "1.0.0"
