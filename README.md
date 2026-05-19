# misung-hwpx-merger

미성엠프로 성능점검표 보고서(.hwpx) 합치기 서비스

## API

### POST /merge

여러 hwpx 파일을 받아 1개로 합쳐서 반환.

**요청 (multipart/form-data):**
- `api_key`: 인증 키
- `files`: hwpx 파일들 (순서대로 합쳐짐)
- `filename`: 결과 파일명 (선택)

## 배포

Render 무료 티어. GitHub push → 자동 배포.

## 로컬 테스트

```bash
pip install -r requirements.txt
MERGE_API_KEY=test python app.py
```
