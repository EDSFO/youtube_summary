from app.services.youtube import YouTubeService


def build_service_without_init():
    return object.__new__(YouTubeService)


def test_normalize_topics_removes_accents_and_duplicates():
    service = build_service_without_init()

    topics = service._normalize_topics(["Inteligência Artificial", "inteligencia artificial", "OpenAI"])

    assert topics == ["inteligencia artificial", "openai"]


def test_matches_topics_uses_title_and_description():
    service = build_service_without_init()
    video = {
        "title": "Panorama de OpenAI para empresas",
        "description": "Resumo do impacto de IA no mercado financeiro.",
    }

    assert service._matches_topics(video, ["mercado financeiro"])
    assert service._matches_topics(video, ["OpenAI"])
    assert not service._matches_topics(video, ["biologia"])


def test_matches_topics_returns_true_when_no_topic_filter_is_provided():
    service = build_service_without_init()
    video = {"title": "Qualquer titulo", "description": "Qualquer descricao"}

    assert service._matches_topics(video, [])
    assert service._matches_topics(video, None)
