# 프론트 반영 요청 — 새 판결 흐름·저장 PNG·관리자 b-meme 파일 등록

작성일: 2026-09-16. **geoji-web에는 커밋·푸시하지 않았다.**
아래는 에이전트 저장소에 남기는 담당자 검토용 요청사항이다.
임시 checkout에서 실제 API/브라우저 검증한 참고 패치는
[frontend.patch](../evidence/20260916/frontend.patch)에 있다.
기준 commit은 `f7fd4f443999fff83459b21262222f50564d776d`이며 담당자가 필요한 변경만 반영한다.
이 문서는 프론트 요청을 다룬다. 백엔드 API 계약은 [10](../10-backend-contract.md)이 정본이다.

## 1. 반영 우선순위

| 우선순위 | 요청 | 관련 파일/계약 |
| --- | --- | --- |
| 1 | 홈/방의 지출 등록을 신규 `post-submissions`로 연결. `NEEDS_INPUT`·`BLOCKED`·`PROCEED/REVISE` 처리, 같은 payload 재전송은 같은 멱등키 사용 | `shared/api/posts.ts`, `features/post/pages/ExpenseCreatePage.tsx`, 10 §9 |
| 1 | 방 피드·상세·투표를 신규 posts API에 연결하고 서버의 `canVote/myVote`를 사용 | `features/post/`, 10 §16.4 |
| 1 | 공개 응답의 camelCase·nullable을 반영. 무죄/동의의 형량·짤 null을 정상 표시 | `contracts/verdict-view-v1.schema.json`, 10 §9 |
| 1 | 완료한 판결도5초마다 재조회해 원천삭제/공유철회 반영. 같은 textVersion이라도 `view.source=TEMPLATE` 변경을 반영하고 조회 오류에는 과거 문구 숨김 | `features/post/`, 10 §8·§9 |
| 1 | 서버가 저장한 PNG를 인증된 blob 요청으로 미리보기/다운로드. READY 뒤에도 작업 상태 재확인 | `PostCardPage.tsx`, `useDownloadCard.ts`, 10 §16.4 |
| 2 | 관리자에게 로컬 b-meme 완성 파일 선택→등록→미리보기→검수 승인→활성화를 제공 | `features/images/`, **10 §16.5** |
| 2 | 개인 이미지는 비공개 업로드·검수 흐름으로 분리 | `features/images/`, 10 §16.4 |

## 2. 키 없는 관리자 등록 화면

- 파일 선택기는 **이미 변환된 PNG/JPEG**를 받는다. OpenAI/xAI 키 입력란은 필요 없다.
- 기존 로그인 JWT로 `POST /api/admin/memes` multipart를 전송한다. `file`, `tag` 필수,
  `strategies`, `emotions`, `keywords` 선택. 태그/검색정보는 검증된 카탈로그 자료를 사용한다.
- 업로드 직후 “검수 대기·선택 비활성”을 표시한다. 승인과 활성화를 별도 동작으로 둔다.
- 이 경로의 `selectedVersion=ORIGINAL`은 업로드한 완성 b-meme 파일이다.
  추가 흑백/생성형 변환을 요구하지 않는다. AI API readiness와 파일 등록 화면을 연결하지 않는다.
- 인증된 원본·검수 이미지는 백엔드 파일 endpoint에서 blob으로 읽는다.
  Authorization을 임의의 외부 이미지 URL로 보내지 않는다.
- 업로드 실패 이유와 재시도 상태를 표시하고, 새 파일을 고르면 이전 파일의 오류를 지운다.
  업로드 중에는 파일 교체와 중복 제출을 막는다.

## 3. 카드·판결 회귀 주의점

1. 구형 `expenses/trial`은 StubAiClient 경로이므로 새 posts의 생성 성공으로 취급하지 않는다.
   참고 패치는 기존 화면을 `/expenses/:id`로 분리했다. 이미 외부 공유된 구형
   `/posts/{expenseId}` 링크의 이전 정책은 담당자가 결정해야 한다.
2. 다운로드는 매번 서버의 `/share-card/download`를 다시 호출한다. 공유 철회 전에 가져온
   브라우저 캐시로 다운로드를 허용하지 않는다. 완료한 render도5초마다 재확인한다.
3. 짤 후보 없음은 정상이다. 다른 평결 태그의 이미지를 기본값으로 대신 쓰지 않는다.
4. 확정된 판결에는 투표 잔여시간을 표시하지 않는다. API `message`는 null일 수 있다.

## 4. 검증 및 남은 범위

- 참고 패치에서 `pnpm check`(타입·빌드·ESLint·Prettier), `pnpm harness:check` 통과.
- 실제 브라우저 입력→게시물 저장→테스트 배심원3표 API→판결·짤→서버PNG→Chrome 다운로드 검증.
  판결문 모델은 무료 fixture를 사용했다. 실제 LLM 품질 검증은 아니다.
- 비공개 업로드/흑백 변환/검수, 관리자 미리보기·활성 토글, 공유 철회 후 카드 숨김을 확인했다.
- 새 패키지·운영 인증 코드 변경은 없다. 초기 제출 초안은 컴포넌트 메모리에만 있으므로
  새로고침 복구, 개인 이미지 목록, 구형 공유 링크 이전은 별도 후속 범위다.
- 최종 파일 해시·브라우저 다운로드의 검증 한계와 재실행 방법은
  [15 실행 보고](../15-local-e2e-implementation.md) 및 [검증 요약](../evidence/20260916/summary.json)에 있다.

백엔드 담당자가 API·DDL을 반영한 뒤 같은 시나리오를 다시 검증한다. 에이전트 저장소의
커밋·푸시가 프론트 또는 백엔드의 반영·배포를 의미하지 않는다.
