from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Callable

from agency.notifications.tickets import Ticket


def _format_body(ticket: Ticket) -> str:
    lines = [
        f"category: {ticket.category}",
        f"severity: {ticket.severity}",
        f"occurrences: {ticket.occurrences}",
        f"first seen: {ticket.created_at}",
        f"last seen: {ticket.updated_at}",
        "",
        ticket.summary,
        "",
        json.dumps(ticket.detail, indent=2, default=str),
    ]
    return "\n".join(lines)


class EmailTicketNotifier:
    """Real SMTP (stdlib `smtplib`/`email`), not a fabricated vendor
    integration -- works with any mail provider the operator already has.
    Threading is plain RFC 5322 (`Message-ID`/`In-Reply-To`/`References`),
    so a ticket's updates land as one thread in any real mail client without
    needing an actual ticketing SaaS.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addr: str,
        use_tls: bool = True,
        smtp_client_factory: Callable[[], smtplib.SMTP] | None = None,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._from_addr = from_addr
        self._to_addr = to_addr
        self._use_tls = use_tls
        self._smtp_client_factory = smtp_client_factory or (lambda: smtplib.SMTP(self._host, self._port, timeout=10))

    def _send(self, msg: EmailMessage) -> None:
        with self._smtp_client_factory() as smtp:
            if self._use_tls:
                smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(msg)

    def notify_new(self, ticket: Ticket) -> str:
        msg = EmailMessage()
        msg["Subject"] = f"[agency][{ticket.severity}][{ticket.category}] {ticket.summary} (ticket {ticket.ticket_id})"
        msg["From"] = self._from_addr
        msg["To"] = self._to_addr
        message_id = make_msgid()
        msg["Message-ID"] = message_id
        msg.set_content(_format_body(ticket))
        self._send(msg)
        return message_id

    def notify_update(self, ticket: Ticket, in_reply_to: str | None) -> None:
        msg = EmailMessage()
        msg["Subject"] = f"Re: [agency][{ticket.severity}][{ticket.category}] {ticket.summary} (ticket {ticket.ticket_id})"
        msg["From"] = self._from_addr
        msg["To"] = self._to_addr
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(_format_body(ticket) + f"\n\n(occurrence #{ticket.occurrences})")
        self._send(msg)
