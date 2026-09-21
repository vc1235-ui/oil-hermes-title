"""控制面。

两个入口，覆盖同一套动作：
- ``hermes oil-title <sub>``：CLI 子命令。会话 id 优先取 ``--session``，
  否则读环境变量 ``HERMES_SESSION_ID``（Agent 通过 terminal 调用时自动带入），
  所以「预览这个话题的新标题」这类自然语言请求可以由 Agent 直接执行。
- ``/oil-title <sub>``：会话内斜杠命令（CLI / gateway / 桌面）。斜杠命令拿不到
  当前会话 id，因此只提供无会话参数的动作（status / pause / resume / usage）。
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import oht_backfill as backfill
from . import oht_engine as engine
from . import oht_state as state_mod
from . import oht_store as store_mod

_CTX: Any = None

_SESSION_HELP = "会话 id / 前缀 / 标题（默认读 HERMES_SESSION_ID）"


def bind_context(ctx: Any) -> None:
    """由 register() 调用，供 CLI / 斜杠命令复用同一份插件上下文。"""
    global _CTX
    _CTX = ctx


def _ctx() -> Any:
    if _CTX is None:
        raise RuntimeError("oil-hermes-title 尚未注册（插件未启用？）")
    return _CTX


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 会话解析


def resolve_session_id(explicit: str = "", *, required: bool = True) -> Optional[str]:
    ident = (explicit or "").strip() or os.environ.get("HERMES_SESSION_ID", "").strip() \
        or os.environ.get("HERMES_SESSION_KEY", "").strip()
    if not ident:
        if required:
            raise SystemExit("找不到会话：请用 --session <id>，或在 Hermes 会话内由 Agent 调用"
                             "（此时 HERMES_SESSION_ID 会自动带入）")
        return None
    try:
        db = store_mod.open_db(store_mod.db_path(engine.load_config(_ctx()).state_db_path))
    except Exception:
        return ident
    return store_mod.resolve_session(db, ident) or ident


def _db():
    ctx = _ctx()
    return store_mod.open_db(store_mod.db_path(engine.load_config(ctx).state_db_path))


# ---------------------------------------------------------------- 动作实现


def action_status(session_id: Optional[str]) -> str:
    ctx = _ctx()
    config = engine.load_config(ctx)
    store = state_mod.StateStore(ctx)
    lines = [
        f"oil-hermes-title @ {_now()}",
        f"开关：{'关闭（enabled=false）' if not config.enabled else '开启'}"
        f"{'，已暂停（/oil-title resume 恢复）' if store.paused() else ''}",
        f"命名模型：ctx.llm（当前活动模型；需要固定便宜模型时设置 "
        f"plugins.entries.oil-hermes-title.llm.allow_model_override 与本插件 style/model 配置）"
        f"（超时 {config.timeout:g}s，并发 {config.max_parallel_workers}）",
        f"参考轮数：{config.history_turns}，单轮摘录上限 {config.max_chars_per_turn} 字符",
        f"纯确认跳过上限：{config.confirm_skip_limit} 轮",
    ]
    if session_id:
        try:
            db = _db()
            title, source = store_mod.read_title(db, session_id)
            session_state = store.session(session_id)
            lines += [
                f"当前会话：{session_id}",
                f"标题：{title or '(未命名)'}",
                f"标题权限：{source or '-'}（user = 已固定，自动命名不再改动；llm = 自动维护）",
                f"上次自动写入：{session_state.get('last_title') or '-'}",
            ]
        except Exception as exc:
            lines.append(f"读取会话失败：{exc}")
    return "\n".join(lines)


def action_preview(session_id: str, *, apply: bool = False, as_json: bool = False) -> str:
    ctx = _ctx()
    history = engine.load_history(ctx, session_id)
    result = engine.name_session(ctx, session_id=session_id, history=history, preview=not apply)
    if as_json:
        return json.dumps(result, ensure_ascii=False, indent=2)
    status = result.get("status")
    if status == "preview":
        return (f"候选标题：{result.get('title')}\n"
                f"当前标题：{result.get('previous') or '(未命名)'}\n"
                f"理由：{result.get('reason') or '-'}\n"
                f"（仅预览，未写入。确认后执行：hermes oil-title apply --session {session_id}）")
    if status in ("applied", "applied_deduped"):
        detail = f"（{result.get('detail')}）" if result.get("detail") else ""
        return f"已更新标题：{result.get('title')}{detail}"
    if status == "unchanged":
        return f"标题已是最新：{result.get('title')}"
    if status == "not_updated":
        return f"模型认为当前标题已经准确，保持原样：{result.get('title') or '(未命名)'}"
    if status == "locked":
        return f"标题已固定（user 权限），未改动：{result.get('title')}"
    if status == "invalid":
        return f"候选标题格式不合规，未写入：{result.get('candidate')!r} — {result.get('detail')}"
    return f"未更新（{status}）：{result.get('detail') or ''}"


def action_rename(session_id: str, title: str) -> str:
    db = _db()
    if not title.strip():
        return "标题不能为空。"
    store_mod.manual_rename(db, session_id, title.strip())
    return f"已重命名为：{title.strip()}（user 权限，自动命名不再改动；需要交还自动维护请执行 unlock）"


def action_lock(session_id: str, locked: bool) -> str:
    db = _db()
    if store_mod.set_lock(db, session_id, locked):
        return "已固定当前标题，自动命名不再改动它。" if locked else "已解除固定，标题交回自动维护。"
    return "操作失败：会话不存在，或当前没有标题可固定。"


def action_pause(paused: bool) -> str:
    state_mod.StateStore(_ctx()).set_paused(paused)
    return "已暂停自动命名（/oil-title resume 恢复）。" if paused else "已恢复自动命名。"


def action_usage(days: int, *, as_json: bool = False) -> str:
    summary = state_mod.StateStore(_ctx()).ledger_summary(days=days)
    if as_json:
        return json.dumps(summary, ensure_ascii=False, indent=2)
    if not summary["calls"]:
        return f"最近 {summary['days']} 天没有命名调用记录。"
    lines = [f"最近 {summary['days']} 天命名用量：{summary['calls']} 次调用"
             f"（其中 {summary['unknown_usage']} 次用量未知）"]
    for purpose, slot in sorted(summary["by_purpose"].items()):
        unknown = f"，{slot['unknown_usage']} 次未知" if slot["unknown_usage"] else ""
        lines.append(f"  · {purpose}: {slot['calls']} 次, tokens {slot['total_tokens']}{unknown}")
    for model, slot in sorted(summary["by_model"].items()):
        lines.append(f"  · {model}: {slot['calls']} 次, tokens {slot['total_tokens']}")
    lines.append("  状态：" + ", ".join(f"{k}×{v}" for k, v in sorted(summary["statuses"].items())))
    return "\n".join(lines)


def action_archive_preview(days: int, *, as_json: bool = False) -> str:
    rows = store_mod.idle_sessions(_db(), days)
    if as_json:
        return json.dumps(rows, ensure_ascii=False, indent=2)
    if not rows:
        return f"没有空闲超过 {days} 天且未固定的会话。"
    lines = [f"空闲超过 {days} 天、未固定、未归档的会话（{len(rows)} 个，仅预览）："]
    for row in rows:
        lines.append(f"  · {row['days_idle']:>3} 天  {row['id']}  {row['title']}")
    lines.append("确认后执行： hermes sessions archive --older-than "
                 f"{days}d   （固定/置顶的会话不会被归档）")
    return "\n".join(lines)


def action_backfill(*, days: int = 14, apply: bool = False, include_user: bool = False,
                    include_automation: bool = False, limit: int = 0, min_messages: int = 2,
                    as_json: bool = False) -> str:
    """批量回填历史会话标题。默认只列范围（不调用模型），``apply=True`` 才真跑。"""
    ctx = _ctx()
    config = engine.load_config(ctx)
    db = _db()
    store = state_mod.StateStore(ctx)
    rows = db.list_sessions_rich(limit=5000, order_by_last_active=True)
    selected, skipped = backfill.select_sessions(
        rows, days=days, now=time.time(), include_user=include_user,
        include_automation=include_automation, min_messages=min_messages, limit=limit)

    if not apply:
        if as_json:
            return json.dumps({"days": days, "selected": selected, "skipped": skipped},
                              ensure_ascii=False, indent=2)
        return backfill.describe_selection(selected, skipped, days=days, include_user=include_user,
                                           include_automation=include_automation)

    if not selected:
        return "没有需要回填的会话（范围预览见 `hermes oil-title backfill`）。"

    backup_path = backfill.backup_titles(db, selected, store.data_dir)
    lines: List[str] = []

    def _progress(index: int, total: int, change: Dict[str, Any]) -> None:
        lines.append(f"[{index}/{total}] {change['status']:>16}  {change['id']}  "
                     f"{change['from'] or '(未命名)'} → {change['to'] or '-'}")

    result = backfill.run_backfill(ctx, db, selected, config=config, progress=_progress)
    if as_json:
        return json.dumps({"backup": str(backup_path), **result}, ensure_ascii=False, indent=2)
    lines.append("")
    lines.append(f"回填 {result['total']} 条，耗时 {result['duration_s']}s；"
                 f"结果：{'、'.join(f'{k}×{v}' for k, v in sorted(result['statuses'].items()))}")
    lines.append(f"标题备份：{backup_path}")
    lines.append(f"回滚：hermes oil-title restore --file {backup_path} --apply")
    lines.append("用量：hermes oil-title usage")
    return "\n".join(lines)


def action_restore(path: str = "", *, apply: bool = False, as_json: bool = False) -> str:
    """回滚标题。不给 --file 时列出已有备份。"""
    db = _db()
    store = state_mod.StateStore(_ctx())
    directory = store.data_dir / backfill.BACKUP_DIRNAME
    if not path:
        if not directory.exists():
            return f"还没有标题备份：{directory}"
        files = sorted(directory.glob("*.json"), reverse=True)
        if not files:
            return f"还没有标题备份：{directory}"
        return "可用备份（新的在前）：\n" + "\n".join(
            f"  · {p.name}  {p.stat().st_size} bytes" for p in files[:15]) + \
            "\n回滚： hermes oil-title restore --file <备份路径> --apply"
    backup = backfill.load_backup(Path(path))
    if not apply:
        plan = []
        for session_id, record in (backup.get("sessions") or {}).items():
            current, _ = store_mod.read_title(db, session_id)
            want = (record or {}).get("title")
            if current != want:
                plan.append({"id": session_id, "current": current, "restore": want})
        if as_json:
            return json.dumps({"file": path, "changes": plan}, ensure_ascii=False, indent=2)
        if not plan:
            return f"{path}：所有会话标题与备份一致，无需回滚。"
        return (f"{path}：{len(plan)} 条会被还原（仅预览）：\n"
                + "\n".join(f"  · {c['id']}  {c['current']!r} → {c['restore']!r}" for c in plan[:25])
                + f"\n执行： hermes oil-title restore --file {path} --apply")
    stats = backfill.restore_titles(db, backup)
    if as_json:
        return json.dumps({"file": path, "stats": stats}, ensure_ascii=False, indent=2)
    return f"回滚完成：{'、'.join(f'{k}×{v}' for k, v in sorted(stats.items()))}"


def action_doctor() -> str:
    ctx = _ctx()
    config = engine.load_config(ctx)
    checks: List[tuple] = []
    checks.append(("插件上下文", True, "已绑定"))
    try:
        db = _db()
        checks.append(("会话库", True, str(store_mod.db_path(config.state_db_path))))
        checks.append(("会话可读", len(db.list_sessions_rich(limit=1)) >= 0, "list_sessions_rich ok"))
    except Exception as exc:
        checks.append(("会话库", False, str(exc)))
    try:
        from . import oht_naming as naming
        prompt_ok = len(naming.load_prompt(style="")) > 100
        checks.append(("命名规则文件", prompt_ok, str(naming.DEFAULT_PROMPT_PATH)))
    except Exception as exc:
        checks.append(("命名规则文件", False, str(exc)))
    try:
        sid = resolve_session_id(required=False)
        checks.append(("会话解析", True, sid or "未提供 --session 且不在会话内（不影响 hook）"))
    except Exception as exc:
        checks.append(("会话解析", False, str(exc)))
    lines = ["oil-hermes-title doctor", ""]
    ok_all = True
    for name, ok, detail in checks:
        ok_all = ok_all and ok
        lines.append(f"{'✓' if ok else '✗'} {name}: {detail}")
    lines.append("")
    lines.append("命名调用不会在这里真跑（避免消耗额度）；用 "
                 "`hermes oil-title preview --session <id>` 验证端到端。")
    lines.append(f"hook 超时约束：命名为后台线程，不占 post_llm_call 的 "
                 f"plugins.hook_callback_timeout（默认 30s）预算。")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def _setup_argparse(subparser) -> None:
    subparser.add_argument("--session", default="", help=_SESSION_HELP)
    subs = subparser.add_subparsers(dest="oil_title_command")

    subs.add_parser("status", help="显示开关、配置与当前会话标题状态")

    preview = subs.add_parser("preview", help="预览候选标题（不写入）")
    preview.add_argument("--json", action="store_true", help="输出 JSON")

    apply_cmd = subs.add_parser("apply", help="生成并采用候选标题")
    apply_cmd.add_argument("--json", action="store_true", help="输出 JSON")

    rename = subs.add_parser("rename", help="手动重命名（= 固定，user 权限）")
    rename.add_argument("title", nargs="+", help="新标题")

    subs.add_parser("lock", help="固定当前标题，自动命名不再改动它")
    subs.add_parser("unlock", help="解除固定，标题交回自动维护")
    subs.add_parser("pause", help="暂停自动命名")
    subs.add_parser("resume", help="恢复自动命名")

    usage = subs.add_parser("usage", help="查看命名用量")
    usage.add_argument("--days", type=int, default=7, help="统计最近多少天（默认 7）")
    usage.add_argument("--json", action="store_true", help="输出 JSON")

    archive = subs.add_parser("archive-preview", help="预览空闲可归档的会话")
    archive.add_argument("--days", type=int, default=3, help="空闲天数阈值（默认 3）")
    archive.add_argument("--json", action="store_true", help="输出 JSON")

    subs.add_parser("doctor", help="自检")

    backfill_cmd = subs.add_parser("backfill", help="批量回填历史会话标题（默认只列范围，不调用模型）")
    backfill_cmd.add_argument("--days", type=int, default=14, help="时间窗口，默认 14 天")
    backfill_cmd.add_argument("--apply", action="store_true", help="真的执行（先自动写标题备份）")
    backfill_cmd.add_argument("--include-user", action="store_true",
                              help="连 user 权限标题一起改（默认保护，不覆盖用户自己的命名）")
    backfill_cmd.add_argument("--include-automation", action="store_true",
                              help="连 cron/kanban/tool/oneshot 等自动化会话一起改（默认跳过）")
    backfill_cmd.add_argument("--limit", type=int, default=0, help="最多处理多少条（0 = 不限）")
    backfill_cmd.add_argument("--min-messages", type=int, default=2, help="消息数下限，默认 2")
    backfill_cmd.add_argument("--json", action="store_true", help="输出 JSON")

    restore_cmd = subs.add_parser("restore", help="按备份回滚标题（不给 --file 时列出备份）")
    restore_cmd.add_argument("--file", default="", help="标题备份 JSON 路径")
    restore_cmd.add_argument("--apply", action="store_true", help="真的写回（默认只预览差异）")
    restore_cmd.add_argument("--json", action="store_true", help="输出 JSON")

    # ``--session`` 在父 parser 与每个子命令上都可用：``hermes oil-title --session X lock``
    # 与 ``hermes oil-title lock --session X`` 都成立（SUPPRESS 保证子命令不覆盖父级已解析的值）。
    for _child in subs.choices.values():
        _child.add_argument("--session", default=argparse.SUPPRESS, help=_SESSION_HELP)

    subparser.set_defaults(func=_handle_cli)


def _handle_cli(args) -> int:
    command = getattr(args, "oil_title_command", None) or "status"
    explicit = getattr(args, "session", "") or ""
    try:
        if command == "status":
            print(action_status(resolve_session_id(explicit, required=False)))
            return 0
        if command == "preview":
            print(action_preview(resolve_session_id(explicit), apply=False, as_json=args.json))
            return 0
        if command == "apply":
            print(action_preview(resolve_session_id(explicit), apply=True, as_json=args.json))
            return 0
        if command == "rename":
            print(action_rename(resolve_session_id(explicit), " ".join(args.title)))
            return 0
        if command == "lock":
            print(action_lock(resolve_session_id(explicit), True))
            return 0
        if command == "unlock":
            print(action_lock(resolve_session_id(explicit), False))
            return 0
        if command == "pause":
            print(action_pause(True))
            return 0
        if command == "resume":
            print(action_pause(False))
            return 0
        if command == "usage":
            print(action_usage(args.days, as_json=args.json))
            return 0
        if command == "archive-preview":
            print(action_archive_preview(args.days, as_json=args.json))
            return 0
        if command == "doctor":
            print(action_doctor())
            return 0
        if command == "backfill":
            print(action_backfill(days=args.days, apply=args.apply, include_user=args.include_user,
                                  include_automation=args.include_automation, limit=args.limit,
                                  min_messages=args.min_messages, as_json=args.json))
            return 0
        if command == "restore":
            print(action_restore(getattr(args, "file", "") or "", apply=args.apply, as_json=args.json))
            return 0
    except SystemExit as exc:
        print(str(exc))
        return 1
    except Exception as exc:
        print(f"oil-hermes-title: {exc}")
        return 1
    print("用法：hermes oil-title <status|preview|apply|rename|lock|unlock|pause|resume|usage|"
          "archive-preview|backfill|restore|doctor>")
    return 1


# ---------------------------------------------------------------- 会话内斜杠命令


def handle_slash(raw_args: str) -> str:
    argv = [part for part in str(raw_args or "").split() if part]
    if not argv:
        argv = ["status"]
    action = argv[0].lower()
    try:
        if action == "status":
            return action_status(None)
        if action == "pause":
            return action_pause(True)
        if action == "resume":
            return action_pause(False)
        if action == "usage":
            days = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 7
            return action_usage(days)
        if action == "archive-preview":
            days = int(argv[1]) if len(argv) > 1 and argv[1].isdigit() else 3
            return action_archive_preview(days)
        if action == "doctor":
            return action_doctor()
        if action in ("preview", "apply", "lock", "unlock", "rename"):
            return (f"/oil-title {action} 需要指定话题，斜杠命令拿不到当前会话 id。\n"
                    f"直接对我说「{'预览' if action == 'preview' else '固定'}这个话题的标题」即可，"
                    f"或者用 CLI： hermes oil-title {action} --session <id>")
    except Exception as exc:
        return f"oil-hermes-title: {exc}"
    return ("用法：/oil-title <status|pause|resume|usage|archive-preview|doctor>；"
            "预览/固定/重命名请直接对我说，或用 hermes oil-title <sub> --session <id>")


def register_commands(ctx: Any) -> None:
    bind_context(ctx)
    ctx.register_cli_command(
        name="oil-title",
        help="会话标题实时命名：预览 / 采用 / 固定 / 暂停 / 用量 / 归档预览",
        setup_fn=_setup_argparse,
        handler_fn=_handle_cli,
        description="oil-hermes-title 的控制面（也供 Agent 通过 terminal 调用）",
    )
    ctx.register_command(
        "oil-title",
        handler=handle_slash,
        description="会话标题实时命名：status / pause / resume / usage / archive-preview",
        args_hint="<status|pause|resume|usage|archive-preview|doctor>",
    )