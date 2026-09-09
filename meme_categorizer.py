"""
Concept and category suggestion for generated line art assets.

This is the "Metadata Agent" step of docs/ai-agent-design-plan.md §10.2. It fills
two of the retrieval facets of the `meme_images` record in §10.3:

  concepts           the tone the meme carries — 분노, 조롱, 비난, 무시 … (the
                     `emotions` field; used by Meme Tool priority 4, "감정과 강도 일치")
  expense_categories which expense category the meme suits (the `categories` field,
                     priority 3). Secondary — most reaction memes carry no such signal.

Scoring is local and deterministic: it reads the text recognized from the image
(macOS Vision, see text_layer) and matches keyword and punctuation patterns. No API
key, no network. Output is a *suggestion* — §10.3 requires human review before an
asset is registered, and anything without a hit is reported at zero confidence so a
reviewer can see it needs attention.

Text is a partial signal for tone: the face carries most of it. Treat a low-scoring
result as "needs a look", not as "no tone".
"""
import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence, Tuple

import text_layer

# --------------------------------------------------------------------- taxonomies

# The tone a meme carries. No enum is fixed in the design doc; DISAPPROVAL and
# ABSURD_SERIOUSNESS are the two values it names by example (§10.3, §10.5).
CONCEPTS: Tuple[Tuple[str, str], ...] = (
    ("ANGER", "분노"),
    ("MOCKERY", "조롱"),
    ("BLAME", "비난"),
    ("DISMISSAL", "무시"),
    ("DISAPPROVAL", "못마땅함"),
    ("SHOCK", "경악"),
    ("SELF_MOCKERY", "자조"),
    ("RESIGNATION", "체념"),
    ("ABSURD_SERIOUSNESS", "어이없는 진지함"),
    ("APPROVAL", "인정"),
    ("UNCLASSIFIED", "미분류"),
)

# NOTE: labels are the 11 expense categories fixed on 9/8 (D-21). The codes follow
# the naming in §10.3; reconcile them with geoji-web/src/shared/constants/
# expense-categories.ts before assets ship.
EXPENSE_CATEGORIES: Tuple[Tuple[str, str], ...] = (
    ("FOOD", "식비"),
    ("DELIVERY", "배달"),
    ("CAFE_SNACK", "카페/간식"),
    ("TRANSPORT_TAXI", "교통/택시"),
    ("SHOPPING_FASHION", "쇼핑/패션"),
    ("BEAUTY", "뷰티"),
    ("HOBBY_LEISURE", "취미/여가"),
    ("ALCOHOL_NIGHTLIFE", "술/유흥"),
    ("SUBSCRIPTION", "구독"),
    ("LIVING", "생활"),
    ("ETC", "기타"),
)

CONCEPT_FALLBACK = "UNCLASSIFIED"
EXPENSE_FALLBACK = "ETC"

