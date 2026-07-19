#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vault.py — 伴生灵魂盘（Soul Framework） · 混合记忆层（走廊 + 房间）

架构（见 docs/记忆库-vault-架构定案.md）：
  厅堂 JSONL 常驻   → record.py 负责，本文件不碰
  走廊 SQLite active → 近期对话摘要 / 本周事件 / 活跃任务，预载最近 N 条
  房间 SQLite archive → 历史摘要 / 冷记忆，FTS5 全文检索按需拉取（不预载）

约束：
  - 零依赖：仅 Python 标准库 sqlite3 + FTS5(trigram 分词)
  - 不引 ChromaDB / numpy / jieba
  - 数据盘 <root>/data/vault/{active.db, archive.db}，出厂零数据，首次调用自动建

决策锁定（2026-07-18 确认）：
  - 走廊预载 10 条（ACTIVE_PRELOAD，可配）
  - vault_search 默认只搜 archive；level='all' 才扫 active+archive
  - 滚动时增量刷新 weekly rollup，不引定时器
  - 自然周以 Asia/Shanghai 计（周一为起点）；created_at 存 UTC，展示本地
  - 滚动阈值：active 满 50 条 或 跨周，先满先滚
"""

import os
import json
import sqlite3
import datetime
import time
from contextlib import contextmanager

# 跨进程文件锁（多智能体并发写 vault，避免 roll 竞态导致重复/丢失）
try:
    import msvcrt
    def _vlock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    def _vunlock(f):
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
except ImportError:
    import fcntl
    def _vlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
    def _vunlock(f):
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


ACTIVE_PRELOAD = 10           # 走廊预载条数
ACTIVE_ROLL_THRESHOLD = 50    # 走廊滚动阈值（满即滚）
TZ_OFFSET_HOURS = 8           # Asia/Shanghai = UTC+8（零依赖，不依赖 zoneinfo）


def _utc_now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _shanghai_now():
    """返回上海时区当前 datetime（零依赖：UTC+8 手动偏移）。"""
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=TZ_OFFSET_HOURS)


def _week_key(dt=None):
    """自然周 key：YYYY-Www（ISO 周，周一起点），以上海时区计。"""
    dt = dt or _shanghai_now()
    return dt.strftime("%G-W%V")


def _conn(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    # 多智能体并发：开 WAL 日志模式 + 忙等待，避免多进程写时 database is locked
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
    except sqlite3.OperationalError:
        pass
    return conn


def _init_db(db_path):
    conn = _conn(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY,
            wing TEXT NOT NULL DEFAULT 'default',
            room TEXT NOT NULL DEFAULT 'general',
            level TEXT NOT NULL DEFAULT 'active',
            content TEXT NOT NULL,
            summary TEXT,
            tags TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            created_week TEXT NOT NULL
        )
    """)
    # FTS5 全文索引（trigram 分词，零依赖）
    # 注：原定 unicode61 对中文无效（CJK 无词边界，整句被当单一 token，
    #      无法按词/子串检索）。trigram 同样内置于 SQLite FTS5、零依赖，
    #      按三字组索引，支持中文子串检索，是中文场景的正确选择。
    cur.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content, summary,
            content='memories', content_rowid='id',
            tokenize='trigram'
        )
    """)
    # 增删改同步 FTS 的触发器
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, summary)
            VALUES (new.id, new.content, new.summary);
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            DELETE FROM memories_fts WHERE rowid = old.id;
        END
    """)
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            DELETE FROM memories_fts WHERE rowid = old.id;
            INSERT INTO memories_fts(rowid, content, summary)
            VALUES (new.id, new.content, new.summary);
        END
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vault_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    # 防重复兜底：同一 wing 下 content 唯一（公共区 cross 实现多 agent 互学去重；
    # 私有 wing 防自身并发写重复）。已存在的实例也补上索引，失败(已有重复)忽略不阻断。
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_content_wing "
            "ON memories(content, wing)"
        )
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


