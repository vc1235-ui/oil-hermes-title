"""oil-hermes-title — 会话标题实时命名（Hermes 移植版）。

改编自 oil-oil/oil-codex-title（MIT）：每轮对话结束后，由独立后台调用参考最近几轮
内容更新会话标题，统一采用「类别 emoji + 对象｜目标」。

与上游的对应关系（Codex → Hermes）：
- Stop Hook                      → ``post_llm_call`` 插件钩子（payload 直接带
                                   session_id / user_message / conversation_history）
- 独立 Luna Fast 会话             → 宿主提供的 ``ctx.llm``（跑用户当前模型/鉴权）
- App Server ``thread/name/set``  → ``SessionDB.set_auto_title(source="llm")``
- 「手动改名即锁定」               → Hermes 的 title provenance（user > llm > derived）
- 插件自带 Skill                  → 一段系统提示段落 + 插件 Skill + CLI/斜杠控制面
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).resolve().parent
SKILL_PATH = PLUGIN_DIR / "skills" / "oil-hermes-title" / "SKILL.md"

# 让 Agent 知道这套控制面的存在（等价于上游插件内置的 Skill 被发现）。
# 只写触发条件与命令，细节留在 Skill / README 里，控制每轮提示词开销。
_AGENT_HINT = """## oil-hermes-title（会话标题实时命名）
本实例装有 oil-hermes-title：每轮对话结束后，独立后台调用会把会话标题更新成「类别 emoji + 对象｜目标」，标题只跟随持续主线，不被"继续/修复/推一下"这类临时动作带偏。
用户说「预览这个话题的新标题」「固定这个话题的标题」「暂停/恢复自动命名」「查看命名用量」「预览可以归档的闲置话题」时，用 terminal 执行对应命令（当前会话 id 由环境变量 HERMES_SESSION_ID 自动带入，不要手填）：
- `hermes oil-title preview`（只预览，不写入）/ `hermes oil-title apply`（采用候选标题）
- `hermes oil-title lock`（固定当前标题）/ `hermes oil-title unlock`（解除固定）
- `hermes oil-title pause` / `hermes oil-title resume`
- `hermes oil-title usage` / `hermes oil-title archive-preview`
- `hermes oil-title rename "<标题>"`（用户指定标题 = 固定）
不要每次对话都主动运行这些命令；只在用户提出与标题相关的要求时执行。边界与排错见 skill_view('oil-hermes-title:oil-hermes-title')。"""


def register(ctx):
    """插件入口：注册命名钩子、控制面与提示段落。"""
    from . import oht_cli, oht_engine

    oht_engine.register_hooks(ctx)
    oht_cli.register_commands(ctx)

    try:
        ctx.register_system_prompt_section("oil-hermes-title", _AGENT_HINT)
    except Exception as exc:  # 段落注册失败不影响命名主链路
        logger.warning("oil-hermes-title: system prompt section not registered (%s)", exc)

    if SKILL_PATH.exists():
        try:
            ctx.register_skill("oil-hermes-title", SKILL_PATH,
                               description="会话标题实时命名：预览 / 固定 / 暂停 / 用量与排错")
        except Exception as exc:
            logger.warning("oil-hermes-title: skill not registered (%s)", exc)

    logger.info("oil-hermes-title registered (hooks: post_llm_call; commands: oil-title)")
