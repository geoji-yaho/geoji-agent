# 짤 로컬 메타데이터 v2 구현 계획

> 근거: 11 §4·§10.1. 사용자 요청에 따라 설계를 검토한 뒤 첫 구현 단위를 진행한다. 커밋은 사용자가 검증 후 수행한다.

## 1. 검토 결론과 범위

11의 책임 분리, 승인 필터, RRF 후보 결합과 장애 폴백 방향은 타당하다. 다만 백엔드 저장·공개 계약과 실제 벡터 검색은 별도 구현 단위다. 이번에는 기존 독립 스크립트의 로컬 산출물을 확장한다. 운영 검색 활성화나 최적 모델 확정을 완료했다고 간주하지 않는다.

- `skills/b-meme/scripts/save_metadata.py`: v1 입력·출력 호환, v2 검증·저장, 검색 문서와 해시 생성.
- `tests/test_b_meme_metadata.py`: v1 회귀 및 v2 정상·경계·실패·재실행 검사.
- `skills/b-meme/SKILL.md`, `references/classification.md`: 새 작성 형식과 변환 절차.
- `docs/plans/13-meme-reranking-evaluation.md`: 모델 비교 실험 방법과 채택 기준.

백엔드의 asset_id·asset_version·owner_id, 저장 상태와 임베딩 모델·벡터는 발급하거나 추측하지 않는다. 실제 이미지 형식·크기 검증은 등록 단계 책임으로 남기며 이번 로컬 파일 해시는 등록 가능성이나 이미지 유효성 인증을 뜻하지 않는다. 외부 API 호출·라이브러리 추가·DB/public API 변경은 없다.

## 2. 로컬 계약

1. `schema_version` 없는 기존 annotations는 v1으로 읽는다. 기존 `*.metadata.json` 동작을 유지한다.
2. v2 annotations는 기존 분류 필드에 `schema_version: 2`, `description`, `usage_context`, `tags`를 추가한다. version의 bool·미지원 값·누락 필드는 거부한다.
3. description은 관찰 사실, usage_context는 사용 해석이다. 각각 trim/NFC 후 비어 있지 않은 최대 1,000자. tags는 trim/NFC 및 중복 제거 후 최대 20개, 항목당 40자. 단어 내부 공백은 보존한다. 기존 keywords 최대 5개는 보조 필드로 유지한다.
4. v2는 `*.metadata.v2.json`에 저장한다. v1 sidecar와 이미지·입력 annotations를 변경하지 않는다. manifest에는 별도 v2 참조를 추가하여 기존 v1 분류와 경로를 유지한다. v1→v2 변환은 기존 classification에 사람이 실제 이미지를 보고 작성한 새 필드를 더한 annotations를 같은 명령으로 저장하는 명시적 절차다. 없는 장면 설명을 자동 추측하지 않는다. 기존 v1 이미지 해시가 현재 파일과 다르면 변환을 거부하고 uncertainties·검수 필요 상태를 보존한다.
5. v2 sidecar에 description/usage_context/tags, 기존 classification, schema_version=2, metadata_version, 검색 텍스트·SHA-256·텍스트 형식 버전을 기록한다. 검색 텍스트는 필드 라벨과 함께 description → usage_context → 컷 순서 captions → tags를 결합한다. uncertainties/evidence/prompt/제외 조건은 포함하지 않는다.
6. 동일 이미지·정규화 메타데이터 재실행은 metadata_version을 유지한다. 변경 시 증가하고 검색 텍스트와 해시는 다시 계산한다. 모델·embedding_version은 계산 전 임의 설정하지 않는다. 기존 v2가 깨졌으면 덮어쓰지 않고 실패한다.
7. 검수 상태 pending, 공개 승인 false, 허용 판결 태그 빈 배열, is_active=false를 강제한다. needs_review는 기존 의미(빈 감정·불확실성·needs_review 생성 결과)를 유지하며 false도 운영 승인이 아니다.
8. 모든 입력 검증 후 파일별 원자 교체. 같은 manifest 동시 작성은 지원하지 않으며 I/O 중단 후 같은 명령으로 재실행한다. sidecar가 먼저 저장되고 manifest가 실패해도 버전이 재증가하지 않아야 한다.

## 3. 조사 → 수정 → 검증 → 정리

- 조사: 기존 계약·테스트와 독립 명세 검토, 새 필드·보존 경계 확정.
- 수정: v2 정상 입력 저장 테스트를 먼저 추가해 실패 확인 후 구현. 정규화·길이·타입·버전·원본 보존·부분 I/O 실패 복구 검사를 추가한다.
- 검증: `uv run pytest tests/test_b_meme_metadata.py -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q`. 변경과 무관한 기존 오류는 분리 보고한다.
- 정리: 독립 코드 검토·테스트 검증 결과를 반영하고 실행 예제, 미구현 백엔드 경계와 평가 계획을 전달한다.
