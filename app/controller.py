from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from .assets import ensure_item_icons
from .config import CACHE_DIR, Config
from .data import (
    JsonCache,
    OpenDota,
    build_hero_profiles,
    build_item_profiles,
    matchup_index,
)
from .gamedata import (
    NeutralPool,
    apply_neutral_pool,
    load_hero_complexity,
    load_neutral_pool,
)
from .steam import find_dota_root, read_patch_info
from .vision import DraftScanner, SlotResult, TemplateStore


class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = _TaskSignals()

    def run(self) -> None:  # noqa: D401 - QRunnable API
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001 - reported through the UI
            self.signals.failed.emit(str(exc))
        else:
            self.signals.done.emit(result)


class DataService(QObject):
    """Loads OpenDota data and runs all network work off the UI thread."""

    status = Signal(str)
    core_ready = Signal()
    assets_ready = Signal()
    load_progress = Signal(int, int)
    assets_progress = Signal(int, int)
    error = Signal(str)
    matchups_changed = Signal()
    popularity_changed = Signal(int, dict)

    def __init__(self, config: Config, allow_network: bool = True) -> None:
        super().__init__()
        self.config = config
        self.allow_network = allow_network
        self.cache = JsonCache(CACHE_DIR, config.data_ttl_hours * 3600)
        self.api = OpenDota(self.cache, config, allow_network=allow_network)
        self.profiles: dict[int, Any] = {}
        self.items: dict[int, Any] = {}
        self.heroes_raw: list[dict] = []
        self.matchups: dict[int, dict[int, tuple[float, int]]] = {}
        self.popularity: dict[int, dict[str, dict[str, int]]] = {}
        self.patch: dict[str, str] = {}
        self.neutral_pool: NeutralPool | None = load_neutral_pool()
        self.templates: TemplateStore | None = None
        self.templates_ready = False
        self.last_warning = ""
        self.last_error = ""
        self.core_loaded = False
        self.assets_loaded = False
        self._core_loading = False
        self._assets_loading = False
        self._matchup_inflight: set[int] = set()
        self._popularity_inflight: set[int] = set()
        self._tasks: set[_Task] = set()
        self._lock = threading.Lock()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(4)

    def _start_task(self, task: _Task) -> None:
        """Start a task and keep a Python reference until its signals fire.

        QThreadPool only owns the C++ QRunnable. Without this set the Python
        wrapper (and its signal object) can be garbage-collected before the
        queued done/failed signal is delivered, silently dropping matchups,
        item popularity and asset callbacks.
        """
        self._tasks.add(task)
        task.signals.done.connect(lambda *_: self._tasks.discard(task))
        task.signals.failed.connect(lambda *_: self._tasks.discard(task))
        self.pool.start(task)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._core_loading or self.core_loaded:
            return
        self._core_loading = True
        self.status.emit("Загрузка героев и предметов из OpenDota…")
        task = _Task(self._load_core)
        task.signals.done.connect(self._on_core)
        task.signals.failed.connect(self._on_core_failed)
        self._start_task(task)

    def reload(self) -> None:
        if self._core_loading:
            return
        self.core_loaded = False
        self.assets_loaded = False
        self.templates_ready = False
        self.templates = None
        self._core_loading = False
        self.start()

    def _load_core(self) -> dict[str, Any]:
        heroes = self.api.heroes()
        stats = self.api.hero_stats()
        abilities = self.api.abilities()
        hero_abilities = self.api.hero_abilities()
        items_raw = self.api.items()
        profiles = build_hero_profiles(
            heroes,
            stats,
            hero_abilities,
            abilities,
            load_hero_complexity(),
        )
        items = build_item_profiles(items_raw)
        pool = load_neutral_pool()
        items = apply_neutral_pool(items, pool)
        patch = read_patch_info(find_dota_root())
        images = {
            int(row.get("id") or 0): (row.get("img") or row.get("icon") or "")
            for row in stats
            if row.get("id")
        }
        heroes_with_images = []
        for hero in heroes:
            try:
                hero_id = int(hero.get("id") or 0)
            except (TypeError, ValueError):
                hero_id = 0
            enriched = dict(hero)
            if not enriched.get("img") and images.get(hero_id):
                enriched["img"] = images[hero_id]
            heroes_with_images.append(enriched)
        return {
            "heroes": heroes_with_images,
            "profiles": profiles,
            "items": items,
            "pool": pool,
            "patch": patch,
            "warning": self.api.last_warning,
            "error": self.api.last_error,
        }

    def _on_core(self, result: dict[str, Any]) -> None:
        self._core_loading = False
        self.core_loaded = True
        self.heroes_raw = list(result.get("heroes") or [])
        self.profiles = dict(result.get("profiles") or {})
        self.items = dict(result.get("items") or {})
        self.neutral_pool = result.get("pool")
        self.patch = dict(result.get("patch") or {})
        self.last_warning = str(result.get("warning") or "")
        self.last_error = str(result.get("error") or "")
        self.core_ready.emit()
        self.status.emit(
            f"Данные готовы: {len(self.profiles)} героев, "
            f"{len(self.items)} предметов, "
            f"{sum(1 for i in self.items.values() if getattr(i, 'neutral', False))} нейтральных"
        )
        self._start_assets()

    def _on_core_failed(self, message: str) -> None:
        self._core_loading = False
        self.last_error = message
        self.error.emit(f"Не удалось загрузить данные: {message}")
        self.status.emit("Ошибка загрузки данных — проверьте интернет и кэш.")

    def _start_assets(self) -> None:
        if self._assets_loading or self.assets_loaded or not self.heroes_raw:
            return
        self._assets_loading = True
        self.status.emit("Скачивание портретов героев и иконок предметов…")
        task = _Task(self._load_assets)
        task.signals.done.connect(self._on_assets)
        task.signals.failed.connect(self._on_assets_failed)
        self._start_task(task)

    def _load_assets(self) -> TemplateStore:
        store = TemplateStore()
        store.load(
            self.heroes_raw,
            progress=lambda done, total: self.assets_progress.emit(done, total),
            allow_download=self.allow_network,
        )
        ensure_item_icons(
            self.items.values(),
            progress=lambda done, total: self.load_progress.emit(done, total),
            allow_download=self.allow_network,
        )
        return store

    def _on_assets(self, store: TemplateStore) -> None:
        self._assets_loading = False
        self.assets_loaded = True
        self.templates = store
        self.templates_ready = bool(store.templates)
        self.assets_ready.emit()
        missing = len(store.failed_downloads)
        if missing:
            self.status.emit(f"Портреты загружены, не удалось скачать: {missing}")
        else:
            self.status.emit("Портреты и иконки готовы.")

    def _on_assets_failed(self, message: str) -> None:
        self._assets_loading = False
        self.last_error = message
        self.status.emit(f"Не удалось скачать изображения: {message}")
        self.error.emit(f"Изображения недоступны: {message}")

    # ------------------------------------------------------------------
    # On-demand API data
    # ------------------------------------------------------------------
    def ensure_matchups(self, enemy_ids: list[int]) -> None:
        for hero_id in enemy_ids:
            hero_id = int(hero_id)
            if hero_id <= 0 or hero_id in self.matchups:
                continue
            with self._lock:
                if hero_id in self._matchup_inflight:
                    continue
                self._matchup_inflight.add(hero_id)
            task = _Task(self.api.matchups, hero_id)
            task.signals.done.connect(
                lambda rows, hid=hero_id: self._on_matchup(hid, rows)
            )
            task.signals.failed.connect(
                lambda message, hid=hero_id: self._on_matchup_failed(hid, message)
            )
            self._start_task(task)

    def _on_matchup(self, hero_id: int, rows: Any) -> None:
        with self._lock:
            self._matchup_inflight.discard(hero_id)
        self.matchups[hero_id] = matchup_index(rows or [])
        self.matchups_changed.emit()

    def _on_matchup_failed(self, hero_id: int, message: str) -> None:
        with self._lock:
            self._matchup_inflight.discard(hero_id)
        self.last_error = message
        self.status.emit(f"Не удалось получить матчапы для героя {hero_id}: {message}")

    def ensure_popularity(self, hero_id: int) -> None:
        hero_id = int(hero_id)
        if hero_id <= 0 or hero_id in self.popularity:
            return
        with self._lock:
            if hero_id in self._popularity_inflight:
                return
            self._popularity_inflight.add(hero_id)
        task = _Task(self.api.item_popularity, hero_id)
        task.signals.done.connect(
            lambda data, hid=hero_id: self._on_popularity(hid, data)
        )
        task.signals.failed.connect(
            lambda message, hid=hero_id: self._on_popularity_failed(hid, message)
        )
        self._start_task(task)

    def _on_popularity(self, hero_id: int, data: Any) -> None:
        with self._lock:
            self._popularity_inflight.discard(hero_id)
        self.popularity[hero_id] = data or {}
        self.popularity_changed.emit(hero_id, self.popularity[hero_id])

    def _on_popularity_failed(self, hero_id: int, message: str) -> None:
        with self._lock:
            self._popularity_inflight.discard(hero_id)
        self.status.emit(f"Нет статистики предметов для героя {hero_id}: {message}")

    def cache_age_hours(self, key: str) -> float | None:
        age = self.api.cache_age(key)
        return age / 3600 if age is not None else None


