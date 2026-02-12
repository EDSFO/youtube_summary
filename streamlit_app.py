import os
import asyncio
from datetime import date, timedelta

import httpx
import streamlit as st
from dotenv import load_dotenv

from app.config import settings
from app.models.database import Database
from app.services.openrouter import OpenRouterService
from app.services.telegram import TelegramService
from app.services.youtube import YouTubeService

load_dotenv()


def dedupe_keep_order(values):
    seen = set()
    ordered = []
    for value in values:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in seen:
            ordered.append(cleaned)
            seen.add(cleaned)
    return ordered


def update_env_channel_ids(channel_ids):
    env_path = ".env"
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as file:
            file.write(f"CHANNEL_IDS={','.join(channel_ids)}\n")
        return

    with open(env_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    key = "CHANNEL_IDS="
    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(key):
            lines[idx] = f"{key}{','.join(channel_ids)}\n"
            replaced = True
            break

    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{key}{','.join(channel_ids)}\n")

    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(lines)


async def summarize_single_video(video):
    db = Database()
    await db.init_db()

    saved_map = await db.get_summaries_by_video_ids([video["video_id"]])
    saved = saved_map.get(video["video_id"])
    if saved:
        return {
            **video,
            "summary": saved.get("summary", ""),
            "model_used": saved.get("model_used", ""),
            "created_at": saved.get("created_at", ""),
        }, False

    openrouter = OpenRouterService()
    summary = openrouter.summarize_with_fallback(
        video.get("title", ""),
        video.get("description", ""),
        video["video_id"],
    )

    await db.mark_video_processed(
        video["video_id"],
        video.get("title", ""),
        video.get("channel_id", ""),
        video.get("channel_title", ""),
        summary,
        openrouter.model,
    )

    saved_map = await db.get_summaries_by_video_ids([video["video_id"]])
    saved = saved_map.get(video["video_id"], {})
    return {
        **video,
        "summary": saved.get("summary", summary),
        "model_used": saved.get("model_used", openrouter.model),
        "created_at": saved.get("created_at", ""),
    }, True


async def send_summary_to_telegram(video_with_summary):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return False, "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID nao configurados no .env"

    telegram = TelegramService()
    ok = await telegram.send_video_summary(
        video_with_summary, video_with_summary.get("summary", "")
    )
    if ok:
        return True, ""

    # Fallback: direct Telegram Bot API call with detailed error text.
    title = video_with_summary.get("title", "Sem titulo")
    channel = video_with_summary.get("channel_title", "N/A")
    video_id = video_with_summary.get("video_id")
    video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else ""
    summary_text = (video_with_summary.get("summary", "") or "").strip()
    if len(summary_text) > 3000:
        summary_text = summary_text[:3000].rstrip() + "..."

    message = (
        f"🖥️ {title}\n\n"
        f"Canal: {channel}\n\n"
        f"📝 Resumo:\n{summary_text}\n\n"
        f"Assistir no YouTube\n{video_url}"
    )

    endpoint = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                endpoint,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": message,
                    "disable_web_page_preview": False,
                },
            )
        if response.is_success:
            return True, ""
        return False, f"HTTP {response.status_code}: {response.text[:400]}"
    except Exception as exc:
        return False, str(exc)


st.set_page_config(page_title="Resumo YouTube", layout="wide")
st.title("Painel de Verificacao de Videos")
st.caption("Escolha periodo e canais para conferir se houve publicacoes")

if not settings.youtube_api_key:
    st.error("YOUTUBE_API_KEY nao encontrado no .env")
    st.stop()

if "extra_channel_ids" not in st.session_state:
    st.session_state.extra_channel_ids = []
if "filtered_videos" not in st.session_state:
    st.session_state.filtered_videos = []
if "summary_results_map" not in st.session_state:
    st.session_state.summary_results_map = {}

base_channel_ids = settings.channel_ids_list
all_channel_ids = dedupe_keep_order(base_channel_ids + st.session_state.extra_channel_ids)

