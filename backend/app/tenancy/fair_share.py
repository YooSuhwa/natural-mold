"""Per-company fair order for background work (plan 8.7 "백그라운드 작업"; H-06).

"백그라운드 작업(트리거, 큐, 평가)은 회사별 동시 실행 상한으로 순서를 나눠 한 회사가
작업기를 독점하지 않게 합니다": when a worker slot frees up, the next item is the oldest
one of the company that is running the fewest items right now. A company alone in the
queue still uses every slot (work-conserving); a second company's first item goes ahead
of the first company's backlog.
"""

from collections.abc import Hashable, Mapping, Sequence


def fair_index[K: Hashable](companies: Sequence[K], running: Mapping[K, int]) -> int:
    """Index of the oldest queued item whose company runs the fewest items (``-1``: empty)."""
    best, best_load = -1, 0
    for index, company in enumerate(companies):
        load = running.get(company, 0)
        if best < 0 or load < best_load:
            best, best_load = index, load
    return best


__all__ = ["fair_index"]
