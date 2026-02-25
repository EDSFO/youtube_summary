import logging
from datetime import datetime, timedelta
from typing import Dict, Iterable, Optional

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
            oldest_date = min(valid_dates) if valid_dates else None

            if channel_ids:
                selected_channels = [cid.strip() for cid in channel_ids if cid and cid.strip()]
            else:
                selected_channels = self.settings.channel_ids_list
            selected_channels = list(dict.fromkeys(selected_channels))

            all_videos = []

            if selected_channels:
                # Low-cost flow:
                # 1) channels.list(contentDetails) to get uploads playlist (cost 1 per request).
                # 2) playlistItems.list per selected channel (cost 1 per request).
                channel_metadata = self._get_channel_metadata(selected_channels)
                for ch_id in selected_channels:
                    metadata = channel_metadata.get(ch_id)
                    if not metadata:
                        logger.warning("Canal sem metadata ou playlist de uploads: %s", ch_id)
                        continue

                    videos = self._get_videos_from_uploads_playlist(
                        playlist_id=metadata["uploads_playlist_id"],
                        channel_id=ch_id,
                        fallback_channel_title=metadata.get("channel_title", ""),
                        valid_dates=valid_dates,
                        oldest_date=oldest_date,
                    )
                    all_videos.extend(videos)
                    logger.info("Canal: %s - %s videos", ch_id, len(videos))
            else:
                # Fallback keeps previous behavior for global search when no channels are configured.
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

    @staticmethod
    def _chunked(values, size: int):
        for start in range(0, len(values), size):
            yield values[start : start + size]

    def _get_channel_metadata(self, channel_ids: Iterable[str]) -> Dict[str, Dict[str, str]]:
        metadata = {}

        for chunk in self._chunked(list(channel_ids), 50):
            try:
                response = (
                    self.youtube.channels()
                    .list(
                        part="snippet,contentDetails",
                        id=",".join(chunk),
                        maxResults=50,
                    )
                    .execute()
                )
            except Exception as e:
                logger.error("Erro ao carregar metadata de canais (chunk): %s", e)
                continue

            for item in response.get("items", []):
                channel_id = item.get("id", "")
                uploads_playlist_id = (
                    item.get("contentDetails", {})
                    .get("relatedPlaylists", {})
                    .get("uploads", "")
                )
                channel_title = item.get("snippet", {}).get("title", "")

                if channel_id and uploads_playlist_id:
                    metadata[channel_id] = {
                        "uploads_playlist_id": uploads_playlist_id,
                        "channel_title": channel_title,
                    }

        return metadata

    def _get_videos_from_uploads_playlist(
        self,
        playlist_id: str,
        channel_id: str,
        fallback_channel_title: str,
        valid_dates=None,
        oldest_date=None,
        max_pages: int = 3,
    ):
        videos = []
        page_token = None

        for _ in range(max_pages):
            params = {
                "part": "snippet",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token

            try:
                response = self.youtube.playlistItems().list(**params).execute()
            except Exception as e:
                logger.error(
                    "Erro ao buscar playlist de uploads (%s / %s): %s",
                    channel_id,
                    playlist_id,
                    e,
                )
                break

            stop_pagination = False
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                resource = snippet.get("resourceId", {})
                video_id = resource.get("videoId", "")
                published_at = snippet.get("publishedAt", "")

                if not video_id or not published_at:
                    continue

                try:
                    published_date = datetime.fromisoformat(
                        published_at.replace("Z", "+00:00")
                    ).date()
                except Exception:
                    continue

                if oldest_date and published_date < oldest_date:
                    stop_pagination = True
                    continue

                if valid_dates and published_date not in valid_dates:
                    continue

                thumbnails = snippet.get("thumbnails", {})
                thumbnail = (
                    thumbnails.get("default", {}).get("url")
                    or thumbnails.get("medium", {}).get("url")
                    or ""
                )

                videos.append(
                    {
                        "video_id": video_id,
                        "title": snippet.get("title", ""),
                        "description": snippet.get("description", ""),
                        "channel_title": snippet.get("channelTitle", "")
                        or fallback_channel_title,
                        "channel_id": snippet.get("channelId", "") or channel_id,
                        "published_at": published_at,
                        "thumbnail": thumbnail,
                    }
                )

            if stop_pagination:
                break

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return videos

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
