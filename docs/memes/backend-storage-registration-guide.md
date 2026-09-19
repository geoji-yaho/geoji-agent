# 밈 이미지 저장·등록 백엔드 가이드

## 1. 목적과 범위

이 문서는 `geoji-agent`가 생성·검수한 밈 이미지를 향후 S3와 백엔드 DB에 등록하기 위해,
현재 구현 상태와 백엔드에서 추가로 결정·구현해야 할 범위를 정리한다.

이 문서는 백엔드의 상세 설계나 구현 방법을 강제하지 않는다. 실제 API 형태, AWS 구성,
마이그레이션 분할과 배포 방식은 백엔드 개발자가 현재 운영 환경을 확인한 뒤 결정한다.

S3 연동 전까지 이미지와 메타데이터의 정본은 `geoji-agent/outputs/b-meme/` 아래에 둔다.
AI 워커나 b-meme 스킬이 업무 DB에 직접 쓰거나 S3에 직접 업로드하지 않는다.

## 2. 확인 기준

- AI 저장소: `geoji-agent`, 현재 작업 브랜치 기준
- 백엔드 저장소: `geoji-server`
- 확인한 백엔드 기준 커밋: `8b660a5655aa16541b7994cc4161a8e08ce5ae96`
- 확인일: 2026-09-15
- 관련 설계: `docs/plans/10-backend-contract.md` §2·§11,
  `docs/plans/archive/11-meme-hybrid-search-design.md` §3·§6·§8·§10

## 3. 현재 상태

### 3.1 geoji-agent

- b-meme 결과 이미지와 이미지별 메타데이터를 로컬 파일로 저장한다.
- 메타데이터에는 이미지 SHA-256, 감정, 키워드, 대상 종류, 컷별 표정·포즈·자막,
  판단 근거와 불확실성이 들어 있다.
- 감정 값은 백엔드 계약의 6종 enum과 일치한다.
- S3 업로드, CDN URL 발급, 업무 DB 등록은 하지 않는다.
- S3 연결 전까지 최종 자산 10장을 `outputs/b-meme/` 안에서 관리한다.
- 이전 생성분 중 확장자만 PNG였던 2장은 실제 PNG 형식으로 정규화했고 metadata의
  SHA-256도 함께 갱신했다.

### 3.2 geoji-server

확인한 백엔드 커밋에는 다음 구현이 있다.

- `MemeImage` JPA 엔티티
- `MemeImageRepository`
- `MemeScorer`와 점수 계산 테스트
- 테스트용 `src/test/resources/db/004_verdict_generation.sql`의 `meme_images` DDL 초안

현재 엔티티와 DDL 초안의 필드는 다음과 같다.

| 필드 | 의미 |
|---|---|
| `id` | 서버가 생성하는 UUID |
| `tag` | 판결 결과별 단일 태그 |
| `strategies` | 드립 전략 배열 |
| `emotions` | 감정 6종 배열 |
| `keywords` | 검색 키워드 배열 |
| `image_url` | 클라이언트가 조회할 이미지 URL |
| `is_active` | 선택 후보 포함 여부 |

다음 항목은 아직 확인되지 않았거나 구현되어 있지 않다.

- 테스트용 DDL이 실제 Supabase 운영 migration에 반영되었는지 여부
- 운영 DB에 `meme_images`가 실제 존재하는지 여부
- S3 SDK와 애플리케이션용 AWS 설정
- S3 버킷·객체 키·CDN URL 정책
- 업로드 또는 관리자 등록 API
- 이미지 해시·MIME·크기 검증 및 중복 방지
- S3 성공 후 DB 실패와 같은 부분 실패 재개 방식
- 로컬 메타데이터의 컷·자막·근거 등 확장 필드 보존 방식

GitHub Actions의 AWS 자격증명은 Elastic Beanstalk 배포용이다. 애플리케이션이 S3를 사용할 수
있다는 의미는 아니다.

## 4. 등록 대상 자산

배포 대상으로 확정한 이미지는 총 10장이다. 각 이미지 옆의 metadata JSON을 함께 보존한다.

| 이미지 | metadata | tag |
|---|---|---|
| `outputs/b-meme/run-1/001-images-1.png` | `outputs/b-meme/run-1/001-images-1.metadata.v2.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/run-1/002-images-2.png` | `outputs/b-meme/run-1/002-images-2.metadata.v2.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/skill-test-20260911/001-images-3.png` | `outputs/b-meme/skill-test-20260911/001-images-3.metadata.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/skill-test-20260911/002-images-8.png` | `outputs/b-meme/skill-test-20260911/002-images-8.metadata.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/skill-test-20260911/003-images-9.png` | `outputs/b-meme/skill-test-20260911/003-images-9.metadata.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/run-20260915-agent-team-project/001-aeng_doratna.png` | `outputs/b-meme/run-20260915-agent-team-project/001-aeng_doratna.metadata.json` | `REJECTED` |
| `outputs/b-meme/run-20260915-agent-team-project/002-disappointed.png` | `outputs/b-meme/run-20260915-agent-team-project/002-disappointed.metadata.json` | `GUILTY_LIGHT` |
| `outputs/b-meme/run-20260915-agent-team-project/003-empty_wallet.png` | `outputs/b-meme/run-20260915-agent-team-project/003-empty_wallet.metadata.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/run-20260915-agent-team-project/004-gokyungpyo_shock.png` | `outputs/b-meme/run-20260915-agent-team-project/004-gokyungpyo_shock.metadata.json` | `GUILTY_HEAVY` |
| `outputs/b-meme/run-20260915-agent-team-project/005-park_stop_it.png` | `outputs/b-meme/run-20260915-agent-team-project/005-park_stop_it.metadata.json` | `REJECTED` |

