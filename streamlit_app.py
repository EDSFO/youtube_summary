import os
import re
import asyncio
from datetime import date, timedelta

import httpx
import pandas as pd
import streamlit as st

from app.config import get_settings
from app.models.database import Database
from app.services.openrouter import OpenRouterService
from app.services.telegram import TelegramService
from app.services.youtube import YouTubeService

settings = get_settings()


def dedupe_keep_order(values):
    seen = set()
    ordered = []
    for value in values:
        cleaned = (value or "").strip()
        if cleaned and cleaned not in seen:
            ordered.append(cleaned)
            seen.add(cleaned)
    return ordered


def get_settings_list(settings_obj, list_attr_name, raw_attr_name):
    list_value = getattr(settings_obj, list_attr_name, None)
    if isinstance(list_value, (list, tuple, set)):
        return dedupe_keep_order(list(list_value))

    raw_value = getattr(settings_obj, raw_attr_name, "")
    if isinstance(raw_value, str) and raw_value.strip():
        return dedupe_keep_order(re.split(r"[\s,]+", raw_value.strip()))

    return []


def update_env_list(key, values):
    normalized_values = dedupe_keep_order(values)
    env_path = ".env"
    if not os.path.exists(env_path):
        with open(env_path, "w", encoding="utf-8") as file:
            file.write(f"{key}={','.join(normalized_values)}\n")
        return

    with open(env_path, "r", encoding="utf-8") as file:
        lines = file.readlines()

    line_prefix = f"{key}="
    replaced = False
    for idx, line in enumerate(lines):
        if line.startswith(line_prefix):
            lines[idx] = f"{line_prefix}{','.join(normalized_values)}\n"
            replaced = True
            break

    if not replaced:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(f"{line_prefix}{','.join(normalized_values)}\n")

    with open(env_path, "w", encoding="utf-8") as file:
        file.writelines(lines)


def update_env_channel_ids(channel_ids):
    update_env_list("CHANNEL_IDS", channel_ids)


def update_env_selected_channel_ids(channel_ids):
    update_env_list("SELECTED_CHANNEL_IDS", channel_ids)


def update_env_search_topics(topics):
    update_env_list("SEARCH_TOPICS", topics)


def persist_selected_channels(selected_channels):
    update_env_selected_channel_ids(selected_channels)


def parse_topics(raw_value):
    return dedupe_keep_order(
        [part.strip() for part in re.split(r"[\r\n,;]+", raw_value or "") if part.strip()]
    )


def apply_selected_channels(all_channel_ids, selected_channels):
    selected_set = set(dedupe_keep_order(selected_channels))
    st.session_state.channel_selection_map = {
        cid: (cid in selected_set) for cid in all_channel_ids
    }
    st.session_state.selected_channels = [
        cid for cid in all_channel_ids if cid in selected_set
    ]


def env_key_exists(key):
    env_path = ".env"
    if not os.path.exists(env_path):
        return False

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            return any(line.startswith(f"{key}=") for line in file.readlines())
    except Exception:
        return False


@st.cache_data(ttl=3600, show_spinner=False)
def get_channel_name_map(channel_ids):
    ids = dedupe_keep_order(channel_ids)
    if not ids:
        return {}, 0, ""

    channel_name_map = {}
    failed_chunks = 0
    last_error = ""
    try:
        yt_service = YouTubeService()
        for start in range(0, len(ids), 50):
            chunk = ids[start : start + 50]
            try:
                response = yt_service.youtube.channels().list(
                    part="snippet",
                    id=",".join(chunk),
                    maxResults=50,
                ).execute()
            except Exception as exc:
                # Ignora falha de um bloco e segue para os demais IDs.
                failed_chunks += 1
                last_error = str(exc)
                continue
            for item in response.get("items", []):
                channel_id = item.get("id", "")
                channel_title = item.get("snippet", {}).get("title", "")
                if channel_id and channel_title:
                    channel_name_map[channel_id] = channel_title
    except Exception as exc:
        return {}, max(1, failed_chunks), str(exc)

    return channel_name_map, failed_chunks, last_error


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
with st.expander("Funcao dos botoes (guia rapido)"):
    st.markdown(
        "- `Recarregar nomes dos canais`: atualiza os nomes dos canais exibidos na grade.\n"
        "- `Adicionar channel ID`: inclui um novo canal na lista carregada nesta sessao.\n"
        "- `Selecionar todos`: marca todos os canais na grade.\n"
        "- `Limpar selecao`: desmarca todos os canais na grade.\n"
        "- `Aplicar selecao da grade`: usa a selecao atual apenas nesta sessao.\n"
        "- `Fixar selecao no .env`: salva sua selecao para abrir o script ja com ela.\n"
        "- `Salvar channel IDs no .env`: salva a lista completa de canais no `CHANNEL_IDS`.\n"
        "- `Buscar videos`: busca videos no periodo e canais selecionados.\n"
        "- `IA` (em cada video): gera resumo com IA e tenta enviar para o Telegram."
    )

