# CLAUDE.md

떼거지(친구들이 내 지출을 재판하는 소비 절제 커뮤니티)의 AI 파트. 지출이 등록되면 심문관·조서·양형관·서기·검수관 다섯 역할이 판결문을 만들고, 그 역할을 이 저장소가 구현한다. GitHub 이름은 `geoji-agent`, 로컬 디렉터리는 `geojibang`. 원티드 AI Championship 2026 출품작이고 제출 마감은 2026-09-20, 심사는 9/21부터 10/5까지다. 백엔드 `geoji-server`와 프론트 `geoji-web`는 별도 저장소이다. 개발 언어는 Python이고 산출물은 Docker 이미지다. 이미지를 백엔드 담당자에게 넘기면 그쪽 AWS 계정의 EC2에 올린다. 우리는 이미지와 compose 조각까지만 책임진다.

## 규칙

- 문서와 주석, 커밋 메시지, 응답은 한국어. 코드 식별자는 영어
- 정본은 `docs/plans/`다. 문서는 `10 §15.2`, `01 CT-07`처럼 번호와 절로 부른다. 골격과 결정 기록 절차는 `.claude/rules/plans-format.md`
- Git: `.claude/rules/git-workflow.md`. `type(scope): 한국어 제목`, 계획서 갱신은 `docs(plans)`. `geoji-server/`, `geoji-web/`, `.env`는 `git add` 하지 않는다
- 용어: `.claude/rules/domain-vocabulary.md`. enum은 프론트 값이 표준이다(D-21)
- 코드: `.claude/rules/code-layout.md`. uv와 Python 3.12, `api/workers → application → domain/ports`
- 테스트와 모델 호출: `.claude/rules/testing.md`. 테스트는 fake provider만, 프롬프트 변경은 골든셋 회귀와 사람 검수
- 계획서에 없는 값이 필요하면 만들지 말고 묻는다. 실측 뒤 채울 빈칸은 임의 값으로 채우지 않는다

## 스킬셋

에이전트가 읽는 룰·스킬·서브에이전트·슬래시 커맨드·훅은 `.claude/` 에 있다. 목록과 추가하는 법은 `.claude/README.md`.

<!-- TODO 하네스 스킬이 생기면 여기서 "무엇을 만들 때 어느 스킬로 진행하는지" 한 줄 -->

## 기준 문서

<!-- TODO 정본이 어디인지, 문서끼리 어긋나면 어느 것이 이기는지 -->

## 자주 틀리는 것

<!-- TODO 실제로 틀렸던 것만 적는다 -->
