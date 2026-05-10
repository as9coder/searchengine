import asyncio
import json
import logging
import time
from urllib.parse import urljoin, urlparse, urldefrag
from pathlib import Path

from bs4 import BeautifulSoup
import httpx
from sqlalchemy import select, update, func
from src.config import settings
from src.database import async_session
from src.models import CrawlQueue, Page, Link
from src.extractor import extract_content
from src.state import load_state, save_state

logger = logging.getLogger(__name__)

# Domain tiers for crawl politeness and depth
TIER_1_DOMAINS = {
    "arxiv.org", "huggingface.co", "paperswithcode.com", "openreview.net",
    "distill.pub", "openai.com", "anthropic.com", "deepmind.com", "ai.google",
    "research.google", "blog.google", "pytorch.org", "tensorflow.org",
    "wandb.ai", "lesswrong.com", "alignmentforum.org", "mlsys.org",
    "together.ai", "cohere.com", "mistral.ai", "ai.stanford.edu",
    "bair.berkeley.edu", "crfm.stanford.edu", "mit.edu", "csail.mit.edu",
    "nvidia.com", "news.mit.edu", "blog.research.google",
}

TIER_2_DOMAINS = {
    "github.com", "medium.com", "dev.to", "towardsdatascience.com",
    "neptune.ai", "dagshub.com", "comet.com", "kaggle.com",
    "reddit.com", "stackexchange.com", "stackoverflow.com",
}

DOMAIN_DELAYS = {d: 0.3 for d in TIER_1_DOMAINS}
DOMAIN_DELAYS.update({d: 0.8 for d in TIER_2_DOMAINS})
DEFAULT_DELAY = 1.2


def normalize_url(url: str) -> str:
    url, _ = urldefrag(url)
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    if not path:
        path = "/"
    return f"{parsed.scheme}://{parsed.netloc.lower()}{path}"


def get_domain(url: str) -> str:
    return urlparse(url).netloc.lower()


def get_tld_domain(url: str) -> str:
    netloc = get_domain(url)
    parts = netloc.split(".")
    if len(parts) > 2:
        return ".".join(parts[-2:])
    return netloc


def is_ai_relevant(url: str, text: str = "") -> bool:
    url_lower = url.lower()
    
    priority_domains = [
        "arxiv.org", "huggingface.co", "paperswithcode.com",
        "openreview.net", "neurips.cc", "icml.cc", "iclr.cc", "distill.pub",
        "openai.com", "anthropic.com", "deepmind.com", "ai.google",
        "blog.google", "research.google", "pytorch.org",
        "tensorflow.org", "wandb.ai", "lesswrong.com", "alignmentforum.org",
        "mlsys.org", "together.ai", "cohere.com", "mistral.ai", "perplexity.ai",
        "ai.stanford.edu", "bair.berkeley.edu", "crfm.stanford.edu",
        "mit.edu", "csail.mit.edu", "nvidia.com", "news.mit.edu",
        "blog.research.google", "googleresearch.blogspot.com",
        "eleuther.ai", "mosaicml.com", "adept.ai", "inflection.ai",
        "stability.ai", "midjourney.com", "runwayml.com",
        "assemblyai.com", "deepinfra.com", "fireworks.ai", "replicate.com",
        "modal.com", "baseten.co", "banana.dev", "octo.ai",
    ]
    
    for domain in priority_domains:
        if domain in url_lower:
            return True
    
    if "github.com" in url_lower:
        gh_terms = ["ai", "ml", "llm", "transformer", "pytorch", "tensorflow",
                    "neural", "deep-learning", "gpt", "clip", "diffusion",
                    "model", "benchmark", "dataset", "rlhf", "embedding",
                    "language-model", "jax", "cuda", "mps", "stable-baselines",
                    "huggingface", "hf", "machine-learning", "langchain",
                    "llama-index", "autogpt", "crewai", "openai", "anthropic"]
        return any(t in url_lower for t in gh_terms)
    
    ai_terms = [
        "ai", "ml", "machine-learning", "deep-learning", "llm", "nlp",
        "cv", "reinforcement-learning", "neural", "transformer", "gpt",
        "benchmark", "dataset", "model", "training", "inference", "paper",
        "research", "artificial-intelligence", "language-model", "diffusion",
        "generative", "fine-tune", "rlhf", "alignment", "safety",
        "rag", "agent", "prompt", "embedding", "vector", "evaluation",
        "hallucination", "token", "tokenizer", "ablation", "sota",
        "state-of-the-art", "foundation-model", "pretrain", "finetune",
        "quantization", "distillation", "pruning", "sparse", "mixture-of-experts",
        "moe", " LoRA ", " QLoRA ", "PEFT", "instruction-tuning",
    ]
    
    for term in ai_terms:
        if term in url_lower:
            return True
    
    if text:
        text_lower = text.lower()
        keyword_hits = sum(1 for k in settings.ai_keywords if k in text_lower)
        if keyword_hits >= 2:
            return True
    
    return False


def extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        full = urljoin(base_url, href)
        parsed = urlparse(full)
        if parsed.scheme in ("http", "https"):
            links.append(normalize_url(full))
    return list(set(links))


def extract_sitemap_links(html: str, base_url: str) -> list[str]:
    try:
        soup = BeautifulSoup(html, "xml")
        urls = []
        for loc in soup.find_all("loc"):
            if loc.text:
                urls.append(normalize_url(loc.text))
        return urls
    except Exception:
        return []


class StorageGuard:
    def __init__(self, max_gb: float = 180.0):
        self.max_bytes = max_gb * 1024 * 1024 * 1024
        self.db_path = Path("searchengine.db")
    
    def check(self) -> bool:
        if not self.db_path.exists():
            return True
        return self.db_path.stat().st_size < self.max_bytes
    
    def usage_gb(self) -> float:
        if not self.db_path.exists():
            return 0.0
        return self.db_path.stat().st_size / (1024 ** 3)


class Crawler:
    def __init__(self):
        limits = httpx.Limits(max_keepalive_connections=50, max_connections=100)
        timeout = httpx.Timeout(settings.request_timeout)
        headers = {
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Accept-Encoding": "gzip, deflate, br",
        }
        self.client = httpx.AsyncClient(
            limits=limits, timeout=timeout, headers=headers,
            http2=True, follow_redirects=True,
        )
        self.semaphore = asyncio.Semaphore(settings.crawl_concurrency)
        self.storage_guard = StorageGuard(max_gb=180.0)
        
        # Resume state
        self.domain_counts: dict[str, int] = {}
        self.domain_last_hit: dict[str, float] = {}
        self.seen_hashes: set[str] = set()
        self.processed_since_save = 0
        self._loaded = False
    
    async def _init_state(self):
        """Resume from DB + disk state. Call once before crawling."""
        if self._loaded:
            return
        self._loaded = True
        
        # 1. Reset any stuck 'processing' URLs back to 'pending'
        async with async_session() as session:
            stuck = await session.execute(
                update(CrawlQueue)
                .where(CrawlQueue.status == "processing")
                .values(status="pending")
            )
            if stuck.rowcount:
                logger.info(f"Resumed {stuck.rowcount} stuck URLs back to pending")
            await session.commit()
        
        # 2. Warm domain_counts from already-crawled pages in DB
        async with async_session() as session:
            result = await session.execute(
                select(Page.domain, func.count()).where(Page.crawl_status == "crawled").group_by(Page.domain)
            )
            for domain, count in result.all():
                self.domain_counts[domain] = count
            logger.info(f"Loaded domain counts for {len(self.domain_counts)} domains from DB")
        
        # 3. Warm seen_hashes from DB (last 10k to keep memory sane)
        async with async_session() as session:
            result = await session.execute(
                select(Page.text_hash).where(Page.text_hash.isnot(None)).order_by(Page.id.desc()).limit(10000)
            )
            for (h,) in result.all():
                self.seen_hashes.add(h)
            logger.info(f"Warmed {len(self.seen_hashes)} dedup hashes from DB")
        
        # 4. Load any additional state from disk
        disk_state = load_state()
        if disk_state:
            logger.info(f"Loaded state from disk: {disk_state.get('processed_total', 0)} total processed")
    
    async def save_state(self):
        """Persist state to disk."""
        async with async_session() as session:
            total = await session.execute(select(func.count()).select_from(Page))
            pending = await session.execute(
                select(func.count()).select_from(CrawlQueue).where(CrawlQueue.status == "pending")
            )
            state = {
                "processed_total": total.scalar(),
                "queue_pending": pending.scalar(),
                "domains_crawled": len(self.domain_counts),
                "hashes_cached": len(self.seen_hashes),
                "saved_at": time.time(),
            }
            save_state(state)
            logger.info(f"State saved: {state['processed_total']} pages, {state['queue_pending']} pending")
    
    async def close(self):
        await self.client.aclose()
    
    async def _respect_delay(self, url: str):
        domain = get_tld_domain(url)
        delay = DOMAIN_DELAYS.get(domain, DEFAULT_DELAY)
        last = self.domain_last_hit.get(domain, 0)
        elapsed = time.time() - last
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self.domain_last_hit[domain] = time.time()
    
    async def fetch(self, url: str, retries: int = 3) -> tuple[str, int]:
        async with self.semaphore:
            await self._respect_delay(url)
            for attempt in range(retries):
                try:
                    resp = await self.client.get(url)
                    if resp.status_code == 200:
                        return resp.text, resp.status_code
                    elif resp.status_code in (429, 503, 502):
                        wait = 2 ** attempt
                        logger.warning(f"Rate limit on {url}, backing off {wait}s")
                        await asyncio.sleep(wait)
                    else:
                        return resp.text, resp.status_code
                except Exception as e:
                    if attempt == retries - 1:
                        logger.warning(f"Failed to fetch {url}: {e}")
                        return "", 0
                    await asyncio.sleep(1.5 ** attempt)
            return "", 0
    
    async def process_url(self, url: str, depth: int):
        domain = get_domain(url)
        
        if self.domain_counts.get(domain, 0) >= settings.max_pages_per_domain:
            return
        
        self.domain_counts[domain] = self.domain_counts.get(domain, 0) + 1
        
        html, status = await self.fetch(url)
        if status != 200 or not html:
            async with async_session() as session:
                await session.execute(
                    update(CrawlQueue).where(CrawlQueue.url == url).values(status="failed")
                )
                await session.commit()
            return
        
        # Handle sitemaps
        if url.endswith("sitemap.xml") or "/sitemap" in url:
            sitemap_links = extract_sitemap_links(html, url)
            async with async_session() as session:
                for link in sitemap_links:
                    if is_ai_relevant(link):
                        existing = await session.execute(select(CrawlQueue).where(CrawlQueue.url == link))
                        if not existing.scalar_one_or_none():
                            session.add(CrawlQueue(url=link, depth=depth, domain=get_domain(link), source_url=url))
                await session.commit()
            async with async_session() as session:
                await session.execute(update(CrawlQueue).where(CrawlQueue.url == url).values(status="done"))
                await session.commit()
            return
        
        extracted = extract_content(html, url)
        
        if not extracted:
            async with async_session() as session:
                await session.execute(update(CrawlQueue).where(CrawlQueue.url == url).values(status="failed"))
                await session.commit()
            return
        
        # Deduplication check
        text_hash = extracted["text_hash"]
        if text_hash in self.seen_hashes:
            logger.info(f"Skipping duplicate content: {url}")
            async with async_session() as session:
                await session.execute(update(CrawlQueue).where(CrawlQueue.url == url).values(status="duplicate"))
                await session.commit()
            return
        
        # Check DB for existing hash
        async with async_session() as session:
            existing_hash = await session.execute(
                select(Page).where(Page.text_hash == text_hash)
            )
            if existing_hash.scalar_one_or_none():
                self.seen_hashes.add(text_hash)
                await session.execute(update(CrawlQueue).where(CrawlQueue.url == url).values(status="duplicate"))
                await session.commit()
                return
        
        is_relevant = is_ai_relevant(url, extracted.get("content_text", ""))
        
        async with async_session() as session:
            if is_relevant:
                page = Page(
                    url=url,
                    domain=domain,
                    title=extracted.get("title"),
                    author=extracted.get("author"),
                    description=extracted.get("description"),
                    published_at=extracted.get("published_at") if extracted.get("published_at") else None,
                    content_md=extracted.get("content_md"),
                    content_text=extracted.get("content_text"),
                    metadata_json=json.dumps({
                        "quality_score": extracted.get("quality_score"),
                        "word_count": extracted.get("word_count"),
                        "content_type": extracted.get("content_type"),
                    }),
                    quality_score=extracted.get("quality_score", 0.0),
                    content_type=extracted.get("content_type", "unknown"),
                    text_hash=text_hash,
                    crawl_status="crawled",
                )
                session.add(page)
                await session.commit()
                self.seen_hashes.add(text_hash)
                self.processed_since_save += 1
                logger.info(f"Indexed [{extracted['content_type']}] {url} (Q:{extracted['quality_score']}, {extracted['word_count']} words)")
            
            # Extract and queue outgoing links
            if depth < settings.max_depth:
                links = extract_links(html, url)
                for link in links:
                    if is_ai_relevant(link):
                        existing_q = await session.execute(select(CrawlQueue).where(CrawlQueue.url == link))
                        if not existing_q.scalar_one_or_none():
                            session.add(CrawlQueue(url=link, depth=depth+1, domain=get_domain(link), source_url=url))
                        
                        existing_l = await session.execute(
                            select(Link).where(Link.from_url == url, Link.to_url == link)
                        )
                        if not existing_l.scalar_one_or_none():
                            session.add(Link(from_url=url, to_url=link))
                await session.commit()
            
            await session.execute(update(CrawlQueue).where(CrawlQueue.url == url).values(status="done"))
            await session.commit()
    
    async def run(self, shutdown_flag=None):
        await self._init_state()
        logger.info("Crawler started. Storage guard: 180GB limit.")
        
        while True:
            if shutdown_flag and shutdown_flag():
                logger.info("Shutdown flag set, finishing current batch...")
                break
            
            if not self.storage_guard.check():
                gb = self.storage_guard.usage_gb()
                logger.warning(f"Storage guard triggered: {gb:.1f}GB used. Pausing crawler.")
                await asyncio.sleep(60)
                continue
            
            async with async_session() as session:
                result = await session.execute(
                    select(CrawlQueue)
                    .where(CrawlQueue.status == "pending")
                    .order_by(CrawlQueue.priority.desc())
                    .limit(settings.crawl_concurrency * 2)
                )
                rows = result.scalars().all()
            
            if not rows:
                logger.info("No pending URLs, sleeping...")
                await asyncio.sleep(5)
                continue
            
            async with async_session() as session:
                for row in rows:
                    await session.execute(
                        update(CrawlQueue).where(CrawlQueue.id == row.id).values(status="processing")
                    )
                await session.commit()
            
            results = await asyncio.gather(
                *[self.process_url(row.url, row.depth) for row in rows],
                return_exceptions=True
            )
            for res in results:
                if isinstance(res, Exception):
                    logger.error(f"Crawl batch error: {res}")
            
            # Save state every 50 successfully processed pages
            if self.processed_since_save >= 50:
                await self.save_state()
                self.processed_since_save = 0
