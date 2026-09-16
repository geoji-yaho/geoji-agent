import pytest

from tests.local_e2e.support import expected_meme, localhost_url


def test_meme_strict_tag_and_recent_penalty():
    candidates = [
        {
            "id": "a",
            "tag": "GUILTY_LIGHT",
            "active": True,
            "strategies": ["PREMISE_REJECTION"],
            "emotions": ["PITY"],
            "keywords": ["택시"],
        },
        {
            "id": "b",
            "tag": "GUILTY_LIGHT",
            "active": True,
            "strategies": [],
            "emotions": [],
            "keywords": ["택시", "알람"],
        },
        {
            "id": "c",
            "tag": "NOT_GUILTY",
            "active": True,
            "strategies": ["PREMISE_REJECTION"],
            "emotions": ["PITY"],
            "keywords": ["택시"],
        },
    ]
    hints = {
        "meme_tag": "GUILTY_LIGHT",
        "strategy": "PREMISE_REJECTION",
        "emotion": "PITY",
        "keywords": ["택시", "알람"],
    }
    assert expected_meme(candidates, hints, [], "post") == "a"
    assert expected_meme(candidates, hints, ["a"], "post") == "b"
    assert expected_meme(candidates, {**hints, "meme_tag": "APPROVED"}, [], "post") is None


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com",
        "http://127.0.0.1.evil.test",
        "http://localhost/path",
        "http://user:pass@localhost:80",
    ],
)
def test_external_or_nonroot_url_rejected(url):
    with pytest.raises(ValueError):
        localhost_url(url)


def test_local_root_url_allowed():
    assert localhost_url("http://127.0.0.1:18080") == "http://127.0.0.1:18080"
