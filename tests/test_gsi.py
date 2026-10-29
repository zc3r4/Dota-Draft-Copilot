from __future__ import annotations

import time

from app.gsi import GSIState, GSISnapshot


def payload(**overrides):
    data = {
        "provider": {"name": "Dota 2", "version": 50},
        "map": {"game_state": "DOTA_GAMERULES_STATE_HERO_SELECTION", "clock_time": -75},
        "player": {"team_name": "radiant", "gold": 625},
        "hero": {"id": 8},
        "items": {
            "item_0": 42,
            "item_1": 0,
            "item_2": "36",
            "item_neutral": 0,
        },
        "draft": {
            "team2": {"pick": [{"hero_id": 1}, 2, {"id": 3}]},
            "team3": {"pick": [{"hero_id": 4}, {"hero_id": 0}]},
        },
    }
    data.update(overrides)
    return data


def test_parse_basic_payload() -> None:
    state = GSIState()
    state.update(payload())
    snap = state.snapshot()

    assert snap.connected is True
    assert snap.team == "radiant"
    assert snap.hero_id == 8
    assert snap.clock == -75
    assert snap.gold == 625
    assert snap.items == [42, 36]
    assert snap.draft == {"radiant": [1, 2, 3], "dire": [4]}
    assert snap.in_draft is True


def test_invalid_values_do_not_crash() -> None:
    state = GSIState()
    state.update(
        payload(
            map=None,
            player={"team_name": "unknown", "gold": "abc"},
            hero={"id": "x"},
            items={"item_0": None},
            draft={"team2": {"pick": [{"hero_id": -1}, {}, None]}},
        )
    )
    snap = state.snapshot()
    assert snap.team == ""
    assert snap.hero_id is None
    assert snap.clock is None
    assert snap.gold is None
    assert snap.items == []
    assert snap.draft == {}
    assert snap.connected is True


def test_connection_goes_stale() -> None:
    state = GSIState()
    state.update(payload())
    assert state.snapshot().connected is True
    state._snapshot.last_seen = time.time() - 16.0
    assert state.snapshot().connected is False


def test_game_state_outside_draft() -> None:
    snap = GSISnapshot(game_state="DOTA_GAMERULES_STATE_GAME_IN_PROGRESS")
    assert snap.in_draft is False
    assert GSISnapshot(game_state="").in_draft is True


def test_after_picks_states() -> None:
    snap = GSISnapshot()
    for game_state in (
        "DOTA_GAMERULES_STATE_TEAM_SHOWCASE",
        "DOTA_GAMERULES_STATE_WAIT_FOR_MAP_TO_LOAD",
        "DOTA_GAMERULES_STATE_PRE_GAME",
    ):
        snap.game_state = game_state
        assert snap.after_picks is True, game_state
        # These transition states still belong to the draft screen family.
        assert snap.in_draft is True, game_state

    for game_state in (
        "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
        "DOTA_GAMERULES_STATE_POST_GAME",
    ):
        snap.game_state = game_state
        assert snap.after_picks is True, game_state
        assert snap.in_draft is False, game_state

    for game_state in (
        "DOTA_GAMERULES_STATE_HERO_SELECTION",
        "DOTA_GAMERULES_STATE_STRATEGY_TIME",
    ):
        snap.game_state = game_state
        assert snap.after_picks is False, game_state
        assert snap.in_draft is True, game_state

    snap.game_state = ""
    assert snap.after_picks is False
    assert snap.in_draft is True
