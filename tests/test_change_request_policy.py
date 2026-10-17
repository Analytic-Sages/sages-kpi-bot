from dataclasses import replace

import pytest

from app.change_requests.policy import (
    ChangePolicyError,
    ChangeProposal,
    PolicyContext,
    fingerprint_row,
    validate_proposal,
)

TASK_HEADERS = (
    "Task ID", "Status", "% Complete", "Remarks / Blocker", "Output / Link",
    "Completed Date", "Deadline", "Assigned To", "Priority", "Date Assigned",
    "Assigned To ID", "Days To Deadline",
)
TASK_ROW = {
    "Task ID": "T-001",
    "Status": "In Progress",
    "% Complete": "40",
    "Remarks / Blocker": "",
    "Output / Link": "",
    "Completed Date": "",
    "Deadline": "2026-11-01",
    "Assigned To": "Member One",
    "Priority": "Normal",
    "Date Assigned": "2026-10-01",
    "Assigned To ID": "member-1",
    "Days To Deadline": "15",
}


def task_context(**overrides):
    base = PolicyContext(
        actor_id="member-1",
        actor_active=True,
        matching_record_count=1,
        headers=TASK_HEADERS,
        current_row=TASK_ROW.copy(),
        assigned_to_id="member-1",
        assigned_by_id="manager-1",
        known_active_person_ids=frozenset({"member-1", "member-2"}),
        known_active_assignee_values=frozenset({"Member One", "Member Two"}),
        status_is_authoritative=True,
    )
    return replace(base, **overrides)


def task_proposal(context, changes, *, tier=1, expected=None, **overrides):
    expected_values = expected or {
        field: context.current_row.get(field, "") for field in changes
    }
    return ChangeProposal(
        record_kind="task",
        worksheet="Master Task Tracker",
        record_id="T-001",
        actor_id=context.actor_id,
        tier=tier,
        changes=changes,
        expected_values=expected_values,
        row_fingerprint=fingerprint_row(context.headers, context.current_row),
        **overrides,
    )


def test_valid_tier1_progress_change_returns_normalized_preview():
    context = task_context()
    result = validate_proposal(
        task_proposal(context, {"% Complete": "050"}), context
    )
    assert result.changes == {"% Complete": "50"}
    assert result.expected_values == {"% Complete": "40"}
    assert result.record_id == "T-001"
    assert result.current_fingerprint == fingerprint_row(context.headers, context.current_row)


@pytest.mark.parametrize("value", ["-1", "101", "20.5", "NaN", "Infinity", "not-a-number"])
def test_invalid_percent_complete_is_rejected(value):
    context = task_context()
    with pytest.raises(ChangePolicyError):
        validate_proposal(task_proposal(context, {"% Complete": value}), context)


def test_unknown_and_formula_owned_fields_fail_closed():
    context = task_context()
    with pytest.raises(ChangePolicyError, match="not allowlisted"):
        validate_proposal(task_proposal(context, {"Assigned To ID": "member-2"}), context)
    formula_context = task_context(
        current_formula_fields=frozenset({"% Complete"})
    )
    with pytest.raises(ChangePolicyError, match="Formula-owned"):
        validate_proposal(task_proposal(formula_context, {"% Complete": "60"}), formula_context)


def test_formula_injection_is_rejected_for_text_fields():
    context = task_context()
    for value in ("=IMPORTXML(1)", "+SUM(1,2)", "-cmd", "@SUM(1,2)"):
        with pytest.raises(ChangePolicyError, match="formula-injection"):
            validate_proposal(
                task_proposal(context, {"Remarks / Blocker": value}), context
            )


def test_status_transition_needs_verified_authoritative_cell_and_tier():
    context = task_context(status_is_authoritative=False)
    with pytest.raises(ChangePolicyError, match="authoritative status cell"):
        validate_proposal(task_proposal(context, {"Status": "Blocked"}), context)

    context = task_context()
    with pytest.raises(ChangePolicyError, match="not allowed"):
        validate_proposal(task_proposal(context, {"Status": "Closed"}), context)
    with pytest.raises(ChangePolicyError, match="Tier 2"):
        validate_proposal(task_proposal(context, {"Status": "Completed"}), context,)


def test_completion_requires_tier2_and_100_percent():
    context = task_context()
    proposal = task_proposal(
        context, {"Status": "Completed", "% Complete": "80"}, tier=2
    )
    with pytest.raises(ChangePolicyError, match="Completed requires"):
        validate_proposal(proposal, context)

    proposal = task_proposal(
        context, {"Status": "Completed", "% Complete": "100"}, tier=2
    )
    result = validate_proposal(proposal, context)
    assert result.changes == {"Status": "Completed", "% Complete": "100"}


