# 해,답 — 선원 업무 지원 워크스페이스

최종 업데이트: **2026-10-05**

문서 근거 검색, 운항 조회, Python 배출량 계산, Noon/MRV 초안을 연결합니다. 캡스톤 설계의 **Next.js + Python Tool + PostgreSQL + ChromaDB**를 기본 실행 스택으로 전환했습니다. 기존 Node.js·SQLite MVP와 데이터도 별도로 유지합니다.

## 저장소 통합 및 역할4 API (2026-10-05)

`dudco/sea-the-answer`의 웹 MVP와 로컬 캡스톤의 Next.js·Python 스택을 사용자 레포의 역할4 코드에 통합했습니다. 기존 역할4 조회·계산 로직, 전처리 스크립트, 데이터 계약과 PDF는 유지합니다.

- 기본 웹 앱: `npm start`, http://127.0.0.1:3000 . Python API는 8000입니다. 아래 설치 절차의 `uv sync --frozen`과 `npm ci --prefix web`을 사용합니다.
- 원본 웹 MVP: `npm run legacy` 또는 `start.cmd`, http://127.0.0.1:5173 . 별도 SQLite DB를 사용합니다.
- 역할4 독립 API: `.\.venv\Scripts\python.exe -m uvicorn service.role4_standalone:create_app --factory --host 127.0.0.1 --port 8001` . 기존 Bearer 토큰 계약을 유지합니다. 토큰 클라이언트의 주소를 8001로 변경하세요. API 명세는 http://127.0.0.1:8001/docs 입니다.

기존 역할4 환경이 있다면 `.env.local`과 DB를 유지하고 `.venv`에 `uv sync --frozen`으로 통합 의존성을 설치합니다. 역할4 전용 `requirements.lock.txt`는 독립 API용이며 전체 웹 앱 의존성을 포함하지 않습니다. 신규 웹 설치에는 아래 준비·실행 절차를 따르세요. 기존 DB를 사용할 때는 `python -m service.manage bootstrap`으로 앱 계정·테이블·샘플을 추가한 후 실행합니다. 이 명령은 public 스키마에 앱 테이블을 만들므로 DB 계정에 해당 권한이 필요하며 기존 role4 스키마와 데이터를 재적재하지 않습니다.

웹 앱의 관리자 계정으로 로그인하면 **정형 데이터** 탭에서 MRV·합성 Noon 조회와 조회 도우미를 사용할 수 있습니다. 웹의 `/api/role4/query`, `/calculate`, `/ask`, `/factors`는 관리자 세션으로 접근하며 POST에는 CSRF 토큰이 필요합니다. 계산은 API로 제공하며 조회 화면에는 자동 연결하지 않습니다. 일반·담당자 계정은 역할4 전체 조회에 접근하지 못합니다.

독립 API는 기존 `DATABASE_URL`과 32자 이상의 `ROLE4_API_TOKEN`을 사용합니다. 웹 앱은 토큰을 브라우저에 전달하지 않으며 로그인 세션을 사용합니다. 두 API가 같은 role4 조회·계산 모듈을 호출하므로 데이터 계약·십진 문자열·실제/합성 구분은 같습니다. 원본 데이터와 DB를 포함하지 않으며 최초 데이터 적재는 `scripts/load-role4.ps1`과 [데이터 확보 안내](docs/role4/DATA_ACCESS.md)를 참고하세요.

## 구현 범위

- 로그인, 관리자/담당자/일반 사용자 역할, 선박 배정, 제한 문서, 권한 변경 시 세션 철회
- PDF/JSON 등록, 페이지·조항·발행처·버전·적용 조건, 개정 및 폐기
- BM25 키워드 + Chroma 벡터 검색의 순위 결합(RRF), 검색 전후 권한 필터
- 자연어 문서/운항/계산/초안 분기, 근거 부족 및 모델 오류의 명시적 대체 경로
- Noon 데이터 입력·수정·삭제, 단위/범위 검증, 선박 비교, 연료 급변 표시, 재현 가능한 계산
- Noon/MRV 초안 저장·편집·Markdown/JSON 내보내기, 생성 시점 근거·입력 스냅샷, 동시 수정 충돌 검출
- 질의·변경·계산 이력, 체크섬 백업 및 빈 DB 복원