# ---------------------------------------------------------------------------
# Draft stabilisation
# ---------------------------------------------------------------------------


@dataclass
class TrackerState:
    heroes: list[int | None] = field(default_factory=lambda: [None] * 5)
    scores: list[float] = field(default_factory=lambda: [0.0] * 5)
    locked: list[bool] = field(default_factory=lambda: [False] * 5)


class DraftTracker:
    """Debounces per-slot recognition so one bad frame cannot ruin the draft."""

    def __init__(self, history: int = 3) -> None:
        self.history = history
        self.sides: dict[str, TrackerState] = {
            "radiant": TrackerState(),
            "dire": TrackerState(),
        }
        self._hist: dict[str, list[deque[int | None]]] = {
            "radiant": [deque(maxlen=history) for _ in range(5)],
            "dire": [deque(maxlen=history) for _ in range(5)],
        }

    def state(self, side: str) -> TrackerState:
        return self.sides[side]

    def slots(self, side: str) -> list[int | None]:
        return list(self.sides[side].heroes)

    def is_complete(self) -> bool:
        """True when both teams have five distinct recognised heroes."""
        ids = [hero_id for side in ("radiant", "dire") for hero_id in self.sides[side].heroes if hero_id]
        return len(ids) == 10 and len(set(ids)) == 10

    def apply(self, side: str, results: list[SlotResult]) -> bool:
        if side not in self.sides:
            return False
        state = self.sides[side]
        changed = False
        for slot, result in enumerate(results[:5]):
            if state.locked[slot]:
                continue
            candidate = result.hero_id if (not result.empty and result.score >= 0.30) else None
            history = self._hist[side][slot]
            history.append(candidate)
            counts: dict[int, int] = {}
            for value in history:
                if value is not None:
                    counts[value] = counts.get(value, 0) + 1
            winner = None
            if counts:
                hero_id, count = max(counts.items(), key=lambda kv: kv[1])
                if count >= 2:
                    winner = hero_id
            current = state.heroes[slot]
            if winner is not None:
                new_value = winner
            elif len(history) >= 2 and all(v is None for v in list(history)[-2:]):
                new_value = None
            else:
                new_value = current
            if new_value != current:
                state.heroes[slot] = new_value
                changed = True
            if result.score:
                state.scores[slot] = result.score
        return changed

    def set_manual(self, side: str, slot: int, hero_id: int | None) -> None:
        if side not in self.sides or not (0 <= slot < 5):
            return
        state = self.sides[side]
        state.heroes[slot] = hero_id
        state.locked[slot] = hero_id is not None
        state.scores[slot] = 1.0 if hero_id else 0.0
        if hero_id is None:
            self._hist[side][slot].clear()

    def clear(self, side: str | None = None) -> None:
        sides = [side] if side else ["radiant", "dire"]
        for name in sides:
            if name not in self.sides:
                continue
            state = self.sides[name]
            state.heroes = [None] * 5
            state.scores = [0.0] * 5
            state.locked = [False] * 5
            for history in self._hist[name]:
                history.clear()


