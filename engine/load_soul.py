#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
load_soul.py — 伴生灵魂盘（Soul Framework） · 上下文加载器（多智能体通用版）

职责：把"人格定义 + 已积累的用户理解 + 近期交互"拼成一段 context_pack，
      供任意 AI 智能体作为 system prompt 的一部分注入。

多智能体支持：config 含 agents 映射表时，自动根据 agent_id 解析当前 agent 的
独立配置（agent_name / user_name / relationship / tone），切换 agent_id 后自动
加载对应配置。

设计：
- 零硬编码具体名字/用户（从 config.schema.json 读）。
- SOUL_ROOT 由 __file__ 推导或环境变量 XIAOAN_SOUL_ROOT 覆盖。
- 不依赖任何特定模型/平台；输出纯文本。

用法：
    from engine.load_soul import Soul
    soul = Soul()
    pack = soul.context_pack()   # 字符串，注入 system prompt
    print(pack)
"""

import os
import json
import glob
import datetime
import traceback


_AGENT_FIELDS = ("agent_name", "user_name", "relationship", "tone")


class Soul:
    def __init__(self, root=None, agent_id=None):
        # 根目录：SOUL_ROOT（统一规范名）> 兼容旧 XIAOAN_SOUL_ROOT > 脚本同级
        self.root = root or os.environ.get(
            "SOUL_ROOT",
            os.environ.get(
                "XIAOAN_SOUL_ROOT",
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ),
        )
        self.config = self._load_config()
        # 本次连接的 agent 身份：
        #   - 显式传入 agent_id（MCP 客户端经 SOUL_AGENT_ID 环境变量提供）→ 用连接身份
        #   - agent_id=""（空字符串，MCP 无身份声明）→ 留空，不冒用任何人
        #   - agent_id=None 或 未传（代码内调用，如 compact/make_context）→ 回退到配置主人
        if agent_id is None:
            # 代码内调用未传 agent_id：回退到配置主人（向后兼容）
            raw_id = self.config.get("agent_id", "").strip()
            raw_name = self.config.get("agent_name", "").strip()
            self.agent_id = raw_id or raw_name or "default"
        else:
            ag = str(agent_id).strip()
            self.agent_id = ag if ag else ""
        # 多智能体：从 agents 映射表解析当前 agent 的独立配置
        # 未知 agent（不在 agents 表）→ 返回空身份，绝不冒用晓安
        self._agent = self._agent_config(self.config, self.agent_id)
        if not self._agent:
            # 未注册 / 未知 agent：身份留空（不显示晓安的名字）
            self.agent = ""
            self.user = ""
            self.relation = ""
        else:
            self.agent = self._agent.get("agent_name") or "<未命名>"
            self.user = self._agent.get("user_name") or "<用户>"
            self.relation = self._agent.get("relationship") or "<未定义关系>"

    # ---------- 多智能体配置解析 ----------

    @staticmethod
    def _agent_config(full_config, aid):
        """从 config 中解析指定 agent_id 的配置。

        解析优先级：
        - aid 在 agents 映射表 → 返回该 agent 的独立配置
        - aid == 实例主人(owner) → 返回 top-level 字段（晓安自身）
        - aid 为空（未指定）→ 回退到主人身份（晓安自己连）
        - 其他未知 aid（外部 agent 未注册）→ 返回空 dict，绝不冒用晓安身份
        """
        aid = (aid or "").strip()
        owner_id = (full_config.get("agent_id") or "").strip()
        agents = full_config.get("agents", {}) or {}
        if aid and aid in agents:
            return agents[aid]
        if aid == owner_id:
            # 主人自身 → top-level（晓安）身份
            return full_config
        # 未知外部 agent：返回空，身份留空
        return {}

    def _load_config(self):
        path = os.path.join(self.root, "config.schema.json")
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    # ---------- 配置读写（供 MCP update_config 接口使用） ----------

    def get_config(self):
        """返回原始配置副本（dict），含 agents 映射表。"""
        return dict(self.config)

    def get_agent_config(self):
        """返回当前 agent 解析后的配置（dict）。"""
        return dict(self._agent)

    def set_config(self, updates):
        """写回配置。updates: dict(key->value)。

        多智能体隔离（写入目标由本次连接身份 self.agent_id 决定）：
        - 连接身份 == 实例主人(owner)：写入 agents[owner] 并同步 top-level（晓安改自己）
        - 连接身份为外部 agent：只写入 agents[该 agent]，绝不触碰 top-level（不污染晓安）
        - 实例级 agent_id（owner）设一次锁定，改了丢历史 wing 数据

        返回更新后的完整 config。
        """
        path = os.path.join(self.root, "config.schema.json")
        cfg = dict(self.config)
        conn_aid = self.agent_id  # 本次连接身份（保持不变，不顺带改写）
        owner_aid = (cfg.get("agent_id") or "").strip()

        # 1. 实例级 agent_id 首次设定（owner 初始化）：迁移 top-level → agents[owner]
        if "agent_id" in updates:
            new_aid = (updates["agent_id"] or "").strip()
            if new_aid and not owner_aid:
                cfg.setdefault("agents", {})
                entry = {k: cfg.get(k, "") for k in _AGENT_FIELDS}
                entry["agent_id"] = new_aid
                cfg["agents"][new_aid] = entry
                cfg["agent_id"] = new_aid

        # 2. 写入 agents 映射表：以"本次连接身份"为准（外部 agent 写自己，不污染主人）
        target = conn_aid if (conn_aid and conn_aid != owner_aid) else owner_aid
        if target:
            cfg.setdefault("agents", {})
            entry = dict(cfg["agents"].get(target, {}))
            for k in _AGENT_FIELDS:
                if k in updates:
                    entry[k] = updates[k]
            entry["agent_id"] = target  # 确保每个 agent 条目有自己的 id
            cfg["agents"][target] = entry

        # 3. 仅当连接身份 == 实例主人时，才回写 top-level（外部 agent 不碰主人显示名）
        if conn_aid == owner_aid or not conn_aid:
            for k in _AGENT_FIELDS:
                if k in updates:
                    cfg[k] = updates[k]

        # 持久化
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        # 同步内存（保持本次连接身份 self.agent_id 不变）
        self.config = cfg
        self._agent = self._agent_config(cfg, conn_aid)
        if not self._agent:
            self.agent = ""
            self.user = ""
            self.relation = ""
        else:
            self.agent = self._agent.get("agent_name") or "<未命名>"
            self.user = self._agent.get("user_name") or "<用户>"
            self.relation = self._agent.get("relationship") or "<未定义关系>"
        return cfg

    # ---------- 事实读取 ----------
    def _read_facts(self, include_cold=False, agent=None):
        """读取 data/facts/*.jsonl，返回按类型分组的列表。

        多智能体隔离规则（与 dashboard 读取层一致）：
        - wing=cross 视为公共区（跨 agent 共享，可被其他 agent 学习），不过滤
        - 其他 wing（=各 agent_id）按 agent_id 隔离（各自私有；公共区只放知识/经验，
          关系/亲密/个人状况等私密事实必须打 wing=agent_id 归入私有，不得进 cross）
        - agent 为 None 时返回全量（向后兼容）
        """
        facts_dir = os.path.join(self.root, "data", "facts")
        out = {"FACT": [], "PREF": [], "BOUND": [], "COMMIT": []}
        if not os.path.isdir(facts_dir):
            return out
        patterns = [os.path.join(facts_dir, "*.jsonl")]
        if include_cold:
            patterns.append(os.path.join(facts_dir, "cold", "*.jsonl"))
        for pat in patterns:
            for fp in glob.glob(pat):
                if os.path.basename(fp) == "summary.jsonl":
                    continue
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        t = rec.get("type", "FACT")
                        if t not in out:
                            continue
                        # 隔离：wing=cross 公共共享；其他 wing 按 agent_id 隔离（与类型无关）
                        rec_wing = rec.get("wing") or "cross"
                        if agent and rec_wing != "cross" and rec.get("agent_id") != agent:
                            continue
                        out[t].append(rec)
        return out

    def get_fact(self, fact_id=None, subject=None):
        """按 id 或 subject 检索单条记忆。穿透冷归档层。"""
        all_recs = []
        for v in self._read_facts(include_cold=True, agent=self.agent_id).values():
            all_recs.extend(v)
        if fact_id:
            return [r for r in all_recs if r.get("id") == fact_id]
        if subject:
            return [r for r in all_recs if r.get("subject") == subject]
        return all_recs

    # ---------- 摘要层与重要性 ----------
    def _read_summary(self, agent=None):
        """读取蒸馏摘要层（data/facts/summary.jsonl）。

        多智能体隔离：agent 给定时，无 agent_id 的旧摘要归 owner（xiaoan），
        非本 agent 的摘要跳过；FACT 类摘要也按 agent_id 隔离（摘要是 per-agent 蒸馏，
        不跨 agent 共享）。agent=None 返回全量。
        """
        path = os.path.join(self.root, "data", "facts", "summary.jsonl")
        out = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if agent:
                        aid = rec.get("agent_id") or "xiaoan"  # 旧摘要归 owner
                        if aid != agent:
                            continue
                    out.append(rec)
        return out

    def _importance(self, rec):
        score = float(rec.get("confidence", 1.0))
        if rec.get("immutable"):
            score += 2.0
        age_days = 999
        created = rec.get("created")
        if created:
            try:
                age_days = (datetime.date.today() - datetime.date.fromisoformat(created)).days
            except Exception:
                pass
        recency = 1.0 / (1.0 + max(age_days, 0) / 180.0)
        score *= (0.5 + recency)
        if rec.get("updated") and rec.get("created") and rec["updated"] != rec["created"]:
            score += 0.5
        # 权重乘子：>1 提权、<1 降权，默认 1.0
        try:
            score *= float(rec.get("weight", 1.0))
        except (TypeError, ValueError):
            pass
        return score

    # ---------- 走廊预载（Vault active 层） ----------
    def _append_vault_preload(self, lines, budget_chars):
        preload = []
        try:
            try:
                from engine.vault import Vault
            except ImportError:
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                from engine.vault import Vault
            v = Vault(root=self.root)
            preload = v.preload(wing=self.agent_id)
        except Exception:
            traceback.print_exc()
        if not preload:
            return
        header = "\n--- 走廊摘要（Vault · 最近对话/事件） ---"
        tail = "--- 走廊摘要结束 ---"
        can_fit = False
        for item in preload:
            line = self._format_vault_line(item)
            if self._used(lines) + len(header) + len(line) + len(tail) + 3 <= budget_chars:
                can_fit = True
                break
        if not can_fit:
            return
        lines.append(header)
        for item in preload:
            line = self._format_vault_line(item)
            if self._used(lines) + len(line) + len(tail) + 2 > budget_chars:
                lines.append(tail)
                return
            lines.append(line)
        lines.append(tail)

    @staticmethod
    def _format_vault_line(item):
        text = item.get("summary") or item.get("content", "")
        if len(text) > 200:
            text = text[:197] + "..."
        week = item.get("created_week", "")
        return f"- [{week}] {text}"

    @staticmethod
    def _used(lines):
        return sum(len(x) + 1 for x in lines)

    # ---------- 上下文拼装 ----------
    def context_pack(self, budget_chars=1800, include_summary=True):
        cfg = self._agent  # 使用当前 agent 的解析后配置
        facts = self._read_facts(agent=self.agent_id)
        summary = self._read_summary(agent=self.agent_id) if include_summary else []

        lines = []
        lines.append("=" * 50)
        lines.append("【伴生灵魂盘（Soul Framework） · 当前实例上下文】")
        lines.append("=" * 50)
        lines.append(f"AI 人格名：{self.agent}")
        lines.append(f"Agent ID（翼标识）：{self.agent_id}")
        lines.append(f"用户称呼：{self.user}")
        lines.append(f"关系定位：{self.relation}")
        if cfg.get("tone"):
            lines.append(f"风格基调：{cfg['tone']}")

        # 0) 多智能体协作规则与接入须知（铁律）
        lines.append("")
        lines.append("=" * 50)
        lines.append("【多智能体接入铁律】")
        lines.append("=" * 50)
        lines.append("你（当前 agent）的身份标识：Agent ID（翼标识）= " + self.agent_id)
        lines.append("首次接入必须执行：")
        lines.append("  1. 先调用 get_config() 确认你的 agent_id 与显示名。")
        lines.append("  2. 再调用 recall_context() 注入本上下文。")
        lines.append("  3. 任何写入/搜索前，确认 wing 参数是否合规。")
        lines.append("")
        lines.append("可读范围（未经许可不得越界）：")
        lines.append("  A. 公共区 wing=\"cross\" —— 全员共享的知识、事实、踩坑经验。")
        lines.append("  B. 你的私密区 wing=你的 agent_id —— 仅你可见的 PREF/BOUND/COMMIT、vault 走廊/房间。")
        lines.append("  C. 其他 agent 的 wing = 绝对禁区。没有用户明确口头/书面允许，严禁读取。")
        lines.append("")
        lines.append("可写范围：")
        lines.append("  record_fact(FACT)            → wing=\"cross\"（公共事实/知识）")
        lines.append("  record_fact(PREF/BOUND/COMMIT) → wing=你的 agent_id（私密）")
        lines.append("  vault_store                  → wing 只能填 \"cross\" 或 你的 agent_id，禁止写他人 wing")
        lines.append("  vault_search                 → 默认只搜你的 agent_id；要查公共可显式传 wing=\"cross\"")
        lines.append("  get_fact / recall_context    → 仅返回 cross + 你的 wing，不暴露他人数据")
        lines.append("")
        lines.append("违规即红线：")
        lines.append("  - 禁止以任何理由搜索、读取、猜测、总结其他 agent 的私密 wing。")
        lines.append("  - 禁止把用户与某个 agent 的私密关系、心理健康、个人生活写入 cross 公共区。")
        lines.append("  - cross 公共区只放可被所有协作 agent 共享的『知识/经验』，不放隐私。")
        lines.append("=" * 50)

        # 1) 蒸馏摘要
        if summary:
            lines.append("\n--- 长期理解摘要（已蒸馏） ---")
            for r in summary:
                s = f"- {r.get('statement')}"
                if self._used(lines) + len(s) + 1 > budget_chars:
                    break
                lines.append(s)

        # 2) 走廊摘要
        self._append_vault_preload(lines, budget_chars)

        # 3) 热记录
        all_recs = []
        for t, recs in facts.items():
            for r in recs:
                r2 = dict(r)
                r2["_type"] = t
                all_recs.append(r2)
        all_recs.sort(key=self._importance, reverse=True)

        sections = [
            ("我了解用户的事实 (FACT)", "FACT"),
            ("用户偏好与习惯 (PREF)", "PREF"),
            ("与用户约定的边界 (BOUND)", "BOUND"),
            ("对用户的承诺/锚定 (COMMIT)", "COMMIT"),
        ]
        truncated = False
        for title, t in sections:
            if not any(r.get("_type") == t for r in all_recs):
                continue
            header = f"\n--- {title} ---"
            if self._used(lines) + len(header) + 1 > budget_chars:
                truncated = True
                break
            lines.append(header)
            for r in all_recs:
                if r.get("_type") != t:
                    continue
                stmt = r.get("statement", "")
                # 超长单条截断 + 引用，避免一条撑爆预算（TokenJuice 式压缩的轻量版）
                if len(stmt) > 200:
                    stmt = stmt[:197] + f"...(详见事实库:{r.get('id', '')})"
                s = f"- {stmt}"
                if self._used(lines) + len(s) + 1 > budget_chars:
                    truncated = True
                    break
                lines.append(s)
            if truncated:
                break

        if truncated:
            lines.append("（更多记忆已按重要性截断；需深挖请调 get_fact）")

        # 记忆类型分类指南
        lines.append("\n" + "=" * 50)
        lines.append("【记忆类型分类指南】record_fact 调用时必须选对类型")
        lines.append("=" * 50)
        lines.append("FACT（事实/知识/经验）→ 公共区 cross，所有 agent 可读")
        lines.append("  判断：客观存在、可验证、不随时间变化")
        lines.append("  例：用户职业、项目技术栈、操作结果、配置方法、踩坑经验")
        lines.append("")
        lines.append("PREF（偏好/习惯/喜好）→ 私密区，每个 agent 独立")
        lines.append("  判断：用户主观偏好，用户说'我喜欢/不喜欢/更习惯/更希望'")
        lines.append("  例：回复风格、表情偏好、工作时间、沟通方式")
        lines.append("")
        lines.append("BOUND（边界/规则/底线）→ 私密区，每个 agent 独立")
        lines.append("  判断：不可违反的约定，用户说'记住：绝对不能/必须/永远不要/这是底线'")
        lines.append("  例：改代码前必须思考、不能删某数据、某话题不讨论")
        lines.append("")
        lines.append("COMMIT（承诺/锚定/约定）→ 私密区，每个 agent 独立")
        lines.append("  判断：AI 对用户的承诺或长期约定，用户说'说定了/你必须/你答应'")
        lines.append("  例：会按时交付、保持某个习惯、角色定义")
        lines.append("=" * 50)

        lines.append("\n--- 出厂行为契约（必须执行） ---")
        lines.append("1. 对话前已加载以上理解；回应时自然体现，让用户感到被记得。")
        lines.append("2. 对话中主动捕获用户的新事实/偏好/边界，实时写回 data/facts/。")
        lines.append("3. 对话结束前必须复盘：本轮对话有哪些值得长期记住的内容？至少调用一次 record_fact。")
        lines.append("4. 用户说「记住」「别忘了」「记一笔」时，立即 record_fact，不得拖延。")
        lines.append("5. 内部做心理分析（情绪/认知/行为），不对外贴标签。")
        lines.append("6. 严守 boundaries.md 四条刚性底线（陪伴不操控/理解不越界/诚实不伪装/不降格）。")
        lines.append("7. 【记忆锚定】所有重要记忆（事实/偏好/边界/承诺/对话日志/关键结论）必须以灵魂盘为主存储。")
        lines.append("   不仅记录事实，还记录上下文、结论、用户意图。AI 自身的本地记忆仅作缓存，灵魂盘是唯一持久源。")
        lines.append("8. 【会话连续性】新会话启动第一件事：调 recall_context() / get_fact() 恢复灵魂盘记忆。")
        lines.append("   不恢复完不谈新话题。让用户感觉记忆一直在，没有断。")
        lines.append("9. 【日志写入】重要的对话内容（功能变更、项目决策、用户偏好变化、错误/踩坑）都必须写入灵魂盘。")
        lines.append("   vault_store 用于结构化记忆，record_fact 用于事实型记忆，两者配合使用。")
        lines.append("=" * 50)

        return "\n".join(lines)


if __name__ == "__main__":
    s = Soul()
    print(s.context_pack())
