from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from app.config import Rect
from app.vision import DraftScanner, TemplateStore, classify_confidence, split_team_box


def make_hero_images() -> dict[int, np.ndarray]:
    rng = np.random.default_rng(7)
    images: dict[int, np.ndarray] = {}

    first = np.full((90, 160, 3), (40, 20, 10), dtype=np.uint8)
    cv2.circle(first, (50, 45), 28, (0, 220, 255), -1)
    cv2.rectangle(first, (95, 20), (145, 36), (240, 240, 240), -1)
    images[1] = first

    second = np.full((90, 160, 3), (20, 20, 200), dtype=np.uint8)
    cv2.line(second, (0, 0), (160, 90), (0, 0, 0), 8)
    cv2.line(second, (0, 90), (160, 0), (0, 0, 0), 8)
    cv2.circle(second, (80, 45), 16, (255, 255, 255), -1)
    images[2] = second

    third = np.full((90, 160, 3), (30, 150, 30), dtype=np.uint8)
    for x in (15, 65, 115):
        cv2.rectangle(third, (x, 15), (x + 30, 75), (10, 10, 10), -1)
        cv2.rectangle(third, (x + 8, 30), (x + 22, 60), (250, 250, 250), -1)
    images[3] = third

    # A little deterministic noise keeps the PNGs above the download-size
    # threshold used by TemplateStore.download and makes the patterns unique.
    for hero_id, image in images.items():
        noise = rng.integers(-12, 13, size=image.shape, dtype=np.int16)
        images[hero_id] = np.clip(image.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return images


def write_images(cache_dir: Path, images: dict[int, np.ndarray]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    for hero_id, image in images.items():
        ok, encoded = cv2.imencode(".png", image)
        assert ok
        path = cache_dir / f"{hero_id}.png"
        path.write_bytes(encoded.tobytes())
        assert path.stat().st_size > 1000


def load_store(tmp_path: Path, images: dict[int, np.ndarray]) -> TemplateStore:
    cache_dir = tmp_path / "heroes"
    write_images(cache_dir, images)
    store = TemplateStore(cache_dir)
    heroes = [{"id": hid, "localized_name": f"Hero {hid}"} for hid in images]
    store.load(heroes)
    return store


def test_split_team_box_horizontal() -> None:
    cells = split_team_box((100, 50, 1000, 200))
    assert len(cells) == 5
    assert all(100 <= x and x + w <= 1100 for x, _, w, _ in cells)
    assert all(50 <= y and y + h <= 250 for _, y, _, h in cells)
    # Non-overlapping, left to right.
    assert cells[0][0] < cells[1][0] < cells[4][0]
    assert cells[0][0] + cells[0][2] <= cells[1][0]


def test_split_team_box_vertical() -> None:
    cells = split_team_box((20, 20, 180, 900))
    assert len(cells) == 5
    assert all(20 <= y and y + h <= 920 for _, y, _, h in cells)
    assert cells[0][1] < cells[4][1]


def test_classify_confidence() -> None:
    assert classify_confidence(0.9) == "high"
    assert classify_confidence(0.62) == "high"
    assert classify_confidence(0.55) == "medium"
    assert classify_confidence(0.47) == "medium"
    assert classify_confidence(0.2) == "low"


def test_template_store_recognizes_exact_portraits(tmp_path: Path) -> None:
    images = make_hero_images()
    store = load_store(tmp_path, images)
    assert set(store.templates) == {1, 2, 3}

    for hero_id, image in images.items():
        result = store.recognize(image)
        assert result.empty is False
        assert result.hero_id == hero_id, (hero_id, result.hero_id, result.top)
        assert result.score > 0.7
        assert result.margin >= 0


def test_blank_slot_is_empty(tmp_path: Path) -> None:
    store = load_store(tmp_path, make_hero_images())
    blank = np.full((90, 160, 3), 18, dtype=np.uint8)
    result = store.recognize(blank)
    assert result.empty is True


def test_recognizes_portrait_with_wide_margin(tmp_path: Path) -> None:
    """A calibration box often leaves ~10% empty space around the portrait.

    The whole-slot score can drop below the tracker threshold, so the centred
    crop retries must still run instead of bailing out as if the slot were empty.
    """
    images = make_hero_images()
    store = load_store(tmp_path, images)
    for hero_id, image in images.items():
        cell = np.full((108, 192, 3), 42, dtype=np.uint8)
        inner = cv2.resize(image, (148, 84), interpolation=cv2.INTER_AREA)
        x = (192 - inner.shape[1]) // 2
        y = (108 - inner.shape[0]) // 2
        cell[y : y + inner.shape[0], x : x + inner.shape[1]] = inner
        result = store.recognize(cell)
        assert result.empty is False, (hero_id, result.score)
        assert result.hero_id == hero_id, (hero_id, result.hero_id, result.score)


def test_scan_tolerates_small_calibration_shift(tmp_path: Path) -> None:
    """A hand-drawn strip is often a few pixels off; the padded match must cope."""
    images = make_hero_images()
    store = load_store(tmp_path, images)

    frame = np.full((300, 1500, 3), 12, dtype=np.uint8)
    strip = (100, 50, 1200, 200)
    cells = split_team_box(strip)
    order = [1, 2, 3, 1, 2]
    for (x, y, w, h), hero_id in zip(cells, order):
        frame[y : y + h, x : x + w] = cv2.resize(images[hero_id], (w, h))

    # Shift the calibration box by 10 px horizontally and 4 px vertically.
    rect = Rect((strip[0] + 10) / 1500, (strip[1] + 4) / 300, strip[2] / 1500, strip[3] / 300)
    results = DraftScanner(store).scan(frame, {"dire": rect})["dire"]
    assert [r.hero_id for r in results] == order


def test_scan_tolerates_name_row_in_calibration(tmp_path: Path) -> None:
    """If the drawn box includes the player-name row, the art must still be found."""
    images = make_hero_images()
    store = load_store(tmp_path, images)

    frame = np.full((360, 1500, 3), 12, dtype=np.uint8)
    strip = (100, 40, 1000, 180)
    cells = split_team_box(strip)
    order = [1, 2, 3, 1, 2]
    for (x, y, w, h), hero_id in zip(cells, order):
        art_h = max(8, int(round(h * 0.62)))
        frame[y : y + art_h, x : x + w] = cv2.resize(images[hero_id], (w, art_h))
        frame[y + art_h : y + h, x : x + w] = (28, 28, 32)
        cv2.putText(
            frame,
            "player",
            (x + 4, y + h - 4),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (215, 215, 215),
            1,
            cv2.LINE_AA,
        )

    rect = Rect(strip[0] / 1500, strip[1] / 360, strip[2] / 1500, strip[3] / 360)
    results = DraftScanner(store).scan(frame, {"radiant": rect})["radiant"]
    assert [r.hero_id for r in results] == order


def test_scan_side_end_to_end(tmp_path: Path) -> None:
    images = make_hero_images()
    store = load_store(tmp_path, images)

    frame = np.full((200, 1500, 3), 12, dtype=np.uint8)
    # split_team_box already returns the inset cell rectangles that the scanner
    # crops, so pasting exactly there reproduces a perfectly aligned slot.
    cells = split_team_box((0, 0, 1500, 200))
    order = [1, 2, 3]
    for (x, y, w, h), hero_id in zip(cells, order):
        frame[y : y + h, x : x + w] = cv2.resize(images[hero_id], (w, h))
    # Two empty slots are left black.

    scanner = DraftScanner(store)
    results = scanner.scan(frame, {"radiant": Rect(0, 0, 1, 1)})
    slots = results["radiant"]
    assert len(slots) == 5
    assert [s.hero_id for s in slots[:3]] == order
    assert slots[3].empty is True
    assert slots[4].empty is True
