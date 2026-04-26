"""Database session/engine re-exports for router/service modules.

This thin wrapper lets router code do::

    from core.database import get_db, SessionLocal

instead of depending directly on the ``database`` module (which lives one
directory above each router).  The underlying objects come from
``database.py`` unchanged; nothing is re-implemented here.
"""

from database import Base, SessionLocal, engine, get_db, test_db_connection

__all__ = ["Base", "SessionLocal", "engine", "get_db", "test_db_connection"]
