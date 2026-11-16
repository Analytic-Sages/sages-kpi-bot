from app.operations_db import normalize_database_url
from app.operations_models import Base


def test_postgres_urls_are_normalized_for_psycopg():
    assert normalize_database_url("postgres://user:pass@host/db").startswith(
        "postgresql+psycopg://"
    )
    assert normalize_database_url("postgresql://user:pass@host/db").startswith(
        "postgresql+psycopg://"
    )


def test_sageops_schema_contains_core_operational_tables():
    expected = {
        "teams", "roles", "people", "kpis", "kpi_results", "tasks",
        "task_details", "task_reminder_state", "weekly_reviews", "review_actions",
        "blockers", "settings", "notifications", "change_requests", "audit_log",
        "telegram_updates", "job_runs",
    }
    assert expected.issubset(set(Base.metadata.tables))
    assert "audit_log" in Base.metadata.tables
    assert "owner_person_id" in Base.metadata.tables["blockers"].c
