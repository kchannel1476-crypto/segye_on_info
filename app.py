import os
import base64
import tempfile
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import time
import re
import streamlit as st
import json


def xml_escape(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    # XML에서 깨지는 제어문자 제거 (탭/개행은 허용)
    s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
    s = (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
    return s


def sanitize_svg_for_png(svg: str) -> str:
    if not svg:
        return ""
    svg = svg.replace("\ufeff", "")
    svg = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", svg)
    svg = re.sub(r"^\s*<\?xml[^>]*\?>\s*", "", svg)
    return svg


def strip_svg_imports(svg: str) -> str:
    """SVG 문자열에서 @import url(...); 제거 (일부 렌더러 호환용)."""
    if not svg:
        return ""
    return re.sub(r"@import\s+url\([^;]+;\s*", "", svg)


def strip_css_import(svg: str) -> str:
    """SVG에서 @import url(...); 제거."""
    if not svg:
        return ""
    return re.sub(r"@import\s+url\([^)]*\);\s*", "", svg)


def _font_file_uri(path: str) -> str:
    """절대 경로를 file:// URI로 (cairosvg가 로컬 폰트 로드하도록)."""
    try:
        from pathlib import Path
        return Path(os.path.abspath(path)).as_uri()
    except Exception:
        return ""


# PNG 한글 폰트 검색 경로 (프로젝트 fonts/ → SEGYE_FONTS_DIR → 아래 경로 순)
_FONTS_FALLBACK_DIRS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts"),
    os.environ.get("SEGYE_FONTS_DIR", "").strip() or None,
    os.path.expanduser(r"~\Desktop\static"),
    r"c:\Users\segye\Desktop\static",
]


def _find_font_path(filename: str) -> str:
    """NotoSansKR-*.ttf 파일을 검색 경로에서 찾아 절대 경로 반환."""
    for d in _FONTS_FALLBACK_DIRS:
        if not d or not os.path.isdir(d):
            continue
        path = os.path.join(d, filename)
        if os.path.isfile(path):
            return path
    return ""


def _prepare_fonts_for_png() -> tuple[str, str]:
    """
    PNG 변환용 폰트 file:// URI 반환.
    TTF를 임시 디렉터리에 복사해 짧은 경로의 file:// 로 씀 (cairosvg는 base64 미지원, file:// 시도).
    반환: (uri_regular, uri_bold)
    """
    uri_r, uri_b = "", ""
    try:
        tmp = tempfile.mkdtemp(prefix="segye_fonts_")
        for name in ("NotoSansKR-Regular.ttf", "NotoSansKR-Bold.ttf"):
            src = _find_font_path(name)
            if not src or not os.path.isfile(src):
                continue
            dst = os.path.join(tmp, name)
            shutil.copy2(src, dst)
            uri = Path(dst).resolve().as_uri()
            if "Regular" in name:
                uri_r = uri
            else:
                uri_b = uri
        # 임시 폴더는 프로세스 종료 시 정리되도록 둠 (또는 atexit로 삭제 가능)
    except Exception:
        pass
    return (uri_r, uri_b)


def svg_fonts_to_absolute_paths(svg: str) -> str:
    """
    PNG 변환 시 한글 폰트가 적용되도록 SVG의 폰트 URL을 file:// 절대 경로로 치환.
    cairosvg는 base64 @font-face를 지원하지 않으므로, TTF를 임시 폴더에 복사한 file:// 사용.
    """
    if not svg:
        return svg
    uri_regular, uri_bold = _prepare_fonts_for_png()
    out = svg
    if uri_regular:
        out = out.replace('url("fonts/NotoSansKR-Regular.ttf")', f'url("{uri_regular}")')
        out = out.replace("url('fonts/NotoSansKR-Regular.ttf')", f'url("{uri_regular}")')
        out = re.sub(
            r'url\("data:font/ttf;base64,[^"]+"\)(?=\s*format\(\'truetype\'\)\s*;\s*font-weight:\s*400)',
            f'url("{uri_regular}")',
            out,
            count=1,
        )
    if uri_bold:
        out = out.replace('url("fonts/NotoSansKR-Bold.ttf")', f'url("{uri_bold}")')
        out = out.replace("url('fonts/NotoSansKR-Bold.ttf')", f'url("{uri_bold}")')
        out = re.sub(
            r'url\("data:font/ttf;base64,[^"]+"\)(?=\s*format\(\'truetype\'\)\s*;\s*font-weight:\s*700)',
            f'url("{uri_bold}")',
            out,
            count=1,
        )
    return out


def get_openai_client():
    try:
        from openai import OpenAI
    except Exception:
        return None
    key = None
    try:
        key = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        pass
    key = key or os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    return OpenAI(api_key=key)


def _safe_json_loads(s: str):
    """JSON 파싱. 실패 시 문자열에서 마지막 {} 블록 추출 후 재시도."""
    if not s or not s.strip():
        return None
    try:
        return json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, flags=re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None


def infer_kpi_labels_with_ai(
    title: str,
    article_text: str,
    numbers: List[Dict[str, Any]],
    publisher: str = "세계일보",
) -> Optional[List[Dict[str, Any]]]:
    """
    numbers item 예시(권장):
      {
        "value": 35,
        "unit": "%",
        "raw": "35%",
        "context": "…찬성 비율은 35%…",
        "note": "",
        "trend": "neutral"
      }
    """
    client = get_openai_client()
    if not client:
        return None

    text = (article_text or "").strip()
    if len(text) > 6000:
        text = text[:3500] + "\n...\n" + text[-2000:]

    nums = (numbers or [])[:12]

    schema_hint = {
        "kpis": [
            {
                "index": 0,
                "label": "무엇의 수치인지(짧게)",
                "value": "원본 value 유지",
                "unit": "원본 unit 유지(없으면 빈문자)",
                "note": "필요하면 10~18자 설명(선택)",
                "trend": "up|down|neutral (선택, 모르면 neutral)"
            }
        ]
    }

    system = (
        "너는 세계일보 인포그래픽 편집 데스크다. "
        "입력: 기사 제목/본문 일부 + 숫자리스트(numbers: value/unit/raw/context). "
        "출력: 각 숫자에 대해 '라벨(label)'을 만든다.\n\n"
        "규칙:\n"
        "1) 반드시 context(문장)에서 의미가 확인될 때만 라벨을 채운다.\n"
        "2) 의미가 불명확하면 label은 빈 문자열로 두고 note에 '문맥 부족'이라고 적는다.\n"
        "3) 라벨은 6~10자 내의 짧은 명사구(예: '찬성 비율', '응답자 수', '피해자 수').\n"
        "4) 절대 기사에 없는 단어/수치를 만들어내지 않는다.\n"
        "5) trend는 ▲/▼/증가/감소/상승/하락이 context/raw에 있을 때만 up/down, 아니면 neutral.\n"
    )

    user = {
        "publisher": publisher,
        "title": title,
        "article_excerpt": text,
        "numbers": nums,
        "output_schema": schema_hint
    }

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        data = _safe_json_loads(content or "")
        if not data or "kpis" not in data:
            return None

        kpis = data["kpis"]
        if not isinstance(kpis, list):
            return None

        merged = []
        for i, n in enumerate(nums):
            out = next((x for x in kpis if isinstance(x, dict) and x.get("index") == i), None)
            label = (out.get("label") if out else "") or ""
            note = (out.get("note") if out else "") or n.get("note", "") or ""
            trend_src = (n.get("raw", "") + " " + n.get("context", "")).strip()
            trend = (out.get("trend") if out else "") or classify_trend(trend_src)
            trend = trend if trend in ("up", "down", "neutral") else "neutral"

            merged.append({
                **n,
                "label": label.strip(),
                "note": note.strip(),
                "trend": trend,
            })

        return merged

    except Exception as e:
        st.warning(f"AI 라벨 생성 실패: {e}")
        return None


def enrich_labels(title: str, summary: str, text: str, numbers: list[dict]) -> dict:
    """숫자 목록에 대해 label 생성 후 JSON 반환. 실패 시 빈 dict."""
    client = get_openai_client()
    if not client:
        return {}

    prompt = f"""
기사 제목:
{title}

기사 요약:
{summary}

본문 일부:
{(text or "")[:2000]}

숫자 목록:
{json.dumps(numbers, ensure_ascii=False)}

각 숫자에 대해 label(6단어 이하 KPI 라벨), value(숫자만), unit(단위), note(맥락 한 줄), drop(불명확 시 true)를 채워 JSON 반환.
형식: {"items": [{"label":"", "value":"", "unit":"", "note":"", "drop": false 또는 true}, ...]} 순서는 숫자 목록과 동일하게.
"""

    try:
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "뉴스 데이터 라벨 생성기"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = res.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception:
        return {}


def refine_numbers_with_openai(
    numbers: list[dict],
    title_hint: str = "",
    summary: str = "",
    text: str = "",
) -> list[dict]:
    """
    input: [{"label": "...", "value": "3.5%", "context": "..."}...]
    output: [{"label": "기준금리", "value": "3.50", "unit": "%", "note": "…"} ...]
    text/summary 있으면 enrich_labels로 먼저 라벨 생성 후 사용.
    """
    client = get_openai_client()
    if client is None:
        return numbers

    if not numbers:
        return []

    nums_in = numbers[:8]
    title = title_hint or ""
    article_text = (text or "").strip()
    article_summary = (summary or "").strip()

    if article_text or article_summary:
        enriched = enrich_labels(title, article_summary, article_text, nums_in)
        items = enriched.get("items") or []
        for i, n in enumerate(nums_in):
            if i < len(items) and items[i].get("label"):
                n["label"] = (items[i].get("label") or "").strip()

    system = (
        "너는 경제·정책·사회 뉴스를 해석하는 데이터 에디터다. "
        "주어진 숫자 리스트를 보고, 기사 맥락에 맞는 '짧고 명확한 KPI 라벨'을 생성하라. "
        "규칙: 6단어 이하, 뉴스 그래픽 스타일, 구체적 의미 반영, 모호한 표현 금지, "
        "단순 '수치'·'데이터' 금지, 가능한 경우 단위 의미 포함. "
        "기사에 근거 없는 해석/추측은 하지 않는다."
    )

    summary_for_prompt = article_summary or ""
    text_excerpt = (
        article_text[:2000]
        if article_text
        else "\n".join(
            (i.get("context") or "").strip() for i in nums_in if (i.get("context") or "").strip()
        )[:2000] or "(없음)"
    )
    numbers_json = json.dumps(nums_in, ensure_ascii=False, indent=0)

    user_content = f"""기사 제목:
{title}

기사 요약:
{summary_for_prompt}

본문 일부:
{text_excerpt}

숫자 목록:
{numbers_json}

각 숫자에 대해 label 필드를 생성해 JSON으로 반환하라.
각 item에 대해 label(6단어 이하 KPI 라벨), value(숫자만), unit(단위), note(맥락 한 줄), drop(불명확 시 true)를 채워라.
출력 형식: {{"items": [{{"label":"", "value":"", "unit":"", "note":"", "drop": false 또는 true}}, ...]}} 순서는 숫자 목록과 동일하게."""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        data = json.loads(raw)
    except Exception:
        return numbers

    out = []
    for it in data.get("items", []):
        if it.get("drop") is True:
            continue
        out.append({
            "label": (it.get("label") or "").strip(),
            "value": (it.get("value") or "").strip(),
            "unit": (it.get("unit") or "").strip(),
            "note": (it.get("note") or "").strip(),
        })

    return out[:6]


def analyze_for_desk(article_text: str, title_hint: str = "", url: str = "") -> dict:
    client = get_openai_client()
    if client is None:
        raise RuntimeError("OPENAI_API_KEY not set")

    article_text = (article_text or "").strip()
    if not article_text:
        raise ValueError("empty article_text")

    if len(article_text) > 12000:
        article_text = article_text[:12000] + "\n...(생략)..."

    system = (
        "너는 신문사 편집 데스크의 AI 보조다. "
        "기사 본문만을 근거로 편집/검증 관점의 리포트를 만든다. "
        "추측 금지, 사실 단정 금지. 기사에 없는 정보는 '알 수 없음'으로 표시."
    )

    prompt = {
        "task": "desk_analysis",
        "inputs": {"url": url, "title_hint": title_hint, "article_text": article_text},
        "output_spec": {
            "summary_1": "한 문장 요약(30~60자)",
            "angle": "기사의 관점/프레이밍(간단)",
            "key_facts": ["팩트 5개(각 20~60자)"],
            "numbers_check": [
                {"claim": "수치 주장", "value": "숫자", "unit": "단위", "needs_verify": "True/False", "why": "이유"}
            ],
            "sensitivity": {
                "level": "low|medium|high",
                "reasons": ["민감 요소"],
                "suggestions": ["톤 조정/표현 완화 제안"]
            },
            "legal_copyright": {"risk": "low|medium|high", "notes": ["인용/이미지 주의"]},
            "headlines": ["대체 헤드라인 3개"],
            "seo_keywords": ["키워드 8개"],
            "followups": ["추가 취재/확인 질문 5개"]
        }
    }

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or ""
        return json.loads(raw)
    except Exception as e:
        raise RuntimeError(f"desk analysis failed: {e}") from e


from jinja2 import Environment, FileSystemLoader, select_autoescape
from extractor import extract_article, has_numbers, extract_numbers_with_context, choose_kpis

DESK_KEY = "원하는_긴_비밀번호"


def is_desk_mode() -> bool:
    qp = st.query_params
    return qp.get("mode", "") == "desk"


def desk_auth_ok() -> bool:
    if st.session_state.get("desk_authed"):
        return True

    key = None
    try:
        key = st.secrets.get("DESK_KEY")
    except Exception:
        key = None
    if not key:
        return False

    with st.sidebar:
        st.markdown("### 데스크 모드")
        pw = st.text_input("DESK KEY", type="password")
        if st.button("Unlock"):
            if pw == key:
                st.session_state["desk_authed"] = True
                st.success("Unlocked")
                return True
            st.error("Wrong key")
    return False


def normalize_url(u: str) -> str:
    u = (u or "").strip()
    if not u:
        return ""
    # 사용자가 www 없이 입력해도 보정
    if u.startswith("www."):
        u = "https://" + u
    return u


def is_allowed_url(u: str) -> bool:
    try:
        host = urlparse(u).netloc.lower()
        # segye.com 하위 도메인 포함 허용 (www.segye.com 등)
        return host.endswith("segye.com")
    except Exception:
        return False


def guard_rate_limit(seconds: int = 8):
    now = time.time()
    if now - st.session_state.last_fetch_ts < seconds:
        st.warning(f"요청이 너무 빠릅니다. {seconds}초 후 다시 시도해주세요.")
        st.stop()
    st.session_state.last_fetch_ts = now


_SENT_SPLIT = re.compile(r"(?<=[.!?。…])\s+|\n+")


def make_simple_keypoints(text: str, k: int = 3) -> list[str]:
    text = (text or "").strip()
    if not text:
        return ["", "", ""]
    sents = [s.strip() for s in _SENT_SPLIT.split(text) if len(s.strip()) >= 25]
    picked = sents[:k]
    picked = (picked + ["", "", ""])[:3]
    return picked


def classify_trend(value_any) -> str:
    """
    value_any: str | int | float | None
    반환: "up" | "down" | "neutral"
    규칙:
      - 텍스트에 '증가/상승/늘' 또는 ▲ / + 포함 → up
      - 텍스트에 '감소/하락/줄' 또는 ▼ / - 포함 → down
      - 숫자만 있으면 방향 판단 불가 → neutral
    """
    if value_any is None:
        return "neutral"

    # 숫자면 방향 판단 불가(문맥이 없으니)
    if isinstance(value_any, (int, float)):
        return "neutral"

    # 그 외는 문자열로 처리
    s = str(value_any).strip()

    # 기호 기반
    if "▲" in s or "상승" in s or "증가" in s or "늘" in s or re.search(r"\+\s*\d", s):
        return "up"
    if "▼" in s or "하락" in s or "감소" in s or "줄" in s or re.search(r"-\s*\d", s):
        return "down"

    return "neutral"


def make_simple_callout(text: str, max_len: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    t = text.replace("\n", " ")
    return (t[:max_len] + "…") if len(t) > max_len else t


def wrap_headline(text: str, width: int = 18) -> list[str]:
    words = (text or "").strip().split()
    lines = []
    current = ""

    for w in words:
        if len(current + w) < width:
            current += w + " "
        else:
            lines.append(current.strip())
            current = w + " "

    if current.strip():
        lines.append(current.strip())
    return lines[:2]


# (선택) SVG -> PNG 변환
# pip install cairosvg
# import cairosvg

st.set_page_config(page_title="SEGYE.ON Infographic", layout="wide")

# -----------------------------
# Jinja2 환경
# -----------------------------
env = Environment(
    loader=FileSystemLoader("templates"),
    autoescape=select_autoescape(enabled_extensions=("svg", "j2", "xml")),
)
_tpl = {
    "story_lite": env.get_template("story_lite.svg.j2"),
    "data_focus": env.get_template("data_focus.svg.j2"),
    "timeline": env.get_template("timeline.svg.j2"),
    "compare": env.get_template("compare.svg.j2"),
}


def _load_font_base64():
    """regular.txt, bold.txt가 있으면 base64 문자열 반환 (SVG data URI 임베드용)."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    out = {}
    for key, filename in (("font_regular_b64", "regular.txt"), ("font_bold_b64", "bold.txt")):
        path = os.path.join(base_dir, filename)
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="ascii") as f:
                    out[key] = f.read().strip()
            except Exception:
                pass
    return out


def render_svg(template_key: str, render_model: dict) -> str:
    tpl = _tpl.get(template_key) or _tpl["story_lite"]
    chart = render_model.get("chart") if isinstance(render_model, dict) else None
    rm = {**render_model, "chart": chart}
    rm.update(_load_font_base64())
    return tpl.render(**rm)

def default_spec():
    return {
        "meta": {"source_url":"", "title":"", "publisher":"세계일보", "date":"", "byline":"", "language":"ko"},
        "style": {"brand":"segye", "tone":"clean_serif_like", "density":"low", "output":["svg","png"]},
        "content": {
            "headline":"", "dek":"", "keywords":[],
            "key_points":[
                {"text":"", "evidence":"", "confidence":0.0},
                {"text":"", "evidence":"", "confidence":0.0},
                {"text":"", "evidence":"", "confidence":0.0}
            ],
            "quote":{"speaker":"", "org":"", "text":""},
            "numbers":[],
            "numbers_all":[],
            "charts":[],
            "timeline":[],
            "comparison":{"left_title":"", "right_title":"", "items":[]},
            "callouts":[{"title":"핵심 맥락", "body":""}],
            "sources":[]
        },
        "layout":{"template":"story_lite", "ratio":"1:1", "sections":["headline","key_points","callout","sources"]}
    }

def choose_layout(spec: dict) -> dict:
    c = spec.get("content", {})
    if len(c.get("charts", [])) >= 1 or len(c.get("numbers", [])) >= 2:
        return {"template":"data_focus", "ratio":"1:1", "sections":["headline","chart","key_points","sources"]}
    if len(c.get("timeline", [])) >= 4:
        return {"template":"timeline", "ratio":"1:1", "sections":["headline","timeline","key_points","sources"]}
    comp_items = c.get("comparison", {}).get("items", [])
    if len(comp_items) >= 3:
        return {"template":"compare", "ratio":"1:1", "sections":["headline","comparison","key_points","sources"]}
    return {"template":"story_lite", "ratio":"1:1", "sections":["headline","key_points","callout","sources"]}

def build_render_model(spec: dict) -> dict:
    meta = spec["meta"]
    c = spec["content"]

    kp = [xml_escape(x.get("text", "")) for x in c.get("key_points", [])]
    kp = (kp + ["", "", ""])[:3]

    quote = c.get("quote", {})
    quote_line = xml_escape(quote.get("text", "").strip())

    callout = (c.get("callouts", [{}]) + [{}])[0]
    callout_title = xml_escape(callout.get("title", "").strip())
    callout_body = xml_escape(callout.get("body", "").strip())

    url = meta.get("source_url", "").strip()
    url_short = url.replace("https://", "").replace("http://", "")
    if len(url_short) > 42:
        url_short = url_short[:39] + "..."
    sources_line = xml_escape(f"출처: {meta.get('publisher','')} · {meta.get('date','')} · {url_short}".strip())

    # data_focus
    charts = c.get("charts", [])
    chart_title = xml_escape(charts[0].get("title", "").strip() if charts else "")
    chart_note = xml_escape(charts[0].get("note", "").strip() if charts else "")
    nums = c.get("numbers", [])[:4]
    for n in nums:
        if not n.get("label"):
            n["label"] = "핵심 지표"
        n["trend"] = classify_trend(n.get("raw") or n.get("context") or "")
    numbers = nums[:2]
    big1 = xml_escape(str(numbers[0].get("value", "")) if numbers else "")
    big1_label = xml_escape((numbers[0].get("label", "") or numbers[0].get("context", "") or "").strip() if numbers else "")
    big2 = xml_escape(str(numbers[1].get("value", "")) if len(numbers) > 1 else "")
    big2_label = xml_escape((numbers[1].get("label", "") or numbers[1].get("context", "") or "").strip() if len(numbers) > 1 else "")
    if c.get("chart"):
        if c["chart"].get("title"):
            chart_title = xml_escape(c["chart"].get("title", ""))
        if c["chart"].get("note"):
            chart_note = xml_escape(c["chart"].get("note", ""))

    # timeline
    tl = c.get("timeline", [])[:8]
    text_timeline = [{"date": xml_escape(t.get("date", "")), "event": xml_escape(t.get("event", ""))} for t in tl]

    # compare
    comp = c.get("comparison", {}) or {}
    comp_items = (comp.get("items") or [])[:6]
    compare_rows = [{"left": xml_escape(i.get("left", "")), "right": xml_escape(i.get("right", ""))} for i in comp_items]

    # --- Chart data preparation ---
    chart_items = []
    values = []

    for n in nums:
        raw = str(n.get("value", "")).replace(",", "").strip()
        try:
            val = float(raw)
        except Exception:
            continue
        label = xml_escape((n.get("label") or "").strip())
        values.append(val)
        chart_items.append({"label": label, "value": val})

    chart_max = max(values) if values else 0.0
    for item in chart_items:
        item["norm"] = (item["value"] / chart_max) if chart_max > 0 else 0.0

    chart_type = "donut" if len(chart_items) == 2 else "bar"
    chart_obj = {"items": chart_items, "max": chart_max, "type": chart_type}

    headline = c.get("headline", "").strip()
    headline_lines = [xml_escape(line) for line in wrap_headline(headline)]

    # numbers: SVG에 넣을 복사본에 escape 적용 (원본 spec은 변경하지 않음)
    numbers_escaped = [
        {
            "label": xml_escape(n.get("label", "")),
            "value": xml_escape(str(n.get("value", ""))),
            "unit": xml_escape(n.get("unit", "")),
            "note": xml_escape(n.get("note", "")),
            "trend": n.get("trend", "neutral"),
        }
        for n in nums
    ]

    return {
        "canvas": {"w": 1080, "h": 1080, "margin": 72},
        "text": {
            "headline": xml_escape(headline),
            "headline_lines": headline_lines,
            "dek": xml_escape(c.get("dek", "").strip()),
            "keywords": [xml_escape(k) for k in c.get("keywords", [])[:6]],
            "key_points": kp,
            "quote_line": quote_line,
            "callout_title": callout_title,
            "callout_body": callout_body,
            "sources_line": sources_line,
            "chart_title": chart_title,
            "chart_note": chart_note,
            "big1": big1,
            "big1_label": big1_label,
            "big2": big2,
            "big2_label": big2_label,
            "timeline": text_timeline,
            "left_title": xml_escape(comp.get("left_title", "")),
            "right_title": xml_escape(comp.get("right_title", "")),
            "compare_rows": compare_rows,
        },
        "flags": {
            "has_quote": bool(quote_line),
            "has_callout": bool(callout_title or callout_body)
        },
        "numbers": c.get("numbers", []),
        "chart": (c.get("charts") or [None])[0],
    }

# -----------------------------
# Session state
# -----------------------------
if "spec" not in st.session_state:
    st.session_state.spec = default_spec()
if "dirty" not in st.session_state:
    st.session_state.dirty = False
if "svg" not in st.session_state:
    st.session_state.svg = ""
if "last_fetch_ts" not in st.session_state:
    st.session_state.last_fetch_ts = 0.0


def generate_draft_with_openai(article_text: str, title_hint: str = "") -> dict:
    """기사 본문으로 headline, dek, key_points(3), callout, quote 초안 생성. 실패 시 규칙 기반 fallback."""
    kp = make_simple_keypoints(article_text, k=3)
    fallback = {
        "headline": (title_hint or "").strip(),
        "dek": "",
        "key_points": kp,
        "callout_title": "핵심 맥락",
        "callout_body": make_simple_callout(article_text),
        "quote_text": "",
    }
    client = get_openai_client()
    if not client:
        return fallback
    prompt = f"""다음 기사 내용을 인포그래픽 초안으로 요약하세요. 반드시 JSON만 출력.
형식: {{"headline":"","dek":"","key_points":["","",""],"callout_title":"","callout_body":"","quote_text":""}}

key_points 작성 기준: 기사에서 독자가 알아야 할 핵심 인사이트 3개 작성. 숫자와 의미 포함, 짧고 강하게, 뉴스 톤 유지.

기사:
{(article_text or "")[:6000]}
"""
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content or "{}"
        data = json.loads(raw)
        return {
            "headline": (data.get("headline") or fallback["headline"]).strip(),
            "dek": (data.get("dek") or "").strip(),
            "key_points": [(data.get("key_points") or [])[i] if i < len(data.get("key_points") or []) else fallback["key_points"][i] for i in range(3)],
            "callout_title": (data.get("callout_title") or fallback["callout_title"]).strip(),
            "callout_body": (data.get("callout_body") or fallback["callout_body"]).strip(),
            "quote_text": (data.get("quote_text") or "").strip(),
        }
    except Exception:
        return fallback


def run_desk_mode():
    st.title("SEGYE.ON — AI 편집 데스크")
    st.caption("세계일보 기사 기반 자동 분석/검증/인포그래픽 생성 콘솔")

    left, right = st.columns([0.42, 0.58], gap="large")

    with left:
        st.subheader("입력")
        url = st.text_input("세계일보 URL (segye.com)", value=st.session_state.spec["meta"].get("source_url", ""))
        url = normalize_url(url)

        c1, c2, c3 = st.columns(3)
        do_fetch = c1.button("URL 불러오기", use_container_width=True)
        do_draft = c2.button("AI 초안", use_container_width=True)
        do_analyze = c3.button("데스크 분석", use_container_width=True)

        if do_fetch:
            if not url or not is_allowed_url(url):
                st.error("세계일보(segye.com) URL만 지원합니다.")
                st.stop()
            guard_rate_limit(6)
            data = extract_article(url)

            st.session_state.spec["meta"]["source_url"] = data.url
            st.session_state.spec["meta"]["title"] = data.title
            st.session_state.spec["meta"]["date"] = data.published
            st.session_state.spec["meta"]["byline"] = data.byline

            st.session_state["article_text"] = data.content
            st.session_state["og_image"] = data.og_image

            if not st.session_state.spec["content"]["headline"]:
                st.session_state.spec["content"]["headline"] = (data.title or "").strip()

            nums = extract_numbers_with_context(data.content, max_items=20)
            st.session_state.spec["content"]["numbers_all"] = nums or []
            st.session_state.spec["content"]["numbers"] = choose_kpis(nums or [], k=4)
            st.session_state["template_hint"] = "data_focus" if len(st.session_state.spec["content"]["numbers"]) >= 2 else "story_lite"

            st.success("기사 로드 완료")

        if do_draft:
            article_text = st.session_state.get("article_text", "")
            if not article_text:
                st.warning("먼저 URL 불러오기를 하세요.")
                st.stop()

            draft = generate_draft_with_openai(article_text, st.session_state.spec["meta"].get("title", ""))
            st.session_state.spec["content"]["headline"] = draft["headline"] or st.session_state.spec["content"]["headline"]
            st.session_state.spec["content"]["dek"] = draft.get("dek", "")
            st.session_state.spec["content"]["key_points"][0]["text"] = draft["key_points"][0]
            st.session_state.spec["content"]["key_points"][1]["text"] = draft["key_points"][1]
            st.session_state.spec["content"]["key_points"][2]["text"] = draft["key_points"][2]
            st.session_state.spec["content"]["callouts"][0]["title"] = draft.get("callout_title", "핵심 맥락")
            st.session_state.spec["content"]["callouts"][0]["body"] = draft.get("callout_body", "")
            st.session_state.spec["content"]["quote"]["text"] = draft.get("quote_text", "")

            nums_raw = extract_numbers_with_context(article_text, max_items=10)
            nums_refined = refine_numbers_with_openai(
                nums_raw,
                title_hint=st.session_state.spec["meta"].get("title", ""),
                text=st.session_state.get("article_text", ""),
            )
            st.session_state.spec["content"]["numbers"] = nums_refined
            st.session_state["template_hint"] = "data_focus" if len(nums_refined) >= 2 else "story_lite"
            st.success("AI 초안 생성 완료")

        if do_analyze:
            article_text = st.session_state.get("article_text", "")
            if not article_text:
                st.warning("먼저 URL 불러오기를 하세요.")
                st.stop()

            with st.spinner("데스크 분석 중..."):
                report = analyze_for_desk(
                    article_text=article_text,
                    title_hint=st.session_state.spec["meta"].get("title", ""),
                    url=st.session_state.spec["meta"].get("source_url", "")
                )
                st.session_state["desk_report"] = report
            st.success("데스크 리포트 생성 완료")

        do_label = st.button("🧠 라벨 자동 생성", use_container_width=True)
        if do_label:
            article_text = st.session_state.get("article_text", "")
            numbers = list(st.session_state.spec["content"].get("numbers", []))
            if not article_text:
                st.warning("먼저 URL 불러오기를 하세요.")
                st.stop()
            if not numbers:
                st.warning("먼저 AI 초안을 실행해 숫자를 추출하세요.")
                st.stop()
            with st.spinner("라벨 생성 중..."):
                enriched = enrich_labels(
                    st.session_state.spec["meta"].get("title", ""),
                    "",
                    article_text,
                    numbers,
                )
            items = enriched.get("items") or []
            for i, n in enumerate(numbers):
                if i < len(items) and items[i].get("label"):
                    n["label"] = (items[i].get("label") or "").strip()
            st.session_state.spec["content"]["numbers"] = numbers
            st.success("라벨 적용 완료")

        st.divider()
        st.subheader("KPI 라벨 자동 생성(AI)")
        if st.button("AI로 KPI 라벨 채우기", use_container_width=True, key="kpi_label_desk"):
            numbers = st.session_state.spec["content"].get("numbers", [])
            if not numbers:
                st.info("추출된 수치가 없습니다. (본문에 숫자가 없거나 추출 규칙에 안 잡혔어요)")
            else:
                title = st.session_state.spec["meta"].get("title", "") or st.session_state.spec["content"].get("headline", "")
                article_text = st.session_state.get("article_text", "") or ""
                labeled = infer_kpi_labels_with_ai(
                    title=title,
                    article_text=article_text,
                    numbers=numbers,
                    publisher=st.session_state.spec["meta"].get("publisher", "세계일보")
                )
                if labeled:
                    st.session_state.spec["content"]["numbers"] = labeled
                    st.success("KPI 라벨을 채웠습니다. (필요하면 일부만 수정 후 렌더하세요.)")
                    st.session_state.dirty = True
                else:
                    st.info("AI 라벨 생성 결과가 없거나 실패했습니다. (API 키/본문/숫자 추출을 확인)")

        st.divider()

        report = st.session_state.get("desk_report")
        if report:
            st.subheader("데스크 리포트")
            st.write("**요약**:", report.get("summary_1", ""))
            st.write("**관점/프레이밍**:", report.get("angle", ""))

            with st.expander("팩트 5개", expanded=True):
                for x in report.get("key_facts", [])[:5]:
                    st.write("•", x)

            with st.expander("수치 검증 체크", expanded=True):
                for it in report.get("numbers_check", [])[:8]:
                    st.write(f"- {it.get('claim', '')}")
                    st.caption(f"값: {it.get('value', '')} {it.get('unit', '')} / 검증필요: {it.get('needs_verify')} / 이유: {it.get('why', '')}")

            with st.expander("민감도/리스크", expanded=True):
                s = report.get("sensitivity", {})
                st.write("**Level**:", s.get("level", ""))
                for r in s.get("reasons", [])[:6]:
                    st.write("•", r)
                for sug in s.get("suggestions", [])[:6]:
                    st.write("✅", sug)

            with st.expander("헤드라인 제안 / 키워드", expanded=False):
                for h in report.get("headlines", [])[:3]:
                    st.write("•", h)
                st.write("키워드:", ", ".join(report.get("seo_keywords", [])[:8]))

            with st.expander("후속 질문(추가 취재/확인)", expanded=False):
                for q in report.get("followups", [])[:6]:
                    st.write("•", q)

        with st.expander("편집(선택) — 헤드라인/키포인트 수정", expanded=False):
            st.session_state.spec["content"]["headline"] = st.text_input("헤드라인", value=st.session_state.spec["content"]["headline"])
            st.session_state.spec["content"]["dek"] = st.text_input("서브", value=st.session_state.spec["content"]["dek"])
            for i in range(3):
                st.session_state.spec["content"]["key_points"][i]["text"] = st.text_area(
                    f"키포인트 {i+1}",
                    value=st.session_state.spec["content"]["key_points"][i]["text"],
                    height=70
                )

            _opts = ["story_lite", "data_focus", "timeline", "compare"]
            default_tpl = st.session_state.get("template_hint", "story_lite")
            template = st.radio("템플릿", options=_opts, index=_opts.index(default_tpl) if default_tpl in _opts else 0, horizontal=True)
            st.session_state["template"] = template

            if st.button("생성(렌더)", use_container_width=True):
                st.session_state.spec["layout"] = choose_layout(st.session_state.spec)
                rm = build_render_model(st.session_state.spec)
                tpl_key = st.session_state.get("template") or st.session_state.get("template_hint") or "story_lite"
                st.session_state.svg = render_svg(tpl_key, rm)

    with right:
        st.subheader("미리보기(고정)")
        if st.session_state.svg:
            st.components.v1.html(st.session_state.svg, height=1120, scrolling=True)

            st.download_button("SVG 다운로드", st.session_state.svg.encode("utf-8"), "segye_infographic.svg", "image/svg+xml")

            try:
                import cairosvg
                safe_svg = sanitize_svg_for_png(st.session_state.svg)
                safe_svg = strip_css_import(safe_svg)
                safe_svg = svg_fonts_to_absolute_paths(safe_svg)
                png_bytes = cairosvg.svg2png(bytestring=safe_svg.encode("utf-8"))
                st.download_button(
                    label="PNG 다운로드",
                    data=png_bytes,
                    file_name="segye_infographic.png",
                    mime="image/png"
                )
            except Exception as e:
                st.warning(f"PNG 변환 실패: {e}")
                lines = (st.session_state.svg or "").splitlines()
                if len(lines) >= 6:
                    st.caption("디버그: SVG 상단 8줄")
                    st.code("\n".join(lines[:8]))
        else:
            st.info("좌측에서 URL 불러오기 → AI 초안/데스크 분석 → 생성(렌더) 순으로 진행하세요.")


def run_public_mode():
    """기존 퍼블릭 화면: URL 입력 → 초안 → 수정 → 렌더 → 공유"""
    left, right = st.columns([0.44, 0.56], gap="large")

    with left:
        st.subheader("세계일보 기사 URL로 인포그래픽 생성")

        url = st.text_input(
            "기사 URL (segye.com)",
            value=st.session_state.spec["meta"]["source_url"] or st.session_state.get("url", ""),
            placeholder="예) https://www.segye.com/..."
        )
        url = normalize_url(url)

        btn1, btn2 = st.columns(2)
        with btn1:
            do_fetch = st.button("1) URL 불러오기", use_container_width=True)
        with btn2:
            do_draft = st.button("2) 자동 초안 생성", use_container_width=True)

        st.caption("※ 현재는 세계일보(segye.com) 기사만 지원합니다. 생성물은 참고용이며 출처 링크를 함께 표기합니다.")

        if do_fetch:
            if not url:
                st.error("기사 URL을 입력해주세요.")
                st.stop()
            if not is_allowed_url(url):
                st.error("현재는 세계일보(segye.com) 기사만 지원합니다.")
                st.stop()

            guard_rate_limit(8)

            try:
                data = extract_article(url)

                st.session_state["url"] = data.url
                st.session_state["article_text"] = data.content
                st.session_state["og_image"] = data.og_image

                st.session_state.spec["meta"]["source_url"] = data.url
                st.session_state.spec["meta"]["title"] = data.title
                st.session_state.spec["meta"]["date"] = data.published
                st.session_state.spec["meta"]["byline"] = data.byline

                if not st.session_state.spec["content"]["headline"]:
                    st.session_state.spec["content"]["headline"] = (data.title or "").strip()

                nums = extract_numbers_with_context(data.content, max_items=20)
                st.session_state.spec["content"]["numbers_all"] = nums or []
                st.session_state.spec["content"]["numbers"] = choose_kpis(nums or [], k=4)
                st.session_state["template_hint"] = "data_focus" if len(st.session_state.spec["content"]["numbers"]) >= 2 else "story_lite"

                st.success("기사 정보를 불러왔습니다. 다음으로 '자동 초안 생성'을 눌러주세요.")
            except Exception as e:
                st.error(f"URL 불러오기 실패: {e}")

        if do_draft:
            article_text = st.session_state.get("article_text", "")
            if not article_text:
                st.warning("먼저 'URL 불러오기'를 실행해주세요.")
                st.stop()

            kp = make_simple_keypoints(article_text, k=3)
            st.session_state.spec["content"]["key_points"][0]["text"] = kp[0]
            st.session_state.spec["content"]["key_points"][1]["text"] = kp[1]
            st.session_state.spec["content"]["key_points"][2]["text"] = kp[2]

            st.session_state.spec["content"]["callouts"][0]["title"] = "핵심 맥락"
            st.session_state.spec["content"]["callouts"][0]["body"] = make_simple_callout(article_text)

            nums_raw = extract_numbers_with_context(article_text, max_items=10)
            nums_refined = refine_numbers_with_openai(
                nums_raw,
                title_hint=st.session_state.spec["meta"].get("title", ""),
                text=article_text,
            )
            st.session_state.spec["content"]["numbers"] = nums_refined
            st.session_state["template_hint"] = "data_focus" if len(nums_refined) >= 2 else "story_lite"

            st.success("자동 초안이 생성되었습니다. 필요하면 아래에서 일부만 수정 후 '생성(렌더)'를 눌러주세요.")

        if st.button("AI 초안 생성"):
            client = get_openai_client()
            if not client:
                st.error("API 키가 설정되지 않았습니다.")
            else:
                article_text = st.session_state.get("article_text", "")
                if not article_text:
                    st.warning("먼저 URL을 불러오세요.")
                else:
                    with st.spinner("AI가 초안을 생성 중입니다..."):
                        prompt = f"""
다음 세계일보 기사 내용을 인포그래픽 초안으로 요약하세요.

형식:
- headline
- key_points 3개
- callout (핵심 맥락 1문장)

기사:
{article_text[:6000]}
"""
                        resp = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[{"role": "user", "content": prompt}],
                            temperature=0.3,
                        )
                        draft = resp.choices[0].message.content
                        st.session_state.spec["content"]["callouts"][0]["body"] = draft
                        st.success("AI 초안 생성 완료")

        with st.expander("추출된 숫자(자동) 확인", expanded=False):
            st.json(st.session_state.spec["content"].get("numbers", []))
            if st.button("🧠 라벨 자동 생성", key="label_public"):
                article_text = st.session_state.get("article_text", "")
                numbers = list(st.session_state.spec["content"].get("numbers", []))
                if not article_text:
                    st.warning("먼저 URL을 불러온 뒤 초안을 생성하세요.")
                elif not numbers:
                    st.warning("추출된 숫자가 없습니다. 자동 초안 또는 AI 초안을 먼저 실행하세요.")
                else:
                    with st.spinner("라벨 생성 중..."):
                        enriched = enrich_labels(
                            st.session_state.spec["meta"].get("title", ""),
                            "",
                            article_text,
                            numbers,
                        )
                    items = enriched.get("items") or []
                    for i, n in enumerate(numbers):
                        if i < len(items) and items[i].get("label"):
                            n["label"] = (items[i].get("label") or "").strip()
                    st.session_state.spec["content"]["numbers"] = numbers
                    st.success("라벨 적용 완료")
                    st.rerun()

        st.divider()
        st.subheader("KPI 라벨 자동 생성(AI)")
        if st.button("AI로 KPI 라벨 채우기", use_container_width=True, key="kpi_label_public"):
            numbers = st.session_state.spec["content"].get("numbers", [])
            if not numbers:
                st.info("추출된 수치가 없습니다. (본문에 숫자가 없거나 추출 규칙에 안 잡혔어요)")
            else:
                title = st.session_state.spec["meta"].get("title", "") or st.session_state.spec["content"].get("headline", "")
                article_text = st.session_state.get("article_text", "") or ""
                labeled = infer_kpi_labels_with_ai(
                    title=title,
                    article_text=article_text,
                    numbers=numbers,
                    publisher=st.session_state.spec["meta"].get("publisher", "세계일보")
                )
                if labeled:
                    st.session_state.spec["content"]["numbers"] = labeled
                    st.success("KPI 라벨을 채웠습니다. (필요하면 일부만 수정 후 렌더하세요.)")
                    st.session_state.dirty = True
                else:
                    st.info("AI 라벨 생성 결과가 없거나 실패했습니다. (API 키/본문/숫자 추출을 확인)")

        with st.expander("수정(선택) — 헤드라인/키포인트만 다듬기", expanded=False):
            title = st.text_input("제목(원문)", value=st.session_state.spec["meta"]["title"])
            date = st.text_input("날짜", value=st.session_state.spec["meta"]["date"])
            byline = st.text_input("바이라인", value=st.session_state.spec["meta"]["byline"])

            st.divider()
            headline = st.text_input("헤드라인(인포그래픽용)", value=st.session_state.spec["content"]["headline"])
            dek = st.text_input("서브 문장(선택)", value=st.session_state.spec["content"]["dek"])

            st.write("핵심 포인트(3개)")
            kp1 = st.text_area("1", value=st.session_state.spec["content"]["key_points"][0]["text"], height=72)
            kp2 = st.text_area("2", value=st.session_state.spec["content"]["key_points"][1]["text"], height=72)
            kp3 = st.text_area("3", value=st.session_state.spec["content"]["key_points"][2]["text"], height=72)

            st.write("콜아웃(요약 기반일 때 핵심 맥락 1개)")
            callout_title = st.text_input("콜아웃 제목", value=st.session_state.spec["content"]["callouts"][0]["title"])
            callout_body = st.text_area("콜아웃 본문", value=st.session_state.spec["content"]["callouts"][0]["body"], height=90)

            st.write("인용(기사에 있을 때만)")
            q_text = st.text_area("인용문", value=st.session_state.spec["content"]["quote"]["text"], height=80)

            _opts = ["story_lite", "data_focus", "timeline", "compare"]
            default_tpl = st.session_state.get("template_hint", "story_lite")
            template = st.radio(
                "템플릿",
                options=_opts,
                index=_opts.index(default_tpl) if default_tpl in _opts else 0,
                horizontal=True
            )
            st.session_state["template"] = template

        c1, c2 = st.columns(2)
        with c1:
            if st.button("저장(확인)"):
                st.session_state.spec["meta"]["source_url"] = url
                st.session_state.spec["meta"]["title"] = title
                st.session_state.spec["meta"]["date"] = date
                st.session_state.spec["meta"]["byline"] = byline

                st.session_state.spec["content"]["headline"] = headline
                st.session_state.spec["content"]["dek"] = dek

                st.session_state.spec["content"]["key_points"][0]["text"] = kp1.strip()
                st.session_state.spec["content"]["key_points"][1]["text"] = kp2.strip()
                st.session_state.spec["content"]["key_points"][2]["text"] = kp3.strip()

                st.session_state.spec["content"]["callouts"][0]["title"] = callout_title.strip()
                st.session_state.spec["content"]["callouts"][0]["body"] = callout_body.strip()

                st.session_state.spec["content"]["quote"]["text"] = q_text.strip()

                pub = st.session_state.spec["meta"].get("publisher","세계일보")
                st.session_state.spec["content"]["sources"] = [{"name": pub, "detail": url}]
                st.session_state.dirty = True

        with c2:
            if st.button("생성(렌더)"):
                st.session_state.spec["layout"] = choose_layout(st.session_state.spec)
                rm = build_render_model(st.session_state.spec)
                tpl_key = st.session_state.get("template") or st.session_state.get("template_hint") or "story_lite"
                st.session_state.svg = render_svg(tpl_key, rm)
                st.session_state.dirty = False

        st.caption(f"상태: {'수정됨(미반영)' if st.session_state.dirty else '최신 반영됨'}")

        with st.expander("Spec JSON 보기"):
            st.code(json.dumps(st.session_state.spec, ensure_ascii=False, indent=2), language="json")

    with right:
        st.subheader("미리보기(고정)")
        if st.session_state.svg:
            st.components.v1.html(st.session_state.svg, height=1120, scrolling=True)

            encoded = base64.urlsafe_b64encode(st.session_state.svg.encode()).decode()
            share_url = f"https://segye-on.streamlit.app/?share={encoded}"

            st.markdown("### 공유")
            st.code(share_url)
            st.link_button("카카오톡 공유", f"https://share.kakao.com/?url={share_url}")
            st.link_button("트위터 공유", f"https://twitter.com/intent/tweet?url={share_url}")

            st.download_button(
                label="SVG 다운로드",
                data=st.session_state.svg.encode("utf-8"),
                file_name="segye_infographic.svg",
                mime="image/svg+xml"
            )

            try:
                import cairosvg
                safe_svg = sanitize_svg_for_png(st.session_state.svg)
                safe_svg = strip_css_import(safe_svg)
                safe_svg = svg_fonts_to_absolute_paths(safe_svg)
                png_bytes = cairosvg.svg2png(bytestring=safe_svg.encode("utf-8"))
                st.download_button(
                    label="PNG 다운로드",
                    data=png_bytes,
                    file_name="segye_infographic.png",
                    mime="image/png"
                )
            except Exception as e:
                st.warning(f"PNG 변환 실패: {e}")
                lines = (st.session_state.svg or "").splitlines()
                if len(lines) >= 6:
                    st.caption("디버그: SVG 상단 8줄")
                    st.code("\n".join(lines[:8]))

        else:
            st.info("좌측에서 입력/수정 후 '생성(렌더)'를 누르면 여기에서 결과를 확인할 수 있습니다.")


desk = is_desk_mode()
if desk:
    if not desk_auth_ok():
        st.stop()
    run_desk_mode()
else:
    run_public_mode()
