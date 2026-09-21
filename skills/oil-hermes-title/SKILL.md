---
name: oil-hermes-title
description: "会话标题实时命名：预览/固定/暂停、用量与排错。Use when the user asks to preview, pin, lock, rename, pause, or audit the current session title, or when title renaming looks broken."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [sessions, titles, automation]
    category: productivity
---

# oil-hermes-title（会话标题实时命名）

本插件在每次 ``post_llm_call`` 之后，用独立后台调用把会话标题更新为
「类别 emoji + 对象｜目标」。控制面全部通过 ``hermes oil-title`` 暴露。

## 用户说什么 → 你执行什么

当前会话 id 由环境变量 ``HERMES_SESSION_ID`` 自动带入命令，**不要**让用户提供 id，
也不要把 id 写进命令里。所有命令用 terminal 执行。
需要操作别的会话时才传 ``--session <id>``（放子命令前或后都行）。

| 用户的话 | 命令 |
| --- | --- |
| 「预览这个话题的新标题」 | `hermes oil-title preview` |
| 「就用这个标题」/「改吧」 | `hermes oil-title apply` |
| 「固定这个话题的标题」/「别再改标题」 | `hermes oil-title lock` |
| 「标题交回自动维护」 | `hermes oil-title unlock` |
| 「把标题改成 X」 | `hermes oil-title rename "X"` |
| 「暂停自动命名」 | `hermes oil-title pause` |
| 「恢复自动命名」 | `hermes oil-title resume` |
| 「看下命名用量」 | `hermes oil-title usage [--days 7]` |
| 「预览可以归档的闲置话题」 | `hermes oil-title archive-preview [--days 3]` |
| 「检查标题功能是否正常」 | `hermes oil-title doctor` |

`preview` / `apply` / `lock` / `unlock` / `rename` 都是单会话动作；`pause` / `resume` /
`usage` / `archive-preview` / `doctor` 是全局动作，斜杠命令 `/oil-title <sub>` 也能用。

## 回答方式

- 把命令输出转述给用户，不要贴原始 JSON（除用户要 `--json`）。
- `preview` 之后必须等用户确认再 `apply`；不要自作主张写入标题。
- `archive-preview` 只是预览。真正归档用 `hermes sessions archive`，并且**先跟用户确认**。

## 内置行为（不用你去调用）

- 每轮对话结束后自动评估标题，只有主题实质变化才更新。
- 用户手动改过的标题（`/title`、桌面重命名、`rename`）权限更高，自动命名不再覆盖它 ——
  这就是「固定」；`lock` 只是把当前标题抬到同一权限。
- 纯确认轮（好的/收到/继续）在标题稳定后最多连续跳过 2 轮，不浪费模型调用。

## 排错

1. `hermes oil-title doctor` —— 会话库、命名规则文件、会话解析是否正常。
2. `hermes oil-title status` —— 开关、暂停状态、配置生效值。
3. 标题一直不变？
   - `status` 里标题权限是 `user` → 标题已被固定，`unlock` 交回自动维护。
   - 权限是 `llm` 但仍然不变 → 看 `usage` 是否有调用记录；没有记录说明这一轮被跳过
     （纯确认、内容重复、并发已满）或 `platform` 属于自动化会话（cron/kanban/tool/batch）。
   - 有调用但状态是 `invalid` → 命名模型没按格式输出，可用
     `plugins.entries.oil-hermes-title.settings.style` 补充风格约束。
4. 想改成每轮都重新命名（更耗额度）：把 `confirm_skip_limit` 设为 0。

## 不要做的事

- 不要直接改 ``state.db``；标题一律走插件/宿主接口。
- 不要为了"让标题好看"而把用户手改的标题降权。
- 不要在用户没提标题相关需求时主动运行这些命令。
