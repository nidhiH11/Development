
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Database connection (edit these details if your PostgreSQL settings differ)
DATABASE_URL = "postgresql+psycopg://postgres:postgresql@localhost:5432/attendance_db"

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()