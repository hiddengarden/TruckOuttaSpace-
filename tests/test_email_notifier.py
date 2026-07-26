from agency.notifications.email_notifier import EmailTicketNotifier
from agency.notifications.tickets import Ticket


class FakeSmtp:
    """Stands in for smtplib.SMTP -- real smtplib.SMTP supports `with`
    (calls quit() on exit), so this mirrors that shape rather than mocking
    smtplib internals directly."""

    def __init__(self):
        self.starttls_called = False
        self.login_calls = []
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.starttls_called = True

    def login(self, username, password):
        self.login_calls.append((username, password))

    def send_message(self, msg):
        self.sent.append(msg)


def _ticket(**overrides):
    base = dict(
        ticket_id="tk123",
        dedup_key="local_inference_unavailable:acme",
        category="inference",
        severity="warning",
        summary="local inference unavailable (acme)",
        detail={"brand": "acme"},
        status="open",
        occurrences=1,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        message_id=None,
    )
    base.update(overrides)
    return Ticket(**base)


def _notifier(fake_smtp, **kwargs):
    defaults = dict(
        host="smtp.example.com", port=587, username="user", password="pw",
        from_addr="agency@example.com", to_addr="ops@example.com", use_tls=True,
        smtp_client_factory=lambda: fake_smtp,
    )
    defaults.update(kwargs)
    return EmailTicketNotifier(**defaults)


def test_notify_new_sends_with_starttls_login_and_subject_containing_ticket_id():
    fake_smtp = FakeSmtp()
    notifier = _notifier(fake_smtp)

    message_id = notifier.notify_new(_ticket())

    assert fake_smtp.starttls_called is True
    assert fake_smtp.login_calls == [("user", "pw")]
    assert len(fake_smtp.sent) == 1
    msg = fake_smtp.sent[0]
    assert "tk123" in msg["Subject"]
    assert "warning" in msg["Subject"]
    assert "inference" in msg["Subject"]
    assert msg["From"] == "agency@example.com"
    assert msg["To"] == "ops@example.com"
    assert msg["Message-ID"] == message_id


def test_notify_new_skips_login_when_no_username_configured():
    fake_smtp = FakeSmtp()
    notifier = _notifier(fake_smtp, username="", password="")

    notifier.notify_new(_ticket())

    assert fake_smtp.login_calls == []


def test_notify_new_skips_starttls_when_use_tls_false():
    fake_smtp = FakeSmtp()
    notifier = _notifier(fake_smtp, use_tls=False)

    notifier.notify_new(_ticket())

    assert fake_smtp.starttls_called is False


def test_notify_update_threads_via_in_reply_to_and_marks_subject_as_reply():
    fake_smtp = FakeSmtp()
    notifier = _notifier(fake_smtp)

    notifier.notify_update(_ticket(occurrences=3), in_reply_to="<first@example.com>")

    msg = fake_smtp.sent[0]
    assert msg["Subject"].startswith("Re:")
    assert msg["In-Reply-To"] == "<first@example.com>"
    assert msg["References"] == "<first@example.com>"
    assert "occurrence #3" in msg.get_content()