@dataclass
class ScanOutput:
    results: dict[str, list[SlotResult]]
    debug_path: str = ""


class ScanTask(QRunnable):
    """Capture one frame and recognise both team strips."""

    def __init__(
        self,
        monitor: int,
        templates: TemplateStore,
        boxes: dict[str, Any],
        save_debug: bool = False,
        debug_path: str = "",
    ) -> None:
        super().__init__()
        self.monitor = monitor
        self.templates = templates
        self.boxes = boxes
        self.save_debug = save_debug
        self.debug_path = debug_path
        self.signals = _TaskSignals()

    def run(self) -> None:
        from .capture import ScreenCapture
        from .vision import draw_debug

        try:
            capture = ScreenCapture(self.monitor)
            try:
                frame = capture.grab()
            finally:
                capture.close()
            if frame is None or frame.size == 0:
                raise RuntimeError("снимок экрана пуст")
            if float(frame.mean()) < 2.0:
                raise RuntimeError(
                    "снимок экрана чёрный — проверьте режим «Окно без рамки» и номер монитора"
                )
            scanner = DraftScanner(self.templates)
            results = scanner.scan(frame, self.boxes)
            debug_path = ""
            if self.save_debug and self.debug_path:
                import cv2

                debug = draw_debug(frame, results, self.boxes)
                ok, encoded = cv2.imencode(".png", debug)
                if ok:
                    from pathlib import Path

                    path = Path(self.debug_path)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(encoded.tobytes())
                    debug_path = str(path)
            self.signals.done.emit(ScanOutput(results=results, debug_path=debug_path))
        except Exception as exc:  # noqa: BLE001 - reported through the UI
            self.signals.failed.emit(str(exc))
