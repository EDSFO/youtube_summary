import logging
from typing import Optional

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)


logger = logging.getLogger(__name__)


class TranscriptService:
    def get_transcript_text(
        self,
        video_id: str,
        preferred_languages: Optional[list[str]] = None,
    ) -> str:
        languages = preferred_languages or ["pt-BR", "pt", "en"]

        try:
            transcript = YouTubeTranscriptApi().fetch(video_id, languages=languages)
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable) as exc:
            logger.info("Transcript indisponivel para %s: %s", video_id, exc)
            return ""
        except Exception as exc:
            logger.warning("Falha ao obter transcript de %s: %s", video_id, exc)
            return ""

        parts = [item.text.strip() for item in transcript if getattr(item, "text", "")]
        return " ".join(parts).strip()


transcript_service = TranscriptService()
