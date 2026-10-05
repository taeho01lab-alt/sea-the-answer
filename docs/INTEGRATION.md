# 웹 앱·역할4 통합 검증

검증 날짜: 2026-10-05.

사용자 레포 main `fe37d57`의 역할4 모듈·전처리·문서·PDF를 유지하고, upstream `7c9a517`의 원본 MVP와 로컬 `fba751d` 기반 캡스톤 스택을 복원했습니다. 기존 로컬 작업 폴더 대신 별도 작업 폴더에서 통합했습니다.

## 실행과 호환성

- `npm start`: Next.js 3000, 로그인·문서 검색·운항·보고서·Python API 8000.
- `npm run legacy` 또는 `start.cmd`: 원본 웹 MVP 5173, 별도 SQLite.
- `python -m uvicorn service.role4_standalone:create_app --factory --host 127.0.0.1 --port 8001`: 기존 역할4 Bearer API. 기존 클라이언트는 포트와 서버 실행 모듈을 변경합니다.
- 역할4 조회·계산 모듈은 사용자 main과 동일하며 두 서버가 공유합니다. 웹 API는 관리자 세션·CSRF·앱 감사 기록을 사용합니다. 웹 관리자에게만 정형 데이터 탭을 표시합니다.
- 기존 DB URL의 `postgresql://`와 `postgresql+psycopg://`를 모두 지원합니다. 웹 앱은 public 앱 테이블을 추가하며 role4 원본 스키마와 데이터를 재적재하지 않습니다.
- 전체 설치는 `uv sync --frozen`과 `npm ci --prefix web`입니다. 역할4 전용 requirements 잠금 파일도 유지합니다. 전체 Python 잠금 파일에 기존 전처리용 openpyxl·pandas를 포함했습니다.

## 실제 수행한 검증

- `python -m pytest -q`: 76 passed, 4 skipped. PostgreSQL 실제 데이터 비교 테스트는 DB URL 미설정으로 건너뜁니다.
- `node --test tests/*.test.mjs`: 15 passed. 검색·계산·HTTP·DB 영속성·로컬/LAN 실행 회귀 포함.
- `python scripts/test_prepare_role4.py`: 3 passed.
- Next.js 16.3.6, React 19.3.0의 독립 설치로 웹 소스와 동일한 app·next.config를 검증 폴더에서 빌드: 성공. 원래 OneDrive 의존성 폴더는 클라우드 파일 읽기 지연이 있어 독립 설치로 확인했습니다.
- 역할4 웹 통합 테스트: 미로그인 거부, 일반/담당자 거부, 관리자 POST CSRF 검증, 조회·계산·도우미의 기존 모듈 호출과 감사 기록 확인.

실제 PostgreSQL·원본 데이터 조회, 실제 LLM 추론과 브라우저 화면 조작은 이번 검증에 포함하지 않습니다. 계산 API는 제공하지만 조회 화면에서 자동 계산하지 않습니다. 원본 데이터·DB·비밀번호는 커밋하지 않습니다. 기존 단계별 문서·PDF의 날짜와 테스트 수치는 당시 기록입니다.
