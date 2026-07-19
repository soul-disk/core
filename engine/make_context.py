#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_context.py — 给普通用户的一键上下文生成器（零代码接入）

适用场景：
    你用的是别人做好的、改不了代码的 AI（如 OpenClaw / WorkBuddy 等
    闭源或半闭源客户端）。你没法在代码里注入 System Prompt，但可以：
        1. 跑本脚本，生成一段"AI 是谁、它懂你什么"的文本；
        2. 复制，每次开聊先粘进对话框第一段；
        3. 正常聊。值得记的，让 AI "把这条加进我的伴生灵魂盘"，它写回 data/facts/。

    这就是"不用改代码、不用训练"的真实可行方案——半自动：一键生成 + 粘贴。

用法：
    python engine/make_context.py            # 打印到屏幕，手动复制
    python engine/make_context.py --out       # 同时导出到 data/context_latest.txt
"""

import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine.load_soul import Soul


def main():
    ap = argparse.ArgumentParser(description="生成可粘贴的上下文文本")
    ap.add_argument("--out", action="store_true", help="同时导出到 data/context_latest.txt")
    args = ap.parse_args()

    pack = Soul().context_pack()

    print("=" * 60)
    print("以下为你的「伴生灵魂盘上下文」。复制全部内容，每次开聊粘进对话框第一段：")
    print("=" * 60)
    print(pack)
    print("=" * 60)
    print("提示：粘贴后正常说话即可。让它「记住 XXX」会写回 data/facts/。")

    if args.out:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        out_dir = os.path.join(root, "data")
        os.makedirs(out_dir, exist_ok=True)
        out_path = os.path.join(out_dir, "context_latest.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(pack)
        print(f"\n已导出到：{out_path}")


if __name__ == "__main__":
    main()
