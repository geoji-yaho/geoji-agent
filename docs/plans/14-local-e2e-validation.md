# 로컬 판결·짤·카드 전체 흐름 검증

검토일: 2026-09-15. 실제 실행 결과와 다음 구현 계획을 구분한다.
후속 구현 및 최신 검증 결과는 [15 로컬 구현·재실행](15-local-e2e-implementation.md)을 참조한다.
이번 사용자 요청: 로컬에서 먼저 사용자 입력 → 전원 투표 → AI 판결·검색 힌트 → 기존 짤 선택 →
판결 저장을 검증한다. 완성 카드 PNG의 서버 저장과 사용자 기기 다운로드를 모두 지원하고,
이후 사용자·관리자 이미지 등록 및 변환을 구현·검증한다.

## 1. 결론과 확인 기준

**현재는 로컬 백엔드와 AI API·워커를 함께 실행하는 것이 적합하다.** 서버 주소만 바꿔서는
프론트가 새 판결 흐름을 사용하지 않으며, PNG 서버 저장·이미지 등록은 별도 구현이 필요하다.

| 대상 | 확인한 버전·위치 |
|---|---|
| AI | `46090e3fbdd3e5062289c01b309867f418c61002` |
| 백엔드 | [ac5b88b](https://github.com/geoji-yaho/geoji-server/tree/ac5b88b338977a2d7b554b9430565a6da4f8229f), `/private/tmp/geoji-e2e-review-20260915-server` |
| 프론트 | [f7fd4f4](https://github.com/geoji-yaho/geoji-web/tree/f7fd4f443999fff83459b21262222f50564d776d), `/private/tmp/geoji-e2e-review-20260915-web` |
| 배포 서버 | [health](https://d13dsunuwl4yud.cloudfront.net/actuator/health): `UP` 확인. 사용자 설명상 테스트 서버지만 로컬 우선 |
| 배포 웹 | [떼거지 웹](https://geoji-yaho.github.io/geoji-web/): HTTP 200 확인 |

Git의 최신 소스와 현재 배포된 바이너리가 같다는 것은 health만으로 확인할 수 없다.
배포 서버에는 테스트 데이터를 쓰지 않았다. 로컬 테스트에서도 실제 벤더 호출은 하지 않았다.

## 2. 실제 구조와 구현 현황

```mermaid
flowchart TD
  A[사용자 입력] --> B[백엔드 post-submissions]
  B --> C[AI API: intake]
  B --> D[게시물 저장 + PREPARE 큐]
  D --> E[AI 워커: 조서·드립 준비]
  D --> F[테스트 배심원 전원 투표]
  F --> G[백엔드 평결 확정 + SENTENCE 큐]
  E --> H[AI 워커: 양형·서기·검수]
  G --> H
  H --> I[백엔드 finalize: 문구 검증·짤 선택·DB 저장]
  I --> J[verdict / share-card JSON 조회]
  J --> K[추가 구현: 카드 PNG 생성·서버 저장]
  K --> L[같은 PNG 다운로드]
```

| 구간 | 현재 확인한 상태 |
|---|---|
| 게시물 등록·전원 투표·평결·job INSERT | 백엔드에 구현. 기존 `seed` 프로필이 실제 서비스 메서드를 이용 |
| 조서·양형·서기·검수·finalize 호출 | AI에 구현. fake 모델 테스트와 실제 백엔드 폴백 연결 확인 |
| 실제 LLM 생성 품질·시간·비용 | 이번 세션 미검증. `OPENAI_API_KEY`, `XAI_API_KEY` 없음 |
| 짤 힌트 | `meme_tag`, `meme_hints.emotion`, `keywords`, 문구별 `banter_strategy` 구현 |
| 짤 선택 | 백엔드 `MemeScorer`, `MemeSelector` 구현. 태그 필터 + 점수, 최근 사용 감점, 결정적 동점 처리 |
| 벡터·키워드 하이브리드 검색·리랭킹 | 11·13의 설계/평가 계획. 현재 실행 경로에는 없음 |
| 문구·선택 짤 저장 | `verdicts`, `verdict_texts`, `verdicts.meme_image_id` 구현 |
| 카드용 JSON | `GET /api/posts/{id}/share-card` 구현. PNG 생성·저장 API가 아님 |
| 현재 프론트 | 구형 `expenses/trial` 경로 사용. 새 posts 판결·짤 연결 없음 |
| 사용자 기기 PNG 저장 | 프론트 `html-to-image` 다운로드 코드 있음. 이번 브라우저 검증은 미실행 |
| 카드 PNG 서버 저장 | 미구현 |
| 사용자·관리자 이미지 업로드/변환 API | 미구현. b-meme는 로컬 생성·메타데이터 도구 |

### 2.1 현재 웹에서 AI 성공으로 오해할 수 있는 경로

프론트 `src/shared/api/trials.ts`의 `judgeTrial()` →
백엔드 `ExpenseTrialController` → `StubAiClient.judge()`는 고정 문구를 반환한다.
새 AI 워커의 큐를 통과하지 않는다. 기존 `expenses`와 신규 `posts`는 별도 테이블이다.

프론트 `VerdictCardPage.tsx`는 이 구형 Trial을 읽는다. `ShareCardPreview.tsx`에는 짤 속성도 없다.
따라서 기존 화면의 판결 표시나 PNG 다운로드 성공만으로 신규 AI 파이프라인을 합격시키면 안 된다.

## 3. 이번에 실제 실행한 검증

| 검증 | 결과 | 의미·한계 |
|---|---|---|
| AI `pytest --ignore=tests/integration -q` | 1,112 통과 | 단위·계약·로컬 도구 검사. 라이브 모델 품질 증거 아님 |
| AI `pytest tests/integration -q` | 207 통과 | 실제 PostgreSQL + 가짜 백엔드 + fake 모델 |
| AI `ruff check .` | 통과 | 정적 검사 |
| 백엔드 `./gradlew build` | 481 테스트, 실패·오류·skip 0 | Java 25 + Testcontainers. 컴파일·테스트·bootJar 성공 |
| 짤 카탈로그 검증 | 10장 통과 | 파일·해시·메타데이터·tag 검사. 업로드 증거 아님 |
| 실제 Spring + AI API + 워커 연결 | 게시물 12, 투표 26, 판결·문구 각 10 저장 | 벤더 키 없음. 모두 `TEMPLATE_READY`; 유죄 6·무죄 2·동의 1·기각 1 |
| 실제 연결의 큐 처리 | PREPARE 12 + SENTENCE 10 + RETAIN 30 모두 SUCCEEDED | 최종 집계 시 실패 0. AI 문구·짤·PNG 성공 증거 아님 |

실제 연결 DB의 `meme_images`는 0행, `meme_image_id`는 모두 NULL이었다.
이는 짤 선택 완료가 아니라 등록 단계가 아직 필요하다는 증거다.
테스트 DB는 이번 작업의 임시 컨테이너이며 공유 Supabase와 분리했다.
백엔드 테스트용 001~003은 AI 정본과 출처 주석 1줄만 다르고 SQL 내용은 같았다.
기존 업무 테이블 테스트 DDL은 운영 FK·인덱스·RLS 전체를 재현하지 않으므로 운영 권한 검증은 별도다.

보존한 실행 증거:

- [로컬 DB 최종 집계 JSON](evidence/20260915/local-stack-summary.json): 조회 시각, 판결·작업·실제 모델 호출 건수.
- [Java HTTP 비교 재현 파일](evidence/20260915/GeojiHttpProbe.java): 이번 실행에 사용한 원본.
  localhost:18100과 고정된 테스트 토큰 `geoji-local-service`만 사용한다. 실제 자격증명이 아니다.
  5.2의 AI API를 먼저 실행한 뒤 JDK 25로 `java docs/plans/evidence/20260915/GeojiHttpProbe.java`를 실행한다.
  기본 버전/HTTP 1.1 × 고정 길이/스트리밍의 네 가지 상태 코드가 출력된다.
  422 재현은 별도 로컬 실행에서 API의 `--http h11`을 빼고 비교한다.
  이 파일은 진단용이며 반환 코드를 자동 단언하는 회귀 테스트는 아니다.

### 3.1 실제 연결에서 발견한 422와 검증한 우회

Java 25 `HttpClient`의 기본 버전 설정으로 로컬 Uvicorn에 POST하면
`Unsupported upgrade request`와 `422 INVALID_REQUEST`가 발생해 첫 seed 제출이 중단됐다.
같은 유효 JSON을 보내 전송 설정만 바꿔 확인했다.

| AI API 실행 옵션 | Java 기본 버전 | Java HTTP/1.1 명시 |
|---|---|---|
| 기본 `--http auto` | 422 | 200 |
| `--http h11` | 200 | 200 |

고정 길이·스트리밍 본문 각각 동일했다. `--http h11`로 재기동한 뒤 기존 seed가 성공했다.
이번에는 실행 옵션으로 해결했으며 백엔드 소스는 변경하지 않았다.
장기 수정 후보는 백엔드 `AiApiClientConfig`, `IntakeClient`, `AiTraceClient`가 만드는
`HttpClient`에 `.version(HttpClient.Version.HTTP_1_1)`을 명시하고 실제 HTTP 회귀 테스트를 추가하는 것이다.
`IntakeClient`·`AiTraceClient`가 request factory를 다시 만들기 때문에 공통 설정 한 곳만 수정해서는 부족할 수 있다.

### 3.2 설정 문서 오류 예방

`BACKEND_INTERNAL_URL`은 **호스트 루트**다: `http://127.0.0.1:18080`.
AI `BackendHttp`가 `/internal/v1/...`를 붙인다. 백엔드 전달 보고서 A3의
“주소 + `/internal/v1`”를 그대로 설정하면 경로가 중복된다.

또한 공개 API는 `/api/...`와 camelCase이고, 내부 API는 `/internal/v1/...`와 snake_case다.
AI의 `verdict-view-v1` JSON Schema는 공개 응답의 camelCase·nullable 변경을 아직 반영하지 않았다.
새 프론트 연동 전 공개 계약을 통일해야 한다.

## 4. 권장 실행 환경

### 4.1 첫 검증: 로컬 전체 구성

- 실제 Spring 백엔드, AI API, AI 워커를 별도 프로세스로 실행한다.
- 백엔드와 AI는 같은 **로컬 DB의 서로 다른 소유 스키마**를 사용한다.
- 반복 가능한 통합 테스트에서는 `FakeLLM`을 명시적으로 주입한다. CLI에는 fake 모드가 없다.
- 실제 LLM 실측은 별도 명령과 테스트 데이터·건수·예산으로 실행한다.
- 인증은 자동 테스트의 mock JWT 또는 로컬 테스트 issuer를 사용한다. 실제 공개 API 테스트에는 유효한 테스트 사용자 JWT가 필요하다. 운영 인증을 열어두는 디버그 API를 만들지 않는다.
- 완성 카드는 먼저 로컬 스토리지 어댑터로 검사하고, 이후 테스트 버킷에서 같은 계약을 검증한다.

### 4.2 이후 검증: 배포 환경

배포 백엔드 + 로컬 AI도 원리상 가능하지만 아래 조건이 전부 필요하다.

1. 배포 백엔드가 로컬 AI API로 접근할 수 있는 승인된 경로. 서버의 `localhost`는 사용자 Mac이 아니다.
2. 로컬 워커가 접근할 수 있는 동일 테스트 DB, 내부 API, 양쪽 같은 서비스 토큰.
3. 같은 `ai.jobs`를 읽는 기존 워커와 경쟁하지 않는 격리 환경. 현재 큐에 테스트 전용 환경 라우팅 필드는 없다.
4. CloudFront의 내부 경로·POST·Authorization 전달 및 캐시 설정 확인. health 200은 이를 보장하지 않는다.
5. 실제 테스트 자산 URL·브라우저 origin에서 이미지 읽기와 PNG 저장 확인.

로컬 통과 후 테스트 서버에 같은 버전을 올려 최종 재확인하는 순서를 권장한다.

## 5. 확인된 명령과 재실행 절차

아래는 이번 사용한 절차다. 임시 경로는 정리될 수 있으므로 다음 실행에서는 각 저장소 경로를 다시 지정한다.
Python은 이 저장소의 `.venv/bin/python`을 사용했다. 현재 셸에는 `uv`가 없고 venv의
`geoji-ai` console script도 없어 `python -m geoji_ai.cli`로 실행했다.
백엔드는 JDK 25가 필요하다. 이번에는 공식 [Temurin 배포](https://adoptium.net/installation/ci-scripts)를
임시 폴더에 받고 공식 SHA-256과 대조했다. 시스템 Java 17·21 설정은 바꾸지 않았다.

### 5.1 독립 테스트

```bash
cd /Users/hyun/dev/geoji
.venv/bin/python -m pytest --ignore=tests/integration -q
.venv/bin/ruff check .
.venv/bin/python scripts/build_meme_release.py \
  --catalog outputs/b-meme/catalog-v1.json \
  --output /private/tmp/geoji-registration-check.json
```

통합 테스트는 **ai 스키마를 삭제·재생성**한다. 반드시 새 전용 컨테이너에서 실행한다.
아래 비밀번호는 외부 비밀값이 아닌 로컬 예시다. 포트가 사용 중이면 다른 미사용 포트를 정한다.

```bash
docker run -d --rm --name geoji-local-e2e \
  -p 127.0.0.1:55432:5432 \
  -e POSTGRES_PASSWORD=geoji-local-test -e POSTGRES_DB=geoji_test \
  --tmpfs /var/lib/postgresql/data postgres:16
docker exec geoji-local-e2e pg_isready -U postgres
# accepting connections 확인 후 실행
TEST_DATABASE_URL=postgresql+asyncpg://postgres:geoji-local-test@127.0.0.1:55432/geoji_test \
  .venv/bin/python -m pytest tests/integration -q
```

백엔드의 기본 게이트는 JDK 25와 Docker를 준비하고 저장소 루트에서 `./gradlew build`다.
백엔드 Testcontainers는 스스로 별도 DB를 만든다. 위 AI 테스트 DB와 별개다.

### 5.2 실제 백엔드·워커 폴백 연결 재현

1. 위 전용 컨테이너에 `docker exec geoji-local-e2e createdb -U postgres geoji_stack`으로 **빈 DB**를 만든다.
2. 백엔드 `src/test/resources/db/*.sql`을 파일명 순으로 이 DB에만 적용한다.
   `000a_roles → 000b_base_schema_test → 001~003 → 004 → 004b_*` 순서다.
   이번에는 Python asyncpg로 각 파일 전체를 읽어 `execute()`했다. 이 SQL 묶음을 운영에 적용하지 않는다.
3. AI 터미널마다 아래 환경을 설정한다. API와 워커를 먼저 띄운다.

```bash
cd /Users/hyun/dev/geoji
export DATABASE_URL=postgresql+asyncpg://postgres:geoji-local-test@127.0.0.1:55432/geoji_stack
export BACKEND_INTERNAL_URL=http://127.0.0.1:18080
export SERVICE_AUTH_TOKEN=geoji-local-service
export GUARDRAIL_POLICY_VERSION=guardrail-v2
export OPENAI_API_KEY=
export XAI_API_KEY=

# 터미널 A
.venv/bin/python -m uvicorn geoji_ai.api.app:app \
  --host 127.0.0.1 --port 18100 --http h11

# 위 환경을 똑같이 설정한 터미널 B
.venv/bin/python -m geoji_ai.cli worker
```

4. 백엔드 터미널에서 아래 환경을 설정하고 JDK 25로 빌드한 실행 jar를 시작한다.
   백엔드는 `.env`를 자동으로 읽지 않는다. `seed`는 기존 서비스 메서드를 호출하므로 실제 사용자 JWT 없이
   이 로컬 테스트 스키마의 가상 사용자로 제출·투표 연결을 재현할 수 있다. 공개 API 인증 검증을 대체하지 않는다.

```bash
export DB_URL=jdbc:postgresql://127.0.0.1:55432/geoji_stack
export DB_USERNAME=postgres
export DB_PASSWORD=geoji-local-test
export SERVICE_AUTH_TOKEN=geoji-local-service
export AI_API_BASE_URL=http://127.0.0.1:18100
export GUARDRAIL_POLICY_VERSION=guardrail-v2
export SUPABASE_JWT_ISSUER_URI=http://127.0.0.1:18099/auth/v1
export GEOJI_SEED_USER_IDS=00000000-0000-4000-8000-000000000001,00000000-0000-4000-8000-000000000002,00000000-0000-4000-8000-000000000003,00000000-0000-4000-8000-000000000004

# 백엔드 저장소 루트. JAVA_HOME은 설치된 JDK 25 경로
"$JAVA_HOME/bin/java" -jar build/libs/backend-0.0.1-SNAPSHOT.jar \
  --server.address=127.0.0.1 --server.port=18080 \
  --spring.profiles.active=seed --logging.level.org.hibernate.SQL=warn
```

여기서 `18099`는 테스트 issuer 자리이며 이번에는 issuer 서버를 실행하지 않았다.
seed는 성공하지만 JWT가 필요한 공개 API 호출은 별도 인증 준비가 필요하다.
AI `/health/ready`는 벤더 키가 없어 503인 것이 예상 결과다.

5. `docker exec -i geoji-local-e2e psql -U postgres -d geoji_stack`에서 아래 SQL을 확인한다.

```sql
SELECT kind, status, last_error_code, count(*)
FROM ai.jobs GROUP BY 1,2,3 ORDER BY 1,2;

SELECT jury_result, sentence_status, text_status, sentence_source, count(*)
FROM verdicts GROUP BY 1,2,3,4;

SELECT v.id, v.text_version, v.meme_image_id, t.intensity, t.source, t.headline
FROM verdicts v JOIN verdict_texts t ON t.verdict_id = v.id;
```

6. 종료할 때는 API·워커·백엔드를 각각 종료하고, 이번에 만든 컨테이너만
   `docker stop geoji-local-e2e`로 종료한다. `--rm`·tmpfs이므로 테스트 DB는 보존되지 않는다.
   보존이 필요한 결과는 종료 전에 비밀값 없는 요약·선택 ID·검사 결과로 추출한다.

## 6. 실제 AI·짤 선택 검증을 완성하는 순서

### 6.1 합격 조건을 구분한다

1. **연결 합격:** 공개 입력 또는 seed → 실제 백엔드 job → 워커 → DB 저장. 이번에는 폴백으로 확인했다.
2. **AI 생성 합격:** 실제 모델 호출 원장과 결과를 확인한다. 단순 `SUCCEEDED`가 아니라
   `textStatus=AI_READY`, 대상 문구 `source=AI`, 정상 검수·모델 사용량을 함께 확인한다.
   fake 모델로 만든 `AI_READY`는 전송·저장 검사에만 사용한다.
3. **짤 합격:** 활성 후보 목록에서 기대 순위의 이미지가 선택되고, 저장된 ID·URL·원본 해시가 일치한다.
4. **카드 합격:** 공개 카드 JSON을 렌더링한 PNG가 서버에 저장되고 재조회·다운로드한 파일 해시가 같다.

### 6.2 전원 투표는 테스트 실행기가 자동으로 한다

운영 재판 규칙을 바꾸지 않는다. 테스트 방과 작성자·배심원 계정을 준비하고 테스트 실행기가
모든 배심원의 표를 순서대로 넣는다. 기존 `DemoSeedRunner`가 이 접근을 이미 사용한다.

공개 API 실행기에서는 다음 실제 경로를 사용한다.

1. 작성자 `POST /api/post-submissions`에 `{postType, amountKrw, category, item, reason, roomIds}`.
2. `COMPLETED.postId`를 받는다. `NEEDS_INPUT`이면 테스트 정책에 따라 complete의 `PROCEED` 또는 `REVISE`.
   `BLOCKED`를 임의 통과시키지 않는다.
3. 배심원 각각 `POST /api/posts/{postId}/votes`에 `{verdict, reason, roomId}`.
4. `GET /api/posts/{postId}/verdict?room_id=...` 폴링. 전원 투표 후에도 PREPARE 종료 대기 최대 30초가 있을 수 있다.
5. 백엔드가 SENTENCE를 넣은 뒤 10초 마감과 폴백 여부를 관찰한다.
6. DB와 `GET /api/posts/{postId}/share-card`를 대조한다.

사용자에게 맡길 값은 실제 모델 키의 안전한 로컬 주입과 실측 예산·건수다. 키를 채팅에 적지 않는다.
처음부터 12건 seed로 유료 실행하지 않고 1건으로 계약·시간 예산을 확인한 뒤 사례를 늘리는 것이 좋다.
모델 ID의 계정 사용 가능 여부·Structured Outputs·단가는 실측 전 공식 문서와 API에서 확인한다.

### 6.3 짤 카탈로그를 먼저 등록한다

현재 10장은 `GUILTY_HEAVY` 7, `GUILTY_LIGHT` 1, `REJECTED` 2다.
`NOT_GUILTY`·`APPROVED`가 없고 등록 payload는 모두 `is_active=false`, `image_url=null`이다.

- 로컬 테스트 자산 서버와 별도 테스트 DB에 승인된 이미지·metadata를 등록한다.
- 무죄·동의 및 후보 없음에 사용할 기본 자산을 정한다. 다른 결과의 짤을 임의로 재태깅하지 않는다.
- 처음에는 현재 점수 규칙을 그대로 검증한다: 전략 +3, 감정 +2, 키워드 교집합 개수, 최근 사용 −5.
- 이후 사람 라벨로 “어울리는가”를 별도 평가한다. 점수가 높은 것이 의미상 최적이라는 보장은 없다.
- 11·13의 하이브리드 검색·리랭킹은 이 기준선과 비교한 뒤 별도 구현한다.

## 7. 카드 서버 저장 + 기기 다운로드 설계 제안

이 절은 추가 구현안이며 현재 동작이 아니다. 사용자 요청에 따라 두 저장 방식을 모두 목표로 한다.

1. finalize가 판결 데이터와 선택 짤을 저장한다.
2. 공개 카드용 JSON에서 렌더링 입력을 만든다. 방 내부 문구를 그대로 외부 공유 PNG로 만들지 않는다.
3. 카드 렌더러가 한국어 폰트·짤 로딩 완료를 확인하고 PNG를 생성한다.
4. 서버 스토리지에 올리고 카드 상태·객체 키·파일 해시를 기록한다.
5. 사용자는 저장된 같은 PNG를 미리보기·다운로드한다. 요청 때마다 다른 이미지를 생성하지 않는다.

상태 제안: `PENDING → RENDERING → READY`, 실패하면 `FAILED`에서 카드만 재시도.
판결·문구 저장 트랜잭션 안에서 PNG 렌더링이나 외부 업로드를 기다리지 않는다.
멱등 키에는 판결 ID·공개 카드 입력 버전/해시·선택 이미지 버전·템플릿 버전을 포함한다.
삭제·공유 철회·근거 무효화가 일어나면 이미 저장한 카드도 조회를 차단하거나 새 공개 문구로 갱신해야 한다.
만료 없는 공개 CDN URL은 즉시 접근 철회를 보장하지 못하므로 기본 접근 방식은 구현 전에 결정한다.

DB에는 문구·참조·상태·객체 키를 저장하고 PNG 바이트는 스토리지에 저장하는 구성을 권장한다.
사용자 PNG 다운로드와 서버 PNG 저장은 이 동일 산출물을 공유한다.

## 8. 이후 사용자·관리자 이미지 등록·변환

이미 준비한 10장을 넣는 관리자 등록 경로를 먼저 만들고 사용자 업로드를 확장한다.

```text
업로드 예약 → 원본 업로드·파일 검증 → 선택적 변환 작업
→ 결과 이미지·메타데이터 저장 → 검수 승인 → 검색 후보 활성화
```

- 원본 그대로 등록과 변환 후 등록이 같은 저장 경계를 사용한다.
- 원본·변환본·메타데이터 버전을 연결하고 업로드 요청 ID로 중복을 막는다.
- 실제 MIME·크기·해시·디코딩을 검증한다. 사용자 원본은 기본 비공개로 둔다.
- 개인용 자산과 공용 카탈로그를 소유자·공개 범위로 구분한다. 지금의 `MemeImage`에는 이 필드가 없어 변경이 필요하다.
- 변환·메타데이터 분석은 비동기 작업으로 처리한다. timeout·중복 제출·worker 재시작·비용 상한을 기록한다.
- 변환이 성공하고 DB 등록만 실패하면 기존 파일을 재사용한다. 이미지를 다시 생성해 중복 비용을 내지 않는다.
- 사용자 업로드가 자동으로 전체 사용자 판결 후보가 되지 않도록 검수/공개 승인과 활성 상태를 분리한다.
- Codex b-meme 스킬은 로컬 도구이므로 그대로 백엔드 서버 기능이 되는 것은 아니다.
  자동 변환을 위해서는 서버에서 호출할 이미지 모델/provider와 사용 권한·비용·재시도 계약이 필요하다.

## 9. 구현 전 정리할 항목과 완료 체크리스트

| 우선순위 | 항목 | 처리 방향 |
|---|---|---|
| 먼저 | 로컬 HTTP 422 | `--http h11` 우회 검증 완료. Java HTTP/1.1 명시 + 실제 연결 회귀 테스트 검토 |
| 먼저 | 구형 프론트 재판 경로 | 신규 posts 입력·투표·폴링·share-card로 연결; 기존 expense ID와 혼용 금지 |
| 먼저 | 공개 응답 계약 차이 | `/api`, camelCase, nullable 필드와 중단 상태를 정본에 반영한 뒤 프론트 작성 |
| 먼저 | 짤 카탈로그 미등록·기본 자산 없음 | 로컬 등록 및 결과별 후보·기본 이미지 마련 |
| 다음 | PNG 서버 저장 | 별도 카드 작업과 저장 계약 구현. 외부 스토리지 선택·접근 정책 확정 |
| 다음 | 이미지 등록·변환 | 관리자 등록 → 사용자 비공개 업로드 → 선택적 변환·검수 순서 |
| 회귀 | 삭제·404 의미 차이 | 실제 백엔드는 삭제 snapshot 404; AI PREPARE/SENTENCE는 이를 일반 fail로 처리. 취소와 실패 관측 기준 정리 |
| 회귀 | 메모리·개인정보 미결 | 백엔드 `docs/ai-team-report.md` Q2~Q5의 파생 삭제·즉시 차단·nullable·RULE 참조를 별도 계약 테스트로 확인 |
| 회귀 | demo C 가정 | 백엔드 첫 스타벅스 유죄율 2/3과 AI 메모리 seed의 0.8 차이를 맞춘 뒤 반복 소비 품질 평가 |

- [x] 로컬 독립 DB에서 AI 테스트·백엔드 build 실행
- [x] 실제 API·워커·백엔드 폴백 경로 및 판결 DB 저장 확인
- [x] 로컬 HTTP 호환 문제 재현 및 실행 옵션 우회 검증
- [ ] 실제 모델 1건부터 AI_READY·검수·사용량·지연 검증
- [ ] 저장된 짤 선택·ID 고정·이미지 로딩 검증
- [ ] 프론트 신규 판결·공유 카드 경로 연결 및 브라우저 검증
- [ ] 카드 PNG 서버 저장·재조회·동일 파일 다운로드
- [ ] 카드 생성 실패·중복 요청·삭제 후 재조회 차단
- [ ] 관리자 이미지 등록·활성화와 사용자 업로드·변환 테스트
- [ ] 같은 버전으로 배포 테스트 환경 재검증

**전체 E2E 완료는 위 미완료 항목의 실제 결과를 확보한 뒤에만 선언한다.**

## 10. 세션 종료 시점과 다음 작업

- 이번 검증에서 실행한 API·워커·백엔드 프로세스를 모두 종료했다.
- `geoji-e2e-audit-20260915` 컨테이너는 종료·삭제했고 tmpfs DB도 사라졌다. 다음 실행은 새 DB로 시작한다.
- 백엔드·프론트 임시 clone은 제품 코드 변경 없이 남아 있다. `/private/tmp` 파일은 영구 보관으로 간주하지 않는다.
- 임시 JDK: `/private/tmp/geoji-e2e-jdk25/Contents/Home`, Gradle 캐시: `/private/tmp/geoji-e2e-gradle`.
  경로가 남아 있는지 확인하고 사용할 수 없으면 JDK 25와 프로젝트 lock에 맞는 도구를 준비한다.
- 다음 작업은 기존 결과를 다시 설명하는 데서 끝내지 않고 **실제 Spring 백엔드와 연결하는 반복 가능한 로컬 테스트 실행기**를 구현한다.
  입력·전원 투표·판결·짤 저장을 먼저 검증하고, 카드 PNG 서버 저장·동일 파일 다운로드로 확장한다.
  이후 관리자 등록·사용자 업로드·선택적 변환을 같은 등록 경계로 구현한다.
- fake 모델 검증과 실제 LLM 실측 결과를 분리한다. 키와 실측 예산 확인이 필요한 동안에도 로컬 테스트와 저장·카드 작업은 진행한다.
- 배포 서버·공유 Supabase·S3 변경과 유료 모델 호출은 이번 검증에서 실행하거나 승인한 것으로 취급하지 않는다.
