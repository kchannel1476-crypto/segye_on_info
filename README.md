# segye_on_info
인포그래픽 자동화

## PNG 한글 표시

PNG 다운로드 시 한글이 깨지지 않으려면:

1. **Noto Sans KR TTF**를 다음 중 한 곳에 두세요 (앱이 자동으로 검색합니다).
   - 프로젝트 `fonts/` — `NotoSansKR-Regular.ttf`, `NotoSansKR-Bold.ttf`
   - 환경 변수 `SEGYE_FONTS_DIR` 가리키는 폴더
   - 바탕화면 `Desktop/static/` 또는 `c:\Users\...\Desktop\static\`
2. **그래도 한글이 □로 나오면** → **시스템 폰트로 설치**하세요.  
   TTF 파일을 더블클릭해 "설치"를 누르면, cairosvg가 시스템 폰트를 사용해 PNG에 한글이 나옵니다.  
   (cairosvg는 임베드 폰트를 지원하지 않고 시스템/로컬 폰트만 사용할 수 있습니다.)

## 시스템 폰트 (선택, Linux/서버)

PNG 변환·폰트 폴백이 필요할 때:

```bash
# Debian/Ubuntu
sudo apt install fonts-noto-cjk fonts-noto-cjk-extra fonts-noto-color-emoji
```
