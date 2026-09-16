# B급 밈 자산 위치 색인

`outputs/b-meme/`의 로컬 파일 위치를 찾기 위한 색인이다. 기존 파일은 이동하지 않는다.

## 기존 백엔드 전달 목록 · 10장

[backend-registration-v1.json](backend-registration-v1.json)은 [catalog-v1.json](catalog-v1.json)의 10장을 검증해 만든 **전달용 payload**다. `suggested_object_key`는 제안 경로이며 실제 S3 업로드·DB 등록 완료 증거가 아니다. 원격 상태는 백엔드 DB와 S3에서 별도로 확인해야 한다.

| asset_key | 최종 PNG | 메타데이터 | 제안 S3 key |
|---|---|---|---|
| `run1-images-1` | [이미지](../../outputs/b-meme/run-1/001-images-1.png) | [JSON](../../outputs/b-meme/run-1/001-images-1.metadata.v2.json) | `memes/seed/run1-images-1/v1/image.png` |
| `run1-images-2` | [이미지](../../outputs/b-meme/run-1/002-images-2.png) | [JSON](../../outputs/b-meme/run-1/002-images-2.metadata.v2.json) | `memes/seed/run1-images-2/v1/image.png` |
| `skill-test-images-3` | [이미지](../../outputs/b-meme/skill-test-20260911/001-images-3.png) | [JSON](../../outputs/b-meme/skill-test-20260911/001-images-3.metadata.json) | `memes/seed/skill-test-images-3/v1/image.png` |
| `skill-test-images-8` | [이미지](../../outputs/b-meme/skill-test-20260911/002-images-8.png) | [JSON](../../outputs/b-meme/skill-test-20260911/002-images-8.metadata.json) | `memes/seed/skill-test-images-8/v1/image.png` |
| `skill-test-images-9` | [이미지](../../outputs/b-meme/skill-test-20260911/003-images-9.png) | [JSON](../../outputs/b-meme/skill-test-20260911/003-images-9.metadata.json) | `memes/seed/skill-test-images-9/v1/image.png` |
| `aeng-doratna` | [이미지](../../outputs/b-meme/run-20260915-agent-team-project/001-aeng_doratna.png) | [JSON](../../outputs/b-meme/run-20260915-agent-team-project/001-aeng_doratna.metadata.json) | `memes/seed/aeng-doratna/v1/image.png` |
| `disappointed` | [이미지](../../outputs/b-meme/run-20260915-agent-team-project/002-disappointed.png) | [JSON](../../outputs/b-meme/run-20260915-agent-team-project/002-disappointed.metadata.json) | `memes/seed/disappointed/v1/image.png` |
| `empty-wallet` | [이미지](../../outputs/b-meme/run-20260915-agent-team-project/003-empty_wallet.png) | [JSON](../../outputs/b-meme/run-20260915-agent-team-project/003-empty_wallet.metadata.json) | `memes/seed/empty-wallet/v1/image.png` |
| `gokyungpyo-shock` | [이미지](../../outputs/b-meme/run-20260915-agent-team-project/004-gokyungpyo_shock.png) | [JSON](../../outputs/b-meme/run-20260915-agent-team-project/004-gokyungpyo_shock.metadata.json) | `memes/seed/gokyungpyo-shock/v1/image.png` |
| `park-stop-it` | [이미지](../../outputs/b-meme/run-20260915-agent-team-project/005-park_stop_it.png) | [JSON](../../outputs/b-meme/run-20260915-agent-team-project/005-park_stop_it.metadata.json) | `memes/seed/park-stop-it/v1/image.png` |

## 신규 변환 결과 · 17장 (기존 전달 목록에 없음)

정본은 [manifest.json](run-20260916-new-inputs/manifest.json)과 [annotations.json](run-20260916-new-inputs/annotations.json)이다. 아래 PNG는 각 항목의 **최종본**이며 이미지 옆 JSON은 SHA-256과 감정·키워드를 담는다. `검토 필요`는 생성 실패가 아니라 분류 불확실성이다. 이 17장의 `tag=null`, `is_active=false` 상태를 임의로 승인·활성화하지 않는다.

