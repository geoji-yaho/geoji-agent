# 판결·밈·공유 카드 5개 사례 검증 — 2026-09-19

**상태: 고정 모델 연결 점검 통과. 실제 모델 문구·강도·품질 평가는 API 키 미설정으로 미실행.**

고정 fixture 모델의 `AI_READY`는 테스트 프로토콜상의 성공이며, OpenAI/xAI 실제 생성 성공이 아니다. 유료 호출 0회, 비용 $0.

## 결과

- 현재 로컬 백엔드 소스로 JDK 25 `bootJar` 빌드 성공.
- 독립 Docker PostgreSQL + 실제 Spring API + AI API·worker + 로컬 JWT·이미지 서버로 실행.
- 승인 1건, 기각 1건, 유죄 3건(순한맛·매운맛·지옥맛), 배심원 투표 총 15표.
- 판결 저장·카드 API·밈 점수 기대값·선택 ID 유지·이미지 바이트 조회 등 71개 검사 통과.
- 실제 프론트의 ‘이미지 저장’ 버튼으로 PNG 5개 생성. 각 684×684, PNG CRC 검사 및 육안 확인 완료.
- 공개 카드와 판결 API의 밈 ID 일치 및 브라우저에서 해당 이미지 로딩 확인.

| 사례 | 합성 지출 | 선택 태그 | 연결 밈 | 실제 프론트 저장 결과 |
|---|---|---|---|---|
| 구매 승인 | 업무용 키보드 · 59,000원 | `APPROVED` | 인정하는 펭귄 | [PNG](fixture/01-approved-card.png) |
| 구매 기각 | 한정판 운동화 · 289,000원 | `REJECTED` | 박명수 그만해 | [PNG](fixture/02-rejected-card.png) |
| 유죄 · 순한맛 | 택시 · 12,000원 | `GUILTY_LIGHT` | 실망 | [PNG](fixture/03-mild-card.png) |
| 유죄 · 매운맛 | 치킨 배달 · 31,000원 | `GUILTY_LIGHT` | 실망 | [PNG](fixture/04-spicy-card.png) |
| 유죄 · 지옥맛 | 게임 스킨 묶음 · 99,000원 | `GUILTY_LIGHT` | 실망 | [PNG](fixture/05-hell-card.png) |

## 텍스트 결과의 한계

5개 모두 고정 모델이 같은 제목 **“지갑이 잠시 쉬어 갈 시간”**, 본문 **“다음 결제 전에는 한 번 더 생각해 봐요.”**를 반환한다. 이를 순한맛·매운맛·지옥맛 실제 생성 결과로 평가하면 안 된다.

승인·기각의 형량은 null, 유죄 3건은 고정 모델의 `oneDay`다. 말투 강도와 형량은 별개이며, `GUILTY_LIGHT`/`GUILTY_HEAVY`는 형량으로 교정된다. 이번 테스트의 세 유죄 사례는 모두 `GUILTY_LIGHT`다.

## 관찰 사항

- 현재 로컬 카탈로그 14장 중 `GUILTY_LIGHT`는 1장뿐이다. 최근 노출 감점이 있어도 같은 태그의 다른 후보가 없어 유죄 3건 모두 ‘실망’ 이미지가 선택됐다. 다른 태그 이미지로 대체하지 않는 현재 규칙대로다.
- 공유 PNG에는 제목·금액·형량·밈이 들어가고 판결문 본문은 포함되지 않는다. 현재 프론트 컴포넌트의 동작이다.
- 이번 실행은 로컬 카탈로그를 테스트 DB에 직접 시드했다. 관리자 업로드/S3/GitHub 등록 성공을 검증한 결과가 아니다.
- 인증은 임시 로컬 JWT, 프론트는 Vite 개발 모드다. 운영 Supabase 로그인·GitHub Pages·AWS 프록시·배포 성능은 검증하지 않았다.
- 지연 시간은 fake 모델 기준이며 실제 LLM 지연 시간 비교에 사용할 수 없다.

## 실행기 호환성 보정

일회성 실행기에만 현재 계약을 반영했다. 기존 서비스 소스는 수정하지 않았다.

- 공유 카드·판결 재조회에 `room_id` 전달.
- `쇼핑/패션` 카테고리 사용.
- 사용자당 방 최대 3개에 맞춰 강도별 방 재사용.
- 지옥맛을 중형 태그와 동일시하던 fixture 기대값 제거.

## 실제 모델 재실행

프로젝트 `.env`에 `OPENAI_API_KEY`와 `XAI_API_KEY`를 설정해야 한다. 키를 보고서나 채팅에 기록하지 않는다.

예약 예산 상한 $0.75, 최대 60회(재시도 포함). 합성 입력만 모델에 전송한다. 운영 DB와 S3는 사용하지 않는다.

아래 명령은 이번 작업에서 빌드해 둔 임시 복사본을 사용한다. 임시 폴더가 삭제되면 해당 커밋으로 다시 준비해야 한다.

```bash
cd /Users/hyun/dev/geoji
.venv/bin/python outputs/validation/20260919-five-cases/run_five_cases.py \
  --workspace /var/folders/kn/j8ddfvv96tv6fsjhn35lyfh80000gn/T/geoji-five-cases-20260919-j6w5ow0j \
  --output /Users/hyun/dev/geoji/outputs/validation/20260919-five-cases/live \
  --live
```

실행기는 브라우저 검증을 위해 스택을 남긴다. 출력된 `RUN_DIRECTORY`를 사용해 프론트를 연결하고 작업 후 정리한다.

```bash
.venv/bin/python scripts/start_local_e2e_web.py <RUN_DIRECTORY> \
  --frontend /var/folders/kn/j8ddfvv96tv6fsjhn35lyfh80000gn/T/geoji-five-cases-20260919-j6w5ow0j/web
.venv/bin/python scripts/stop_local_e2e.py <RUN_DIRECTORY>
```

## 근거 파일

- [브라우저 카드 비교](gallery.html)
- [API·DB 검사 원본](fixture/report.json)
- [브라우저 이미지 로드·PNG 검사](fixture/browser-checks.json)
- [일회성 실행기](run_five_cases.py)

`state.json`과 임시 인증 정보는 결과 폴더에 포함하지 않았다.

## 검증한 소스

- ai: `f8eb99d97ed7f9bf59f013dec57dc634eb7fb2ae`
- backend: `fa1825601a6f6ea5140ad6caf37b7a9db7cbba32`
- frontend: `64e963764f00a3fe920c0f771ceb3cd1da4d096f`

AI 저장소의 기존 밈 카탈로그 미커밋 변경을 포함해 검증했다. 해당 변경은 되돌리지 않았다.

이번 fixture 실행의 프론트·백엔드·AI 프로세스와 전용 DB는 검증 후 종료했다. PNG와 보고서는 유지한다.