CONCEPT_KEYWORDS: Dict[str, Tuple[Tuple[str, float], ...]] = {
    "ANGER": (
        ("닥쳐", 1.8), ("하지마", 1.5), ("미쳤", 1.5), ("돌았", 1.5), ("그만해", 1.5),
        ("열받", 1.5), ("빡쳐", 1.8), ("화가", 1.3), ("그만", 1.0), ("당장", 1.0),
    ),
    "MOCKERY": (
        ("보고 웃", 1.8), ("비웃", 1.8), ("타고난", 1.5), ("왜 항상", 1.5), ("잘났", 1.5),
        ("어이구", 1.5), ("놀리", 1.3), ("웃는", 1.2), ("ㅋㅋ", 1.2), ("ㅎㅎ", 1.0),
        ("꼬라지", 1.5), ("주제에", 1.5),
    ),
    "BLAME": (
        ("찾아냈", 1.8), ("때문에", 1.5), ("책임", 1.5), ("다 쓴", 1.5), ("또 샀", 1.5),
        ("누가 그랬", 1.5), ("니가", 1.2), ("네가", 1.2), ("탓", 1.3), ("범인", 1.5),
    ),
    "DISMISSAL": (
        ("어쩌라고", 1.8), ("알빠", 1.8), ("됐고", 1.5), ("관심 없", 1.5), ("그러든지", 1.5),
        ("말든", 1.3), ("신경 꺼", 1.5), ("모르겠고", 1.3),
    ),
    "DISAPPROVAL": (
        ("굳이", 1.5), ("그건 좀", 1.5), ("별로", 1.3), ("아쉽", 1.2), ("실망", 1.5),
        ("진심", 1.0), ("정말", 0.8),
    ),
    "SHOCK": (
        ("이럴 리가", 1.8), ("말도 안", 1.8), ("실화", 1.5), ("믿기지", 1.5), ("잠깐", 1.2),
        ("헐", 1.2), ("뭐야", 1.2), ("설마", 1.3),
    ),
    "SELF_MOCKERY": (
        ("걱정할 돈", 1.8), ("없으니까", 1.5), ("거지예요", 1.5), ("거지에요", 1.5),
        ("거렁뱅이", 1.3), ("한장조차", 1.5), ("도와주세요", 1.3), ("돈이 없", 1.3),
        ("없어요", 1.2), ("거지세요", 1.0), ("가난", 0.8), ("난가", 1.0),
    ),
    "RESIGNATION": (
        ("어차피", 1.5), ("포기", 1.5), ("됐다", 1.2), ("어쩔 수 없", 1.5), ("살아갈", 1.0),
        ("그냥", 0.8),
    ),
    "ABSURD_SERIOUSNESS": (
        ("정색", 1.8), ("엄중", 1.5), ("진지", 1.5), ("선고", 1.3), ("판결", 1.3),
        ("강림", 1.2), ("본 법정", 1.5),
    ),
    "APPROVAL": (
        ("훌륭", 1.5), ("잘했", 1.5), ("칭찬", 1.5), ("인정", 1.3), ("멋지", 1.3),
        ("굿", 1.0),
    ),
}

EXPENSE_KEYWORDS: Dict[str, Tuple[Tuple[str, float], ...]] = {
    "FOOD": (
        ("밥값", 1.5), ("한끼", 1.5), ("국밥", 1.5), ("백반", 1.5), ("김밥", 1.5),
        ("라면", 1.2), ("도시락", 1.2), ("점심", 1.2), ("저녁", 1.0), ("아침", 1.0),
        ("식당", 1.2), ("식비", 1.5), ("식사", 1.2), ("급식", 1.2), ("밥", 0.8),
    ),
    "DELIVERY": (
        ("배달비", 1.8), ("배민", 1.8), ("요기요", 1.8), ("쿠팡이츠", 1.8),
        ("배달", 1.5), ("야식", 1.2), ("치킨", 1.2), ("피자", 1.2), ("족발", 1.2),
        ("탕수육", 1.2), ("시켜먹", 1.5),
    ),
    "CAFE_SNACK": (
        ("아메리카노", 1.8), ("스타벅스", 1.8), ("스벅", 1.8), ("아아", 1.5),
        ("카페", 1.5), ("커피", 1.5), ("라떼", 1.5), ("디저트", 1.3), ("케이크", 1.3),
        ("빙수", 1.3), ("간식", 1.2), ("음료", 1.0), ("마카롱", 1.3),
    ),
    "TRANSPORT_TAXI": (
        ("택시", 1.8), ("지하철", 1.5), ("버스", 1.3), ("교통비", 1.8), ("주유", 1.5),
        ("기름값", 1.5), ("따릉이", 1.5), ("킥보드", 1.3), ("ktx", 1.5), ("기차", 1.2),
        ("대중교통", 1.5), ("교통", 1.0),
    ),
    "SHOPPING_FASHION": (
        ("무신사", 1.8), ("지그재그", 1.8), ("명품", 1.5), ("쇼핑", 1.5), ("패션", 1.3),
        ("신발", 1.3), ("가방", 1.3), ("원피스", 1.3), ("코트", 1.2), ("옷", 1.0),
        ("지름", 1.2), ("질렀", 1.2),
    ),
    "BEAUTY": (
        ("화장품", 1.8), ("미용실", 1.8), ("네일", 1.5), ("파마", 1.5), ("염색", 1.5),
        ("향수", 1.3), ("립스틱", 1.3), ("마스크팩", 1.3), ("피부과", 1.5), ("뷰티", 1.3),
    ),
    "HOBBY_LEISURE": (
        ("노래방", 1.5), ("피시방", 1.5), ("콘서트", 1.5), ("전시회", 1.5), ("헬스장", 1.5),
        ("여행", 1.3), ("캠핑", 1.3), ("골프", 1.3), ("굿즈", 1.3), ("덕질", 1.3),
        ("영화", 1.2), ("게임", 1.2), ("취미", 1.2),
    ),
    "ALCOHOL_NIGHTLIFE": (
        ("술값", 1.8), ("소주", 1.5), ("맥주", 1.5), ("막걸리", 1.5), ("위스키", 1.5),
        ("와인", 1.3), ("회식", 1.3), ("포차", 1.3), ("안주", 1.2), ("해장", 1.2),
        ("클럽", 1.2), ("유흥", 1.3), ("술", 1.0),
    ),
    "SUBSCRIPTION": (
        ("넷플릭스", 1.8), ("넷플", 1.8), ("왓챠", 1.8), ("디즈니", 1.5), ("스포티파이", 1.8),
        ("멜론", 1.5), ("정기결제", 1.8), ("월정액", 1.8), ("구독료", 1.8), ("구독", 1.5),
        ("프리미엄", 1.0),
    ),
    "LIVING": (
        ("관리비", 1.8), ("공과금", 1.8), ("전기세", 1.8), ("통신비", 1.8), ("월세", 1.8),
        ("생필품", 1.5), ("생활비", 1.5), ("다이소", 1.5), ("마트", 1.3), ("약국", 1.3),
        ("세제", 1.2), ("휴지", 1.2),
    ),
}

