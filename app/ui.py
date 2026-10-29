from __future__ import annotations

import math
import os
import time
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QIcon,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtGui import QGuiApplication, QScreen

from . import __version__
from .assets import item_icon_path
from .capture import CaptureError, ScreenCapture
from .config import (
    CACHE_DIR,
    DATA_DIR,
    DEBUG_DIR,
    ROLE_LABELS,
    ROLES,
    Config,
    Rect,
)
from .controller import DataService, DraftTracker, ScanOutput, ScanTask
from .data import plural_ru
from .gsi import GSIServer, GSIState, GSISnapshot
from .recommend import (
    BuildResult,
    CounterCandidate,
    CounterResult,
    ItemEngine,
    ItemSuggestion,
    NeutralEngine,
    NeutralResult,
    RecommendationEngine,
)
from .steam import find_dota_root, gsi_cfg_path, install_gsi, is_gsi_installed
from .vision import TemplateStore

SIDE_LABELS = {"radiant": "Radiant (свет)", "dire": "Dire (тьма)"}
SIDE_SHORT = {"radiant": "Radiant", "dire": "Dire"}
CONFIDENCE_LABELS = {"high": "высокая", "medium": "средняя", "low": "низкая"}
CONFIDENCE_COLORS = {"high": "#6cd48a", "medium": "#f0b35c", "low": "#ec6b6b"}
PHASE_LABELS = {
    "start": "Старт",
    "early": "Ранняя игра",
    "mid": "Мид",
    "late": "Лейт",
    "situational": "Ситуативно",
}

ITEM_ICON_SIZE = QSize(88, 64)
ITEM_GRID_SIZE = QSize(104, 86)

ATTRIBUTE_LABELS = {
    "": "Все атрибуты",
    "str": "Сила",
    "agi": "Ловкость",
    "int": "Интеллект",
    "all": "Универсальный",
}
ATTACK_LABELS = {
    "": "Любая атака",
    "Melee": "Ближний бой",
    "Ranged": "Дальний бой",
}
COMPLEXITY_LABELS = {
    0: "Любая сложность",
    1: "Простая (1)",
    2: "Средняя (2)",
    3: "Сложная (3)",
}
COMPLEXITY_NAMES = {1: "простая", 2: "средняя", 3: "сложная"}
COUNTER_SORT_LABELS = (
    ("recommendation", "По рекомендации"),
    ("name", "По имени A–Z"),
    ("complexity_asc", "Сложность: простая → сложная"),
    ("complexity_desc", "Сложность: сложная → простая"),
)
COUNTER_DEFAULT_LIMIT = 25


def filter_counter_candidates(
    candidates: list[CounterCandidate],
    profiles: dict[int, Any],
    query: str = "",
    attribute: str = "",
    attack_type: str = "",
    complexity: int = 0,
    sort_mode: str = "recommendation",
) -> list[CounterCandidate]:
    """Filter and sort a counter list before it is put into the table."""
    needle = query.strip().casefold()
    filtered: list[CounterCandidate] = []
    for candidate in candidates:
        profile = profiles.get(candidate.hero_id)
        if profile is None:
            continue
        if needle and needle not in candidate.name.casefold():
            continue
        if attribute and getattr(profile, "primary_attr", "") != attribute:
            continue
        if attack_type and getattr(profile, "attack_type", "") != attack_type:
            continue
        rating = int(getattr(profile, "complexity", 0) or 0)
        if complexity and rating != complexity:
            continue
        filtered.append(candidate)

    if sort_mode == "name":
        filtered.sort(key=lambda item: (item.name.casefold(), -item.score))
    elif sort_mode in ("complexity_asc", "complexity_desc"):
        descending = sort_mode == "complexity_desc"

        def complexity_key(item: CounterCandidate) -> tuple[int, str]:
            profile = profiles.get(item.hero_id)
            rating = int(getattr(profile, "complexity", 0) or 0)
            rank = rating if rating else 99
            if descending and rating:
                rank = -rating
            return rank, item.name.casefold()

        filtered.sort(key=complexity_key)
    else:
        filtered.sort(key=lambda item: item.score, reverse=True)
    return filtered


def filter_proposed_counters(
    candidates: list[CounterCandidate],
    profiles: dict[int, Any],
    *,
    limit: int = COUNTER_DEFAULT_LIMIT,
    query: str = "",
    attribute: str = "",
    attack_type: str = "",
    complexity: int = 0,
    sort_mode: str = "recommendation",
) -> list[CounterCandidate]:
    """Refine only the heroes that are already proposed in the default view.

    Filtering the whole candidate pool looks like the app suddenly recommends
    every hero in the game.  The table is a shortlist, so filters and sorting
    must stay inside its top-N entries.
    """
    shortlist = sorted(candidates, key=lambda item: item.score, reverse=True)[
        : max(0, limit)
    ]
    return filter_counter_candidates(
        shortlist,
        profiles,
        query=query,
        attribute=attribute,
        attack_type=attack_type,
        complexity=complexity,
        sort_mode=sort_mode,
    )


def format_clock(seconds: int | None) -> str:
    if seconds is None:
        return "—"
    sign = "-" if seconds < 0 else ""
    seconds = abs(int(seconds))
    return f"{sign}{seconds // 60}:{seconds % 60:02d}"


def format_cost(cost: int) -> str:
    return f"{cost:,}".replace(",", " ") + "g"


def cv_to_pixmap(frame: np.ndarray) -> QPixmap:
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]
    image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format_RGB888).copy()
    return QPixmap.fromImage(image)


