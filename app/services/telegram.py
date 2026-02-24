import asyncio
import logging

from telegram import Update
from telegram.ext import AIORateLimiter, Application, CommandHandler, ContextTypes

from app.config import get_settings

logger = logging.getLogger(__name__)


class TelegramService:
    def __init__(self):
        settings = get_settings()
        self.token = settings.telegram_bot_token
        self.chat_id = settings.telegram_chat_id
        self.application = None

    async def send_message(
        self,
        text: str,
        chat_id: str = None,
        parse_mode: str = None,
        disable_web_page_preview: bool = False,
    ):
        """Send a Telegram message."""
        try:
            destination = chat_id or self.chat_id
            if not destination:
                logger.warning("Nenhum chat_id configurado para envio")
                return False

            if not self.application:
                rate_limiter = AIORateLimiter()
                self.application = (
                    Application.builder()
                    .token(self.token)
                    .rate_limiter(rate_limiter)
                    .build()
                )
                await self.application.initialize()

            await self.application.bot.send_message(
                chat_id=destination,
                text=text,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            logger.info("Mensagem enviada para %s", destination)
            return True
        except Exception as e:
            logger.error("Erro ao enviar mensagem: %s", e)
            return False

    async def send_video_summary(self, video: dict, summary: str):
        """Send one summary in the requested visual style."""
        title = video.get("title", "Sem titulo")
        channel = video.get("channel_title", "N/A")
        video_id = video.get("video_id")
        video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""

        summary_text = (summary or "").strip()
        max_summary_len = 3000
        if len(summary_text) > max_summary_len:
            summary_text = summary_text[:max_summary_len].rstrip() + "..."

        message = (
            f"🖥️ {title}\n\n"
            f"Canal: {channel}\n\n"
            f"📝 Resumo:\n{summary_text}\n\n"
            f"Assistir no YouTube\n{video_url}"
        )
        return await self.send_message(message, parse_mode=None, disable_web_page_preview=False)

    async def send_videos_digest(self, videos: list, summaries: dict):
        """Send a compact digest for the day."""
        if not videos:
            message = "🎬 Nenhum video encontrado\n\nNao foram encontrados videos publicados ontem."
            return await self.send_message(message)

        message = f"📊 Resumo do Dia\n\n{len(videos)} videos encontrados:\n\n"

        for i, video in enumerate(videos, 1):
            summary = summaries.get(video["video_id"], "Resumo nao disponivel.")
            message += f"{i}. {video['title']}\n"
            message += f"   Canal: {video['channel_title']}\n"
            message += f"   📝 {summary[:100]}...\n"
            message += f"   🔗 https://www.youtube.com/watch?v={video['video_id']}\n\n"

        return await self.send_message(message)

    async def start_bot(self):
        """Start bot commands."""
        from app.models.database import Database
        from app.services.openrouter import OpenRouterService
        from app.services.youtube import YouTubeService

        async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
            await update.message.reply_text(
                "🤖 Bot de Resumo YouTube ativo!\n"
                "Comandos disponiveis:\n"
                "/resumo - Gerar resumo do dia\n"
                "/hoje - Videos de ontem"
            )

        async def resumo(update: Update, context: ContextTypes.DEFAULT_TYPE):
            yt = YouTubeService()
            openrouter = OpenRouterService()
            db = Database()

            videos = yt.get_yesterday_videos()

            if videos:
                for video in videos:
                    summary = openrouter.summarize_video(video["title"], video["description"])
                    await db.mark_video_processed(
                        video["video_id"],
                        video.get("title", ""),
                        video.get("channel_id", ""),
                        video.get("channel_title", ""),
                        summary,
                        openrouter.model,
                    )
                    await self.send_video_summary(video, summary)
            else:
                await update.message.reply_text("Nenhum video encontrado de ontem.")

        async def hoje(update: Update, context: ContextTypes.DEFAULT_TYPE):
            from app.services.youtube import YouTubeService

            yt = YouTubeService()
            videos = yt.get_yesterday_videos()

            if videos:
                message = f"📺 {len(videos)} videos encontrados:\n\n"
                for video in videos:
                    message += f"• {video['title']}\n"
                await update.message.reply_text(message)
            else:
                await update.message.reply_text("Nenhum video encontrado.")

        rate_limiter = AIORateLimiter()
        self.application = (
            Application.builder()
            .token(self.token)
            .rate_limiter(rate_limiter)
            .build()
        )

        self.application.add_handler(CommandHandler("start", start))
        self.application.add_handler(CommandHandler("resumo", resumo))
        self.application.add_handler(CommandHandler("hoje", hoje))

        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        logger.info("Bot Telegram iniciado")

    def run(self):
        """Run bot (blocking)."""
        asyncio.run(self.start_bot())


telegram_service = TelegramService()
