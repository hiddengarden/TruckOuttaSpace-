from pydantic import BaseModel, ValidationError


class IntegrationRecord(BaseModel):
    """The fields this codebase actually reads from a `/public/v1/integrations`
    entry -- not an attempt to mirror Postiz's full internal DTO. Grounded
    against `apps/backend/src/api/routes/integrations.controller.ts`'s
    `getIntegrationList` (id/name/disabled all present there); the specific
    public-api serializer for `/public/v1/integrations` couldn't be fetched
    directly (blocked by this environment's proxy), so this is the fields
    `Secretary.check_platform_compliance` depends on today, not a complete
    schema. Extra fields are ignored by default (pydantic v2), so a field
    Postiz adds later doesn't fail this check -- only a field we depend on
    disappearing or changing type does.
    """

    id: str
    name: str
    disabled: bool = False


def validate_integrations_contract(integrations: list[dict]) -> list[str]:
    """Run at preflight against a real, already-fetched `/public/v1/integrations`
    response: catches the wire contract drifting out from under
    Secretary/PostizClient loudly, before a scheduled run trusts it, rather
    than discovering it as a confusing KeyError mid-batch."""
    problems: list[str] = []
    for i, entry in enumerate(integrations):
        try:
            IntegrationRecord.model_validate(entry)
        except ValidationError as exc:
            label = entry.get("id", f"index {i}")
            problems.append(f"integration {label}: {exc}")
    return problems
