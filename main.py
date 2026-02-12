"""
Sistema de Resumos AutomÃ¡ticos de VÃ­deos YouTube
================================================
Usa Supadata API para transcriÃ§Ãµes + OpenRouter para resumir
Salva resultados em SQLite
"""

# Fix UTF-8 encoding for Windows
import sys
import io
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

import os
import sys

# Add project directory to path
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

from dotenv import load_dotenv
load_dotenv(os.path.join(PROJECT_DIR, '.env'))

import time
import json
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dataclasses import dataclass, field

import httpx
import asyncio
from openai import OpenAI
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ============================================
# CONFIGURAÃ‡Ã•ES - EDITE AQUI
# ============================================

@dataclass
class Config:
    # YouTube Data API
    YOUTUBE_API_KEY: str = ""

    # Supadata API
    SUPADATA_API_KEY: str = ""

    # OpenRouter API (https://openrouter.ai)
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "anthropic/claude-sonnet-4-20250514"

    # Canais para monitorar (handles ou IDs)
    CHANNELS: List[str] = field(default_factory=list)

    # VÃ­deos por canal a processar
    VIDEOS_PER_CHANNEL: int = 3

    # Idioma das transcriÃ§Ãµes
    TRANSCRIPT_LANG: str = "pt"

    # Banco de dados
    DB_PATH: str = "resumos.db"

    # Telegram (opcional)
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""

# ============================================
# CLIENT SUPADATA
# ============================================

class SupadataClient:
    BASE_URL = "https://api.supadata.ai/v1"
    _rate_lock = asyncio.Lock()
    _last_request_ts = 0.0

    def __init__(self, api_key: str):
        self.headers = {"x-api-key": api_key}
        self.min_interval_s = 1.1  # 1 req/second with safety margin

    async def _throttle(self):
        """Global throttle to respect 1 req/sec limit."""
        async with self._rate_lock:
            now = time.time()
            wait_s = self.min_interval_s - (now - self._last_request_ts)
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            self._last_request_ts = time.time()

    async def get_channel_videos(self, channel_id: str, limit: int = 10) -> List[str]:
        """Busca vÃ­deos de um canal usando a API de busca."""
        async with httpx.AsyncClient() as client:
            # Usar apenas uma query para reduzir consumo de quota
            query = channel_id
            if channel_id.startswith("UC"):
                query = f"channel:{channel_id}"

            for attempt in range(2):
                try:
                    await self._throttle()
                    response = await client.get(
                        f"{self.BASE_URL}/youtube/search",
                        headers=self.headers,
                        params={
                            "query": query,
                            "type": "video",
                            "uploadDate": "week",
                            "limit": limit
                        },
                        timeout=10
                    )

                    if response.status_code == 429:
                        wait_s = 2
                        print(
                            f"âš ï¸ Supadata rate limit (429) para query '{query}'. "
                            f"Aguardando {wait_s}s antes de tentar novamente..."
                        )
                        await asyncio.sleep(wait_s)
                        continue

                    if response.status_code != 200:
                        print(
                            f"âš ï¸ Supadata /youtube/search status {response.status_code} "
                            f"para query '{query}': {response.text[:500]}"
                        )
                        return []

                    data = response.json()
                    video_ids = []
                    for result in data.get("results", []):
                        result_id = result.get("id") or result.get("videoId")
                        if result_id:
                            video_ids.append(result_id)
                    return video_ids
                except Exception as e:
                    if attempt < 1:
                        wait_s = 2
                        print(
                            f"âš ï¸ Erro ao buscar vÃ­deos ({e}). "
                            f"Aguardando {wait_s}s antes de tentar novamente..."
                        )
                        await asyncio.sleep(wait_s)
                        continue
                    return []

            return []

# ============================================
# CLIENT YOUTUBE DATA API
# ============================================

class YouTubeDataClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.youtube = build("youtube", "v3", developerKey=self.api_key)

    def get_channel_videos(self, channel_id_or_handle: str, limit: int = 10) -> List[Dict]:
        """Busca vÃ­deos recentes de um canal usando YouTube Data API."""
        # Caminho de baixo custo: usar uploads playlist
        if channel_id_or_handle.startswith("UC"):
            channels_resp = self.youtube.channels().list(
                part="contentDetails",
                id=channel_id_or_handle
            ).execute()

            items = channels_resp.get("items", [])
            if not items:
                return []

            uploads_id = (
                items[0]
                .get("contentDetails", {})
                .get("relatedPlaylists", {})
                .get("uploads")
            )
            if not uploads_id:
                return []

            playlist_resp = self.youtube.playlistItems().list(
                part="snippet",
                playlistId=uploads_id,
                maxResults=limit
            ).execute()

            videos = []
            for item in playlist_resp.get("items", []):
                snippet = item.get("snippet", {})
                video_id = snippet.get("resourceId", {}).get("videoId")
                if not video_id:
                    continue
                videos.append({
                    "video_id": video_id,
                    "title": snippet.get("title"),
                    "description": snippet.get("description"),
                    "channel_title": snippet.get("channelTitle"),
                    "channel_id": snippet.get("channelId"),
                    "published_at": snippet.get("publishedAt"),
                    "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url")
                })

            return videos

        # Tentativa com handle (forHandle) - baixo custo
        handle = channel_id_or_handle.lstrip("@")
        try:
            channels_resp = self.youtube.channels().list(
                part="contentDetails",
                forHandle=handle
            ).execute()
            items = channels_resp.get("items", [])
            if items:
                channel_id = items[0].get("id")
                if channel_id:
                    return self.get_channel_videos(channel_id, limit=limit)
        except HttpError:
            pass

        # Fallback: search (custo maior)
        params = {
            "part": "snippet",
            "type": "video",
            "order": "date",
            "maxResults": limit,
            "q": handle
        }

        request = self.youtube.search().list(**params)
        response = request.execute()

        videos = []
        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            video_id = item.get("id", {}).get("videoId")
            if not video_id:
                continue
            videos.append({
                "video_id": video_id,
                "title": snippet.get("title"),
                "description": snippet.get("description"),
                "channel_title": snippet.get("channelTitle"),
                "channel_id": snippet.get("channelId"),
                "published_at": snippet.get("publishedAt"),
                "thumbnail": snippet.get("thumbnails", {}).get("default", {}).get("url")
            })

        return videos

    async def get_video_metadata(self, video_id: str) -> Optional[Dict]:
        """ObtÃ©m metadados de um vÃ­deo."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/youtube/video",
                headers=self.headers,
                params={"id": video_id}
            )
            if response.status_code == 200:
                return response.json()
            return None

    async def request_transcript_batch(
        self,
        video_ids: List[str],
        lang: str = "pt",
        text_mode: bool = True
    ) -> str:
        """Solicita transcriÃ§Ã£o em lote de vÃ­deos."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.BASE_URL}/youtube/transcript/batch",
                headers=self.headers,
                json={
                    "videoIds": video_ids,
                    "lang": lang,
                    "text": text_mode
                }
            )
            response.raise_for_status()
            return response.json()["jobId"]

    async def get_batch_status(self, job_id: str) -> Dict:
        """Verifica status de um job em lote."""
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.BASE_URL}/youtube/batch/{job_id}",
                headers=self.headers
            )
            response.raise_for_status()
            return response.json()

# ============================================
# CLIENT OPENROUTER (RESUMOS)
# ============================================

class OpenRouterClient:
    BASE_URL = "https://openrouter.ai/api/v1"

    def __init__(self, api_key: str, model: str = "anthropic/claude-sonnet-4-20250514"):
        self.client = OpenAI(
            base_url=self.BASE_URL,
            api_key=api_key,
        )
        self.model = model

    def generate_summary(self, transcript: str, video_info: Dict) -> str:
        """Gera resumo do vÃ­deo usando IA."""

        prompt = f"""
VocÃª Ã© um assistente que cria resumos de vÃ­deos do YouTube.

## VÃ­deo
- TÃ­tulo: {video_info.get('title', 'N/A')}
- Canal: {video_info.get('channel', {}).get('name', 'N/A')}
- DuraÃ§Ã£o: {video_info.get('duration', 0)} segundos

## TranscriÃ§Ã£o
{transcript[:15000]}  # Limita para nÃ£o exceder contexto

## InstruÃ§Ãµes
1. Crie um resumo conciso e informativo (300-500 palavras)
2. Destaque os principais pontos e insights
3. Inclua os momentos mais importantes com timestamps aproximados
4. Formate em markdown limpo
5. Use portuguÃªs brasileiro

## Formato de SaÃ­da
```markdown
# ðŸ“º [TÃ­tulo do VÃ­deo]

**Canal:** [Nome] | **DuraÃ§Ã£o:** [X] min

## ðŸŽ¯ Resumo Executive
[ParÃ¡grafo de 2-3 linhas com o cerne do conteÃºdo]

## ðŸ“Œ Principais Pontos
- [Ponto 1]
- [Ponto 2]
- [Ponto 3]
...

## â° Highlights com Timestamps
- [00:00] - [Assunto]
- [05:30] - [Assunto importante]
```
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "VocÃª Ã© um especialista em criar resumos claros e informativos de vÃ­deos."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.5,
            max_tokens=2000
        )

        return response.choices[0].message.content

    def generate_summary_from_description(self, description: str, video_info: Dict) -> str:
        """Gera resumo do vÃ­deo usando tÃ­tulo/descriÃ§Ã£o (sem transcriÃ§Ã£o)."""

        prompt = f"""