class Vault:
    def __init__(self, root=None):
        self.root = root or os.environ.get(
            "SOUL_ROOT",
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        self.vault_dir = os.path.join(self.root, "data", "vault")
        os.makedirs(self.vault_dir, exist_ok=True)
        self.active_db = os.path.join(self.vault_dir, "active.db")
        self.archive_db = os.path.join(self.vault_dir, "archive.db")
        _init_db(self.active_db)
        _init_db(self.archive_db)
        self._init_rollup()

    def _init_rollup(self):
        """仅 active.db 建 rollup 表（archive 不需要滚动摘要）。"""
        conn = _conn(self.active_db)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS active_rollup (
                week TEXT PRIMARY KEY,
                summary TEXT,
                updated_at TEXT
            )
        """)
        conn.commit()
        conn.close()

    @contextmanager
    def _vault_lock(self):
        """跨进程排它锁，串行化 store+roll，避免多 agent 并发 roll 竞态导致重复/丢失。"""
        lock_path = os.path.join(self.vault_dir, ".write.lock")
        with open(lock_path, "w", encoding="utf-8") as lf:
            _vlock(lf)
            try:
                yield
            finally:
                _vunlock(lf)

    # ---- 写入 ----
    def store(self, content, wing="default", room="general", tags=None,
              level="active", summary=None):
        """写入一条记忆到指定层。level 默认 'active'（走廊），可显式 'archive'（房间）。

        多智能体并发安全：WAL + 写重试（database is locked 时退避重试）。
        """
        if not content or not content.strip():
            raise ValueError("content 不能为空")
        db = self.active_db if level == "active" else self.archive_db
        now = _utc_now_iso()
        wk = _week_key()
        tags_json = json.dumps(tags or [], ensure_ascii=False)
        with self._vault_lock():
            mid = self._store_retry(db, wing, room, level, content, summary, tags_json, now, wk)
            self._maybe_roll()
        return {"id": mid, "level": level, "wing": wing, "room": room, "created_at": now}

    def _store_retry(self, db, wing, room, level, content, summary, tags_json, now, wk, attempts=5):
        """带退避重试的写入，解决多进程并发 'database is locked'。

        关键修正：使用 BEGIN IMMEDIATE 立即取写锁 + INSERT OR IGNORE + 唯一索引
        (content,wing)。即使 commit 遇 busy 超时后重试，也不会产生重复行——
        因为重试时同一条已被唯一索引拒绝（IGNORE），物理上不可能重复。
        """
        last_err = None
        for attempt in range(attempts):
            conn = None
            try:
                conn = _conn(db)
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.cursor()
                cur.execute(
                    "INSERT OR IGNORE INTO memories(wing,room,level,content,summary,tags,created_at,created_week) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (wing, room, level, content, summary, tags_json, now, wk),
                )
                mid = cur.lastrowid
                conn.commit()
                return mid
            except sqlite3.OperationalError as e:
                if conn:
                    try:
                        conn.rollback()
                    except sqlite3.OperationalError:
                        pass
                last_err = e
                if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                    raise
                time.sleep(0.1 * (attempt + 1))
            finally:
                if conn:
                    conn.close()
        raise last_err

    # ---- 检索 ----
    def search(self, query, level="archive", limit=10, wing=None, room=None):
        """FTS5 全文检索。level 默认 'archive'（房间）；'all' 扫 active+archive；'active' 只扫走廊。

        分词器 trigram 要求查询词 >= 3 字符才走 FTS5 索引；短于 3 字（如 'AB'）
        自动降级为 LIKE 子串扫描，保证短词也能命中。
        """
        if not query or not query.strip():
            raise ValueError("query 不能为空")
        q = query.strip()
        if len(q) < 3:
            return self._like_search(q, level, limit, wing, room)
        if level == "all":
            dbs = [self.active_db, self.archive_db]
        elif level == "active":
            dbs = [self.active_db]
        else:
            dbs = [self.archive_db]
        results = []
        for db in dbs:
            conn = _conn(db)
            cur = conn.cursor()
            sql = (
                "SELECT m.id, m.wing, m.room, m.level, m.content, m.summary, "
                "m.created_at, m.created_week FROM memories_fts f "
                "JOIN memories m ON m.id = f.rowid "
                "WHERE memories_fts MATCH ?"
            )
            params = [q]
            if wing:
                sql += " AND m.wing = ?"
                params.append(wing)
            if room:
                sql += " AND m.room = ?"
                params.append(room)
            sql += " ORDER BY m.id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            for row in cur.fetchall():
                results.append({
                    "id": row["id"], "wing": row["wing"], "room": row["room"],
                    "level": row["level"], "content": row["content"],
                    "summary": row["summary"], "created_at": row["created_at"],
                    "created_week": row["created_week"],
                })
            conn.close()
        return results[:limit]

    def _like_search(self, query, level="archive", limit=10, wing=None, room=None):
        """短词（< 3 字）LIKE 子串兜底检索。"""
        if level == "all":
            dbs = [self.active_db, self.archive_db]
        elif level == "active":
            dbs = [self.active_db]
        else:
            dbs = [self.archive_db]
        results = []
        like = f"%{query}%"
        for db in dbs:
            conn = _conn(db)
            cur = conn.cursor()
            sql = ("SELECT id, wing, room, level, content, summary, created_at, created_week "
                   "FROM memories WHERE content LIKE ?")
            params = [like]
            if wing:
                sql += " AND wing = ?"
                params.append(wing)
            if room:
                sql += " AND room = ?"
                params.append(room)
            sql += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            cur.execute(sql, params)
            for row in cur.fetchall():
                results.append({
                    "id": row["id"], "wing": row["wing"], "room": row["room"],
                    "level": row["level"], "content": row["content"],
                    "summary": row["summary"], "created_at": row["created_at"],
                    "created_week": row["created_week"],
                })
            conn.close()
        return results[:limit]

    # ---- 预载（供 recall_context 后续接入） ----
    def preload(self, n=ACTIVE_PRELOAD, wing=None, room=None):
        """返回走廊(active)最近 n 条，时间正序，供 recall 预注入。"""
        conn = _conn(self.active_db)
        cur = conn.cursor()
        sql = "SELECT id,wing,room,level,content,summary,created_at,created_week FROM memories WHERE 1=1"
        params = []
        if wing:
            sql += " AND wing = ?"
            params.append(wing)
        if room:
            sql += " AND room = ?"
            params.append(room)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(n)
        cur.execute(sql, params)
        rows = cur.fetchall()
        conn.close()
        out = []
        for row in reversed(rows):  # 时间正序
            out.append({
                "id": row["id"], "wing": row["wing"], "room": row["room"],
                "content": row["content"], "summary": row["summary"],
                "created_at": row["created_at"], "created_week": row["created_week"],
            })
        return out

    # ---- 状态 ----
    def status(self):
        return {
            "active_count": self._count(self.active_db),
            "archive_count": self._count(self.archive_db),
            "roll_threshold": ACTIVE_ROLL_THRESHOLD,
            "preload": ACTIVE_PRELOAD,
            "last_roll_week": self._meta_get(self.active_db, "last_roll_week"),
            "current_week": _week_key(),
        }

    def _count(self, db):
        conn = _conn(db)
        n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
        return n

    def _meta_get(self, db, key):
        conn = _conn(db)
        row = conn.execute("SELECT value FROM vault_meta WHERE key=?", (key,)).fetchone()
        conn.close()
        return row["value"] if row else None

    def _meta_set(self, db, key, value):
        conn = _conn(db)
        conn.execute(
            "INSERT INTO vault_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()
        conn.close()

    # ---- 滚动归档（compact） ----
    def _maybe_roll(self):
        cur_week = _week_key()
        last_week = self._meta_get(self.active_db, "last_roll_week")
        cnt = self._count(self.active_db)
        if cnt >= ACTIVE_ROLL_THRESHOLD or (last_week and last_week != cur_week):
            self._roll()

    def compact(self, force=False):
        """滚动归档：active 满阈值或跨周 → 迁入 archive + 刷新 weekly rollup。返回统计。"""
        return self._roll(force=force)

    def _roll(self, force=False):
        cur_week = _week_key()
        cnt = self._count(self.active_db)
        last_week = self._meta_get(self.active_db, "last_roll_week")
        if not force and cnt < ACTIVE_ROLL_THRESHOLD and (last_week is None or last_week == cur_week):
            return {"rolled": 0, "reason": "未达阈值且未跨周"}
        # 把 active 全量迁入 archive（走廊内容沉淀为房间历史）
        src = _conn(self.active_db)
        rows = src.execute(
            "SELECT wing,room,level,content,summary,tags,created_at,created_week "
            "FROM memories ORDER BY id"
        ).fetchall()
        src.close()
        dst = _conn(self.archive_db)
        n = 0
        for r in rows:
            cur = dst.execute(
                "INSERT OR IGNORE INTO memories(wing,room,level,content,summary,tags,created_at,created_week) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (r["wing"], r["room"], r["level"], r["content"], r["summary"],
                 r["tags"], r["created_at"], r["created_week"]),
            )
            n += cur.rowcount  # INSERT OR IGNORE: 成功插入=1, 被唯一索引拒绝=0
        dst.commit()
        dst.close()
        self._clear(self.active_db)
        self._refresh_rollup(cur_week, rows)
        self._meta_set(self.active_db, "last_roll_week", cur_week)
        return {"rolled": n, "to_week": cur_week,
                "reason": "threshold_or_week" if not force else "forced"}

    def _clear(self, db):
        conn = _conn(db)
        conn.execute("DELETE FROM memories_fts")
        conn.execute("DELETE FROM memories")
        try:
            conn.execute("DELETE FROM sqlite_sequence WHERE name='memories'")
        except sqlite3.OperationalError:
            pass
        conn.commit()
        conn.close()

    def _refresh_rollup(self, week, rows):
        """增量刷新本周走廊摘要（Phase 1 占位：规则拼接，后续可接 LLM 蒸馏）。"""
        snippet = "\n".join(f"- {r['content'][:120]}" for r in rows[:20])
        summary = f"[走廊周报 {week}]\n{snippet}" if snippet else f"[走廊周报 {week}]（空）"
        conn = _conn(self.active_db)
        conn.execute(
            "INSERT INTO active_rollup(week,summary,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(week) DO UPDATE SET summary=excluded.summary, updated_at=excluded.updated_at",
            (week, summary, _utc_now_iso()),
        )
        conn.commit()
        conn.close()


if __name__ == "__main__":
    # 演示（在临时根下跑，不污染框架 data/）
    import tempfile
    tmp = tempfile.mkdtemp(prefix="vault_demo_")
    os.environ["SOUL_ROOT"] = tmp
    v = Vault(root=tmp)
    v.store("用户在做一款 AI 助手产品", tags=["项目"])  # 示例数据
    v.store("本周讨论了三层 Vault 架构", tags=["架构"])  # 示例数据
    # 默认搜 archive（房间），数据在 active（走廊）→ 空结果符合设计
    print("search('灵魂盘', level='all'):", v.search("灵魂盘", level="all"))
    print("status:", v.status())
    print("预载:", v.preload())
    # force=True 强制滚动：active → archive，之后默认 level 也能搜到
    print("compact(force=True):", v.compact(force=True))
    print("search('灵魂盘') after compact:", v.search("灵魂盘"))
    print("演示目录:", tmp)
