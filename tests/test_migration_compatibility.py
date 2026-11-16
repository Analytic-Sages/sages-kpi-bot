from importlib import import_module

from app.operations_models import Base


class MetadataInspector:
    """Small inspector double backed by the current ORM metadata."""

    def __init__(self, missing_columns=()):
        self.missing_columns = set(missing_columns)

    def has_table(self, table):
        return table in Base.metadata.tables

    def get_columns(self, table):
        return [
            {"name": column.name, "nullable": column.nullable}
            for column in Base.metadata.tables[table].columns
            if (table, column.name) not in self.missing_columns
        ]

    def get_indexes(self, table):
        # The ORM's metadata models an FK for owner_person_id, but the
        # migration-created supporting index is not part of that metadata.
        # Include indexes that the baseline migrations create explicitly.
        indexes = [
            {"name": index.name}
            for index in Base.metadata.tables[table].indexes
        ]
        if table == "blockers":
            indexes.append({"name": "ix_blockers_owner_person_id"})
        return indexes

    def get_foreign_keys(self, table):
        result = []
        for constraint in Base.metadata.tables[table].foreign_key_constraints:
            elements = list(constraint.elements)
            result.append(
                {
                    "constrained_columns": [element.parent.name for element in elements],
                    "referred_table": elements[0].column.table.name if elements else None,
                }
            )
        return result


def test_revision_0003_is_noop_when_current_metadata_already_created_schema(monkeypatch):
    migration = import_module(
        "migrations.versions.0003_complete_operations_persistence"
    )
    operations = []
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: MetadataInspector())
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())

    for operation in (
        "add_column",
        "alter_column",
        "create_table",
        "create_index",
    ):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: operations.append(
                (_operation, args, kwargs)
            ),
        )

    migration.upgrade()

    assert operations == []


def test_revision_0003_adds_a_column_missing_from_an_older_schema(monkeypatch):
    migration = import_module(
        "migrations.versions.0003_complete_operations_persistence"
    )
    operations = []
    inspector = MetadataInspector(
        missing_columns={("notifications", "delivery_started_at")}
    )
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: operations.append((table, column.name)),
    )

    migration._add_column_if_missing(
        "notifications",
        migration.sa.Column(
            "delivery_started_at",
            migration.sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    assert operations == [("notifications", "delivery_started_at")]


def test_revision_0004_is_noop_when_current_metadata_already_created_ownership(monkeypatch):
    migration = import_module("migrations.versions.0004_blocker_ownership")
    operations = []
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: MetadataInspector())
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())

    for operation in ("add_column", "create_foreign_key", "create_index"):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: operations.append(
                (_operation, args, kwargs)
            ),
        )

    migration.upgrade()

    assert operations == []
