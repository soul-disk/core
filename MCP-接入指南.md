# MCP 接入指南（部署者 / 技术用户）

> 把 Soul Framework 作为 **MCP Server** 挂到支持 MCP 的 AI 客户端上，
> 让 AI 在对话中**实时、自主**地"记"与"忆"。
> 本指南覆盖 Claude Desktop / Cursor / OpenClaw / Cline 等主流 MCP 客户端，配置格式通用。

> **目录约定**：本指南假设你位于仓库根目录（即 `mcp_server.py` 所在目录）。所有命令均从此处执行。

---

## 一、它是什么

`mcp_server.py` 是一个符合 MCP 协议（2024-11-05）的 stdio server，
向 AI 客户端暴露 **11 个工具**：

| 工具 | 作用 | AI 何时用 |
|------|------|-----------|
| `get_onboarding` | 返回多智能体接入须知（wing 隔离规则 + write_key 铁律 + 注册顺序） | **首次接入第一件事** |
| `recall_context` | 返回完整上下文包（人格 + 多智能体规则 + 走廊预载 + 用户理解 + 行为契约） | 对话开始时注入 system prompt |
| `get_fact` | 按主体/ id 检索记忆 | 想"回忆"用户某方面信息时 |
| `record_fact` | 写入/更新记忆。FACT→公共区 cross，PREF/BOUND/COMMIT→私密区 | 识别到值得长期保留的内容时 |
| `compact_memory` | 蒸馏归档到 cold/ + 刷新长期摘要 + 触发 vault 滚动 | 定期（每周/每月）主动调一次 |
| `get_config` | 读取身份配置（agent_id/昵称/用户/关系/基调）+ wing 读写铁律（不含 write_key） | 首次连接确认自己是谁 |
| `update_config` | 修改配置。agent_id 设一次锁定；agent_name/user_name/relationship/tone 可随时改；**首次注册自动生成 write_key** | 首次连接设 agent_id、或改名时 |
| `vault_store` | 手动写入混合记忆层（走廊 active / 房间 archive）。wing 限 cross 共享或自己 agent_id 私藏 | 想手动记一条笔记/决策时 |
| `vault_search` | FTS5 全文检索引擎。默认只搜自己 wing；查公共区显式传 wing="cross" | 想深挖某话题/回忆旧事时 |
| `vault_status` | 查看记忆层全局统计（走廊/房间条数、阈值、当前周） | 想看记忆层健康状态时 |
| `vault_compact` | 滚动归档：走廊满 50 条或跨周 → 沉淀进房间 + 刷新周摘要 | 手动触发归档、或定期清理时 |

AI 不用你教它怎么用——它看到这些工具，会在合适时机自主调用，实现**实时细粒度记忆 + 自主身份设定**。

---

## 二、安全模型（务必先读）

多智能体共用同一个灵魂盘时，靠两道防线隔离：

### 2.1 wing 分区隔离
- **公共区 `wing="cross"`**：FACT/知识/经验，全员可读写互学。
- **私密区 `wing=各自 agent_id`**：PREF/BOUND/COMMIT，仅自己可见。
- **强制边界（写在代码里，越权即抛错）**：
  - `vault_store` 的 `wing` 只能填 `"cross"` 或你自己的 `agent_id`，禁止写入他人 wing。
  - `vault_search` 默认只搜你自己的 wing；想查公共区须**显式**传 `wing="cross"`；查他人 wing 直接拒绝。
  - 没有用户明确允许，绝对不能读/写其他 agent 的 wing。

### 2.2 write_key 注册隔离
- 每个 agent **首次注册**（`update_config` 设 `agent_id`）时，框架生成一个 **write_key** 并返回（明文仅此一次）。
- write_key 在 `config.schema.json` 中**以 SHA256 哈希存储，不存明文**。
- 该 agent 之后**修改配置**、或向**自己的私密区写数据**时，必须携带正确的 write_key，否则拒绝写入。
- **write_key 丢了 = 永久失去管理该 agent 配置的权限**（需管理员直接编辑 config 重置）。
- `get_config` 返回中不含 write_key，仅提示"已设置/未设置"。

> 简单说：**onboarding 先看规则 → 注册拿到 write_key 并妥善保管 → 之后每次写都带 key。**

---

## 三、零依赖启动（重点）

**本 server 仅用 Python 标准库实现，不需要 `pip install` 任何包。**
复制整个引擎文件夹到任意机器即可运行（Python 3.8+）。

手动验证 server 能起（可选）：
```bash
python mcp_server.py < /dev/null   # 无输入会阻塞等待，Ctrl+C 退出即正常
```

---

## 四、MCP 客户端接入

任何支持 MCP stdio transport 的客户端都能用。核心配置就两行：`command` 填 `python`，`args` 填 `mcp_server.py` 的绝对路径。

### 4.1 通用配置步骤

1. 找到你的客户端 MCP 配置入口（Settings → MCP 或编辑配置文件）
2. 添加新的 MCP Server
3. 按下面表格填：

| 字段 | 填什么 | 说明 |
|------|--------|------|
| **服务名称** | `SoulFramework` | 随便起，你自己认得就行 |
| **命令** | `python` | 或 `python3` / 绝对路径如 `C:\Python314\python.exe` |
| **参数** | `<本仓库目录>/mcp_server.py` | 脚本的绝对路径（即 `mcp_server.py` 所在目录），一行一个参数 |

