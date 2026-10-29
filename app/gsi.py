from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .config import SIDES

DRAFT_STATES = {
    "DOTA_GAMERULES_STATE_HERO_SELECTION",
    "DOTA_GAMERULES_STATE_STRATEGY_TIME",
    "DOTA_GAMERULES_STATE_TEAM_SHOWCASE",
    "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
    "DOTA_GAMERULES_STATE_PRE_GAME",
}

# Once the client leaves the pick/strategy UI, the same screen area shows the
# versus showcase or the game HUD.  Further template scans merely overwrite the
# already complete draft, so the companion stops capturing at this point.
POST_DRAFT_STATES = {
    "DOTA_GAMERULES_STATE_TEAM_SHOWCASE",
    "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
    "DOTA_GAMERULES_STATE_PRE_GAME",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
    "DOTA_GAMERULES_STATE_POST_GAME",
}

# The GSI config asks Dota for a heartbeat every 10 seconds.  A shorter
# connection timeout made the UI flap to "not connected" between heartbeats
# while the draft screen was idle.
STALE_AFTER = 12.0


@dataclass
class GSISnapshot:
    connected: bool = False
    last_seen: float = 0.0
    game_state: str = ""
    team: str = ""  # "radiant" / "dire" / ""
    hero_id: int | None = None
    clock: int | None = None
    gold: int | None = None
    items: list[int] = field(default_factory=list)
    draft: dict[str, list[int]] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def in_draft(self) -> bool:
        return not self.game_state or self.game_state in DRAFT_STATES

    @property
    def after_picks(self) -> bool:
        return self.game_state in POST_DRAFT_STATES


class GSIState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._snapshot = GSISnapshot()
        self._last_payload_at = 0.0

    def update(self, payload: dict[str, Any]) -> None:
        with self._lock:
            now = time.time()
            self._last_payload_at = now

            map_data = payload.get("map") or {}
            player = payload.get("player") or {}
            hero = payload.get("hero") or {}
            items = payload.get("items") or {}

            team = str(player.get("team_name") or "").lower()
            if team not in SIDES:
                team = ""

            hero_id = hero.get("id")
            try:
                hero_id = int(hero_id) if hero_id else None
            except (TypeError, ValueError):
                hero_id = None

            clock = map_data.get("clock_time")
            try:
                clock = int(clock) if clock is not None else None
            except (TypeError, ValueError):
                clock = None

            gold = player.get("gold")
            try:
                gold = int(gold) if gold is not None else None
            except (TypeError, ValueError):
                gold = None

            item_ids: list[int] = []
            for key in ("item_0", "item_1", "item_2", "item_3", "item_4", "item_5", "item_neutral"):
                try:
                    value = int(items.get(key) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    item_ids.append(value)

            draft: dict[str, list[int]] = {}
            raw_draft = payload.get("draft") or {}
            for key, side in (("team2", "radiant"), ("team3", "dire")):
                picks = (raw_draft.get(key) or {}).get("pick") or []
                parsed: list[int] = []
                for entry in picks:
                    if isinstance(entry, dict):
                        value = entry.get("hero_id") or entry.get("id")
                    else:
                        value = entry
                    try:
                        value = int(value)
                    except (TypeError, ValueError):
                        continue
                    if value > 0:
                        parsed.append(value)
                if parsed:
                    draft[side] = parsed

            self._snapshot = GSISnapshot(
                connected=True,
                last_seen=now,
                game_state=str(map_data.get("game_state") or ""),
                team=team,
                hero_id=hero_id,
                clock=clock,
                gold=gold,
                items=item_ids,
                draft=draft,
                raw=payload,
            )

    def snapshot(self) -> GSISnapshot:
        with self._lock:
            snap = self._snapshot
            # Dataclass is effectively immutable for consumers; copy the mutable
            # containers to avoid races.
            snap_items = list(snap.items)
            snap_draft = {k: list(v) for k, v in snap.draft.items()}
            connected = bool(snap.last_seen and (time.time() - snap.last_seen) < STALE_AFTER)
            return GSISnapshot(
                connected=connected,
                last_seen=snap.last_seen,
                game_state=snap.game_state,
                team=snap.team,
                hero_id=snap.hero_id,
                clock=snap.clock,
                gold=snap.gold,
                items=snap_items,
                draft=snap_draft,
                raw=snap.raw,
            )


class _GSIHandler(BaseHTTPRequestHandler):
    server_version = "DotaDraftCopilotGSI/1.0"

    def do_POST(self) -> None:  # noqa: N802 (HTTP method name)
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length else b""
        try:
            payload = json.loads(body.decode("utf-8", errors="replace") or "{}")
        except ValueError:
            payload = {}
        if isinstance(payload, dict) and payload:
            self.server.state.update(payload)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        body = b"Dota Draft Copilot GSI endpoint. POST JSON here."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        return


class GSIServer:
    def __init__(self, port: int, state: GSIState) -> None:
        self.port = int(port)
        self.state = state
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._httpd is not None:
            return
        httpd = ThreadingHTTPServer(("127.0.0.1", self.port), _GSIHandler)
        httpd.daemon_threads = True
        httpd.state = self.state  # type: ignore[attr-defined]
        self._httpd = httpd
        self._thread = threading.Thread(target=httpd.serve_forever, name="gsi-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        httpd, self._httpd = self._httpd, None
        if httpd is not None:
            try:
                httpd.shutdown()
                httpd.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
