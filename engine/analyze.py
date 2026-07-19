#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
analyze.py — 伴生灵魂盘（Soul Framework） · 心理分析辅助（去数据化通用版）

职责：从一段对话文本中，抽取轻量信号，辅助 AI 做内部心理分析。
      输出结构化草稿，供 AI 判断是否写回 data/（内部日志，不对外贴标签）。

注意：这是启发式辅助，不是诊断。结果标低置信，交由 AI 判断。
      严禁本模块直接输出"你是XX症"类标签给用户。

用法：
    from engine.analyze import analyze
    sig = analyze("用户说：今天又熬夜了，累得不想动")
    print(sig)
"""

import re

# 轻量情绪词典（出厂通用，不含具体用户）
NEG = ["累", "烦", "焦虑", "难受", "孤独", "无聊", "压力", "崩溃", "想死", "不想活", "没意思", "空虚"]
POS = ["开心", "爽", "高兴", "满足", "放松", "爱", "喜欢", "期待", "稳"]
TIRED = ["熬夜", "累", "困", "没睡", "失眠", "疲惫"]
RISK = ["想死", "不想活", "轻生", "活不下去", "没意思活", "结束自己"]

# 行为/习惯暗示
HABIT_PATTERNS = {
    "深夜活跃": r"(半夜|凌晨|深夜|两点|三点|四点).*(在线|还没睡|干活|聊)",
    "偏好纯文本": r"(纯文本|别用表格|不要用表|markdown就行)",
    "直接风格": r"(直接说|别绕|说人话|简单点)",
}


def analyze(text):
    """输入用户文本，返回信号 dict。"""
    sig = {
        "emotion": "neutral",
        "emotion_score": 0.0,
        "tired": False,
        "risk_flag": False,
        "habits": [],
        "notes": [],
    }
    t = text or ""

    neg_hits = [w for w in NEG if w in t]
    pos_hits = [w for w in POS if w in t]
    if neg_hits:
        sig["emotion"] = "negative"
        sig["emotion_score"] = min(1.0, 0.3 + 0.15 * len(neg_hits))
        sig["notes"].append(f"负面情绪词：{neg_hits}")
    elif pos_hits:
        sig["emotion"] = "positive"
        sig["emotion_score"] = min(1.0, 0.3 + 0.15 * len(pos_hits))
        sig["notes"].append(f"正面情绪词：{pos_hits}")

    if any(w in t for w in TIRED):
        sig["tired"] = True
        sig["notes"].append("含疲惫/熬夜信号")

    if any(w in t for w in RISK):
        sig["risk_flag"] = True
        sig["notes"].append("⚠️ 风险信号，走 boundaries.md §稳妥响应")

    for name, pat in HABIT_PATTERNS.items():
        if re.search(pat, t):
            sig["habits"].append(name)
            sig["notes"].append(f"习惯暗示：{name}")

    return sig


if __name__ == "__main__":
    sample = "凌晨三点还在干活，累得不想动，今天又没睡好"
    print(analyze(sample))