| 번호 | 입력 원본 | 최종 PNG | 메타데이터 | 분류 |
|---:|---|---|---|---|
| 001 | [92_55169b475d46c_2834.jpg](<../../inputs/92_55169b475d46c_2834.jpg>) | [001-92_55169b475d46c_2834.png](run-20260916-new-inputs/001-92_55169b475d46c_2834.png) | [JSON](run-20260916-new-inputs/001-92_55169b475d46c_2834.metadata.json) | 검토 필요 |
| 002 | [Mnet_그의-손에-쥐어지는-합격-목걸이_고전-밈.webp](<../../inputs/Mnet_그의-손에-쥐어지는-합격-목걸이_고전-밈.webp>) | [002-Mnet-necklace.png](run-20260916-new-inputs/002-Mnet-necklace.png) | [JSON](run-20260916-new-inputs/002-Mnet-necklace.metadata.json) | 완료 |
| 003 | [images (1).jpeg](<../../inputs/images (1).jpeg>) | [003-images-1.png](run-20260916-new-inputs/003-images-1.png) | [JSON](run-20260916-new-inputs/003-images-1.metadata.json) | 완료 |
| 004 | [images (10).jpeg](<../../inputs/images (10).jpeg>) | [004-images-10.png](run-20260916-new-inputs/004-images-10.png) | [JSON](run-20260916-new-inputs/004-images-10.metadata.json) | 완료 |
| 005 | [images (2).jpeg](<../../inputs/images (2).jpeg>) | [005-images-2.jpeg.png](run-20260916-new-inputs/005-images-2.jpeg.png) | [JSON](run-20260916-new-inputs/005-images-2.jpeg.metadata.json) | 완료 |
| 006 | [images (2).png](<../../inputs/images (2).png>) | [006-images-2-png.png](run-20260916-new-inputs/006-images-2-png.png) | [JSON](run-20260916-new-inputs/006-images-2-png.metadata.json) | 검토 필요 |
| 007 | [images (3).jpeg](<../../inputs/images (3).jpeg>) | [007-images-3.png](run-20260916-new-inputs/007-images-3.png) | [JSON](run-20260916-new-inputs/007-images-3.metadata.json) | 검토 필요 |
| 008 | [images (4).jpeg](<../../inputs/images (4).jpeg>) | [008-images-4.png](run-20260916-new-inputs/008-images-4.png) | [JSON](run-20260916-new-inputs/008-images-4.metadata.json) | 완료 |
| 009 | [images (5).jpeg](<../../inputs/images (5).jpeg>) | [009-images-5.png](run-20260916-new-inputs/009-images-5.png) | [JSON](run-20260916-new-inputs/009-images-5.metadata.json) | 완료 |
| 010 | [images (6).jpeg](<../../inputs/images (6).jpeg>) | [010-images-6.png](run-20260916-new-inputs/010-images-6.png) | [JSON](run-20260916-new-inputs/010-images-6.metadata.json) | 완료 |
| 011 | [images (7).jpeg](<../../inputs/images (7).jpeg>) | [011-images-7.png](run-20260916-new-inputs/011-images-7.png) | [JSON](run-20260916-new-inputs/011-images-7.metadata.json) | 완료 |
| 012 | [images (8).jpeg](<../../inputs/images (8).jpeg>) | [012-images-8.png](run-20260916-new-inputs/012-images-8.png) | [JSON](run-20260916-new-inputs/012-images-8.metadata.json) | 완료 |
| 013 | [images (9).jpeg](<../../inputs/images (9).jpeg>) | [013-images-9.png](run-20260916-new-inputs/013-images-9.png) | [JSON](run-20260916-new-inputs/013-images-9.metadata.json) | 완료 |
| 014 | [images.jpeg](<../../inputs/images.jpeg>) | [014-images-jpeg.png](run-20260916-new-inputs/014-images-jpeg.png) | [JSON](run-20260916-new-inputs/014-images-jpeg.metadata.json) | 검토 필요 |
| 015 | [jjalbox_20241231_6773f2c89ff40.jpg](<../../inputs/jjalbox_20241231_6773f2c89ff40.jpg>) | [015-jjalbox-corrected.png](run-20260916-new-inputs/015-jjalbox-corrected.png) | [JSON](run-20260916-new-inputs/015-jjalbox-corrected.metadata.json) | 완료 |
| 016 | [images (1).png](<../../inputs/images (1).png>) | [016-images-1-png.png](run-20260916-new-inputs/016-images-1-png.png) | [JSON](run-20260916-new-inputs/016-images-1-png.metadata.json) | 검토 필요 |
| 017 | [semojjal_20260913_7061c9684148a0.jpg](<../../inputs/semojjal_20260913_7061c9684148a0.jpg>) | [017-semojjal.png](run-20260916-new-inputs/017-semojjal.png) | [JSON](run-20260916-new-inputs/017-semojjal.metadata.json) | 검토 필요 |

`015-jjalbox.png`는 제작자 표기가 빠졌던 최초 생성본으로 보존했다. 등록 대상 최종본은 `015-jjalbox-corrected.png`다.

## 등록 전 확인

1. 분류 `검토 필요` 6장을 사람이 확인하고, 각 이미지의 판결 `tag` 및 필요한 `strategies`를 승인한다.
2. 기존 10장과 신규 17장을 혼동하지 않도록 등록 목록을 갱신한다. 파일 이동 시 manifest·metadata 경로 및 SHA-256 연결도 함께 검증한다.
3. 백엔드 관리자 API·S3 업로드가 실제 적용된 환경에서 파일 해시와 객체 key를 확인한 뒤, S3 저장과 DB 등록 상태를 각각 확인한다. Git에 저장된 전달용 JSON만으로 운영 등록을 완료 처리하지 않는다.
