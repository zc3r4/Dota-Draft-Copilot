from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

APP_NAME = "Dota Draft Copilot"
APP_SLUG = "dota-draft-copilot"

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
DEBUG_DIR = DATA_DIR / "debug"
LOG_DIR = DATA_DIR / "logs"
CONFIG_PATH = BASE_DIR / "config.json"

SIDES = ("radiant", "dire")
ROLES = ("", "carry", "mid", "offlane", "support", "hard_support")
ROLE_LABELS = {
    "": "Любая",
    "carry": "Керри (1)",
    "mid": "Мид (2)",
    "offlane": "Оффлейн (3)",
    "support": "Саппорт (4)",
    "hard_support": "Хард-саппорт (5)",
}


@dataclass
class Rect:
    """Normalized rectangle (0..1) relative to the captured screen."""

    x: float
    y: float
    w: float
    h: float

    def to_px(self, screen_w: int, screen_h: int) -> tuple[int, int, int, int]:
        x = max(0, min(screen_w - 1, int(round(self.x * screen_w))))
        y = max(0, min(screen_h - 1, int(round(self.y * screen_h))))
        w = max(1, min(screen_w - x, int(round(self.w * screen_w))))
        h = max(1, min(screen_h - y, int(round(self.h * screen_h))))
        return x, y, w, h

    @classmethod
    def from_px(cls, x: int, y: int, w: int, h: int, screen_w: int, screen_h: int) -> "Rect":
        return cls(x / screen_w, y / screen_h, w / screen_w, h / screen_h)

    def is_valid(self) -> bool:
        return self.w > 0.005 and self.h > 0.005

    def to_list(self) -> list[float]:
        return [self.x, self.y, self.w, self.h]

    @classmethod
    def from_list(cls, value: Any) -> "Rect | None":
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            return None
        try:
            return cls(*(float(v) for v in value))
        except (TypeError, ValueError):
            return None


@dataclass
class Config:
    gsi_port: int = 32123
    monitor: int = 1
    scan_interval: float = 1.2
    always_on_top: bool = True
    opacity: float = 0.96

    # Normalized rectangles for the two team strips. They are calibrated per
    # faction, not per local player: radiant box always shows Radiant picks.
    boxes: dict[str, Rect] = field(default_factory=dict)
    calibration_screen: tuple[int, int] = (0, 0)

    side_override: str = ""  # "", "radiant", "dire"
    role: str = ""  # see ROLES
    role_lock: bool = False

    # Neutral item unlock times in seconds (tier -> seconds).  These defaults
    # come from scripts/npc/neutral_items.txt of the installed client; the
    # settings tab lets the user adjust them after a patch.
    neutral_times: dict[str, int] = field(
        default_factory=lambda: {"1": 0, "2": 900, "3": 1500, "4": 2100, "5": 3600}
    )
    auto_scan: bool = True
    scan_only_in_draft: bool = True
    show_debug_frames: bool = False
    transparent_on_scan: bool = True

    window_x: int = -1
    window_y: int = -1
    window_w: int = 900
    window_h: int = 940

    data_ttl_hours: float = 12.0
    request_timeout: float = 45.0
    log_level: str = "INFO"

    def box(self, side: str) -> Rect | None:
        rect = self.boxes.get(side)
        return rect if isinstance(rect, Rect) and rect.is_valid() else None

    def has_calibration(self) -> bool:
        return all(self.box(s) is not None for s in SIDES)

    def effective_side(self) -> str:
        if self.side_override in SIDES:
            return self.side_override
        return ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["boxes"] = {k: v.to_list() for k, v in self.boxes.items() if isinstance(v, Rect)}
        d["calibration_screen"] = list(self.calibration_screen)
        return d

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "Config":
        cfg = cls()
        for key, value in (data or {}).items():
            if not hasattr(cfg, key):
                continue
            if key == "boxes":
                boxes = {}
                for side in SIDES:
                    rect = Rect.from_list((value or {}).get(side))
                    if rect and rect.is_valid():
                        boxes[side] = rect
                cfg.boxes = boxes
            elif key == "calibration_screen":
                if isinstance(value, (list, tuple)) and len(value) == 2:
                    cfg.calibration_screen = (int(value[0]), int(value[1]))
            elif key == "neutral_times":
                if isinstance(value, dict):
                    cfg.neutral_times = {str(k): int(v) for k, v in value.items()}
            elif key == "role" and value not in ROLES:
                cfg.role = ""
            elif key == "side_override" and value not in ("",) + SIDES:
                cfg.side_override = ""
            else:
                setattr(cfg, key, value)
        return cfg

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "Config":
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError):
            return cls()
        return cls.from_json(data)

    def save(self, path: Path = CONFIG_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


def ensure_dirs() -> None:
    for path in (DATA_DIR, CACHE_DIR, DEBUG_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