다음 파일은 등록 대상이 아니다.

- 사용자가 삭제한 `outputs/b-meme/run-1/003-images-3.png`와 해당 metadata
- 수정 전 중간본 `outputs/b-meme/run-20260915-agent-team-project/005-park_stop_it-v1.png`
- `skill-test-20260911/inputs/` 아래 원본 입력 이미지
- 실행 프롬프트와 로컬 절대 경로를 포함한 manifest

## 5. 백엔드에서 필요한 작업

### 5.1 운영 DB 확인과 migration

1. 운영 Supabase에서 `public.meme_images` 존재 여부와 실제 컬럼·제약을 확인한다.
2. 테이블이 없거나 테스트 DDL과 다르면 운영 migration을 작성한다.
3. 기존 운영 테이블과 충돌하지 않도록 현재 스키마에서 migration을 검증한다.
4. `tag`, 감정 6종, 배열 기본값, `image_url`, 활성 상태의 제약을 JPA 엔티티와 맞춘다.
5. 업로드 검증과 멱등 등록에 필요한 객체 키와 SHA-256을 DB에 보존할지 결정한다.

운영 DB 반영 전에는 테스트용 `004_verdict_generation.sql` 전체를 그대로 실행하지 않는다.
이 파일은 다른 업무 테이블까지 포함하므로 운영 스키마와의 차이를 먼저 확인해야 한다.

### 5.2 S3와 이미지 제공

1. 운영 리전, 버킷, 객체 키 규칙과 공개 범위를 결정한다.
2. 애플리케이션 실행 역할에는 필요한 버킷·prefix의 최소 권한만 부여한다.
3. 비밀키를 저장소나 DB에 넣지 않고 실행 역할 또는 배포 환경의 secret으로 제공한다.
4. 브라우저가 판결 카드 이미지를 읽을 수 있도록 CORS와 CDN 응답 헤더를 검증한다.
5. DB의 `image_url`에는 만료되는 업로드 URL이 아니라 지속 가능한 CDN URL을 저장한다.
6. 승인된 같은 버전의 객체를 덮어쓰지 않는 키 정책을 사용한다.

현재 카탈로그의 객체 키 제안은 `memes/seed/{asset_key}/v1/image.png`이다. 백엔드가 다른 규칙을
선택하면 등록 도구가 같은 규칙을 사용할 수 있도록 문서화한다.

### 5.3 등록 경계

등록 기능은 최소한 다음을 보장해야 한다.

- 관리자 또는 신뢰된 배포 주체만 등록 가능
- 저장소 상대 경로를 서버 파일 경로로 신뢰하지 않음
- 실제 파일의 SHA-256, MIME, 크기를 서버 또는 신뢰된 등록 단계에서 재검증
- 같은 요청이나 같은 자산 재시도 시 중복 행·중복 객체가 생기지 않음
- S3 업로드 성공과 DB 등록 성공을 별도 상태로 취급
- DB 실패 시 이미지를 다시 생성하거나 다른 객체 키로 무한 업로드하지 않음
- 등록 직후에는 비활성으로 두고 검수 후 활성화 가능
- 삭제·비활성화된 이미지는 신규 판결 후보에서 즉시 제외

구체적인 관리자 API, presigned URL 사용 여부와 상태 모델은 백엔드 개발자가 배포 환경과 운영
범위를 확인한 뒤 설계한다.

### 5.4 메타데이터 매핑

현재 DB에 바로 매핑되는 값은 다음과 같다.

| 로컬 카탈로그/metadata | 백엔드 |
|---|---|
| `tag` | `meme_images.tag` |
| `strategies` | `meme_images.strategies` |
| `classification.emotions` | `meme_images.emotions` |
| `classification.keywords` | `meme_images.keywords` |
| 업로드 후 CDN URL | `meme_images.image_url` |
| `is_active` | `meme_images.is_active` |

`asset_sha256`, `subject`, `panels`, `expense_categories`, `evidence`, `uncertainties`는 현재
엔티티에 대응 컬럼이 없다. 백엔드가 검색·감사에 필요한 범위를 결정하되, 사용하지 않는 필드를
억지로 업무 테이블에 추가하지 않는다. 원본 metadata JSON은 S3 또는 Git 이력에 보존할 수 있다.

## 6. geoji-agent가 제공하는 등록 파일

