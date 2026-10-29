"""Offscreen UI smoke test.

Runs the main window against the local cache, injects a fake draft and checks
that the recommendation widgets receive data. Intended for development:

    .venv\\Scripts\\python.exe tools\\ui_smoke.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

NATIVE = os.environ.get("DDC_SMOKE_NATIVE") == "1"
if not NATIVE:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("DDC_NO_GSI", "1")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.config import Config  # noqa: E402
from app.theme import QSS  # noqa: E402
from app.ui import MainWindow  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(QSS)

    original = Config()
    cfg = Config()
    cfg.save = lambda *args, **kwargs: None  # do not persist smoke-test state
    cfg.side_override = "radiant"
    cfg.role = "carry"
    cfg.auto_scan = False
    cfg.monitor = 1
    window = MainWindow(cfg)
    if NATIVE:
        # Render with the real Windows font database without flashing a window.
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()

    deadline = time.monotonic() + 25.0
    while not window.service.core_loaded and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)

    if not window.service.core_loaded:
        print("FAIL: core data did not load within 25 s")
        print("status:", window.status_label.text())
        return 1

    print(
        f"data: {len(window.service.profiles)} heroes, "
        f"{len(window.service.items)} items"
    )

    asset_deadline = time.monotonic() + 20.0
    while not window.service.templates_ready and time.monotonic() < asset_deadline:
        app.processEvents()
        time.sleep(0.05)
    templates = len(window.service.templates.templates) if window.service.templates else 0
    print(f"templates: {templates}")

    profiles = window.service.profiles
    known = [hid for hid in profiles if hid > 0][:10]
    if len(known) < 10:
        print("FAIL: not enough heroes to build a fake draft")
        return 1
    ally, enemy = known[:5], known[5:10]
    for slot, hero_id in enumerate(ally):
        window.tracker.set_manual("radiant", slot, hero_id)
    for slot, hero_id in enumerate(enemy):
        window.tracker.set_manual("dire", slot, hero_id)
    window._select_combo_hero(window.own_combo, ally[0])
    window.own_hero_id = ally[0]

    window._refresh_team_panels()
    window._refresh_all()

    # Matchups load asynchronously; capture the populated table, not the
    # placeholder that only contains meta win rates.
    window.service.ensure_matchups(enemy)
    matchup_deadline = time.monotonic() + 20.0
    while (
        any(hero_id not in window.service.matchups for hero_id in enemy)
        and time.monotonic() < matchup_deadline
    ):
        app.processEvents()
        time.sleep(0.05)
    if any(hero_id not in window.service.matchups for hero_id in enemy):
        print("warning: matchups did not finish loading before the screenshot")
    else:
        window._refresh_all()
    app.processEvents()

    rows = window.counter_table.rowCount()
    print(f"counter rows: {rows}")
    if rows <= 0:
        print("FAIL: counter table is empty")
        return 1

    for phase in ("start", "early", "mid", "late", "situational"):
        widget = window.item_lists[phase]
        print(f"items {phase}: {widget.count()}")
    for tier in range(1, 6):
        print(f"neutrals T{tier}: {window.neutral_lists[tier].count()}")

    # Counter filters must refine the proposed top-25 shortlist only.
    candidate = window._counter_candidates[0]
    window.counter_search.setText(candidate.name)
    app.processEvents()
    filtered_rows = window.counter_table.rowCount()
    print(f"filtered counter rows for {candidate.name}: {filtered_rows}")
    if filtered_rows <= 0:
        print("FAIL: counter search returned no rows")
        return 1
    if any(
        window.counter_table.item(row, 0).text() != candidate.name
        for row in range(filtered_rows)
    ):
        print("FAIL: counter search returned an unrelated hero")
        return 1
    window._reset_counter_filters()
    app.processEvents()
    if not 0 < window.counter_table.rowCount() <= 25:
        print("FAIL: reset filters did not restore the default top-25 view")
        return 1

    top_names = [item.name.casefold() for item in window._counter_candidates[:25]]
    window.counter_complexity.setCurrentIndex(window.counter_complexity.findData(3))
    app.processEvents()
    complex_rows = window.counter_table.rowCount()
    print(f"complexity-3 rows in shortlist: {complex_rows}")
    if any(
        window.counter_table.item(row, 0).text().casefold() not in top_names
        for row in range(complex_rows)
    ):
        print("FAIL: complexity filter surfaced a hero outside the top-25")
        return 1
    window._reset_counter_filters()
    app.processEvents()

    outsider = next(
        (
            item
            for item in window._counter_candidates[25:]
            if not any(item.name.casefold() in name for name in top_names)
        ),
        None,
    )
    if outsider is not None:
        window.counter_search.setText(outsider.name)
        app.processEvents()
        if window.counter_table.rowCount() != 0:
            print(f"FAIL: filter surfaced {outsider.name} outside the top-25")
            return 1
        window._reset_counter_filters()
        app.processEvents()
    else:
        print("warning: no candidate outside the top-25 to test the shortlist guard")

    # Freeze stops the scan timer and protects the recognised draft.
    window.cfg.auto_scan = True
    window._apply_scan_interval()
    if not window.scan_timer.isActive():
        print("FAIL: scan timer did not start")
        return 1
    window.gsi_state.update(
        {
            "map": {"game_state": "DOTA_GAMERULES_STATE_TEAM_SHOWCASE"},
            "player": {"team_name": "radiant"},
            "hero": {"id": ally[0]},
        }
    )
    window._tick_gsi()
    app.processEvents()
    if not window._draft_frozen or window.scan_timer.isActive():
        print("FAIL: GSI post-pick state did not freeze the draft")
        return 1
    if not window.freeze_badge.isVisibleTo(window):
        print("FAIL: freeze badge is hidden")
        return 1
    if window.scan_button.isEnabled() or not window.new_draft_button.isEnabled():
        print("FAIL: freeze controls have wrong enabled state")
        return 1

    from app.config import DEBUG_DIR

    window.tabs.setCurrentIndex(0)
    app.processEvents()
    window.repaint()
    app.processEvents()
    shot = DEBUG_DIR / "ui_smoke.png"
    if window.grab().save(str(shot)):
        print(f"screenshot: {shot}")

    window.tabs.setCurrentIndex(1)
    window.item_tabs.setCurrentIndex(0)
    app.processEvents()
    items_shot = DEBUG_DIR / "ui_smoke_items.png"
    if window.grab().save(str(items_shot)):
        print(f"screenshot: {items_shot}")

    window.tabs.setCurrentIndex(2)
    window.neutral_tabs.setCurrentIndex(0)
    app.processEvents()
    neutral_shot = DEBUG_DIR / "ui_smoke_neutral.png"
    if window.grab().save(str(neutral_shot)):
        print(f"screenshot: {neutral_shot}")

    # A new HERO_SELECTION transition resets the frozen draft automatically.
    window.gsi_state.update(
        {
            "map": {"game_state": "DOTA_GAMERULES_STATE_HERO_SELECTION"},
            "player": {"team_name": "radiant"},
            "hero": {"id": 0},
        }
    )
    window._tick_gsi()
    app.processEvents()
    if window._draft_frozen or window._has_picks() or not window.scan_timer.isActive():
        print("FAIL: new HERO_SELECTION did not reset the frozen draft")
        return 1

    # The manual reset button clears the draft and resumes scanning too.
    window.tracker.set_manual("radiant", 0, ally[0])
    window._freeze_draft("ручная проверка")
    window._start_new_draft(automatic=False)
    app.processEvents()
    if window._draft_frozen or window._has_picks() or not window.scan_timer.isActive():
        print("FAIL: manual new draft did not reset the draft and resume scanning")
        return 1
    window.cfg.auto_scan = False
    window._apply_scan_interval()
    if window.scan_timer.isActive():
        print("FAIL: scan timer did not stop after disabling auto scan")
        return 1

    # Do not persist the synthetic draft/role used by this smoke test.
    cfg.side_override = original.side_override
    cfg.role = original.role
    cfg.auto_scan = original.auto_scan
    cfg.window_x = original.window_x
    cfg.window_y = original.window_y
    cfg.window_w = original.window_w
    cfg.window_h = original.window_h
    window.close()
    print("UI SMOKE OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
