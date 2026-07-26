from __future__ import annotations

import sys

from agency.notifications.email_notifier import EmailTicketNotifier
from agency.notifications.telegram import TelegramNotifier
from agency.notifications.tickets import TicketRegistry

# event -> (category, severity) for run-ledger events that should become a
# ticket. "compose_done" is handled separately below since only its
# escalated=True case is notification-worthy, not every field-shape.
_TICKET_EVENTS = {
    "local_inference_unavailable": ("inference", "warning"),
    "finalize_ambiguous_skip": ("publish-integrity", "critical"),
    "fallback_used": ("cloud-fallback", "info"),
}


class Director:
    """Owns workflow/process and is the one place both notification
    channels meet: Telegram for the human (escalations, daily progress,
    monetization summaries -- low-volume, high-signal), email tickets for
    everything else operational (fallback usage, ambiguous publishes,
    local-inference outages -- the stuff DevOps/an on-call human would want
    a ticket for, not a phone buzz). Both are optional; a Director built
    with neither configured is a safe no-op, so running without
    SMTP/Telegram set up in `.env` degrades to "nothing gets sent," not a
    crash.
    """

    def __init__(
        self,
        telegram: TelegramNotifier | None = None,
        tickets: TicketRegistry | None = None,
        email: EmailTicketNotifier | None = None,
    ):
        self._telegram = telegram
        self._tickets = tickets
        self._email = email

    def _send_telegram(self, text: str) -> None:
        # Best-effort, like illustrate_node's ComfyUI call in graph.py: a
        # dead/misconfigured Telegram bot or network blip must never take
        # down run_all, admin-report, devops-health, etc. -- notifying is an
        # enhancement layered on top of the real work, not part of it.
        try:
            self._telegram.send(text)
        except Exception as exc:
            print(f"Director: failed to send Telegram message: {exc}", file=sys.stderr)

    def escalate_to_human(self, fields: dict) -> None:
        if self._telegram is None:
            return
        label = str(fields.get("customer", "?")) + "/" + str(fields.get("brand", "?"))
        if fields.get("project"):
            label += f"/{fields['project']}"
        text = (
            f"Escalation: {label}\n"
            f"topic: {fields.get('topic', '?')}\n"
            f"thread: {fields.get('thread_id', '?')}\n"
            f"Resolve with: agency resume --thread-id {fields.get('thread_id', '?')} --approve|--reject"
        )
        self._send_telegram(text)

    def daily_summary(self, text: str) -> None:
        if self._telegram is not None:
            self._send_telegram(f"Daily summary\n{text}")

    def monetization_summary(self, text: str) -> None:
        """Sends whatever summary text the caller built. No revenue/analytics
        integration is wired in here -- Postiz's public API doesn't document
        a verified monetization endpoint, and no accounting/revenue system
        was specified, so rather than fabricate one, this is a bring-your-own
        extension point: point `agency director-monetization-report` at a
        file, or feed it real numbers once a real source exists.
        """
        if self._telegram is not None:
            self._send_telegram(f"Monetization summary\n{text}")

    def open_ticket(self, dedup_key: str, category: str, severity: str, summary: str, detail: dict) -> None:
        if self._tickets is None:
            return
        ticket, is_new = self._tickets.open_or_bump(dedup_key, category, severity, summary, detail)
        if self._email is None:
            return
        # Same best-effort principle as _send_telegram: an unreachable SMTP
        # host must not break whatever primary command triggered the ticket.
        try:
            if is_new:
                message_id = self._email.notify_new(ticket)
                self._tickets.set_message_id(ticket.ticket_id, message_id)
            else:
                self._email.notify_update(ticket, in_reply_to=ticket.message_id)
        except Exception as exc:
            print(f"Director: failed to send ticket email: {exc}", file=sys.stderr)

    def handle_ledger_event(self, event: str, fields: dict) -> None:
        """Wired as `RunLedger`'s `on_event` hook: every notable event in the
        system already flows through the ledger, so subscribing here -- and
        nowhere else -- is what lets run.py/graph.py stay untouched by this
        entire notification layer.
        """
        if event == "compose_done" and fields.get("escalated"):
            self.escalate_to_human(fields)
            return
        if event not in _TICKET_EVENTS:
            return
        category, severity = _TICKET_EVENTS[event]
        brand = fields.get("brand") or fields.get("command") or "unknown"
        dedup_key = f"{event}:{brand}"
        summary = f"{event.replace('_', ' ')} ({brand})"
        self.open_ticket(dedup_key, category, severity, summary, detail=fields)
