"""SQLite storage primitives for MVP-02.

State changes must be made by higher-level API modules. This package owns
connections, migrations and transaction boundaries; it does not implement
task-state transitions.
"""

from app.db.connection import Database
from app.db.migrations import MigrationError, apply_migrations

__all__ = ["Database", "MigrationError", "apply_migrations"]
