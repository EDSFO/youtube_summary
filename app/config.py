import re
from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


CHANNEL_ID_PATTERN = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANNEL_IDS_FILES = ("channel_ids.txt", "Channel_ids.txt")


def _dedupe_keep_order(values):
    seen = set()
    ordered = []
    for value in values:
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def _parse_channel_ids(raw_value: str):
    if not raw_value:
        return []
    parts = re.split(r"[\s,]+", raw_value.strip())
    return [cid for cid in parts if cid and CHANNEL_ID_PATTERN.match(cid)]


def _read_channel_ids_from_file():
    for file_name in CHANNEL_IDS_FILES:
        file_path = PROJECT_ROOT / file_name
        if not file_path.exists():
            continue
        try:
            text = file_path.read_text(encoding="utf-8")
        except Exception:
            return []
        return _parse_channel_ids(text)
    return []


def _parse_topics(raw_value: str):
    if not raw_value:
        return []
    parts = re.split(r"[\r\n,;]+", raw_value.strip())
    return _dedupe_keep_order([part.strip() for part in parts if part and part.strip()])


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=False,
        extra="ignore",
    )

    # YouTube
    youtube_api_key: str = ""
    channel_ids: str = ""
    selected_channel_ids: str = ""
    search_topics: str = ""

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
        env_ids = _parse_channel_ids(self.channel_ids)
        file_ids = _read_channel_ids_from_file()
        return _dedupe_keep_order(env_ids + file_ids)

    @property
    def selected_channel_ids_list(self):
        """Retorna lista de channel IDs selecionados para busca."""
        return _parse_channel_ids(self.selected_channel_ids)

    @property
    def search_topics_list(self):
        """Retorna lista de assuntos para filtrar videos."""
        return _parse_topics(self.search_topics)


def get_settings() -> Settings:
    # Recarrega .env e permite atualização de variáveis em processos longos.
    load_dotenv(override=True)
    return Settings()


settings = get_settings()
