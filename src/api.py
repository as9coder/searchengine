from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, text
from pathlib import Path
import markdown as md

from src.database import async_session, init_db
from src.models import Page, CrawlQueue
from pydantic import BaseModel

app = FastAPI(title="AI Search Engine")

static_dir = Path(__file__).resolve().parent / "static"
templates_dir = Path(__file__).resolve().parent / "templates"
app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

@app.get("/")
async def root():
    return FileResponse(static_dir / "index.html")


class CrawlRequest(BaseModel):
    url: str
    priority: int = 10


@app.on_event("startup")
async def on_startup():
    await init_db()


@app.post("/crawl")
async def submit_crawl(req: CrawlRequest):
    from src.crawler import normalize_url, get_domain, is_ai_relevant
    url = normalize_url(req.url)
    if not is_ai_relevant(url):
        raise HTTPException(status_code=400, detail="URL does not appear AI-relevant")
    
    async with async_session() as session:
        existing = await session.execute(select(CrawlQueue).where(CrawlQueue.url == url))
        if existing.scalar_one_or_none():
            return {"status": "already_queued", "url": url}
        
        q = CrawlQueue(url=url, priority=req.priority, domain=get_domain(url))
        session.add(q)
        await session.commit()
    return {"status": "queued", "url": url}


@app.get("/search")
async def search(q: str = Query(..., min_length=1), limit: int = 20, type_filter: str = ""):
    async with async_session() as session:
        base_query = """
            SELECT pages.id, pages.url, pages.title, pages.description, pages.content_type, pages.quality_score
            FROM pages_fts
            JOIN pages ON pages_fts.rowid = pages.id
            WHERE pages_fts MATCH :q
        """
        if type_filter and type_filter.strip():
            base_query += " AND pages.content_type = :type_filter"
        
        base_query += " ORDER BY rank LIMIT :limit"
        
        stmt = text(base_query)
        params = {"q": q, "limit": limit}
        if type_filter and type_filter.strip():
            params["type_filter"] = type_filter.strip()
            
        result = await session.execute(stmt, params)
        rows = result.mappings().all()
        return {"query": q, "results": [dict(r) for r in rows]}


@app.get("/view/{page_id}")
async def view_page(request: Request, page_id: int):
    async with async_session() as session:
        result = await session.execute(select(Page).where(Page.id == page_id))
        page = result.scalar_one_or_none()
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        
        content_html = md.markdown(page.content_md or "", extensions=["fenced_code", "tables"])
        
        return templates.TemplateResponse(
            request,
            "view.html",
            {
                "title": page.title,
                "url": page.url,
                "domain": page.domain,
                "author": page.author,
                "crawled_at": page.crawled_at,
                "content": content_html,
                "content_type": page.content_type,
                "quality_score": page.quality_score,
            },
        )


@app.get("/page/{page_id}")
async def get_page(page_id: int):
    async with async_session() as session:
        result = await session.execute(select(Page).where(Page.id == page_id))
        page = result.scalar_one_or_none()
        if not page:
            raise HTTPException(status_code=404, detail="Page not found")
        return {
            "id": page.id,
            "url": page.url,
            "title": page.title,
            "author": page.author,
            "description": page.description,
            "content_md": page.content_md,
            "content_text": page.content_text,
            "content_type": page.content_type,
            "quality_score": page.quality_score,
            "crawled_at": page.crawled_at,
        }


@app.get("/stats")
async def stats():
    async with async_session() as session:
        page_count = await session.execute(select(func.count()).select_from(Page))
        queue_pending = await session.execute(
            select(func.count()).select_from(CrawlQueue).where(CrawlQueue.status == "pending")
        )
        types = await session.execute(
            text("SELECT content_type, COUNT(*) FROM pages GROUP BY content_type")
        )
        avg_quality = await session.execute(
            text("SELECT AVG(quality_score) FROM pages")
        )
        return {
            "pages_indexed": page_count.scalar(),
            "queue_pending": queue_pending.scalar(),
            "content_types": {row[0]: row[1] for row in types.all()},
            "avg_quality": round(avg_quality.scalar() or 0, 3),
        }
