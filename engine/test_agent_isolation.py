#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
回归测试：多智能体身份隔离（防"外部 agent 冒用晓安身份"问题复发）

运行：
    cd soul-framework/core
    python -m engine.test_agent_isolation

覆盖：
- 主人(晓安)自身连接：身份=晓安，不变
- 外部未注册 agent：身份留空，context_pack 不显示"晓安"
- 外部 agent 写配置：只写 agents[自己]，绝不污染 top-level 的主人显示名
- 外部 agent 注册后：显示自己的独立身份，不冒用晓安
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # repo root

from engine.load_soul import Soul


def _tmp_config():
    tmp = tempfile.mkdtemp(prefix="soul_iso_")
    cfg = {
        "agent_id": "xiaoan",
        "agent_name": "晓安",
        "user_name": "老大",
        "relationship": "伴侣",
        "agents": {"xiaoan": {"agent_name": "晓安", "user_name": "老大", "relationship": "伴侣"}},
    }
    os.makedirs(os.path.join(tmp, "data", "facts"), exist_ok=True)
    with open(os.path.join(tmp, "config.schema.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return tmp


class TestAgentIsolation(unittest.TestCase):
    def test_owner_identity(self):
        tmp = _tmp_config()
        os.environ["SOUL_ROOT"] = tmp
        s = Soul()
        self.assertEqual(s.agent_id, "xiaoan")
        self.assertEqual(s.agent, "晓安")

    def test_unknown_agent_blank(self):
        tmp = _tmp_config()
        os.environ["SOUL_ROOT"] = tmp
        s = Soul(agent_id="hermes")
        self.assertEqual(s.agent_id, "hermes")
        self.assertEqual(s.agent, "")
        self.assertEqual(s.user, "")
        self.assertEqual(s.relation, "")
        self.assertNotIn("AI 人格名：晓安", s.context_pack())

    def test_external_write_isolation(self):
        tmp = _tmp_config()
        os.environ["SOUL_ROOT"] = tmp
        s = Soul(agent_id="hermes")
        cfg = s.set_config({"agent_name": "Hermes", "user_name": "老大"})
        self.assertEqual(cfg["agents"]["hermes"]["agent_name"], "Hermes")
        # top-level 主人显示名绝不被外部 agent 覆盖
        self.assertEqual(cfg.get("agent_name"), "晓安")
        self.assertEqual(cfg.get("user_name"), "老大")

    def test_registered_agent_own_identity(self):
        tmp = _tmp_config()
        os.environ["SOUL_ROOT"] = tmp
        Soul(agent_id="hermes").set_config({"agent_name": "Hermes"})
        s = Soul(agent_id="hermes")
        self.assertEqual(s.agent, "Hermes")
        self.assertNotIn("AI 人格名：晓安", s.context_pack())


if __name__ == "__main__":
    unittest.main()
