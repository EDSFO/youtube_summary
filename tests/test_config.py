from app.config import _dedupe_keep_order, _parse_channel_ids, _parse_topics


def test_parse_topics_splits_multiple_separators_and_deduplicates():
    topics = _parse_topics("IA, OpenAI\nmercado financeiro;IA")

    assert topics == ["IA", "OpenAI", "mercado financeiro"]


def test_parse_channel_ids_keeps_only_valid_ids():
    valid_a = "UC" + "a" * 22
    valid_b = "UC" + "b" * 22
    raw = f"{valid_a}, canal-invalido {valid_b}"

    channel_ids = _parse_channel_ids(raw)

    assert channel_ids == [valid_a, valid_b]


def test_dedupe_keep_order_preserves_first_occurrence():
    values = ["OpenAI", "IA", "OpenAI", "dados"]

    assert _dedupe_keep_order(values) == ["OpenAI", "IA", "dados"]