with st.sidebar:
    st.subheader("Canais")
    new_channel_id = st.text_input("Novo Channel ID", placeholder="UC...")
    if st.button("Adicionar channel ID"):
        if not new_channel_id.strip():
            st.warning("Informe um channel ID")
        else:
            st.session_state.extra_channel_ids = dedupe_keep_order(
                st.session_state.extra_channel_ids + [new_channel_id.strip()]
            )
            st.success("Channel ID adicionado")
            st.rerun()

    if all_channel_ids:
        selected_channels = st.multiselect(
            "Channel IDs para consultar",
            options=all_channel_ids,
            default=all_channel_ids,
        )
    else:
        selected_channels = []
        st.info("Nenhum channel ID configurado")

    if st.button("Salvar channel IDs no .env"):
        update_env_channel_ids(all_channel_ids)
        st.success("CHANNEL_IDS salvo no .env")

col1, col2 = st.columns([1, 1])
with col1:
    start_date = st.date_input("Data inicial", value=date.today() - timedelta(days=7))
with col2:
    end_date = st.date_input("Data final", value=date.today())

if isinstance(start_date, tuple):
    start_date = start_date[0]
if isinstance(end_date, tuple):
    end_date = end_date[0]

if st.button("Buscar videos", type="primary"):
    if not selected_channels:
        st.warning("Adicione ou selecione ao menos um channel ID")
    else:
        with st.spinner("Consultando YouTube..."):
            yt_service = YouTubeService()
            videos = yt_service.get_videos_between_dates(
                start_date=start_date,
                end_date=end_date,
                channel_ids=selected_channels,
            )

        st.session_state.filtered_videos = videos
        st.session_state.summary_results_map = {}

if st.session_state.filtered_videos:
    st.success(f"{len(st.session_state.filtered_videos)} video(s) encontrado(s)")

    st.subheader("Videos encontrados")
    header_cols = st.columns([1.2, 3.8, 2, 2, 1])
    header_cols[0].markdown("**Capa**")
    header_cols[1].markdown("**Titulo**")
    header_cols[2].markdown("**Canal**")
    header_cols[3].markdown("**Publicado (UTC)**")
    header_cols[4].markdown("**Resumo**")
    st.divider()

    for item in st.session_state.filtered_videos:
        row_col1, row_col2, row_col3, row_col4, row_col5 = st.columns([1.2, 3.8, 2, 2, 1])
        with row_col1:
            thumbnail_url = item.get("thumbnail")
            if thumbnail_url:
                st.image(thumbnail_url, width=120)
        with row_col2:
            st.markdown(
                f"[{item['title']}](https://www.youtube.com/watch?v={item['video_id']})"
            )
        with row_col3:
            st.write(item["channel_title"])
        with row_col4:
            st.write(item["published_at"])
        with row_col5:
            button_key = f"summary_btn_{item['video_id']}"
            if st.button("IA", key=button_key, help="Gerar resumo com IA"):
                if not settings.openrouter_api_key:
                    st.error("OPENROUTER_API_KEY nao encontrado no .env")
                else:
                    with st.spinner("Gerando resumo..."):
                        summary_item, created_now = asyncio.run(summarize_single_video(item))
                        sent_ok, sent_error = asyncio.run(send_summary_to_telegram(summary_item))
                    st.session_state.summary_results_map[item["video_id"]] = summary_item
                    if created_now:
                        st.success("Resumo gerado e salvo no banco.")
                    else:
                        st.info("Resumo ja existente. Exibindo resultado salvo.")
                    if sent_ok:
                        st.success("Resumo enviado para o Telegram.")
                    else:
                        st.warning(f"Resumo nao foi enviado ao Telegram: {sent_error}")
        st.divider()

all_summaries_to_show = st.session_state.summary_results_map

if all_summaries_to_show:
    st.subheader("Resumos Gerados")
    for item in all_summaries_to_show.values():
        st.markdown(f"### {item['title']}")
        st.write(f"Canal: {item['channel_title']}")
        st.write(f"Video: https://www.youtube.com/watch?v={item['video_id']}")
        if item.get("created_at"):
            st.write(f"Criado em: {item['created_at']}")
        if item.get("model_used"):
            st.write(f"Modelo: {item['model_used']}")
        st.write(item.get("summary", "Sem resumo"))
        st.divider()