CONCEPT_LABELS: Dict[str, str] = dict(CONCEPTS)
EXPENSE_LABELS: Dict[str, str] = dict(EXPENSE_CATEGORIES)

_NON_WORD = re.compile(r"[^0-9a-z가-힣ㄱ-ㅎ]+")


# ------------------------------------------------------------------------ scoring


def normalize(text: str) -> str:
    """Lowercases and collapses punctuation so keyword matching is not tripped by it."""
    return _NON_WORD.sub(" ", text.lower()).strip()


def _punctuation_signals(raw_text: str) -> List[Tuple[str, float, str]]:
    """
    Tone signals that `normalize` would erase.

    Shouting and disbelief live in the punctuation, so they are read off the raw
    string before it is cleaned. These only ever *modify* a tone the words already
    support — see `_score` — because "…싶으세요? 네!" is a question and an
    exclamation without being either a question of disapproval or a shout.
    Returns (code, weight, evidence) triples.
    """
    signals = []
    exclamations = raw_text.count("!") + raw_text.count("！")
    questions = raw_text.count("?") + raw_text.count("？")

    if exclamations >= 2:
        signals.append(("ANGER", 1.0, "!!"))
    elif exclamations == 1:
        signals.append(("ANGER", 0.4, "!"))
    if questions >= 2:
        signals.append(("SHOCK", 0.8, "??"))
    elif questions == 1:
        signals.append(("DISAPPROVAL", 0.4, "?"))
    return signals


