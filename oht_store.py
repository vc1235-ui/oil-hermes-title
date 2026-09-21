"""state.db 访问层：读标题、按 provenance 规则写标题、固定/解除、闲置会话列举。

**核心坑（上游没有、Hermes 特有）**：``sessions.title_source`` 的权限是单调的
（derived=0 < llm=1 < user=2），且 ``_set_session_title`` 的判定是
``当前 rank >= 新 rank → 直接放弃``。也就是说自动命名写成 ``llm`` 之后，
**再写一次 ``llm`` 是空操作**。要在后续回合继续更新标题，必须先
``set_session_title_source(sid, "derived")`` 降级、再写 ``llm``。
用户手动改过的标题是 ``user``，永远不会被自动写覆盖 —— 这就是免费的「固定」。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

USER_SOURCE = "user"
LLM_SOURCE = "llm"
DERIVED_SOURCE = "derived"


def hermes_home() -> Path:
    """当前 profile 的 Hermes home。

    调用点必须落在「回合线程」上：gateway 用 ContextVar 绑定 profile，
    新起的线程不继承它。所以 hook 里先解析好路径，再传给后台线程。
    """
    from hermes_constants import get_hermes_home
    return Path(get_hermes_home())


def db_path(override: str = "") -> Path:
    if override and str(override).strip():
        return Path(os.path.expanduser(str(override).strip()))
    return hermes_home() / "state.db"


def open_db(path: Optional[Path] = None):
    """打开 SessionDB（导入放函数内：插件加载要轻）。"""
    from hermes_state import SessionDB
    return SessionDB(db_path=path) if path is not None else SessionDB()


def resolve_session(db, ident: str) -> Optional[str]:
    """把 id / 前缀 / 标题解析成会话 id。"""
    ident = (ident or "").strip()
    if not ident:
        return None
    try:
        resolved = db.resolve_session_id(ident)
    except Exception:
        resolved = None
    if resolved:
        return resolved
    try:
        return db.resolve_session_by_title(ident)
    except Exception:
        return None


def read_title(db, session_id: str) -> Tuple[Optional[str], Optional[str]]:
    """→ (title, title_source)；无标题时 title_source 为 None。"""
    try:
        title = db.get_session_title(session_id)
    except Exception:
        title = None
    source = None
    if title is not None:
        try:
            source = db.get_session_title_source(session_id)
        except Exception:
            source = None
        if source is None:
            # 老行（迁移前）没有 provenance，语义上等价于用户手改
            source = USER_SOURCE
    return title, source


def is_locked(db, session_id: str) -> bool:
    _, source = read_title(db, session_id)
    return source == USER_SOURCE


def write_auto_title(db, session_id: str, title: str) -> Dict[str, Any]:
    """按 provenance 规则写入自动标题。→ {"status", "title", "detail"}"""
    current, source = read_title(db, session_id)
    if source == USER_SOURCE:
        return {"status": "locked", "title": current,
                "detail": "标题已被用户/其它写入者固定为 user 权限，自动命名不再改动"}
    if current == title:
        return {"status": "unchanged", "title": current, "detail": "标题已是最新"}

    attempts: List[str] = [title]
    try:
        deduped = db.get_next_title_in_lineage(title)
    except Exception:
        deduped = None
    if deduped and deduped != title:
        attempts.append(deduped)

    last_error = ""
    for attempt in attempts:
        if source == LLM_SOURCE:
            # 降级再写：同 rank 重写会被 provenance 判定静默拒绝
            try:
                db.set_session_title_source(session_id, DERIVED_SOURCE)
            except Exception as exc:  # pragma: no cover - 只在 DB 异常时发生
                last_error = f"provenance downgrade failed: {exc}"
                continue
        try:
            ok = db.set_auto_title(session_id, attempt, source=LLM_SOURCE)
        except ValueError as exc:
            last_error = str(exc)
            continue
        except Exception as exc:  # pragma: no cover
            last_error = str(exc)
            continue
        if ok:
            status = "applied" if attempt == title else "applied_deduped"
            return {"status": status, "title": attempt, "detail": "" if attempt == title else "标题重名，已自动加序号"}
        # rowcount 0：被更高权限挡住（读后被抢占），重新读一次给出原因
        _, now_source = read_title(db, session_id)
        if now_source == USER_SOURCE:
            return {"status": "locked", "title": current, "detail": "写入前被手动标题抢占"}
        last_error = last_error or "write rejected by title store"
    return {"status": "conflict", "title": current, "detail": last_error or "写入冲突"}


def set_lock(db, session_id: str, locked: bool) -> bool:
    """固定 = 把标题权限抬到 user（自动命名从此不再改动它）；解除 = 降回 llm。"""
    title, source = read_title(db, session_id)
    if title is None:
        return False
    target = USER_SOURCE if locked else LLM_SOURCE
    if source == target:
        return True
    try:
        return bool(db.set_session_title_source(session_id, target))
    except Exception as exc:
        logger.debug("set_lock(%s, %s) failed: %s", session_id, locked, exc)
        return False


def manual_rename(db, session_id: str, title: str) -> bool:
    """写入用户标题（= 固定）。空标题清空。"""
    return bool(db.set_session_title(session_id, title))


def idle_sessions(db, days: int, *, limit: int = 30) -> List[Dict[str, Any]]:
    """空闲超过 days 天、未固定、未归档的会话（只读预览，用于归档建议）。"""
    import time
    cutoff = time.time() - max(1, int(days)) * 86400
    try:
        rows = db.list_sessions_rich(limit=max(1, int(limit)) * 4, order_by_last_active=True)
    except Exception:
        rows = db.list_sessions_rich(limit=max(1, int(limit)) * 4)
    idle: List[Dict[str, Any]] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("archived") or row.get("hidden") or row.get("pinned"):
            continue
        last_active = row.get("last_active") or row.get("started_at") or 0
        try:
            last_active = float(last_active)
        except (TypeError, ValueError):
            continue
        if last_active and last_active < cutoff:
            idle.append({"id": row.get("id"), "title": row.get("title") or "(未命名)",
                         "last_active": last_active, "days_idle": int((time.time() - last_active) / 86400)})
        if len(idle) >= limit:
            break
    return idle
