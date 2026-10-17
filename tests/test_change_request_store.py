from datetime import UTC, datetime, timedelta

import pytest

from app.change_requests.store import (
    ChangeRequestError,
    ChangeRequestForbidden,
    ChangeRequestInvalidTransition,
    ChangeRequestNotFound,
    SQLiteChangeRequestStore,
)


def moment() -> datetime:
    return datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


async def make_tier1(store, *, now=None, source=False):
    request = await store.create_draft(
        actor_id="member-1",
        tier=1,
        payload={"worksheet": "Master Task Tracker", "task_id": "T001",
                 "changes": {"% Complete": "50"}},
        reason="Progress update",
        source_chat_id="chat-1" if source else None,
        source_message_id="message-1" if source else None,
        now=now or moment(),
    )
    return request


@pytest.mark.asyncio
async def test_tier1_request_confirmation_and_apply_are_persisted(tmp_path):
    path = str(tmp_path / "requests.sqlite3")
    store = SQLiteChangeRequestStore(path)
    draft = await make_tier1(store)
    assert draft.status == "DRAFT"

    pending = await store.submit_for_confirmation(draft.request_id, now=moment())
    assert pending.status == "AWAITING_CONFIRMATION"
    assert pending.confirmation_expires_at == moment() + timedelta(minutes=10)

    approved = await store.confirm(
        draft.request_id, actor_id="member-1", now=moment() + timedelta(minutes=1)
    )
    assert approved.status == "APPROVED"
    applied = await store.mark_applied(
        draft.request_id, now=moment() + timedelta(minutes=2)
    )
    assert applied.status == "APPLIED"
    assert applied.applied_at == moment() + timedelta(minutes=2)

    restarted = SQLiteChangeRequestStore(path)
    assert (await restarted.get(draft.request_id)).status == "APPLIED"
    events = await restarted.list_events(draft.request_id)
    assert [event["to_status"] for event in events] == [
        "DRAFT", "AWAITING_CONFIRMATION", "APPROVED", "APPLIED"
    ]


