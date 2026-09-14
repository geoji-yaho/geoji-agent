"""짤 점수 선택 규칙(10 §11, 08 §3.4). 모델·네트워크·DB 없음."""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from zlib import crc32

import pytest

from geoji_ai.contracts.writer import MemeTag, WriterDraft
from geoji_ai.domain.intensity import Intensity
from geoji_ai.domain.meme_selection import MemeImage, select_meme

ROOT = Path(__file__).resolve().parents[2]
POST_ID = "post-taxi-20260907-0852"


def taxi_draft(**update: object) -> WriterDraft:
    """meme_tag GUILTY_LIGHT, hints DISAPPROVAL·[택시, 늦잠, 알람].

    강도별 전략: mild CHEAPER_ALTERNATIVE · spicy REPEAT_OFFENSE · hell CHEAPER_ALTERNATIVE.
    """
    data = json.loads((ROOT / "contracts/fixtures/writer-draft-taxi.json").read_text("utf-8"))
    data.update(update)
    return WriterDraft.model_validate(data)


def image(
    image_id: str,
    *,
    tag: MemeTag = MemeTag.GUILTY_LIGHT,
    strategies: tuple[str, ...] = (),
    emotions: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    is_active: bool = True,
) -> MemeImage:
    return MemeImage(
        image_id=image_id,
        tag=tag,
        strategies=frozenset(strategies),
        emotions=frozenset(emotions),
        keywords=frozenset(keywords),
        is_active=is_active,
    )


def select(catalog: list[MemeImage], **kwargs: object):
    options: dict = {"post_id": POST_ID, "default_intensity": Intensity.spicy, **kwargs}
    draft = options.pop("draft", None) or taxi_draft()
    return select_meme(draft, catalog=catalog, **options)


def test_태그가_다르거나_비활성이면_후보에서_빠진다():
    result = select(
        [
            image("heavy", tag=MemeTag.GUILTY_HEAVY, emotions=("DISAPPROVAL",)),
            image("inactive", is_active=False, emotions=("DISAPPROVAL",)),
            image("light"),
        ]
    )
    assert [s.image_id for s in result.ranking] == ["light"]
    assert result.image_id == "light"


def test_점수는_전략3_감정2_키워드마다1():
    full = image(
        "full",
        strategies=("REPEAT_OFFENSE",),
        emotions=("DISAPPROVAL",),
        keywords=("택시", "늦잠", "지각"),
    )
    [score] = select([full]).ranking
    assert (score.strategy_match, score.emotion_match, score.keyword_overlap) == (True, True, 2)
    assert score.score == 3 + 2 + 2


def test_전략은_default_intensity_판결문에서_가져온다():
    repeat = image("repeat", strategies=("REPEAT_OFFENSE",))
    cheaper = image("cheaper", strategies=("CHEAPER_ALTERNATIVE",))

    spicy = {s.image_id: s.score for s in select([repeat, cheaper]).ranking}
    assert spicy == {"repeat": 3, "cheaper": 0}

    mild = select([repeat, cheaper], default_intensity=Intensity.mild)
    assert {s.image_id: s.score for s in mild.ranking} == {"repeat": 0, "cheaper": 3}


def test_default_intensity_판결문이_없으면_전략_점수가_없다():
    draft = taxi_draft()
    draft = draft.model_copy(update={"texts": [t for t in draft.texts if t.intensity != "spicy"]})
    [score] = select([image("repeat", strategies=("REPEAT_OFFENSE",))], draft=draft).ranking
    assert score.strategy_match is False
    assert score.score == 0


def test_키워드는_NFC와_앞뒤_공백을_정규화해_비교한다():
    decomposed = unicodedata.normalize("NFD", "택시")
    assert decomposed != "택시"
    draft = taxi_draft(meme_hints={"emotion": "DISAPPROVAL", "keywords": [f"  {decomposed} "]})
    [score] = select([image("taxi", keywords=("택시",))], draft=draft).ranking
    assert score.keyword_overlap == 1


def test_meme_hints가_없으면_감정·키워드_점수가_없다():
    draft = taxi_draft(meme_hints=None)
    catalog = [image("x", emotions=("DISAPPROVAL",), keywords=("택시",))]
    [score] = select(catalog, draft=draft).ranking
    assert (score.emotion_match, score.keyword_overlap, score.score) == (False, 0, 0)


def test_최근_노출_앞_5장만_감점하고_1위가_바뀐다():
    catalog = [
        image("best", emotions=("DISAPPROVAL",), keywords=("택시",)),  # 3점
        image("second", emotions=("DISAPPROVAL",)),  # 2점
    ]
    assert select(catalog).image_id == "best"

    shown = select(catalog, recent_image_ids=["best"])
    assert {s.image_id: s.score for s in shown.ranking} == {"best": -2, "second": 2}
    assert shown.image_id == "second"

    sixth = ["r1", "r2", "r3", "r4", "r5", "best"]
    assert select(catalog, recent_image_ids=sixth).image_id == "best"


@pytest.mark.parametrize("post_id", [POST_ID, "post-1", "9f0c4c21-0000-4000-8000-000000000001"])
def test_동점은_crc32_오름차순이고_목록_순서와_무관하다(post_id: str):
    ids = ["meme-a", "meme-b", "meme-c", "meme-d"]
    expected = min(ids, key=lambda i: crc32(f"{post_id}{i}".encode()))
    catalog = [image(i) for i in ids]
    assert select(catalog, post_id=post_id).image_id == expected
    assert select(list(reversed(catalog)), post_id=post_id).image_id == expected


def test_후보가_없으면_기본_이미지로_가도록_None():
    result = select([image("heavy", tag=MemeTag.GUILTY_HEAVY)])
    assert result.image_id is None
    assert result.ranking == ()
    assert result.tag == MemeTag.GUILTY_LIGHT
