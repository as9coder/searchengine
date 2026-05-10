import json
import re
import hashlib
import xml.etree.ElementTree as ET
from typing import Optional
import trafilatura


# Content type heuristics
CONTENT_TYPE_PATTERNS = {
    "paper": ["/abs/", "/pdf/", ".pdf", "/paper", "arxiv.org", "openreview.net", "proceedings"],
    "documentation": ["/docs/", "/doc/", "readme", "documentation", "/api/", "/guide/", "/tutorials/", "/reference"],
    "forum": ["/r/", "/forum", "/discuss", "/topic/", "/question/", "/thread", "lesswrong.com", "alignmentforum.org"],
    "benchmark": ["/benchmark", "/leaderboard", "paperswithcode.com/sota", "eval", "lmsys.org", "lmarena", "chatbot-arena"],
    "dataset": ["/datasets/", "/dataset/", "huggingface.co/datasets", "kaggle.com/datasets"],
    "news": ["/blog/", "/news/", "/article/", "/press/", "/release"],
}


def classify_content_type(url: str, markdown: str = "") -> str:
    url_l = url.lower()
    md_l = markdown.lower()[:2000]
    
    scores = {}
    for ctype, patterns in CONTENT_TYPE_PATTERNS.items():
        score = sum(1 for p in patterns if p in url_l)
        scores[ctype] = score
    
    # Markdown hints
    if "arxiv" in md_l or "abstract" in md_l and "introduction" in md_l and "references" in md_l:
        scores["paper"] = scores.get("paper", 0) + 2
    if "benchmark" in md_l and "result" in md_l:
        scores["benchmark"] = scores.get("benchmark", 0) + 1
    if "dataset" in md_l and "download" in md_l:
        scores["dataset"] = scores.get("dataset", 0) + 1
    if "```" in markdown and ("install" in md_l or "usage" in md_l or "example" in md_l):
        scores["documentation"] = scores.get("documentation", 0) + 1
    
    if not scores or max(scores.values()) == 0:
        return "blog" if "/blog/" in url_l or len(markdown) > 500 else "unknown"
    
    return max(scores, key=scores.get)


def _normalize_text(text: str) -> str:
    """Normalize text for deduplication hash."""
    text = re.sub(r"\s+", " ", text.lower().strip())
    text = re.sub(r"[^\w\s]", "", text)
    return text[:5000]  # First 5k chars is enough for dedup


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode()).hexdigest()


def _xml_to_markdown(element) -> str:
    """Convert trafilatura XML to clean Markdown."""
    tag = element.tag
    text = element.text or ""
    tail = element.tail or ""
    children = "".join(_xml_to_markdown(child) for child in element)
    
    if tag == "main":
        return f"{text}{children}{tail}"
    
    if tag == "p":
        content = f"{text}{children}".strip()
        if not content:
            return tail
        return f"\n\n{content}{tail}"
    
    if tag == "head":
        level = element.get("rend", "h2")
        level_num = int(level[1]) if level.startswith("h") and level[1].isdigit() else 2
        hashes = "#" * level_num
        content = f"{text}{children}".strip()
        return f"\n\n{hashes} {content}\n\n{tail}"
    
    if tag == "list":
        return f"{children}{tail}"
    
    if tag == "item":
        content = f"{text}{children}".strip()
        return f"\n- {content}{tail}"
    
    if tag == "code":
        content = f"{text}{children}".rstrip()
        if not content:
            return tail
        return f"\n\n```\n{content}\n```\n\n{tail}"
    
    if tag == "quote":
        content = f"{text}{children}".strip()
        lines = content.split("\n")
        quoted = "\n".join(f"> {line}" for line in lines)
        return f"\n\n{quoted}\n\n{tail}"
    
    if tag == "table":
        return f"\n\n{children}\n\n{tail}"
    
    if tag == "row":
        cells = []
        for cell in element:
            cell_text = f"{cell.text or ''}{''.join(_xml_to_markdown(c) for c in cell)}".strip()
            cells.append(cell_text)
        return f"| {' | '.join(cells)} |\n{tail}"
    
    if tag == "cell":
        return f"{text}{children}{tail}"
    
    if tag == "ref":
        target = element.get("target", "")
        content = f"{text}{children}".strip()
        if target:
            return f"[{content}]({target}){tail}"
        return f"{content}{tail}"
    
    return f"{text}{children}{tail}"


def _clean_markdown(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\n{3,}", "\n\n", text)
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped in ["|", "---", "* * *", "___", ""] and not lines:
            continue
        if re.match(r"^(Home|Index|Previous|Next|Back|Menu|Archives|Cookies|Privacy)\s*[/|>]", stripped, re.I):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    text = re.sub(r"^(#{1,6})([^\s#])", r"\1 \2", text, flags=re.MULTILINE)
    return text


def _compute_quality(metadata: dict, markdown: str) -> float:
    score = 0.0
    if metadata.get("title"):
        score += 0.15
    md_len = len(markdown)
    if 1000 <= md_len <= 30000:
        score += 0.25
    elif md_len > 30000:
        score += 0.15
    elif md_len > 500:
        score += 0.10
    if metadata.get("description"):
        score += 0.10
    if metadata.get("date"):
        score += 0.05
    words = len(markdown.split())
    lines = max(1, len(markdown.split("\n")))
    density = words / lines
    if density > 5:
        score += 0.20
    elif density > 2:
        score += 0.10
    has_structure = any(c in markdown for c in ["##", "\n- ", "```"])
    if has_structure:
        score += 0.15
    link_count = markdown.count("](")
    if link_count < words / 50:
        score += 0.10
    return min(1.0, score)


def extract_content(html: str, url: str) -> Optional[dict]:
    """Production-grade extraction. Returns None if quality too low."""
    if not html or len(html) < 200:
        return None
    
    config = trafilatura.settings.use_config()
    config.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")
    
    # Metadata + raw text
    metadata_json = trafilatura.extract(
        html, url=url, output_format="json",
        include_comments=False, include_tables=True, include_formatting=True,
        config=config,
    )
    if not metadata_json:
        return None
    
    metadata = json.loads(metadata_json)
    raw_text = metadata.get("text", "")
    
    if not raw_text or len(raw_text) < 200:
        return None
    
    # Structured XML for markdown
    xml_output = trafilatura.extract(
        html, url=url, output_format="xml",
        include_comments=False, include_tables=True, include_formatting=True,
        include_images=False, config=config,
    )
    
    if xml_output:
        try:
            root = ET.fromstring(xml_output)
            markdown = _xml_to_markdown(root)
        except ET.ParseError:
            markdown = raw_text
    else:
        markdown = raw_text
    
    markdown = _clean_markdown(markdown)
    
    quality = _compute_quality(metadata, markdown)
    if quality < 0.20:
        return None
    
    content_type = classify_content_type(url, markdown)
    text_hash = _text_hash(raw_text)
    
    return {
        "title": metadata.get("title"),
        "author": metadata.get("author"),
        "description": metadata.get("description"),
        "published_at": metadata.get("date"),
        "content_md": markdown,
        "content_text": raw_text,
        "quality_score": round(quality, 3),
        "content_type": content_type,
        "text_hash": text_hash,
        "word_count": len(raw_text.split()),
    }