AI 저장소에는 다음 두 파일을 둔다.

| 파일 | 용도 |
|---|---|
| `outputs/b-meme/catalog-v1.json` | 사람이 관리하는 10개 자산 목록, 승인 tag, 권장 S3 key |
| `outputs/b-meme/backend-registration-v1.json` | 실제 파일과 metadata를 검증해 생성한 백엔드 전달용 payload |

두 파일 모두 네트워크·DB·AWS 상태를 변경하지 않는 로컬 산출물이다. 백엔드 전달용 payload는
다음 값을 제공한다.

- 저장소 상대 이미지 경로와 metadata 경로
- 실제 이미지 SHA-256, 바이트 크기, MIME, 픽셀 크기
- 승인된 단일 `tag`
- `strategies`, `emotions`, `keywords`
- `image_url: null`, `is_active: false`
- 권장 S3 객체 키

카탈로그 생성·검증 도구는 파일 누락, metadata 누락, 해시 불일치, 중복 해시, 중간본 포함,
개별 metadata와 카탈로그의 tag 불일치, 허용되지 않은 tag·감정 값을 실패로 처리한다.
업로드나 DB 쓰기는 수행하지 않는다.

저장소 루트에서 다음 명령으로 검증과 payload 재생성을 함께 수행한다.

```bash
.venv/bin/python scripts/build_meme_release.py \
  --catalog outputs/b-meme/catalog-v1.json \
  --output outputs/b-meme/backend-registration-v1.json
```

성공 시 `밈 등록 카탈로그 검증 완료: 10장`이 출력된다. 백엔드는
`backend-registration-v1.json`의 각 항목을 입력으로 사용하되, 업로드 직전에 SHA-256과 실제
MIME을 다시 검증하고 S3 업로드 후 발급된 지속 URL로 `image_url`을 채운다. 저장소에 있는
`image_url: null`, `is_active: false`는 아직 외부 저장·게시되지 않았다는 의미이므로 그대로
활성 데이터로 등록하지 않는다.

## 7. 실패·보안 기준

- 이미지와 metadata는 외부 입력으로 보고 파일명이나 JSON만 신뢰하지 않는다.
- S3 업로드 URL, AWS 키, DB 접속 문자열을 로그·manifest·Git에 남기지 않는다.
- 실제 MIME과 확장자를 대조하고 허용한 이미지 형식과 크기만 받는다.
- 같은 SHA-256이 이미 등록된 경우 기존 자산을 반환할지 새 버전을 만들지 정책으로 결정한다.
- S3 타임아웃 후에는 객체 존재와 해시부터 확인한 뒤 재시도한다.
- 비활성·미승인 자산은 검색 및 판결 선택 후보에서 제외한다.
- 브라우저 캔버스가 이미지를 사용할 경우 실제 배포 origin에서 CORS를 검증한다.

## 8. 백엔드 검증 항목

- 운영 migration을 빈 DB와 현재 스키마에서 각각 검증
- JPA `ddl-auto=validate` 기동 성공
- 허용되지 않은 tag·감정과 빈 `image_url` 거부
- 비활성 이미지가 `MemeImageRepository` 후보에서 제외됨
- 중복 등록 요청이 행이나 객체를 늘리지 않음
- 업로드 성공 후 DB 실패를 같은 요청으로 재개 가능
- DB 성공 응답 유실 후 재시도해도 같은 자산 반환
- 잘못된 해시·MIME·크기 등록 거부
- 권한 없는 등록·활성화·삭제 요청 거부
- S3/CDN 이미지가 실제 프론트 origin에서 CORS 오류 없이 조회됨
- 후보 없음과 이미지 서비스 장애 시 기본 이미지 폴백

## 9. 완료 조건

다음 조건이 모두 충족되면 백엔드 저장·등록 준비가 끝난 것으로 본다.

1. 운영 DB에 검증된 `meme_images` migration이 적용되어 있다.
2. 백엔드가 사용할 S3 버킷·prefix·권한·CDN URL 규칙이 문서화되어 있다.
3. 같은 자산을 안전하게 재시도할 수 있는 등록 경계가 구현되어 있다.
4. 10개 카탈로그 항목의 이미지 SHA-256과 S3 객체가 일치한다.
5. DB 행의 tag·감정·키워드·URL이 카탈로그와 일치한다.
6. 등록 직후 비활성 상태와 검수 후 활성화 전환이 검증된다.
7. 최종 이미지가 판결 카드에서 CORS 오류 없이 표시된다.

## 10. 이번 AI 저장소 작업의 경계

이번 작업에서는 다음만 수행한다.

- 기존 5장과 2026-09-15 생성 5장을 `outputs/b-meme/`에서 보존
- 승인된 tag를 이미지 metadata와 통합 카탈로그에 기록
- 로컬 검증과 업로드 패키지 준비 도구 제공
- 백엔드 현황과 필요 작업을 이 문서로 전달

이번 작업에서는 백엔드 코드, Supabase, AWS, S3, CDN, 배포 설정을 변경하지 않는다.
