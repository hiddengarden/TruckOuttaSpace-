from agency.postiz.contract import validate_integrations_contract


def test_validate_integrations_contract_passes_for_well_formed_entries():
    integrations = [
        {"id": "int_1", "name": "Twitter", "disabled": False},
        {"id": "int_2", "name": "LinkedIn", "disabled": True},
    ]

    assert validate_integrations_contract(integrations) == []


def test_validate_integrations_contract_tolerates_extra_unknown_fields():
    # Postiz adding a new field must not fail this check -- only fields we
    # actually depend on disappearing/changing type should.
    integrations = [{"id": "int_1", "name": "Twitter", "disabled": False, "picture": "/x.jpg", "editor": "html"}]

    assert validate_integrations_contract(integrations) == []


def test_validate_integrations_contract_defaults_missing_disabled_to_false():
    # `disabled` has a default in our model since Secretary treats missing
    # as "not disabled" today -- confirm that leniency is intentional, not
    # a silent gap.
    assert validate_integrations_contract([{"id": "int_1", "name": "Twitter"}]) == []


def test_validate_integrations_contract_flags_missing_required_field():
    problems = validate_integrations_contract([{"id": "int_1", "disabled": False}])  # no "name"

    assert len(problems) == 1
    assert "int_1" in problems[0]


def test_validate_integrations_contract_flags_wrong_type():
    problems = validate_integrations_contract([{"id": "int_1", "name": "Twitter", "disabled": "not-a-bool"}])

    assert len(problems) == 1


def test_validate_integrations_contract_reports_index_when_id_missing():
    problems = validate_integrations_contract([{"name": "Twitter"}])  # no "id" either

    assert len(problems) == 1
    assert "index 0" in problems[0]


def test_validate_integrations_contract_empty_list_is_fine():
    assert validate_integrations_contract([]) == []
