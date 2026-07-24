#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — 伴生灵魂盘（Soul Framework）· 实时仪表盘 + 管理面板

零依赖 HTTP 服务，浏览器打开即可实时查看全部数据，区分公共区(cross)与各智能体私密区。
首次启动自动生成管理员密码，登录后可管理 AI 身份和数据。

用法：
    python engine/dashboard.py              # 启动服务 + 打开浏览器（默认 localhost:8877）
    python engine/dashboard.py --no-open    # 只启动服务
    python engine/dashboard.py --port 9999  # 指定端口
    python engine/dashboard.py --export     # 导出静态 HTML
    python engine/dashboard.py --reset-admin  # 重置管理员密码（忘记密码时用，不改记忆数据）
"""

import os, sys, json, glob, datetime, sqlite3, webbrowser, argparse, uuid, hashlib, hmac, re
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

ROOT = os.environ.get("SOUL_ROOT",
    os.environ.get("XIAOAN_SOUL_ROOT",
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# ---------- 会话管理 ----------

_sessions = {}  # {token: timestamp}

def _gen_session():
    return uuid.uuid4().hex + uuid.uuid4().hex

def _is_authenticated(cookies_str):
    """从 Cookie 头提取 session token 并验证。"""
    if not cookies_str:
        return False
    for kv in cookies_str.split(";"):
        kv = kv.strip()
        if kv.startswith("soul_admin="):
            token = kv.split("=", 1)[1].strip()
            return token in _sessions
    return False

# ---------- 密码管理 ----------

# 内存中的初始密码（第一次运行生成，展示给用户后清空）
_initial_password_plain = None

CONFIG_PATH = os.path.join(ROOT, "config.schema.json")

def _load_config():
    """读取 config.schema.json，对常见手改格式错误（尾逗号等）做容错自动修复。

    返回 dict；文件不存在返回 {}。即使 JSON 彻底损坏也不抛异常（返回 {} 并备份损坏文件），
    避免仪表盘因一个逗号就启动崩溃。
    """
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # 容错：自动去除尾逗号（手改 config 删字段最容易留下的坑）
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                raw = f.read()
            repaired = re.sub(r",\s*(?=[}\]])", "", raw)
            cfg = json.loads(repaired)
            # 写回修复后的合法 JSON，根除尾逗号隐患
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            print("[提示] config.schema.json 存在格式问题（如尾逗号），已自动修复并写回。")
            return cfg
        except Exception:
            # 实在修不好：备份后返回空配置，不让仪表盘崩
            try:
                import shutil
                shutil.copy2(CONFIG_PATH, CONFIG_PATH + ".corrupt.bak")
                print(f"[警告] config.schema.json 格式损坏且无法自动修复，已备份为 {CONFIG_PATH}.corrupt.bak；仪表盘以空配置继续。")
            except Exception:
                pass
            return {}

def _save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def _get_admin_password_hash():
    cfg = _load_config()
    return (cfg.get("admin_password") or "").strip()

def _set_admin_password_hash(h):
    cfg = _load_config()
    cfg["admin_password"] = h
    _save_config(cfg)

def _hash_password(pw):
    return hashlib.sha256(pw.encode("utf-8")).hexdigest()

def _ensure_admin_password():
    """首次启动时生成随机管理员密码，打印到控制台 + 持久化到 config 供前端展示（重启后仍可显示），存哈希到 config。

    安全模型（本地单机工具，物理访问即信任）：
      - 初始密码持续显示在登录口，直到用户主动修改密码；
      - 一旦用户改过密码（admin_password_changed=true），初始密码明文即从 config 删除、不再展示；
      - 无网页端"重置密码"入口（避免任何人未登录即可重置的漏洞）。改后忘记密码，
        需手动删除 config.schema.json 的 admin_password 字段并重启，以重新生成初始密码。

    config 中的状态字段：
      - admin_password:          SHA256 哈希（存在 = 已初始化）
      - admin_password_changed:  bool（true = 用户主动改过密码 → 不再展示初始密码）
      - _initial_password_plain: 明文（仅当未改密码时存于 config，改密码后删除）
    """
    global _initial_password_plain
    cfg = _load_config()
    existing = (cfg.get("admin_password") or "").strip()
    changed = cfg.get("admin_password_changed") == True

    if not existing:
        # ── 首次启动：无密码 → 生成并持久化 ──
        pw = uuid.uuid4().hex[:12]
        _initial_password_plain = pw
        _set_admin_password_hash(_hash_password(pw))
        cfg = _load_config()  # 重新读，避免覆盖 _set 的写入
        cfg["_initial_password_plain"] = pw
        cfg["admin_password_changed"] = False
        _save_config(cfg)
        return pw

    if not changed:
        # ── 有密码但用户尚未主动修改 → 从 config 恢复初始密码明文，持续展示 ──
        plain = (cfg.get("_initial_password_plain") or "").strip()
        if plain:
            _initial_password_plain = plain
            return plain  # 重启后仍能显示

    # ── 用户已改过密码（或明文已丢失）→ 不再展示 ──
    return None

# ---------- 管理员操作 ----------

def _delete_agent(agent_id):
    """删除指定 agent 的所有数据。返回 (success, message)。"""
    cfg = _load_config()
    agents = cfg.setdefault("agents", {})
    owner = (cfg.get("agent_id") or "").strip()

    # 不能删除主人
    if agent_id == owner:
        return False, "不能删除实例主人（xiaoan），只能管理外部 agent。"

    if agent_id not in agents:
        return False, f"agent '{agent_id}' 不存在。"

    # 1. 删 config 条目
    del agents[agent_id]
    _save_config(cfg)

    # 2. 删 vault 数据（active + archive）
    for db_name in ("active.db", "archive.db"):
        db_path = os.path.join(ROOT, "data", "vault", db_name)
        if os.path.exists(db_path):
            try:
                conn = sqlite3.connect(db_path)
                cur = conn.cursor()
                rows = cur.execute("SELECT id FROM memories WHERE wing=?", (agent_id,)).fetchall()
                ids = [r[0] for r in rows]
                if ids:
                    cur.executemany("DELETE FROM memories_fts WHERE rowid=?", [(i,) for i in ids])
                    cur.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in ids])
                conn.commit()
                conn.close()
            except Exception:
                pass

    # 3. 删 facts JSONL（精确匹配 agent_id 字段，字符串包含会误删）
    facts_dir = os.path.join(ROOT, "data", "facts")
    if os.path.isdir(facts_dir):
        for fn in ("facts.jsonl", "prefs.jsonl", "bounds.jsonl", "commits.jsonl"):
            fp = os.path.join(facts_dir, fn)
            if os.path.exists(fp):
                with open(fp, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                kept = []
                for l in lines:
                    stripped = l.strip()
                    if not stripped:
                        kept.append(l)
                        continue
                    try:
                        rec = json.loads(stripped)
                        if rec.get("agent_id") != agent_id:
                            kept.append(l)
                    except json.JSONDecodeError:
                        kept.append(l)
                if len(kept) != len(lines):
                    with open(fp, "w", encoding="utf-8") as f:
                        f.writelines(kept)

    return True, f"agent '{agent_id}' 已删除（config + vault + facts 均已清理）。"

def _reset_write_key(agent_id):
    """重置指定 agent 的 write_key。返回 (new_key, message)。"""
    cfg = _load_config()
    agents = cfg.setdefault("agents", {})
    owner = (cfg.get("agent_id") or "").strip()

    if agent_id == owner:
        # 主人 key 在顶层
        wk = uuid.uuid4().hex
        cfg["write_key"] = hashlib.sha256(wk.encode()).hexdigest()
        _save_config(cfg)
        return wk, f"主人（{agent_id}）write_key 已重置。"

    if agent_id not in agents:
        return None, f"agent '{agent_id}' 不存在。"

    wk = uuid.uuid4().hex
    entry = agents[agent_id]
    entry["write_key"] = hashlib.sha256(wk.encode()).hexdigest()
    _save_config(cfg)
    return wk, f"agent '{agent_id}' write_key 已重置。"

# ---------- 数据读取 ----------

def read_jsonl_facts(agent=None):
    facts_dir = os.path.join(ROOT, "data", "facts")
    if not os.path.isdir(facts_dir):
        return {"FACT": [], "PREF": [], "BOUND": [], "COMMIT": []}
    out = {"FACT": [], "PREF": [], "BOUND": [], "COMMIT": []}
    for fp in glob.glob(os.path.join(facts_dir, "*.jsonl")):
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
                rec_wing = rec.get("wing") or "cross"
                if agent and rec_wing != "cross" and rec.get("agent_id") != agent:
                    continue
                out[t].append(rec)
    return out

def read_vault(db_name, agent=None):
    db_path = os.path.join(ROOT, "data", "vault", db_name)
    if not os.path.exists(db_path):
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, wing, room, content, summary, tags, created_at, created_week "
            "FROM memories ORDER BY id DESC"
        ).fetchall()
        data = [dict(r) for r in rows]
        if agent:
            # agent 模式下只返回该 agent 私密 wing，不包含 cross 公共区
            data = [d for d in data if d.get("wing") == agent]
        return data
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()

def read_vault_status(agent=None):
    """读取 vault 状态。agent 给定时只统计该 agent 的私密 wing 数据（不含 cross）。"""
    status = {"active_count": 0, "archive_count": 0, "roll_threshold": 50, "wings": {}}
    for db_name in ("active.db", "archive.db"):
        key = "active_count" if db_name == "active.db" else "archive_count"
        db_path = os.path.join(ROOT, "data", "vault", db_name)
        if not os.path.exists(db_path):
            continue
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            if agent:
                # agent 模式：只统计该 agent 私密 wing（不含 cross）
                cnt = conn.execute(
                    "SELECT COUNT(*) as cnt FROM memories WHERE wing=?", (agent,)
                ).fetchone()["cnt"]
                status[key] = cnt
                for r in conn.execute(
                    "SELECT wing, COUNT(*) as cnt FROM memories WHERE wing=? GROUP BY wing", (agent,)
                ).fetchall():
                    status["wings"][r["wing"]] = status["wings"].get(r["wing"], 0) + r["cnt"]
            else:
                # 全部模式：统计所有 wing
                status[key] = conn.execute("SELECT COUNT(*) as cnt FROM memories").fetchone()["cnt"]
                if db_name == "active.db":
                    meta = conn.execute("SELECT value FROM vault_meta WHERE key='roll_threshold'").fetchone()
                    if meta:
                        status["roll_threshold"] = int(meta["value"])
                for r in conn.execute("SELECT wing, COUNT(*) as cnt FROM memories GROUP BY wing").fetchall():
                    status["wings"][r["wing"]] = status["wings"].get(r["wing"], 0) + r["cnt"]
        except:
            pass
        finally:
            conn.close()
    return status

def read_config():
    # 与 _load_config 共用同一套容错逻辑（含尾逗号自动修复）
    return _load_config()

def gather_all_data(agent=None):
    facts = read_jsonl_facts(agent)
    config = read_config()
    agents_map = config.get("agents", {}) or {}
    if agent and agent in agents_map:
        cfg = {"agent_id": agent}
        for k in ("agent_name", "user_name", "relationship", "tone"):
            cfg[k] = agents_map[agent].get(k, config.get(k, ""))
    elif agent:
        cfg = {"agent_id": agent, "agent_name": "", "user_name": "",
               "relationship": "", "tone": ""}
    else:
        cfg = {k: config.get(k, "") for k in ("agent_id", "agent_name", "user_name", "relationship", "tone")}
    return {
        "FACT": facts["FACT"], "PREF": facts["PREF"], "BOUND": facts["BOUND"], "COMMIT": facts["COMMIT"],
        "vault_active": read_vault("active.db", agent), "vault_archive": read_vault("archive.db", agent),
        "vault_status": read_vault_status(agent),
        "config": cfg,
        "agents_list": list(agents_map.keys()) if agents_map else [config.get("agent_id", "") or "default"],
        "gen_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_records": len(facts["FACT"]) + len(facts["PREF"]) + len(facts["BOUND"]) + len(facts["COMMIT"]),
        "initial_password": _initial_password_plain or "",
    }

# ---------- HTML ----------

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>伴生灵魂盘 · 实时仪表盘</title>
<style>
:root{--bg:#0a0e14;--bg2:#12171f;--bg3:#1a1f2b;--hover:#212838;--border:#2a3040;--text:#dde2ea;--muted:#6b7385;--cross:#2dd4bf;--cross-bg:#0d2b26;--agent1:#f59e0b;--agent1-bg:#2b200d;--agent2:#a78bfa;--agent2-bg:#1c1530;--agent3:#60a5fa;--agent3-bg:#0d1f3a;--green:#22c55e;--red:#ef4444;--yellow:#eab308;--font:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{margin:0;padding:0;box-sizing:border-box}
html{overflow-x:hidden}
body{background:var(--bg);color:var(--text);font-family:var(--font);line-height:1.5;overflow-x:hidden}
.app{display:flex;min-height:100vh}
.sidebar{width:260px;background:var(--bg2);border-right:1px solid var(--border);padding:20px 16px;display:flex;flex-direction:column;position:fixed;top:0;left:0;bottom:0;overflow-y:auto;z-index:10}
.main{margin-left:260px;flex:1;padding:24px 28px;min-width:0;overflow-x:hidden}
.sidebar h2{font-size:17px;margin-bottom:4px}
.sidebar .agent-name{font-size:13px;color:var(--muted);margin-bottom:20px}
.sidebar .nav-label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:var(--muted);margin:18px 0 6px}
.sidebar .nav-item{display:flex;align-items:center;gap:10px;padding:9px 12px;border-radius:8px;cursor:pointer;font-size:13px;color:var(--muted);transition:all .12s;margin-bottom:2px}
.sidebar .nav-item:hover{background:var(--hover);color:var(--text)}
.sidebar .nav-item.active{background:var(--bg3);color:var(--text);font-weight:600}
.sidebar .nav-item .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sidebar .nav-item .dot.cross{background:var(--cross)}
.sidebar .nav-item .dot.agent{background:var(--agent1)}
.sidebar .nav-item .count{font-size:11px;color:var(--muted);margin-left:auto}
.sidebar .footer{font-size:11px;color:var(--muted);margin-top:auto;padding-top:16px;border-top:1px solid var(--border)}
.sidebar .agent-switch{margin:10px 0 4px}
.sidebar .agent-switch select{width:100%;background:var(--bg3);color:var(--text);border:1px solid var(--border);border-radius:7px;padding:7px 8px;font-size:12px;cursor:pointer}
.sidebar .agent-switch select:focus{outline:none;border-color:var(--cross)}

/* Admin panel */
.sidebar .admin-section{border-top:1px solid var(--border);margin-top:12px;padding-top:12px;display:none}
.sidebar .admin-section.visible{display:block}
.admin-btn{display:block;width:100%;padding:8px 12px;background:var(--bg3);border:1px solid var(--border);border-radius:7px;color:var(--text);cursor:pointer;font-size:12px;margin-bottom:4px;text-align:left;transition:all .12s}
.admin-btn:hover{background:var(--hover);border-color:var(--muted)}
.admin-btn.danger{color:var(--red);border-color:var(--red)}
.admin-btn.danger:hover{background:#2b0d0d}
.admin-btn.success{color:var(--green);border-color:var(--green)}
.admin-btn.success:hover{background:#0d2b1a}
.admin-btn.primary{color:var(--cross);border-color:var(--cross)}
.admin-btn.primary:hover{background:var(--cross-bg)}

/* Modal */
.modal-overlay{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.6);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.visible{display:flex}
.modal-box{background:var(--bg2);border:1px solid var(--border);border-radius:12px;padding:24px;max-width:480px;width:90%;max-height:80vh;overflow-y:auto}
.modal-box h3{font-size:16px;margin-bottom:12px}
.modal-box .field{display:flex;flex-direction:column;gap:4px;margin-bottom:12px}
.modal-box .field label{font-size:12px;color:var(--muted)}
.modal-box .field input,.modal-box .field select,.modal-box .field textarea{padding:7px 10px;background:var(--bg3);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;outline:none;width:100%}
.modal-box .field input:focus{border-color:var(--cross)}
.modal-actions{display:flex;gap:8px;margin-top:16px;justify-content:flex-end}

/* Login */
.login-msg{color:var(--red);font-size:12px;margin-bottom:8px;display:none}
.login-msg.visible{display:block}
.login-header{display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:16px}
.login-header .lock{font-size:32px}

/* Toast */
.toast{position:fixed;top:20px;right:20px;padding:12px 18px;border-radius:8px;font-size:13px;z-index:2000;max-width:360px;animation:fadeIn .2s;display:none}
.toast.success{background:#0d2b1a;border:1px solid var(--green);color:var(--green);display:block}
.toast.error{background:#2b0d0d;border:1px solid var(--red);color:var(--red);display:block}
.toast.info{background:#0d1f3a;border:1px solid var(--agent3);color:var(--agent3);display:block}
@keyframes fadeIn{from{opacity:0;transform:translateY(-10px)}to{opacity:1;transform:translateY(0)}}

.header{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;flex-wrap:wrap;gap:10px}
.header h1{font-size:22px;font-weight:700}
.header-right{display:flex;gap:8px;align-items:center}
.btn{padding:7px 14px;background:var(--bg3);border:1px solid var(--border);border-radius:7px;color:var(--text);cursor:pointer;font-size:12px;transition:all .15s;display:flex;align-items:center;gap:6px}
.btn:hover{background:var(--hover);border-color:var(--muted)}
.btn-accent{border-color:var(--cross);color:var(--cross)}
.btn-accent:hover{background:var(--cross-bg)}
.btn-admin{border-color:var(--green);color:var(--green)}
.btn-admin:hover{background:#0d2b1a}
.subtitle{color:var(--muted);font-size:12px;margin-bottom:18px}
.subtitle .live{color:var(--green);font-weight:600;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.wing-stats{display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap}
.wing-card{flex:1;min-width:120px;background:var(--bg2);border:1px solid var(--border);border-radius:10px;padding:14px 16px;transition:all .15s;cursor:pointer}
.wing-card:hover{background:var(--hover)}
.wing-card.active{border-color:var(--cross);box-shadow:0 0 0 1px var(--cross)}
.wing-card.cross-card{border-left:3px solid var(--cross)}
.wing-card.agent-card{border-left:3px solid var(--agent1)}
.wing-card.agent2-card{border-left:3px solid var(--agent2)}
.wing-card.agent3-card{border-left:3px solid var(--agent3)}
.wing-card .wing-name{font-size:13px;font-weight:600;display:flex;align-items:center;gap:6px}
.wing-card .wing-name .dot{width:8px;height:8px;border-radius:50%}
.wing-card .wing-name .dot.cross{background:var(--cross)}
.wing-card .wing-name .dot.a1{background:var(--agent1)}
.wing-card .wing-name .dot.a2{background:var(--agent2)}
.wing-card .wing-name .dot.a3{background:var(--agent3)}
.wing-card .wing-num{font-size:26px;font-weight:700;margin-top:4px;color:var(--text)}
.wing-card .wing-label{font-size:11px;color:var(--muted)}
.quick-stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;margin-bottom:18px}
.qs-card{background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 14px;text-align:center;cursor:pointer;transition:all .15s}
.qs-card:hover{background:var(--hover);border-color:var(--muted)}
.qs-card.active{border-color:var(--cross);box-shadow:0 0 0 1px var(--cross)}
.qs-card .num{font-size:24px;font-weight:700;color:var(--text)}
.qs-card .label{font-size:11px;color:var(--muted);margin-top:1px}
.qs-card .desc{font-size:9px;color:var(--muted);margin-top:3px;opacity:.7}
.toolbar{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap;align-items:center}
.search-box{flex:1;min-width:160px;padding:7px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;outline:none}
.search-box:focus{border-color:var(--cross)}
.search-box::placeholder{color:var(--muted)}
.select-box{padding:7px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:7px;color:var(--text);font-size:13px;cursor:pointer;outline:none}
.toolbar-info{color:var(--muted);font-size:12px;margin-left:auto;white-space:nowrap}
.table-wrap{border-radius:8px;border:1px solid var(--border);overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed}
th{text-align:left;padding:8px 8px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.5px;white-space:nowrap;cursor:pointer;user-select:none;background:var(--bg2);overflow:hidden;text-overflow:ellipsis}
th:hover{color:var(--text)}
th .sort-arrow{opacity:.3;font-size:10px}
th.sorted .sort-arrow{opacity:1;color:var(--cross)}
td{padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
tr:last-child td{border-bottom:none}
tr:hover td{background:var(--hover)}
.col-id{color:var(--muted);font-family:"JetBrains Mono","Cascadia Code",monospace;font-size:11px;width:80px}
.col-wing{width:80px}
.col-date{color:var(--muted);font-size:11px;width:85px}
.col-type{width:60px}
.col-source{width:70px}
.col-subject{width:70px}
.col-confidence{width:60px}
.col-room{width:65px}
.col-summary{width:130px}
.col-tags{width:90px}
.col-week{width:65px}
.col-statement{white-space:normal;word-break:break-word;line-height:1.3;overflow:hidden;max-height:3.6em}
tr.expanded .col-statement{max-height:none;white-space:pre-wrap}
tr{cursor:pointer;transition:background .12s}
.wing-badge{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:500;font-family:monospace}
.wing-badge.cross{background:var(--cross-bg);color:var(--cross)}
.wing-badge.agent{background:var(--agent1-bg);color:var(--agent1)}
.wing-badge .bdot{width:6px;height:6px;border-radius:50%}
.wing-badge.cross .bdot{background:var(--cross)}
.wing-badge.agent .bdot{background:var(--agent1)}
.tag{display:inline-block;padding:2px 6px;border-radius:5px;font-size:11px;margin:1px 2px;font-weight:500}
.tag-fact{background:#0d2b1a;color:var(--green)}
.tag-pref{background:#2b200d;color:var(--yellow)}
.tag-bound{background:#2b0d0d;color:var(--red)}
.tag-commit{background:#0d1a2b;color:var(--agent3)}
.tag-room{background:var(--border);color:var(--muted)}
.immutable-icon{color:var(--yellow);font-size:12px;cursor:help}
.agent-detail{font-size:11px;color:var(--muted);margin-bottom:14px;line-height:1.5}
.pagination{display:flex;justify-content:center;align-items:center;gap:6px;margin-top:16px;padding:12px 0}
.pagination button{padding:6px 12px;background:var(--bg2);border:1px solid var(--border);border-radius:7px;color:var(--text);cursor:pointer;font-size:13px}
.pagination button:hover:not(:disabled){background:var(--hover);border-color:var(--cross)}
.pagination button:disabled{opacity:.35;cursor:default}
.pagination .page-info{color:var(--muted);font-size:13px}
.pagination input{width:44px;padding:5px 6px;background:var(--bg2);border:1px solid var(--border);border-radius:7px;color:var(--text);text-align:center;font-size:13px}
.empty{text-align:center;padding:64px 16px;color:var(--muted)}
.empty .icon{font-size:42px;margin-bottom:10px;opacity:.5}
::-webkit-scrollbar{width:5px;height:5px}
::-webkit-scrollbar-track{background:transparent}
::-webkit-scrollbar-thumb{background:var(--border);border-radius:3px}
@media(max-width:768px){.sidebar{display:none}.main{margin-left:0;padding:16px}.wing-stats{flex-direction:column}}
</style>
</head>
<body>
<div class="app">
<aside class="sidebar">
  <h2>💾 灵魂盘</h2>
  <div class="agent-name" id="sidebarAgent">加载中...</div>
  <div class="agent-detail" id="sidebarDetail"></div>
  <div class="agent-switch" id="agentSwitch"></div>
  <div class="nav-label">数据分区</div>
  <div class="nav-item active" id="nav-overview" onclick="switchView('overview','all')">
    <span class="dot cross"></span>全部数据<span class="count" id="navCnt-all">-</span>
  </div>
  <div class="nav-item" id="nav-hall" onclick="switchView('hall','all')">
    <span class="dot cross"></span>厅堂 JSONL<span class="count" id="navCnt-hall">-</span>
  </div>
  <div class="nav-label">WING 分区</div>
  <div id="navWings"></div>
  <div class="nav-item" id="nav-vault" onclick="switchView('vault','all')">
    <span class="dot agent"></span>Vault 记忆<span class="count" id="navCnt-vault">-</span>
  </div>
  <div class="footer">更新于 <span id="navTime">-</span></div>
</aside>
<main class="main">
<div class="header">
  <h1>伴生灵魂盘 · 仪表盘</h1>
  <div class="header-right">
    <button class="btn btn-admin" id="loginBtn" onclick="showAdminModal()">🔑 管理</button>
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);cursor:pointer">
      <input type="checkbox" id="autoRefresh" onchange="toggleAutoRefresh()" style="accent-color:var(--cross)">自动刷新
    </label>
    <select class="select-box" id="refreshInterval" onchange="onIntervalChange()" style="min-width:70px">
      <option value="5">5s</option>
      <option value="15" selected>15s</option>
      <option value="30">30s</option>
      <option value="60">60s</option>
      <option value="300">5min</option>
    </select>
    <button class="btn btn-accent" id="refreshBtn" onclick="loadData()">🔄 刷新</button>
  </div>
</div>
<p class="subtitle" id="subtitle"><span class="live">● LIVE</span> 加载中...</p>
<div class="wing-stats" id="wingStats"></div>
<div class="quick-stats" id="quickStats"></div>
<div class="toolbar">
  <input class="search-box" id="search" type="text" placeholder="🔍 搜索..." oninput="onSearch()">
  <select class="select-box" id="pageSize" onchange="onSearch()">
    <option value="25">25</option><option value="50" selected>50</option><option value="100">100</option><option value="200">200</option>
  </select>
  <span class="toolbar-info" id="toolbarInfo"></span>
</div>
<div class="table-wrap">
<table id="dataTable"><thead id="tableHead"></thead><tbody id="tableBody"></tbody></table>
</div>
<div class="empty" id="emptyState" style="display:none"><div class="icon">📭</div><p>暂无数据</p></div>
<div class="pagination" id="pagination"></div>
</main>
</div>

<!-- Login modal -->
<div class="modal-overlay" id="loginModal">
<div class="modal-box">
  <div class="login-header"><span class="lock">🔐</span><h3>管理员登录</h3></div>
  <div class="login-msg" id="loginMsg">密码错误</div>
  <div class="field">
      <label>管理员密码</label>
      <input type="password" id="loginPw" onkeydown="if(event.key==='Enter')doLogin()" autofocus>
    </div>
    <div id="pwDisplay" style="display:none;background:var(--bg3);border:1px solid var(--cross);border-radius:7px;padding:10px 12px;margin-top:4px">
      <div style="color:var(--cross);font-size:11px;margin-bottom:4px">🔑 初始密码（首次使用）</div>
      <div style="font-family:monospace;font-size:18px;font-weight:700;color:var(--text);letter-spacing:1px" id="pwText"></div>
      <div style="color:var(--muted);font-size:10px;margin-top:4px">修改密码后此提示消失，请尽快设置您自己的密码</div>
    </div>
    <p style="color:var(--muted);font-size:11px;margin-top:4px;line-height:1.4">
      登录后可管理 AI 身份和数据。
    </p>
  <div class="modal-actions">
    <button class="btn" onclick="closeModal('loginModal')">取消</button>
    <button class="btn btn-accent" onclick="doLogin()">登录</button>
  </div>
</div>
</div>

<!-- Admin modal -->
<div class="modal-overlay" id="adminModal">
<div class="modal-box" style="max-width:560px;max-height:85vh;display:flex;flex-direction:column">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
    <h3 style="margin:0">🔐 管理面板</h3>
    <button class="btn" onclick="closeModal('adminModal')" style="font-size:12px">✕</button>
  </div>
  <div id="adminContent" style="overflow-y:auto;flex:1;min-height:0"></div>
</div>
</div>

<!-- Confirm modal -->
<div class="modal-overlay" id="confirmModal">
<div class="modal-box">
  <h3 id="confirmTitle">确认操作</h3>
  <p id="confirmText" style="color:var(--muted);font-size:13px;line-height:1.5"></p>
  <div class="modal-actions">
    <button class="btn" onclick="closeModal('confirmModal')">取消</button>
    <button class="btn danger" id="confirmBtn" onclick="">确认</button>
  </div>
</div>
</div>

<!-- Change password modal -->
<div class="modal-overlay" id="changePwModal">
<div class="modal-box">
  <div class="login-header"><span class="lock">🔑</span><h3>修改密码</h3></div>
  <div class="login-msg" id="pwMsg">错误</div>
  <div class="field">
    <label>当前密码</label>
    <input type="password" id="oldPw" style="width:100%">
  </div>
  <div class="field">
    <label>新密码</label>
    <input type="password" id="newPw" style="width:100%">
  </div>
  <div class="field">
    <label>再次输入新密码</label>
    <input type="password" id="newPw2" style="width:100%" onkeydown="if(event.key==='Enter')doChangePw()">
  </div>
  <div class="modal-actions">
    <button class="btn" onclick="closeModal('changePwModal')">取消</button>
    <button class="btn btn-accent" onclick="doChangePw()">确认修改</button>
  </div>
</div>
</div>

<!-- Toast -->
<div id="toast" class="toast"></div>

<script>
let ALL_DATA=null,currentView="overview",currentWingFilter="all",currentSort={field:null,asc:true},currentPage=1,filteredData=[],autoRefreshTimer=null,refreshCountdown=0,refreshMs=15000,isAdmin=false;
const WING_COLORS=["agent","agent2","agent3","agent","agent2","agent3"];
function wingClass(wing,idx){if(wing==="cross")return"cross";let i=typeof idx==="number"?idx:wingColorIdx(wing);return WING_COLORS[i%WING_COLORS.length]||"agent"}
function wingColorIdx(wing){let s=0;for(let i=0;i<wing.length;i++)s+=wing.charCodeAt(i);return s}

function toast(msg,type){
  let el=document.getElementById("toast");
  el.textContent=msg;el.className="toast "+type;
  clearTimeout(el._t);el._t=setTimeout(()=>{el.className="toast"},4000);
}

function showLogin(){
  document.getElementById("loginMsg").className="login-msg";
  document.getElementById("loginPw").value="";
  document.getElementById("loginModal").className="modal-overlay visible";
  // 显示初始密码（如果有）
  let pw=document.getElementById("pwDisplay"),txt=document.getElementById("pwText");
  if(ALL_DATA&&ALL_DATA.initial_password){
    pw.style.display="block";txt.textContent=ALL_DATA.initial_password;
  }else{pw.style.display="none"}
  setTimeout(()=>document.getElementById("loginPw").focus(),100);
}
function closeModal(id){document.getElementById(id).className="modal-overlay"}
function doLogin(){
  let pw=document.getElementById("loginPw").value;
  fetch("/api/admin/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({password:pw})})
  .then(r=>r.json()).then(d=>{
    if(d.success){
      closeModal("loginModal");isAdmin=true;
      document.getElementById("loginBtn").textContent="🔓 管理";
      document.getElementById("loginBtn").className="btn btn-accent";
      showAdminModal();
      toast("管理员已登录","success");
    }else{
      document.getElementById("loginMsg").className="login-msg visible";
      document.getElementById("loginMsg").textContent=d.error||"密码错误";
    }
  }).catch(e=>toast("登录失败: "+e,"error"));
}

function showChangePw(){
  document.getElementById("pwMsg").className="login-msg";
  document.getElementById("oldPw").value="";
  document.getElementById("newPw").value="";
  document.getElementById("newPw2").value="";
  document.getElementById("changePwModal").className="modal-overlay visible";
}

function doChangePw(){
  let oldPw=document.getElementById("oldPw").value;
  let newPw=document.getElementById("newPw").value;
  let newPw2=document.getElementById("newPw2").value;
  if(newPw!==newPw2){document.getElementById("pwMsg").className="login-msg visible";document.getElementById("pwMsg").textContent="两次新密码不一致";return}
  fetch("/api/admin/change-password",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({old_password:oldPw,new_password:newPw})})
  .then(r=>r.json()).then(d=>{
    if(d.success){closeModal("changePwModal");toast("✅ 密码已修改","success")}
    else{document.getElementById("pwMsg").className="login-msg visible";document.getElementById("pwMsg").textContent=d.error||"修改失败"}
  }).catch(e=>toast("修改失败: "+e,"error"));
}

function logout(){
  fetch("/api/admin/logout",{method:"POST"})
  .then(r=>r.json()).then(d=>{
    isAdmin=false;
    document.getElementById("loginBtn").textContent="🔑 管理";
    document.getElementById("loginBtn").className="btn btn-admin";
    closeModal("adminModal");
    toast("已退出登录","info");
  });
}

function showAdminModal(){
  if(!isAdmin){showLogin();return}
  document.getElementById("adminModal").className="modal-overlay visible";
  renderAdminPanel();
}

function checkAdmin(){
  fetch("/api/admin/agents").then(r=>r.json()).then(d=>{
    if(d.success){isAdmin=true;
      document.getElementById("loginBtn").textContent="🔓 管理";
      document.getElementById("loginBtn").className="btn btn-accent";
    }
  }).catch(()=>{});
}

function renderAdminPanel(){
  let html='<button class="admin-btn" onclick="adminReloadAgents()">🔄 刷新智能体列表</button>';
  html+='<div id="adminAgentList"></div>';
  html+='<button class="admin-btn primary" onclick="showChangePw()" style="margin-top:8px">🔑 修改密码</button>';
  html+='<button class="admin-btn danger" onclick="logout()" style="margin-top:4px">🚪 退出登录</button>';
  document.getElementById("adminContent").innerHTML=html;
  adminReloadAgents();
}

function adminReloadAgents(){
  fetch("/api/admin/agents").then(r=>r.json()).then(d=>{
    if(!d.success){toast(d.error||"获取失败","error");return}
    let html='';
    (d.agents||[]).forEach(a=>{
      let ownerTag=a.is_owner?' <span style="color:var(--muted);font-size:10px">(🔑 初始化角色)</span>':'';
      html+='<div class="admin-item" style="padding:6px 2px;font-size:12px;border-bottom:1px solid var(--border)">';
      html+='<div><strong>🤖 '+esc(a.name||'<未命名>')+'</strong> <span style="color:var(--muted)">('+esc(a.id)+')</span>'+ownerTag+'</div>';
      html+='<div style="display:flex;gap:4px;margin-top:4px">';
      html+='<button class="admin-btn danger" style="font-size:11px;padding:4px 8px;flex:1" onclick="confirmDelete(\''+esc(a.id)+'\',\''+esc(a.name||a.id)+'\')">🗑 删除</button>';
      html+='<button class="admin-btn success" style="font-size:11px;padding:4px 8px;flex:1" onclick="resetKey(\''+esc(a.id)+'\')">🔑 重置 Key</button>';
      html+='</div></div>';
    });
    document.getElementById("adminAgentList").innerHTML=html;
  });
}

function confirmDelete(aid,name){
  document.getElementById("confirmTitle").textContent='🗑 删除 AI: '+name;
  document.getElementById("confirmText").innerHTML='输入 <strong style="color:var(--red)">'+esc(name)+'</strong> 以确认删除：<br><br>'
    +'<input type="text" id="deleteConfirmInput" placeholder="输入 AI 名称确认删除" '
    +'style="width:100%;padding:8px 10px;background:var(--bg3);border:1px solid var(--red);border-radius:7px;color:var(--text);font-size:14px;outline:none" '
    +'onkeydown="if(event.key===\'Enter\')doDeleteChecked(\''+esc(aid)+'\',\''+esc(name)+'\')">'
    +'<br><br><span style="color:var(--red);font-size:12px">⚠ 此操作永久删除不可恢复</span>';
  document.getElementById("confirmBtn").onclick=function(){doDeleteChecked(aid,name)};
  document.getElementById("confirmBtn").textContent='确认删除';
  document.getElementById("confirmBtn").className='btn danger';
  document.getElementById("confirmModal").className="modal-overlay visible";
  setTimeout(()=>{let inp=document.getElementById("deleteConfirmInput");if(inp)inp.focus()},100);
}

function doDeleteChecked(aid,name){
  let inp=document.getElementById("deleteConfirmInput");
  if(!inp||inp.value.trim()!==name){
    inp.style.borderColor='var(--red)';inp.focus();return
  }
  closeModal("confirmModal");
  fetch("/api/admin/delete-agent",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agent_id:aid})})
  .then(r=>r.json()).then(d=>{
    if(d.success){toast("✅ "+d.message,"success");adminReloadAgents();loadData()}
    else toast("❌ "+d.error,"error");
  }).catch(e=>toast("请求失败: "+e,"error"));
}

function resetKey(aid){
  document.getElementById("confirmTitle").textContent='🔑 重置 Write Key: '+aid;
  document.getElementById("confirmText").textContent='确认重置 '+aid+' 的 write_key？旧的 key 将失效，需要重新通知该 AI 保存新 key。';
  document.getElementById("confirmBtn").onclick=function(){
    closeModal("confirmModal");
    fetch("/api/admin/reset-key",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agent_id:aid})})
    .then(r=>r.json()).then(d=>{
      if(d.success){
        toast("✅ 新 key: "+d.write_key+"<br>请立即保存！","success");
        adminReloadAgents();
      }else toast("❌ "+d.error,"error");
    }).catch(e=>toast("请求失败: "+e,"error"));
  };
  document.getElementById("confirmModal").className="modal-overlay visible";
}

async function loadData(){
  let cur=new URLSearchParams(location.search).get("agent")||"";
  try{
    let r=await fetch("/api/data?"+Date.now()+(cur?"&agent="+encodeURIComponent(cur):""));
    if(!r.ok) throw new Error("HTTP "+r.status);
    ALL_DATA=await r.json();
    refreshCountdown=Math.ceil(refreshMs/1000);
    updateSubtitle();
    renderIdentity();renderAgentSwitch();
    document.getElementById("navTime").textContent=ALL_DATA.gen_time;
    renderWingStats();renderQuickStats();renderNav();
    refreshCountdown=Math.ceil(refreshMs/1000);let sy=window.scrollY;applyFilters();window.scrollTo(0,sy);
  }catch(e){
    console.error('loadData error:', e);
    document.getElementById("subtitle").innerHTML='<span style="color:var(--red)">⚠ 连接失败 · 自动重试中</span>';
  }
}
function updateSubtitle(){
  let live='<span class="live">● LIVE</span>';
  let state=autoRefreshTimer?('实时动态 · 自动刷新 <span id="cd">'+refreshCountdown+'</span>s'):'实时动态 · 手动刷新';
  document.getElementById("subtitle").innerHTML=live+' '+state+' · '+esc(ALL_DATA.config.agent_name||"未命名")+" · "+esc(ALL_DATA.config.user_name||"");
}
function tickCountdown(){if(!autoRefreshTimer)return;refreshCountdown--;if(refreshCountdown<=0){loadData();return;}let el=document.getElementById("cd");if(el)el.textContent=refreshCountdown;}
function startAutoRefresh(){
  clearInterval(autoRefreshTimer);
  refreshMs=parseInt(document.getElementById("refreshInterval").value)*1000;
  refreshCountdown=Math.ceil(refreshMs/1000);
  autoRefreshTimer=setInterval(tickCountdown,1000);
  updateSubtitle();
}
function stopAutoRefresh(){clearInterval(autoRefreshTimer);autoRefreshTimer=null;refreshCountdown=0;updateSubtitle();}
function toggleAutoRefresh(){if(document.getElementById("autoRefresh").checked){startAutoRefresh();localStorage.setItem("soul_ar","1");}else{stopAutoRefresh();localStorage.setItem("soul_ar","0");}}
function onIntervalChange(){if(document.getElementById("autoRefresh").checked)startAutoRefresh();localStorage.setItem("soul_ri",document.getElementById("refreshInterval").value);}
function getWingList(){let wings={};[...ALL_DATA.vault_active,...ALL_DATA.vault_archive].forEach(r=>{let w=r.wing||"unknown";wings[w]=(wings[w]||0)+1});return Object.entries(wings).sort((a,b)=>a[0]==="cross"?-1:b[0]==="cross"?1:a[0].localeCompare(b[0]))}
function renderWingStats(){
  let wings=getWingList(),html="";
  let crossCnt=wings.find(w=>w[0]==="cross");crossCnt=crossCnt?crossCnt[1]:0;
  html+='<div class="wing-card cross-card'+(currentWingFilter==="cross"?" active":"")+'" onclick="switchView(\'vault\',\'cross\')">';
  html+='<div class="wing-name"><span class="dot cross"></span>🌐 公共区 cross</div>';
  html+='<div class="wing-num">'+crossCnt+'</div><div class="wing-label">Vault · 全员共享</div></div>';
  wings.filter(w=>w[0]!=="cross").forEach((w,i)=>{let cls=wingClass(w[0],i);
    html+='<div class="wing-card agent-card'+(currentWingFilter===w[0]?" active":"")+'" onclick="switchView(\'vault\',\''+w[0]+'\')">';
    html+='<div class="wing-name"><span class="dot '+cls+'"></span>🔒 '+esc(w[0])+'</div>';
    html+='<div class="wing-num">'+w[1]+'</div><div class="wing-label">Vault · 私密</div></div>'});
  document.getElementById("wingStats").innerHTML=html||'<div style="color:var(--muted);padding:8px">暂无 Vault 数据</div>';
}
function renderQuickStats(){
  let s=ALL_DATA.vault_status;
  document.getElementById("quickStats").innerHTML=[
    {n:ALL_DATA.total_records,l:"厅堂",d:"JSONL永久记忆",k:"hall"},
    {n:ALL_DATA.FACT.length,l:"FACT",d:"事实/知识/经验",k:"FACT"},
    {n:ALL_DATA.PREF.length,l:"PREF",d:"用户偏好与习惯",k:"PREF"},
    {n:ALL_DATA.BOUND.length,l:"BOUND",d:"行为边界约定",k:"BOUND"},
    {n:ALL_DATA.COMMIT.length,l:"COMMIT",d:"承诺与锚定",k:"COMMIT"},
    {n:s.active_count,l:"走廊",d:"近期预载区(active)",k:"vault_active"},
    {n:s.archive_count,l:"房间",d:"历史归档区(archive)",k:"vault_archive"},
    {n:s.roll_threshold,l:"阈值",d:"滚动归档触发条件",k:null}
  ].map(c=>'<div class="qs-card'+(currentTypeFilter===c.k?' active':'')+'" onclick="filterByType(\''+c.k+'\',\''+c.l+'\')"><div class="num">'+c.n+'</div><div class="label">'+c.l+'</div><div class="desc">'+c.d+'</div></div>').join("");
}
let currentTypeFilter=null;
function filterByType(key,label){
  let cards=document.querySelectorAll(".qs-card");
  if(currentTypeFilter===key){currentTypeFilter=null;cards.forEach(c=>c.classList.remove("active"));prepareData();renderTable();return;}
  currentTypeFilter=key;
  currentWingFilter="all";
  if(key==="FACT"||key==="PREF"||key==="BOUND"||key==="COMMIT"||key==="hall")currentView="overview";
  else if(key==="vault_active"||key==="vault_archive")currentView="vault";
  cards.forEach(c=>{c.classList.remove("active");if(c.querySelector(".label").textContent===label)c.classList.add("active")});
  document.querySelectorAll(".wing-card").forEach(c=>c.classList.remove("active"));
  renderWingStats();renderNav();
  prepareData();renderTable();
}
function filterByWing(wing){
  if(currentWingFilter===wing){currentWingFilter="all";currentView="overview";currentTypeFilter=null;renderWingStats();renderNav();prepareData();renderTable();return;}
  currentWingFilter=wing;
  currentTypeFilter=null;
  currentView="vault";
  document.querySelectorAll(".qs-card").forEach(c=>c.classList.remove("active"));
  renderWingStats();renderNav();
  prepareData();renderTable();
}
function renderNav(){
  let wings=getWingList();
  document.getElementById("navCnt-all").textContent=ALL_DATA.total_records+ALL_DATA.vault_active.length+ALL_DATA.vault_archive.length;
  document.getElementById("navCnt-hall").textContent=ALL_DATA.total_records;
  document.getElementById("navCnt-vault").textContent=ALL_DATA.vault_active.length+ALL_DATA.vault_archive.length;
  document.querySelectorAll(".sidebar .nav-item").forEach(el=>el.classList.remove("active"));
  let wh="";wings.forEach((w,i)=>{let cls=wingClass(w[0],i),isActive=currentWingFilter===w[0];
    wh+='<div class="nav-item'+(isActive?" active":"")+'" onclick="filterByWing(\''+w[0]+'\')">';
    wh+='<span class="dot '+cls+'"></span>'+esc(w[0])+'<span class="count">'+w[1]+'</span></div>'});
  document.getElementById("navWings").innerHTML=wh;
  if(currentWingFilter==="all"){
    let navId=currentView==="hall"?"nav-hall":currentView==="vault"?"nav-vault":"nav-overview";
    document.getElementById(navId).classList.add("active");
  }
}
function switchView(view,wing){currentView=view;currentWingFilter=wing||"all";currentPage=1;currentSort={field:null,asc:true};currentTypeFilter=null;document.querySelectorAll(".qs-card").forEach(c=>c.classList.remove("active"));document.querySelectorAll(".wing-card").forEach(c=>c.classList.remove("active"));renderWingStats();renderNav();prepareData();renderTable()}
function prepareData(){
  filteredData=[];
  let hall=[...ALL_DATA.FACT.map(r=>({...r,_source:"FACT"})),...ALL_DATA.PREF.map(r=>({...r,_source:"PREF"})),...ALL_DATA.BOUND.map(r=>({...r,_source:"BOUND"})),...ALL_DATA.COMMIT.map(r=>({...r,_source:"COMMIT"}))];
  let vault=[...ALL_DATA.vault_active.map(r=>({...r,_source:"vault_active"})),...ALL_DATA.vault_archive.map(r=>({...r,_source:"vault_archive"}))];
  if(currentWingFilter!=="all")vault=vault.filter(r=>r.wing===currentWingFilter);
  if(currentView==="hall"){filteredData=hall}
  else if(currentView==="vault"){filteredData=vault}
  else{filteredData=[...hall,...vault]}
  if(currentTypeFilter){
    if(currentTypeFilter==="hall")filteredData=filteredData.filter(r=>["FACT","PREF","BOUND","COMMIT"].includes(r._source));
    else if(currentTypeFilter==="vault_active")filteredData=filteredData.filter(r=>r._source==="vault_active");
    else if(currentTypeFilter==="vault_archive")filteredData=filteredData.filter(r=>r._source==="vault_archive");
    else filteredData=filteredData.filter(r=>r._source===currentTypeFilter);
  }
}
function getColumns(){
  let hasVault=filteredData.some(r=>r._source==="vault_active"||r._source==="vault_archive");
  let hasHall=filteredData.some(r=>r._source==="FACT"||r._source==="PREF"||r._source==="BOUND"||r._source==="COMMIT");
  if(hasVault&&!hasHall)return[{k:"id",l:"ID",s:true,c:"col-id"},{k:"wing",l:"Wing",s:true,c:"col-wing"},{k:"room",l:"Room",s:true,c:"col-room"},{k:"content",l:"内容",s:false,c:"col-statement"},{k:"summary",l:"摘要",s:false,c:"col-summary"},{k:"tags",l:"标签",s:false,c:"col-tags"},{k:"created_week",l:"周",s:true,c:"col-week"},{k:"created_at",l:"时间",s:true,c:"col-date"}];
  return[{k:"id",l:"ID",s:true,c:"col-id"},{k:"type",l:"类型",s:true,c:"col-type"},{k:"subject",l:"主体",s:true,c:"col-subject"},{k:"statement",l:"内容",s:false,c:"col-statement"},{k:"source",l:"出处",s:false},{k:"confidence",l:"置信度",s:true,c:"col-confidence"},{k:"created",l:"时间",s:true,c:"col-date"}];
}
function onSearch(){currentPage=1;applyFilters()}
function applyFilters(){prepareData();let q=document.getElementById("search").value.toLowerCase().trim();if(q)filteredData=filteredData.filter(r=>Object.values(r).some(v=>v!=null&&String(v).toLowerCase().includes(q)));if(currentSort.field)sortData();renderTable()}
function setSort(field){if(currentSort.field===field)currentSort.asc=!currentSort.asc;else{currentSort.field=field;currentSort.asc=true}applyFilters()}
function sortData(){let f=currentSort.field,a=currentSort.asc;filteredData.sort((x,y)=>{let va=x[f],vb=y[f];if(va==null)va="";if(vb==null)vb="";if(typeof va==="number"&&typeof vb==="number")return a?va-vb:vb-va;return a?String(va).localeCompare(String(vb),"zh-CN"):String(vb).localeCompare(String(va),"zh-CN")})}
function renderTable(){
  let ps=parseInt(document.getElementById("pageSize").value),tp=Math.max(1,Math.ceil(filteredData.length/ps));
  if(currentPage>tp)currentPage=tp;
  let start=(currentPage-1)*ps,end=Math.min(start+ps,filteredData.length),pageData=filteredData.slice(start,end);
  let cols=getColumns();
  document.getElementById("tableHead").innerHTML="<tr>"+cols.map(c=>{let s=currentSort.field===c.k?" sorted":"",a=s?(currentSort.asc?" ▴":" ▾"):"";let cls=c.c?c.c+' '+s:s;return'<th class="'+cls+'" onclick="'+(c.s?'setSort(\''+c.k+'\')':'')+'" style="'+(c.s?'':'cursor:default')+'\">'+c.l+'<span class="sort-arrow">'+a+'</span></th>'}).join("")+"</tr>";
  if(pageData.length===0){document.getElementById("tableBody").innerHTML="";document.getElementById("emptyState").style.display="block";document.getElementById("pagination").innerHTML="";document.getElementById("toolbarInfo").textContent="";document.getElementById("dataTable").style.display="none";return}
  document.getElementById("emptyState").style.display="none";document.getElementById("dataTable").style.display="";
  let hasVault=filteredData.some(r=>r._source==="vault_active"||r._source==="vault_archive"),hasHall=filteredData.some(r=>r._source==="FACT"||r._source==="PREF"||r._source==="BOUND"||r._source==="COMMIT"),isVault=hasVault&&!hasHall;
  document.getElementById("tableBody").innerHTML=pageData.map(r=>{
    if(isVault){let w=r.wing,wc=wingClass(w);
      let fullContent=attr(r.content||""),fullSummary=attr(r.summary||"");
      let tags=parseTags(r.tags)||[],tagText=attr(tags.join(", "));return'<tr onclick="toggleExpandRow(this)"><td class="col-id" title="'+attr(r.id)+'">'+esc(r.id)+'</td><td class="col-wing" title="'+attr(w)+'"><span class="wing-badge '+wc+'"><span class="bdot"></span>'+esc(w)+'</span></td><td class="col-room" title="'+attr(r.room)+'"><span class="tag tag-room">'+esc(r.room)+'</span></td><td class="col-statement col-content" data-full="'+fullContent+'" title="'+fullContent+'">'+esc(trunc(r.content,60))+'</td><td class="col-statement col-summary" data-full="'+fullSummary+'" title="'+fullSummary+'" style="color:var(--muted)">'+esc(trunc(r.summary,60))+'</td><td class="col-tags" title="'+tagText+'">'+(parseTags(r.tags)||[]).map(t=>'<span class="tag">'+esc(t)+'</span>').join("")+'</td><td class="col-week" title="'+attr(r.created_week)+'">'+esc(r.created_week)+'</td><td class="col-date" title="'+attr(r.created_at)+'">'+esc(r.created_at)+'</td></tr>';}
    let type=r._source||r.type||"FACT",tc=type==="FACT"?"tag-fact":type==="PREF"?"tag-pref":type==="BOUND"?"tag-bound":type==="COMMIT"?"tag-commit":"tag-room";
    let fullStatement=attr(r.statement||r.content||""),fullSource=attr(r.source||"");
    let subjectText=attr(r.subject||r.wing||""), subjectHtml=esc(r.subject||r.wing||"");let lock=r.immutable?' <span class="immutable-icon" title="不可变记录">🔒</span>':'';return'<tr onclick="toggleExpandRow(this)"><td class="col-id" title="'+attr(r.id)+'">'+esc(r.id)+'</td><td class="col-type" title="'+attr(r.type||type)+'"><span class="tag '+tc+'">'+esc(r.type||type)+'</span></td><td class="col-subject" data-full="'+subjectText+'" title="'+subjectText+'">'+subjectHtml+lock+'</td><td class="col-statement" data-full="'+fullStatement+'" title="'+fullStatement+'">'+esc(trunc(r.statement||r.content||"",80))+'</td><td class="col-statement" data-full="'+fullSource+'" title="'+fullSource+'" style="color:var(--muted);font-size:11px;line-height:1.35;word-break:break-word">'+esc(trunc(r.source||"",70))+'</td><td class="col-confidence" title="'+attr(((r.confidence!=null?r.confidence:0)*100).toFixed(0)+"%")+'">'+((r.confidence!=null?r.confidence:0)*100).toFixed(0)+'%</td><td class="col-date" title="'+attr(r.created||r.established_at||r.created_at||'')+'">'+esc(r.created||r.established_at||r.created_at||'')+'</td></tr>';
  }).join("");
  document.getElementById("toolbarInfo").textContent="共 "+filteredData.length+" 条 / "+(start+1)+"-"+end+" / 第 "+currentPage+" 页";
  let ph="";ph+='<button onclick="goPage(1)"'+(currentPage===1?" disabled":"")+'>⏮</button>';ph+='<button onclick="goPage('+(currentPage-1)+')"'+(currentPage===1?" disabled":"")+'>◀</button>';ph+='<span class="page-info"><input type="number" value="'+currentPage+'" min="1" max="'+tp+'" onchange="goPage(parseInt(this.value)||1)"> / '+tp+'</span>';ph+='<button onclick="goPage('+(currentPage+1)+')"'+(currentPage===tp?" disabled":"")+'>▶</button>';ph+='<button onclick="goPage('+tp+')"'+(currentPage===tp?" disabled":"")+'>⏭</button>';
  document.getElementById("pagination").innerHTML=ph;
}
function goPage(n){let ps=parseInt(document.getElementById("pageSize").value),tp=Math.max(1,Math.ceil(filteredData.length/ps));currentPage=Math.max(1,Math.min(n,tp));renderTable()}
function esc(s){if(s==null)return"";let d=document.createElement("div");d.textContent=String(s);return d.innerHTML}
function attr(s){if(s==null)return"";return esc(s).replace(/"/g,'&quot;').replace(/'/g,'&#39;')}
function trunc(s,n){if(!s)return"";return s.length>n?s.substring(0,n)+"…":s}
function toggleExpandRow(tr){
  let was=tr.classList.contains("expanded");
  if(!was){
    // 展开：用 data-full 替换截断内容
    let cells=tr.querySelectorAll("[data-full]");
    cells.forEach(td=>{
      if(!td._saved)td._saved=td.innerHTML;
      td.innerHTML=td.dataset.full;
      td.style.maxHeight="none";td.style.whiteSpace="pre-wrap";
    });
  }else{
    // 收起：恢复截断内容
    let cells=tr.querySelectorAll("[data-full]");
    cells.forEach(td=>{
      if(td._saved)td.innerHTML=td._saved;
      td.style.maxHeight="3.6em";td.style.whiteSpace="normal";
    });
  }
  tr.classList.toggle("expanded");
}
function parseTags(t){try{return JSON.parse(t)}catch(e){return[]}}
function renderIdentity(){let c=ALL_DATA.config||{};document.getElementById("sidebarAgent").textContent=(c.agent_name||"未命名")+" · "+(c.agent_id||"无ID");document.getElementById("sidebarDetail").innerHTML=(c.user_name?'<div>👤 用户：'+esc(c.user_name)+'</div>':'')+(c.relationship?'<div>💞 关系：'+esc(c.relationship)+'</div>':'')+(c.tone?'<div>🎯 语气：'+esc(c.tone)+'</div>':'');}
function renderAgentSwitch(){let box=document.getElementById("agentSwitch");if(!box)return;let list=ALL_DATA.agents_list||[];let cur=new URLSearchParams(location.search).get("agent")||"";let html='<select onchange="switchAgent(this.value)"><option value="">· 全部 ·</option>';list.forEach(a=>{html+='<option value="'+esc(a)+'"'+(a===cur?' selected':'')+'>'+esc(a)+'</option>'});html+='</select>';box.innerHTML=html}
function switchAgent(v){location.href=v?('?agent='+encodeURIComponent(v)):('./');}
(function restoreAR(){try{let ar=localStorage.getItem("soul_ar");let ri=localStorage.getItem("soul_ri");if(ri)document.getElementById("refreshInterval").value=ri;if(ar==="1"){document.getElementById("autoRefresh").checked=true;startAutoRefresh();}}catch(e){}})();
checkAdmin();
// 首屏立即基于服务端注入的 ALL_DATA 渲染，不依赖 fetch（若 fetch 失败首屏仍有内容）
try {
  if (ALL_DATA) {
    refreshCountdown = Math.ceil(refreshMs / 1000);
    renderIdentity(); renderAgentSwitch();
    document.getElementById("navTime").textContent = ALL_DATA.gen_time;
    renderWingStats(); renderQuickStats(); renderNav();
    updateSubtitle();
    prepareData(); renderTable();
  }
} catch(e) { console.error('initial render error:', e); }
// 再异步获取最新数据（兼自动刷新）
loadData();
</script>
</body>
</html>"""

