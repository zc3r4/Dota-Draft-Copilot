from __future__ import annotations

import threading
from pathlib import Path

import numpy as np


class CaptureError(RuntimeError):
    pass


class ScreenCapture:
    """Thin mss wrapper returning BGR frames."""

    def __init__(self, monitor: int = 1) -> None:
        self.monitor = int(monitor)
        self._lock = threading.Lock()
        self._sct = None

    def _ensure(self):
        if self._sct is None:
            import mss

            self._sct = mss.mss()
        return self._sct

    def monitors(self) -> list[dict]:
        sct = self._ensure()
        return list(sct.monitors)

    def monitor_region(self, monitor: int | None = None) -> dict:
        sct = self._ensure()
        idx = self.monitor if monitor is None else int(monitor)
        if idx < 0 or idx >= len(sct.monitors):
            idx = 1 if len(sct.monitors) > 1 else 0
        return dict(sct.monitors[idx])

    def grab(self, monitor: int | None = None) -> np.ndarray:
        sct = self._ensure()
        region = self.monitor_region(monitor)
        with self._lock:
            try:
                raw = sct.grab(region)
            except Exception as exc:  # mss raises platform-specific errors
                raise CaptureError(f"Не удалось захватить экран: {exc}") from exc
        frame = np.asarray(raw)
        if frame.size == 0:
            raise CaptureError("Пустой кадр. Проверьте режим окна Dota 2 (нужен borderless).")
        return frame[:, :, :3].copy()  # BGRA -> BGR

    def grab_png(self, path: Path, monitor: int | None = None) -> Path:
        import cv2

        frame = self.grab(monitor)
        path.parent.mkdir(parents=True, exist_ok=True)
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            raise CaptureError("Не удалось закодировать снимок экрана.")
        path.write_bytes(encoded.tobytes())
        return path

    def close(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None
