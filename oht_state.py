"""插件自有状态 + 用量账本。

- 开关、每会话状态：``ctx.state``（profile 隔离的 JSON，10MB 配额）
- 用量账本：``<plugin-data>/usage/YYYY-MM-DD.jsonl``，与上游 oil-codex-title 的
  ``usage/日期/`` 布局对应，只记时间、用途、模型、会话、用量、耗时、状态，
  不记标题原文、不记对话内容。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_MAX_SESSIONS = 400          # 状态里最多保留多少个会话的记账，超过按最近使用裁剪
_KEY_SESSIONS = "sessions"


class StateStore:
    def __init__(self, ctx: Any) -> None:
        self._state = ctx.state

    # ---- 全局开关 ----
    def paused(self) -> bool:
        return bool(self._state.get("paused", False))

    def set_paused(self, paused: bool) -> None:
        self._state.set("paused", bool(paused))

    # ---- 每会话状态 ----
    def _sessions(self) -> Dict[str, Any]:
        data = self._state.get(_KEY_SESSIONS, {})
        return data if isinstance(data, dict) else {}

    def session(self, session_id: str) -> Dict[str, Any]:
        entry = self._sessions().get(session_id, {})
        return entry if isinstance(entry, dict) else {}

    def update_session(self, session_id: str, **fields: Any) -> None:
        sessions = self._sessions()
        entry = sessions.get(session_id)
        entry = dict(entry) if isinstance(entry, dict) else {}
        entry.update(fields)
        entry["updated_at"] = time.time()
        sessions[session_id] = entry
        if len(sessions) > _MAX_SESSIONS:
            newest = sorted(sessions.items(), key=lambda kv: float((kv[1] or {}).get("updated_at") or 0),
                            reverse=True)[:_MAX_SESSIONS]
            sessions = dict(newest)
        self._state.set(_KEY_SESSIONS, sessions)

    def forget_session(self, session_id: str) -> None:
        sessions = self._sessions()
        if session_id in sessions:
            sessions.pop(session_id, None)
            self._state.set(_KEY_SESSIONS, sessions)

    # ---- 用量账本 ----
    @property
    def usage_dir(self) -> Path:
        return Path(self._state.data_dir) / "usage"

    def ledger_append(self, entry: Dict[str, Any]) -> None:
        try:
            day = datetime.now().strftime("%Y-%m-%d")
            self.usage_dir.mkdir(parents=True, exist_ok=True)
            record = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
            with open(self.usage_dir / f"{day}.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("ledger append failed", exc_info=True)

    def ledger_summary(self, days: int = 7) -> Dict[str, Any]:
        """按用途/模型汇总最近 days 天的用量。未知不等于零：缺字段单独计数。"""
        today = datetime.now().date()
        files = []
        for offset in range(max(1, int(days))):
            day = today - timedelta(days=offset)
            path = self.usage_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
            if path.exists():
                files.append(path)
        calls = 0
        by_purpose: Dict[str, Dict[str, int]] = {}
        by_model: Dict[str, Dict[str, int]] = {}
        statuses: Dict[str, int] = {}
        unknown_usage = 0
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except Exception:
                    continue
                calls += 1
                purpose = str(record.get("purpose") or "unknown")
                model = str(record.get("model") or "unknown")
                status = str(record.get("status") or "unknown")
                tokens = record.get("total_tokens")
                input_tokens = record.get("input_tokens")
                output_tokens = record.get("output_tokens")
                statuses[status] = statuses.get(status, 0) + 1
                for bucket, key in ((by_purpose, purpose), (by_model, model)):
                    slot = bucket.setdefault(key, {"calls": 0, "total_tokens": 0, "unknown_usage": 0})
                    slot["calls"] += 1
                    if isinstance(tokens, int):
                        slot["total_tokens"] += tokens
                    else:
                        slot["unknown_usage"] += 1
                    if isinstance(input_tokens, int):
                        slot["input_tokens"] = slot.get("input_tokens", 0) + input_tokens
                    if isinstance(output_tokens, int):
                        slot["output_tokens"] = slot.get("output_tokens", 0) + output_tokens
                if not isinstance(tokens, int):
                    unknown_usage += 1
        return {"days": max(1, int(days)), "calls": calls, "unknown_usage": unknown_usage,
                "by_purpose": by_purpose, "by_model": by_model, "statuses": statuses,
                "files": [str(p) for p in files]}