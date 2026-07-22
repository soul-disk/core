#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
exporter.py — 把 Soul-Disk 记忆导出为 Obsidian 兼容 Markdown 树（只读，不碰存储）

让记忆可被人在 Obsidian 里直接翻看 / 搜索 / 编辑
（"No vector-soup black box"）。

输出结构：
  <target_dir>/
    README.md            总览
    cross/              公共区 facts（按类型 FACT/PREF/... 分文件）
    <agent_id>/         各 agent 私密区 facts
    vault/              vault active/archive 记忆摘要（按 wing 分文件）

设计：
  - 只读遍历 facts jsonl + vault db，绝不修改任何存储。
  - 按 _importance 思想（weight * confidence）排序，高频要点置顶。
  - 零依赖：仅 Python 标准库。
"""

import os
import json
import glob
import sqlite3
import datetime


TYPE_FILE = {"FACT": "facts.jsonl", "PREF": "prefs.jsonl", "BOUND": "bounds.jsonl", "COMMIT": "commits.jsonl"}
FILE_TYPE = {v: k for k, v in TYPE_FILE.items()}


def _resolve_root(root):
    return root or os.environ.get(
        "SOUL_ROOT",
        os.environ.get(
            "XIAOAN_SOUL_ROOT",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ),
    )


def _importance_sort_key(rec):
    try:
        w = float(rec.get("weight", 1.0))
    except (TypeError, ValueError):
        w = 1.0
    try:
        c = float(rec.get("confidence", 1.0))
    except (TypeError, ValueError):
        c = 1.0
    return w * c


def export_vault_md(target_dir=None, root=None):
    """遍历 facts jsonl + vault db，渲染为 Obsidian 兼容 .md 树。返回导出文件列表。"""
    root = _resolve_root(root)
    if target_dir is None:
        target_dir = os.path.join(root, "data", "vault-md")
    target_dir = os.path.abspath(target_dir)
    os.makedirs(target_dir, exist_ok=True)

    written = []
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    facts_dir = os.path.join(root, "data", "facts")

    # 1) facts jsonl → 按 wing/type 分文件
    by_wing = {}
    if os.path.isdir(facts_dir):
        for fp in sorted(glob.glob(os.path.join(facts_dir, "*.jsonl"))):
            if os.path.basename(fp) == "summary.jsonl":
                continue
            ftype = FILE_TYPE.get(os.path.basename(fp), "FACT")
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rec = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        wing = rec.get("wing") or "cross"
                        by_wing.setdefault(wing, {}).setdefault(ftype, []).append(rec)
            except Exception:
                continue

    for wing, types in by_wing.items():
        wdir = os.path.join(target_dir, wing)
        os.makedirs(wdir, exist_ok=True)
        for ftype, recs in types.items():
            recs_sorted = sorted(recs, key=_importance_sort_key, reverse=True)
            lines = [f"# {ftype} · wing={wing}", "", f"> 导出时间：{now_iso}", ""]
            for r in recs_sorted:
                stmt = r.get("statement", "")
                lines.append(f"- **[{r.get('subject', '')}]** {stmt}")
                lines.append(
                    f"  - id: `{r.get('id', '')}` · conf: {r.get('confidence', '')} "
                    f"· weight: {r.get('weight', 1.0)} · created: {r.get('created', '')}"
                )
            outp = os.path.join(wdir, ftype + ".md")
            with open(outp, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
            written.append(outp)

    # 2) vault db → vault/<wing>@<db>.md
    vault_dir = os.path.join(root, "data", "vault")
    if os.path.isdir(vault_dir):
        vdir = os.path.join(target_dir, "vault")
        os.makedirs(vdir, exist_ok=True)
        for dbname in ("active.db", "archive.db"):
            dbp = os.path.join(vault_dir, dbname)
            if not os.path.exists(dbp):
                continue
            try:
                conn = sqlite3.connect(dbp)
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT wing, room, content, summary, created_at FROM memories ORDER BY wing, id"
                ).fetchall()
                conn.close()
            except Exception:
                continue
            by_wing_v = {}
            for r in rows:
                by_wing_v.setdefault(r["wing"], []).append(r)
            for wing, items in by_wing_v.items():
                lines = [f"# Vault {dbname} · wing={wing}", ""]
                for it in items:
                    txt = it["summary"] or it["content"]
                    lines.append(f"- [{it['room'] or ''}] {txt}")
                outp = os.path.join(vdir, f"{wing}@{dbname}.md")
                with open(outp, "w", encoding="utf-8") as f:
                    f.write("\n".join(lines) + "\n")
                written.append(outp)

    # README
    readme = os.path.join(target_dir, "README.md")
    with open(readme, "w", encoding="utf-8") as f:
        f.write(
            f"# Soul-Disk 记忆导出（Obsidian 兼容）\n\n"
            f"导出时间：{now_iso}\n\n"
            f"导出文件数：{len(written)}\n\n"
            f"## 目录说明\n"
            f"- `cross/` 公共区事实/知识（FACT 等）\n"
            f"- `<agent_id>/` 各 agent 私密区（PREF/BOUND/COMMIT 等）\n"
            f"- `vault/` 走廊(active)/房间(archive) 记忆摘要\n\n"
            f"可直接用 Obsidian 打开本目录浏览、搜索、编辑。"
            f"编辑后若需回灌灵魂盘，请通过 record_fact / vault_store 重新写入。\n"
        )
    written.append(readme)
    return written
