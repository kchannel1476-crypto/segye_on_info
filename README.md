# segye_on_info
인포그래픽 자동화

## PNG 한글 표시

PNG 다운로드 시 한글이 깨지지 않으려면 **프로젝트의 `fonts/` 폴더**에 다음 파일을 넣으세요.

- `fonts/NotoSansKR-Regular.ttf`
- `fonts/NotoSansKR-Bold.ttf`

앱이 PNG 변환 시 이 경로를 절대 경로로 바꿔 cairosvg가 폰트를 사용합니다.

## 시스템 폰트 (선택, Linux/서버)

PNG 변환·폰트 폴백이 필요할 때:

```bash
# Debian/Ubuntu
sudo apt install fonts-noto-cjk fonts-noto-color-emoji
```
