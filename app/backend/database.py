from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import OperationalError
from atelierai.config import DATABASE_URL

is_sqlite = "sqlite" in DATABASE_URL

# Create the SQLAlchemy engine
# For SQLite: allow cross-thread access, increase busy timeout, and prefer WAL mode.
sqlite_connect_args = {"check_same_thread": False, "timeout": 30} if is_sqlite else {}
engine = create_engine(DATABASE_URL, connect_args=sqlite_connect_args)


if is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

# Create a configured "Session" class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create a Base class that our models will inherit from
Base = declarative_base()


# Dependency to get a DB session for each request
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- NEW: Add a function to test the DB connection ---
def test_db_connection():
    """Tests the database connection and prints a helpful error message."""
    try:
        # Attempt to connect to the database
        with engine.connect():
            print("✅ Database connection successful.")
            return True
    except OperationalError as e:
        print("❌ FATAL: Could not connect to the database.")
        print(f"   Error: {e}")
        print(f"   DATABASE_URL: {DATABASE_URL}")
        print("\n   Troubleshooting:")
        print("   1. Ensure the directory for the database file exists and is writable.")
        print("   2. If using Docker, check your volume mounts in docker-compose.yml.")
        print("   3. If running locally, check file/directory permissions.")
        return False