# ---------- HTTP ----------

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, f, *a): pass

    def _admin_required(self):
        cookie = self.headers.get("Cookie", "")
        if not _is_authenticated(cookie):
            self._json({"success": False, "error": "未登录"}, 401)
            return False
        return True

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path
        q = parse_qs(parsed.query)
        agent = q.get("agent", [None])[0]
        if p in ("/", "/index.html"):
            self._html(agent)
        elif p == "/api/data":
            self._api(agent)
        elif p == "/api/admin/agents":
            if not self._admin_required():
                return
            self._admin_agents()
        else:
            self.send_response(404)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Not Found")

    def do_POST(self):
        parsed = urlparse(self.path)
        p = parsed.path
        cl = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(cl) if cl else b"{}"
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = {}

        if p == "/api/admin/login":
            self._admin_login(data)
        elif p == "/api/admin/logout":
            self._admin_logout()
        elif p == "/api/admin/delete-agent":
            if not self._admin_required():
                return
            self._admin_delete(data)
        elif p == "/api/admin/reset-key":
            if not self._admin_required():
                return
            self._admin_reset_key(data)
        elif p == "/api/admin/change-password":
            if not self._admin_required():
                return
            self._admin_change_password(data)
        else:
            self._json({"success": False, "error": "未知接口"}, 404)

    def _html(self, agent=None):
        data = json.dumps(gather_all_data(agent), ensure_ascii=False, default=str)
        html = HTML_PAGE.replace("let ALL_DATA=null,", "let ALL_DATA=" + data + ",")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _api(self, agent=None):
        data = gather_all_data(agent)
        self._json(data)

    def _json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    # ---------- 管理 API ----------

    def _admin_login(self, data):
        pw = data.get("password", "")
        stored = _get_admin_password_hash()
        if not stored:
            self._json({"success": False, "error": "未设置管理员密码，请重启服务以生成初始密码。"})
            return
        if _hash_password(pw) != stored:
            self._json({"success": False, "error": "密码错误"})
            return
        token = _gen_session()
        _sessions[token] = datetime.datetime.now().isoformat()
        # 注意：不在登录时清除初始密码——初始密码应持续展示直到用户"主动修改密码"
        # （见 _admin_change_password）。仅"登录成功"不代表用户已设置自己的密码。
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie",
            f"soul_admin={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=86400")
        self.end_headers()
        self.wfile.write(json.dumps({"success": True}).encode("utf-8"))

    def _admin_logout(self):
        cookie = self.headers.get("Cookie", "")
        if cookie:
            for kv in cookie.split(";"):
                kv = kv.strip()
                if kv.startswith("soul_admin="):
                    token = kv.split("=", 1)[1].strip()
                    _sessions.pop(token, None)
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Set-Cookie",
            "soul_admin=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0")
        self.end_headers()
        self.wfile.write(json.dumps({"success": True}).encode("utf-8"))

    def _admin_agents(self):
        cfg = _load_config()
        agents = cfg.get("agents", {}) or {}
        owner_id = (cfg.get("agent_id") or "").strip()
        agent_list = []
        for aid, entry in agents.items():
            agent_list.append({
                "id": aid,
                "name": entry.get("agent_name", "") or "<未命名>",
                "user": entry.get("user_name", "") or "<未设>",
                "relationship": entry.get("relationship", "") or "<未设>",
                "is_owner": (aid == owner_id),
                "has_write_key": bool(entry.get("write_key", "")),
            })
        # 如果主人不在 agents 表中，补一个
        if owner_id and owner_id not in agents:
            agent_list.insert(0, {
                "id": owner_id,
                "name": cfg.get("agent_name", "") or "<未命名>",
                "user": cfg.get("user_name", "") or "<未设>",
                "relationship": cfg.get("relationship", "") or "<未设>",
                "is_owner": True,
                "has_write_key": bool(cfg.get("write_key", "")),
            })
        self._json({"success": True, "agents": agent_list})

    def _admin_delete(self, data):
        aid = (data.get("agent_id") or "").strip()
        if not aid:
            self._json({"success": False, "error": "缺少 agent_id"})
            return
        ok, msg = _delete_agent(aid)
        self._json({"success": ok, "error" if not ok else "message": msg})

    def _admin_reset_key(self, data):
        aid = (data.get("agent_id") or "").strip()
        if not aid:
            self._json({"success": False, "error": "缺少 agent_id"})
            return
        wk, msg = _reset_write_key(aid)
        if wk:
            self._json({"success": True, "write_key": wk, "message": msg})
        else:
            self._json({"success": False, "error": msg})

    def _admin_change_password(self, data):
        old_pw = data.get("old_password", "")
        new_pw = data.get("new_password", "")
        if not new_pw or len(new_pw) < 4:
            self._json({"success": False, "error": "新密码至少 4 位"})
            return
        stored = _get_admin_password_hash()
        if not stored:
            self._json({"success": False, "error": "未设置密码，无法修改"})
            return
        if _hash_password(old_pw) != stored:
            self._json({"success": False, "error": "旧密码错误"})
            return
        _set_admin_password_hash(_hash_password(new_pw))
        # 用户已主动设置自己的密码 → 标记 changed + 清除持久化的初始密码明文，登录口不再展示
        global _initial_password_plain
        _initial_password_plain = None
        cfg = _load_config()
        cfg["admin_password_changed"] = True
        cfg.pop("_initial_password_plain", None)
        _save_config(cfg)
        self._json({"success": True, "message": "密码已修改"})


