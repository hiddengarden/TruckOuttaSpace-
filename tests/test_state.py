from agency.state import TopicHistory


def test_recent_is_empty_before_any_record(tmp_path):
    assert TopicHistory(tmp_path, "acme", "widgets", None).recent() == []


def test_record_then_recent_round_trips(tmp_path):
    history = TopicHistory(tmp_path, "acme", "widgets", None)

    history.record("topic a")
    history.record("topic b")

    assert TopicHistory(tmp_path, "acme", "widgets", None).recent() == ["topic a", "topic b"]


def test_recent_respects_limit(tmp_path):
    history = TopicHistory(tmp_path, "acme", "widgets", None)
    for i in range(5):
        history.record(f"topic {i}")

    assert history.recent(limit=2) == ["topic 3", "topic 4"]


def test_project_scoping_keeps_histories_separate(tmp_path):
    default_history = TopicHistory(tmp_path, "acme", "widgets", None)
    project_history = TopicHistory(tmp_path, "acme", "widgets", "fall-launch")

    default_history.record("default topic")
    project_history.record("project topic")

    assert default_history.recent() == ["default topic"]
    assert project_history.recent() == ["project topic"]
