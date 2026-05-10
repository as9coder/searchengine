from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import text
from src.config import settings
from src.models import Base

engine = create_async_engine(settings.database_url, echo=False, future=True)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
        await conn.execute(text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
                title, description, content_text,
                content='pages', content_rowid='id'
            )
        """))
        
        await conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS pages_fts_insert AFTER INSERT ON pages BEGIN
                INSERT INTO pages_fts(rowid, title, description, content_text)
                VALUES (new.id, new.title, new.description, new.content_text);
            END
        """))
        
        await conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS pages_fts_update AFTER UPDATE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, title, description, content_text)
                VALUES ('delete', old.id, old.title, old.description, old.content_text);
                INSERT INTO pages_fts(rowid, title, description, content_text)
                VALUES (new.id, new.title, new.description, new.content_text);
            END
        """))
        
        await conn.execute(text("""
            CREATE TRIGGER IF NOT EXISTS pages_fts_delete AFTER DELETE ON pages BEGIN
                INSERT INTO pages_fts(pages_fts, rowid, title, description, content_text)
                VALUES ('delete', old.id, old.title, old.description, old.content_text);
            END
        """))
