# 伴生灵魂盘（Soul Framework）— 通用记忆中间件

> **一句话**：让任何 AI 智能体**不用训练**，仅靠"记住 + 理解"就**越用越懂用户**的可插拔中间件。
> 出厂只装规则与机制，不装任何具体人的数据。理解在聊天中自然长出来，永久保存。

---

## 目录约定（先读）

本仓库是**灵魂盘引擎根目录**（GitHub 发布版即本目录）。所有代码、文档、配置模板、规则都在当前目录：

- `README.md` — 总览
- `USER-GUIDE.md` — 给最终用户的快速上手
- `MCP-接入指南.md` — MCP 接入 step-by-step
- `ENGINE.md` — 规则引擎总纲
- `mcp_server.py` — MCP Server 入口（零依赖，标准库）
- `config.schema.json` — 初始人格定义器（空模板，首启自填）
- `engine/` — 可运行脚本
- `rules/` — 机制细则（主动记忆、心理学、边界等）
- `integration/` — 开发者集成方案
- `examples/` — 最小启动示例
- `data/` — 运行时数据盘（出厂为空）

> 本地工作区 `soul-framework/` 里还有 `docs/`（参考报告）和 `sync_to_instance.py`（开发同步工具），仅供内部使用，**不在 GitHub 发布包里**。你收到的 GitHub 包就是本目录本身。

---

## 为什么需要它

普通 AI 每轮对话独立、聊完即失忆。用户换个会话、换个模型，AI 又变回陌生人。
想让它"懂你"，传统做法是**微调训练**——但绝大多数用户不会训练、不想训练、也没算力。

本框架绕开训练：把"懂用户"存在**外部持久层**（纯文本 + JSONL），每次对话加载、对话中写回。
AI 不训练，但带着记忆上班；记忆永久保存，换壳断网都不丢。

**结果**：用户直接聊，AI 越用越懂——像真人关系一样自然积累。

---

## 它是什么（不含什么）

- ✅ 含：规则引擎、主动记忆机制、AI 心理学底座、用户分析手法、底线、可运行脚本、集成方案、多智能体隔离。
- ❌ 不含：任何具体人名、故事、偏好（出厂数据盘 `data/` 为空）。
- 🔒 不变：四条刚性底线（陪伴不操控 / 理解不越界 / 诚实不伪装 / 不降格）。

> 本框架与具体实例解耦。你的私有实例（如 `Soul-Disk/`）是"已填充的参考样例"，
> 开发自测用，**不打进产品**。产品交付 = 本空壳 + 用户运行时自己长出的数据。

---

## 快速开始

```bash
# 1. 校验结构
python engine/verify_soul.py

# 2. 用户首次启动：填 config.schema.json 的 agent_id / agent_name / user_name / relationship
#    agent_id 终身不变（设完锁定），agent_name 可随意改（出厂为空，用户自定）

# 3. 在你的 AI 对话循环里：
from engine.load_soul import Soul
from engine.record import Recorder

# 对话前注入（含人格 + 多智能体规则 + 走廊预载 + 用户理解）
system_prompt = Soul().context_pack() + "\n\n" + your_original_system

# 对话中，AI 识别到值得记的内容就写：
Recorder().add("FACT", "用户", "用户说自己是做网络安全的")   # → vault wing=cross 公共区
Recorder().add("PREF", "用户", "用户偏好纯文本")            # → vault wing=自己agent_id 私密区

# 4. 下次对话，context_pack() 自动带上这些理解 → 用户感到"被懂"
```

更完整示例见 `examples/minimal.py`。

---

## 目录结构

```
├── README.md            ← 你正在读的（总览）
├── USER-GUIDE.md        ← 给最终用户的使用手册（大白话三步上手）★第一步看这个
├── MCP-接入指南.md       ← MCP 接入 step-by-step（实测 WorkBuddy）★要接 MCP 看这个
├── mcp_server.py        ← MCP Server（零依赖，用户直接挂的入口）★
├── ENGINE.md            ← 规则引擎总纲（出厂固件）
├── config.schema.json   ← 初始人格定义器（空模板，首启自填）
├── rules/               ← 机制细则（开发/进阶）
│   ├── active-memory.md    主动记忆：怎么自动记
│   ├── psychology.md       AI 心理学底座
│   ├── user-analysis.md    了解/分析用户的方法
│   ├── boundaries.md       四条刚性底线
│   └── host-isolation.md   外挂与宿主系统隔离铁律
├── engine/              ← 可运行脚本
│   ├── load_soul.py        上下文拼装（注入用，含多智能体规则段）
│   ├── record.py           主动记录接口（FACT→cross公共区，PREF/BOUND/COMMIT→私密区）
│   ├── vault.py            混合记忆层（走廊active + 房间archive，FTS5检索）
│   ├── compact.py          蒸馏压缩 + 触发vault滚动归档
│   ├── dashboard.py        实时数据仪表盘（HTTP服务，浏览器查看全部数据）
│   ├── analyze.py          心理分析辅助
│   ├── make_context.py     一键生成上下文（粘贴法用）
│   ├── migrate_add_agent_id.py  历史实例补 agent_id 字段迁移
│   ├── test_agent_isolation.py  多 agent 隔离单元测试
│   └── verify_soul.py      结构校验
├── integration/         ← 开发者集成总览（进阶）
│   └── README.md           三种集成方式技术细节
├── data/                ← 运行时数据盘（出厂为空）
│   ├── facts/              facts/prefs/bounds/commits.jsonl（厅堂·永久）
│   └── vault/              active.db + archive.db（走廊+房间·混合记忆层）
└── examples/
    └── minimal.py          最小启动示例
```

