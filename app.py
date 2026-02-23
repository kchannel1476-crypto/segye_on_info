import streamlit as st
import json
from jinja2 import Environment, FileSystemLoader

# (선택) SVG -> PNG 변환
# pip install cairosvg
# import cairosvg

st.set_page_config(page_title="SEGYE.ON Infographic", layout="wide")

# -----------------------------
# Jinja2 환경
# -----------------------------
env = Environment(loader=FileSystemLoader("templates"), autoescape=False)
tpl_story = env.get_template("story_lite.svg.j2")

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
        "layout":{"template":"story", "ratio":"1:1", "sections":["headline","key_points","callout","sources"]}
    }

def choose_layout(spec: dict) -> dict:
    # MVP 로직(간단): numbers>=2 or charts>=1 -> data, timeline>=4 -> timeline, compare items>=3 -> compare else story
    c = spec.get("content", {})
    if len(c.get("charts", [])) >= 1 or len(c.get("numbers", [])) >= 2:
        return {"template":"data", "ratio":"1:1", "sections":["headline","chart","key_points","sources"]}
    if len(c.get("timeline", [])) >= 4:
        return {"template":"timeline", "ratio":"1:1", "sections":["headline","timeline","key_points","sources"]}
    comp_items = c.get("comparison", {}).get("items", [])
    if len(comp_items) >= 3:
        return {"template":"compare", "ratio":"1:1", "sections":["headline","comparison","key_points","sources"]}
    return {"template":"story", "ratio":"1:1", "sections":["headline","key_points","callout","sources"]}

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
            "sources_line": sources_line
        },
        "flags": {
            "has_quote": bool(quote_line),
            "has_callout": bool(callout_title or callout_body)
        }
    }

def render_svg_story(render_model: dict) -> str:
    return tpl_story.render(**render_model)

# -----------------------------
# Session state
# -----------------------------
if "spec" not in st.session_state:
    st.session_state.spec = default_spec()
if "dirty" not in st.session_state:
    st.session_state.dirty = False
if "svg" not in st.session_state:
    st.session_state.svg = ""

# -----------------------------
# UI Layout
# -----------------------------
left, right = st.columns([0.44, 0.56], gap="large")

with left:
    st.subheader("입력 · 확인 · 수정 · 생성")

    url = st.text_input("기사 URL", value=st.session_state.spec["meta"]["source_url"])
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
            st.session_state.spec["layout"] = choose_layout(st.session_state.spec)
            rm = build_render_model(st.session_state.spec)

            # 현재는 story 템플릿만 연결 (MVP)
            st.session_state.svg = render_svg_story(rm)
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