**한계:** 초기 자료는 가상 선박 2척·운항 14건과 기존 공개 요약/가상 문서입니다. 공식 CII 등급·규정 적합성은 판정하지 않습니다. OCR, 표 셀 구조 복원, 공식 MRV 제출 서식과 다국어 검색 평가는 후속 범위입니다. 실제 외부/로컬 LLM 추론은 미검증이며 모델 미설정 시 원문 근거와 Python 결과를 표시합니다. KPI 달성을 주장하지 않습니다.

## 1. 준비 및 실행

Node.js 24 LTS, Python 3.12, uv, PostgreSQL 18이 필요합니다. 명령은 **이 README가 있는 저장소 루트**에서 실행합니다. uv가 없다면 `python -m pip install uv`로 설치하세요.

### Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-local.ps1
npm start
```

PostgreSQL 경로가 다르면 설치 명령에 `-PgBin 'D:\PostgreSQL\18\bin'`을 붙입니다. 기본은 `C:\Program Files\PostgreSQL\18\bin`입니다. 잠금 파일로 의존성을 설치하고 전용 DB를 `data/postgres`, `127.0.0.1:55432`에 생성합니다. 기존 시스템 DB와 `.env.local`은 덮어쓰지 않습니다. 앱 DB 계정은 비슈퍼유저입니다.

웹: **http://127.0.0.1:3000**, Python API: http://127.0.0.1:8000. 최초 계정은 `captain`, 무작위 비밀번호는 **`data/initial-login.txt`**에 있습니다. `start-design.cmd`로도 실행할 수 있습니다. Ctrl+C로 개발 서버를 종료합니다.

컴퓨터 재시작 후에는 전용 DB를 먼저 시작하세요.

```powershell
.\.venv\Scripts\python.exe -m service.manage local-db
npm start
```

별도 DB를 `.env.local`에 설정했다면 해당 DB를 직접 시작하세요. 서비스·자동 시작·외부 공개는 구성하지 않습니다.

### Linux / 별도 PostgreSQL

전용 빈 데이터베이스와 비슈퍼유저 소유 계정을 준비하고 `.env.example`을 `.env.local`로 복사해 DB URL을 설정합니다. Windows 전용 `local-db` 대신 준비한 DB를 사용하세요.

```bash
uv sync --frozen --python 3.12
npm ci --prefix web
mkdir -p data
.venv/bin/python -m service.manage bootstrap
npm start
```

Ubuntu 실행은 아직 검증하지 않았습니다. 기존 SQLite 자료의 PostgreSQL 자동 이전은 수행하지 않습니다. 기존 MVP를 계속 사용할 수 있으며 필요한 문서는 JSON으로 등록하세요.

## 2. 벡터 검색

기본 BM25는 모델 다운로드가 필요 없습니다. 다음 명령은 공개 ONNX MiniLM 임베딩 모델(약 79MB)을 최초 다운로드하고 Chroma를 색인합니다.

```powershell
.\.venv\Scripts\python.exe -m service.manage prepare-vectors
```

`.env.local`의 `VECTOR_ENABLED=1`로 바꾸고 앱을 재시작합니다. 준비 후 검색은 외부 모델 호출 없이 동작합니다. 모델/색인 오류 시 BM25로 전환하고 경고합니다. PostgreSQL이 원본·권한·활성 버전의 기준이며 Chroma는 재생성 가능한 색인입니다. 기본 영문 MiniLM과 한영 해사 용어 사전을 사용하므로 한국어 검색 품질은 별도 평가가 필요합니다.

## 3. 사용 및 문서 등록

1. 로그인 후 선박·기간을 선택합니다. 샘플 기간은 **2026-09-11~2026-09-17**입니다.
2. **통합 질의:** `현재 선박의 배출량과 관련 IMO 규정을 알려줘`, `Noon Report 초안을 만들어줘` 등을 입력합니다. 계산 대상은 상단의 명시적 선박·기간입니다. 자유문에서 임의 수치를 추출해 DB를 변경하지 않습니다.
3. **운항 데이터:** 조회·계산·선박 비교와 JSON 등록/수정/삭제. 연료 t, 거리 nm, 속력 kn, DWT t를 사용합니다. 환산계수는 입력값이며 공식 적용성을 자동 확인하지 않습니다.
4. **문서·근거:** PDF/JSON 등록, 동일 논리 ID의 새 버전 추가, 폐기. 구버전은 검색에서 제외하고 이전 보고서 근거로 보존합니다.
5. **보고서:** 명시적으로 초안을 저장한 뒤 편집하고 Markdown/원본 포함 JSON을 내려받습니다. 담당자 검토 전 공식 제출 문서가 아닙니다.
6. **관리:** 사용자 생성, 역할/선박 배정/비활성화, 선박 등록. 권한이 바뀐 사용자는 다시 로그인해야 합니다.

```json
{
  "logical_id": "training-guide",
  "title": "교육용 운항 안내",
  "version": "1.0",
  "kind": "sample",
  "issuer": "교육 자료",
  "issued_at": "2026-09-29",
  "applicability": "가상 선박 교육용",
  "restricted": false,
  "vessels": ["HAE-01"],
  "source_url": "",
  "sections": [{"page": 1, "section": "1. 기록", "text": "교육용 가상 문서입니다. 담당자가 입력값과 단위를 확인합니다."}]
}
```

`kind`: `official-summary`, `onboard`, `sample`. 모르는 페이지는 `null`로 둡니다. PDF는 JSON의 `sections` 대신 본문을 추출합니다. 제한은 10MB/250페이지/추출 100만 자이며 스캔·암호화 PDF는 거절합니다. 원본 PDF 파일 자체는 저장하지 않으므로 별도 보존하세요. 관리자의 빈 `vessels`는 전체 선박 공개, 담당자는 배정 선박을 지정해야 합니다. `restricted: true`는 일반 사용자 비공개입니다.

## 4. 선택적 LLM 연결

기본은 `LLM_ENABLED=0`입니다. 함수 호출과 JSON 출력이 가능한 OpenAI 호환 Chat Completions 서버를 연결합니다. 외부 모델을 켜면 질문과 접근 가능한 문서 발췌가 해당 서버로 전송됩니다.

```dotenv
# .env.local — 로컬 모델 예시
LLM_ENABLED=1
LLM_BASE_URL=http://127.0.0.1:11434/v1
LLM_MODEL=YOUR_INSTALLED_MODEL
LLM_API_KEY=
ALLOW_EXTERNAL_LLM=0
```

GPT API는 `LLM_BASE_URL=https://api.openai.com/v1`, 사용 가능한 모델명, 서버 전용 `LLM_API_KEY`, `ALLOW_EXTERNAL_LLM=1`을 설정하세요. 재시작 후 **근거 기반 AI 답변**을 선택할 때만 호출합니다. 모델/학교 크레딧 계정은 자동 생성하지 않습니다. 도구는 읽기 전용 경로를 선택하고 계산은 Python이 수행합니다. 근거 ID·인용문 검사가 문장 전체의 의미적 정합성을 증명하지는 않습니다. 오류 시 원문 근거로 전환합니다.

