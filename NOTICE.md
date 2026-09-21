NOTICE
======

oil-hermes-title — Hermes Agent 的会话标题实时命名插件。

本作品改编自 oil-oil/oil-codex-title（MIT License，https://github.com/oil-oil/oil-codex-title）：

- 命名格式「类别 emoji + 对象｜目标」、emoji 类别表与命名稳定性规则、
  纯确认跳过策略、预览/固定/暂停/归档预览的产品行为，均源自上游。
- 上游面向 Codex 桌面插件（Stop Hook + App Server `thread/name/set`）；
  本仓库把这些能力重新实现为 Hermes 的原生插件形态：

  | 上游机制 | 本仓库实现 |
  | --- | --- |
  | Codex Stop Hook | `post_llm_call` 插件钩子 |
  | 独立 Luna Fast 会话 | 宿主的 `ctx.llm`（当前活动模型与鉴权） |
  | `thread/name/set` | `SessionDB.set_auto_title(source="llm")` |
  | 检测外部改名后锁定 | Hermes 标题权限（`user > llm > derived`，单调） |
  | 插件内置 Skill | 系统提示段落 + 插件 Skill + CLI/斜杠控制面 |

- 本仓库的命名规则文本（`prompts/naming.md`）在上游规则基础上改写为 Hermes 语境，
  保留了同样的类别语义与稳定性约束。

上游 MIT 许可证全文见本仓库 LICENSE（同时包含原始版权声明）。