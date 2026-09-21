"""纯逻辑：消息判别、语言判断、标题格式校验。不碰 DB，不碰网络，便于单测。"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable, List, Optional, Sequence, Tuple

# 类别 emoji —— 与上游 oil-codex-title 的固定类别表一致
CATEGORY_EMOJI = {
    "🎬": "内容制作",
    "🧩": "工具开发",
    "🔎": "对比调研",
    "📝": "方法整理与文档",
    "📅": "日程安排",
    "🎨": "页面设计",
    "⚙️": "环境配置",
    "💬": "一般讨论",
}
EMOJI_SET = frozenset(CATEGORY_EMOJI)

SEPARATOR = "｜"          # 全角竖线，恰好一个
MAX_TITLE_CHARS = 60      # 低于 Hermes 的 100 上限，给 "#N" 去重后缀留空间
MAX_EXCERPT_TURNS = 12

# 纯确认（不构成新的主要目标）
CONFIRM_PHRASES = frozenset({
    "好的", "好", "嗯", "恩", "收到", "了解", "明白", "谢谢", "多谢", "感谢", "继续", "可以", "行",
    "是的", "是", "对", "没错", "辛苦", "辛苦了", "试试", "ok", "okay", "k", "kk", "yes", "yep", "yeah",
    "cool", "nice", "great", "thanks", "thank you", "thx", "got it", "understood", "continue", "go on",
    "go ahead", "lgtm", "👍", "🙏",
})
_CONFIRM_RE = re.compile(
    r"^(?:好|嗯|恩|收到|了解|明白|继续|谢谢|多谢|感谢|可以|行|ok|okay|thanks|thx|👍)+"
    r"[!！。.~～\s]*$", re.IGNORECASE)

# 机械性/无实义的用户消息
_MACHINERY_RE = re.compile(r"^[/]?[a-z][a-z0-9_-]{1,30}$", re.IGNORECASE)   # 斜杠命令、单 token 指令
_PUNCT_ONLY_RE = re.compile(r"^[\W_]+$", re.UNICODE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n?|\n?```$")
_LEADING_LABEL_RE = re.compile(r"^\s*(?:标题|title|session\s*title)\s*[:：]\s*", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


def flatten_text(content: Any) -> str:
    """把消息 content（str / 多段列表）压成纯文本；忽略非文本块。"""
    if isinstance(content, str):
        return content
    if isinstance(content, (list, tuple)):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if str(block.get("type") or "text") in ("text", "input_text", "output_text"):
                    text = block.get("text")
                    if isinstance(text, str):
                        parts.append(text)
        return "\n".join(parts)
    return ""


def normalize_text(text: Any) -> str:
    return _WS_RE.sub(" ", flatten_text(text)).strip()


def is_pure_confirmation(text: Any) -> bool:
    """整条消息只是确认/客套 → 不该触发重新命名。"""
    normalized = normalize_text(text)
    if not normalized or len(normalized) > 24:
        return False
    lowered = normalized.rstrip("!！。.~～ ").lower()
    if lowered in CONFIRM_PHRASES:
        return True
    return bool(_CONFIRM_RE.match(normalized))


def is_titleable(text: Any, *, min_chars: int = 4) -> bool:
    """这条用户消息是否值得作为命名输入。"""
    normalized = normalize_text(text)
    if len(normalized) < max(1, int(min_chars)):
        return False
    if is_pure_confirmation(normalized):
        return False
    if _PUNCT_ONLY_RE.match(normalized):
        return False
    stripped = normalized.lstrip("/")
    if _MACHINERY_RE.match(stripped) and len(normalized.split()) == 1:
        return False
    return True


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    cjk = len(_CJK_RE.findall(text))
    letters = len(re.findall(r"[A-Za-z]", text))
    total = cjk + letters
    return (cjk / total) if total else 0.0


def guess_language(user_texts: Sequence[str]) -> str:
    """按最近几轮用户消息的主要语言给出提示词用的语言名（空串 = 交给模型判断）。"""
    joined = " ".join(t for t in user_texts if t)[:4000]
    if not joined.strip():
        return ""
    ratio = cjk_ratio(joined)
    if ratio >= 0.35:
        return "中文（保留产品名与技术名词原文）"
    if ratio <= 0.05:
        return "English (keep product and technical names as-is)"
    return ""


def fingerprint(user_texts: Sequence[str]) -> str:
    """最近几轮用户消息的内容指纹：用来发现"这一轮和上一轮内容相同"。"""
    payload = "\n".join(normalize_text(t) for t in user_texts if normalize_text(t))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def clean_candidate(raw: Any) -> str:
    """去掉围栏、引号、'标题:' 前缀，取第一行。"""
    text = str(raw or "").strip()
    text = _FENCE_RE.sub("", text)
    text = text.strip().strip('"“”\'`')
    text = _LEADING_LABEL_RE.sub("", text)
    first = text.splitlines()[0] if text.splitlines() else ""
    return _WS_RE.sub(" ", first).strip().strip('"“”\'`').strip()


def validate_title(raw: Any) -> Tuple[Optional[str], str]:
    """校验并规范化候选标题 → (规范化标题 | None, 原因)。"""
    title = clean_candidate(raw)
    if not title:
        return None, "empty"
    if len(title) > MAX_TITLE_CHARS:
        return None, f"too_long({len(title)})"
    if title.count(SEPARATOR) != 1:
        return None, f"separator_count({title.count(SEPARATOR)})"
    head, tail = (part.strip() for part in title.split(SEPARATOR))
    if not head or not tail:
        return None, "empty_part"
    emoji = head[0]
    if emoji not in EMOJI_SET:
        return None, f"bad_emoji({emoji!r})"
    obj = head[1:].strip()
    if not obj:
        return None, "empty_object"
    normalized = f"{emoji} {obj}{SEPARATOR}{tail}"
    if len(normalized) > MAX_TITLE_CHARS:
        return None, f"too_long({len(normalized)})"
    return normalized, "ok"


def iter_recent_turns(history: Iterable[Any], *, max_turns: int = MAX_EXCERPT_TURNS) -> List[dict]:
    """从会话历史里取最近若干条 user/assistant 文本消息（时间正序）。"""
    collected: List[dict] = []
    user_turns = 0
    for message in reversed(list(history or [])):
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        if role not in ("user", "assistant"):
            continue
        text = normalize_text(message.get("content"))
        if not text:
            continue
        collected.append({"role": role, "text": text})
        if role == "user":
            user_turns += 1
            if user_turns >= max(1, int(max_turns)):
                break
    collected.reverse()
    return collected


def recent_user_texts(history: Iterable[Any], *, max_turns: int = 5) -> List[str]:
    texts: List[str] = []
    for turn in reversed(iter_recent_turns(history, max_turns=max_turns)):
        if turn["role"] == "user":
            texts.append(turn["text"])
    texts.reverse()
    return texts
