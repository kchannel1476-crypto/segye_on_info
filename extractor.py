# extractor.py
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

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


def _split_sentences_ko(text: str):
    text = (text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return re.split(r"(?<=[\.\?\!]|다|요|함|됨)\s+", text)


def _normalize_number(value_str: str):
    return float(value_str.replace(",", ""))


def extract_numbers_with_context(text: str, max_items: int = 12) -> List[Dict[str, Any]]:
    """
    그룹 이름 충돌 없는 안전 버전
    """
    sentences = _split_sentences_ko(text)

    pattern = re.compile(
        r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%p|%|명|건|개|년|개월|일|시간|배|p|조|억|만|원)"
    )

    results = []
    seen = set()

    for sent in sentences:
        for m in pattern.finditer(sent):

            num_str = m.group(1)
            unit = m.group(2)
            raw = m.group(0)

            key = (num_str, unit)
            if key in seen:
                continue
            seen.add(key)

            try:
                val = _normalize_number(num_str)
            except Exception:
                continue

            results.append({
                "value": int(val) if val.is_integer() else val,
                "unit": unit,
                "raw": raw,
                "context": sent[:180],
                "label": "",
                "note": "",
                "trend": "neutral",
            })

            if len(results) >= max_items:
                break

        if len(results) >= max_items:
            break

    results = postprocess_numbers(results)
    return results


def _is_noise_number(raw: str, context: str) -> bool:
    raw = (raw or "").strip()
    ctx = (context or "").strip()

    # URL/조회 ID/기사번호/타임스탬프류
    if "newsView" in ctx or "http" in ctx or "www." in ctx:
        return True

    # 날짜/시간 같은 흔한 노이즈: 2026-02-23, 08:59:53, 20260223 등
    if re.search(r"\b20\d{2}[-/\.]\d{1,2}[-/\.]\d{1,2}\b", ctx):
        return True
    if re.search(r"\b\d{1,2}:\d{2}(:\d{2})?\b", ctx):
        return True
    if re.search(r"\b20\d{6,}\b", raw):  # 20260223 같은 덩어리
        return True

    # 페이지/회/차수/기수 같은 메타성 숫자
    if re.search(r"(제\s*\d+\s*(회|차|기)|\d+\s*(회|차|기))", ctx):
        return True

    return False


def postprocess_numbers(nums: List[Dict[str, Any]], title: str = "") -> List[Dict[str, Any]]:
    # 1) 노이즈 제거
    cleaned = []
    seen = set()
    for n in nums:
        raw = n.get("raw", "")
        ctx = n.get("context", "")
        if _is_noise_number(raw, ctx):
            continue

        key = (str(n.get("value")), n.get("unit", ""), raw)
        if key in seen:
            continue
        seen.add(key)

        cleaned.append(n)

    # 2) 우선순위 점수 (KPI에 쓸만한 것 먼저)
    def score(n):
        unit = n.get("unit", "")
        raw = n.get("raw", "")
        ctx = n.get("context", "")
        s = 0
        if "%" in unit or "%p" in unit:
            s += 50
        if unit in ("명", "건", "개"):
            s += 35
        if unit in ("조", "억", "만", "원"):
            s += 30
        if unit in ("년", "개월", "일", "시간"):
            s += 20
        # 기사 제목 근처에서 등장한 숫자를 조금 가산 (대충)
        if title and title[:12] and (title[:12] in ctx):
            s += 8
        # context가 길수록(문맥 풍부) 가산
        s += min(len(ctx), 180) / 60
        # 너무 작은 수(1,2 등)는 KPI로 의미 없을 확률 높아서 감점(단, % 제외)
        try:
            v = float(n.get("value"))
            if v <= 2 and ("%" not in unit):
                s -= 10
        except Exception:
            pass
        return s

    cleaned.sort(key=score, reverse=True)
    return cleaned


def has_numbers(text: str) -> bool:
    if not text:
        return False
    return bool(_NUM_RE.search(text))


def extract_numbers(text: str, limit: int = 8) -> list[str]:
    if not text:
        return []
    found = _NUM_RE.findall(text)
    out = []
    for x in found:
        if x not in out:
            out.append(x)
        if len(out) >= limit:
            break
    return out


def _kpi_bucket(unit: str) -> str:
    u = (unit or "").strip()
    if "%" in u:  # %, %p
        return "ratio"
    if u in ("명", "건", "개"):
        return "count"
    if u in ("원", "만", "억", "조") or ("원" in u):
        return "money"
    if u in ("년", "개월", "일", "시간"):
        return "time"
    return "other"


def _kpi_score(n: dict) -> float:
    unit = n.get("unit", "")
    ctx = n.get("context", "") or ""
    s = 0.0
    if "%" in unit:
        s += 50
    if unit in ("명", "건", "개"):
        s += 35
    if unit in ("조", "억", "만", "원") or ("원" in unit):
        s += 30
    if unit in ("년", "개월", "일", "시간"):
        s += 20

    s += min(len(ctx), 180) / 60.0

    try:
        v = float(n.get("value"))
        if v <= 2 and ("%" not in unit):
            s -= 10
    except Exception:
        pass

    if (n.get("label") or "").strip():
        s += 5

    return s


def choose_kpis(nums: list, k: int = 4) -> list:
    if not nums:
        return []

    candidates = []
    for n in nums:
        nn = dict(n)
        nn["_score"] = _kpi_score(nn)
        candidates.append(nn)
    candidates.sort(key=lambda x: x["_score"], reverse=True)

    buckets = {"ratio": [], "count": [], "money": [], "time": [], "other": []}
    for n in candidates:
        buckets[_kpi_bucket(n.get("unit", ""))].append(n)

    picked = []

    def take(bucket_name: str, limit: int):
        nonlocal picked
        for n in buckets[bucket_name]:
            if len(picked) >= k:
                return
            key = (str(n.get("value")), n.get("unit", ""))
            if any((str(p.get("value")), p.get("unit", "")) == key for p in picked):
                continue
            picked.append(n)
            if sum(1 for p in picked if _kpi_bucket(p.get("unit","")) == bucket_name) >= limit:
                return

    take("ratio", 1)
    take("count", 1)
    take("money", 1)
    take("time", 1)

    if len(picked) < k:
        take("ratio", 2)

    if len(picked) < k:
        for n in candidates:
            if len(picked) >= k:
                break
            key = (str(n.get("value")), n.get("unit", ""))
            if any((str(p.get("value")), p.get("unit", "")) == key for p in picked):
                continue
            picked.append(n)

    for p in picked:
        p.pop("_score", None)
    return picked[:k]
