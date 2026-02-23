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
    # 범용: article 태그 우선, 없으면 본문 후보 div들 탐색
    candidates = [
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
