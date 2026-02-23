import json
import streamlit as st
from jinja2 import Environment, FileSystemLoader
import cairosvg

st.set_page_config(page_title="SEGYE.ON Infographic MVP", layout="wide")

# ----------------------------
# Jinja2 env + templates
# ----------------------------
env = Environment(loader=FileSystemLoader("templates"), autoescape=False)
T_STORY = env.get_template("story_lite.svg.j2")
T_DATA = env.get_template("data_focus.svg.j2")
T_TIME = env.get_template("timeline.svg.j2")
T_COMP = env.get_template("compare.svg.j2")

# ----------------------------
# Defaults
# ----------------------------
def default_spec():
    return {
        "meta": {"source_url":"", "title":"", "publisher":"세계일보", "date":"", "byline":"", "language":"ko"},
        "content": {
            "headline":"(예) 기준금리, 언제까지 높은 수준?",
            "dek":"핵심 쟁점과 흐름을 한 장으로 정리",
            "key_points":[
                "(예) 물가·환율 압력으로 인하 시점이 늦춰질 수 있음",
                "(예) 경기 둔화 신호가 커지면 정책 기조 변화 가능",
                "(예) 가계·기업의 이자 부담은 당분간 지속될 전망"
            ],
            "quote":"",  # 기사에 실제 인용문이 있을 때만
            "callout_title":"핵심 맥락",
            "callout_body":"수치가 부족한 기사라도 '쟁점/흐름/영향'을 요약해 인포그래픽으로 구성합니다.",
            "timeline":[
                {"date":"2025-12", "event":"(예) 물가 재상승 우려 확대"},
                {"date":"2026-01", "event":"(예) 동결 기조 유지"},
                {"date":"2026-02", "event":"(예) 시장의 인하 기대 조정"}
            ],
            "comparison":{
                "left_title":"인하 신중론",
                "right_title":"인하 필요론",
                "items":[
                    {"left":"물가 안정 확인 필요", "right":"경기 둔화 대응 필요"},
                    {"left":"환율 변동성 우려", "right":"이자 부담 완화 필요"},
                    {"left":"가계부채 관리", "right":"투자·소비 심리 회복"}
                ]
            },
            "numbers":[
                {"label":"(예) 기준금리", "value":"3.50%", "note":"현재 수준"},
                {"label":"(예) 물가상승률", "value":"2.8%", "note":"최근 지표"}
            ],
            "chart":{
                "title":"(예) 물가 vs 금리 추이",
                "note":"MVP: 차트 박스로 표시(다음 단계에서 SVG 차트 렌더링)"
            }
        }
    }

def choose_template(spec):
    c = spec["content"]
    # 간단 우선순위: data > timeline > compare > story
    if len(c.get("numbers", [])) >= 2:
        return "data"
    if len(c.get("timeline", [])) >= 4:
        return "timeline"
    if len(c.get("comparison", {}).get("items", [])) >= 3:
        return "compare"
    return "story"

def build_render_model(spec):
    meta = spec.get("meta", {})
    c = spec.get("content", {})

    url = meta.get("source_url", "").strip()
    url_short = url.replace("https://","").replace("http://","")
    if len(url_short) > 46:
        url_short = url_short[:43] + "..."

    sources_line = f"출처: {meta.get('publisher','')} · {meta.get('date','')} · {url_short}".strip()

    # key points exactly 3
    kp = (c.get("key_points", []) + ["", "", ""])[:3]

    # timeline up to 6
    tl = c.get("timeline", [])[:6]

    comp = c.get("comparison", {})
    rows = (comp.get("items", []) + [])[:6]

    nums = (c.get("numbers", []) + [{"label":"","value":"","note":""},{"label":"","value":"","note":""}])[:2]

    rm = {
        "canvas": {"w": 1080, "h": 1080, "margin": 72},
        "text": {
            "headline": c.get("headline","").strip(),
            "dek": c.get("dek","").strip(),
            "key_points": [x.strip() if isinstance(x, str) else str(x).strip() for x in kp],
            "quote_line": c.get("quote","").strip(),
            "callout_title": c.get("callout_title","").strip(),
            "callout_body": c.get("callout_body","").strip(),
            "sources_line": sources_line,

            # data template extras
            "chart_title": c.get("chart", {}).get("title","").strip(),
            "chart_note": c.get("chart", {}).get("note","").strip(),
            "big1": str(nums[0].get("value","")).strip(),
            "big1_label": str(nums[0].get("label","")).strip(),
            "big2": str(nums[1].get("value","")).strip(),
            "big2_label": str(nums[1].get("label","")).strip(),

            # timeline template extras
            "timeline": [{"date": x.get("date",""), "event": x.get("event","")} for x in tl],

            # compare template extras
            "left_title": comp.get("left_title","").strip(),
            "right_title": comp.get("right_title","").strip(),
            "compare_rows": [{"left": r.get("left",""), "right": r.get("right","")} for r in rows],
        },
        "flags": {
            "has_quote": bool(c.get("quote","").strip()),
            "has_callout": bool(c.get("callout_title","").strip() or c.get("callout_body","").strip())
        }
    }
    return rm

def render_svg(template_key, rm):
    if template_key == "data":
        return T_DATA.render(**rm)
    if template_key == "timeline":
        return T_TIME.render(**rm)
    if template_key == "compare":
        return T_COMP.render(**rm)
    return T_STORY.render(**rm)

# ----------------------------
# Session
# ----------------------------
if "spec" not in st.session_state:
    st.session_state.spec = default_spec()
if "dirty" not in st.session_state:
    st.session_state.dirty = False
if "svg" not in st.session_state:
    st.session_state.svg = ""
if "template" not in st.session_state:
    st.session_state.template = "story"

