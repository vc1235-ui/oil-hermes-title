"""命名调用：摘录构建、提示词装配、模型调用、回复解析。

独立调用走宿主提供的 ``ctx.llm``（受 ``plugins.entries.oil-hermes-title.llm.*``
信任策略约束），不额外保存任何 API Key。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import oht_rules as rules

logger = logging.getLogger(__name__)

DEFAULT_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "naming.md"
MAX_TOKENS = 512
_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def load_prompt(path: Optional[Path] = None, *, style: str = "") -> str:
    prompt_path = Path(path) if path else DEFAULT_PROMPT_PATH
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
    except OSError:
        text = "你是会话命名助手。只输出 JSON：{\"updated\": bool, \"title\": \"\", \"reason\": \"\"}。"
    style = (style or "").strip()
    if style:
        text = f"{text}\n\n## 用户自定义风格（优先于上面的通用表述）\n{style}"
    return text


def build_excerpt(history: Sequence[Any], *, turns: int = 5, max_chars: int = 700) -> str:
    """最近若干轮 user/assistant 文本，压成一段给命名模型看的摘录。"""
    recent = rules.iter_recent_turns(history, max_turns=turns)
    if not recent:
        return ""
    per_message = max(120, int(max_chars) // max(1, len(recent)))
    lines: List[str] = []
    for turn in recent:
        text = turn["text"]
        if len(text) > per_message:
            head = text[: int(per_message * 0.7)]
            tail = text[-int(per_message * 0.3):]
            text = f"{head}…{tail}"
        lines.append(f"{'用户' if turn['role'] == 'user' else '助手'}: {text}")
    return "\n".join(lines)[: max(200, int(max_chars) * 3)]


def parse_reply(text: Any) -> Dict[str, Any]:
    """解析模型回复 → {"updated", "title", "reason", "parsed": bool}。"""
    raw = str(text or "").strip()
    if not raw:
        return {"updated": False, "title": "", "reason": "", "parsed": False}
    match = _JSON_BLOCK_RE.search(raw)
    if match:
        try:
            payload = json.loads(match.group(0))
            if isinstance(payload, dict):
                title = payload.get("title")
                updated = payload.get("updated")
                if isinstance(updated, str):
                    updated = updated.strip().lower() in ("true", "1", "yes", "是")
                return {"updated": bool(updated) if updated is not None else bool(title),
                        "title": rules.clean_candidate(title), "reason": str(payload.get("reason") or "")[:80],
                        "parsed": True}
        except Exception:
            logger.debug("naming reply was not valid JSON; falling back to first line")
    return {"updated": True, "title": rules.clean_candidate(raw), "reason": "", "parsed": False}


def request_title(llm: Any, *, prompt: str, excerpt: str, current_title: Optional[str], language: str,
                  timeout: float, model: str = "", max_tokens: int = MAX_TOKENS) -> Dict[str, Any]:
    """跑一次命名调用。→ {"ok", "updated", "title", "reason", "model", "usage", "error"}"""
    current = (current_title or "").strip() or "(无)"
    user_block = [f"当前标题：{current}"]
    if language:
        user_block.append(f"语言：{language}")
    user_block.append("最近对话：\n" + (excerpt or "(无可用对话)"))
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": "\n\n".join(user_block)}]

    model_override = (model or "").strip()
    for attempt_model in ([model_override, ""] if model_override else [""]):
        try:
            result = llm.complete(messages=messages, max_tokens=max_tokens, timeout=timeout,
                                  model=attempt_model or None, purpose="oil-hermes-title")
        except Exception as exc:
            # 模型覆盖需要 plugins.entries.<id>.llm.allow_model_override + 白名单；
            # 未授权时降级为「用当前模型」，而不是整个功能失败。
            if attempt_model and exc.__class__.__name__ == "PluginLlmTrustError":
                logger.warning("oil-hermes-title: model override %r denied, falling back to the active model",
                               attempt_model)
                continue
            logger.debug("naming call failed", exc_info=True)
            return {"ok": False, "updated": False, "title": "", "reason": "", "model": attempt_model, "usage": {},
                    "error": f"{exc.__class__.__name__}: {exc}"}
        parsed = parse_reply(getattr(result, "text", ""))
        usage = getattr(result, "usage", None)
        return {"ok": True, "updated": parsed["updated"], "title": parsed["title"], "reason": parsed["reason"],
                "model": getattr(result, "model", "") or attempt_model,
                "provider": getattr(result, "provider", ""),
                "usage": {"input_tokens": getattr(usage, "input_tokens", None),
                          "output_tokens": getattr(usage, "output_tokens", None),
                          "total_tokens": getattr(usage, "total_tokens", None),
                          "cost_usd": getattr(usage, "cost_usd", None)} if usage else {},
                "error": "", "parsed": parsed.get("parsed", False)}
    return {"ok": False, "updated": False, "title": "", "reason": "", "model": model_override, "usage": {},
            "error": "no usable model route"}


def repair_hint(reason: str) -> str:
    """格式不合格时追加的一次性纠正指令。"""
    return (f"上一版标题不合规（{reason}）。只输出 JSON，title 必须形如 "
            f"「🧩 对象｜目标」：以固定类别 emoji 开头，之后一个空格，恰好一个全角 ｜，"
            f"对象在前、目标在后，总长 ≤ 40 字。")


def usage_record(result: Dict[str, Any], *, purpose: str, session_id: str, duration_ms: int, status: str
                 ) -> Dict[str, Any]:
    usage = result.get("usage") or {}

    def _int(value: Any) -> Optional[int]:
        return int(value) if isinstance(value, int) else None

    return {"purpose": purpose, "session_id": session_id, "model": result.get("model") or "",
            "input_tokens": _int(usage.get("input_tokens")), "output_tokens": _int(usage.get("output_tokens")),
            "total_tokens": _int(usage.get("total_tokens")), "cost_usd": usage.get("cost_usd"),
            "duration_ms": int(duration_ms), "status": status}