"""历史会话回填：按命名规则批量改写既有会话标题。

设计约束（都来自插件已确立的规则，不新发明）：
- 默认**不碰** ``user`` 权限标题 —— 那是用户自己的命名，插件的"固定"语义就是保护它。
- 默认**跳过**自动化来源（cron / kanban / tool / batch / oneshot / subagent）—— 与实时钩子一致。
- ``--apply`` 前先写一份标题备份（JSON），并提供 ``restore`` 回滚，批量改写不留死角。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# 与 oht_engine._SKIP_SURFACES 保持同一集合（自动化会话无人在看，命名是噪音）
AUTOMATION_SOURCES = frozenset({"cron", "kanban", "tool", "batch", "oneshot", "subagent"})
BACKUP_DIRNAME = "title-backups"


def select_sessions(
    rows: Iterable[Dict[str, Any]], *, days: int, now: float,
    include_user: bool = False, include_automation: bool = False,
    min_messages: int = 2, limit: int = 0, exclude: Sequence[str] = (),
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """按窗口与规则挑出待回填的会话 → (selected, skipped 原因计数)。

    ``exclude``: 显式排除的会话 id —— 有些自动化会话（例如 cron 任务用
    ``hermes chat --source cli`` 起的）在库里看不出自动化痕迹，只能靠 id 排除。
    """
    cutoff = now - max(1, int(days)) * 86400
    excluded = {str(item).strip() for item in exclude if str(item).strip()}
    selected: List[Dict[str, Any]] = []
    skipped: Counter = Counter()
    for row in rows or []:
        session_id = str(row.get("id") or "")
        last_active = _as_float(row.get("last_active") or row.get("started_at"))
        if last_active < cutoff:          # 窗口外：不算"被跳过"，只是不在范围
            continue
        if session_id in excluded:
            skipped["excluded"] += 1
            continue
        if row.get("archived") or row.get("hidden"):
            skipped["archived_or_hidden"] += 1
            continue
        source = str(row.get("source") or "")
        if not include_automation and source in AUTOMATION_SOURCES:
            skipped[f"automation:{source}"] += 1
            continue
        if int(row.get("message_count") or 0) < max(0, int(min_messages)):
            skipped["too_few_messages"] += 1
            continue
        title = row.get("title")
        title_source = str(row.get("title_source") or "")
        if not include_user and title_source == "user" and title:
            skipped["user_title_protected"] += 1
            continue
        selected.append({
            "id": str(row.get("id") or ""),
            "source": source,
            "title": title,
            "title_source": title_source or None,
            "message_count": int(row.get("message_count") or 0),
            "last_active": last_active,
        })
    if limit and limit > 0:
        selected = selected[: int(limit)]
    return selected, dict(skipped)


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def backup_titles(db, sessions: Sequence[Dict[str, Any]], data_dir: Path) -> Path:
    """把待改写会话的标题现状落盘 → 备份文件路径。"""
    directory = Path(data_dir) / BACKUP_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{stamp}.json"
    payload = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "sessions": {},
    }
    for entry in sessions:
        session_id = entry["id"]
        title, source = entry.get("title"), entry.get("title_source")
        payload["sessions"][session_id] = {"title": title, "title_source": source}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_backup(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        raise ValueError(f"备份文件格式不对: {path}")
    return data


def restore_titles(db, backup: Dict[str, Any], *, only_changed: bool = True) -> Dict[str, int]:
    """把备份里的标题写回。原权限是 llm/derived 的会一并还原（不能让回滚把标题变成"用户固定"）。"""
    from . import oht_store as store_mod

    stats: Counter = Counter()
    for session_id, record in (backup.get("sessions") or {}).items():
        if not isinstance(record, dict):
            continue
        want_title = record.get("title")
        want_source = record.get("title_source") or ""
        current, _ = store_mod.read_title(db, session_id)
        if only_changed and current == want_title:
            stats["unchanged"] += 1
            continue
        if not want_title:
            # 备份里本来就没有标题 → 清空，才叫"还原"
            try:
                db.set_session_title(session_id, "")
            except Exception:
                stats["clear_failed"] += 1
                continue
            stats["cleared"] += 1
            continue
        try:
            ok = db.set_session_title(session_id, want_title)   # user 权限写入，必然落地
        except ValueError:
            # 唯一性冲突：原标题已被别的会话占用（回滚时可能发生）
            stats["conflict"] += 1
            continue
        if not ok:
            stats["missing"] += 1
            continue
        if want_source and want_source != store_mod.USER_SOURCE:
            db.set_session_title_source(session_id, want_source)
        stats["restored"] += 1
    return dict(stats)


def run_backfill(ctx, db, sessions: Sequence[Dict[str, Any]], *, config, progress=None) -> Dict[str, Any]:
    """逐条回填（串行：一次模型调用一条，避免打爆额度与端点）。"""
    from . import oht_engine as engine

    statuses: Counter = Counter()
    changes: List[Dict[str, Any]] = []
    started = time.time()
    for index, entry in enumerate(sessions, start=1):
        session_id = entry["id"]
        history = engine.load_history(ctx, session_id, limit=40)
        result = engine.evaluate(ctx, session_id=session_id, history=history, config=config,
                                 purpose="backfill")
        statuses[str(result.get("status"))] += 1
        change = {"id": session_id, "from": entry.get("title"), "to": result.get("title"),
                  "status": result.get("status"), "detail": result.get("detail") or ""}
        changes.append(change)
        if progress:
            progress(index, len(sessions), change)
    return {"days": None, "total": len(sessions), "statuses": dict(statuses), "changes": changes,
            "duration_s": round(time.time() - started, 1)}


def describe_selection(selected: Sequence[Dict[str, Any]], skipped: Dict[str, int], *, days: int,
                       include_user: bool, include_automation: bool) -> str:
    lines = [f"回填范围：最近 {days} 天"
             f"（用户标题 {'包含' if include_user else '保护不覆盖'}，"
             f"自动化会话 {'包含' if include_automation else '跳过'}）",
             f"待回填 {len(selected)} 条；跳过：{_fmt_skipped(skipped)}"]
    if not selected:
        return "\n".join(lines)
    lines.append("")
    for entry in selected[:25]:
        lines.append(f"  · {entry['id']}  [{entry['source']}/{entry['title_source'] or '-'}]  "
                     f"{entry['title'] or '(未命名)'}")
    if len(selected) > 25:
        lines.append(f"  … 其余 {len(selected) - 25} 条")
    lines.append("")
    lines.append("这只是范围预览（未调用模型）。执行： hermes oil-title backfill --apply "
                 f"--days {days}" + ("  --include-user" if include_user else "")
                 + ("  --include-automation" if include_automation else ""))
    return "\n".join(lines)


def _fmt_skipped(skipped: Dict[str, int]) -> str:
    if not skipped:
        return "无"
    order = sorted(skipped.items(), key=lambda kv: (-kv[1], kv[0]))
    return "、".join(f"{key}×{value}" for key, value in order)