# ----------------------------
# UI
# ----------------------------
left, right = st.columns([0.45, 0.55], gap="large")

with left:
    st.subheader("입력 · 확인 · 수정 · 생성")

    meta = st.session_state.spec["meta"]
    c = st.session_state.spec["content"]

    meta["source_url"] = st.text_input("기사 URL", value=meta.get("source_url",""))
    meta["title"] = st.text_input("기사 제목(원문)", value=meta.get("title",""))
    meta["date"] = st.text_input("작성일", value=meta.get("date",""))
    meta["byline"] = st.text_input("바이라인", value=meta.get("byline",""))

    st.divider()

    c["headline"] = st.text_input("헤드라인(인포그래픽)", value=c.get("headline",""))
    c["dek"] = st.text_input("서브 문장(선택)", value=c.get("dek",""))

    st.write("핵심 포인트(3개)")
    kps = c.get("key_points", ["","",""])
    kps[0] = st.text_area("포인트 1", value=kps[0], height=70)
    kps[1] = st.text_area("포인트 2", value=kps[1], height=70)
    kps[2] = st.text_area("포인트 3", value=kps[2], height=70)
    c["key_points"] = kps

    with st.expander("Story Lite 옵션(수치 부족 시)"):
        c["quote"] = st.text_area("인용문(기사에 있을 때만)", value=c.get("quote",""), height=70)
        c["callout_title"] = st.text_input("콜아웃 제목", value=c.get("callout_title","핵심 맥락"))
        c["callout_body"] = st.text_area("콜아웃 본문", value=c.get("callout_body",""), height=90)

    with st.expander("Data Focus 옵션(수치/차트)"):
        nums = c.get("numbers", [{"label":"","value":"","note":""},{"label":"","value":"","note":""}])
        nums = (nums + [{"label":"","value":"","note":""},{"label":"","value":"","note":""}])[:2]
        nums[0]["label"] = st.text_input("수치1 라벨", value=nums[0].get("label",""))
        nums[0]["value"] = st.text_input("수치1 값", value=str(nums[0].get("value","")))
        nums[1]["label"] = st.text_input("수치2 라벨", value=nums[1].get("label",""))
        nums[1]["value"] = st.text_input("수치2 값", value=str(nums[1].get("value","")))
        c["numbers"] = nums

        chart = c.get("chart", {"title":"","note":""})
        chart["title"] = st.text_input("차트 제목", value=chart.get("title",""))
        chart["note"] = st.text_input("차트 주석", value=chart.get("note",""))
        c["chart"] = chart

    with st.expander("Timeline 옵션"):
        tl = c.get("timeline", [])
        if len(tl) == 0:
            tl = [{"date":"","event":""},{"date":"","event":""},{"date":"","event":""},{"date":"","event":""}]
        for i in range(min(6, len(tl))):
            tl[i]["date"] = st.text_input(f"타임라인 {i+1} 날짜", value=tl[i].get("date",""), key=f"tld{i}")
            tl[i]["event"] = st.text_input(f"타임라인 {i+1} 사건", value=tl[i].get("event",""), key=f"tle{i}")
        c["timeline"] = tl

    with st.expander("Compare 옵션"):
        comp = c.get("comparison", {"left_title":"","right_title":"","items":[]})
        comp["left_title"] = st.text_input("좌측 제목", value=comp.get("left_title",""))
        comp["right_title"] = st.text_input("우측 제목", value=comp.get("right_title",""))
        items = comp.get("items", [])
        if len(items) == 0:
            items = [{"left":"","right":""},{"left":"","right":""},{"left":"","right":""}]
        for i in range(min(6, len(items))):
            items[i]["left"] = st.text_input(f"비교 {i+1} 좌", value=items[i].get("left",""), key=f"cl{i}")
            items[i]["right"] = st.text_input(f"비교 {i+1} 우", value=items[i].get("right",""), key=f"cr{i}")
        comp["items"] = items
        c["comparison"] = comp

    st.divider()

    st.session_state.template = st.selectbox(
        "템플릿 선택(자동/수동)",
        options=["auto","story","data","timeline","compare"],
        index=0
    )

    c1, c2 = st.columns(2)
    with c1:
        if st.button("저장(수정 반영)"):
            st.session_state.spec["meta"] = meta
            st.session_state.spec["content"] = c
            st.session_state.dirty = True

    with c2:
        if st.button("생성(렌더)"):
            st.session_state.spec["meta"] = meta
            st.session_state.spec["content"] = c

            t = st.session_state.template
            if t == "auto":
                t = choose_template(st.session_state.spec)

            rm = build_render_model(st.session_state.spec)
            st.session_state.svg = render_svg(t, rm)
            st.session_state.dirty = False

    st.caption(f"상태: {'수정됨(미반영)' if st.session_state.dirty else '최신 반영됨'}")

    with st.expander("Spec JSON 보기"):
        st.code(json.dumps(st.session_state.spec, ensure_ascii=False, indent=2), language="json")

with right:
    st.subheader("미리보기(고정)")
    if st.session_state.svg:
        st.components.v1.html(st.session_state.svg, height=1120, scrolling=True)

        st.download_button(
            "SVG 다운로드",
            data=st.session_state.svg.encode("utf-8"),
            file_name="segye_infographic.svg",
            mime="image/svg+xml"
        )

        png_bytes = cairosvg.svg2png(bytestring=st.session_state.svg.encode("utf-8"))
        st.download_button(
            "PNG 다운로드",
            data=png_bytes,
            file_name="segye_infographic.png",
            mime="image/png"
        )
    else:
        st.info("좌측에서 입력/수정 후 '생성(렌더)'를 누르면 여기에서 결과를 확인할 수 있습니다.")
