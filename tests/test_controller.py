from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

from app.config import Config
from app.controller import DataService, DraftTracker, _Task
from app.vision import SlotResult


def result(hero_id: int | None, score: float = 0.8, empty: bool = False) -> SlotResult:
    return SlotResult(slot=0, hero_id=hero_id, score=score, margin=0.1, top=[], empty=empty)


def results(hero_id: int | None, score: float = 0.8) -> list[SlotResult]:
    return [result(hero_id, score)] + [result(None, 0.0, True) for _ in range(4)]


def test_one_bad_frame_does_not_change_draft() -> None:
    tracker = DraftTracker()
    assert tracker.apply("radiant", results(1)) is False
    assert tracker.slots("radiant")[0] is None
    assert tracker.apply("radiant", results(1)) is True
    assert tracker.slots("radiant")[0] == 1


def test_is_complete_requires_ten_distinct_heroes() -> None:
    tracker = DraftTracker()
    for slot, hero_id in enumerate(range(1, 6)):
        tracker.set_manual("radiant", slot, hero_id)
    assert tracker.is_complete() is False

    for slot, hero_id in enumerate(range(6, 10)):
        tracker.set_manual("dire", slot, hero_id)
    assert tracker.is_complete() is False
    tracker.set_manual("dire", 4, 10)
    assert tracker.is_complete() is True

    # Ten slots filled, but one hero is repeated: not a valid draft.
    tracker.set_manual("dire", 4, 1)
    assert tracker.is_complete() is False

    tracker.clear()
    assert tracker.is_complete() is False


def test_low_confidence_is_ignored() -> None:
    tracker = DraftTracker()
    tracker.apply("radiant", results(1, score=0.2))
    tracker.apply("radiant", results(1, score=0.2))
    assert tracker.slots("radiant")[0] is None


def test_empty_frames_clear_a_slot() -> None:
    tracker = DraftTracker()
    tracker.apply("radiant", results(1))
    tracker.apply("radiant", results(1))
    tracker.apply("radiant", results(None))
    assert tracker.slots("radiant")[0] == 1
    tracker.apply("radiant", results(None))
    assert tracker.slots("radiant")[0] is None


def test_manual_lock_survives_scans() -> None:
    tracker = DraftTracker()
    tracker.set_manual("radiant", 0, 5)
    assert tracker.state("radiant").locked[0] is True
    tracker.apply("radiant", results(7))
    tracker.apply("radiant", results(7))
    assert tracker.slots("radiant")[0] == 5
    tracker.set_manual("radiant", 0, None)
    assert tracker.state("radiant").locked[0] is False


def test_sides_are_independent() -> None:
    tracker = DraftTracker()
    tracker.set_manual("radiant", 0, 1)
    tracker.set_manual("dire", 0, 2)
    assert tracker.slots("radiant")[0] == 1
    assert tracker.slots("dire")[0] == 2
    tracker.clear("radiant")
    assert tracker.slots("radiant")[0] is None
    assert tracker.slots("dire")[0] == 2


@pytest.fixture(scope="module")
def qt_core():
    app = QCoreApplication.instance() or QCoreApplication([])
    yield app


def test_data_service_retains_task_until_done(qt_core) -> None:
    """A QRunnable wrapper must stay alive until its queued signal arrives."""
    service = DataService(Config(), allow_network=False)
    seen: list[int] = []
    loop = QEventLoop()
    task = _Task(lambda: 42)
    task.signals.done.connect(lambda value: (seen.append(value), loop.quit()))
    service._start_task(task)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    service.pool.waitForDone(1000)
    assert seen == [42]
