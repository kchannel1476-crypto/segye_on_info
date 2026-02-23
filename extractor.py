# extractor.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional, Dict, Any

import requests
from bs4 import BeautifulSoup


@dataclass
class ArticleExtract:
    url: str
    title: str = ""
    published: str = ""
    byline: str = ""
    content: str = ""
    og_image: str = ""


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
)


def fetch_html(url: str, timeout: int = 12) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    r.raise_for_status()
    return r.text


def _meta(soup: BeautifulSoup, key: str) -> str:
    # property=og:title / name=description 등
    node = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
    if node and node.get("content"):
        return node["content"].strip()
    return ""


def _first_text(soup: BeautifulSoup, selectors: list[str]) -> str:
    for sel in selectors:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt
    return ""


def _article_text(soup: BeautifulSoup) -> str:
    # segye 전용 후보(우선) + 공통 후보
    candidates = [
        "div.view_text",              # segye (실제 DOM에 맞게 조정 가능)
        "div#article_txt",           # segye
        "article",
        "div[itemprop='articleBody']",
        "div.article-body",
        "div#articleBody",
        "div#article-body",
        "div.newsct_article",
        "div#dic_area",
    ]
    for sel in candidates:
        el = soup.select_one(sel)
        if el:
            txt = el.get_text("\n", strip=True)
            if len(txt) > 200:
                return txt
    # fallback: body 전체에서 너무 짧지 않은 텍스트
    body = soup.body.get_text("\n", strip=True) if soup.body else ""
    return body[:5000]


def extract_article(url: str) -> ArticleExtract:
    html = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title = _meta(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else "")
    og_image = _meta(soup, "og:image")

    # 날짜/기자명은 사이트별이라 범용 selector + meta 혼합
    published = (
        _meta(soup, "article:published_time")
        or _first_text(soup, ["time", "span.t11", "span.date", "em.date"])
    )
    byline = _first_text(soup, ["span.byline", "p.byline", "em.byline", ".journalist", ".reporter"])

    content = _article_text(soup)

    return ArticleExtract(
        url=url,
        title=title,
        published=published,
        byline=byline,
        content=content,
        og_image=og_image,
    )


_NUM_RE = re.compile(r"(?<!\w)(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:%|조|억|만|원|명|건|배|년|월|일)?(?!\w)")


def has_numbers(text: str) -> bool:
    if not text:
        return False
    return bool(_NUM_RE.search(text))


def extract_numbers(text: str, limit: int = 8) -> list[str]:
    if not text:
        return []
    found = _NUM_RE.findall(text)
    # 중복 제거(순서 유지)
    out = []
    for x in found:
        if x not in out:
            out.append(x)
        if len(out) >= limit:
            break
    return out


def extract_numbers_with_context(text: str, limit: int = 8, context_chars: int = 40) -> list[dict]:
    """숫자와 주변 맥락(문맥)을 함께 반환. [{"value": str, "context": str}, ...]"""
    if not text:
        return []
    out = []
    seen = set()
    for m in _NUM_RE.finditer(text):
        if len(out) >= limit:
            break
        val = m.group(0)
        if val in seen:
            continue
        seen.add(val)
        start = max(0, m.start() - context_chars)
        end = min(len(text), m.end() + context_chars)
        ctx = text[start:end].replace("\n", " ").strip()
        out.append({"value": val, "context": ctx})
    return out
