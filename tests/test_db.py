"""Tests for the SQLite schema and session helpers."""

from __future__ import annotations

import uuid

import pytest
from sqlmodel import create_engine

from optout.db import (
    MonitoringScan,
    create_submission,
    get_session,
    get_submission,
    init_db,
    list_events,
    list_submissions,
    mark_submitted,
    record_event,
    update_status,
)


@pytest.fixture()
def engine():
    """In-memory SQLite engine, freshly initialised for each test."""
    e = create_engine("sqlite:///:memory:", echo=False)
    init_db(e)
    return e


class TestSchema:
    def test_tables_created(self, engine) -> None:
        from sqlmodel import inspect as sqlinspect

        inspector = sqlinspect(engine)
        tables = set(inspector.get_table_names())
        assert {"submission", "submissionevent", "monitoringscan"} <= tables

    def test_submission_defaults(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="whitepages",
                method_used="web_form",
                legal_basis_cited=["CCPA", "CPRA"],
                statutory_response_days=45,
            )
        assert sub.status == "queued"
        assert sub.legal_basis_cited == "CCPA,CPRA"
        assert sub.retry_count == 0
        assert sub.created_at is not None


class TestSubmissionLifecycle:
    def test_create_and_retrieve(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="spokeo",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            sub_id = sub.id

        with get_session(engine) as session:
            fetched = get_submission(session, sub_id)
            assert fetched is not None
            assert fetched.broker_slug == "spokeo"

    def test_update_status(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="spokeo",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            update_status(session, sub, "submitted")
            assert sub.status == "submitted"

    def test_mark_submitted_sets_deadline(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="spokeo",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            mark_submitted(
                session,
                sub,
                payload={"to": "optout@spokeo.com"},
                artifact_path=None,
                statutory_response_days=30,
            )
            assert sub.status == "submitted"
            assert sub.submitted_at is not None
            assert sub.deadline_at is not None
            delta = sub.deadline_at - sub.submitted_at
            assert delta.days == 30

    def test_failure_reason_stored(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="spokeo",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            update_status(session, sub, "failed", failure_reason="Form changed")
            assert sub.failure_reason == "Form changed"

    def test_list_submissions_filter_by_slug(self, engine) -> None:
        with get_session(engine) as session:
            create_submission(
                session,
                broker_slug="a",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            create_submission(
                session,
                broker_slug="b",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            results = list_submissions(session, broker_slug="a")
        assert len(results) == 1
        assert results[0].broker_slug == "a"

    def test_list_submissions_filter_by_status(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="a",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            create_submission(
                session,
                broker_slug="b",
                method_used="email",
                legal_basis_cited=["CCPA"],
                statutory_response_days=30,
            )
            update_status(session, sub, "confirmed")
            results = list_submissions(session, status="confirmed")
        assert len(results) == 1


class TestSubmissionEvents:
    def test_create_submission_emits_created_event(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="whitepages",
                method_used="web_form",
                legal_basis_cited=["CCPA"],
                statutory_response_days=45,
            )
            events = list_events(session, sub.id)
        assert len(events) == 1
        assert events[0].event_type == "created"

    def test_record_event_appends(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="whitepages",
                method_used="web_form",
                legal_basis_cited=["CCPA"],
                statutory_response_days=45,
            )
            record_event(session, sub, "submitted", {"to": "form@whitepages.com"})
            events = list_events(session, sub.id)
        assert len(events) == 2
        assert events[1].event_type == "submitted"

    def test_events_foreign_key_to_submission(self, engine) -> None:
        with get_session(engine) as session:
            sub = create_submission(
                session,
                broker_slug="whitepages",
                method_used="web_form",
                legal_basis_cited=["CCPA"],
                statutory_response_days=45,
            )
            events = list_events(session, sub.id)
        assert events[0].submission_id == sub.id


class TestMonitoringScan:
    def test_scan_insert_and_retrieve(self, engine) -> None:
        with get_session(engine) as session:
            scan = MonitoringScan(
                broker_slug="whitepages",
                result="found",
                matches_json='[{"name": "Jane Q Public"}]',
            )
            session.add(scan)
            session.commit()
            session.refresh(scan)

        assert isinstance(scan.id, uuid.UUID)
        assert scan.result == "found"
