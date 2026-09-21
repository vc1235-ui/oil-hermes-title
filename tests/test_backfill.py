"""回填的选样规则与备份/回滚往返测试。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from hermes_state import SessionDB

from oht_backfill import backup_titles, load_backup, restore_titles, select_sessions
from oht_store import LLM_SOURCE, USER_SOURCE, read_title, write_auto_title

NOW = 1_800_000_000.0
DAY = 86400


def row(session_id, *, source="cli", title="旧标题", title_source="llm", msgs=6, age_days=1,
        archived=False, hidden=False):
    return {"id": session_id, "source": source, "title": title, "title_source": title_source,
            "message_count": msgs, "last_active": NOW - age_days * DAY,
            "archived": archived, "hidden": hidden}


def test_window_excludes_old_sessions():
    rows = [row("recent", age_days=3), row("old", age_days=20)]
    selected, skipped = select_sessions(rows, days=14, now=NOW)
    assert [s["id"] for s in selected] == ["recent"]
    assert skipped == {}          # 窗口外不算"跳过"


def test_user_titles_are_protected_by_default():
    rows = [row("a", title_source=LLM_SOURCE), row("b", title_source=USER_SOURCE)]
    selected, skipped = select_sessions(rows, days=14, now=NOW)
    assert [s["id"] for s in selected] == ["a"]
    assert skipped == {"user_title_protected": 1}

    selected, skipped = select_sessions(rows, days=14, now=NOW, include_user=True)
    assert sorted(s["id"] for s in selected) == ["a", "b"]
    assert skipped == {}


def test_automation_sources_skipped_by_default():
    rows = [row("c1", source="cron"), row("k1", source="kanban"), row("d1", source="desktop"),
            row("o1", source="oneshot")]
    selected, skipped = select_sessions(rows, days=14, now=NOW)
    assert [s["id"] for s in selected] == ["d1"]
    assert skipped == {"automation:cron": 1, "automation:kanban": 1, "automation:oneshot": 1}

    selected, _ = select_sessions(rows, days=14, now=NOW, include_automation=True)
    assert len(selected) == 4


def test_min_messages_archived_and_limit():
    rows = [row("tiny", msgs=1), row("arch", archived=True), row("hid", hidden=True),
            row("ok1"), row("ok2")]
    selected, skipped = select_sessions(rows, days=14, now=NOW, min_messages=2)
    assert [s["id"] for s in selected] == ["ok1", "ok2"]
    assert skipped == {"too_few_messages": 1, "archived_or_hidden": 2}

    selected, _ = select_sessions(rows, days=14, now=NOW, limit=1)
    assert [s["id"] for s in selected] == ["ok1"]


# ------------------------------------------------------------ 备份 / 回滚


@pytest.fixture()
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session("20260901_000000_aaaaaa", source="cli")
    database.create_session("20260901_000000_bbbbbb", source="cli")
    yield database
    database.close()


def test_backup_and_restore_round_trip(db, tmp_path):
    a, b = "20260901_000000_aaaaaa", "20260901_000000_bbbbbb"
    write_auto_title(db, a, "🧩 旧标题A｜目标")
    write_auto_title(db, b, "🔎 旧标题B｜目标")

    sessions = [{"id": a, "title": "🧩 旧标题A｜目标", "title_source": LLM_SOURCE},
                {"id": b, "title": "🔎 旧标题B｜目标", "title_source": LLM_SOURCE}]
    path = backup_titles(db, sessions, tmp_path / "plugin-data")
    assert path.exists() and json.loads(path.read_text())["sessions"][a]["title"] == "🧩 旧标题A｜目标"

    # 模拟回填改掉了标题
    write_auto_title(db, a, "🧩 新标题A｜目标")
    write_auto_title(db, b, "🔎 新标题B｜目标")
    assert read_title(db, a)[0] == "🧩 新标题A｜目标"

    stats = restore_titles(db, load_backup(path))
    assert stats == {"restored": 2}
    assert read_title(db, a) == ("🧩 旧标题A｜目标", LLM_SOURCE)   # 文字与权限都还原
    assert read_title(db, b)[0] == "🔎 旧标题B｜目标"


def test_restore_clears_title_that_had_none(db, tmp_path):
    a = "20260901_000000_aaaaaa"
    write_auto_title(db, a, "🧩 本来没标题｜新加的")
    sessions = [{"id": a, "title": None, "title_source": None}]
    path = backup_titles(db, sessions, tmp_path / "plugin-data")
    stats = restore_titles(db, load_backup(path))
    assert stats == {"cleared": 1}
    assert read_title(db, a) == (None, None)


def test_restore_is_idempotent(db, tmp_path):
    a = "20260901_000000_aaaaaa"
    write_auto_title(db, a, "🧩 标题｜目标")
    sessions = [{"id": a, "title": "🧩 标题｜目标", "title_source": LLM_SOURCE}]
    path = backup_titles(db, sessions, tmp_path / "plugin-data")
    assert restore_titles(db, load_backup(path)) == {"unchanged": 1}