if not settings.youtube_api_key:
    st.error("YOUTUBE_API_KEY nao encontrado no .env")
    st.stop()

if "extra_channel_ids" not in st.session_state:
    st.session_state.extra_channel_ids = []
if "filtered_videos" not in st.session_state:
    st.session_state.filtered_videos = []
if "summary_results_map" not in st.session_state:
    st.session_state.summary_results_map = {}
if "selected_channels" not in st.session_state:
    st.session_state.selected_channels = []
if "channel_selection_map" not in st.session_state:
    st.session_state.channel_selection_map = {}
if "persisted_selection_loaded" not in st.session_state:
    st.session_state.persisted_selection_loaded = False
if "search_topics_text" not in st.session_state:
    st.session_state.search_topics_text = "\n".join(settings.search_topics_list)

base_channel_ids = get_settings_list(settings, "channel_ids_list", "channel_ids")
all_channel_ids = dedupe_keep_order(base_channel_ids + st.session_state.extra_channel_ids)
channel_name_map, channel_name_failed_chunks, channel_name_last_error = get_channel_name_map(
    tuple(all_channel_ids)
)

if not st.session_state.persisted_selection_loaded:
    has_saved_selection = env_key_exists("SELECTED_CHANNEL_IDS")
    persisted_selected_set = set(
        get_settings_list(settings, "selected_channel_ids_list", "selected_channel_ids")
    )
    if has_saved_selection:
        st.session_state.channel_selection_map = {
            cid: (cid in persisted_selected_set) for cid in all_channel_ids
        }
    else:
        st.session_state.channel_selection_map = {cid: True for cid in all_channel_ids}
    st.session_state.persisted_selection_loaded = True

# Sincroniza estado da grade: canais novos entram selecionados por padrão.
selection_map = st.session_state.channel_selection_map
synced_selection_map = {cid: selection_map.get(cid, True) for cid in all_channel_ids}
st.session_state.channel_selection_map = synced_selection_map
st.session_state.selected_channels = [
    cid for cid in all_channel_ids if synced_selection_map.get(cid, False)
]

