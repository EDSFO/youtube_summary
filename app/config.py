from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # YouTube
    youtube_api_key: str = ""
    channel_ids: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "gpt-4o"

    # Telegram
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Server
    host: str = "0.0.0.0"
    port: int = 8000

    @property
    def channel_ids_list(self):
        """Retorna lista de channel IDs"""
        if not self.channel_ids:
            return []
        return [cid.strip() for cid in self.channel_ids.split(",") if cid.strip()]


def get_settings() -> Settings:
    # Recarrega .env e permite atualização de variáveis em processos longos.
    load_dotenv(override=True)
    return Settings()


settings = get_settings()
