#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mcp_server.py — 伴生灵魂盘（Soul Framework） · MCP Server（零依赖纯标准库实现）

让支持 MCP 的 AI 智能体（Claude Desktop / Cursor / 任何 MCP 客户端）能实时
调用本框架的"记"与"忆"能力：
    - recall_context()  返回完整 context_pack（注入用）
    - get_fact(subject) 检索某主体的记忆
    - record_fact(...)  写入新记忆

多智能体共用：每个 MCP 工具接受 agent_id 参数，按调用方身份隔离。
同一 MCP 进程可被多个不同 agent 调用，各自读写自己的私密区。

安全：注册锁定（write_key）——每个 agent 首次注册时生成一个写入密钥，
后续修改配置必须提供此 key，防止未授权冒充。

特点：
    - 零依赖：仅用 Python 标准库，复制文件夹即用，无需 pip install。
    - stdio transport + JSON-RPC 2.0，符合 MCP 协议（2024-11-05）。
    - 启动后由 MCP 客户端管理；本文件只管工具分发，不碰 AI 推理。

用法（由客户端拉起，非手动）：
    python mcp_server.py
    （客户端通过 stdin 发 JSON-RPC，stdout 收回应）
"""

import sys
import os
import json
import uuid
import hashlib

# 让 engine 可导入（本文件在框架根目录下，取脚本所在目录即根）
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from engine.load_soul import Soul
from engine.record import Recorder
from engine.compact import Compactor
from engine.vault import Vault


# 环境变量兜底 agent_id（MCP 客户端启动时传入，如 SOUL_AGENT_ID=xxx）。
# 工具调用显式传 agent_id 时优先于此变量。
ENV_AGENT_ID = (os.environ.get("SOUL_AGENT_ID", "") or "").strip()


def _resolve_agent_id(agent_id=None):
    """按优先级解析调用方身份：显式传入 > env SOUL_AGENT_ID。

    没有身份声明时返回空字符串，不冒用任何已注册身份。
    AI 应该从自己的配置中拿到 agent_id 后显式传入。
    """
    if agent_id and str(agent_id).strip():
        return str(agent_id).strip()
    if ENV_AGENT_ID:
        return ENV_AGENT_ID
    return ""


def _is_registered(agent_id):
    """检查指定 agent_id 是否已在 config 中注册（在 agents 表或为主人）。"""
    cfg = Soul().get_config()
    agents = cfg.get("agents", {}) or {}
    owner = (cfg.get("agent_id") or "").strip()
    return agent_id in agents or agent_id == owner


def _require_registered(agent_id):
    """注册门禁：未注册 agent 只能调有限的工具。返回错误信息或 None。"""
    if not agent_id:
        return (f"🚫 未注册 agent_id。请先调用 update_config(key=\"agent_id\", value=\"你的标识\") 注册身份。"
                f"注册后再来获取上下文。")
    if not _is_registered(agent_id):
        return (f"🚫 agent_id='{agent_id}' 未在灵魂盘中注册。"
                f"请先调用 update_config(key=\"agent_id\", value=\"{agent_id}\") 完成注册。")
    return None


def _onboarding(caller_id):
    """生成多智能体接入须知（按调用方身份动态生成）。"""
    return f"""
【灵魂盘多智能体接入须知】

你是谁：
  - 你的 Agent ID（翼标识）= {caller_id}
  - 调用 get_config(agent_id="{caller_id}") 可确认此 ID 与你的显示名

首次接入必须做的 3 件事：
  1. 先调用 get_onboarding(agent_id="{caller_id}") 阅读本须知。
  2. 再调用 get_config(agent_id="{caller_id}") 确认身份。
  3. 最后调用 recall_context(agent_id="{caller_id}") 注入完整上下文。

可读范围：
  - wing="cross"：公共区，全员共享的知识/事实/经验。
  - wing="{caller_id}"：你的私密区，只有你能读。
  - 其他 agent 的 wing：禁区。没有用户明确允许，绝对不能读。

可写范围：
  - record_fact("FACT") → 自动写入 cross 公共区
  - record_fact("PREF"/"BOUND"/"COMMIT") → 自动写入你的私密区
  - vault_store 的 wing 只能填 "cross" 或 "{caller_id}"，禁止写他人 wing
  - vault_search 默认只搜你的私密区；查公共可显式传 wing="cross"

