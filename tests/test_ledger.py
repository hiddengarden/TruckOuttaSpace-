from agency.ledger import RunLedger


def test_record_writes_a_json_line(tmp_path):
    ledger = RunLedger(tmp_path)

    ledger.record("compose_done", thread_id="t1", approved=True)

    entries = ledger.read_all()
    assert len(entries) == 1
    assert entries[0]["event"] == "compose_done"
    assert entries[0]["thread_id"] == "t1"
    assert "timestamp" in entries[0]


def test_record_calls_on_event_hook_with_event_and_fields(tmp_path):
    calls = []
    ledger = RunLedger(tmp_path, on_event=lambda event, fields: calls.append((event, fields)))

    ledger.record("fallback_used", brand="acme", detail="timeout")

    assert calls == [("fallback_used", {"brand": "acme", "detail": "timeout"})]


def test_record_without_on_event_hook_does_not_error(tmp_path):
    ledger = RunLedger(tmp_path)

    ledger.record("compose_done", thread_id="t1")  # must not raise