def _score(
    text: str,
    keywords: Dict[str, Tuple[Tuple[str, float], ...]],
    labels: Dict[str, str],
    fallback: str,
    top_n: int,
    min_confidence: float,
    min_evidence: float,
    extra_signals: Sequence[Tuple[str, float, str]] = (),
) -> List[Dict]:
    """
    Shared scorer for both taxonomies.

    Confidence is each entry's share of the total score — "how much of the evidence
    points here", not a calibrated probability. Because a share says nothing about
    how much evidence there was, `min_evidence` gates the absolute total: below it
    the result is the fallback, so one weak hit cannot look like certainty.
    """
    haystack = normalize(text)

    scores: Dict[str, float] = {}
    hits: Dict[str, List[str]] = {}

    for code, entries in keywords.items():
        # Longest first, then skip any keyword nested in one already matched, so
        # "넷플릭스" is not also counted as "넷플" and does not outweigh a rival.
        for keyword, weight in sorted(entries, key=lambda kw: -len(kw[0])):
            if keyword not in haystack:
                continue
            if any(keyword in matched for matched in hits.get(code, ())):
                continue
            scores[code] = scores.get(code, 0.0) + weight
            hits.setdefault(code, []).append(keyword)

    # Punctuation modifies a tone the words support; on its own it decides nothing.
    if scores:
        for code, weight, evidence in extra_signals:
            scores[code] = scores.get(code, 0.0) + weight
            hits.setdefault(code, []).append(evidence)

    total = sum(scores.values())
    if total < min_evidence:
        return [_entry(fallback, labels, 0.0, [])]

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    out = [
        _entry(code, labels, score / total, hits[code])
        for code, score in ranked
        if score / total >= min_confidence
    ]
    return out[:top_n] or [_entry(fallback, labels, 0.0, [])]


def _entry(code: str, labels: Dict[str, str], confidence: float, matched: List[str]) -> Dict:
    return {
        "code": code,
        "label": labels[code],
        "confidence": round(confidence, 3),
        "matched": matched,
    }


MIN_EVIDENCE = 1.0


def categorize_concepts(
    text: str,
    top_n: int = 3,
    min_confidence: float = 0.15,
    min_evidence: float = MIN_EVIDENCE,
) -> List[Dict]:
    """Suggests the tone a meme carries — 분노, 조롱, 비난, 무시 and the rest of CONCEPTS."""
    return _score(
        text,
        CONCEPT_KEYWORDS,
        CONCEPT_LABELS,
        CONCEPT_FALLBACK,
        top_n,
        min_confidence,
        min_evidence,
        _punctuation_signals(text),
    )


def categorize_expenses(
    text: str,
    top_n: int = 3,
    min_confidence: float = 0.15,
    min_evidence: float = MIN_EVIDENCE,
) -> List[Dict]:
    """Suggests which expense categories a meme suits. Most reaction memes: none."""
    return _score(
        text,
        EXPENSE_KEYWORDS,
        EXPENSE_LABELS,
        EXPENSE_FALLBACK,
        top_n,
        min_confidence,
        min_evidence,
    )


def categorize_image(
    image_path: str, extra_text: str = "", top_n: int = 3, min_confidence: float = 0.15
) -> Tuple[List[Dict], List[Dict], str]:
    """
    Recognizes the text in an image and scores both taxonomies.

    `extra_text` lets an operator add context the image itself does not carry (a
    collector note, a title). Returns (concepts, expense_categories, recognized_text).
    """
    recognized = " ".join(box[0] for box in text_layer.recognize_text(image_path))
    combined = " ".join(part for part in (recognized, extra_text) if part)
    return (
        categorize_concepts(combined, top_n, min_confidence),
        categorize_expenses(combined, top_n, min_confidence),
        recognized,
    )


def build_metadata(
    concepts: List[Dict],
    expense_categories: Optional[List[Dict]] = None,
    source_path: Optional[str] = None,
    asset_path: Optional[str] = None,
    recognized_text: str = "",
) -> Dict:
    """Builds the suggestion slice of a meme_images record (§10.3)."""
    return {
        "source_path": source_path,
        "asset_path": asset_path,
        "recognized_text": recognized_text,
        "concepts": concepts,
        "expense_categories": expense_categories or [],
        "needs_review": all(item["confidence"] <= 0.0 for item in concepts),
        "generated_by": "meme_categorizer/keyword-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def write_metadata(metadata: Dict, output_path: str) -> str:
    """Writes the metadata JSON sidecar next to the generated asset."""
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return output_path
