from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy import String, Boolean, DateTime, JSON, ForeignKey, Integer, BigInteger, Float, Text, UniqueConstraint
from datetime import datetime, timezone
import os
import re

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./drafiti.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
elif DATABASE_URL.startswith("postgresql://") and "+asyncpg" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
# asyncpg doesn't accept sslmode in the query string
DATABASE_URL = re.sub(r"[?&]sslmode=[^&]*", "", DATABASE_URL)

_connect_args = {"ssl": False} if DATABASE_URL.startswith("postgresql") else {}
engine = create_async_engine(DATABASE_URL, echo=False, connect_args=_connect_args)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc)
    )

    history_entries: Mapped[list["HistoryEntry"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    learned_rules: Mapped[list["LearnedRule"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    custom_categories: Mapped[list["CustomCategory"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class RevokedToken(Base):
    __tablename__ = "revoked_tokens"

    jti:        Mapped[str]      = mapped_column(String(36), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class HistoryEntry(Base):
    __tablename__ = "history_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    filename: Mapped[str] = mapped_column(String(255))
    fecha_corte: Mapped[str | None] = mapped_column(String(60), nullable=True)
    uploaded_at: Mapped[str] = mapped_column(String(20))
    total: Mapped[float] = mapped_column(Float, default=0)
    summary_json: Mapped[dict] = mapped_column(JSON, default=dict)
    transactions_json: Mapped[list] = mapped_column(JSON, default=list)
    sort_key: Mapped[int] = mapped_column(BigInteger, default=0)

    user: Mapped["User"] = relationship(back_populates="history_entries")


class LearnedRule(Base):
    __tablename__ = "learned_rules"
    __table_args__ = (UniqueConstraint("user_id", "description_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    description_key: Mapped[str] = mapped_column(Text)
    categories_json: Mapped[list] = mapped_column(JSON)

    user: Mapped["User"] = relationship(back_populates="learned_rules")


class CustomCategory(Base):
    __tablename__ = "custom_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    icon: Mapped[str] = mapped_column(String(20))
    bg: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(String(20))

    user: Mapped["User"] = relationship(back_populates="custom_categories")


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with SessionLocal() as session:
        yield session