with st.sidebar:
    st.subheader("Canais")
    if st.button(
        "Recarregar nomes dos canais",
        help="Atualiza os nomes dos canais na grade lateral.",
    ):
        get_channel_name_map.clear()
        st.rerun()

    if channel_name_failed_chunks > 0:
        st.warning(
            f"Falha ao carregar nomes de canais em {channel_name_failed_chunks} bloco(s)."
        )
        if channel_name_last_error:
            st.caption(channel_name_last_error[:220])

    new_channel_id = st.text_input("Novo Channel ID", placeholder="UC...")
    if st.button(
        "Adicionar channel ID",
        help="Inclui um novo canal na lista carregada nesta sessao.",
    ):
        if not new_channel_id.strip():
            st.warning("Informe um channel ID")
        else:
            st.session_state.extra_channel_ids = dedupe_keep_order(
                st.session_state.extra_channel_ids + [new_channel_id.strip()]
            )
            st.success("Channel ID adicionado")
            st.rerun()

    if all_channel_ids:
        action_col1, action_col2 = st.columns(2)
        with action_col1:
            if st.button(
                "Selecionar todos",
                help="Marca todos os canais na grade.",
            ):
                apply_selected_channels(all_channel_ids, all_channel_ids)
                st.rerun()
        with action_col2:
            if st.button(
                "Limpar selecao",
                help="Desmarca todos os canais na grade.",
            ):
                apply_selected_channels(all_channel_ids, [])
                st.rerun()

        st.markdown("### Canais adicionados")
        channel_df = pd.DataFrame(
            [
                {
                    "Selecionar": st.session_state.channel_selection_map.get(
                        channel_id, True
                    ),
                    "Canal": channel_name_map.get(channel_id, "Canal nao encontrado"),
                    "Channel ID": channel_id,
                }
                for channel_id in all_channel_ids
            ]
        )
        edited_channel_df = st.data_editor(
            channel_df,
            use_container_width=True,
            hide_index=True,
            height=360,
            disabled=["Canal", "Channel ID"],
            column_config={
                "Selecionar": st.column_config.CheckboxColumn(
                    "Selecionar",
                    help="Marque os canais que participam da busca por data.",
                    default=True,
                )
            },
            key="channels_grid_editor",
        )

        selected_from_grid = edited_channel_df.loc[
            edited_channel_df["Selecionar"] == True, "Channel ID"
        ].tolist()
        grid_action_col1, grid_action_col2 = st.columns(2)
        with grid_action_col1:
            if st.button(
                "Aplicar selecao da grade",
                help="Aplica a selecao atual da grade para a busca desta sessao.",
            ):
                apply_selected_channels(all_channel_ids, selected_from_grid)
                st.success(
                    f"{len(st.session_state.selected_channels)} canais selecionados para consulta."
                )
                st.rerun()
        with grid_action_col2:
            if st.button(
                "Fixar selecao no .env",
                help="Salva sua selecao atual no SELECTED_CHANNEL_IDS para reutilizar ao reabrir.",
            ):
                apply_selected_channels(all_channel_ids, selected_from_grid)
                persist_selected_channels(st.session_state.selected_channels)
                st.success(
                    "Selecao fixada no .env. Na proxima abertura ela sera carregada automaticamente."
                )
                st.rerun()

        selected_channels = st.session_state.selected_channels
        st.caption(
            f"Total carregado: {len(all_channel_ids)} | Selecionados para busca: {len(selected_channels)}"
        )
    else:
        selected_channels = []
        st.info("Nenhum channel ID configurado")

    if st.button(
        "Salvar channel IDs no .env",
        help="Grava a lista completa de canais carregados no CHANNEL_IDS.",
    ):
        update_env_channel_ids(all_channel_ids)
        st.success("CHANNEL_IDS salvo no .env")

col1, col2 = st.columns([1, 1])
with col1:
    start_date = st.date_input("Data inicial", value=date.today() - timedelta(days=7))
with col2:
    end_date = st.date_input("Data final", value=date.today())

st.subheader("Filtro por assuntos")
st.caption(
    "Cadastre um assunto por linha. A busca considera titulo e descricao do video."
)
st.text_area(
    "Assuntos",
    key="search_topics_text",
    height=140,
    placeholder="Ex.: IA\nOpenAI\nmercado financeiro",
)
topic_list = parse_topics(st.session_state.search_topics_text)
topic_actions_col1, topic_actions_col2 = st.columns(2)
with topic_actions_col1:
    if st.button(
        "Salvar assuntos no .env",
        help="Persiste os assuntos para reutilizar nas proximas buscas.",
    ):
        update_env_search_topics(topic_list)
        st.success("Assuntos salvos no .env")
with topic_actions_col2:
    if topic_list:
        st.caption(f"{len(topic_list)} assunto(s) ativo(s) nesta busca")
    else:
        st.caption("Sem filtro de assuntos. A busca retorna todos os videos do periodo.")

if isinstance(start_date, tuple):
    start_date = start_date[0]
if isinstance(end_date, tuple):
    end_date = end_date[0]

if st.button(
    "Buscar videos",
    type="primary",
    help="Busca videos no periodo informado usando somente os canais selecionados.",
):
    if not selected_channels:
        st.warning("Adicione ou selecione ao menos um channel ID")
    else:
        with st.spinner("Consultando YouTube..."):
            yt_service = YouTubeService()
            videos = yt_service.get_videos_between_dates(
                start_date=start_date,
                end_date=end_date,
                channel_ids=selected_channels,
                topics=topic_list,
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
