from agency.agents.director import Director
from agency.notifications.tickets import Ticket


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


class FakeTickets:
    def __init__(self):
        self.calls = []
        self._next_is_new = True

    def open_or_bump(self, dedup_key, category, severity, summary, detail):
        self.calls.append((dedup_key, category, severity, summary, detail))
        ticket = Ticket(
            ticket_id="tk1", dedup_key=dedup_key, category=category, severity=severity,
            summary=summary, detail=detail, occurrences=1 if self._next_is_new else 2,
            created_at="t", updated_at="t", message_id=None if self._next_is_new else "<first@x>",
        )
        is_new = self._next_is_new
        self._next_is_new = False
        return ticket, is_new

    def set_message_id(self, ticket_id, message_id):
        self.message_id_set = (ticket_id, message_id)


class FakeEmail:
    def __init__(self):
        self.new_calls = []
        self.update_calls = []

    def notify_new(self, ticket):
        self.new_calls.append(ticket)
        return "<generated@x>"

    def notify_update(self, ticket, in_reply_to):
        self.update_calls.append((ticket, in_reply_to))


# --- escalate_to_human / compose_done routing ---


def test_handle_ledger_event_escalates_on_compose_done_with_escalated_true():
    telegram = FakeTelegram()
    director = Director(telegram=telegram)

    director.handle_ledger_event(
        "compose_done",
        {"thread_id": "t1", "customer": "acme", "brand": "widgets", "project": None, "topic": "launch", "escalated": True},
    )

    assert len(telegram.sent) == 1
    assert "t1" in telegram.sent[0]
    assert "acme/widgets" in telegram.sent[0]
    assert "launch" in telegram.sent[0]


def test_handle_ledger_event_does_not_escalate_when_not_escalated():
    telegram = FakeTelegram()
    director = Director(telegram=telegram)

    director.handle_ledger_event("compose_done", {"thread_id": "t1", "escalated": False})

    assert telegram.sent == []


def test_escalate_to_human_is_a_safe_noop_without_telegram_configured():
    director = Director()  # nothing configured

    director.handle_ledger_event("compose_done", {"thread_id": "t1", "escalated": True})  # must not raise


# --- ticket-worthy ledger events ---


def test_handle_ledger_event_opens_ticket_for_local_inference_unavailable():
    tickets, email = FakeTickets(), FakeEmail()
    director = Director(tickets=tickets, email=email)

    director.handle_ledger_event("local_inference_unavailable", {"brand": "acme", "error": "connection refused"})

    assert len(tickets.calls) == 1
    dedup_key, category, severity, summary, detail = tickets.calls[0]
    assert dedup_key == "local_inference_unavailable:acme"
    assert category == "inference"
    assert severity == "warning"
    assert len(email.new_calls) == 1


def test_handle_ledger_event_opens_critical_ticket_for_ambiguous_finalize():
    tickets, email = FakeTickets(), FakeEmail()
    director = Director(tickets=tickets, email=email)

    director.handle_ledger_event("finalize_ambiguous_skip", {"brand": "acme"})

    _, category, severity, _, _ = tickets.calls[0]
    assert category == "publish-integrity"
    assert severity == "critical"


def test_handle_ledger_event_ignores_irrelevant_events():
    tickets = FakeTickets()
    director = Director(tickets=tickets)

    director.handle_ledger_event("finalize_done", {"brand": "acme", "published": True})

    assert tickets.calls == []


def test_open_ticket_sends_threaded_update_on_second_occurrence():
    tickets, email = FakeTickets(), FakeEmail()
    director = Director(tickets=tickets, email=email)

    director.open_ticket("k1", "inference", "warning", "s1", {})
    director.open_ticket("k1", "inference", "warning", "s2", {})

    assert len(email.new_calls) == 1
    assert len(email.update_calls) == 1
    ticket, in_reply_to = email.update_calls[0]
    assert in_reply_to == "<first@x>"


def test_open_ticket_is_a_safe_noop_without_tickets_configured():
    director = Director()

    director.handle_ledger_event("local_inference_unavailable", {"brand": "acme"})  # must not raise


# --- daily / monetization summaries ---


def test_daily_summary_sends_via_telegram():
    telegram = FakeTelegram()
    director = Director(telegram=telegram)

    director.daily_summary("3 posts published, 1 escalation")

    assert telegram.sent == ["Daily summary\n3 posts published, 1 escalation"]


def test_monetization_summary_sends_via_telegram():
    telegram = FakeTelegram()
    director = Director(telegram=telegram)

    director.monetization_summary("revenue: $500")

    assert telegram.sent == ["Monetization summary\nrevenue: $500"]


# --- notification failures must never propagate to the caller ---


class RaisingTelegram:
    def send(self, text):
        raise ConnectionError("telegram unreachable")


class RaisingEmail:
    def notify_new(self, ticket):
        raise OSError("smtp unreachable")

    def notify_update(self, ticket, in_reply_to):
        raise OSError("smtp unreachable")


def test_escalate_to_human_does_not_raise_when_telegram_send_fails():
    director = Director(telegram=RaisingTelegram())

    director.handle_ledger_event(
        "compose_done", {"thread_id": "t1", "customer": "acme", "brand": "widgets", "escalated": True}
    )  # must not raise


def test_daily_summary_does_not_raise_when_telegram_send_fails():
    Director(telegram=RaisingTelegram()).daily_summary("text")  # must not raise


def test_open_ticket_does_not_raise_when_email_send_fails():
    tickets = FakeTickets()
    director = Director(tickets=tickets, email=RaisingEmail())

    director.open_ticket("k1", "inference", "warning", "s", {})  # must not raise
