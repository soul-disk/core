#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
compact.py — 伴生灵魂盘（Soul Framework） · 蒸馏压缩（治"用几年后臃肿"的治本手段）

问题：
    record 只 append，几年后单文件越来越大；context_pack 即便有预算截断，
    也要先全量读热层再排序——数据极大时 IO/CPU 不划算，且低价值旧记录占着热层。

解法（蒸馏）：
    1. 把"低重要性 + 够老 + 非不可变"的旧记录移入 data/facts/cold/ 归档，
       热层只留近期/高价值记录（context_pack 只读热层 + 摘要，token 恒定）。
    2. 把同类记录聚类，生成"长期理解摘要"写入 summary.jsonl，
       recall 时摘要优先注入（轻量、稳定、不随条数膨胀）。

用法：
    手动：  python engine/compact.py
    或 AI 在对话中调 MCP 工具 compact_memory 触发。

说明：
    本模块提供"纯规则兜底蒸馏"（按 subject+type 取最高置信代表句）。
    更高质的摘要由承载 AI 完成：AI 调 get_fact 取全量 → 自行归纳 →
    record_fact 写回 summary（type 标记为 SUMMARY）。二者互补。
"""

import os
import json
import glob
import datetime


TYPE_FILE = {
    "FACT": "facts.jsonl",
    "PREF": "prefs.jsonl",
    "BOUND": "bounds.jsonl",
    "COMMIT": "commits.jsonl",
}


class Compactor:
    def __init__(self, root=None):
        self.root = root or os.environ.get(
            "XIAOAN_SOUL_ROOT",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        self.facts_dir = os.path.join(self.root, "data", "facts")
        self.cold_dir = os.path.join(self.facts_dir, "cold")

    def _read_hot(self):
        out = []
        for fp in glob.glob(os.path.join(self.facts_dir, "*.jsonl")):
            if os.path.basename(fp) == "summary.jsonl":
                continue
            with open(fp, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return out

    def _read_summary(self):
        path = os.path.join(self.facts_dir, "summary.jsonl")
        out = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            out.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
        return out

    @staticmethod
    def importance(rec):
        score = float(rec.get("confidence", 1.0))
        if rec.get("immutable"):
            score += 2.0
        age = 999
        c = rec.get("created")
        if c:
            try:
                age = (datetime.date.today() - datetime.date.fromisoformat(c)).days
            except Exception:
                pass
        score *= (0.5 + 1.0 / (1.0 + max(age, 0) / 180.0))
        return score

    def run(self, archive_age_days=180):
        """执行蒸馏压缩。

        archive_age_days：超过该天数的"低重要性非不可变"记录归档到 cold/。
        返回统计 dict。
        """
        hot = self._read_hot()
        today = datetime.date.today()

        cold_records = []
        stay = []
        for r in hot:
            age = 999
            c = r.get("created")
            if c:
                try:
                    age = (today - datetime.date.fromisoformat(c)).days
                except Exception:
                    pass
            # 底线保护：BOUND(边界)/COMMIT(承诺) 永不归档，始终留在热层。
            # 否则 180 天后 AI 可能"忘了用户定下的底线/承诺"，对一个以
            # "四条刚性底线"为核心的产品是硬伤。
            protected = r.get("type") in ("BOUND", "COMMIT")
            if (not r.get("immutable")) and (not protected) and age > archive_age_days and self.importance(r) < 1.0:
                cold_records.append(r)
            else:
                stay.append(r)

        # 热层写回（按类型分文件；被全归档的类型文件须清空，避免旧记录在热层残留）
        by_file = {fn: [] for fn in TYPE_FILE.values()}
        for r in stay:
            fn = TYPE_FILE.get(r.get("type", "FACT"))
            by_file[fn].append(r)
        for fn, items in by_file.items():
            with open(os.path.join(self.facts_dir, fn), "w", encoding="utf-8") as f:
                for r in items:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        # 冷层归档
        if cold_records:
            os.makedirs(self.cold_dir, exist_ok=True)
            cold_path = os.path.join(self.cold_dir, f"archive_{today.year}.jsonl")
            with open(cold_path, "a", encoding="utf-8") as f:
                for r in cold_records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")

        # 刷新长期摘要（纯规则兜底；AI 可覆盖为更高质量摘要）
        summary = self._build_summary(stay)
        self._write_summary(summary)

        # Vault 兼容：同步触发走廊滚动归档
        vault_rolled = 0
        try:
            from engine.vault import Vault
            v = Vault(root=self.root)
            vr = v.compact()
            vault_rolled = vr.get("rolled", 0)
        except Exception:
            pass

        return {
            "archived": len(cold_records),
            "stay_hot": len(stay),
            "summary_items": len(summary),
            "cold_dir": self.cold_dir if cold_records else None,
            "vault_rolled": vault_rolled,
        }

    def _build_summary(self, recs):
        """纯规则兜底：按 (subject, type) 聚类，保留最高重要性代表句。"""
        groups = {}
        for r in recs:
            key = (r.get("subject"), r.get("type"))
            groups.setdefault(key, []).append(r)
        out = []
        today = datetime.date.today().isoformat()
        for (subj, typ), items in groups.items():
            items_sorted = sorted(items, key=self.importance, reverse=True)
            rep = items_sorted[0]
            out.append({
                "id": f"sum_{len(out) + 1:03d}",
                "type": typ,
                "subject": subj,
                "statement": f"[长期摘要]{rep.get('statement')}（同类共 {len(items)} 条）",
                "confidence": rep.get("confidence", 1.0),
                "created": today,
                "updated": today,
                "source": "compact蒸馏",
            })
        return out

    def _write_summary(self, summary):
        path = os.path.join(self.facts_dir, "summary.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            for s in summary:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    print("蒸馏结果：", Compactor().run())