VocÃª Ã© um assistente que cria resumos de vÃ­deos do YouTube.

## VÃ­deo
- TÃ­tulo: {video_info.get('title', 'N/A')}
- Canal: {video_info.get('channel', {}).get('name', 'N/A')}

## DescriÃ§Ã£o
{(description or '').strip()[:8000]}

## InstruÃ§Ãµes
1. Crie um resumo conciso e informativo (150-250 palavras)
2. Destaque os principais pontos e o tema central
3. Se a descriÃ§Ã£o for curta, deixe claro que o resumo Ã© baseado nela
4. Formate em markdown limpo
5. Use portuguÃªs brasileiro

## Formato de SaÃ­da
```markdown
# ðŸŽ¥ [TÃ­tulo do VÃ­deo]

**Canal:** [Nome]

## ðŸŽ¯ Resumo
[ParÃ¡grafo de 2-4 linhas com o cerne do conteÃºdo]

## ðŸ“Œ Pontos Principais
- [Ponto 1]
- [Ponto 2]
```
"""

        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": "VocÃª Ã© um especialista em criar resumos claros e informativos de vÃ­deos."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=0.5,
            max_tokens=1200
        )

        return response.choices[0].message.content

# ============================================
# CLIENT TELEGRAM
# ============================================

class TelegramClient:
    """Cliente para envio de mensagens para o Telegram."""
    TELEGRAM_MAX_MESSAGE_LEN = 4096

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    async def send_message(self, text: str, parse_mode: Optional[str] = None) -> bool:
        """Envia uma mensagem para o Telegram."""
        if not self.bot_token or not self.chat_id:
            print("Telegram nao configurado (bot_token ou chat_id vazios)")
            return False

        async with httpx.AsyncClient() as client:
            try:
                payload = {
                    "chat_id": self.chat_id,
                    "text": text,
                    "disable_web_page_preview": False,
                }
                if parse_mode:
                    payload["parse_mode"] = parse_mode

                response = await client.post(
                    f"{self.base_url}/sendMessage",
                    json=payload,
                    timeout=30,
                )
                response.raise_for_status()
                return True
            except Exception as e:
                print(f"Erro ao enviar para Telegram: {e}")
                return False

    async def send_video_summary(
        self,
        title: str,
        channel: str,
        summary: str,
        video_url: str = None,
    ) -> bool:
        """Envia um resumo de video formatado."""
        summary_text = (summary or "").strip()
        max_summary_len = 3000
        if len(summary_text) > max_summary_len:
            summary_text = summary_text[:max_summary_len].rstrip() + "..."

        if not summary_text.startswith("```"):
            summary_text = f"```markdown\n{summary_text}\n```"

        message = f"🖥️ {title}\n\n"
        message += f"Canal: {channel}\n\n"
        message += f"📝 Resumo:\n{summary_text}\n\n"

        if video_url:
            message += "Assistir no YouTube\n"
            message += f"{video_url}"

        return await self.send_message(message)

    async def _send_in_chunks(self, text: str, max_len: int = 3500) -> bool:
        """Envia texto em lotes menores para respeitar limite do Telegram."""
        if len(text) <= self.TELEGRAM_MAX_MESSAGE_LEN:
            return await self.send_message(text)

        success = True
        current_chunk = ""

        for line in text.splitlines(keepends=True):
            if len(current_chunk) + len(line) > max_len and current_chunk:
                sent = await self.send_message(current_chunk.rstrip())
                success = success and sent
                current_chunk = line
            else:
                current_chunk += line

        if current_chunk.strip():
            sent = await self.send_message(current_chunk.rstrip())
            success = success and sent

        return success

    async def send_daily_summary(self, summaries: List[Dict]) -> bool:
        """Envia o resumo diario com todos os videos."""
        if not summaries:
            return False

        ontem = datetime.now().date() - timedelta(days=1)

        message = f"Resumos YouTube - {ontem.strftime('%d/%m/%Y')}\n"
        message += f"{len(summaries)} video(s)\n\n"
        message += "-" * 20 + "\n\n"

        for i, s in enumerate(summaries, 1):
            title = s.get("title", "Video")
            message += f"{i}. {title}\n"
            message += f"Canal: {s.get('channel_id', 'N/A')}\n"
            message += "-" * 15 + "\n"

        message += "\nGenerated by ResumoYouTube Bot"

        return await self._send_in_chunks(message)

# ============================================
# BANCO DE DADOS (SQLITE)
# ============================================

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Cria as tabelas se nÃ£o existirem."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE NOT NULL,
                    name TEXT,
                    added_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT UNIQUE NOT NULL,
                    channel_id TEXT,
                    title TEXT,
                    duration INTEGER,
                    view_count INTEGER,
                    transcript TEXT,
                    published_at DATETIME,
                    processed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (channel_id) REFERENCES channels(channel_id)
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT UNIQUE NOT NULL,
                    summary TEXT,
                    model_used TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                )
            """)

            conn.commit()

    def save_video(self, video_data: Dict):
        """Salva ou atualiza dados de um vÃ­deo."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO videos
                (video_id, channel_id, title, duration, view_count, transcript, published_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                video_data["video_id"],
                video_data.get("channel_id"),
                video_data.get("title"),
                video_data.get("duration"),
                video_data.get("view_count"),
                video_data.get("transcript"),
                video_data.get("published_at")
            ))
            conn.commit()

    def save_summary(self, video_id: str, summary: str, model: str):
        """Salva o resumo gerado."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO summaries (video_id, summary, model_used)
                VALUES (?, ?, ?)
            """, (video_id, summary, model))
            conn.commit()

    def video_exists(self, video_id: str) -> bool:
        """Verifica se vÃ­deo jÃ¡ foi processado."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT 1 FROM videos WHERE video_id = ? AND transcript IS NOT NULL",
                (video_id,)
            )
            return cursor.fetchone() is not None

    def get_unprocessed_videos(self) -> List[Dict]:
        """Retorna vÃ­deos sem transcriÃ§Ã£o."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                SELECT video_id, channel_id, title, duration
                FROM videos
                WHERE transcript IS NULL
            """)
            return [dict(zip(["video_id", "channel_id", "title", "duration"], row))
                    for row in cursor.fetchall()]

    def get_summaries_by_date(self, target_date: str = None) -> List[Dict]:
        """Retorna resumos de uma data especÃ­fica (formato: YYYY-MM-DD)."""
        if target_date is None:
            target_date = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT
                    v.video_id,
                    v.title,
                    v.channel_id,
                    v.published_at,
                    s.summary,
                    s.created_at
                FROM summaries s
                JOIN videos v ON s.video_id = v.video_id
                WHERE DATE(s.created_at) = ?
                ORDER BY s.created_at DESC
            """, (target_date,))

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_yesterday_summaries(self) -> List[Dict]:
        """Retorna resumos do dia anterior."""
        yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
        return self.get_summaries_by_date(yesterday)

    def get_summaries_by_published_date(self, target_date: str) -> List[Dict]:
        """Retorna resumos pela data de publicação do vídeo (YYYY-MM-DD)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT
                    v.video_id,
                    v.title,
                    v.channel_id,
                    v.published_at,
                    s.summary,
                    s.created_at
                FROM summaries s
                JOIN videos v ON s.video_id = v.video_id
                WHERE DATE(v.published_at) = ?
                ORDER BY v.published_at DESC
            """, (target_date,))

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

    def get_summaries_by_published_date_range(self, start_date: str, end_date: str) -> List[Dict]:
        """Retorna resumos por intervalo de data de publicação (YYYY-MM-DD)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT
                    v.video_id,
                    v.title,
                    v.channel_id,
                    v.published_at,
                    s.summary,
                    s.created_at
                FROM summaries s
                JOIN videos v ON s.video_id = v.video_id
                WHERE DATE(v.published_at) BETWEEN ? AND ?
                ORDER BY v.published_at DESC
            """, (start_date, end_date))

            rows = cursor.fetchall()
            return [dict(row) for row in rows]

# ============================================
# ORQUESTRADOR PRINCIPAL
# ============================================

class ResumoYouTube:
    def __init__(self, config: Config):
        self.config = config
        self.supadata = SupadataClient(config.SUPADATA_API_KEY) if config.SUPADATA_API_KEY else None
        self.youtube = YouTubeDataClient(config.YOUTUBE_API_KEY) if config.YOUTUBE_API_KEY else None
        self.openrouter = OpenRouterClient(
            config.OPENROUTER_API_KEY,
            config.OPENROUTER_MODEL
        )
        self.db = Database(config.DB_PATH)
        self.telegram = TelegramClient(
            config.TELEGRAM_BOT_TOKEN,
            config.TELEGRAM_CHAT_ID
        )

    @staticmethod
    def _extract_video_date(metadata: Dict) -> Optional[datetime.date]:
        """Extrai a data de publicação a partir dos metadados."""
        published_raw = metadata.get("published_at") or metadata.get("uploadDate")
        if not isinstance(published_raw, str) or not published_raw.strip():
            return None
        try:
            return datetime.fromisoformat(
                published_raw.replace("Z", "+00:00")
            ).date()
        except Exception:
            return None

    async def run(self, target_dates: Optional[List[str]] = None):
        """Executa o pipeline completo filtrando vídeos por data de publicação."""
        hoje = datetime.now().date()
        if target_dates:
            selected_dates = []
            for d in target_dates:
                selected_dates.append(datetime.strptime(d, "%Y-%m-%d").date())
        else:
            selected_dates = [hoje - timedelta(days=1)]
        selected_dates_set = set(selected_dates)
        selected_dates_label = ", ".join(d.strftime("%d/%m/%Y") for d in sorted(selected_dates))

        print(f"\nðŸš€ Resumos YouTube - {hoje.strftime('%d/%m/%Y')}")
        print(f"ðŸ“… Processando vídeos publicados em: {selected_dates_label}")
        print("=" * 60)

        # Coletar vÃ­deos de todos os canais com metadados
        videos_do_dia = []

        print("\nðŸ“º Buscando vídeos do(s) dia(s) solicitado(s)...")
        for channel in self.config.CHANNELS:
            print(f"  â–¶ï¸ {channel}...", end=" ")

            try:
                if self.youtube:
                    videos = self.youtube.get_channel_videos(
                        channel,
                        limit=self.config.VIDEOS_PER_CHANNEL * 2
                    )
                    selected_count = 0
                    for v in videos:
                        video_date = self._extract_video_date(v)
                        if video_date not in selected_dates_set:
                            continue
                        videos_do_dia.append({
                            "video_id": v["video_id"],
                            "metadata": v
                        })
                        selected_count += 1
                    print(f"âœ“ {selected_count} vídeo(s) no período")
                else:
                    # Fallback para Supadata (caso YouTube API nÃ£o esteja configurada)
                    if not self.supadata:
                        print("âœ— Erro: Configure YOUTUBE_API_KEY ou SUPADATA_API_KEY")
                        continue

                    video_ids = await self.supadata.get_channel_videos(
                        channel,
                        limit=self.config.VIDEOS_PER_CHANNEL * 2
                    )

                    selected_count = 0
                    for video_id in video_ids:
                        metadata = await self.supadata.get_video_metadata(video_id)
                        if not metadata:
                            continue
                        video_date = self._extract_video_date(metadata)
                        if video_date not in selected_dates_set:
                            continue

                        videos_do_dia.append({
                            "video_id": video_id,
                            "metadata": metadata
                        })
                        selected_count += 1

                    print(f"âœ“ {selected_count} vídeo(s) no período")

            except Exception as e:
                print(f"âœ— Erro: {e}")

        # Remover duplicatas
        seen = set()
        unique_videos = []
        for v in videos_do_dia:
            if v["video_id"] not in seen:
                seen.add(v["video_id"])
                unique_videos.append(v)

        print(f"\nðŸ“Š {len(unique_videos)} vÃ­deos encontrados")

        if not unique_videos:
            print("âœ… Nenhum vÃ­deo novo para processar!")
            return

        # Exibir lista de vÃ­deos encontrados
        print("\nðŸ“‹ VÃ­deos encontrados:")
        for i, v in enumerate(unique_videos, 1):
            meta = v["metadata"]
            titulo = meta.get("title", "N/A")[:50]
            canal = (
                meta.get("channel_title")
                or meta.get("channel", {}).get("name")
                or "N/A"
            )
            print(f"   {i}. [{canal}] {titulo}")

        # Processar vÃ­deos
        if self.youtube:
            print("\nðŸ“ Gerando resumos (sem transcriÃ§Ã£o)...")
            for v in unique_videos:
                meta = v["metadata"]
                video_id = v["video_id"]

                try:
                    summary = self.openrouter.generate_summary_from_description(
                        meta.get("description", ""),
                        {
                            "title": meta.get("title"),
                            "channel": {"name": meta.get("channel_title")}
                        }
                    )

                    video_record = {
                        "video_id": video_id,
                        "channel_id": meta.get("channel_id"),
                        "title": meta.get("title"),
                        "duration": None,
                        "view_count": None,
                        "published_at": meta.get("published_at"),
                        "transcript": None
                    }
                    self.db.save_video(video_record)
                    self.db.save_summary(video_id, summary, self.config.OPENROUTER_MODEL)
                    print(f"    âœ“ Resumo gerado: {meta.get('title', 'N/A')}")

                    if self.config.TELEGRAM_BOT_TOKEN and self.config.TELEGRAM_CHAT_ID:
                        sent = await self.telegram.send_video_summary(
                            title=meta.get("title", "Sem título"),
                            channel=meta.get("channel_title", "N/A"),
                            summary=summary,
                            video_url=f"https://www.youtube.com/watch?v={video_id}"
                        )
                        if sent:
                            print("      âœ“ Enviado ao Telegram")
                        else:
                            print("      âš ï¸ Falha ao enviar ao Telegram")
                except Exception as e:
                    print(f"    âœ— Erro ao gerar resumo: {e}")
        else:
            # Processar transcriÃ§Ãµes em lote via Supadata
            print("\nðŸ“ Solicitando transcriÃ§Ãµes...")
            video_ids_batch = [v["video_id"] for v in unique_videos]

            batch_size = 50
            for i in range(0, len(video_ids_batch), batch_size):
                batch = video_ids_batch[i:i + batch_size]
                print(f"  Lote {i//batch_size + 1}: {len(batch)} vÃ­deos...")

                job_id = await self.supadata.request_transcript_batch(
                    batch,
                    lang=self.config.TRANSCRIPT_LANG
                )
                print(f"    Job ID: {job_id}")

                await self._wait_for_batch(job_id)
                results = await self.supadata.get_batch_status(job_id)

                for result in results.get("results", []):
                    await self._process_result(result)

        print("\n" + "=" * 60)
        print("âœ… Processamento concluÃ­do!")

    async def _wait_for_batch(self, job_id: str, max_attempts: int = 30):
        """Aguarda um job batch ser concluÃ­do."""
        for attempt in range(max_attempts):
            status = await self.supadata.get_batch_status(job_id)
            current_status = status.get("status", "unknown")

            print(f"    Status: {current_status} ({attempt + 1}/{max_attempts})")

            if current_status == "completed":
                return
            elif current_status == "failed":
                raise Exception(f"Job falhou: {status}")

            await asyncio.sleep(2)  # Espera 2 segundos entre checks

    async def _process_result(self, result: Dict):
        """Processa um resultado individual."""
        video_id = result.get("videoId")
        transcript_data = result.get("transcript")
        video_data = result.get("video")

        if not transcript_data:
            print(f"    âš ï¸ VÃ­deo {video_id}: sem transcriÃ§Ã£o disponÃ­vel")
            return

        # Extrair texto da transcriÃ§Ã£o
        if isinstance(transcript_data.get("content"), list):
            transcript_text = " ".join(
                chunk.get("text", "")
                for chunk in transcript_data["content"]
            )
        else:
            transcript_text = transcript_data.get("content", "")

        # Salvar no banco
        video_record = {
            "video_id": video_id,
            "channel_id": video_data.get("channel", {}).get("id") if video_data else None,
            "title": video_data.get("title") if video_data else None,
            "duration": video_data.get("duration") if video_data else None,
            "view_count": video_data.get("viewCount") if video_data else None,
            "published_at": video_data.get("uploadDate") if video_data else None,
            "transcript": transcript_text
        }

        self.db.save_video(video_record)
        print(f"    âœ“ TranscriÃ§Ã£o salva: {video_id}")

        # Gerar resumo com IA
        if video_data and transcript_text:
            print(f"    ðŸ¤– Gerando resumo com {self.config.OPENROUTER_MODEL}...")

            try:
                summary = self.openrouter.generate_summary(
                    transcript_text,
                    {
                        "title": video_data.get("title"),
                        "channel": video_data.get("channel"),
                        "duration": video_data.get("duration")
                    }
                )

                self.db.save_summary(video_id, summary, self.config.OPENROUTER_MODEL)
                print(f"    âœ“ Resumo gerado!")
            except Exception as e:
                print(f"    âœ— Erro ao gerar resumo: {e}")

# ============================================
# VISUALIZAR RESUMOS
# ============================================

def exibir_resumos_do_dia(config: Config):
    """Exibe os resumos do dia anterior."""
    db = Database(config.DB_PATH)
    summaries = db.get_yesterday_summaries()

    ontem = (datetime.now().date() - timedelta(days=1))

    print(f"\nðŸ“š RESUMOS DO DIA - {ontem.strftime('%d/%m/%Y')}")
    print("=" * 70)

    if not summaries:
        print("âŒ Nenhum resumo encontrado para esta data.")
        return

    print(f"ðŸ“Š {len(summaries)} vÃ­deo(s) resumido(s)\n")

    for i, s in enumerate(summaries, 1):
        print(f"\n{'â”€' * 70}")
        print(f"ðŸ“º #{i} {s['title']}")
        print(f"{'â”€' * 70}")
        print(s['summary'])

    print(f"\n{'=' * 70}")
    print(f"Total: {len(summaries)} resumos")


def _expand_date_range(start_date: str, end_date: str) -> List[str]:
    """Expande um intervalo de datas em uma lista YYYY-MM-DD (inclusivo)."""
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end < start:
        raise ValueError("A data final deve ser maior ou igual à data inicial.")

    result = []
    current = start
    while current <= end:
        result.append(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)
    return result


# ============================================
# EXECUTAR
# ============================================

if __name__ == "__main__":
    import argparse
    import asyncio

    # Carregar configuraÃ§Ãµes do .env
    from dotenv import load_dotenv
    load_dotenv()

    # ConfiguraÃ§Ã£o com variÃ¡veis de ambiente
    channels_env = os.getenv("CHANNELS", "").strip()
    if not channels_env:
        channels_env = os.getenv("CHANNEL_IDS", "").strip()

    config = Config(
        SUPADATA_API_KEY=os.getenv("SUPADATA_API_KEY", ""),
        YOUTUBE_API_KEY=os.getenv("YOUTUBE_API_KEY", ""),
        OPENROUTER_API_KEY=os.getenv("OPENROUTER_API_KEY", ""),
        OPENROUTER_MODEL=os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4-20250514"),
        CHANNELS=[c.strip() for c in channels_env.split(",") if c.strip()],
        DB_PATH=os.getenv("DB_PATH", "resumos.db"),
        TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        TELEGRAM_CHAT_ID=os.getenv("TELEGRAM_CHAT_ID", "")
    )

    # Parser de argumentos
    parser = argparse.ArgumentParser(description="Sistema de Resumos YouTube")
    parser.add_argument("--run", action="store_true", help="Executar busca e geraÃ§Ã£o de resumos")
    parser.add_argument("--view", action="store_true", help="Visualizar resumos do dia anterior")
    parser.add_argument("--telegram", action="store_true", help="Enviar resumos para Telegram")
    parser.add_argument("--date", type=str, help="Data especÃ­fica (YYYY-MM-DD)")
    parser.add_argument("--from-date", type=str, help="Data inicial do intervalo (YYYY-MM-DD)")
    parser.add_argument("--to-date", type=str, help="Data final do intervalo (YYYY-MM-DD)")

    args = parser.parse_args()

    has_video_source = bool(config.YOUTUBE_API_KEY or config.SUPADATA_API_KEY)

    if args.date and (args.from_date or args.to_date):
        print("❌ Use --date ou --from-date/--to-date, não ambos.")
        sys.exit(1)

    if (args.from_date and not args.to_date) or (args.to_date and not args.from_date):
        print("❌ Para intervalo, informe os dois argumentos: --from-date e --to-date.")
        sys.exit(1)

    selected_dates = None
    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
            selected_dates = [args.date]
        except ValueError:
            print("❌ Data inválida em --date. Use o formato YYYY-MM-DD.")
            sys.exit(1)
    elif args.from_date and args.to_date:
        try:
            selected_dates = _expand_date_range(args.from_date, args.to_date)
        except ValueError as e:
            print(f"❌ Intervalo inválido: {e}")
            sys.exit(1)

    if not has_video_source or not config.OPENROUTER_API_KEY:
        print("âŒ Erro: Configure OPENROUTER_API_KEY e ao menos uma fonte de vídeos (YOUTUBE_API_KEY ou SUPADATA_API_KEY) no arquivo .env")
        print("\nðŸ“„ Exemplo de arquivo .env:")
        print("""
