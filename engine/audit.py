#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit.py — 操作审计日志（零依赖，独立 SQLite）

记录每次写操作（agent_id / action / target / detail / created_at），
供 Soul-Disk 可追溯性与安全研判。

设计：
  - 独立 data/audit.db，不碰 vault 的 active/archive，避免触发器/索引干扰。
  - best-effort：审计失败绝不阻断主流程（写记忆/配置才是关键路径）。
  - 零依赖：仅 Python 标准库 sqlite3。
"""

import os
import sqlite3
import datetime


def _resolve_root(root):
    return root or os.environ.get(
        "SOUL_ROOT",
        os.environ.get(
            "XIAOAN_SOUL_ROOT",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ),
    )


def _db_path(root):
    root = _resolve_root(root)
    d = os.path.join(root, "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "audit.db")


def log(agent_id, action, target="", detail="", root=None):
    """记录一条审计。best-effort，异常吞掉不阻断主流程。"""
    try:
        db = _db_path(root)
        conn = sqlite3.connect(db)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id TEXT,
                action TEXT,
                target TEXT,
                detail TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute(
            "INSERT INTO audit_log(agent_id, action, target, detail, created_at) "
            "VALUES(?,?,?,?,?)",
            (
                str(agent_id or ""),
                str(action),
                str(target or ""),
                str(detail or ""),
                datetime.datetime.now(datetime.timezone.utc).isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def query(limit=50, agent_id=None, root=None):
    """读取近期审计。best-effort，失败返回空列表。"""
    try:
        db = _db_path(root)
        if not os.path.exists(db):
            return []
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        if agent_id:
            rows = conn.execute(
                "SELECT * FROM audit_log WHERE agent_id=? ORDER BY id DESC LIMIT ?",
                (str(agent_id), int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []
