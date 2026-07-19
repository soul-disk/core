#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
migrate_add_agent_id.py — 为存量 facts 补 agent_id 字段（多智能体隔离迁移）

背景：早期 facts 未带 agent_id，所有 agent 共用同一份。
本脚本给缺字段的记录补上默认 agent_id（即该灵魂盘的 owner，如 xiaoan），
使读取层按 agent 隔离后，owner 仍能看到自己的全部记忆，其他 agent 只看到公共 FACT。

用法：
    python migrate_add_agent_id.py <soul_root> [agent_id]
例：
    python migrate_add_agent_id.py D:/AI_WorkDir/Soul-Disk xiaoan
"""
import os
import sys
import json
import glob


def migrate(root, agent_id):
    facts_dir = os.path.join(root, "data", "facts")
    if not os.path.isdir(facts_dir):
        print(f"[跳过] 无 facts 目录: {facts_dir}")
        return 0
    total = 0
    patched = 0
    for fp in glob.glob(os.path.join(facts_dir, "*.jsonl")):
        if os.path.basename(fp) == "summary.jsonl":
            continue
        recs = []
        file_changed = False
        with open(fp, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    d = json.loads(s)
                except json.JSONDecodeError:
                    recs.append(s)
                    continue
                total += 1
                if "agent_id" not in d:
                    d["agent_id"] = agent_id
                    file_changed = True
                    patched += 1
                recs.append(json.dumps(d, ensure_ascii=False))
        if file_changed:
            with open(fp, "w", encoding="utf-8") as f:
                for r in recs:
                    f.write(r + "\n")
    print(f"[完成] 扫描 {total} 条，补 agent_id={agent_id} 共 {patched} 条")
    return patched


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("SOUL_ROOT", "")
    agent = sys.argv[2] if len(sys.argv) > 2 else "xiaoan"
    if not root:
        print("用法: python migrate_add_agent_id.py <soul_root> [agent_id]")
        sys.exit(1)
    migrate(root, agent)
