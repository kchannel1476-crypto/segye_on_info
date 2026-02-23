# segye_on_info
인포그래픽 자동화

## PNG 한글 표시

PNG 다운로드 시 한글이 깨지지 않으려면 **Noto Sans KR** 폰트가 다음 위치 중 한 곳에 있으면 됩니다.

1. **프로젝트** `fonts/` — `NotoSansKR-Regular.ttf`, `NotoSansKR-Bold.ttf`
2. **환경 변수** `SEGYE_FONTS_DIR` — 해당 폴더에 위 두 파일
3. **바탕화면** `Desktop/static/` — 예: `NotoSansKR-Regular.ttf`, `NotoSansKR-Bold.ttf` 등

앱이 PNG 변환 시 위 순서로 폰트를 찾아 절대 경로(`file://`)로 넣어 cairosvg가 사용합니다.

## 시스템 폰트 (선택, Linux/서버)

PNG 변환·폰트 폴백이 필요할 때:

```bash
# Debian/Ubuntu
sudo apt install fonts-noto-cjk fonts-noto-color-emoji
```
