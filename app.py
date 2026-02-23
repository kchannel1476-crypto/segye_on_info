import os
from urllib.parse import urlparse
import time
import re
import streamlit as st
import json


def get_openai_client():
    key = None
    try:
        key = st.secrets.get("OPENAI_API_KEY")
    except Exception:
        pass
    key = key or os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    from openai import OpenAI
    return OpenAI(api_key=key)


def refine_numbers_with_openai(numbers: list[dict], title_hint: str = "") -> list[dict]:
    """
    input: [{"label": "...", "value": "3.5%", "context": "..."}...]
    output: [{"label": "기준금리", "value": "3.50", "unit": "%", "note": "…"} ...]
    """
    client = get_openai_client()
    if client is None:
        return numbers

    if not numbers:
        return []

    nums_in = numbers[:8]

    system = (
        "너는 신문사 인포그래픽 편집 데스크다. "
        "숫자 후보를 보고 '무슨 수치인지' 라벨과 단위를 정확히 정리한다. "
        "기사에 근거 없는 해석/추측은 하지 않는다."
    )

    user_payload = {
        "title_hint": title_hint,
        "items": nums_in,
        "instruction": (
            "각 item에 대해 아래 필드를 채워라.\n"
            "- label: 독자용 짧은 라벨(2~8자)\n"
            "- value: 숫자만(쉼표 제거, 소수 유지 가능)\n"
            "- unit: 단위(%/원/명/건/배/년/월/일/조/억/만 등 없으면 빈 문자열)\n"
            "- note: 맥락 한 줄(20~45자), 원문 문맥 기반\n"
            "의미가 불명확하거나 순번/날짜/페이지 등으로 보이면 drop=true로 표시해 제외해라.\n"
            "출력은 반드시 JSON 하나만. 형식: {\"items\": [{\"label\":\"\", \"value\":\"\", \"unit\":\"\", \"note\":\"\", \"drop\": false 또는 true}, ...]}"
        ),
    }

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
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


from jinja2 import Environment, FileSystemLoader
from extractor import extract_article, has_numbers, extract_numbers_with_context


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


def make_simple_callout(text: str, max_len: int = 160) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    t = text.replace("\n", " ")
    return (t[:max_len] + "…") if len(t) > max_len else t


# (선택) SVG -> PNG 변환
# pip install cairosvg
# import cairosvg

st.set_page_config(page_title="SEGYE.ON Infographic", layout="wide")

# -----------------------------
# Jinja2 환경
# -----------------------------
env = Environment(loader=FileSystemLoader("templates"), autoescape=False)
tpl_story = env.get_template("story_lite.svg.j2")
tpl_data = env.get_template("data_focus.svg.j2")
tpl_timeline = env.get_template("timeline.svg.j2")
tpl_compare = env.get_template("compare.svg.j2")