@pytest.mark.asyncio
async def test_tier2_requires_confirmation_and_separate_approver(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    draft = await store.create_draft(
        actor_id="kpi-owner",
        tier=2,
        payload={"worksheet": "KPI Register", "kpi_id": "K001",
                 "changes": {"Target": "20"}},
        allowed_approver_ids=("team-lead", "ops-manager"),
        now=moment(),
    )
    await store.submit_for_confirmation(draft.request_id, now=moment())
    pending = await store.confirm(
        draft.request_id, actor_id="kpi-owner", now=moment() + timedelta(minutes=1)
    )
    assert pending.status == "AWAITING_APPROVAL"
    assert pending.approval_expires_at == moment() + timedelta(minutes=1, hours=48)

    with pytest.raises(ChangeRequestForbidden, match="Requester cannot"):
        await store.approve(
            draft.request_id, approver_id="kpi-owner", now=moment() + timedelta(minutes=2)
        )
    with pytest.raises(ChangeRequestForbidden, match="authorized approver"):
        await store.approve(
            draft.request_id, approver_id="other-person", now=moment() + timedelta(minutes=2)
        )
    approved = await store.approve(
        draft.request_id, approver_id="team-lead", now=moment() + timedelta(minutes=2)
    )
    assert approved.status == "APPROVED"
    assert approved.approver_id == "team-lead"


@pytest.mark.asyncio
async def test_duplicate_source_message_returns_same_request(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    first = await make_tier1(store, source=True)
    duplicate = await make_tier1(store, source=True)
    assert duplicate.request_id == first.request_id
    assert len(await store.list_events(first.request_id)) == 1


@pytest.mark.asyncio
async def test_duplicate_source_message_with_different_payload_is_rejected(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    first = await make_tier1(store, source=True)
    with pytest.raises(ChangeRequestError, match="different request data"):
        await store.create_draft(
            actor_id="member-1",
            tier=1,
            payload={"worksheet": "Master Task Tracker", "task_id": "T002",
                     "changes": {"% Complete": "90"}},
            source_chat_id="chat-1",
            source_message_id="message-1",
            now=moment(),
        )
    assert (await store.get(first.request_id)).payload["task_id"] == "T001"


@pytest.mark.asyncio
async def test_request_id_cannot_be_taken_over_or_reused_with_different_payload(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    first = await store.create_draft(
        actor_id="member-1",
        tier=1,
        payload={"task_id": "T001", "changes": {"% Complete": "50"}},
        request_id="stable-request",
        now=moment(),
    )
    with pytest.raises(ChangeRequestForbidden):
        await store.create_draft(
            actor_id="member-2", tier=1,
            payload={"task_id": "T001", "changes": {"% Complete": "50"}},
            request_id="stable-request", now=moment(),
        )
    with pytest.raises(ChangeRequestError, match="different request data"):
        await store.create_draft(
            actor_id="member-1", tier=1,
            payload={"task_id": "T001", "changes": {"% Complete": "80"}},
            request_id="stable-request", now=moment(),
        )
    assert (await store.get(first.request_id)).payload["changes"]["% Complete"] == "50"


@pytest.mark.asyncio
async def test_confirmation_expiry_blocks_late_confirmation(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    draft = await make_tier1(store)
    await store.submit_for_confirmation(draft.request_id, now=moment())
    expired = await store.confirm(
        draft.request_id, actor_id="member-1", now=moment() + timedelta(minutes=10)
    )
    assert expired.status == "EXPIRED"
    assert expired.outcome_code == "CONFIRMATION_EXPIRED"
    with pytest.raises(ChangeRequestInvalidTransition):
        await store.confirm(
            draft.request_id, actor_id="member-1", now=moment() + timedelta(minutes=11)
        )


@pytest.mark.asyncio
async def test_approval_expiry_and_expire_sweep_are_audited(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    draft = await store.create_draft(
        actor_id="owner",
        tier=2,
        payload={"kpi_id": "K001", "changes": {"Target": "12"}},
        allowed_approver_ids=("lead",),
        now=moment(),
    )
    await store.submit_for_confirmation(draft.request_id, now=moment())
    await store.confirm(draft.request_id, actor_id="owner", now=moment())
    expired_at = moment() + timedelta(hours=48)
    assert await store.expire_due(now=expired_at) == 1
    assert (await store.get(draft.request_id)).status == "EXPIRED"
    events = await store.list_events(draft.request_id)
    assert events[-1]["action"] == "expire"
    assert events[-1]["outcome_code"] == "APPROVAL_EXPIRED"
    assert await store.expire_due(now=expired_at + timedelta(minutes=1)) == 0


@pytest.mark.asyncio
async def test_reject_cancel_conflict_and_failure_transitions(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))

    rejected = await store.create_draft(
        actor_id="owner", tier=2, payload={"target": "new"},
        allowed_approver_ids=("lead",), now=moment(),
    )
    await store.submit_for_confirmation(rejected.request_id, now=moment())
    await store.confirm(rejected.request_id, actor_id="owner", now=moment())
    result = await store.reject(rejected.request_id, approver_id="lead", now=moment())
    assert result.status == "REJECTED"

    cancelled = await make_tier1(store)
    result = await store.cancel(cancelled.request_id, actor_id="member-1", now=moment())
    assert result.status == "CANCELLED"

    conflicted = await make_tier1(store)
    await store.submit_for_confirmation(conflicted.request_id, now=moment())
    await store.confirm(conflicted.request_id, actor_id="member-1", now=moment())
    result = await store.mark_conflict(conflicted.request_id, now=moment())
    assert result.status == "CONFLICT"

    failed = await make_tier1(store)
    await store.submit_for_confirmation(failed.request_id, now=moment())
    await store.confirm(failed.request_id, actor_id="member-1", now=moment())
    result = await store.mark_failed(
        failed.request_id, outcome_code="SHEETS_TIMEOUT", now=moment()
    )
    assert result.status == "FAILED"
    assert result.outcome_code == "SHEETS_TIMEOUT"


@pytest.mark.asyncio
async def test_duplicate_approval_does_not_apply_twice(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    draft = await store.create_draft(
        actor_id="owner", tier=2, payload={"target": "12"},
        allowed_approver_ids=("lead",), now=moment(),
    )
    await store.submit_for_confirmation(draft.request_id, now=moment())
    await store.confirm(draft.request_id, actor_id="owner", now=moment())
    await store.approve(draft.request_id, approver_id="lead", now=moment())
    with pytest.raises(ChangeRequestInvalidTransition):
        await store.approve(draft.request_id, approver_id="lead", now=moment())
    applied = await store.mark_applied(draft.request_id, now=moment())
    duplicate = await store.mark_applied(draft.request_id, now=moment() + timedelta(seconds=1))
    assert duplicate.status == "APPLIED"
    assert duplicate.applied_at == applied.applied_at
    events = await store.list_events(draft.request_id)
    assert sum(event["action"] == "applied" for event in events) == 1


@pytest.mark.asyncio
async def test_unknown_request_and_naive_timestamp_are_rejected(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    with pytest.raises(ChangeRequestNotFound):
        await store.get("missing")
    with pytest.raises(ValueError, match="timezone-aware"):
        await make_tier1(store, now=datetime(2026, 10, 17, 12, 0))


@pytest.mark.asyncio
async def test_tier2_requires_approver_and_self_approval_is_removed(tmp_path):
    store = SQLiteChangeRequestStore(str(tmp_path / "requests.sqlite3"))
    with pytest.raises(ChangeRequestError, match="at least one approver"):
        await store.create_draft(
            actor_id="owner", tier=2, payload={"target": "12"}, now=moment()
        )
    with pytest.raises(ChangeRequestError, match="cannot approve"):
        await store.create_draft(
            actor_id="owner", tier=2, payload={"target": "12"},
            allowed_approver_ids=("owner",), now=moment(),
        )