def test_deadline_must_be_valid_and_not_precede_assignment():
    context = task_context()
    with pytest.raises(ChangePolicyError, match="precede Date Assigned"):
        validate_proposal(
            task_proposal(context, {"Deadline": "2026-09-30"}, tier=2), context
        )
    with pytest.raises(ChangePolicyError, match="valid ISO date"):
        validate_proposal(
            task_proposal(context, {"Deadline": "next Friday"}, tier=2), context
        )


def test_owner_active_identity_unique_id_and_snapshot_are_required():
    context = task_context(actor_active=False)
    with pytest.raises(ChangePolicyError, match="Inactive"):
        validate_proposal(task_proposal(context, {"% Complete": "50"}), context)

    context = task_context(matching_record_count=2)
    with pytest.raises(ChangePolicyError, match="exactly one"):
        validate_proposal(task_proposal(context, {"% Complete": "50"}), context)

    context = task_context(assigned_to_id="another-person")
    with pytest.raises(ChangePolicyError, match="not authorized"):
        validate_proposal(task_proposal(context, {"% Complete": "50"}), context)


def test_stale_row_fingerprint_and_expected_value_are_rejected():
    context = task_context()
    proposal = task_proposal(context, {"% Complete": "60"})
    changed_row = {**context.current_row, "Priority": "Urgent"}
    changed_context = task_context(current_row=changed_row)
    with pytest.raises(ChangePolicyError, match="Row snapshot is stale"):
        validate_proposal(proposal, changed_context)

    stale_expected = task_proposal(
        context, {"% Complete": "60"}, expected={"% Complete": "0"}
    )
    with pytest.raises(ChangePolicyError, match="Expected value"):
        validate_proposal(stale_expected, context)


def test_duplicate_or_blank_headers_fail_closed():
    context = task_context(headers=("Task ID", "Status", "status"))
    proposal = task_proposal(context, {"Status": "Blocked"})
    with pytest.raises(ChangePolicyError, match="Duplicate"):
        validate_proposal(proposal, context)

    context = task_context(headers=("Task ID", "", "% Complete"))
    proposal = task_proposal(context, {"% Complete": "50"})
    with pytest.raises(ChangePolicyError, match="Blank"):
        validate_proposal(proposal, context)


def test_tier2_assignment_must_reference_known_active_person():
    context = task_context()
    proposal = task_proposal(context, {"Assigned To": "ghost"}, tier=2)
    with pytest.raises(ChangePolicyError, match="known active person"):
        validate_proposal(proposal, context)


def test_reopening_closed_task_requires_tier2_approval():
    row = {**TASK_ROW, "Status": "Closed"}
    context = task_context(current_row=row)
    tier1 = task_proposal(context, {"Status": "In Progress"}, tier=1)
    with pytest.raises(ChangePolicyError, match="require Tier 2"):
        validate_proposal(tier1, context)

    tier2 = task_proposal(context, {"Status": "In Progress"}, tier=2)
    assert validate_proposal(tier2, context).changes == {"Status": "In Progress"}


def test_kpi_target_requires_owner_and_positive_finite_number():
    headers = ("KPI ID", "Target", "Owner")
    row = {"KPI ID": "K-1", "Target": "10", "Owner": "member-1"}
    context = PolicyContext(
        actor_id="member-1",
        actor_active=True,
        matching_record_count=1,
        headers=headers,
        current_row=row,
        owner_id="member-1",
    )
    proposal = ChangeProposal(
        record_kind="kpi",
        worksheet="KPI Register",
        record_id="K-1",
        actor_id="member-1",
        tier=2,
        changes={"Target": "12.50"},
        expected_values={"Target": "10"},
        row_fingerprint=fingerprint_row(headers, row),
    )
    assert validate_proposal(proposal, context).changes == {"Target": "12.50"}
    for value in ("0", "-1", "NaN", "inf", "x"):
        bad = replace(proposal, changes={"Target": value})
        with pytest.raises(ChangePolicyError):
            validate_proposal(bad, context)

    wrong_owner = replace(context, owner_id="another-person")
    with pytest.raises(ChangePolicyError, match="record owner"):
        validate_proposal(proposal, wrong_owner)


def test_wrong_worksheet_and_tier_fail_closed():
    context = task_context()
    wrong_sheet = replace(task_proposal(context, {"Deadline": "2026-11-02"}, tier=2),
                          worksheet="KPI Register")
    with pytest.raises(ChangePolicyError, match="do not match"):
        validate_proposal(wrong_sheet, context)

    wrong_tier = task_proposal(context, {"Deadline": "2026-11-02"}, tier=1)
    with pytest.raises(ChangePolicyError, match="require Tier 2"):
        validate_proposal(wrong_tier, context)
