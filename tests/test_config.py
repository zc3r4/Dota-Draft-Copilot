from __future__ import annotations

import json
from pathlib import Path

from app.config import Config, Rect


def test_rect_roundtrip_and_clamping() -> None:
    rect = Rect.from_px(160, 90, 320, 180, 1920, 1080)
    assert rect.to_px(1920, 1080) == (160, 90, 320, 180)
    assert rect.is_valid()

    rect = Rect(x=0.99, y=0.99, w=0.5, h=0.5)
    x, y, w, h = rect.to_px(100, 100)
    assert (x, y) == (99, 99)
    assert w >= 1 and h >= 1
    assert x + w <= 100 and y + h <= 100


def test_invalid_rect() -> None:
    assert Rect.from_list([1, 2, 3]) is None
    assert Rect.from_list("nope") is None
    assert Rect.from_list([0, 0, 0, 0]).is_valid() is False


def test_config_roundtrip(tmp_path: Path) -> None:
    cfg = Config()
    cfg.boxes = {
        "radiant": Rect(0.1, 0.2, 0.3, 0.4),
        "dire": Rect(0.5, 0.6, 0.3, 0.4),
    }
    cfg.side_override = "dire"
    cfg.role = "offlane"
    cfg.neutral_times = {"1": 0, "2": 900, "3": 1500, "4": 2100, "5": 3600}

    path = tmp_path / "config.json"
    cfg.save(path)
    loaded = Config.load(path)

    assert loaded.boxes["radiant"].to_list() == [0.1, 0.2, 0.3, 0.4]
    assert loaded.side_override == "dire"
    assert loaded.role == "offlane"
    assert loaded.neutral_times["5"] == 3600
    assert loaded.has_calibration()


def test_config_rejects_invalid_values(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps(
            {
                "side_override": "middle",
                "role": "jungle",
                "boxes": {"radiant": [0, 0, 0, 0], "dire": "bad"},
                "neutral_times": {"1": "0", "2": 900},
                "unknown_key": 123,
            }
        ),
        encoding="utf-8",
    )
    cfg = Config.load(path)
    assert cfg.side_override == ""
    assert cfg.role == ""
    assert cfg.boxes == {}
    assert cfg.neutral_times == {"1": 0, "2": 900}


def test_missing_config_uses_defaults(tmp_path: Path) -> None:
    cfg = Config.load(tmp_path / "missing.json")
    assert cfg.gsi_port == 32123
    assert cfg.has_calibration() is False
    assert cfg.neutral_times["1"] == 0
