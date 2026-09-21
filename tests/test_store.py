"""state.db 写入层单测 —— 覆盖 Hermes 特有的 provenance 单调坑。

核心回归：自动标题写成 ``llm`` 之后，**再写一次 ``llm`` 会被静默拒绝**；
必须先把权限降级到 ``derived`` 再写。
"""

from __future__ import annotations

import pytest

from hermes_state import SessionDB

from oht_store import (  # noqa: E402
    LLM_SOURCE,
    USER_SOURCE,
    idle_sessions,
    read_title,
    set_lock,
    write_auto_title,
)


@pytest.fixture()
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session("20260101_000000_aaaaaa", source="cli")
    yield database
    database.close()


SID = "20260101_000000_aaaaaa"


def test_first_write_lands(db):
    result = write_auto_title(db, SID, "🧩 登录表单｜布局优化")
    assert result["status"] == "applied"
    assert read_title(db, SID) == ("🧩 登录表单｜布局优化", LLM_SOURCE)


def test_second_llm_write_requires_downgrade(db):
    """同一个会话第二轮必须还能改标题（上游的每轮重命名就靠这条）。"""
    write_auto_title(db, SID, "🧩 登录表单｜布局优化")
    second = write_auto_title(db, SID, "🧩 登录表单｜响应式适配")
    assert second["status"] == "applied", second
    assert read_title(db, SID) == ("🧩 登录表单｜响应式适配", LLM_SOURCE)


def test_same_title_is_unchanged(db):
    write_auto_title(db, SID, "🎬 品牌片｜转场设计")
    assert write_auto_title(db, SID, "🎬 品牌片｜转场设计")["status"] == "unchanged"


def test_user_title_blocks_auto_write(db):
    db.set_session_title(SID, "我自己起的名字")
    assert read_title(db, SID)[1] == USER_SOURCE
    result = write_auto_title(db, SID, "🧩 登录表单｜布局优化")
    assert result["status"] == "locked"
    assert read_title(db, SID)[0] == "我自己起的名字"


def test_lock_and_unlock(db):
    write_auto_title(db, SID, "🧩 登录表单｜布局优化")
    assert set_lock(db, SID, True)
    assert read_title(db, SID)[1] == USER_SOURCE
    # 固定后自动写入被拒
    assert write_auto_title(db, SID, "🧩 别的｜目标")["status"] == "locked"
    assert set_lock(db, SID, False)
    assert read_title(db, SID)[1] == LLM_SOURCE
    assert write_auto_title(db, SID, "🧩 别的｜目标")["status"] == "applied"


def test_title_collision_gets_lineage_suffix(db):
    other = "20260101_000000_bbbbbb"
    db.create_session(other, source="cli")
    write_auto_title(db, other, "🧩 登录表单｜布局优化")
    result = write_auto_title(db, SID, "🧩 登录表单｜布局优化")
    assert result["status"] == "applied_deduped", result
    assert read_title(db, SID)[0] == "🧩 登录表单｜布局优化 #2"


def test_idle_sessions_league_fresh_session(db):
    """刚创建、刚活动的会话不该出现在归档建议里。"""
    assert idle_sessions(db, days=3) == []