## 5. 백업·복원

```powershell
.\.venv\Scripts\python.exe -m service.manage backup
# 반드시 별도로 준비한 빈 DB에 복원
.\.venv\Scripts\python.exe -m service.manage restore --path data/backups/FILE.json --database-url 'postgresql+psycopg://USER:PASSWORD@127.0.0.1:55432/EMPTY_DB'
```

사용자(비밀번호 해시)·선박·운항·문서/청크·보고서·이력을 일관된 스냅샷으로 저장합니다. 기존 파일/비어 있지 않은 DB는 덮어쓰지 않습니다. 세션·API 키·DB 비밀번호는 제외합니다. 백업의 비밀이 아닌 설정은 참고용이며 `.env.local`에 수동 재적용해야 합니다. 복구 DB로 설정을 바꾼 뒤 `prepare-vectors`를 실행하세요. 백업, 비밀 설정, 원본 PDF는 접근 제한을 적용해 별도 보관하세요.

## 6. 개발 및 검증

```powershell
.\.venv\Scripts\python.exe -m pytest -q
npm test
npm run build
```

새 서비스 테스트는 외래키를 켠 격리 SQLite를 사용하며 실제 앱 저장소는 PostgreSQL입니다. `npm test`는 기존 MVP 회귀 테스트입니다. 2026-09-29 검증: 새 서비스 28개, 기존 MVP 15개 통과, Next.js 프로덕션 빌드 성공. 로컬 PostgreSQL·Chroma 질의 및 브라우저 보고서 저장 확인. 실제 LLM 추론, Ubuntu 실행, 대규모 부하와 KPI/SUS 평가는 미검증입니다.

