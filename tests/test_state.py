from agency.brand import load_brand
from agency.state import TopicHistory


def test_recent_is_empty_before_any_record(tmp_path):
    brand = load_brand("brands/example_brand.yaml")
    assert TopicHistory(tmp_path, brand).recent() == []


def test_record_then_recent_round_trips(tmp_path):
    brand = load_brand("brands/example_brand.yaml")
    history = TopicHistory(tmp_path, brand)

    history.record("topic a")
    history.record("topic b")

    assert TopicHistory(tmp_path, brand).recent() == ["topic a", "topic b"]


def test_recent_respects_limit(tmp_path):
    brand = load_brand("brands/example_brand.yaml")
    history = TopicHistory(tmp_path, brand)
    for i in range(5):
        history.record(f"topic {i}")

    assert history.recent(limit=2) == ["topic 3", "topic 4"]
