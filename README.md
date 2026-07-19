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

## 设计原则（出厂不可删）

伴生灵魂盘而非权重训练 · 主动而非被动 · 理解在框架不在模型 · 出厂零数据 · 平台无关 · 多智能体共用（wing 分区，公共 cross + 私密 agent_id）。

---

*这是框架 v1。规则写清楚了，但机制靠运行期 AI 实打实去记、去分析、去积累。*
*它不替用户训练，它替用户"记得"。*
