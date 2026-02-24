import logging
from datetime import datetime, timedelta
from typing import Iterable, Optional

from googleapiclient.discovery import build

from app.config import get_settings

logger = logging.getLogger(__name__)


class YouTubeService:
    def __init__(self):
        self.settings = get_settings()
        self.api_key = self.settings.youtube_api_key
        self.youtube = build("youtube", "v3", developerKey=self.api_key)

    def get_yesterday_videos(self, channel_id: str = None):
        """Fetch only videos published yesterday (UTC)."""
        try:
            now_utc = datetime.utcnow()
            yesterday_date = now_utc.date() - timedelta(days=1)
            return self.get_videos_between_dates(
                start_date=yesterday_date,
                end_date=yesterday_date,
                channel_ids=[channel_id] if channel_id else None,
            )
        except Exception as e:
            logger.error("Erro ao buscar videos: %s", e)
            return []

    def get_videos_between_dates(
        self,
        start_date,
        end_date,
        channel_ids: Optional[Iterable[str]] = None,
    ):
        """Fetch videos in an inclusive UTC date range."""
        try:
            if isinstance(start_date, datetime):
                start_date = start_date.date()
            if isinstance(end_date, datetime):
                end_date = end_date.date()

            if start_date > end_date:
                start_date, end_date = end_date, start_date

            range_start = datetime.combine(start_date, datetime.min.time())
            range_end = datetime.combine(end_date + timedelta(days=1), datetime.min.time())
            published_after = range_start.strftime("%Y-%m-%dT%H:%M:%SZ")
            published_before = range_end.strftime("%Y-%m-%dT%H:%M:%SZ")
            valid_dates = {
                start_date + timedelta(days=offset)
                for offset in range((end_date - start_date).days + 1)
            }

            if channel_ids:
                selected_channels = [cid.strip() for cid in channel_ids if cid and cid.strip()]
            else:
                selected_channels = self.settings.channel_ids_list

            all_videos = []

            if selected_channels:
                for ch_id in selected_channels:
                    videos = self._search_videos(
                        channel_id=ch_id,
                        published_after=published_after,
                        published_before=published_before,
                        valid_dates=valid_dates,
                    )
                    all_videos.extend(videos)
                    logger.info("Canal: %s - %s videos", ch_id, len(videos))
            else:
                logger.warning("Nenhum channel_id configurado no .env")
                videos = self._search_videos(
                    channel_id=None,
                    published_after=published_after,
                    published_before=published_before,
                    valid_dates=valid_dates,
                )
                all_videos.extend(videos)

            all_videos.sort(key=lambda item: item["published_at"], reverse=True)
            return all_videos
        except Exception as e:
            logger.error("Erro ao buscar videos no intervalo: %s", e)
            return []

    def _search_videos(
        self,
        channel_id: str = None,
        published_after: str = None,
        published_before: str = None,
        valid_dates=None,
    ):
        """Fetch videos from search endpoint with date filter."""
        params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "maxResults": 10,
        }
        if channel_id:
            params["channelId"] = channel_id
        if published_after:
            params["publishedAfter"] = published_after
        if published_before:
            params["publishedBefore"] = published_before

        request = self.youtube.search().list(**params)
        response = request.execute()

        videos = []
        for item in response.get("items", []):
            published_at = item["snippet"]["publishedAt"]
            if valid_dates:
                published_date = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                ).date()
                if published_date not in valid_dates:
                    continue

            videos.append(
                {
                    "video_id": item["id"]["videoId"],
                    "title": item["snippet"]["title"],
                    "description": item["snippet"]["description"],
                    "channel_title": item["snippet"]["channelTitle"],
                    "channel_id": item["snippet"]["channelId"],
                    "published_at": published_at,
                    "thumbnail": item["snippet"]["thumbnails"]["default"]["url"],
                }
            )

        return videos

    def get_video_details(self, video_id: str):
        """Get details for a specific video."""
        try:
            request = self.youtube.videos().list(part="snippet,statistics", id=video_id)
            response = request.execute()

            if response["items"]:
                item = response["items"][0]
                return {
                    "video_id": video_id,
                    "title": item["snippet"]["title"],
                    "description": item["snippet"]["description"],
                    "channel_title": item["snippet"]["channelTitle"],
                    "view_count": item["statistics"].get("viewCount", "N/A"),
                    "like_count": item["statistics"].get("likeCount", "N/A"),
                    "comment_count": item["statistics"].get("commentCount", "N/A"),
                }
            return None
        except Exception as e:
            logger.error("Erro ao buscar detalhes do video: %s", e)
            return None


youtube_service = YouTubeService()
