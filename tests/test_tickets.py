from agency.notifications.tickets import TicketRegistry


def test_open_or_bump_creates_a_new_ticket(tmp_path):
    registry = TicketRegistry(tmp_path)

    ticket, is_new = registry.open_or_bump("k1", "inference", "warning", "summary", {"a": 1})

    assert is_new is True
    assert ticket.dedup_key == "k1"
    assert ticket.occurrences == 1
    assert ticket.status == "open"
    assert registry.list_open() == [ticket]


def test_open_or_bump_bumps_existing_open_ticket_instead_of_duplicating(tmp_path):
    registry = TicketRegistry(tmp_path)
    first, _ = registry.open_or_bump("k1", "inference", "warning", "first summary", {"a": 1})

    second, is_new = registry.open_or_bump("k1", "inference", "critical", "second summary", {"a": 2})

    assert is_new is False
    assert second.ticket_id == first.ticket_id
    assert second.occurrences == 2
    assert second.summary == "second summary"
    assert second.severity == "critical"
    assert len(registry.list_open()) == 1


def test_resolve_marks_ticket_closed_and_a_new_occurrence_reopens_it(tmp_path):
    registry = TicketRegistry(tmp_path)
    ticket, _ = registry.open_or_bump("k1", "inference", "warning", "summary", {})

    registry.resolve("k1")
    assert registry.list_open() == []

    reopened, is_new = registry.open_or_bump("k1", "inference", "warning", "summary again", {})
    assert is_new is True  # the resolved one doesn't match "status == open", so a fresh ticket opens
    assert reopened.ticket_id != ticket.ticket_id


def test_set_message_id_persists_across_reads(tmp_path):
    registry = TicketRegistry(tmp_path)
    ticket, _ = registry.open_or_bump("k1", "inference", "warning", "summary", {})

    registry.set_message_id(ticket.ticket_id, "<abc@example.com>")

    assert registry.list_open()[0].message_id == "<abc@example.com>"


def test_list_open_excludes_resolved(tmp_path):
    registry = TicketRegistry(tmp_path)
    registry.open_or_bump("k1", "a", "info", "s1", {})
    registry.open_or_bump("k2", "b", "info", "s2", {})
    registry.resolve("k1")

    open_keys = {t.dedup_key for t in registry.list_open()}

    assert open_keys == {"k2"}
