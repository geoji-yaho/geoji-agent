"""판결문 생성 → 짤 힌트 → 이미지 매칭 흐름(05 §3 writer, 10 §11).

FakeLLM 그래프 C 가 만든 finalize 초안을 그대로 선택기에 넣는다. 실제 b-meme 산출물은
분류(감정·키워드)만 쓰고 승인 상태는 테스트에서 가정한다. 네트워크·DB·키 없음.
"""

from __future__ import annotations

import json
from pathlib import Path

from geoji_ai.contracts.writer import MemeEmotion, MemeTag
from geoji_ai.domain.meme_selection import MemeImage, select_meme
from tests.unit.test_graph_c import make_snapshot, run
from tests.unit.test_meme_selection import taxi_draft

ROOT = Path(__file__).resolve().parents[2]
B_MEME = ROOT / "outputs" / "b-meme" / "skill-test-20260911"


def catalog_image(
    image_id: str, tag: MemeTag, emotions: tuple[str, ...], keywords: tuple[str, ...] = ()
) -> MemeImage:
    return MemeImage(
        image_id=image_id,
        tag=tag,
        strategies=frozenset(),
        emotions=frozenset(emotions),
        keywords=frozenset(keywords),
        is_active=True,
    )


#: 태그마다 힌트와 맞는 짤 1장과 안 맞는 짤 1장. fake 서기 힌트는 DISAPPROVAL·[택시, 늦잠, 알람].
CATALOG = [
    image
    for tag in MemeTag
    for image in (
        catalog_image(f"{tag}-match", tag, ("DISAPPROVAL",), ("택시",)),
        catalog_image(f"{tag}-other", tag, ("CELEBRATION",)),
    )
]


def select_for(req_draft, recent: tuple[str, ...] = ()):
    snapshot = make_snapshot()
    return select_meme(
        req_draft,
        post_id=snapshot.post_id,
        default_intensity=snapshot.jury.default_intensity,
        catalog=CATALOG,
        recent_image_ids=recent,
    )


def test_fake_판결에_짤_힌트가_함께_생성된다():
    draft = run().finalize().draft
    assert draft.meme_tag in MemeTag
    assert draft.meme_hints is not None
    assert draft.meme_hints.emotion in MemeEmotion
    assert draft.meme_hints.keywords


def test_유죄_판결은_같은_태그에서_감정·키워드가_맞는_이미지를_고른다():
    draft = run().finalize().draft
    selection = select_for(draft)
    by_id = {image.image_id: image for image in CATALOG}
    assert [s.image_id for s in selection.ranking] == [
        f"{draft.meme_tag}-match",
        f"{draft.meme_tag}-other",
    ]
    assert all(by_id[s.image_id].tag == draft.meme_tag for s in selection.ranking)
    assert selection.ranking[0].score == 2 + 1  # DISAPPROVAL +2, 택시 +1


def test_같은_판결을_다시_만들어도_같은_이미지다():
    first = select_for(run().finalize().draft)
    second = select_for(run().finalize().draft)
    assert first.image_id == second.image_id


def test_최근_노출이면_다음_후보로_바뀐다():
    draft = run().finalize().draft
    assert (
        select_for(draft, recent=(f"{draft.meme_tag}-match",)).image_id == f"{draft.meme_tag}-other"
    )


def test_부결_판결은_REJECTED_이미지를_고른다():
    snapshot = make_snapshot(result="disagree", vote_counts={"agree": 1, "disagree": 3})
    draft = run(snapshot=snapshot).finalize().draft
    assert draft.meme_tag == MemeTag.REJECTED
    assert select_for(draft).image_id == "REJECTED-match"


def load_b_meme(path: Path, *, assume_tag: MemeTag) -> MemeImage:
    """b-meme 사이드카의 분류를 쓰고 태그·활성은 가정값으로 채운다(승인이 아니다)."""
    data = json.loads(path.read_text("utf-8"))
    classification = data["classification"]
    return MemeImage(
        image_id=path.name.removesuffix(".metadata.json"),
        tag=assume_tag,
        strategies=frozenset(data["strategies"]),
        emotions=frozenset(classification["emotions"]),
        keywords=frozenset(classification["keywords"]),
        is_active=True,
    )


def test_실제_b_meme_분류로_돈없음_판결에_어울리는_짤_순위가_나온다():
    paths = sorted(B_MEME.glob("*.metadata.json"))
    assert len(paths) == 3
    catalog = [load_b_meme(p, assume_tag=MemeTag.GUILTY_LIGHT) for p in paths]
    # 손으로 쓴 힌트 — 돈을 다 써서 체념하는 판결.
    draft = taxi_draft(meme_hints={"emotion": "RESIGNATION", "keywords": ["돈없음", "지갑"]})
    selection = select_meme(
        draft, post_id="post-1", default_intensity=draft.texts[0].intensity, catalog=catalog
    )
    assert [(s.image_id, s.score) for s in selection.ranking] == [
        ("002-images-8", 3),  # RESIGNATION +2, 돈없음 +1
        ("003-images-9", 1),  # 지갑 +1
        ("001-images-3", 0),
    ]
