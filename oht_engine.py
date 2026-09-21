"""回合钩子与命名编排。

``post_llm_call`` 在**回合线程**上触发，且被 ``plugins.hook_callback_timeout``（默认 30s）
约束、超时即被放弃。所以这里只做廉价判定，然后：
1. 在回合线程里解析 profile 的 state.db 路径（ContextVar 不跨线程）；
2. 丢一个 daemon 线程去做模型调用与写入，立即返回，不阻塞对话。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from . import oht_naming as naming
from . import oht_rules as rules
from . import oht_state as state_mod
from . import oht_store as store_mod

logger = logging.getLogger(__name__)

# 无人在看的自动化会话不命名（省额度）
_SKIP_SURFACES = frozenset({"cron", "kanban", "tool", "batch", "subagent", "oneshot"})
_UNTRUTHY = frozenset({"", "0", "false", "no", "off", "none", "null"})


def is_cron_session() -> bool:
    """本回合是否由 cron 任务驱动。

    ``platform`` 只反映 ``--source``：cron 任务用 ``hermes chat --source cli`` 起会话时
    会伪装成普通 CLI 会话，所以必须另看 ``HERMES_CRON_SESSION``（cron 调度器设的会话
    ContextVar，并会随环境传给子进程）。取不到就当普通会话处理。
    """
    value = ""
    try:
        from gateway.session_context import get_session_env
        value = str(get_session_env("HERMES_CRON_SESSION", "") or "")
    except Exception:
        value = str(os.environ.get("HERMES_CRON_SESSION", "") or "")
    return value.strip().lower() not in _UNTRUTHY


_SEMAPHORE: Optional[threading.Semaphore] = None
_SEMAPHORE_LIMIT = 0
_SEMAPHORE_LOCK = threading.Lock()


@dataclass
class Config:
    enabled: bool = True
    history_turns: int = 5
    max_chars_per_turn: int = 700
    timeout: float = 30.0
    max_parallel_workers: int = 2
    confirm_skip_limit: int = 2
    min_user_chars: int = 4
    style: str = ""
    state_db_path: str = ""


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def load_config(ctx: Any) -> Config:
    get = ctx.get_config

    def _cfg(key: str, default: Any) -> Any:
        try:
            value = get(key, default)
        except Exception:
            return default
        return default if value is None else value

    return Config(
        enabled=bool(_cfg("enabled", True)),
        history_turns=_clamp(_cfg("history_turns", 5), 1, rules.MAX_EXCERPT_TURNS, 5),
        max_chars_per_turn=_clamp(_cfg("max_chars_per_turn", 700), 200, 6000, 700),
        timeout=float(_clamp(_cfg("timeout", 30), 5, 300, 30)),
        max_parallel_workers=_clamp(_cfg("max_parallel_workers", 2), 1, 8, 2),
        confirm_skip_limit=_clamp(_cfg("confirm_skip_limit", 2), 0, 10, 2),
        min_user_chars=_clamp(_cfg("min_user_chars", 4), 1, 200, 4),
        style=str(_cfg("style", "") or ""),
        state_db_path=str(_cfg("state_db_path", "") or ""),
    )


def _semaphore(limit: int) -> threading.Semaphore:
    global _SEMAPHORE, _SEMAPHORE_LIMIT
    with _SEMAPHORE_LOCK:
        if _SEMAPHORE is None or _SEMAPHORE_LIMIT != limit:
            _SEMAPHORE = threading.Semaphore(limit)
            _SEMAPHORE_LIMIT = limit
        return _SEMAPHORE


# ---------------------------------------------------------------- 命名编排


def evaluate(ctx: Any, *, session_id: str, history: Sequence[Any], config: Config,
             preview: bool = False, purpose: str = "naming") -> Dict[str, Any]:
    """跑一次命名评估。同步执行（调用方决定是否起线程）。"""
    store = state_mod.StateStore(ctx)
    started = time.time()
    if not session_id:
        return {"status": "error", "detail": "缺少 session_id"}

    db_path = store_mod.db_path(config.state_db_path)
    try:
        db = store_mod.open_db(db_path)
    except Exception as exc:
        return {"status": "error", "detail": f"无法打开会话库: {exc}"}

    current_title, source = store_mod.read_title(db, session_id)
    if source == store_mod.USER_SOURCE and not preview:
        return {"status": "locked", "title": current_title,
                "detail": "标题已被固定为 user 权限（手动 /title 或 lock），自动命名不再改动"}

    user_texts = rules.recent_user_texts(history, max_turns=config.history_turns)
    excerpt = naming.build_excerpt(history, turns=config.history_turns,
                                   max_chars=config.max_chars_per_turn)
    if not excerpt.strip() and not user_texts:
        return {"status": "skip_empty", "detail": "没有可用于命名的对话内容"}

    prompt = naming.load_prompt(style=config.style)
    language = rules.guess_language(user_texts)
    result = naming.request_title(ctx.llm, prompt=prompt, excerpt=excerpt, current_title=current_title,
                                  language=language, timeout=config.timeout)
    duration_ms = int((time.time() - started) * 1000)
    if not result.get("ok"):
        store.ledger_append(naming.usage_record(result, purpose=purpose, session_id=session_id,
                                                duration_ms=duration_ms, status="error"))
        return {"status": "error", "title": current_title, "detail": result.get("error") or "命名调用失败"}

    candidate, reason = rules.validate_title(result.get("title"))
    if not result.get("updated"):
        store.ledger_append(naming.usage_record(result, purpose=purpose, session_id=session_id,
                                                duration_ms=duration_ms, status="kept"))
        return {"status": "not_updated", "title": current_title, "reason": result.get("reason") or "",
                "detail": "模型认为当前标题已经准确，保持原样"}

    # 格式复核：不合规时补一次纠正指令（只重试一次）
    if candidate is None:
        retry_excerpt = f"{excerpt}\n\n{naming.repair_hint(reason)}"
        retry = naming.request_title(ctx.llm, prompt=prompt, excerpt=retry_excerpt,
                                     current_title=current_title, language=language, timeout=config.timeout)
        duration_ms = int((time.time() - started) * 1000)
        if retry.get("ok"):
            store.ledger_append(naming.usage_record(retry, purpose=f"{purpose}_retry", session_id=session_id,
                                                    duration_ms=duration_ms, status="retry"))
            candidate, reason2 = rules.validate_title(retry.get("title"))
            if candidate is None:
                return {"status": "invalid", "title": current_title, "candidate": retry.get("title"),
                        "detail": f"候选标题格式不合规: {reason} / {reason2}"}
        else:
            return {"status": "invalid", "title": current_title, "candidate": result.get("title"),
                    "detail": f"候选标题格式不合规: {reason}"}

    if preview:
        store.ledger_append(naming.usage_record(result, purpose="preview", session_id=session_id,
                                                duration_ms=duration_ms, status="preview"))
        return {"status": "preview", "title": candidate, "candidate": candidate,
                "reason": result.get("reason") or "", "previous": current_title}

    write = store_mod.write_auto_title(db, session_id, candidate)
    if write["status"] in ("applied", "applied_deduped"):
        store.update_session(session_id, last_title=write["title"], last_source=store_mod.LLM_SOURCE,
                             last_applied_at=time.time())
    store.ledger_append(naming.usage_record(result, purpose=purpose, session_id=session_id,
                                            duration_ms=duration_ms, status=write["status"]))
    return {"status": write["status"], "title": write.get("title") or candidate, "candidate": candidate,
            "reason": result.get("reason") or "", "previous": current_title, "detail": write.get("detail") or ""}


def name_session(ctx: Any, *, session_id: str, history: Sequence[Any], preview: bool = False) -> Dict[str, Any]:
    """给 CLI / 斜杠命令用的同步入口。"""
    return evaluate(ctx, session_id=session_id, history=history, config=load_config(ctx), preview=preview,
                    purpose="preview" if preview else "manual")


def load_history(ctx: Any, session_id: str, limit: int = 40) -> List[Dict[str, Any]]:
    """从 state.db 读最近的会话消息，供 CLI 手动触发命名时使用。"""
    try:
        db = store_mod.open_db(store_mod.db_path(load_config(ctx).state_db_path))
        messages = db.get_messages(session_id, limit=limit, latest=True)
    except Exception:
        logger.debug("could not load history for %s", session_id, exc_info=True)
        return []
    history: List[Dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role in ("user", "assistant"):
            history.append({"role": role, "content": message.get("content")})
    return history


# ---------------------------------------------------------------- 回合钩子


def _handle_turn(ctx: Any, payload: Dict[str, Any]) -> None:
    """post_llm_call 的主体：全部是廉价判定 + 起线程。"""
    try:
        config = load_config(ctx)
        if not config.enabled:
            return
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return
        platform = str(payload.get("platform") or "").strip().lower()
        if platform in _SKIP_SURFACES:
            return
        if is_cron_session():
            return
        store = state_mod.StateStore(ctx)
        if store.paused():
            return

        history = payload.get("conversation_history") or []
        user_message = payload.get("user_message") or ""
        session_state = store.session(session_id)

        if rules.is_pure_confirmation(user_message):
            streak = int(session_state.get("confirm_streak") or 0) + 1
            store.update_session(session_id, confirm_streak=streak)
            # 没有稳定标题 → 这一轮没有实质内容可作命名输入；未超上限 → 直接保留标题。
            # 超过上限才继续往下走，重新判断一次，避免长时间漂移。
            if not session_state.get("last_title") or streak <= config.confirm_skip_limit:
                return
        else:
            if not rules.is_titleable(user_message, min_chars=config.min_user_chars):
                return
            # 同一段内容重复触发（重试、回声）不重复调用模型
            fingerprint = rules.fingerprint(rules.recent_user_texts(history, max_turns=config.history_turns))
            if fingerprint and fingerprint == session_state.get("fingerprint"):
                return
            store.update_session(session_id, fingerprint=fingerprint, confirm_streak=0)

        # profile 由回合线程的 ContextVar 绑定；线程里读不到，所以在这里先解析好路径。
        db_path = store_mod.db_path(config.state_db_path)
        semaphore = _semaphore(config.max_parallel_workers)
        if not semaphore.acquire(blocking=False):
            logger.info("oil-hermes-title: %d 个命名调用在跑，本回合跳过（session=%s）",
                        config.max_parallel_workers, session_id)
            return

        logger.info("oil-hermes-title: 已排入后台命名（session=%s platform=%s history=%d）",
                    session_id, platform or "-", len(history))
        thread = threading.Thread(
            target=_worker, name="oil-hermes-title", daemon=True,
            args=(ctx, config, session_id, list(history), db_path, semaphore))
        thread.start()
    except Exception:
        logger.debug("oil-hermes-title turn handling failed", exc_info=True)


def _worker(ctx: Any, config: Config, session_id: str, history: List[Any], db_path: Any,
            semaphore: threading.Semaphore) -> None:
    try:
        scoped = Config(**{**config.__dict__, "state_db_path": str(db_path)})
        result = evaluate(ctx, session_id=session_id, history=history, config=scoped, purpose="naming")
        logger.info("oil-hermes-title: %s → %s (%s)", session_id, result.get("title") or "-",
                    result.get("status"))
    except Exception:
        logger.debug("oil-hermes-title worker failed", exc_info=True)
    finally:
        semaphore.release()


def register_hooks(ctx: Any) -> None:
    def on_post_llm_call(**payload: Any) -> None:
        _handle_turn(ctx, payload)

    ctx.register_hook("post_llm_call", on_post_llm_call)
