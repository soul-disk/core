#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
minimal.py — 伴生灵魂盘（Soul Framework） · 最小启动示例

演示一个 AI 智能体如何"不用训练"就接入框架：
  1. 对话前加载 context_pack 注入 system prompt
  2. 模拟一轮对话，AI 识别到值得记的内容
  3. 写回 data/facts/
  4. 下一轮对话自动带上新理解

运行：
    python examples/minimal.py
"""

import sys
import os

# 把 engine 加入路径（示例独立于框架根运行也行）
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "engine"))

from load_soul import Soul
from record import Recorder


def simulate_ai_turn(user_msg, soul, recorder):
    """模拟：AI 收到用户消息，先加载理解，再决定是否记录。"""
    pack = soul.context_pack()
    # 真实场景：把 pack 拼进 system prompt 交给底层模型
    print(f"\n[系统注入的上下文包前 120 字]\n{pack[:120]}...\n")

    # 极简"记录决策"：命中关键词就记（真实 AI 用语义判断）
    # 以下示例数据为虚构通用数据，非任何真实用户
    triggers = {
        "AI 产品": ("FACT", "用户职业是 AI 产品经理"),
        "简洁": ("PREF", "用户偏好简洁回复"),
        "别删文件": ("BOUND", "未经确认不删用户文件"),
    }
    for kw, (ftype, stmt) in triggers.items():
        if kw in user_msg:
            rec = recorder.add(ftype, "用户", stmt, confidence=0.9)
            print(f"[AI 主动记录] {ftype} <- {stmt}  (id={rec['id']})")


def main():
    print("=== 伴生灵魂盘（Soul Framework） · 最小示例 ===")
    soul = Soul()
    recorder = Recorder()

    # 第一轮对话（示例数据全为虚构）
    print("\n--- 第 1 轮 ---")
    simulate_ai_turn("我是做 AI 产品的，平时给我简洁回复就行", soul, recorder)

    # 第二轮：用户划了条边界
    print("\n--- 第 2 轮 ---")
    simulate_ai_turn("对了，未经确认别删我文件", soul, recorder)

    # 第三轮：框架已积累理解，context_pack 自动带上
    print("\n--- 第 3 轮（AI 已'更懂'用户）---")
    print(soul.context_pack())

    print("\n=== 示例结束 ===")
    print("提示：以上为演示，data/facts/ 已写入示例记录。生产环境由真实 AI 语义判断记录。")


if __name__ == "__main__":
    main()
