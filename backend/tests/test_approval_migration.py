"""The approval-source migration must preserve existing request decisions."""
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_approval_source_migration_keeps_existing_records():
    path = Path(__file__).parents[1] / "alembic" / "versions" / "0007_approval_source.py"
    spec = importlib.util.spec_from_file_location("approval_source_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE procurement_requests (id INTEGER PRIMARY KEY, approval_status VARCHAR NOT NULL)"))
        connection.execute(text("INSERT INTO procurement_requests VALUES (1, 'approved'), (2, 'pending')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        rows = connection.execute(text("SELECT id, approval_status, approval_source FROM procurement_requests ORDER BY id")).all()
        assert rows == [(1, "approved", None), (2, "pending", None)]
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
        assert "approval_source" not in {column["name"] for column in inspect(connection).get_columns("procurement_requests")}
        assert connection.execute(text("SELECT COUNT(*) FROM procurement_requests")).scalar_one() == 2