def main():
    parser = argparse.ArgumentParser(description="灵魂盘实时仪表盘")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--port", "-p", type=int, default=8877)
    parser.add_argument("--export", action="store_true")
    parser.add_argument("--output", "-o")
    parser.add_argument("--agent")
    parser.add_argument("--reset-admin", action="store_true",
                        help="重置管理员密码：清空并重新生成初始密码，不影响任何记忆数据。")
    args = parser.parse_args()

    # 重置管理员密码（忘记密码时）：清空密码字段后重新生成，不碰记忆数据
    if args.reset_admin:
        cfg = _load_config()
        for k in ("admin_password", "_initial_password_plain", "admin_password_changed"):
            cfg.pop(k, None)
        _save_config(cfg)
        new_pw = _ensure_admin_password()
        border = "!" * 54
        print(f"\n{border}")
        print(f"   🔑 管理员密码已重置")
        print(f"")
        print(f"    新初始密码：{new_pw}")
        print(f"")
        print(f"    重启仪表盘后用此密码登录，建议立即在面板里修改。")
        print(f"    记忆数据未受影响。")
        print(f"{border}\n")
        return

    # 首次启动：生成管理员密码
    initial_pw = _ensure_admin_password()
    if initial_pw:
        border = "!" * 54
        print(f"\n{border}")
        print(f"   ⚠️  首次启动 · 管理员密码")
        print(f"")
        print(f"    🔑 密码：{initial_pw}")
        print(f"")
        print(f"    登录后请在面板中修改密码。")
        print(f"    此消息仅显示一次。")
        print(f"{border}\n")

    if args.export:
        op = args.output or os.path.join(ROOT, "data", "dashboard.html")
        os.makedirs(os.path.dirname(op), exist_ok=True)
        data = json.dumps(gather_all_data(args.agent), ensure_ascii=False, default=str)
        html = HTML_PAGE.replace("let ALL_DATA=null,", "let ALL_DATA=" + data + ",")
        html = html.replace("async function loadData(){", "async function loadData(){return;")
        live_link = "http://127.0.0.1:8877/" + (("?agent=" + args.agent) if args.agent else "")
        html = html.replace("loadData();",
            "ALL_DATA=" + data + ";"
            "renderIdentity();renderAgentSwitch();prepareData();renderTable();"
            "try{var af=document.getElementById('autoRefresh');if(af){af.disabled=true;af.checked=false;}}catch(e){}")
        with open(op, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"仪表盘已导出到 {op}")
        print(f"实时版请访问 {live_link}")
        return

    server = HTTPServer(("127.0.0.1", args.port), DashboardHandler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"❤️ 灵魂盘仪表盘启动 → {url}")
    if not args.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