| 위치 | 역할 |
|---|---|
| `web/app/` | Next.js 화면, 로컬 API 프록시 |
| `service/app.py` | 인증·권한·업무 API, `/docs`에 API 명세 |
| `service/storage.py` | 테이블과 제약 조건 |
| `service/retrieval.py` | PDF 청크·BM25·Chroma·권한 필터 |
| `service/calculations.py` | 입력 검증·계산·보고서 템플릿 |
| `service/llm.py` | 모델 Gateway와 규칙 대체 경로 |
| `service/manage.py` | DB·샘플·색인·백업·복원 |
| `service/tests/` | 권한·업무·PDF·복원 검증 |

API 추가 시 서버에서 역할/선박/문서 권한을 확인하고 쓰기에는 CSRF 토큰을 요구하세요. Pydantic 입력 검증과 Python 계산을 사용하고 화면에서 재산정하지 않습니다. DB 변경은 기존 자료를 보존하는 명시적 마이그레이션이 필요합니다. 현재는 초기 스키마와 가산적 색인 생성만 제공합니다.

## 문제 해결

- DB 연결 실패: 전용 DB 시작 명령, `.env.local`, PostgreSQL 경로를 확인합니다.
- 빈 결과: 샘플 기간/배정 선박/문서 현재 버전과 권한을 확인합니다.
- 벡터 경고: 준비 명령, `CHROMA_PATH` 및 쓰기 권한을 확인하고 재색인합니다.
- 포트 사용 중: 기존 개발 서버 종료 후 재실행. 웹 3000/API 8000/DB 55432입니다.
- 비밀번호 파일 없음: `bootstrap`은 기존 관리자 비밀번호를 재발급하지 않습니다.

## 기존 MVP 및 변경 이력

- **2026-10-05:** 사용자 main의 역할4 전용 API를 보존하면서 원본 웹 MVP·캡스톤 앱 복원, 관리자 세션 기반 역할4 조회와 감사 기록 통합. 검증 결과는 `docs/INTEGRATION.md` 참고.

`npm run legacy` 또는 기존 `start.cmd`/`start.ps1`은 Node.js·SQLite 버전을 5173에서 실행합니다. 새 버전과 계정·보고서 DB를 공유하지 않습니다. [기존 안내](docs/LEGACY_MVP.md), [기존 백엔드](docs/BACKEND.md), [설계 대응](docs/DESIGN.md)을 참고하세요.

- **2026-09-29:** 설계 스택으로 기본 실행 전환, 인증/역할·PDF/개정/RAG·Python 계산·보고서/이력·백업복원. 기존 MVP·데이터 보존, 실행 경로 구분.
- 이전 변경 내역은 기존 README 보관본에 있습니다.

## 역할 4 데이터 전처리 (2026-09-30)

실제 MRV 보고기간 집계와 합성 Noon 개발 데이터를 분리했습니다. [데이터 계약·ERD](docs/role4/ROLE4_DATA_CONTRACT.md), [실행 결과](docs/role4/VALIDATION.md)를 참고하세요. 새 정의는 기존 19개 테이블 초안 전체를 대체하지 않습니다. 적재 스크립트와 검증 SQL을 제공합니다. 기존 role4 스키마는 덮어쓰지 않으며 원본·전처리 CSV와 실제 DB는 Git에 포함하지 않습니다.

원본 6개 파일이 있는 폴더를 지정합니다. 하위 폴더도 탐색하며 같은 이름이 둘 이상이면 중단합니다. 원본은 읽기만 하고 결과 폴더는 매번 새 이름을 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r scripts/requirements-role4.txt
.\.venv\Scripts\python.exe scripts/prepare_role4.py --source 'D:/Data' --out data/role4/new-run
.\.venv\Scripts\python.exe scripts/test_prepare_role4.py
.\.venv\Scripts\python.exe scripts/describe_role4.py --out docs/role4
```

실행 결과는 원본 체크섬·출처 목록, 별도 연간/합성 일별 CSV, 선박·가상 항차 키, 품질 이슈 CSV, 원본 레코드 JSONL, summary.json입니다. 빈 값은 임의로 0이나 가상값으로 채우지 않습니다. 파생거리는 추정치이며 공식 계산 검증의 정답이 아닙니다. 데이터 파일은 Git에 포함하지 않습니다. 이용조건 검토는 별도이며 추가 공개·재배포는 수행하지 않았습니다.

- **2026-09-30:** MRV 80,552행과 합성 Noon 4,380행 전처리, 24,654행 운항시간 원본 복구, Partial 분리, 역할 4 데이터 계약·ERD·DDL 작성. 기존 적재 결과는 docs/role4/DB_LOAD_RESULT.md에 보관합니다.
