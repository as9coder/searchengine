import datetime
import hashlib
from typing import Optional

from sqlalchemy import Integer, Float, String, DateTime, Text, func, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Page(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("url", name="uq_pages_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(512), index=True)
    title: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    lang: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    content_md: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # Clean markdown for AI
    content_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)    # Plain text for embeddings
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # Structured metadata
    quality_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)  # 0.0 - 1.0
    content_type: Mapped[str] = mapped_column(String(50), default="unknown", index=True)  # blog, paper, doc, forum, benchmark, dataset, news
    links_out: Mapped[Optional[str]] = mapped_column(Text, nullable=True)       # JSON list of outgoing URLs
    text_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # SHA256 for dedup
    crawl_status: Mapped[str] = mapped_column(String(20), default="crawled", index=True)
    crawled_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CrawlQueue(Base):
    __tablename__ = "crawl_queue"
    __table_args__ = (UniqueConstraint("url", name="uq_crawl_queue_url"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    url: Mapped[str] = mapped_column(String(2048), unique=True, index=True)
    domain: Mapped[str] = mapped_column(String(512), index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    discovered_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)


class Link(Base):
    __tablename__ = "links"
    __table_args__ = (UniqueConstraint("from_url", "to_url", name="uq_links_from_to"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_url: Mapped[str] = mapped_column(String(2048), index=True)
    to_url: Mapped[str] = mapped_column(String(2048), index=True)
    discovered_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
