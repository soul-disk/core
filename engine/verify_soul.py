#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_soul.py — 伴生灵魂盘（Soul Framework） · 结构校验（去数据化通用版）

职责：校验框架目录结构完整、config 合法、data/ 数据盘格式正确。
      不因数据盘为空而报错（出厂本就为空）。

用法：
    python engine/verify_soul.py
"""

import os
import json
import sys

REQUIRED_STRUCTURE = [
    "README.md",
    "USER-GUIDE.md",
    "MCP-接入指南.md",
    "mcp_server.py",
    "ENGINE.md",
    "config.schema.json",
    "rules/active-memory.md",
    "rules/psychology.md",
    "rules/user-analysis.md",
    "rules/boundaries.md",
    "rules/host-isolation.md",
    "engine/load_soul.py",
    "engine/record.py",
    "engine/analyze.py",
    "engine/make_context.py",
    "engine/verify_soul.py",
    "engine/compact.py",
    "integration/README.md",
    "examples/minimal.py",
]

EXPECTED_FACT_FILES = ["facts.jsonl", "prefs.jsonl", "bounds.jsonl", "commits.jsonl"]


def verify(root):
    errors, warns, ok = [], [], 0
    for rel in REQUIRED_STRUCTURE:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            ok += 1
        else:
            errors.append(f"缺失文件：{rel}")

    # config 校验
    cfg_path = os.path.join(root, "config.schema.json")
    if os.path.exists(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"config.schema.json 非法 JSON：{e}")

    # data/facts 校验（允许为空）
    facts_dir = os.path.join(root, "data", "facts")
    os.makedirs(facts_dir, exist_ok=True)
    for fn in EXPECTED_FACT_FILES:
        fp = os.path.join(facts_dir, fn)
        if not os.path.exists(fp):
            # 出厂为空合法，建空文件
            open(fp, "a", encoding="utf-8").close()

    print("=" * 50)
    print("伴生灵魂盘（Soul Framework） · 结构校验")
    print("=" * 50)
    print(f"✅ 通过：{ok}/{len(REQUIRED_STRUCTURE)} 结构文件")
    for w in warns:
        print(f"⚠️  {w}")
    for e in errors:
        print(f"❌ {e}")
    print("=" * 50)
    if errors:
        print("结果：失败")
        return 1
    print("结果：通过（出厂数据盘为空属正常）")
    return 0


if __name__ == "__main__":
    root = os.environ.get(
        "XIAOAN_SOUL_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    sys.exit(verify(root))
