from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

import httpx

from agency.org import Customer
from agency.postiz.client import PostizClient


@dataclass
class AdminFinding:
    severity: str  # "info" | "warning" | "overdue"
    category: str
    message: str
    brand_slug: str
    project_slug: str | None = None


AdminCheck = Callable[[Customer], list[AdminFinding]]


class Secretary:
    """One per customer: day-to-day admin.

    Ships two concrete, verifiable checks -- deadlines (from each brand's
    Project.due_date) and platform compliance (Postiz integration health,
    via the same GET /public/v1/integrations used elsewhere in this repo).
    Paperwork, bookkeeping/accounting, and security/authentication admin
    have no concrete system in this repo to integrate with (no accounting
    API, no auth provider was specified), so rather than fabricate one,
    Secretary accepts `extra_checks`: bring your own AdminCheck callables
    once you have a real system to point at.
    """

    def __init__(
        self,
        postiz_client: PostizClient | None = None,
        extra_checks: list[AdminCheck] | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        self._postiz = postiz_client
        self._extra_checks = extra_checks or []
        self._now = now

    def check_deadlines(self, customer: Customer, warn_within_days: int = 7) -> list[AdminFinding]:
        today = self._now().date()
        findings = []
        for brand in customer.brands:
            for project in brand.projects:
                if not project.due_date:
                    continue
                due = date.fromisoformat(project.due_date)
                days_left = (due - today).days
                if days_left < 0:
                    findings.append(
                        AdminFinding(
                            severity="overdue",
                            category="deadline",
                            message=f"'{project.name}' was due {due.isoformat()} ({-days_left} day(s) ago)",
                            brand_slug=brand.slug,
                            project_slug=project.slug,
                        )
                    )
                elif days_left <= warn_within_days:
                    findings.append(
                        AdminFinding(
                            severity="warning",
                            category="deadline",
                            message=f"'{project.name}' is due {due.isoformat()} (in {days_left} day(s))",
                            brand_slug=brand.slug,
                            project_slug=project.slug,
                        )
                    )
        return findings

    def check_platform_compliance(self, customer: Customer) -> list[AdminFinding]:
        if self._postiz is None:
            return []
        findings = []
        for brand in customer.brands:
            try:
                integrations = self._postiz.list_integrations(group=brand.postiz_group_id)
            except httpx.HTTPError as exc:
                # Postiz being unreachable must not take down the whole
                # admin report -- surface it as a finding (same severity
                # class as a disabled integration) instead of crashing.
                findings.append(
                    AdminFinding(
                        severity="warning",
                        category="platform_compliance",
                        message=f"Could not reach Postiz to check integrations: {exc}",
                        brand_slug=brand.slug,
                    )
                )
                continue
            for integration in integrations:
                if integration.get("disabled"):
                    findings.append(
                        AdminFinding(
                            severity="warning",
                            category="platform_compliance",
                            message=f"Integration '{integration.get('name')}' is disabled -- reconnect in Postiz",
                            brand_slug=brand.slug,
                        )
                    )
        return findings

    def run_all_checks(self, customer: Customer) -> list[AdminFinding]:
        findings = self.check_deadlines(customer) + self.check_platform_compliance(customer)
        for check in self._extra_checks:
            findings.extend(check(customer))
        return findings