def render_svg(template_key: str, render_model: dict) -> str:
    if template_key == "data_focus":
        return tpl_data.render(**render_model)
    if template_key == "timeline":
        return tpl_timeline.render(**render_model)
    if template_key == "compare":
        return tpl_compare.render(**render_model)
    return tpl_story.render(**render_model)

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

    kp = [x.get("text","") for x in c.get("key_points", [])]
    kp = (kp + ["","",""])[:3]

    quote = c.get("quote", {})
    quote_line = quote.get("text","").strip()

    callout = (c.get("callouts", [{}]) + [{}])[0]
    callout_title = callout.get("title","").strip()
    callout_body = callout.get("body","").strip()

    url = meta.get("source_url","").strip()
    url_short = url.replace("https://","").replace("http://","")
    if len(url_short) > 42:
        url_short = url_short[:39] + "..."

    sources_line = f"출처: {meta.get('publisher','')} · {meta.get('date','')} · {url_short}".strip()

    # data_focus
    charts = c.get("charts", [])
    chart_title = charts[0].get("title", "").strip() if charts else ""
    chart_note = charts[0].get("note", "").strip() if charts else ""
    nums = c.get("numbers", [])[:4]
    numbers = nums[:2]
    big1 = str(numbers[0].get("value", "")) if numbers else ""
    big1_label = (numbers[0].get("label", "") or numbers[0].get("context", "") or "").strip() if numbers else ""
    big2 = str(numbers[1].get("value", "")) if len(numbers) > 1 else ""
    big2_label = (numbers[1].get("label", "") or numbers[1].get("context", "") or "").strip() if len(numbers) > 1 else ""
    if c.get("chart"):
        chart_title = c["chart"].get("title", chart_title)
        chart_note = c["chart"].get("note", chart_note)

    # timeline
    tl = c.get("timeline", [])[:8]
    text_timeline = [{"date": t.get("date", ""), "event": t.get("event", "")} for t in tl]

    # compare
    comp = c.get("comparison", {}) or {}
    comp_items = (comp.get("items") or [])[:6]
    compare_rows = [{"left": i.get("left", ""), "right": i.get("right", "")} for i in comp_items]

    return {
        "canvas": {"w": 1080, "h": 1080, "margin": 72},
        "text": {
            "headline": c.get("headline","").strip(),
            "dek": c.get("dek","").strip(),
            "keywords": c.get("keywords", [])[:6],
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
            "left_title": comp.get("left_title", ""),
            "right_title": comp.get("right_title", ""),
            "compare_rows": compare_rows,
        },
        "flags": {
            "has_quote": bool(quote_line),
            "has_callout": bool(callout_title or callout_body)
        },
        "numbers": nums,
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

# -----------------------------
# UI Layout
# -----------------------------
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

            # session vars
            st.session_state["url"] = data.url
            st.session_state["article_text"] = data.content
            st.session_state["og_image"] = data.og_image

            # spec 반영
            st.session_state.spec["meta"]["source_url"] = data.url
            st.session_state.spec["meta"]["title"] = data.title
            st.session_state.spec["meta"]["date"] = data.published
            st.session_state.spec["meta"]["byline"] = data.byline

            # 기본 headline은 제목으로
            if not st.session_state.spec["content"]["headline"]:
                st.session_state.spec["content"]["headline"] = (data.title or "").strip()

            st.success("기사 정보를 불러왔습니다. 다음으로 '자동 초안 생성'을 눌러주세요.")
        except Exception as e:
            st.error(f"URL 불러오기 실패: {e}")

    if do_draft:
        article_text = st.session_state.get("article_text", "")
        if not article_text:
            st.warning("먼저 'URL 불러오기'를 실행해주세요.")
            st.stop()

        # 키포인트 3개 자동 초안
        kp = make_simple_keypoints(article_text, k=3)
        st.session_state.spec["content"]["key_points"][0]["text"] = kp[0]
        st.session_state.spec["content"]["key_points"][1]["text"] = kp[1]
        st.session_state.spec["content"]["key_points"][2]["text"] = kp[2]

        # 콜아웃 자동(짧은 맥락)
        st.session_state.spec["content"]["callouts"][0]["title"] = "핵심 맥락"
        st.session_state.spec["content"]["callouts"][0]["body"] = make_simple_callout(article_text)

        # 1) 후보 추출(규칙 기반)
        nums_raw = extract_numbers_with_context(article_text, limit=10)

        # 2) LLM로 라벨/단위/노트 정제 + 불필요 숫자 제거
        nums_refined = refine_numbers_with_openai(
            nums_raw,
            title_hint=st.session_state.spec["meta"].get("title", ""),
        )

        # 3) spec 반영
        st.session_state.spec["content"]["numbers"] = nums_refined

        # 4) 템플릿 힌트/자동 선택 강화
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

            # sources 자동
            pub = st.session_state.spec["meta"].get("publisher","세계일보")
            st.session_state.spec["content"]["sources"] = [{"name": pub, "detail": url}]
            st.session_state.dirty = True

    with c2:
        if st.button("생성(렌더)"):
            # 저장 버튼을 안 눌러도 반영되도록(권장) spec 업데이트는 다음 작업 2에서 같이 처리
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

        st.download_button(
            label="SVG 다운로드",
            data=st.session_state.svg.encode("utf-8"),
            file_name="segye_infographic.svg",
            mime="image/svg+xml"
        )

        # PNG는 실패할 수 있으니 try/except로 감싸서 버튼 노출/에러 표시
        try:
            import cairosvg
            png_bytes = cairosvg.svg2png(bytestring=st.session_state.svg.encode("utf-8"))
            st.download_button(
                label="PNG 다운로드",
                data=png_bytes,
                file_name="segye_infographic.png",
                mime="image/png"
            )
        except Exception as e:
            st.warning(f"PNG 변환 실패: {e}")

    else:
        st.info("좌측에서 입력/수정 후 '생성(렌더)'를 누르면 여기에서 결과를 확인할 수 있습니다.")