SUPADATA_API_KEY=sua_chave_aqui
YOUTUBE_API_KEY=sua_chave_aqui
OPENROUTER_API_KEY=sua_chave_aqui
OPENROUTER_MODEL=anthropic/claude-sonnet-4-20250514
CHANNELS=@alanzoka,@cellbit,@outrocanal
TELEGRAM_BOT_TOKEN=seu_bot_token
TELEGRAM_CHAT_ID=seu_chat_id
DB_PATH=resumos.db
""")
    elif args.telegram:
        # Enviar para Telegram
        telegram = TelegramClient(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
        db = Database(config.DB_PATH)

        if selected_dates and len(selected_dates) == 1:
            summaries = db.get_summaries_by_published_date(selected_dates[0])
        elif selected_dates and len(selected_dates) > 1:
            summaries = db.get_summaries_by_published_date_range(selected_dates[0], selected_dates[-1])
        else:
            yesterday = (datetime.now().date() - timedelta(days=1)).strftime("%Y-%m-%d")
            summaries = db.get_summaries_by_published_date(yesterday)

        if summaries:
            print(f"ðŸ“¨ Enviando {len(summaries)} resumos para Telegram...")
            success = True
            for item in summaries:
                video_id = item.get("video_id")
                video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
                sent = asyncio.run(telegram.send_video_summary(
                    title=item.get("title", "Sem título"),
                    channel=item.get("channel_id", "N/A"),
                    summary=item.get("summary", ""),
                    video_url=video_url
                ))
                success = success and sent
                if not sent:
                    print(f"âš ï¸ Falha ao enviar: {item.get('title', 'Sem título')}")

            if success:
                print("âœ… Enviado com sucesso!")
            else:
                print("âŒ Falha ao enviar")
        else:
            if selected_dates and len(selected_dates) == 1:
                target = selected_dates[0]
            elif selected_dates and len(selected_dates) > 1:
                target = f"{selected_dates[0]} até {selected_dates[-1]}"
            else:
                target = "do dia anterior"
            print(f"âŒ Nenhum resumo encontrado para {target}")
    elif args.view:
        if args.date:
            db = Database(config.DB_PATH)
            summaries = db.get_summaries_by_date(args.date)
            if summaries:
                print(f"\nðŸ“š RESUMOS - {args.date}")
                print("=" * 70)
                for i, s in enumerate(summaries, 1):
                    print(f"\n{'â”€' * 70}")
                    print(f"ðŸ“º #{i} {s['title']}")
                    print(f"{'â”€' * 70}")
                    print(s['summary'])
            else:
                print(f"âŒ Nenhum resumo encontrado para {args.date}")
        else:
            exibir_resumos_do_dia(config)
    elif args.run:
        app = ResumoYouTube(config)
        asyncio.run(app.run(target_dates=selected_dates))
    else:
        # Modo interativo
        print("\nðŸ“š SISTEMA DE RESUMOS YOUTUBE")
        print("=" * 40)
        print("\nOpÃ§Ãµes:")
        print("  python main.py --run           # Buscar vÃ­deos de ontem e gerar resumos")
        print("  python main.py --view          # Visualizar resumos de ontem")
        print("  python main.py --telegram      # Enviar resumos para Telegram")
        print("  python main.py --view --date 2025-02-04  # Visualizar data especÃ­fica")
        print("\nExemplo de agendamento no Windows (Tarefa Agendada):")
        print('  python "C:\\caminho\\para\\main.py" --run')

