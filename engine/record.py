#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
record.py — 伴生灵魂盘（Soul Framework） · 主动记录接口（去数据化通用版）

职责：把 AI 在对话中识别到的"值得长期保留"的内容，写回 data/facts/。
      支持去重（同主体同陈述更新，不重复建 id）、置信度、不可变锁定。

用法：
    from engine.record import Recorder
    rec = Recorder()
    rec.add("FACT", "用户", "职业是 AI 产品经理", confidence=0.9)  # 示例数据，请替换为真实语义
    rec.add("PREF", "用户", "偏好简洁回复", immutable=False)  # 示例数据，请替换为真实语义
"""

import os
import json
import glob
import datetime
import traceback
from contextlib import contextmanager

# 跨进程文件锁（多智能体并发写 facts jsonl 防互相覆盖）
try:
    import msvcrt
    def _lock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    def _unlock_file(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
except ImportError:
    import fcntl
    def _lock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    def _unlock_file(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


TYPE_FILE = {
    "FACT": "facts.jsonl",
    "PREF": "prefs.jsonl",
    "BOUND": "bounds.jsonl",
    "COMMIT": "commits.jsonl",
}


class Recorder:
    def __init__(self, root=None, agent_name=None):
        # 根目录：SOUL_ROOT（统一规范名）> 兼容旧 XIAOAN_SOUL_ROOT > 脚本同级
        self.root = root or os.environ.get(
            "SOUL_ROOT",
            os.environ.get(
                "XIAOAN_SOUL_ROOT",
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ),
        )
        self.facts_dir = os.path.join(self.root, "data", "facts")
        os.makedirs(self.facts_dir, exist_ok=True)
        self._vault = None  # 延迟初始化，首次 add 时才开（避免无数据空建 db）
        self.wing_id = agent_name or self._read_agent_name()

    def _read_agent_name(self):
        """读取稳定 agent 标识作为 Vault wing key。

        优先级：agent_id（终身不变）> agent_name（显示名，可改）> "default"。
        用 agent_id 的原因：agent_name 改了不会丢历史数据。
        """
        try:
            path = os.path.join(self.root, "config.schema.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                ident = cfg.get("agent_id", "").strip()
                if not ident:
                    ident = cfg.get("agent_name", "").strip()
                return ident or "default"
        except Exception:
            pass
        return "default"

    def _path(self, ftype):
        return os.path.join(self.facts_dir, TYPE_FILE.get(ftype, "facts.jsonl"))

    @contextmanager
    def _facts_lock(self):
        """跨进程排它锁，串行化 jsonl 写，避免多 agent 并发重写互相覆盖。"""
        lock_path = os.path.join(self.facts_dir, ".write.lock")
        with open(lock_path, "w", encoding="utf-8") as lf:
            _lock_file(lf)
            try:
                yield
            finally:
                _unlock_file(lf)

    def _all(self):
        # 关键：summary.jsonl 由 compact.py 独占管理，record 层绝不读/写它。
        # 否则 _rewrite_all 会把摘要记录泄露进热层文件，且任意 delete/update
        # 调用 _rewrite_all 时会因不重写 summary.jsonl 而清空整个摘要层。
        recs = []
        for fp in glob.glob(os.path.join(self.facts_dir, "*.jsonl")):
            if os.path.basename(fp) == "summary.jsonl":
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            recs.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return recs

    def _next_id(self, ftype):
        prefix = {"FACT": "fact", "PREF": "pref", "BOUND": "bound", "COMMIT": "commit"}[ftype]
        existing = [r["id"] for r in self._all() if r.get("id", "").startswith(prefix)]
        nums = [int(r.split("_")[-1]) for r in existing if r.split("_")[-1].isdigit()]
        return f"{prefix}_{nums[-1] + 1:04d}" if nums else f"{prefix}_0001"

    def add(self, ftype, subject, statement, confidence=1.0, immutable=False, source="对话", weight=1.0):
        """新增或更新一条记忆。返回记录 dict。

        weight：重要性权重（默认 1.0，>1 提权、<1 降权），融入 recall 排序。
        """
        if ftype not in TYPE_FILE:
            raise ValueError(f"未知类型 {ftype}，应为 {list(TYPE_FILE)}")
        today = datetime.date.today().isoformat()
        try:
            weight = float(weight)
        except (TypeError, ValueError):
            weight = 1.0

        # 去重：同 agent 下 同主体同陈述才更新（避免跨 agent 误去重）
        for r in self._all():
            if r.get("agent_id", "") == self.wing_id and r.get("subject") == subject and r.get("statement") == statement:
                r["updated"] = today
                r["confidence"] = max(r.get("confidence", 0), confidence)
                try:
                    r["weight"] = max(float(r.get("weight", 1.0)), float(weight))
                except (TypeError, ValueError):
                    r["weight"] = r.get("weight", 1.0)
                self._rewrite_all()
                self._to_vault(r)  # 去重更新也同步 vault（容忍重复写入）
                return r

        rec = {
            "id": self._next_id(ftype),
            "type": ftype,
            "subject": subject,
            "statement": statement,
            "agent_id": self.wing_id,
            "wing": "cross" if ftype == "FACT" else self.wing_id,
            "source": source,
            "confidence": confidence,
            "immutable": immutable,
            "weight": weight,
            "created": today,
            "updated": today,
        }
        with self._facts_lock():
            with open(self._path(ftype), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        # Vault 分流：同步写入走廊层（active），供 recall_context 预载
        self._to_vault(rec)

        # 审计：记录写操作（best-effort，失败不阻断）
        try:
            try:
                from engine.audit import log as _audit
            except ImportError:
                from .audit import log as _audit
            _audit(self.wing_id, "record_fact", rec.get("id", ""), f"{ftype}:{subject}", root=self.root)
        except Exception:
            pass

        return rec

    def update(self, fact_id, **fields):
        """按 id 更新字段。"""
        today = datetime.date.today().isoformat()
        for r in self._all():
            if r.get("id") == fact_id:
                if r.get("immutable") and any(k in fields for k in ("subject", "statement")):
                    raise PermissionError(f"记录 {fact_id} 为 immutable，不可改主体/陈述")
                r.update(fields)
                r["updated"] = today
                self._rewrite_all()
                return r
        return None

    def delete(self, fact_id):
        """物理删除（被遗忘权）。"""
        with self._facts_lock():
            recs = self._all()
            kept = [r for r in recs if r.get("id") != fact_id]
            if len(kept) == len(recs):
                return False
            # 重写所有文件
            by_file = {}
            for r in kept:
                fn = TYPE_FILE.get(r.get("type", "FACT"))
                by_file.setdefault(fn, []).append(r)
            for fn, items in by_file.items():
                with open(os.path.join(self.facts_dir, fn), "w", encoding="utf-8") as f:
                    for r in items:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")
            return True

    def _to_vault(self, rec):
        """同步写入 Vault 走廊层（best-effort，失败不中断主流程）。

        多智能体分区规则：
        - FACT（事实/知识/经验）→ wing="cross" 公共区，全员可见可学
        - PREF / BOUND / COMMIT → wing=self.wing_id 私密区，仅本 agent 可见
        """
        try:
            try:
                from engine.vault import Vault
            except ImportError:
                import sys as _sys, os as _os
                _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
                from engine.vault import Vault
            if self._vault is None:
                self._vault = Vault(root=self.root)
            # 分区决策
            if rec.get("type") == "FACT":
                wing = "cross"
            else:
                wing = self.wing_id
            v = self._vault
            v.store(
                content=f"[{rec['type']}] {rec['statement']}",
                wing=wing,
                room=rec.get("type", "FACT").lower(),
                tags=[rec.get("type", "FACT"), rec.get("subject", "")],
                level="active",
                summary=f"{rec.get('subject', '')}: {rec.get('statement', '')[:120]}",
            )
        except Exception:
            traceback.print_exc()

    def _rewrite_all(self):
        with self._facts_lock():
            recs = self._all()
            by_file = {}
            for r in recs:
                fn = TYPE_FILE.get(r.get("type", "FACT"))
                by_file.setdefault(fn, []).append(r)
            for fn, items in by_file.items():
                with open(os.path.join(self.facts_dir, fn), "w", encoding="utf-8") as f:
                    for r in items:
                        f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    # 演示：空壳出厂后第一条记录（由 AI 在对话中调用）
    r = Recorder()
    demo = r.add("FACT", "<用户>", "（示例）用户首次启动时自填的信息会出现在这里", confidence=1.0)
    print("已写入示例记录：", demo["id"])
    # 清理演示
    r.delete(demo["id"])
    print("演示记录已清理（出厂数据盘保持为空）")
