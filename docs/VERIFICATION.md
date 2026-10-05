# 검증 범위

최종 업데이트: 2026-10-05.

## 이번 도구·문서 재배치

Python 테스트 80개 통과, PostgreSQL 테스트 4개는 연결 설정이 없어 건너뛰었습니다. 전처리 단위 테스트 3개 통과. 통합 앱의 관리자 세션·CSRF·감사와 독립 API의 토큰 인증이 옮긴 공통 모듈을 사용하도록 검증했습니다. DDL·데이터 사전 생성 경로도 확인했습니다.

명령은 저장소 루트에서 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe tools/maritime_data/scripts/test_prepare_maritime_data.py
```

## 앞선 UI 통합 검증 (2026-10-05)

Node.js 테스트 29개, DOM·HTTP/SQLite 점검 19개, Chrome 브라우저 점검 11개 통과. Next.js 프로덕션 빌드 성공. 검색·근거·원문·계산·보고서 저장 후 복구·메뉴·모바일 보고서 폭과 JavaScript 오류 여부를 확인했습니다. 이번 재배치는 Node/Next.js 화면 코드를 변경하지 않았습니다.

## 한계

실제 PostgreSQL 재적재·스키마 이름 전환·외부 LLM 추론은 이번 작업에서 수행하지 않았습니다. 모델 오류·인용 처리는 모의 응답으로 검증했습니다. Node UI의 SQLite와 Python 앱의 PostgreSQL은 별개이며 해사 데이터는 Node UI에 자동 연결되지 않습니다. 공식 CII·규정 적합성, 임의 PDF의 OCR·복잡한 표 정확도, 독립된 다른 기기의 접속은 별도 검증이 필요합니다.
