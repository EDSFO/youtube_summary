import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime
from app.services.youtube import YouTubeService
from app.services.openrouter import OpenRouterService
from app.services.telegram import TelegramService
from app.models.database import Database

logger = logging.getLogger(__name__)

router = APIRouter()


class SummaryRequest(BaseModel):
    channel_id: str = None


class VideoResponse(BaseModel):
    video_id: str
    title: str
    description: str
    channel_title: str
    published_at: str
    thumbnail: str
    summary: str = None


class SummaryResponse(BaseModel):
    status: str
    videos_count: int
    videos: list
    generated_at: str


@router.get("/videos")
async def get_yesterday_videos():
    """Buscar vídeos do dia anterior"""
    try:
        yt = YouTubeService()
        videos = yt.get_yesterday_videos()
        return {
            "status": "success",
            "count": len(videos),
            "date": "yesterday",
            "videos": videos
        }
    except Exception as e:
        logger.error(f"Erro ao buscar vídeos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/resumo", response_model=SummaryResponse)
async def generate_summary(request: SummaryRequest = None):
    """Gerar resumos dos vídeos e enviar via Telegram"""
    try:
        yt = YouTubeService()
        openrouter = OpenRouterService()
        telegram = TelegramService()
        db = Database()

        # Buscar vídeos de ontem
        videos = yt.get_yesterday_videos(request.channel_id if request else None)

        if not videos:
            return {
                "status": "success",
                "videos_count": 0,
                "videos": [],
                "generated_at": datetime.now().isoformat()
            }

        processed_videos = []

        for video in videos:
            video_id = video['video_id']

            # Verificar se já foi processado
            if await db.is_video_processed(video_id):
                logger.info(f"Vídeo {video_id} já processado, pulando...")
                continue

            # Gerar resumo
            logger.info(f"Gerando resumo para: {video['title']}")
            summary = openrouter.summarize_video(video['title'], video['description'])

            # Marcar como processado
            await db.mark_video_processed(
                video_id,
                video['title'],
                video.get('channel_id', ''),
                video.get('channel_title', ''),
                summary,
                openrouter.model
            )

            # Enviar para Telegram
            await telegram.send_video_summary(video, summary)
            await db.log_telegram_sent(video_id, True)

            processed_videos.append({
                **video,
                "summary": summary
            })

        return {
            "status": "success",
            "videos_count": len(processed_videos),
            "videos": processed_videos,
            "generated_at": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Erro ao gerar resumos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/videos/all")
async def get_all_videos():
    """Buscar todos os vídeos processados"""
    try:
        db = Database()
        videos = await db.get_processed_videos(100)
        return {
            "status": "success",
            "count": len(videos),
            "videos": videos
        }
    except Exception as e:
        logger.error(f"Erro ao buscar vídeos: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/schedule")
async def run_schedule():
    """Executar geração de resumos (para cron externo)"""
    try:
        yt = YouTubeService()
        openrouter = OpenRouterService()
        telegram = TelegramService()
        db = Database()

        videos = yt.get_yesterday_videos()

        if not videos:
            await telegram.send_message("🎬 Nenhum vídeo encontrado de ontem.")
            return {"status": "success", "message": "Nenhum vídeo encontrado", "count": 0}

        summaries = {}
        processed_count = 0

        for video in videos:
            video_id = video['video_id']

            if await db.is_video_processed(video_id):
                continue

            summary = openrouter.summarize_video(video['title'], video['description'])
            summaries[video_id] = summary

            await db.mark_video_processed(
                video_id,
                video['title'],
                video.get('channel_id', ''),
                video.get('channel_title', ''),
                summary,
                openrouter.model
            )
            await telegram.send_video_summary(video, summary)
            await db.log_telegram_sent(video_id, True)
            processed_count += 1

        await telegram.send_videos_digest(videos, summaries)

        return {
            "status": "success",
            "message": f"{processed_count} resumos gerados e enviados",
            "count": processed_count
        }

    except Exception as e:
        logger.error(f"Erro no schedule: {e}")
        raise HTTPException(status_code=500, detail=str(e))