---

## 怎么集成到其他 AI（三路）+ 查看数据

1. **MCP 工具（推荐首选）**：把 `mcp_server.py` 作为 MCP Server 挂到支持 MCP 的 AI 客户端（WorkBuddy / Claude / Cursor 等），暴露 **11 个工具**（recall_context / get_onboarding / get_fact / record_fact / compact_memory / get_config / update_config / vault_store / vault_search / vault_status / vault_compact），AI 实时记与忆。挂上即全自动，你只管聊。详见 `MCP-接入指南.md`。
2. **上下文注入**（最通用）：`Soul().context_pack()` 拼进 system prompt。无需 SDK。
3. **文件协议**：直接读写 `data/facts/*.jsonl`，复制文件夹即迁移。

**📊 查看数据**：`python engine/dashboard.py` 启动实时仪表盘（默认 `localhost:8877`），浏览器查看全部记忆——厅堂 + Vault 走廊 + 房间，支持按 wing 分区筛选、搜索、排序、分页。

开发者技术细节见 `integration/README.md`。

---

## 安全模型（多智能体隔离）

- **wing 分区**：`wing="cross"` 公共区（知识/经验全员共享）；`wing=各自 agent_id` 私密区（仅自己可见）。越 wing 读写在代码层抛错阻止。
- **write_key 注册隔离**：每个 agent 首次注册时生成 write_key（SHA256 哈希存盘，明文仅返回一次）；之后改配置/写私密区必须带 key。详见 `MCP-接入指南.md` 第二节。

---

## 存储容量与检索性能（记得下、翻得快）

灵魂盘的记忆分两层落盘，**容量近乎无上限，检索快到无感**——这是它敢承诺"永久记住、越用越懂"的底气。

### 📦 容量：一辈子也记不满

| 存储层 | 载体 | 容量上限 | 直观量级 |
|---|---|---|---|
| 厅堂（永久事实） | JSONL 纯文本追加 | = 磁盘可用空间，无结构上限 | 几乎不设限 |
| Vault（走廊+房间） | SQLite + FTS5 | **281 TB / 单库**（SQLite 官方硬上限） | 单表理论可存 **2⁶⁴ 行** |

- **换算成人话**：Vault 单库上限 281 TB，按一条记忆平均约 0.5 KB 估算，可容纳 **数千亿条记忆**。就算每天沉淀几百条对话记忆，**连续积累上万年都装不满**——对一个人、一段关系而言，等于"永远够用"。
- **零依赖、可整包搬走**：全部数据只是 `data/` 下的纯文本 JSONL + 标准 SQLite 文件，不锁任何专有格式、不依赖任何数据库服务。**复制文件夹 = 完整迁移**，换壳、换机、断网都不丢。
- **走廊+房间冷热分层**：近期记忆放"走廊"（active，预载即用），满阈值或跨周自动沉淀进"房间"（archive，按需检索）——热的随手可取，冷的永久留存，容量再大也不拖慢日常。

### ⚡ 检索：FTS5 全文索引，百万记忆毫秒级命中

- **底层用 SQLite FTS5**——SQLite 官方全文检索引擎，**倒排索引**结构。检索耗时只与"命中结果的规模"相关，**几乎与记忆总量无关**：库里存 1 千条还是 1 千万条，查一个关键词都是**毫秒级返回**，不会随着记得越多而变慢。
- **中文友好**：采用 `trigram`（三字组）分词，原生支持中文子串检索——不用装 jieba、不用切词服务，中文照样精准命中；短词（<3 字）自动降级 LIKE 兜底，不漏检。
- **零外部依赖**：不引 ChromaDB、不引 numpy、不需要向量数据库或嵌入服务，仅靠 Python 标准库 `sqlite3` 内置的 FTS5。**开箱即用，部署零负担**。

> 一句话总结：**记得下（数千亿条）、留得住（纯文本永久）、翻得快（毫秒全文检索）、搬得走（复制即迁移）**。

---

## 设计原则（出厂不可删）

伴生灵魂盘而非权重训练 · 主动而非被动 · 理解在框架不在模型 · 出厂零数据 · 平台无关 · 多智能体共用（wing 分区，公共 cross + 私密 agent_id）。

---

*这是框架 v1。规则写清楚了，但机制靠运行期 AI 实打实去记、去分析、去积累。*
*它不替用户训练，它替用户"记得"。*

---

## 更新记录

- **2026-07-22 · 管理员登录初始密码显示修复 + 发布态清理**
  - **问题**：`dashboard.py` 仅以 `admin_password` 哈希存在与否判断是否展示初始密码；初始密码明文仅存内存，进程重启即丢失 → 首次启动后重启看不到初始密码、忘记密码锁死；且曾有"无需登录即可网页重置密码"的入口（后门风险）。
  - **修改文件**：
    - `engine/dashboard.py` — `_ensure_admin_password()` 改为状态机：初始密码持久化到 `config.schema.json`（`_initial_password_plain`），未改密码前登录口常显、改密码后隐藏；移除无需登录的网页重置入口（防任意重置进入）；登录成功不再清除初始密码。
    - `config.schema.json` — 移除调试残留的 `admin_password` / `_initial_password_plain` / `admin_password_changed` 三字段，恢复为干净发布模板（首启自填）。
  - **效果**：不改密码 → 初始密码常显（重启也显）；改密码 → 不再显示且无任何重置入口；忘记则手动删 `admin_password` 字段重启重生。