def cover_pixmap(source: QPixmap, width: int, height: int) -> QPixmap:
    if source.isNull() or width <= 0 or height <= 0:
        return QPixmap()
    scaled = source.scaled(
        width,
        height,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    return scaled.copy(x, y, width, height)


def placeholder_pixmap(width: int, height: int, seed: int = 0, text: str = "") -> QPixmap:
    pixmap = QPixmap(width, height)
    hue = (seed * 47) % 360
    pixmap.fill(QColor.fromHsv(hue, 60, 55))
    painter = QPainter(pixmap)
    painter.setPen(QColor(220, 228, 238))
    font = QFont("Segoe UI", max(8, min(16, height // 3)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, text or "?")
    painter.end()
    return pixmap


# ---------------------------------------------------------------------------
# Small reusable widgets
# ---------------------------------------------------------------------------


class PortraitLabel(QLabel):
    def __init__(self, width: int, height: int, radius: int = 6) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._placeholder = "?"
        self._radius = radius
        self.setFixedSize(width, height)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_image(self, pixmap: QPixmap, placeholder: str = "?") -> None:
        self._pixmap = pixmap
        self._placeholder = placeholder
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(self.rect(), self._radius, self._radius)
        painter.setClipPath(path)
        if not self._pixmap.isNull():
            painter.drawPixmap(self.rect(), self._pixmap)
        else:
            painter.fillRect(self.rect(), QColor("#1b2432"))
            painter.setPen(QColor("#8d9aab"))
            painter.setFont(QFont("Segoe UI", 11, QFont.Bold))
            painter.drawText(self.rect(), Qt.AlignCenter, self._placeholder)
        painter.end()


class HeroCard(QFrame):
    clicked = Signal(str, int)
    context_requested = Signal(str, int, QPoint)

    def __init__(self, faction: str, slot: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.faction = faction
        self.slot = slot
        self._hero_id: int | None = None
        self._locked = False
        self.setObjectName("HeroCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(56)
        self.setToolTip("Левый клик — выбрать героя вручную, правый — очистить/снять блокировку")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 8, 5)
        layout.setSpacing(8)
        self.portrait = PortraitLabel(72, 44, radius=6)
        layout.addWidget(self.portrait)
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)
        self.name_label = QLabel("Пусто")
        self.name_label.setObjectName("HeroName")
        self.name_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.score_label = QLabel("нажмите для выбора")
        self.score_label.setObjectName("HeroScore")
        self.score_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        text_layout.addWidget(self.name_label)
        text_layout.addWidget(self.score_label)
        layout.addLayout(text_layout, 1)

    @property
    def hero_id(self) -> int | None:
        return self._hero_id

    def set_data(
        self,
        hero_id: int | None,
        name: str,
        pixmap: QPixmap | None,
        score: float = 0.0,
        locked: bool = False,
        placeholder: str = "?",
    ) -> None:
        self._hero_id = hero_id
        self._locked = locked
        if hero_id and name:
            metrics = QFontMetrics(self.name_label.font())
            self.name_label.setText(metrics.elidedText(name, Qt.ElideRight, 148))
            self.name_label.setToolTip(name)
            self.score_label.setText(
                ("вручную" if locked else f"совпадение {score * 100:.0f}%")
            )
            self.portrait.set_image(pixmap or placeholder_pixmap(72, 44, hero_id, placeholder), placeholder)
        else:
            self.name_label.setText("Пусто")
            self.name_label.setToolTip("")
            self.score_label.setText("нажмите для выбора")
            self.portrait.set_image(QPixmap(), "?")
        self.setProperty("locked", "true" if locked else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.faction, self.slot)
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        self.context_requested.emit(self.faction, self.slot, event.globalPos())


class TeamPanel(QFrame):
    slot_clicked = Signal(str, int)
    slot_context = Signal(str, int, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Panel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.title_label = QLabel("Команда")
        self.title_label.setObjectName("SectionTitle")
        self.subtitle_label = QLabel("")
        self.subtitle_label.setObjectName("Subtitle")
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        self.cards: list[HeroCard] = []
        for slot in range(5):
            card = HeroCard("radiant", slot)
            card.clicked.connect(self.slot_clicked)
            card.context_requested.connect(self.slot_context)
            self.cards.append(card)
            layout.addWidget(card)

    def set_header(self, title: str, subtitle: str, color: str = "#e8edf5") -> None:
        self.title_label.setText(title)
        self.title_label.setStyleSheet(f"color: {color};")
        self.subtitle_label.setText(subtitle)

    def set_faction(self, faction: str) -> None:
        for card in self.cards:
            card.faction = faction

    def update_slots(
        self,
        heroes: list[int | None],
        names: dict[int, str],
        pixmaps: dict[int, QPixmap],
        scores: list[float],
        locked: list[bool],
    ) -> None:
        for slot, card in enumerate(self.cards):
            hero_id = heroes[slot] if slot < len(heroes) else None
            name = names.get(hero_id, "") if hero_id else ""
            card.set_data(
                hero_id,
                name,
                pixmaps.get(hero_id) if hero_id else None,
                scores[slot] if slot < len(scores) else 0.0,
                locked[slot] if slot < len(locked) else False,
                (name[:1] or "?"),
            )


class HeroPickerDialog(QDialog):
    def __init__(
        self,
        profiles: dict[int, Any],
        pixmap_for: Callable[[int], QPixmap | None],
        parent: QWidget | None = None,
        current: int | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Выбор героя")
        self.resize(760, 560)
        self.selected: int | None = current
        self._profiles = profiles
        layout = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск героя…")
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setIconSize(QSize(96, 54))
        self.list.setGridSize(QSize(116, 96))
        self.list.setSpacing(4)
        self.list.setUniformItemSizes(True)
        layout.addWidget(self.list, 1)

        for hero_id, profile in sorted(profiles.items(), key=lambda kv: kv[1].name.lower()):
            item = QListWidgetItem(profile.name)
            pixmap = pixmap_for(hero_id)
            if pixmap and not pixmap.isNull():
                item.setIcon(QIcon(cover_pixmap(pixmap, 96, 54)))
            else:
                item.setIcon(QIcon(placeholder_pixmap(96, 54, hero_id, profile.name[:1])))
            item.setData(Qt.UserRole, hero_id)
            item.setToolTip(profile.name)
            self.list.addItem(item)
            if hero_id == current:
                self.list.setCurrentItem(item)

        buttons = QHBoxLayout()
        clear_button = QPushButton("Очистить слот")
        cancel_button = QPushButton("Отмена")
        select_button = QPushButton("Выбрать")
        select_button.setObjectName("Accent")
        buttons.addWidget(clear_button)
        buttons.addStretch(1)
        buttons.addWidget(cancel_button)
        buttons.addWidget(select_button)
        layout.addLayout(buttons)

        self.search.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda _item: self._accept_selection())
        self.list.itemActivated.connect(lambda _item: self._accept_selection())
        select_button.clicked.connect(self._accept_selection)
        cancel_button.clicked.connect(self.reject)
        clear_button.clicked.connect(self._clear)
        self.search.setFocus()

    def _filter(self, text: str) -> None:
        query = text.strip().lower()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(bool(query) and query not in item.text().lower())

    def _accept_selection(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.selected = int(item.data(Qt.UserRole))
        self.accept()

    def _clear(self) -> None:
        self.selected = None
        self.accept()


class ItemList(QListWidget):
    """Icon-only item grid with all text available on hover."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemGrid")
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setIconSize(ITEM_ICON_SIZE)
        self.setGridSize(ITEM_GRID_SIZE)
        self.setSpacing(2)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setSelectionMode(QListWidget.NoSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setToolTip("Наведите курсор на предмет, чтобы увидеть название, цену и причину.")
        self._empty = QLabel("", self.viewport())
        self._empty.setObjectName("Muted")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._empty.hide()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._empty.setGeometry(self.viewport().rect().adjusted(12, 12, -12, -12))

    def clear(self) -> None:
        if hasattr(self, "_empty"):
            self._empty.hide()
        super().clear()

    def show_empty(self, text: str) -> None:
        self.clear()
        self._empty.setText(text)
        self._empty.setGeometry(self.viewport().rect().adjusted(12, 12, -12, -12))
        self._empty.raise_()
        self._empty.show()

    @staticmethod
    def _tooltip(
        suggestion: ItemSuggestion | Any,
        suffix: str,
        extra: str,
    ) -> str:
        name = getattr(suggestion, "name", str(suggestion))
        cost = int(getattr(suggestion, "cost", 0) or 0)
        tier = getattr(suggestion, "tier", None)
        neutral = bool(getattr(suggestion, "neutral", False)) or tier is not None
        reason = str(getattr(suggestion, "reason", "") or "")
        lines = [name]
        if neutral:
            lines.append(f"Нейтральный предмет · T{tier}" if tier else "Нейтральный предмет")
        elif cost:
            lines.append(f"Стоимость: {format_cost(cost)}")
        if suffix:
            lines.append(suffix)
        if reason:
            lines.append(reason)
        if extra:
            lines.append(extra)
        return "\n".join(lines)

    def add_suggestion(
        self,
        suggestion: ItemSuggestion | Any,
        icon: QPixmap | None = None,
        suffix: str = "",
        tooltip: str = "",
    ) -> None:
        name = getattr(suggestion, "name", str(suggestion))
        item = QListWidgetItem()
        item.setData(Qt.UserRole, getattr(suggestion, "item_id", None))
        item.setData(Qt.AccessibleTextRole, name)
        if icon and not icon.isNull():
            item.setIcon(QIcon(icon))
        else:
            item.setIcon(
                QIcon(placeholder_pixmap(88, 64, hash(name) % 997, name[:1] or "?"))
            )
        item.setToolTip(self._tooltip(suggestion, suffix, tooltip))
        item.setSizeHint(ITEM_GRID_SIZE)
        self._empty.hide()
        self.addItem(item)


class CounterTable(QTableWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setColumnCount(5)
        self.setHorizontalHeaderLabels(["Герой", "Счёт", "Преимущество", "Надёжность", "Почему"])
        self.verticalHeader().setVisible(False)
        self.setShowGrid(False)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QTableWidget.SelectRows)
        self.setEditTriggers(QTableWidget.NoEditTriggers)
        self.setWordWrap(True)
        self.verticalHeader().setDefaultSectionSize(46)
        header = self.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)

    def set_candidates(
        self,
        candidates: list[CounterCandidate],
        pixmap_for: Callable[[int], QPixmap | None],
        profiles: dict[int, Any],
    ) -> None:
        self.setRowCount(0)
        for row, candidate in enumerate(candidates):
            self.insertRow(row)
            hero_item = QTableWidgetItem(candidate.name)
            pixmap = pixmap_for(candidate.hero_id)
            if pixmap and not pixmap.isNull():
                hero_item.setIcon(QIcon(cover_pixmap(pixmap, 28, 36)))
            profile = profiles.get(candidate.hero_id)
            if profile is not None:
                attr = ATTRIBUTE_LABELS.get(getattr(profile, "primary_attr", ""), "")
                attack = ATTACK_LABELS.get(getattr(profile, "attack_type", ""), "")
                rating = int(getattr(profile, "complexity", 0) or 0)
                complexity = COMPLEXITY_NAMES.get(rating, "неизвестна")
                roles = ", ".join(getattr(profile, "roles", ())) or "—"
                hero_item.setToolTip(
                    f"{candidate.name}\n{attr} · {attack}\n"
                    f"Сложность: {complexity}\nРоли: {roles}"
                )
            else:
                hero_item.setToolTip(candidate.name)
            self.setItem(row, 0, hero_item)

            score_item = QTableWidgetItem(f"{candidate.score:.1f}")
            score_item.setTextAlignment(Qt.AlignCenter)
            score_item.setToolTip("Итоговый балл: матчапы, нижняя граница, мета и синергия")
            self.setItem(row, 1, score_item)

            adv_item = QTableWidgetItem(f"{candidate.advantage * 100:+.1f}%")
            adv_item.setTextAlignment(Qt.AlignCenter)
            adv_item.setToolTip(
                "Среднее преимущество против выбранных врагов (после сглаживания)"
            )
            self.setItem(row, 2, adv_item)

            confidence = candidate.confidence
            conf_item = QTableWidgetItem(CONFIDENCE_LABELS.get(confidence, confidence))
            conf_item.setTextAlignment(Qt.AlignCenter)
            conf_item.setForeground(QColor(CONFIDENCE_COLORS.get(confidence, "#e8edf5")))
            conf_item.setToolTip(
                "Надёжность зависит от числа публичных матчей OpenDota по парам героев"
            )
            self.setItem(row, 3, conf_item)

            reason = "; ".join(candidate.reasons[:3])
            reason_item = QTableWidgetItem(reason)
            details = []
            for enemy_id, (adv, games) in candidate.per_enemy.items():
                enemy = profiles.get(enemy_id)
                if enemy:
                    details.append(
                        f"{enemy.name}: {adv * 100:+.1f}% "
                        f"({games} {plural_ru(games, 'матч', 'матча', 'матчей')})"
                    )
            reason_item.setToolTip("\n".join(details) or reason)
            self.setItem(row, 4, reason_item)

    def clear_rows(self) -> None:
        self.setRowCount(0)


class CalibrationWindow(QWidget):
    saved = Signal(dict)
    cancelled = Signal()

    def __init__(
        self,
        screenshot: QPixmap,
        screen: QScreen,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.screenshot = screenshot
        self.screen = screen
        self.rects: list[QRect] = []
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setFocusPolicy(Qt.StrongFocus)
        self._reset_btn = QPushButton("Заново", self)
        self._swap_btn = QPushButton("Поменять местами", self)
        self._save_btn = QPushButton("Сохранить", self)
        self._save_btn.setObjectName("Accent")
        self._cancel_btn = QPushButton("Отмена", self)
        self._reset_btn.clicked.connect(self._reset)
        self._swap_btn.clicked.connect(self._swap)
        self._save_btn.clicked.connect(self._save)
        self._cancel_btn.clicked.connect(self._cancel)
        self._update_buttons()
        try:
            self.setScreen(screen)
        except Exception:
            pass
        self.setGeometry(screen.geometry())
        self.setWindowTitle("Калибровка зон драфта")
        self.showFullScreen()

    def _update_buttons(self) -> None:
        self._save_btn.setEnabled(len(self.rects) >= 2)
        self._swap_btn.setEnabled(len(self.rects) >= 2)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        width = 180
        total = width * 4 + 10 * 3
        x = max(10, (self.width() - total) // 2)
        y = self.height() - 58
        for index, button in enumerate(
            (self._reset_btn, self._swap_btn, self._save_btn, self._cancel_btn)
        ):
            button.setGeometry(x + index * (width + 10), y, width, 38)
            button.raise_()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if not self.screenshot.isNull():
            painter.drawPixmap(self.rect(), self.screenshot)
        else:
            painter.fillRect(self.rect(), QColor("#0b0f16"))
        painter.fillRect(self.rect(), QColor(5, 8, 12, 120))

        # Title / instruction card.
        card = QRect(24, 20, self.width() - 48, 86)
        painter.setBrush(QColor(17, 24, 35, 235))
        painter.setPen(QPen(QColor("#2c394c"), 1))
        painter.drawRoundedRect(card, 10, 10)
        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 16, QFont.Bold))
        painter.drawText(QRect(card.x() + 18, card.y() + 10, card.width() - 36, 26), Qt.AlignLeft, "Калибровка экрана драфта")
        painter.setPen(QColor("#b9c6d6"))
        painter.setFont(QFont("Segoe UI", 10))
        step = (
            "1. Обведите рамкой пять портретов Radiant (светлая сторона)."
            if not self.rects
            else "2. Теперь обведите рамкой пять портретов Dire (тёмная сторона)."
            if len(self.rects) == 1
            else "Готово. Если рамки неверные — «Заново». Сохраните результат."
        )
        painter.drawText(
            QRect(card.x() + 18, card.y() + 38, card.width() - 36, 20),
            Qt.AlignLeft,
            step,
        )
        painter.setPen(QColor("#8d9aab"))
        painter.drawText(
            QRect(card.x() + 18, card.y() + 58, card.width() - 36, 20),
            Qt.AlignLeft,
            "Обводите только портреты, без имён и панелей. Режим Dota 2 — «Окно без рамки».",
        )

        colors = [QColor("#6cd48a"), QColor("#ec6b6b")]
        labels = ["RADIANT", "DIRE"]
        for index, rect in enumerate(self.rects[:2]):
            pen = QPen(colors[index], 3)
            painter.setPen(pen)
            painter.setBrush(QColor(colors[index].red(), colors[index].green(), colors[index].blue(), 25))
            painter.drawRect(rect)
            label_rect = QRect(rect.x(), max(0, rect.y() - 24), 92, 22)
            painter.fillRect(label_rect, colors[index])
            painter.setPen(QColor("#0b0f16"))
            painter.setFont(QFont("Segoe UI", 10, QFont.Bold))
            painter.drawText(label_rect, Qt.AlignCenter, labels[index])

        if self._drag_start and self._drag_current:
            rect = QRect(self._drag_start, self._drag_current).normalized()
            painter.setPen(QPen(QColor("#f0b35c"), 2, Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)

        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton and len(self.rects) < 2:
            self._drag_start = event.position().toPoint()
            self._drag_current = self._drag_start
            self.update()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_start is not None:
            self._drag_current = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.LeftButton or self._drag_start is None:
            return
        rect = QRect(self._drag_start, event.position().toPoint()).normalized()
        self._drag_start = None
        self._drag_current = None
        if rect.width() >= 24 and rect.height() >= 24 and len(self.rects) < 2:
            self.rects.append(rect)
            self._update_buttons()
        self.update()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key_Escape:
            self._cancel()
            return
        if event.key() == Qt.Key_R:
            self._reset()
            return
        super().keyPressEvent(event)

    def _reset(self) -> None:
        self.rects.clear()
        self._drag_start = None
        self._drag_current = None
        self._update_buttons()
        self.update()

    def _swap(self) -> None:
        if len(self.rects) >= 2:
            self.rects[0], self.rects[1] = self.rects[1], self.rects[0]
            self.update()

    def _save(self) -> None:
        if len(self.rects) < 2:
            return
        boxes: dict[str, Rect] = {}
        for side, rect in zip(("radiant", "dire"), self.rects):
            boxes[side] = Rect.from_px(
                rect.x(), rect.y(), rect.width(), rect.height(), self.width(), self.height()
            )
        self.saved.emit(boxes)
        self.close()

    def _cancel(self) -> None:
        self.cancelled.emit()
        self.close()


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    def __init__(self, config: Config) -> None:
        super().__init__()
        self.cfg = config
        self._offline = os.environ.get("DDC_OFFLINE") == "1"
        self._no_gsi = os.environ.get("DDC_NO_GSI") == "1"
        self.tracker = DraftTracker()
        self.snapshot = GSISnapshot()
        self.own_hero_id: int | None = None
        self.own_manual = False
        self._updating_own_combo = False
        self._updating_side_combo = False
        self._updating_role_combo = False
        self._pixmap_cache: dict[int, QPixmap] = {}
        self._item_pixmap_cache: dict[str, QPixmap] = {}
        self._last_build: BuildResult | None = None
        self._last_neutral: NeutralResult | None = None
        self._last_counter: CounterResult | None = None
        self._counter_candidates: list[CounterCandidate] = []
        self._draft_frozen = False
        self._freeze_reason = ""
        self._scan_inflight = False
        self._scan_task: ScanTask | None = None
        self._scan_opacity_before: float | None = None
        self._open_debug_after_scan = False
        self._last_scan_text = "сканов ещё не было"
        self._last_neutral_clock: int | None = None
        self._last_effective_side = ""
        self._dota_root = find_dota_root()
        self._monitors: list[dict] = []
        self._calibration_window: CalibrationWindow | None = None
        self._first_show = True

        self.setWindowTitle("Dota Draft Copilot")
        self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(config.always_on_top))
        self.resize(config.window_w, config.window_h)
        if config.window_x >= 0 and config.window_y >= 0:
            self.move(config.window_x, config.window_y)
        self.setWindowOpacity(max(0.6, min(1.0, config.opacity)))

        self.service = DataService(config, allow_network=not self._offline)
        self.service.status.connect(self._on_status)
        self.service.core_ready.connect(self._on_core_ready)
        self.service.assets_ready.connect(self._on_assets_ready)
        self.service.error.connect(self._on_data_error)
        self.service.matchups_changed.connect(self._schedule_refresh)
        self.service.popularity_changed.connect(self._on_popularity_changed)
        self.service.assets_progress.connect(self._on_assets_progress)
        self.service.load_progress.connect(self._on_assets_progress)

        self.gsi_state = GSIState()
        self.gsi_server: GSIServer | None = None

        self._build_ui()
        if not self._no_gsi:
            self.gsi_server = GSIServer(config.gsi_port, self.gsi_state)
            try:
                self.gsi_server.start()
            except OSError as exc:
                self.gsi_server = None
                self._on_status(f"GSI-сервер не запущен: {exc}")

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_all)

        self.gsi_timer = QTimer(self)
        self.gsi_timer.setInterval(500)
        self.gsi_timer.timeout.connect(self._tick_gsi)
        self.gsi_timer.start()

        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self._auto_scan)
        self._apply_scan_interval()

        if self._offline:
            self._on_status("Офлайн-режим: только локальный кэш, без запросов в сеть.")
            self._update_banner()
            self._update_data_tab()
        self.service.start()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("Root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(7)

        header = QFrame()
        header.setObjectName("Header")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 9, 12, 9)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("Dota Draft Copilot")
        title.setObjectName("Title")
        self.status_label = QLabel("Запуск…")
        self.status_label.setObjectName("Subtitle")
        self.status_label.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(self.status_label)
        header_layout.addLayout(title_box, 1)
        self.freeze_badge = QLabel("Драфт заморожен")
        self.freeze_badge.setObjectName("Good")
        self.freeze_badge.hide()
        header_layout.addWidget(self.freeze_badge)
        self.patch_label = QLabel("")
        self.patch_label.setObjectName("Badge")
        header_layout.addWidget(self.patch_label)
        self.scan_button = QPushButton("Скан")
        self.scan_button.setObjectName("Accent")
        self.scan_button.setToolTip("Сделать снимок экрана и распознать драфт сейчас")
        self.scan_button.clicked.connect(lambda: self._scan_now(save_debug=False))
        header_layout.addWidget(self.scan_button)
        self.new_draft_button = QPushButton("Новый драфт")
        self.new_draft_button.setToolTip(
            "Очистить распознанный драфт и снова включить сканирование"
        )
        self.new_draft_button.clicked.connect(lambda: self._start_new_draft(automatic=False))
        header_layout.addWidget(self.new_draft_button)
        self.calibrate_button = QPushButton("Калибровка")
        self.calibrate_button.clicked.connect(self._start_calibration)
        header_layout.addWidget(self.calibrate_button)
        layout.addWidget(header)

        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 7, 10, 7)
        self.banner_label = QLabel("")
        self.banner_label.setWordWrap(True)
        banner_layout.addWidget(self.banner_label, 1)
        self.banner_button = QPushButton("")
        self.banner_button.clicked.connect(self._banner_action)
        banner_layout.addWidget(self.banner_button)
        self.banner.hide()
        layout.addWidget(self.banner)

        controls = QFrame()
        controls.setObjectName("Panel")
        controls_layout = QGridLayout(controls)
        controls_layout.setContentsMargins(9, 7, 9, 7)
        controls_layout.setHorizontalSpacing(7)
        controls_layout.setVerticalSpacing(5)

        controls_layout.addWidget(QLabel("Сторона:"), 0, 0)
        self.side_combo = QComboBox()
        self.side_combo.addItem("Авто (GSI)", "")
        self.side_combo.addItem("Radiant", "radiant")
        self.side_combo.addItem("Dire", "dire")
        self.side_combo.setToolTip("Если GSI недоступен, выберите сторону вручную")
        self.side_combo.currentIndexChanged.connect(self._on_side_changed)
        controls_layout.addWidget(self.side_combo, 0, 1)
        self.side_actual = QLabel("")
        self.side_actual.setObjectName("Muted")
        controls_layout.addWidget(self.side_actual, 0, 2)

        controls_layout.addWidget(QLabel("Роль:"), 0, 3)
        self.role_combo = QComboBox()
        for role in ROLES:
            self.role_combo.addItem(ROLE_LABELS.get(role, role), role)
        self.role_combo.setToolTip("Фильтр рекомендованных героев по вашей позиции")
        self.role_combo.currentIndexChanged.connect(self._on_role_changed)
        controls_layout.addWidget(self.role_combo, 0, 4)

        controls_layout.addWidget(QLabel("Мой герой:"), 1, 0)
        self.own_combo = QComboBox()
        self.own_combo.setMaxVisibleItems(18)
        self.own_combo.setIconSize(QSize(30, 17))
        self.own_combo.setMinimumWidth(230)
        self.own_combo.setToolTip("Обычно определяется через GSI. Можно выбрать вручную.")
        self.own_combo.currentIndexChanged.connect(self._on_own_changed)
        controls_layout.addWidget(self.own_combo, 1, 1, 1, 3)
        self.own_reset_button = QPushButton("Из GSI")
        self.own_reset_button.setToolTip("Снова брать героя из GSI и снять ручной выбор")
        self.own_reset_button.clicked.connect(self._reset_own_from_gsi)
        controls_layout.addWidget(self.own_reset_button, 1, 4)
        layout.addWidget(controls)

        teams = QHBoxLayout()
        teams.setSpacing(7)
        self.ally_panel = TeamPanel()
        self.enemy_panel = TeamPanel()
        self.ally_panel.slot_clicked.connect(self._edit_slot)
        self.enemy_panel.slot_clicked.connect(self._edit_slot)
        self.ally_panel.slot_context.connect(self._slot_context)
        self.enemy_panel.slot_context.connect(self._slot_context)
        teams.addWidget(self.ally_panel, 1)
        teams.addWidget(self.enemy_panel, 1)
        layout.addLayout(teams)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_counter_tab(), "Контрпики")
        self.tabs.addTab(self._build_items_tab(), "Предметы")
        self.tabs.addTab(self._build_neutral_tab(), "Нейтралки")
        self.tabs.addTab(self._build_data_tab(), "Данные")
        self.tabs.addTab(self._build_settings_tab(), "Настройки")
        layout.addWidget(self.tabs, 1)

        self.footer = QLabel("")
        self.footer.setObjectName("Muted")
        self.footer.setWordWrap(True)
        layout.addWidget(self.footer)

        self._populate_own_combo()
        self._sync_config_controls()
        self._update_freeze_state()
        self._update_banner()

    def _build_counter_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.counter_note = QLabel("Загрузка данных…")
        self.counter_note.setObjectName("Muted")
        self.counter_note.setWordWrap(True)
        layout.addWidget(self.counter_note)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(5)
        self.counter_search = QLineEdit()
        self.counter_search.setPlaceholderText("Поиск героя…")
        self.counter_search.setClearButtonEnabled(True)
        self.counter_search.setMinimumWidth(150)
        filter_row.addWidget(self.counter_search, 2)

        self.counter_sort = QComboBox()
        for value, label in COUNTER_SORT_LABELS:
            self.counter_sort.addItem(label, value)
        filter_row.addWidget(QLabel("Сортировка:"))
        filter_row.addWidget(self.counter_sort, 1)

        self.counter_reset = QPushButton("Сбросить")
        self.counter_reset.setToolTip("Сбросить поиск, фильтры и порядок строк")
        filter_row.addWidget(self.counter_reset)
        layout.addLayout(filter_row)

        filter_row2 = QHBoxLayout()
        filter_row2.setSpacing(5)
        self.counter_attr = QComboBox()
        for value, label in ATTRIBUTE_LABELS.items():
            self.counter_attr.addItem(label, value)
        filter_row2.addWidget(self.counter_attr)

        self.counter_attack = QComboBox()
        for value, label in ATTACK_LABELS.items():
            self.counter_attack.addItem(label, value)
        filter_row2.addWidget(self.counter_attack)

        self.counter_complexity = QComboBox()
        for value, label in COMPLEXITY_LABELS.items():
            self.counter_complexity.addItem(label, value)
        filter_row2.addWidget(self.counter_complexity)

        self.counter_filter_label = QLabel("")
        self.counter_filter_label.setObjectName("Muted")
        self.counter_filter_label.setToolTip(
            "Фильтры и сортировка действуют только по топ-25 предложенным героям."
        )
        filter_row2.addWidget(self.counter_filter_label, 1)
        layout.addLayout(filter_row2)

        self.counter_table = CounterTable()
        layout.addWidget(self.counter_table, 1)
        self.needs_label = QLabel("")
        self.needs_label.setObjectName("Muted")
        self.needs_label.setWordWrap(True)
        layout.addWidget(self.needs_label)

        self.counter_search.textChanged.connect(self._apply_counter_filters)
        self.counter_sort.currentIndexChanged.connect(self._apply_counter_filters)
        self.counter_attr.currentIndexChanged.connect(self._apply_counter_filters)
        self.counter_attack.currentIndexChanged.connect(self._apply_counter_filters)
        self.counter_complexity.currentIndexChanged.connect(self._apply_counter_filters)
        self.counter_reset.clicked.connect(self._reset_counter_filters)
        return tab

    def _build_items_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.item_note = QLabel("Выберите своего героя — появятся предметы.")
        self.item_note.setObjectName("Muted")
        self.item_note.setWordWrap(True)
        layout.addWidget(self.item_note)
        self.item_tabs = QTabWidget()
        self.item_lists: dict[str, ItemList] = {}
        for phase in ("start", "early", "mid", "late", "situational"):
            widget = ItemList()
            self.item_lists[phase] = widget
            self.item_tabs.addTab(widget, PHASE_LABELS[phase])
        layout.addWidget(self.item_tabs, 1)
        return tab

    def _build_neutral_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.neutral_note = QLabel("Нейтральные предметы появятся после выбора вашего героя.")
        self.neutral_note.setObjectName("Muted")
        self.neutral_note.setWordWrap(True)
        layout.addWidget(self.neutral_note)
        self.neutral_tabs = QTabWidget()
        self.neutral_lists: dict[int, ItemList] = {}
        for tier in range(1, 6):
            widget = ItemList()
            self.neutral_lists[tier] = widget
            self.neutral_tabs.addTab(widget, f"T{tier}")
        layout.addWidget(self.neutral_tabs, 1)
        return tab

    def _build_data_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(5)
        self.data_view = QTextBrowser()
        self.data_view.setOpenExternalLinks(False)
        layout.addWidget(self.data_view, 1)
        buttons = QHBoxLayout()
        refresh_button = QPushButton("Обновить данные")
        refresh_button.clicked.connect(self._reload_data)
        test_button = QPushButton("Проверить распознавание")
        test_button.clicked.connect(self._test_scan)
        folder_button = QPushButton("Папка данных")
        folder_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(DATA_DIR))))
        buttons.addWidget(refresh_button)
        buttons.addWidget(test_button)
        buttons.addWidget(folder_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        return tab

    def _build_settings_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        layout = QVBoxLayout(inner)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(9)

        gsi_group = QGroupBox("Game State Integration")
        gsi_layout = QVBoxLayout(gsi_group)
        self.gsi_path_label = QLabel("")
        self.gsi_path_label.setWordWrap(True)
        self.gsi_path_label.setObjectName("Muted")
        gsi_layout.addWidget(self.gsi_path_label)
        gsi_buttons = QHBoxLayout()
        self.gsi_install_button = QPushButton("Установить / обновить GSI")
        self.gsi_install_button.clicked.connect(self._install_gsi)
        gsi_buttons.addWidget(self.gsi_install_button)
        gsi_buttons.addStretch(1)
        gsi_layout.addLayout(gsi_buttons)
        gsi_hint = QLabel(
            "Файл кладётся в game/dota/cfg/gamestate_integration/. "
            "После установки перезапустите Dota 2."
        )
        gsi_hint.setObjectName("Muted")
        gsi_hint.setWordWrap(True)
        gsi_layout.addWidget(gsi_hint)
        layout.addWidget(gsi_group)

        calib_group = QGroupBox("Калибровка и распознавание")
        calib_layout = QVBoxLayout(calib_group)
        self.calib_label = QLabel("")
        self.calib_label.setWordWrap(True)
        self.calib_label.setObjectName("Muted")
        calib_layout.addWidget(self.calib_label)
        calib_buttons = QHBoxLayout()
        calib_button = QPushButton("Калибровать")
        calib_button.clicked.connect(self._start_calibration)
        reset_calib = QPushButton("Сбросить калибровку")
        reset_calib.clicked.connect(self._reset_calibration)
        test_scan = QPushButton("Тестовый скан")
        test_scan.clicked.connect(self._test_scan)
        calib_buttons.addWidget(calib_button)
        calib_buttons.addWidget(reset_calib)
        calib_buttons.addWidget(test_scan)
        calib_buttons.addStretch(1)
        calib_layout.addLayout(calib_buttons)
        self.auto_scan_check = QCheckBox("Автоматически сканировать экран")
        self.scan_in_draft_check = QCheckBox("Сканировать только во время драфта (по GSI)")
        self.transparent_check = QCheckBox("Делать окно прозрачным на время скана")
        self.debug_check = QCheckBox("Сохранять отладочный кадр при тестовом скане")
        calib_layout.addWidget(self.auto_scan_check)
        calib_layout.addWidget(self.scan_in_draft_check)
        calib_layout.addWidget(self.transparent_check)
        calib_layout.addWidget(self.debug_check)
        interval_row = QHBoxLayout()
        interval_row.addWidget(QLabel("Интервал скана, сек:"))
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.5, 10.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setDecimals(1)
        interval_row.addWidget(self.interval_spin)
        interval_row.addWidget(QLabel("Монитор:"))
        self.monitor_combo = QComboBox()
        interval_row.addWidget(self.monitor_combo, 1)
        calib_layout.addLayout(interval_row)
        layout.addWidget(calib_group)

        ui_group = QGroupBox("Окно")
        ui_layout = QVBoxLayout(ui_group)
        self.top_check = QCheckBox("Поверх других окон")
        ui_layout.addWidget(self.top_check)
        opacity_row = QHBoxLayout()
        opacity_row.addWidget(QLabel("Прозрачность:"))
        self.opacity_slider = QSlider(Qt.Horizontal)
        self.opacity_slider.setRange(60, 100)
        opacity_row.addWidget(self.opacity_slider, 1)
        self.opacity_value = QLabel("96%")
        opacity_row.addWidget(self.opacity_value)
        ui_layout.addLayout(opacity_row)
        layout.addWidget(ui_group)

        neutral_group = QGroupBox("Время появления нейтральных предметов")
        neutral_layout = QGridLayout(neutral_group)
        self.neutral_spins: dict[int, QSpinBox] = {}
        for tier in range(1, 6):
            neutral_layout.addWidget(QLabel(f"T{tier}:"), (tier - 1) // 2, ((tier - 1) % 2) * 2)
            spin = QSpinBox()
            spin.setRange(0, 7200)
            spin.setSingleStep(60)
            spin.setSuffix(" сек")
            self.neutral_spins[tier] = spin
            neutral_layout.addWidget(spin, (tier - 1) // 2, ((tier - 1) % 2) * 2 + 1)
        neutral_hint = QLabel("Значения по умолчанию взяты из neutral_items.txt установленного клиента.")
        neutral_hint.setObjectName("Muted")
        neutral_hint.setWordWrap(True)
        neutral_layout.addWidget(neutral_hint, 3, 0, 1, 4)
        layout.addWidget(neutral_group)

        save_button = QPushButton("Сохранить настройки")
        save_button.setObjectName("Accent")
        save_button.clicked.connect(self._save_settings)
        layout.addWidget(save_button)
        layout.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    # ------------------------------------------------------------------
    # Initialisation helpers
    # ------------------------------------------------------------------
    def _populate_own_combo(self) -> None:
        self._updating_own_combo = True
        self.own_combo.clear()
        self.own_combo.addItem("— не выбран —", None)
        if self.service.profiles:
            for hero_id, profile in sorted(
                self.service.profiles.items(), key=lambda kv: kv[1].name.lower()
            ):
                pixmap = self._hero_pixmap(hero_id)
                icon = QIcon(cover_pixmap(pixmap, 30, 17)) if not pixmap.isNull() else QIcon()
                self.own_combo.addItem(icon, profile.name, hero_id)
        self._updating_own_combo = False
        self._select_combo_hero(self.own_combo, self.own_hero_id)

    @staticmethod
    def _select_combo_hero(combo: QComboBox, hero_id: int | None) -> None:
        index = combo.findData(hero_id)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _sync_config_controls(self) -> None:
        self._updating_side_combo = True
        index = self.side_combo.findData(self.cfg.side_override)
        self.side_combo.setCurrentIndex(index if index >= 0 else 0)
        self._updating_side_combo = False
        self._updating_role_combo = True
        index = self.role_combo.findData(self.cfg.role)
        self.role_combo.setCurrentIndex(index if index >= 0 else 0)
        self._updating_role_combo = False
        self.auto_scan_check.setChecked(self.cfg.auto_scan)
        self.scan_in_draft_check.setChecked(self.cfg.scan_only_in_draft)
        self.transparent_check.setChecked(self.cfg.transparent_on_scan)
        self.debug_check.setChecked(self.cfg.show_debug_frames)
        self.interval_spin.setValue(self.cfg.scan_interval)
        self.top_check.setChecked(self.cfg.always_on_top)
        self.opacity_slider.setValue(int(round(self.cfg.opacity * 100)))
        self.opacity_value.setText(f"{int(round(self.cfg.opacity * 100))}%")
        self.opacity_slider.valueChanged.connect(
            lambda value: self.opacity_value.setText(f"{value}%")
        )
        self._populate_monitors()
        for tier, spin in self.neutral_spins.items():
            spin.setValue(int(self.cfg.neutral_times.get(str(tier), 0) or 0))

    def _populate_monitors(self) -> None:
        self.monitor_combo.clear()
        try:
            capture = ScreenCapture(self.cfg.monitor)
            self._monitors = capture.monitors()
            capture.close()
        except Exception:
            self._monitors = []
        if not self._monitors:
            self.monitor_combo.addItem("Основной экран", 1)
        else:
            for index, monitor in enumerate(self._monitors):
                if index == 0:
                    label = f"Все экраны ({monitor.get('width', 0)}×{monitor.get('height', 0)})"
                else:
                    label = (
                        f"Монитор {index}: {monitor.get('width', 0)}×{monitor.get('height', 0)} "
                        f"({monitor.get('left', 0)},{monitor.get('top', 0)})"
                    )
                self.monitor_combo.addItem(label, index)
        index = self.monitor_combo.findData(self.cfg.monitor)
        self.monitor_combo.setCurrentIndex(index if index >= 0 else 0)

    # ------------------------------------------------------------------
    # Data events
    # ------------------------------------------------------------------
    def _on_status(self, message: str) -> None:
        self.status_label.setText(message)
        self._update_banner()

    def _on_data_error(self, message: str) -> None:
        self.status_label.setText(message)
        self._update_data_tab()

    def _on_core_ready(self) -> None:
        self._populate_own_combo()
        if not self.own_manual and self.snapshot.hero_id:
            self.own_hero_id = self.snapshot.hero_id
            self._select_combo_hero(self.own_combo, self.own_hero_id)
        self._refresh_team_panels()
        self._schedule_refresh(120)
        self._update_data_tab()
        self._update_banner()
        self._update_patch_label()

    def _on_assets_ready(self) -> None:
        self._pixmap_cache.clear()
        self._item_pixmap_cache.clear()
        self._populate_own_combo()
        self._refresh_team_panels()
        self._schedule_refresh(120)
        self._update_banner()

    def _on_assets_progress(self, done: int, total: int) -> None:
        if total:
            self.status_label.setText(f"Загрузка изображений: {done}/{total}")

    def _on_popularity_changed(self, _hero_id: int, _data: dict) -> None:
        self._schedule_refresh(80)

    def _schedule_refresh(self, delay: int = 180) -> None:
        if self._refresh_timer.isActive():
            self._refresh_timer.stop()
        self._refresh_timer.start(delay)

    # ------------------------------------------------------------------
    # GSI
    # ------------------------------------------------------------------
    def _tick_gsi(self) -> None:
        snapshot = self.gsi_state.snapshot()
        previous = self.snapshot
        self.snapshot = snapshot

        state_changed = snapshot.game_state != previous.game_state
        if state_changed and snapshot.game_state == "DOTA_GAMERULES_STATE_HERO_SELECTION":
            if self._draft_frozen or self._has_picks():
                self._start_new_draft(automatic=True)
        if snapshot.after_picks and not self._draft_frozen and self._has_picks():
            self._freeze_draft("клиент перешёл к показу команд")

        if snapshot.hero_id and not self.own_manual and snapshot.hero_id != self.own_hero_id:
            self.own_hero_id = snapshot.hero_id
            self._updating_own_combo = True
            self._select_combo_hero(self.own_combo, self.own_hero_id)
            self._updating_own_combo = False
            self._schedule_refresh()

        side = self.cfg.side_override or snapshot.team
        if side != self._last_effective_side:
            self._last_effective_side = side
            self._refresh_team_panels()
            self._schedule_refresh()
        self._refresh_side_labels()

        if snapshot.connected != previous.connected or state_changed:
            if snapshot.connected:
                if self._draft_frozen:
                    self.status_label.setText(self._freeze_status_text())
                else:
                    state = snapshot.game_state or "меню/драфт"
                    self.status_label.setText(f"GSI подключён · {state}")
            else:
                self.status_label.setText("GSI не подключён — сторона и герой вручную")
            self._update_banner()

        if snapshot.clock is not None and self._last_neutral_clock is not None:
            if abs(snapshot.clock - self._last_neutral_clock) >= 30:
                self._last_neutral_clock = snapshot.clock
                self._schedule_refresh(60)
        elif snapshot.clock is not None:
            self._last_neutral_clock = snapshot.clock

    def _refresh_side_labels(self) -> None:
        side = self.cfg.side_override or self.snapshot.team
        if side:
            self.side_actual.setText(f"сейчас: {SIDE_SHORT.get(side, side)}")
            self.side_actual.setStyleSheet(f"color: {'#6cd48a' if side == 'radiant' else '#ec6b6b'};")
        else:
            self.side_actual.setText("сторона не определена")
            self.side_actual.setStyleSheet("color: #f0b35c;")

    # ------------------------------------------------------------------
    # Draft / team panels
    # ------------------------------------------------------------------
    def _effective_side(self) -> str:
        return self.cfg.side_override or self.snapshot.team

    def _faction_ids(self, side: str) -> list[int]:
        return [hero_id for hero_id in self.tracker.slots(side) if hero_id]

    def _ally_enemy_ids(self) -> tuple[list[int], list[int]]:
        side = self._effective_side()
        if side == "radiant":
            return self._faction_ids("radiant"), self._faction_ids("dire")
        if side == "dire":
            return self._faction_ids("dire"), self._faction_ids("radiant")
        return [], []

    def _refresh_team_panels(self) -> None:
        side = self._effective_side()
        if side:
            ally_faction = side
            enemy_faction = "dire" if side == "radiant" else "radiant"
            self.ally_panel.set_header(
                "Мы",
                f"{SIDE_LABELS[ally_faction]} · ваш состав",
                "#6cd48a" if ally_faction == "radiant" else "#ec6b6b",
            )
            self.enemy_panel.set_header(
                "Враги",
                f"{SIDE_LABELS[enemy_faction]} · цели для контрпиков",
                "#ec6b6b" if enemy_faction == "dire" else "#6cd48a",
            )
        else:
            ally_faction = "radiant"
            enemy_faction = "dire"
            self.ally_panel.set_header(
                "Radiant", "сторона не определена — выберите вручную", "#6cd48a"
            )
            self.enemy_panel.set_header(
                "Dire", "сторона не определена — выберите вручную", "#ec6b6b"
            )
        self.ally_panel.set_faction(ally_faction)
        self.enemy_panel.set_faction(enemy_faction)
        for panel, faction in ((self.ally_panel, ally_faction), (self.enemy_panel, enemy_faction)):
            state = self.tracker.state(faction)
            panel.update_slots(
                state.heroes,
                {hero_id: profile.name for hero_id, profile in self.service.profiles.items()},
                {hero_id: self._hero_pixmap(hero_id) for hero_id in self.service.profiles},
                state.scores,
                state.locked,
            )
        self._update_freeze_state()

    def _hero_pixmap(self, hero_id: int) -> QPixmap:
        cached = self._pixmap_cache.get(hero_id)
        if cached is not None:
            return cached
        path = CACHE_DIR / "heroes" / f"{hero_id}.png"
        pixmap = QPixmap(str(path)) if path.exists() else QPixmap()
        if pixmap.isNull():
            name = self.service.profiles.get(hero_id).name if hero_id in self.service.profiles else ""
            pixmap = placeholder_pixmap(72, 44, hero_id, name[:1] or "?")
        else:
            pixmap = cover_pixmap(pixmap, 72, 44)
        self._pixmap_cache[hero_id] = pixmap
        return pixmap

    def _item_pixmap(self, item: Any) -> QPixmap:
        key = getattr(item, "key", "")
        if key in self._item_pixmap_cache:
            return self._item_pixmap_cache[key]
        path = item_icon_path(item)
        pixmap = QPixmap(str(path)) if path.exists() else QPixmap()
        if pixmap.isNull():
            pixmap = placeholder_pixmap(88, 64, hash(key) % 997, str(getattr(item, "name", "?"))[:1])
        self._item_pixmap_cache[key] = pixmap
        return pixmap

    def _edit_slot(self, faction: str, slot: int) -> None:
        if not self.service.profiles:
            return
        current = self.tracker.slots(faction)[slot]
        dialog = HeroPickerDialog(
            self.service.profiles,
            lambda hero_id: cover_pixmap(self._hero_pixmap(hero_id), 96, 54),
            self,
            current,
        )
        if dialog.exec() == QDialog.Accepted:
            self.tracker.set_manual(faction, slot, dialog.selected)
            self._refresh_team_panels()
            self._schedule_refresh(60)

    def _slot_context(self, faction: str, slot: int, global_pos: QPoint) -> None:
        state = self.tracker.state(faction)
        menu = QMenu(self)
        if state.locked[slot]:
            unlock = menu.addAction("Снять ручную блокировку")
            unlock.triggered.connect(lambda: self._unlock_slot(faction, slot))
        clear = menu.addAction("Очистить слот")
        clear.triggered.connect(lambda: self._clear_slot(faction, slot))
        menu.exec(global_pos)

    def _unlock_slot(self, faction: str, slot: int) -> None:
        state = self.tracker.state(faction)
        state.locked[slot] = False
        self._refresh_team_panels()

    def _clear_slot(self, faction: str, slot: int) -> None:
        self.tracker.set_manual(faction, slot, None)
        self._refresh_team_panels()
        self._schedule_refresh(60)

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def _on_side_changed(self) -> None:
        if self._updating_side_combo:
            return
        value = self.side_combo.currentData() or ""
        self.cfg.side_override = value
        self.cfg.save()
        self._last_effective_side = self._effective_side()
        self._refresh_side_labels()
        self._refresh_team_panels()
        self._schedule_refresh(60)

    def _on_role_changed(self) -> None:
        if self._updating_role_combo:
            return
        self.cfg.role = self.role_combo.currentData() or ""
        self.cfg.save()
        self._schedule_refresh(60)

    def _on_own_changed(self) -> None:
        if self._updating_own_combo:
            return
        hero_id = self.own_combo.currentData()
        self.own_hero_id = int(hero_id) if hero_id else None
        self.own_manual = True
        self._schedule_refresh(60)

    def _reset_own_from_gsi(self) -> None:
        self.own_manual = False
        self.own_hero_id = self.snapshot.hero_id
        self._updating_own_combo = True
        self._select_combo_hero(self.own_combo, self.own_hero_id)
        self._updating_own_combo = False
        self._schedule_refresh(60)

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------
    def _refresh_all(self) -> None:
        if not self.service.core_loaded:
            return
        side = self._effective_side()
        ally_ids, enemy_ids = self._ally_enemy_ids()
        own = self.own_hero_id
        if own:
            enemy_ids = [hero_id for hero_id in enemy_ids if hero_id != own]
            if own not in ally_ids:
                ally_ids = [own] + ally_ids

        role = self.cfg.role
        if side and enemy_ids:
            self.service.ensure_matchups(enemy_ids)
            matchups = {hero_id: self.service.matchups.get(hero_id, {}) for hero_id in enemy_ids}
            engine = RecommendationEngine(self.service.profiles, matchups, role=role)
            result = engine.recommend(enemy_ids, ally_ids)
            self._last_counter = result
            self._counter_candidates = list(result.candidates)
            self._apply_counter_filters()
            self._update_counter_note(result, enemy_ids)
        else:
            self._last_counter = None
            self._counter_candidates = []
            self._apply_counter_filters()
            if not side:
                self.counter_note.setText(
                    "Сторона не определена. Включите GSI или выберите сторону вручную — "
                    "тогда приложение поймёт, какие пики вражеские."
                )
            else:
                self.counter_note.setText(
                    "Пока не распознано ни одного вражеского героя. Нажмите «Скан» или "
                    "внесите пики вручную кликом по карточке."
                )
            self.needs_label.setText("")

        if own:
            profile = self.service.profiles.get(own)
            self.service.ensure_popularity(own)
            popularity = self.service.popularity.get(own, {})
            build = ItemEngine(self.service.profiles, self.service.items).build(
                own, enemy_ids, popularity, role=role
            )
            self._last_build = build
            self._render_items(build, profile, popularity)
            neutral = NeutralEngine(self.service.profiles, self.service.items).recommend(
                own, enemy_ids, self.cfg.neutral_times, clock=self.snapshot.clock
            )
            self._last_neutral = neutral
            self._render_neutral(neutral, profile)
        else:
            self._last_build = None
            self._last_neutral = None
            self._clear_items("Выберите своего героя — появятся предметы.")
            self._clear_neutral("Выберите своего героя — появятся нейтральные предметы.")
        self._update_data_tab()
        self._update_footer()

    def _update_counter_note(self, result: CounterResult, enemy_ids: list[int]) -> None:
        notes = list(result.notes)
        loaded = sum(1 for hero_id in enemy_ids if hero_id in self.service.matchups)
        if loaded < len(enemy_ids):
            notes.append(f"Матчапы загружены для {loaded}/{len(enemy_ids)} врагов; список уточняется.")
        top = result.candidates[:COUNTER_DEFAULT_LIMIT]
        confidence_counts = {"high": 0, "medium": 0, "low": 0}
        for candidate in top:
            confidence_counts[candidate.confidence] += 1
        if len(result.candidates) > len(top):
            table_text = f"В таблице топ-{len(top)} из {len(result.candidates)} кандидатов"
        else:
            table_text = f"В таблице {len(top)} кандидатов"
        summary = (
            f"Врагов: {len(enemy_ids)}. {table_text}; надёжность: "
            f"высокая {confidence_counts['high']}, средняя {confidence_counts['medium']}, "
            f"низкая {confidence_counts['low']}."
        )
        self.counter_note.setText(summary + (" " + " ".join(notes) if notes else ""))
        if result.needs:
            self.needs_label.setText("Составу не хватает: " + ", ".join(result.needs) + ".")
        else:
            self.needs_label.setText("Ключевые роли в союзном составе закрыты.")

    def _apply_counter_filters(self) -> None:
        if not hasattr(self, "counter_table") or not self.service.core_loaded:
            return
        query = self.counter_search.text()
        attribute = self.counter_attr.currentData() or ""
        attack_type = self.counter_attack.currentData() or ""
        complexity = int(self.counter_complexity.currentData() or 0)
        sort_mode = self.counter_sort.currentData() or "recommendation"
        filtered = filter_proposed_counters(
            self._counter_candidates,
            self.service.profiles,
            query=query,
            attribute=attribute,
            attack_type=attack_type,
            complexity=complexity,
            sort_mode=sort_mode,
        )
        filters_active = bool(
            query.strip()
            or attribute
            or attack_type
            or complexity
            or sort_mode != "recommendation"
        )
        self.counter_table.set_candidates(filtered, self._hero_pixmap, self.service.profiles)
        total = len(self._counter_candidates)
        proposed = min(total, COUNTER_DEFAULT_LIMIT)
        if not total:
            self.counter_filter_label.setText("")
        elif not filters_active:
            if proposed < total:
                self.counter_filter_label.setText(
                    f"топ-{proposed} из {total} кандидатов"
                )
            else:
                self.counter_filter_label.setText(
                    f"показано {proposed} из {total}"
                )
        elif filtered:
            self.counter_filter_label.setText(
                f"подходит {len(filtered)} из {proposed} предложенных"
            )
        elif proposed < total:
            self.counter_filter_label.setText(
                f"среди топ-{proposed} нет совпадений · всего {total} кандидатов"
            )
        else:
            self.counter_filter_label.setText(
                f"среди {proposed} предложенных нет совпадений"
            )

    def _reset_counter_filters(self) -> None:
        widgets = (
            self.counter_search,
            self.counter_sort,
            self.counter_attr,
            self.counter_attack,
            self.counter_complexity,
        )
        for widget in widgets:
            widget.blockSignals(True)
        self.counter_search.clear()
        self.counter_sort.setCurrentIndex(0)
        self.counter_attr.setCurrentIndex(0)
        self.counter_attack.setCurrentIndex(0)
        self.counter_complexity.setCurrentIndex(0)
        for widget in widgets:
            widget.blockSignals(False)
        self._apply_counter_filters()

    def _render_items(self, build: BuildResult, profile: Any, popularity: dict) -> None:
        for phase in ("start", "early", "mid", "late", "situational"):
            widget = self.item_lists[phase]
            widget.clear()
            suggestions = getattr(build, phase)
            if not suggestions:
                widget.show_empty("Нет данных для этой фазы.")
                continue
            for suggestion in suggestions:
                item = self.service.items.get(suggestion.item_id)
                widget.add_suggestion(
                    suggestion,
                    self._item_pixmap(item) if item else None,
                )
        if profile:
            source = "статистика OpenDota" if popularity else "базовый набор (нет статистики)"
            self.item_note.setText(
                f"{profile.name}: {source}. Наведите курсор на иконку, чтобы увидеть название, "
                f"цену и причину. Компоненты скрыты из мида и лейта, ситуативные предметы "
                f"учитывают угрозы вражеского драфта."
            )
        if build.notes:
            self.item_note.setText(self.item_note.text() + " " + " ".join(build.notes))

    def _clear_items(self, text: str) -> None:
        self.item_note.setText(text)
        for widget in self.item_lists.values():
            widget.show_empty("Нет данных")

    def _render_neutral(self, result: NeutralResult, profile: Any) -> None:
        for tier in range(1, 6):
            widget = self.neutral_lists[tier]
            widget.clear()
            time_s = int(self.cfg.neutral_times.get(str(tier), 0) or 0)
            self.neutral_tabs.setTabText(tier - 1, f"T{tier} · {format_clock(time_s)}")
            suggestions = result.tiers.get(tier, [])
            if not suggestions:
                widget.show_empty("Нет данных")
                continue
            for suggestion in suggestions:
                item = self.service.items.get(suggestion.item_id)
                available = suggestion.available
                if available is True:
                    suffix = "доступно"
                elif available is False and self.snapshot.clock is not None:
                    suffix = f"через {format_clock(max(0, suggestion.time_s - self.snapshot.clock))}"
                else:
                    suffix = f"с {format_clock(suggestion.time_s)}"
                widget.add_suggestion(
                    suggestion,
                    self._item_pixmap(item) if item else None,
                    suffix=suffix,
                    tooltip=f"Появляется на {format_clock(suggestion.time_s)}",
                )
        if profile:
            self.neutral_note.setText(
                f"{profile.name}: текущий пул из файлов игры. "
                f"Показаны 3 лучших варианта на каждый тир. Наведите курсор на иконку, "
                f"чтобы увидеть название, тир, время появления и причину."
            )
        if result.notes:
            self.neutral_note.setText(self.neutral_note.text() + " " + " ".join(result.notes))

    def _clear_neutral(self, text: str) -> None:
        self.neutral_note.setText(text)
        for widget in self.neutral_lists.values():
            widget.show_empty("Нет данных")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------
    def _apply_scan_interval(self) -> None:
        if self._draft_frozen:
            if self.scan_timer.isActive():
                self.scan_timer.stop()
            return
        self.scan_timer.setInterval(max(400, int(self.cfg.scan_interval * 1000)))
        if self.cfg.auto_scan and not self.scan_timer.isActive():
            self.scan_timer.start()
        elif not self.cfg.auto_scan and self.scan_timer.isActive():
            self.scan_timer.stop()

    def _auto_scan(self) -> None:
        if not self.cfg.auto_scan or self._draft_frozen:
            return
        if not self.isVisible() or self._calibration_window is not None:
            return
        if self.cfg.scan_only_in_draft and self.snapshot.connected and not self.snapshot.in_draft:
            return
        self._scan_now(save_debug=False)

    def _scan_now(self, save_debug: bool = False) -> None:
        if self._draft_frozen:
            self._on_status(
                "Драфт заморожен — сканирование остановлено. "
                "Нажмите «Новый драфт», чтобы начать заново."
            )
            return
        if self._scan_inflight:
            return
        if not self.service.templates_ready or self.service.templates is None:
            self._on_status("Портреты ещё загружаются — скан станет доступен через несколько секунд.")
            return
        boxes = {side: self.cfg.box(side) for side in ("radiant", "dire")}
        if not all(boxes.values()):
            self._on_status("Калибровка не настроена. Нажмите «Калибровка».")
            self._update_banner()
            return
        self._scan_inflight = True
        debug_path = str(DEBUG_DIR / "last_scan.png") if save_debug else ""
        self._hide_for_scan_if_needed()
        delay = 70 if self._scan_opacity_before is not None else 0
        QTimer.singleShot(
            delay,
            lambda: self._start_scan_task(boxes, save_debug, debug_path),
        )

    def _start_scan_task(self, boxes: dict, save_debug: bool, debug_path: str) -> None:
        if self._draft_frozen:
            self._scan_inflight = False
            self._restore_after_scan()
            return
        if not self.service.templates:
            self._scan_inflight = False
            self._restore_after_scan()
            return
        task = ScanTask(
            self.cfg.monitor,
            self.service.templates,
            boxes,
            save_debug=save_debug,
            debug_path=debug_path,
        )
        task.signals.done.connect(self._on_scan_done)
        task.signals.failed.connect(self._on_scan_failed)
        self._scan_task = task
        self.service.pool.start(task)

    def _hide_for_scan_if_needed(self) -> None:
        if not self.cfg.transparent_on_scan or not self.isVisible():
            return
        if not self._window_intersects_boxes():
            return
        self._scan_opacity_before = self.windowOpacity()
        self.setWindowOpacity(0.0)
        QApplication.processEvents()

    def _restore_after_scan(self) -> None:
        if self._scan_opacity_before is not None:
            self.setWindowOpacity(max(0.6, min(1.0, self.cfg.opacity)))
            self._scan_opacity_before = None

    def _window_intersects_boxes(self) -> bool:
        screen = self._screen_for_monitor()
        if screen is None:
            return False
        geometry = screen.geometry()
        window = self.frameGeometry()
        for side in ("radiant", "dire"):
            rect = self.cfg.box(side)
            if rect is None:
                continue
            x, y, w, h = rect.to_px(geometry.width(), geometry.height())
            box_rect = QRect(geometry.x() + x, geometry.y() + y, w, h)
            if window.intersects(box_rect):
                return True
        return False

    def _on_scan_done(self, output: Any) -> None:
        self._scan_inflight = False
        self._restore_after_scan()
        if not isinstance(output, ScanOutput):
            return
        # A frame that was already being captured when the draft froze is still
        # useful: it was taken from the pick screen.  No new scans can start
        # while frozen, so applying it here cannot overwrite the draft later.
        changed = False
        found = 0
        for side, results in output.results.items():
            changed = self.tracker.apply(side, results) or changed
            found += sum(1 for result in results if result.hero_id)
        self._last_scan_text = (
            f"скан {time.strftime('%H:%M:%S')}: найдено {found}/10, "
            f"кадр {self.cfg.monitor}"
        )
        if changed:
            self._refresh_team_panels()
            self._schedule_refresh(70)
        if not self._draft_frozen and self.tracker.is_complete():
            self._freeze_draft("все 10 героев распознаны")
        else:
            self._update_footer()
        if output.debug_path:
            self._on_status(f"Отладочный кадр сохранён: {output.debug_path}")
        if self._open_debug_after_scan:
            self._open_debug_after_scan = False
            if output.debug_path:
                self._open_last_debug()

    def _on_scan_failed(self, message: str) -> None:
        self._scan_inflight = False
        self._open_debug_after_scan = False
        self._restore_after_scan()
        self._on_status(f"Скан не удался: {message}")

    def _test_scan(self) -> None:
        if self._draft_frozen:
            self._on_status(
                "Драфт заморожен — тестовый скан недоступен. "
                "Нажмите «Новый драфт», если хотите протестировать распознавание."
            )
            return
        self._open_debug_after_scan = True
        stale = DEBUG_DIR / "last_scan.png"
        try:
            stale.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        self._scan_now(save_debug=True)
        if not self._scan_inflight:
            self._open_debug_after_scan = False
            self._on_status("Тестовый скан не запущен. Проверьте калибровку и портреты.")

    def _open_last_debug(self) -> None:
        path = DEBUG_DIR / "last_scan.png"
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            self._on_status("Тестовый кадр ещё не готов. Проверьте калибровку и портреты.")

    # ------------------------------------------------------------------
    # Draft freeze / new draft
    # ------------------------------------------------------------------
    def _has_picks(self) -> bool:
        return any(self._faction_ids(side) for side in ("radiant", "dire"))

    def _freeze_status_text(self) -> str:
        reason = self._freeze_reason or "пики зафиксированы"
        return (
            f"Драфт заморожен: {reason}. Сканирование остановлено — "
            "нажмите «Новый драфт» для следующего матча."
        )

    def _update_freeze_state(self) -> None:
        if not hasattr(self, "freeze_badge"):
            return
        self.freeze_badge.setVisible(self._draft_frozen)
        self.freeze_badge.setToolTip(self._freeze_reason or "")
        self.scan_button.setEnabled(not self._draft_frozen)
        self.new_draft_button.setEnabled(self._draft_frozen or self._has_picks())

    def _freeze_draft(self, reason: str) -> None:
        """Keep the recognised draft and stop all template scans.

        After the last pick the client replaces the pick portraits with the
        versus/team-showcase screen, so another scan can only overwrite good
        data with garbage.  Freezing also removes the timer load from the
        machine while the match is loading.
        """
        if self._draft_frozen:
            return
        self._draft_frozen = True
        self._freeze_reason = reason
        if self.scan_timer.isActive():
            self.scan_timer.stop()
        self._refresh_team_panels()
        self._update_footer()
        self._on_status(self._freeze_status_text())
        self._schedule_refresh(0)

    def _start_new_draft(self, automatic: bool = False) -> None:
        """Clear picks, unfreeze and resume scanning for the next draft."""
        was_frozen = self._draft_frozen
        had_picks = self._has_picks()
        self.tracker.clear()
        self._draft_frozen = False
        self._freeze_reason = ""
        self._last_build = None
        self._last_neutral = None
        self._last_counter = None
        self._counter_candidates = []
        self._last_neutral_clock = self.snapshot.clock
        self._last_scan_text = (
            "новый драфт по GSI" if automatic else "драфт очищен вручную"
        )
        if automatic:
            self.own_manual = False
            self.own_hero_id = (
                self.snapshot.hero_id
                if self.snapshot.game_state == "DOTA_GAMERULES_STATE_HERO_SELECTION"
                else None
            )
            self._updating_own_combo = True
            self._select_combo_hero(self.own_combo, self.own_hero_id)
            self._updating_own_combo = False
        self._refresh_team_panels()
        self._apply_scan_interval()
        self._update_footer()
        self._schedule_refresh(0)
        if automatic:
            self.status_label.setText("Новый драфт (GSI): сканирование возобновлено.")
        elif was_frozen or had_picks:
            self.status_label.setText("Драфт очищен. Сканирование возобновлено.")

    # ------------------------------------------------------------------
    # Calibration
    # ------------------------------------------------------------------
    def _screen_for_monitor(self) -> QScreen | None:
        screens = QGuiApplication.screens()
        if not screens:
            return None
        index = max(0, int(self.cfg.monitor) - 1)
        if index >= len(screens):
            index = 0
        return screens[index]

    def _start_calibration(self) -> None:
        screen = self._screen_for_monitor()
        if screen is None:
            QMessageBox.warning(self, "Калибровка", "Не найден экран для калибровки.")
            return
        self.hide()
        QTimer.singleShot(300, lambda: self._open_calibration(screen))

    def _open_calibration(self, screen: QScreen) -> None:
        try:
            capture = ScreenCapture(self.cfg.monitor)
            frame = capture.grab()
            capture.close()
            pixmap = cv_to_pixmap(frame)
        except Exception as exc:
            self.show()
            QMessageBox.warning(self, "Калибровка", f"Не удалось сделать снимок экрана: {exc}")
            return
        window = CalibrationWindow(pixmap, screen, self)
        self._calibration_window = window
        window.saved.connect(self._on_calibration_saved)
        window.cancelled.connect(self._on_calibration_cancelled)
        window.show()

    def _on_calibration_saved(self, boxes: dict) -> None:
        self.cfg.boxes = dict(boxes)
        screen = self._screen_for_monitor()
        if screen:
            self.cfg.calibration_screen = (screen.geometry().width(), screen.geometry().height())
        self.cfg.save()
        self._calibration_window = None
        self.show()
        self.raise_()
        self._update_banner()
        self._on_status("Калибровка сохранена. Проверяю распознавание…")
        QTimer.singleShot(200, lambda: self._scan_now(save_debug=True))

    def _on_calibration_cancelled(self) -> None:
        self._calibration_window = None
        self.show()
        self.raise_()

    def _reset_calibration(self) -> None:
        self.cfg.boxes = {}
        self.cfg.save()
        self._update_banner()
        self._on_status("Калибровка сброшена.")

    # ------------------------------------------------------------------
    # Settings / banner / data
    # ------------------------------------------------------------------
    def _banner_action(self) -> None:
        if not self.cfg.has_calibration():
            self._start_calibration()
        elif not is_gsi_installed(self._dota_root, self.cfg.gsi_port):
            self._install_gsi()
        else:
            self._install_gsi()

    def _update_banner(self) -> None:
        if not self.cfg.has_calibration():
            self.banner_label.setText(
                "Калибровка не выполнена: приложение не знает, где на экране портреты команд."
            )
            self.banner_button.setText("Калибровать")
            self.banner.show()
            self.calibrate_button.setEnabled(True)
        elif not is_gsi_installed(self._dota_root, self.cfg.gsi_port):
            self.banner_label.setText(
                "GSI не установлен: сторона и ваш герой не определятся автоматически. "
                "Можно выбрать их вручную."
            )
            self.banner_button.setText("Установить GSI")
            self.banner.show()
        else:
            self.banner.hide()
        if hasattr(self, "gsi_path_label"):
            if self._dota_root:
                path = gsi_cfg_path(self._dota_root)
                installed = is_gsi_installed(self._dota_root, self.cfg.gsi_port)
                self.gsi_path_label.setText(
                    f"{'установлен' if installed else 'не установлен'}: {path}"
                )
            else:
                self.gsi_path_label.setText("Dota 2 не найдена. Укажите установку через Steam.")
            self.calib_label.setText(
                "Калибровка сохранена." if self.cfg.has_calibration() else "Калибровка не выполнена."
            )
        self._update_patch_label()

    def _update_patch_label(self) -> None:
        version = self.service.patch.get("ClientVersion") or ""
        date = self.service.patch.get("VersionDate") or ""
        if version:
            self.patch_label.setText(f"patch {version} · {date}")
        else:
            self.patch_label.setText("patch ?")

    def _install_gsi(self) -> None:
        root = find_dota_root()
        self._dota_root = root
        if not root:
            QMessageBox.warning(
                self,
                "GSI",
                "Dota 2 не найдена. Запустите Steam и проверьте библиотеку игры.",
            )
            return
        try:
            path = install_gsi(root, self.cfg.gsi_port)
        except OSError as exc:
            QMessageBox.warning(self, "GSI", f"Не удалось записать конфиг: {exc}")
            return
        self._update_banner()
        self._update_data_tab()
        QMessageBox.information(
            self,
            "GSI установлен",
            f"Файл записан:\n{path}\n\nПерезапустите Dota 2, чтобы он начал отправлять данные.",
        )

    def _reload_data(self) -> None:
        if self._offline:
            self._on_status("Офлайн-режим: перечитываю локальный кэш без запросов в сеть.")
        self._pixmap_cache.clear()
        self._item_pixmap_cache.clear()
        self.service.reload()

    def _save_settings(self) -> None:
        old_monitor = self.cfg.monitor
        self.cfg.auto_scan = self.auto_scan_check.isChecked()
        self.cfg.scan_only_in_draft = self.scan_in_draft_check.isChecked()
        self.cfg.transparent_on_scan = self.transparent_check.isChecked()
        self.cfg.show_debug_frames = self.debug_check.isChecked()
        self.cfg.scan_interval = float(self.interval_spin.value())
        self.cfg.monitor = int(self.monitor_combo.currentData() or 1)
        self.cfg.always_on_top = self.top_check.isChecked()
        self.cfg.opacity = max(0.6, min(1.0, self.opacity_slider.value() / 100.0))
        self.cfg.neutral_times = {
            str(tier): int(spin.value()) for tier, spin in self.neutral_spins.items()
        }
        self.setWindowOpacity(self.cfg.opacity)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, self.cfg.always_on_top)
        self.show()
        self._apply_scan_interval()
        self.cfg.save()
        self._update_banner()
        self._update_data_tab()
        self._schedule_refresh(50)
        if old_monitor != self.cfg.monitor:
            QMessageBox.information(
                self,
                "Монитор изменён",
                "Вы сменили монитор. Если координаты не совпадают, откалибруйте экран заново.",
            )

    def _update_data_tab(self) -> None:
        if not hasattr(self, "data_view"):
            return
        patch = self.service.patch
        pool = self.service.neutral_pool
        neutral_count = sum(1 for item in self.service.items.values() if getattr(item, "neutral", False))
        gsi_text = "подключён" if self.snapshot.connected else "нет данных"
        installed = is_gsi_installed(self._dota_root, self.cfg.gsi_port)
        ages = []
        for key, label in (
            ("heroes", "герои"),
            ("hero_stats", "статистика героев"),
            ("items_constants", "предметы"),
        ):
            age = self.service.cache_age_hours(key)
            if age is not None:
                ages.append(f"{label}: {age:.1f} ч")
        matchup_games = [sum(g for _, g in index.values()) for index in self.service.matchups.values()]
        total_matchup_games = sum(matchup_games)
        min_games = min((g for index in self.service.matchups.values() for _, g in index.values()), default=0)
        pool_text = ""
        if pool:
            times = ", ".join(f"T{tier} {format_clock(pool.times.get(tier, 0))}" for tier in sorted(pool.times))
            pool_text = (
                f"<p><b>Нейтральные предметы из файлов игры:</b> {neutral_count} шт.<br>"
                f"{times}<br>"
                f"источник: <code>{pool.source}</code>, client {pool.client_version or '?'}"
                f" ({pool.version_date or '?'})</p>"
            )
        warnings = []
        if self.service.last_warning:
            warnings.append(self.service.last_warning)
        if self.service.last_error:
            warnings.append(self.service.last_error)
        current_version = str(patch.get("ClientVersion") or "")
        if (
            pool
            and current_version
            and pool.client_version
            and pool.client_version != current_version
        ):
            warnings.append(
                f"Нейтральные предметы взяты из клиента {pool.client_version}, "
                f"а установлен {current_version}. Запустите tools/import_game_data.py."
            )
        warning_text = "<br>".join(warnings) if warnings else "нет"
        loaded_heroes = len(self.service.matchups)
        hero_word = plural_ru(loaded_heroes, "героя", "героев", "героев")
        game_word = plural_ru(total_matchup_games, "игра", "игры", "игр")
        html = f"""
        <h3>Данные</h3>
        <p><b>Версия приложения:</b> {__version__}<br>
        <b>Патч клиента:</b> {patch.get('ClientVersion', '?')} · {patch.get('VersionDate', '?')}<br>
        <b>Героев:</b> {len(self.service.profiles)} · <b>предметов:</b> {len(self.service.items)} ·
        <b>нейтральных:</b> {neutral_count}<br>
        <b>Матчапы загружены для:</b> {loaded_heroes} {hero_word},
        всего {total_matchup_games} {game_word}, минимум {min_games} на пару<br>
        <b>Кэш:</b> {', '.join(ages) if ages else 'пусто'}</p>
        {pool_text}
        <p><b>GSI:</b> {'установлен' if installed else 'не установлен'}, сейчас {gsi_text}.<br>
        <b>Сторона:</b> {SIDE_LABELS.get(self._effective_side(), 'не определена')} ·
        <b>роль:</b> {ROLE_LABELS.get(self.cfg.role, self.cfg.role or 'любая')}<br>
        <b>Драфт:</b> {'заморожен — ' + self._freeze_reason if self._draft_frozen else 'активен'}</p>
        <p><b>Предупреждения:</b> {warning_text}</p>
        <p style="color:#8d9aab">OpenDota отдаёт публичную статистику по парам героев; выборка на пару
        часто небольшая. Поэтому в таблице контрпиков показана надёжность, а рекомендации
        сглаживаются. Это помощник для драфта, а не гарантия результата.</p>
        <p style="color:#8d9aab">Приложение не читает память игры, не внедряется в процесс и не
        нажимает кнопки за вас. Используйте на свой риск и по правилам Valve.</p>
        """
        self.data_view.setHtml(html)

    def _update_footer(self) -> None:
        gsi = "GSI: да" if self.snapshot.connected else "GSI: нет"
        side = SIDE_LABELS.get(self._effective_side(), "сторона ?")
        enemy = len(self._ally_enemy_ids()[1])
        frozen = (
            f" · драфт заморожен: {self._freeze_reason}"
            if self._draft_frozen
            else ""
        )
        self.footer.setText(
            f"{self._last_scan_text} · {gsi} · {side} · врагов распознано: {enemy}/5{frozen}"
        )

    # ------------------------------------------------------------------
    # Window lifecycle
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self._first_show:
            self._first_show = False
            if self.cfg.window_x < 0 or self.cfg.window_y < 0:
                screen = QGuiApplication.primaryScreen()
                if screen:
                    geo = screen.availableGeometry()
                    self.move(
                        max(geo.x(), geo.x() + geo.width() - self.width() - 16),
                        max(geo.y(), geo.y() + 14),
                    )
            self._update_banner()
            self._update_patch_label()
            self._update_data_tab()
            self._refresh_side_labels()
            self._refresh_team_panels()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.cfg.window_x = self.x()
        self.cfg.window_y = self.y()
        self.cfg.window_w = self.width()
        self.cfg.window_h = self.height()
        try:
            self.cfg.save()
        except OSError:
            pass
        self.scan_timer.stop()
        self.gsi_timer.stop()
        if self.gsi_server:
            self.gsi_server.stop()
        try:
            self.service.pool.waitForDone(2500)
        except Exception:
            pass
        super().closeEvent(event)