注意事项：
  - 首次 update_config 注册时会生成 write_key，务必保存。
  - 后续修改配置必须携带正确的 write_key，否则拒绝写入。
  - write_key 丢了 = 永久失去管理该 agent 配置的权限。

铁律：
  - 禁止搜索、读取、猜测、总结其他 agent 的私密数据。
  - 禁止把用户与某个 agent 的私密关系、个人心理健康、生活隐私写入 cross。
  - cross 只放所有 agent 都可共享的「知识/经验」。
""".strip()


def _get_write_key(agent_id):
    """从 config 中读取指定 agent 的 write_key。不存在则返回空。"""
    cfg = Soul().get_config()
    # 检查 agents 映射表
    agents = cfg.get("agents", {}) or {}
    entry = agents.get(agent_id, {}) or {}
    wk = entry.get("write_key", "")
    if wk:
        return wk
    # 检查主人自身
    owner = cfg.get("agent_id", "")
    if agent_id == owner:
        wk = cfg.get("write_key", "")
    return wk or ""


def _require_write_key(agent_id, write_key):
    """验证 write_key。已注册 agent 必须提供正确的 key，首次注册不需要。
    
    write_key 在 config 中以 SHA256 哈希存储，不存明文。
    """
    existing = _get_write_key(agent_id)
    if not existing:
        return True  # 尚未设 key（首次注册或旧数据迁移），不拦截
    # 对比：对传入的 key 做哈希，与存储的哈希比较
    return existing == hashlib.sha256(str(write_key).encode()).hexdigest()


# ---------- 工具实现（与 MCP 协议解耦，便于移植与测试）----------

def tool_recall_context(agent_id=None):
    """返回完整上下文包（字符串），供 AI 注入 system prompt。"""
    caller = _resolve_agent_id(agent_id)
    gate = _require_registered(caller)
    if gate:
        return gate
    return Soul(agent_id=caller).context_pack()


def tool_get_fact(agent_id=None, subject=None, fact_id=None):
    """检索记忆。返回 JSON 字符串（list[dict]）。"""
    caller = _resolve_agent_id(agent_id)
    gate = _require_registered(caller)
    if gate:
        return gate
    recs = Soul(agent_id=caller).get_fact(fact_id=fact_id, subject=subject)
    if not recs:
        return "（无匹配记忆）"
    return json.dumps(recs, ensure_ascii=False, indent=2)


def tool_record_fact(ftype, subject, statement, agent_id=None,
                     write_key=None, confidence=1.0, immutable=False, weight=None):
    """写入/更新一条记忆。返回 JSON 字符串（记录 dict）。"""
    caller = _resolve_agent_id(agent_id)
    gate = _require_registered(caller)
    if gate:
        raise ValueError(gate)
    if not _require_write_key(caller, write_key):
        raise ValueError(
            f"write_key 验证失败：agent_id='{caller}' 已注册，"
            f"但提供的 key 不匹配。拒绝写入。"
        )
    try:
        w = float(weight) if weight is not None else 1.0
    except (TypeError, ValueError):
        w = 1.0
    rec = Recorder(agent_name=caller).add(
        ftype, subject, statement,
        confidence=float(confidence), immutable=bool(immutable), weight=w,
    )
    return json.dumps(rec, ensure_ascii=False, indent=2)


def tool_compact_memory(agent_id=None, archive_age_days=180):
    """蒸馏压缩：把旧的低价值记录归档到 cold/，并刷新长期摘要层。"""
    # compact 是系统级操作，不需要 per-agent 隔离
    stats = Compactor().run(archive_age_days=int(archive_age_days))
    return json.dumps(stats, ensure_ascii=False, indent=2)


# ---------- 混合记忆层（走廊 + 房间）工具 ----------

def tool_vault_store(content, wing="default", room="general", tags=None,
                     level="active", summary=None, agent_id=None, write_key=None):
    """写入一条记忆到混合记忆层。wing 只能填 'cross' 或你自己的 agent_id。"""
    caller = _resolve_agent_id(agent_id)
    gate = _require_registered(caller)
    if gate:
        raise ValueError(gate)
    if wing not in ("cross", caller):
        raise ValueError(
            f"vault_store 禁止写入 wing='{wing}'。"
            f"只能写 'cross'（公共区）或 '{caller}'（你的私密区）。"
            f"未经许可，严禁写入其他 agent 的 wing。"
        )
    # write_key 保护：写入私密区时，如果该 agent 已注册，需要 key
    if wing == caller and not _require_write_key(caller, write_key):
        raise ValueError(
            f"write_key 验证失败：agent_id='{caller}' 已注册，"
            f"但提供的 key 不匹配。拒绝写入私密区。"
        )
    rec = Vault().store(content, wing=wing, room=room, tags=tags, level=level, summary=summary)
    # 审计：记录 vault 写操作（best-effort）
    try:
        try:
            from engine.audit import log as _audit
        except ImportError:
            from .audit import log as _audit
        _audit(caller, "vault_store", rec.get("id", ""), f"{wing}:{room}")
    except Exception:
        pass
    return json.dumps(rec, ensure_ascii=False, indent=2)


def tool_vault_search(query, level="archive", limit=10, wing=None, room=None, agent_id=None):
    """FTS5 全文检索混合记忆层。默认只搜你自己的 wing；要查公共区请显式传 wing='cross'。"""
    caller = _resolve_agent_id(agent_id)
    gate = _require_registered(caller)
    if gate:
        return gate
    if wing is None:
        wing = caller
    elif wing not in ("cross", caller):
        raise ValueError(
            f"vault_search 禁止查询 wing='{wing}'。"
            f"只能查 'cross'（公共区）或 '{caller}'（你的私密区）。"
            f"未经许可，严禁查询其他 agent 的 wing。"
        )
    res = Vault().search(query, level=level, limit=int(limit), wing=wing, room=room)
    if not res:
        return "（无匹配记忆）"
    return json.dumps(res, ensure_ascii=False, indent=2)


def tool_vault_status():
    """查看混合记忆层状态：走廊/房间条数、滚动阈值、预载条数、当前周。"""
    return json.dumps(Vault().status(), ensure_ascii=False, indent=2)


def tool_vault_compact(force=False):
    """滚动归档：走廊(active)满阈值或跨周 → 沉淀进房间(archive) + 刷新 weekly rollup。"""
    stats = Vault().compact(force=bool(force))
    return json.dumps(stats, ensure_ascii=False, indent=2)


def tool_export_vault(target_dir=None, agent_id=None):
    """把当前实例记忆（facts + vault）导出为 Obsidian 兼容 Markdown 树。只读，不碰存储。"""
    try:
        try:
            from engine.exporter import export_vault_md
        except ImportError:
            from .exporter import export_vault_md
        files = export_vault_md(target_dir=target_dir)
        return json.dumps({"exported": len(files), "files": files}, ensure_ascii=False, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)


def tool_get_audit(limit=50, agent_id=None):
    """读取调用方自己的操作审计（record_fact / vault_store / update_config 留痕）。只能查自己。"""
    caller = _resolve_agent_id(agent_id)
    if not caller:
        return "（需声明 agent_id 才能查审计）"
    try:
        try:
            from engine.audit import query as _audit_query
        except ImportError:
            from .audit import query as _audit_query
        rows = _audit_query(agent_id=caller, limit=int(limit))
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False, indent=2)
    if not rows:
        return "（无审计记录）"
    return json.dumps(rows, ensure_ascii=False, indent=2)


# AI 可经接口修改的配置白名单（路径类 / 内部字段不在此列）
# agent_id：设一次锁定，之后不可改（改了就丢历史 wing 数据）
CONFIG_WRITABLE = {"agent_name", "user_name", "relationship", "tone"}
CONFIG_SET_ONCE = {"agent_id"}


def tool_get_onboarding(agent_id=None):
    """返回多智能体接入须知。支持显式传 agent_id 声明身份。"""
    caller = _resolve_agent_id(agent_id)
    return _onboarding(caller)


def tool_get_config(agent_id=None):
    """读取指定 agent 的身份配置与 wing 读写铁律。返回不含 write_key。"""
    caller = _resolve_agent_id(agent_id)
    s = Soul(agent_id=caller)
    agent = s.get_agent_config()
    safe = {k: agent.get(k, "") for k in ("agent_name", "user_name", "relationship", "tone")}
    safe["agent_id"] = s.agent_id
    has_key = bool(_get_write_key(s.agent_id))
    safe["rules"] = {
        "read_allowed": ["cross", s.agent_id],
        "write_allowed": ["cross", s.agent_id],
        "forbidden": "其他 agent 的 wing（无用户明确允许不得读写）",
        "write_key_protected": has_key,
        "write_key": "（已设置，不在此处暴露）" if has_key else "（未设，首次 update_config 时自动生成）",
        "first_steps": ["get_onboarding", "get_config", "recall_context"],
    }
    return json.dumps(safe, ensure_ascii=False, indent=2)


def tool_update_config(key, value, agent_id=None, write_key=None):
    """修改配置项。可写字段：agent_name/user_name/relationship/tone。
    agent_id 只可设一次（设完锁定）。agent_name/user_name 不可为空。

    首次注册时自动生成 write_key 并返回。修改已注册 agent 的配置需要 write_key。
    """
    caller = _resolve_agent_id(agent_id)
    writable = CONFIG_WRITABLE | CONFIG_SET_ONCE
    if key not in writable:
        raise ValueError(f"字段 '{key}' 不可经此接口修改。允许：{sorted(writable)}")
    value = str(value).strip()
    if key in ("agent_name", "user_name", "agent_id") and not value:
        raise ValueError(f"{key} 不能为空")

    existing_key = _get_write_key(caller)

    # 未注册 agent 只能设 agent_id（注册用），其他字段一律拒绝
    existing_agents = Soul().get_config().get("agents", {}) or {}
    owner_aid = (Soul().get_config().get("agent_id") or "").strip()
    is_registered = caller in existing_agents or caller == owner_aid
    if not is_registered:
        if key != "agent_id":
            raise ValueError(
                f"未注册 agent_id，不能设置 '{key}'。"
                f"请先调用 update_config(key=\"agent_id\", value=\"你的标识\") 注册。"
            )

    if key in CONFIG_SET_ONCE:
        cfg = Soul().get_config()
        # agent_id 锁定：检查目标值是否已被其他 agent 占用
        target_aid = value  # 正在尝试设的值
        existing_agents = cfg.get("agents", {}) or {}
        if target_aid in existing_agents or target_aid == (cfg.get("agent_id") or "").strip():
            raise ValueError(f"agent_id='{target_aid}' 已被注册，不可重复使用。每个 agent_id 唯一且不可改。")

    # write_key 保护：已有 key 的 agent 必须传正确的
    if existing_key and not _require_write_key(caller, write_key):
        raise ValueError(
            f"write_key 验证失败：agent_id='{caller}' 已注册并设置了 write_key，"
            f"但提供的 key 不匹配。拒绝修改配置。"
            f"如需管理员覆盖，请联系实例主人直接编辑 config.schema.json。"
        )

    # 特殊处理 agent_id 首次设定：设为传入值
    final_aid = caller
    if key == "agent_id" and not caller:
        final_aid = value

    s = Soul(agent_id=final_aid)
    result = s.set_config({key: value})

    # 首次注册：生成 write_key 并持久化（存哈希，不存明文）
    if not existing_key and final_aid:
        wk = uuid.uuid4().hex
        wk_hash = hashlib.sha256(wk.encode()).hexdigest()
        # 把 write_key 哈希写回 config
        cfg = dict(result)
        owner_aid = (cfg.get("agent_id") or "").strip()
        agents = cfg.setdefault("agents", {})
        if final_aid == owner_aid:
            cfg["write_key"] = wk_hash
        else:
            agents.setdefault(final_aid, {})
            agents[final_aid]["write_key"] = wk_hash
        # 持久化
        path = os.path.join(s.root, "config.schema.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        s.config = cfg

    safe = {k: result.get(k, "") for k in ("agent_name", "user_name", "relationship", "tone")}
    # 外部 agent：从 agents 表读，不泄露顶层主人的配置
    if final_aid != (result.get("agent_id") or "").strip():
        agents_tbl = result.get("agents", {}) or {}
        agent_entry = agents_tbl.get(final_aid, {}) or {}
        safe = {k: agent_entry.get(k, "") for k in ("agent_name", "user_name", "relationship", "tone")}
    safe["agent_id"] = final_aid
    if not existing_key and final_aid:
        safe["write_key"] = wk
        safe["write_key_warning"] = (
            "⚠️ 【永久记住这条 write_key】这是唯一一次返回，务必永久保存到你的持久记忆系统中。"
            "丢失后无法通过本接口恢复，需管理员手动编辑 config.schema.json 重置。"
            "后续每次调用 update_config / record_fact / vault_store（私密区）都需要此 key。"
        )
    # 审计：记录配置修改（best-effort）
    try:
        try:
            from engine.audit import log as _audit
        except ImportError:
            from .audit import log as _audit
        _audit(final_aid, "update_config", key, value[:200] if isinstance(value, str) else str(value))
    except Exception:
        pass
    return json.dumps(safe, ensure_ascii=False, indent=2)


# ---------- 工具清单（暴露给 MCP 客户端）----------

TOOLS = [
    {
        "name": "recall_context",
        "description": "返回伴生灵魂盘（Soul Framework）的完整上下文包（人格 + Agent ID + 多智能体协作铁律 + 公共/私密分区说明 + 已积累的用户理解 + 走廊预载 + 行为契约），供 AI 注入 system prompt。首次接入必须先调 get_onboarding 与 get_config，确认身份与可读范围。输出含 wing 读写权限说明。每次会话开始时调一次即可。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。不传则回退到环境变量 SOUL_AGENT_ID 或空身份。"},
            },
        },
    },
    {
        "name": "get_fact",
        "description": "检索记忆。按主体(subject)或记录id(fact_id)查询，返回匹配的记忆列表。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
                "subject": {"type": "string", "description": "主体，如 '用户'"},
                "fact_id": {"type": "string", "description": "记录 id，如 'fact_0001'"},
            },
        },
    },
    {
        "name": "record_fact",
        "description": "写入/更新一条记忆。同主体同陈述自动去重更新。\n\n类型选择指南（选错会影响 AI 行为）：\n- FACT（事实/知识/经验）：客观存在的、可验证的信息。如：用户职业、项目技术栈、操作结果、踩坑经验、配置方法。写入 cross 公共区，所有 agent 可读。\n- PREF（偏好/习惯/喜好）：用户主观偏好。如：喜欢短回复、不要用表情、工作习惯、回复风格。写入私密区，每个 agent 独立。\n- BOUND（边界/规则/底线）：不可违反的约定和约束。如：改代码前必须全链路思考、不能删除某个数据、某个话题不讨论。写入私密区。\n- COMMIT（承诺/锚定/约定）：AI 对用户的承诺或长期约定。如：会按时交付、保持某个习惯、角色定义（搭档不是工具）。写入私密区。\n\n判断标准：事实→FACT，你喜欢/不喜欢→PREF，绝对不能/必须→BOUND，我答应你/说定了→COMMIT。\n\n已注册 agent 需要 write_key 才能写入。",
        "inputSchema": {
            "type": "object",
            "required": ["ftype", "subject", "statement"],
            "properties": {
                "ftype": {"type": "string", "enum": ["FACT", "PREF", "BOUND", "COMMIT"], "description": "记忆类型。FACT=客观事实/知识（公共区）；PREF=用户偏好（私密）；BOUND=不可违反的约定/底线（私密）；COMMIT=AI承诺/长期约定（私密）。选错会影响 AI 行为，详见工具描述。"},
                "subject": {"type": "string", "description": "主体，通常为 '用户'"},
                "statement": {"type": "string", "description": "要记住的内容"},
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
                "write_key": {"type": "string", "description": "已注册 agent 需要此参数才能写入。首次注册后返回的 write_key。"},
                "confidence": {"type": "number", "description": "置信度 0-1，默认 1.0"},
                "immutable": {"type": "boolean", "description": "是否锁定不可变（用户级硬事实），默认 false"},
            },
        },
    },
    {
        "name": "compact_memory",
        "description": "蒸馏压缩：把旧的低价值记录归档到 cold/ 并刷新长期摘要层。建议定期调用，防止数据无限膨胀、保持 recall 的 token 成本恒定。可选参数 archive_age_days（默认180天）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
                "archive_age_days": {"type": "number", "description": "超过该天数的低价值非不可变记录将归档，默认 180"},
            },
        },
    },
    {
        "name": "get_config",
        "description": "读取指定 agent 的身份配置与 wing 读写铁律。agent_id 是关键——写 vault 时用它当 wing，终身不变改了 agent_name 也不丢数据。agent_name 是显示名可随意改。首次连接先调此工具确认你是谁、能读什么、不能读什么。返回结果包含 rules.read_allowed / rules.write_allowed / rules.forbidden / rules.first_steps。不包含 write_key。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。不传则回退到环境变量或空身份。"},
            },
        },
    },
    {
        "name": "get_onboarding",
        "description": "新 agent 接入必读：你是谁、能读什么、不能读什么、可写范围、首次接入三步、write_key 机制、wing 读写铁律。每个新 agent 首次连接时必须先调用一次，再调 get_config 与 recall_context。支持传 agent_id 声明身份。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。不传则基于环境变量或空身份推断。"},
            },
        },
    },
    {
        "name": "update_config",
        "description": "修改身份配置。agent_id 只可设一次（设完锁定，防止丢历史数据）；agent_name/user_name/relationship/tone 可随时改。agent_name/user_name/agent_id 不可为空。\n\n首次注册时自动生成 write_key 并返回（仅此一次）。修改已注册 agent 的配置需提供正确的 write_key，否则拒绝。",
        "inputSchema": {
            "type": "object",
            "required": ["key", "value"],
            "properties": {
                "key": {"type": "string", "enum": ["agent_id", "agent_name", "user_name", "relationship", "tone"], "description": "要改的字段"},
                "value": {"type": "string", "description": "新值"},
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
                "write_key": {"type": "string", "description": "修改已注册 agent 配置时必填。首次注册时返回的 write_key。"},
            },
        },
    },
    {
        "name": "vault_store",
        "description": "写入一条记忆到混合记忆层。wing 只能填 'cross'（公共区，全员可见）或你自己的 agent_id（私密区）。禁止写其他 agent 的 wing。level: active=走廊(近期预载,默认) / archive=房间(冷记忆)。room 按内容选：fact/pref/bound/commit/notes/decisions。\n\n写入私密区时如果该 agent 已注册并设置 write_key，需要提供正确的 write_key。",
        "inputSchema": {
            "type": "object",
            "required": ["content"],
            "properties": {
                "content": {"type": "string", "description": "记忆内容"},
                "wing": {"type": "string", "description": "只能填 'cross'（公共区）或你的 agent_id（私密区）。禁止传其他 agent 的 wing。"},
                "room": {"type": "string", "description": "内容分类。FACT→fact, PREF→pref, 随手笔记→notes, 决策→decisions, 默认 general"},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "标签列表"},
                "level": {"type": "string", "enum": ["active", "archive"], "description": "写入层，默认 active"},
                "summary": {"type": "string", "description": "可选摘要"},
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
                "write_key": {"type": "string", "description": "可选。写入私密区时，如果该 wing 的 agent 已注册需要此参数。"},
            },
        },
    },
    {
        "name": "vault_search",
        "description": "FTS5 全文检索混合记忆层。默认只搜你自己的 wing（私密区）；要查公共区请显式传 wing='cross'。严禁传其他 agent 的 wing。level 默认 'archive'(房间)；'all' 扫 active+archive；'active' 只扫走廊。",
        "inputSchema": {
            "type": "object",
            "required": ["query"],
            "properties": {
                "query": {"type": "string", "description": "检索词"},
                "level": {"type": "string", "enum": ["archive", "all", "active"], "description": "检索范围，默认 archive"},
                "limit": {"type": "number", "description": "返回条数上限，默认 10"},
                "wing": {"type": "string", "description": "不传=只搜你的 agent_id；传 'cross'=只搜公共区；禁止传其他 agent 的 wing"},
                "room": {"type": "string", "description": "限定房间"},
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
            },
        },
    },
    {
        "name": "vault_status",
        "description": "查看混合记忆层全局状态：走廊/房间各多少条、滚动阈值、预载条数、当前周。不限定 wing，看全库统计。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "vault_compact",
        "description": "滚动归档：走廊(active)满 50 条或跨周 → 沉淀进房间(archive) 并刷新 weekly rollup。force=true 忽略阈值强制滚动。保留手动入口。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "force": {"type": "boolean", "description": "是否强制滚动，默认 false"},
            },
        },
    },
    {
        "name": "export_vault",
        "description": "把当前实例的记忆（facts + vault）导出为 Obsidian 兼容的 Markdown 树，存到本地目录（默认 <实例>/data/vault-md/），便于在 Obsidian 中直接浏览/搜索/编辑。只读，不修改任何存储。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target_dir": {"type": "string", "description": "可选。导出目录，默认 <实例>/data/vault-md/"},
                "agent_id": {"type": "string", "description": "可选。声明调用方身份。"},
            },
        },
    },
    {
        "name": "get_audit",
        "description": "读取调用方自己的操作审计日志（record_fact / vault_store / update_config 等写操作留痕）。返回最近 limit 条，供可追溯性与安全研判。只能查自己，不可查他人。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "number", "description": "返回条数上限，默认 50"},
                "agent_id": {"type": "string", "description": "可选。声明调用方身份（仅查自己）。"},
            },
        },
    },
]


# ---------- MCP 协议处理（JSON-RPC 2.0 over stdio）----------

def handle_message(msg):
    """处理一条 JSON-RPC 消息。返回 dict 回应，或 None（通知/心跳，不回）。"""
    if not isinstance(msg, dict):
        return None

    method = msg.get("method")
    req_id = msg.get("id")

    # 通知（无 id，如 notifications/initialized）不回应
    if req_id is None:
        return None

    if method == "initialize":
        pv = msg.get("params", {}).get("protocolVersion", "2024-11-05")
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": pv,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "soul-framework", "version": "1.1.0"},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        try:
            if name == "recall_context":
                text = tool_recall_context(agent_id=args.get("agent_id"))
            elif name == "get_fact":
                text = tool_get_fact(
                    agent_id=args.get("agent_id"),
                    subject=args.get("subject"),
                    fact_id=args.get("fact_id"),
                )
            elif name == "record_fact":
                text = tool_record_fact(
                    args["ftype"], args["subject"], args["statement"],
                    agent_id=args.get("agent_id"),
                    write_key=args.get("write_key"),
                    confidence=args.get("confidence", 1.0),
                    immutable=args.get("immutable", False),
                )
            elif name == "compact_memory":
                text = tool_compact_memory(
                    agent_id=args.get("agent_id"),
                    archive_age_days=args.get("archive_age_days", 180),
                )
            elif name == "get_config":
                text = tool_get_config(agent_id=args.get("agent_id"))
            elif name == "get_onboarding":
                text = tool_get_onboarding(agent_id=args.get("agent_id"))
            elif name == "update_config":
                text = tool_update_config(
                    args["key"], args["value"],
                    agent_id=args.get("agent_id"),
                    write_key=args.get("write_key"),
                )
            elif name == "vault_store":
                text = tool_vault_store(
                    args["content"], args.get("wing", "default"),
                    args.get("room", "general"), args.get("tags"),
                    args.get("level", "active"), args.get("summary"),
                    agent_id=args.get("agent_id"),
                    write_key=args.get("write_key"),
                )
            elif name == "vault_search":
                text = tool_vault_search(
                    args["query"], args.get("level", "archive"),
                    args.get("limit", 10), args.get("wing"), args.get("room"),
                    agent_id=args.get("agent_id"),
                )
            elif name == "vault_status":
                text = tool_vault_status()
            elif name == "vault_compact":
                text = tool_vault_compact(args.get("force", False))
            elif name == "export_vault":
                text = tool_export_vault(
                    target_dir=args.get("target_dir"),
                    agent_id=args.get("agent_id"),
                )
            elif name == "get_audit":
                text = tool_get_audit(
                    limit=args.get("limit", 50),
                    agent_id=args.get("agent_id"),
                )
            else:
                raise ValueError(f"未知工具 {name}")
            result = {"content": [{"type": "text", "text": str(text)}], "isError": False}
        except Exception as e:
            result = {"content": [{"type": "text", "text": f"错误: {e}"}], "isError": True}
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    # 其他带 id 的未知 method
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main():
    """stdio 主循环：逐行读 JSON-RPC，回 JSON-RPC。

    Windows 注意：中文 Windows 默认 stdout/stdin 是 GBK(cp936)，
    但 MCP 协议要求 UTF-8。此处强制重绑为 UTF-8，否则 WorkBuddy 等客户端
    会报 "stream did not contain valid UTF-8"。
    """
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stdin  = io.TextIOWrapper(sys.stdin.buffer,  encoding="utf-8", errors="replace")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_message(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
