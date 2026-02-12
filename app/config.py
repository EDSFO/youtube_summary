from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
import os

load_dotenv()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # YouTube
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    channel_ids: str = os.getenv("CHANNEL_IDS", "")

    # OpenRouter
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "gpt-4o")

    # Telegram
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # Server
    host: str = os.getenv("HOST", "0.0.0.0")
    port: int = int(os.getenv("PORT", "8000"))

    @property
    def channel_ids_list(self):
        """Retorna lista de channel IDs"""
        if not self.channel_ids:
            return []
        return [cid.strip() for cid in self.channel_ids.split(",") if cid.strip()]

settings = Settings()
