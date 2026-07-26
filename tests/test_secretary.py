from datetime import datetime, timezone

from agency.agents.secretary import AdminFinding, Secretary
from agency.org import Brand, Customer, Project


def _fixed_now(iso_date: str):
    return lambda: datetime.fromisoformat(iso_date).replace(tzinfo=timezone.utc)


def _brand_with_projects(projects):
    return Brand(
        slug="widgets",
        name="Widgets",
        postiz_group_id="g1",
        integration_ids=["i1"],
        voice="plain",
        audience="everyone",
        projects=projects,
    )


def test_check_deadlines_flags_overdue_project():
    project = Project(slug="launch", name="Launch", due_date="2026-07-01")
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([project])])
    secretary = Secretary(now=_fixed_now("2026-07-10"))

    findings = secretary.check_deadlines(customer)

    assert len(findings) == 1
    assert findings[0].severity == "overdue"
    assert "9 day" in findings[0].message


def test_check_deadlines_flags_upcoming_within_window():
    project = Project(slug="launch", name="Launch", due_date="2026-07-15")
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([project])])
    secretary = Secretary(now=_fixed_now("2026-07-10"))

    findings = secretary.check_deadlines(customer, warn_within_days=7)

    assert len(findings) == 1
    assert findings[0].severity == "warning"


def test_check_deadlines_ignores_far_future_and_missing_due_dates():
    far = Project(slug="far", name="Far", due_date="2027-01-01")
    none_due = Project(slug="none", name="None")
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([far, none_due])])
    secretary = Secretary(now=_fixed_now("2026-07-10"))

    assert secretary.check_deadlines(customer) == []


class FakePostizClient:
    def __init__(self, integrations_by_group):
        self._integrations_by_group = integrations_by_group

    def list_integrations(self, group=None):
        return self._integrations_by_group.get(group, [])


def test_check_platform_compliance_flags_disabled_integrations():
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([])])
    postiz = FakePostizClient({"g1": [{"name": "Twitter", "disabled": True}, {"name": "LinkedIn", "disabled": False}]})
    secretary = Secretary(postiz_client=postiz)

    findings = secretary.check_platform_compliance(customer)

    assert len(findings) == 1
    assert "Twitter" in findings[0].message


def test_check_platform_compliance_without_postiz_client_returns_empty():
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([])])
    secretary = Secretary(postiz_client=None)

    assert secretary.check_platform_compliance(customer) == []


class RaisingPostizClient:
    def list_integrations(self, group=None):
        import httpx

        raise httpx.ConnectError("connection refused")


def test_check_platform_compliance_surfaces_unreachable_postiz_as_a_finding_instead_of_raising():
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([])])
    secretary = Secretary(postiz_client=RaisingPostizClient())

    findings = secretary.check_platform_compliance(customer)

    assert len(findings) == 1
    assert findings[0].severity == "warning"
    assert "widgets" == findings[0].brand_slug
    assert "connection refused" in findings[0].message


def test_run_all_checks_combines_builtin_and_extra_checks():
    customer = Customer(slug="acme", name="Acme", brands=[_brand_with_projects([])])

    def extra_check(customer):
        return [AdminFinding(severity="info", category="paperwork", message="renew contract", brand_slug="widgets")]

    findings = Secretary(extra_checks=[extra_check]).run_all_checks(customer)

    assert len(findings) == 1
    assert findings[0].category == "paperwork"