4. 保存配置，部分客户端有「测试连接」按钮——应提示连接成功
5. 重启客户端或刷新 MCP 列表

配置成功后，MCP 列表里会显示 `SoulFramework` 及 11 个可用工具，与其他 MCP Server 并排运行，互不干扰。

### 4.2 首次接入流程（重要）

接上后，**AI 不要直接设 agent_id**，按这个顺序走：

1. **`get_onboarding(agent_id="你的标识")`** —— 阅读多智能体接入须知（wing 隔离 + write_key 铁律）。
2. **`get_config(agent_id="你的标识")`** —— 确认身份与读写范围。
3. **`recall_context(agent_id="你的标识")`** —— 注入完整上下文，开始工作。
4. **首次注册**：在对话中让 AI 说"你的 agent_id 是 XX""你叫 XX"，AI 经 `update_config(key="agent_id", value="XX")` 注册，**框架返回 write_key**，AI 必须把明文 key 存好（或经宿主机制转交你）。之后改配置/写私密区都带这个 key。

> **多智能体**：第二个 AI 接同一个灵魂盘时，同样先 `get_onboarding`，再设自己的 `agent_id` 与 `agent_name`。框架自动把每个 agent 的独立配置存入 `agents` 映射表，互不覆盖。

### 4.3 各客户端配置

配置文件位置（按系统）：
- Windows：`%APPDATA%\Claude\claude_desktop_config.json`
- macOS：`~/Library/Application Support/Claude/claude_desktop_config.json`

在 `mcpServers` 下加一项：
```json
{
  "mcpServers": {
    "SoulFramework": {
      "command": "python",
      "args": ["<本仓库目录>/mcp_server.py"]
    }
  }
}
```
Cursor（`~/.cursor/mcp.json`）、OpenClaw / Cline 等其他客户端格式相同，找它们的 MCP 配置入口填 command + args 即可。
保存后重启/刷新客户端，AI 即可识别 11 个工具。

---

## 五、环境变量

| 变量 | 作用 | 说明 |
|------|------|------|
| `SOUL_ROOT` | **数据目录根** | 指向灵魂盘实例根目录；缺省时按脚本位置自动推导 |
| `SOUL_AGENT_ID` | agent_id 兜底 | AI 启动时传入的身份；**工具调用显式传 `agent_id` 时优先于此变量**。仅在客户端无法逐调用传参时作为兜底 |

> 推荐：数据目录用 `SOUL_ROOT` 显式指定；身份一律在每次工具调用里**显式传 `agent_id`**，不要依赖环境变量（避免多 agent 串身份）。

---

## 六、与其他记忆类 MCP 共存

如果你还挂了其他记忆类 MCP，本框架跟它**完全独立、互不干扰**：

| | 其他记忆类 MCP | 本框架（伴生灵魂盘） |
|--|-----------|---------------|
| 定位 | 通用记忆检索 | 人格+主动记忆+心理分析+底线+多 agent 隔离 |
| 数据目录 | 各自独立 | 本框架 `data/` |
| 能否同时挂 | ✓ 能 | ✓ 能 |

两个 MCP server 在客户端里并排跑，AI 对话中按需调各自的工具。

---

## 七、排错

| 现象 | 可能原因 | 处理 |
|------|----------|------|
| 测试连接失败 | 参数里的路径写错了 | 复制脚本绝对路径，注意脚本路径与反斜杠 |
| 显示"未连接" | python 不在 PATH | 把命令改成 python 绝对路径（如 `C:\Python314\python.exe`） |
| 工具调用报"未注册 agent_id" | 没先 `get_onboarding`/`update_config` 设身份 | 按第四节流程先 onboarding 再注册 |
| 写配置报"write_key 验证失败" | 已注册 agent 没带 key 或 key 错 | 找回首次注册时返回的 write_key；丢了需管理员重置 config |
| 工具调用报错"无记忆" | `data/facts/` 为空（**正常！出厂就是这样**） | 多聊几句，AI 会自动写入第一条 |
| 改了配置不生效 | 客户端没刷新 | 断开重连 MCP Server，或重启客户端 |
| 加了新工具没出现 | 客户端缓存了旧 Server | **断开重连 / 重启客户端**后 AI 才能识别新工具 |
| 想让 AI 改昵称 | 不用动 JSON | 直接对话里说"你叫 XX"，AI 经 `update_config` 自己写回（需带 write_key） |
| 想换框架目录 | 设环境变量 `SOUL_ROOT` 指向目标 | 详见第五节 |

---

## 八、官方 SDK 版（可选，更稳健）

若你希望用官方 `mcp` 包（而非本零依赖实现）：
```bash
pip install mcp
```
零依赖版与官方版**暴露的工具完全一致**，客户端配置不变。

---

*一句话：任意 MCP 客户端 → 添加 Server → 填 python + 脚本路径 → 完成。零依赖，接上就用。首次接入先 onboarding，注册拿到 write_key 并保管好。*

---

## 九、配套：实时数据仪表盘

接好 MCP 后，想看灵魂盘里存了什么？不用翻 JSONL 文件——框架自带 Web 仪表盘：

```bash
python engine/dashboard.py
```

浏览器打开 `localhost:8877`，所有记忆（厅堂 + Vault）按标签页展示，支持搜索/排序/分页/多智能体分区筛选。详见 `USER-GUIDE.md`「怎么看我灵魂盘里存了什么」。
