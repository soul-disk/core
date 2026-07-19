# 集成到其他 AI 智能体（Integration）

> 这是框架的命门：**怎么挂到别的 AI 上，让它"不用训练就越用越懂用户"**。
> 以及一条铁律：**外挂只写自己的本子，不碰宿主 AI 的文件**。

核心认知：框架是**外挂记忆 + 人格中间件**，AI 本身不训练。框架负责"记住你是谁、懂你多少"，AI 负责推理。

框架的 `data/` 目录就是用户的**"伴生灵魂盘 / 伴生灵魂盘"**：完全本地、只装这一个用户的信息；换任何 AI，把框架文件夹递过去，新 AI 读一眼就接上旧记忆。记什么、读什么由你的 AI 在对话中自主决定，但最终文件在你本地、你能看能删能改。

---

## 先看清：你用的 AI 是哪种？

| 你的 AI 环境 | 推荐用哪路 | 说明 |
|------|------|------|
| **支持 MCP 的 AI（2026 主流基本都支持：WorkBuddy、Claude、Cursor、Cline、OpenClaw 等）** | **B MCP（首选）** | 挂一次，全自动，用户零操心 |
| 极少数确实不支持 MCP 的 AI | **A 粘贴法（兜底）** | 复制生成文本粘进对话框 |
| 自己写的 AI / 能改 System Prompt 的 | **C 代码注入** | 全自动 |
| 有文件权限的 AI / 本地部署 | **D 文件协议** | 极致可移植 |

> **2026 现状**：MCP 已是 AI 智能体的标配接口。除非你的 AI 明确不支持 MCP（这种现在很少，且基本可判"太弱"），否则**一律首选 B 路**。

---

## 方式 A：上下文粘贴（最通用 · 零代码 · 闭源 AI 也能用）

**适用**：所有 AI，尤其是**改不了代码的闭源成品 AI**（OpenClaw / WorkBuddy 等）。

**做法**：
1. 运行 `engine/make_context.py`，生成一段"AI 是谁、它懂你什么"的文本（打印到屏幕）。
2. 复制，每次开新会话先粘进 AI 的**对话框第一段**作为开场上下文。
3. 正常聊。让 AI "记住 XXX" 时，它调用框架写回 `data/facts/`（通过 MCP 或你手动维护）。

**为什么这是闭源 AI 的真方案**：闭源 AI 的 System Prompt 是黑盒，你改不了；
但你能往对话框打字。粘贴的 context 就是"对话内容"，宿主自己的记忆系统照常运作，互不干扰。

**半自动增强**：`make_context.py --out` 可导出到 `data/context_latest.txt`，复制更方便。

---

## 方式 B：MCP 工具（实时记与忆 · 推荐首选 · 支持 MCP 的 AI 都能用）

**适用**：任何支持 function calling / MCP 的智能体。**2026 年主流 AI 全部支持**（WorkBuddy、Claude Desktop、Cursor、Cline、OpenClaw 等）。
**注意**：我之前版本写"闭源网页 AI 用不了 MCP"是**错的**——WorkBuddy 自身就有 MCP 配置入口，连它都能接。请以本版为准。

**做法**：把框架包成 MCP server（`mcp_server.py`，零依赖纯标准库实现），暴露 **11 个工具**（完整清单见 `MCP-接入指南.md`）：
- `recall_context()` — 返回完整 context_pack（人格 + 多智能体规则 + 走廊预载 + 用户理解 + 行为契约）
- `get_fact(subject)` — AI 实时检索某用户记忆
- `record_fact(...)` — AI 实时写入新记忆（FACT→公共区 cross，PREF/BOUND/COMMIT→私密区）
- `vault_store(...)` — 手动写入混合记忆层（走廊 active / 房间 archive）
- `vault_search(query)` — FTS5 全文检索引擎，不传 wing=全员互学
- `vault_status()` — 查看记忆层统计
- `vault_compact()` — 走廊滚动归档
- `get_config()` — 读取身份配置（含 agent_id）
- `update_config(key, value)` — 修改配置（agent_id 设一次锁定）
- `compact_memory()` — 蒸馏压缩 + 触发 vault 滚动

AI 在对话中自主决定"现在该记一条""现在该查一下"，实现实时、细粒度记忆读写。**用户完全不用管——只管正常聊，记和读它自己干。**

**多智能体共用**：多个 AI 可共享同一灵魂盘，靠 `agent_id`（wing）分区：公共 cross（FACT/知识）全员可读写；私密各自 wing（PREF/BOUND/COMMIT）仅自己可见。

**启动与配置**：见 `MCP-接入指南.md`（含 Claude Desktop / Cursor / WorkBuddy 的实际 mcp 配置）。

---

## 方式 C：代码注入（最无感 · 仅限你能改 AI 代码）

**适用**：自搭智能体、能改 System Prompt 的 AI。

```python
from engine.load_soul import Soul
pack = Soul().context_pack()
system_prompt = pack + "\n\n" + original_system
```
对话中，AI 调用 `engine/record.py` 把新了解写回 `data/facts/`。

---

## 方式 D：文件协议（最平台无关 · 有文件权限即可）

**适用**：任何有文件系统权限的 AI / 智能体。

框架就是本仓库文件夹（纯文本 + JSONL + SQLite）。AI 直接读写 `data/facts/*.jsonl`（厅堂·永久记忆）与 `data/vault/`（走廊+房间·混合记忆层）与 `config.schema.json`。复制整个文件夹即迁移。

---

## 宿主系统隔离（铁律）

框架是**平行记忆层，不是覆盖层**：

- **绝不读写宿主 AI 自身的记忆 / 配置 / 日志文件**（OpenClaw / WorkBuddy 等各自的存储，框架不碰）。
- 所有"了解用户"的数据**只落在本框架 `data/` 目录**。
- 框架**不凌驾**于宿主记忆之上，不修改宿主任何系统文件。
- 数据主权：框架数据归用户（本地、可删），宿主记忆归平台。

详见 `rules/host-isolation.md`。

---

## 为什么"不训练也能越用越懂"

普通 AI：每轮对话独立 → 聊完失忆 → 下次从零。
本框架：理解存在 `data/facts/` → 每次加载 → 对话中写回 → 永久积累。

AI 换了、壳换了、断网了，"懂"都在框架里。用户**零训练成本**，直接聊，理解自然长出来。

---

*集成四路解决"怎么挂到别的 AI"。框架本身是 rules/ + engine/ 定义的机制，与具体 AI 解耦，且永不触碰宿主的文件。*

---

## 配套：数据仪表盘

集成后，用户和开发者都能通过浏览器实时查看灵魂盘全部数据：

```bash
python engine/dashboard.py              # 实时服务（推荐）
python engine/dashboard.py --export     # 导出静态 HTML
```

零依赖 HTTP 服务，6 标签页（总览/FACT/PREF/BOUND/COMMIT/Vault），支持搜索/排序/分页/多智能体分区筛选/自动刷新。详见 `USER-GUIDE.md`。
