"""编排层单测：evaluate() 全链路（假 LLM）+ post_llm_call 的廉价判定门槛。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_state import SessionDB

from oht_engine import evaluate, load_config, register_hooks  # noqa: E402
from oht_state import StateStore  # noqa: E402
from oht_store import LLM_SOURCE, read_title  # noqa: E402

SID = "20260202_101010_abcdef"


class FakeState:
    def __init__(self, data_dir: Path):
        self._data: dict = {}
        self.data_dir = data_dir

    def get(self, key, default=None):
        return self._data.get(key, default)

    def set(self, key, value):
        self._data[key] = value


class FakeCtx:
    def __init__(self, tmp_path: Path, *, settings=None, llm=None):
        self.settings = dict(settings or {})
        self.state = FakeState(tmp_path / "plugin-data")
        self.llm = llm
        self.hooks = []
        self.skills = []
        self.commands = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_hook(self, name, fn):
        self.hooks.append((name, fn))

    def register_command(self, *a, **k):
        self.commands.append(a)

    def register_cli_command(self, *a, **k):
        self.commands.append(a)

    def register_system_prompt_section(self, *a, **k):
        return None

    def register_skill(self, *a, **k):
        self.skills.append(a)


class FakeLlm:
    """按预设回复序列作答，并记录每次调用。"""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, messages=None, **kwargs):
        self.calls.append({"messages": messages, **kwargs})
        text = self.replies.pop(0) if self.replies else '{"updated": false, "title": "", "reason": ""}'
        usage = SimpleNamespace(input_tokens=100, output_tokens=20, total_tokens=120, cost_usd=None)
        return SimpleNamespace(text=text, model="fake-model", provider="fake", usage=usage)


def reply(title, updated=True, reason="测试"):
    return json.dumps({"updated": updated, "title": title, "reason": reason}, ensure_ascii=False)


@pytest.fixture()
def db(tmp_path):
    database = SessionDB(db_path=tmp_path / "state.db")
    database.create_session(SID, source="cli")
    yield database
    database.close()


def ctx_with(tmp_path, replies, *, settings=None):
    merged = {"state_db_path": str(tmp_path / "state.db"), **(settings or {})}
    return FakeCtx(tmp_path, settings=merged, llm=FakeLlm(replies))


HISTORY = [
    {"role": "user", "content": "帮我把登录页的布局改成响应式"},
    {"role": "assistant", "content": "好，我先看一下现有样式"},
    {"role": "user", "content": "顺便把提交按钮的 loading 加上"},
]


def test_evaluate_applies_candidate(tmp_path, db):
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化")])
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    assert result["status"] == "applied", result
    assert read_title(db, SID) == ("🧩 登录表单｜布局优化", LLM_SOURCE)


def test_evaluate_retitles_next_turn(tmp_path, db):
    """第二轮必须还能改标题（provenance 单调坑的端到端回归）。"""
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化"), reply("🧩 登录表单｜响应式适配")])
    config = load_config(ctx)
    evaluate(ctx, session_id=SID, history=HISTORY, config=config)
    history = HISTORY + [{"role": "user", "content": "窄屏下一行放不下三个按钮"}]
    result = evaluate(ctx, session_id=SID, history=history, config=config)
    assert result["status"] == "applied", result
    assert read_title(db, SID)[0] == "🧩 登录表单｜响应式适配"


def test_evaluate_preview_does_not_write(tmp_path, db):
    ctx = ctx_with(tmp_path, [reply("🎨 首页｜响应式适配")])
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx), preview=True)
    assert result["status"] == "preview"
    assert result["title"] == "🎨 首页｜响应式适配"
    assert read_title(db, SID) == (None, None)


def test_evaluate_keeps_title_when_model_declines(tmp_path, db):
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化", updated=False)])
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    assert result["status"] == "not_updated"
    assert read_title(db, SID)[0] is None


def test_evaluate_retries_malformed_once_then_gives_up(tmp_path, db):
    ctx = ctx_with(tmp_path, [reply("登录表单 布局优化"), reply("还是不合规")])
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    assert result["status"] == "invalid", result
    assert len(ctx.llm.calls) == 2
    assert read_title(db, SID)[0] is None


def test_evaluate_repairs_malformed_on_retry(tmp_path, db):
    ctx = ctx_with(tmp_path, [reply("登录表单 布局优化"), reply("🧩 登录表单｜布局优化")])
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    assert result["status"] == "applied", result
    assert read_title(db, SID)[0] == "🧩 登录表单｜布局优化"


def test_evaluate_respects_locked_title(tmp_path, db):
    db.set_session_title(SID, "我自己起的名字")
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化")])
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    assert result["status"] == "locked"
    assert ctx.llm.calls == []
    assert read_title(db, SID)[0] == "我自己起的名字"


def test_evaluate_writes_ledger(tmp_path, db):
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化")])
    evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    summary = StateStore(ctx).ledger_summary(days=1)
    assert summary["calls"] == 1
    assert summary["statuses"] == {"applied": 1}
    assert summary["by_purpose"]["naming"]["total_tokens"] == 120


def test_model_failure_does_not_raise(tmp_path, db):
    class BrokenLlm:
        def complete(self, **kwargs):
            raise RuntimeError("endpoint down")

    ctx = FakeCtx(tmp_path, settings={"state_db_path": str(tmp_path / "state.db")}, llm=BrokenLlm())
    result = evaluate(ctx, session_id=SID, history=HISTORY, config=load_config(ctx))
    assert result["status"] == "error"
    assert "endpoint down" in result["detail"]


# ------------------------------------------------------------ hook 判定门槛


class SyncThread:
    """把后台线程变成同步执行，让门槛判定可断言、不依赖 sleep。"""

    def __init__(self, target=None, name=None, args=(), daemon=None, **kwargs):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def _payload(**overrides):
    payload = {"session_id": SID, "user_message": "帮我把登录页改成响应式",
               "assistant_response": "好", "conversation_history": HISTORY,
               "model": "fake-model", "platform": "cli"}
    payload.update(overrides)
    return payload


@pytest.fixture()
def hooked(tmp_path, db, monkeypatch):
    monkeypatch.setattr("oht_engine.threading.Thread", SyncThread)
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化")] * 6)
    register_hooks(ctx)
    assert ctx.hooks and ctx.hooks[0][0] == "post_llm_call"
    return ctx


def hook_of(ctx):
    return ctx.hooks[0][1]


def test_hook_applies_title(hooked, db):
    hook_of(hooked)(**_payload())
    assert read_title(db, SID)[0] == "🧩 登录表单｜布局优化"


def test_hook_skips_when_disabled(tmp_path, db, monkeypatch):
    monkeypatch.setattr("oht_engine.threading.Thread", SyncThread)
    ctx = ctx_with(tmp_path, [reply("🧩 登录表单｜布局优化")],
                   settings={"enabled": False})
    register_hooks(ctx)
    hook_of(ctx)(**_payload())
    assert ctx.llm.calls == []


def test_hook_skips_when_paused(hooked):
    StateStore(hooked).set_paused(True)
    hook_of(hooked)(**_payload())
    assert hooked.llm.calls == []


def test_hook_skips_automation_surfaces(hooked):
    hook_of(hooked)(**_payload(platform="cron"))
    hook_of(hooked)(**_payload(platform="kanban"))
    assert hooked.llm.calls == []


def test_hook_skips_confirmation_until_limit(hooked, db):
    hook_of(hooked)(**_payload())                       # 首次命名
    assert len(hooked.llm.calls) == 1
    state = StateStore(hooked)
    for _ in range(2):                                  # confirm_skip_limit 默认 2
        hook_of(hooked)(**_payload(user_message="好的", conversation_history=HISTORY))
    assert len(hooked.llm.calls) == 1, "纯确认轮不该再调用模型"
    assert state.session(SID)["confirm_streak"] == 2
    hook_of(hooked)(**_payload(user_message="好的", conversation_history=HISTORY))
    assert len(hooked.llm.calls) == 2, "超过上限后应重新评估"


def test_hook_skips_duplicate_content(hooked):
    hook_of(hooked)(**_payload())
    hook_of(hooked)(**_payload())                       # 同一段内容重复触发
    assert len(hooked.llm.calls) == 1


def test_hook_skips_untitleable_message(hooked):
    hook_of(hooked)(**_payload(user_message="..."))
    hook_of(hooked)(**_payload(user_message="ok"))
    assert hooked.llm.calls == []