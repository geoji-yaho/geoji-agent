"""삭제 epoch 비교(04 §3.5).

워커는 dossier 저장 직전과 finalize 직전에 `ai.privacy_epochs` 를 읽어 스냅샷의
`privacy_versions` 와 비교한다. 여기는 비교만 한다. 읽기는 어댑터가 한다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

__all__ = ["mismatched_scopes"]


def mismatched_scopes(
    expected: Iterable[tuple[str, int]],
    current: Mapping[str, int],
) -> list[str]:
    """스냅샷 epoch 와 현재 epoch 가 다른 scope_key 목록(입력 순서, 중복 없음).

    `current` 에 행이 없는 scope_key 는 epoch 0 으로 본다. 빈 목록이면 일치다.
    """
    mismatched: list[str] = []
    for scope_key, epoch in expected:
        if current.get(scope_key, 0) != epoch and scope_key not in mismatched:
            mismatched.append(scope_key)
    return mismatched
