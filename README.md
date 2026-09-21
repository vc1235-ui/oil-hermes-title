# oil-hermes-title

让 Hermes 的会话标题跟上你正在做的事情。每轮对话结束后，插件在后台读取最近几轮对话，
由独立调用判断是否更新标题 —— 话题再多，也更容易找回来。

> **改编自 [`oil-oil/oil-codex-title`](https://github.com/oil-oil/oil-codex-title)**（MIT）。
> 上游是 Codex 桌面插件，本仓库把它移植到 Hermes Agent 的插件与钩子体系上，
> 命名规则、命名效果与「手动改名即固定」的行为保持一致。映射关系见文末。

## 一眼看出正在做什么

| 原来的标题 | 更容易找回的标题 |
| --- | --- |
| 帮我把登录页的布局改成响应式，窄屏下三个按钮放不下，顺便给提交按钮加 loading | 🎨 登录页｜响应式布局与提交 loading |
| 接着聊向量数据库选型，pgvector 和 Milvus 该怎么选 | 🔎 向量数据库｜pgvector 与 Milvus 选型 |
| 回应中文问候 | 💬 一般讨论｜打招呼 |

统一采用 **「类别 emoji + 对象｜目标」**，先找对象，再看正在做什么。
（上面两条是真实运行结果，不是示意。）

- **对象在前**：把「登录页」「支付回调」「CRT 转场」这类辨识词放到前面，外层已有的项目名默认省略。
- **类别固定**：🎬 内容制作、🧩 工具开发、🔎 对比调研、📝 方法整理与文档、📅 日程安排、🎨 页面设计、⚙️ 环境配置、💬 一般讨论。同一个话题进入修改阶段，也不会因此换成开发图标。
- **名称稳定**：准确的对象名称尽量不变；旧标题只在工作目标实质变化时才更新；「继续」「推送」「好的」不会取代主线。
- **不打断对话**：命名在后台独立调用里完成，不往原对话里添加消息，不占用回合的 hook 超时预算。
- **跟随你的语言**：按最近几轮**用户**消息的主要语言命名，保留产品名与技术名词。
- **减少重复消耗**：标题稳定后，连续两轮纯确认直接跳过；同一段内容重复触发不重复调用模型。
- **由你控制**：可以预览新标题、固定喜欢的名称、随时暂停或恢复自动命名。
- **可选的闲置归档**：`hermes oil-title archive-preview` 列出长期未聊且未固定的会话，确认后再用 `hermes sessions archive` 收起。

## 安装

```bash
hermes plugins install vc1235-ui/oil-hermes-title --enable
```

装完在新会话里正常聊天即可。命名调用走宿主提供的 `ctx.llm`（当前活动模型与鉴权），
不需要另存 API Key。

验证是否生效：

```bash
hermes plugins list | grep oil-hermes-title     # enabled
hermes oil-title doctor                         # 自检
hermes oil-title status                         # 开关、配置、当前会话标题状态
```

## 日常怎么用

**直接对 Hermes 说**（插件注册了一段提示段落，Agent 知道这些命令，会话 id 自动带入）：

- 「预览这个话题的新标题。」→ `hermes oil-title preview`
- 「就用这个标题。」→ `hermes oil-title apply`
- 「固定这个话题的标题。」/「别再改标题了。」→ `hermes oil-title lock`
- 「标题交回自动维护。」→ `hermes oil-title unlock`
- 「把标题改成 X。」→ `hermes oil-title rename "X"`
- 「暂停自动命名。」/「恢复自动命名。」→ `pause` / `resume`
- 「看下命名用量。」→ `usage [--days 7]`
- 「预览可以归档的闲置话题。」→ `archive-preview [--days 3]`
- 「把近 14 天的会话标题都按规则整理一遍。」→ `backfill --days 14`（先看范围，确认后加 `--apply`）

**斜杠命令**（CLI / 桌面 / gateway 会话内可用，不需要会话 id 的动作）：

```text
/oil-title status | pause | resume | usage | archive-preview | doctor
```

**CLI**（也可以在任意终端直接跑）：

```bash
hermes oil-title status
hermes oil-title preview --session <id> [--json]
hermes oil-title apply   --session <id>
hermes oil-title rename  --session <id> "<标题>"
hermes oil-title lock    --session <id>
hermes oil-title unlock  --session <id>
hermes oil-title pause | resume
hermes oil-title usage   [--days 7] [--json]
hermes oil-title archive-preview [--days 3] [--json]
hermes oil-title backfill [--days 14] [--apply] [--include-user] [--include-automation]
hermes oil-title restore [--file <备份.json>] [--apply]
hermes oil-title doctor
```

`--session <id>` 放在子命令前或后都行（`hermes oil-title --session X lock` = `hermes oil-title lock --session X`）；
不带就取环境变量 `HERMES_SESSION_ID`，也就是 Agent 在会话内调用时自动指向当前会话。

## 配置

写在 `~/.hermes/config.yaml`，或用 `hermes oil-title` 之外的方式直接编辑后重载：

```yaml
plugins:
  entries:
    oil-hermes-title:
      settings:
        enabled: true                # 总开关
        history_turns: 5             # 参考最近几轮对话
        max_chars_per_turn: 700      # 每轮摘录上限（控 token）
        timeout: 30                  # 命名调用超时（秒）
        max_parallel_workers: 2      # 后台同时运行的命名上限（1-8）
        confirm_skip_limit: 2        # 标题稳定后允许连续跳过的纯确认轮数
        min_user_chars: 4            # 更短的实义消息不触发命名
        style: ""                    # 追加到内置命名规则的自定义风格
        state_db_path: ""            # 覆盖 state.db 路径（一般不需要）
```

想把命名固定到便宜模型（需要先授权模型覆盖）：

```yaml
plugins:
  entries:
    oil-hermes-title:
      llm:
        allow_model_override: true
        allowed_models: ["deepseek-v4-flash"]
```

未授权时插件自动回退到当前活动模型（不会整体失败）。

### 和 Hermes 原生自动命名的关系

Hermes 自带一次性的自动命名（首轮即时标题 + 一次模型升级，最多试到第 3 轮）。
本插件在它之后接管**持续**更新，所以：

- 想省掉一次冗余模型调用：把 `auxiliary.title_generation.model_upgrade_enabled` 设为 `false`
  （保留即时标题，不再调用模型升级）。原生标题是 `llm` 权限，本插件会按规则接管。
- 什么都不改也能正常工作：本插件会把 `llm` 权限标题降级后再写回，实现「后续回合还能改标题」。

## 工作原理

| 环节 | 实现 |
| --- | --- |
| 触发 | `post_llm_call` 插件钩子（每个成功回合结束时，宿主同步调用） |
| 判定 | 廉价检查在回合线程：开关、暂停、自动化来源、纯确认识别、内容指纹去重、并发闸门 |
| 命名 | 后台 daemon 线程 → `ctx.llm.complete()`，提示词来自 `prompts/naming.md` |
| 校验 | 恰好一个全角 `｜`、类别 emoji 在固定表内、对象与目标非空、长度 ≤ 60；不合格补一次纠正重试 |
| 写入 | `SessionDB.set_auto_title(source="llm")`；重名自动加 `#N` |
| 固定 | Hermes 的标题权限 `user > llm > derived` 是单调的 —— 手动 `/title`、桌面重命名、`rename` 都会把标题抬到 `user`，自动命名从此不再改动它 |
| 账本 | `~/.hermes/plugin-data/<ns>/usage/YYYY-MM-DD.jsonl`，只记时间、用途、模型、会话、用量、耗时、状态 |

**关键实现细节**：`llm → llm` 的重复写入会被 Hermes 的权限判定静默拒绝（同 rank 不覆盖）。
所以每轮更新必须先 `set_session_title_source(sid, "derived")` 降级，再写 `llm`。
本仓库的 `tests/test_store.py::test_second_llm_write_requires_downgrade` 就是这条的回归测试。

## 边界与已知限制

- **一次性运行**：`hermes chat -q` 这类一次性进程在后台命名返回前就退出了，标题不会落下
  （进程退出会杀掉守护线程）。交互式 CLI、桌面 app、gateway/Telegram 等长驻会话不受影响。
  自动化来源（cron / kanban / tool / batch / subagent / oneshot）默认跳过命名。
- **侧边栏刷新延迟**：宿主的 `session.title` 实时事件只由原生自动命名发出；插件写库后，
  标题在下一次会话列表/信息刷新时可见（通常就是下一轮开始时）。
- **多 profile**：插件跑在会话后端进程里，按当前 profile 解析 `state.db`；一个后端服务多个
  profile 的部署如遇到写错库，可用 `settings.state_db_path` 显式指定。
- **标题唯一性**：Hermes 的标题全局唯一、上限 100 字符；撞名会自动加 `#N`，极端情况下保留原标题。
- **用量不是账单**：账本里的 `total_tokens` 缺失记为未知，不换算账号额度；真正的计费看 Hermes 自身的
  用量统计（本插件的调用会记在会话账上）。
- **命名消耗额度**：每轮一次小调用（实测约 700-900 tokens / 2-5 秒）。想更省：调大
  `confirm_skip_limit`、调小 `history_turns`，或固定到便宜模型。

## 开发与验证

```bash
# 单测（41 个）—— 需要 Hermes 运行时可导入 hermes_state
PYTHONPATH="$HOME/.hermes/hermes-agent" ~/.hermes/hermes-agent/venv/bin/python -m pytest tests/ -q

# 插件契约自检（清单、导入、注册）
hermes plugins doctor . --ci
```

验证记录（2026-09，default profile 实装实测）：

1. 结构：`hermes plugins doctor ~/.hermes/plugins/oil-hermes-title --ci` → manifest/import/注册全通过（1 hook）。
2. 触发：真实交互会话里 `agent.log` 出现 `已排入后台命名（session=… platform=cli）`，
   2.3 秒后写入 `🔎 Redis vs Memcached｜持久化机制对比 (applied)`。
3. 换话题（关键回归）：第二轮标题改成 `🔎 pgvector vs Milvus｜向量库选型 (applied)` ——
   「已有 `llm` 标题仍可被后续回合重写」这条降级路径在真实环境成立。
4. 固定：`hermes oil-title lock` 之后再换话题 → `(locked)`，标题不动、`source=user`、未调用模型。

## 更新记录

- **1.1.1** — 修类别 emoji 校验：`⚙️` 是「U+2699 + VS16」两个码点，单码点比对会把合规标题
  误判成 `bad_emoji`（回填 21 条里有 6 条因此判失败）。现在按去 VS16 的前缀匹配，
  `⚙` 与 `⚙️` 都接受并归一化成表里的形式；补了覆盖全部类别的回归测试。
  命名规则同时明确：标题格式不一致时允许一次性迁移（内容准确也要给合规版本）。
- **1.1.0** — 新增 `backfill`（批量回填历史会话标题，默认不调模型的范围预览 + 执行前自动备份 +
  默认保护 `user` 权限标题与自动化会话）与 `restore`（按备份回滚，标题文字与权限都还原）。
- **1.0.1** — 修 `--session` 只挂在父 parser 上的问题：`hermes oil-title lock --session X`
  会被 argparse 拒绝（README/SKILL 示例正是这个写法）。现在子命令前后都接受 `--session`，
  并补了解析回归测试（`tests/test_cli.py`）。
- **1.0.0** — 首个版本：`post_llm_call` 后台命名、emoji 类别与稳定性规则、
  预览/采用/固定/暂停/用量/归档预览、权限感知写入、用量账本。

## 许可

MIT。命名规则、emoji 类别表与产品行为改编自 [oil-oil/oil-codex-title](https://github.com/oil-oil/oil-codex-title)（MIT，© oil-oil）。
本仓库的 Hermes 适配实现（插件结构、钩子编排、权限写入、控制面）由本仓库作者编写。