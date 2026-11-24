"""用已发布快照验证从安装回执到本机差额计划的正常入口。"""

import argparse
import tomllib
import unittest

from Github.sync import GAME_ID, config_path
from Github.wuthering_waves.inventory import load_inventory
from Github.wuthering_waves.planner import build_plan
from Github.wuthering_waves.rules import risk_actions


DEFAULT_CONFIG = config_path(GAME_ID)
CONFIG = tomllib.loads(DEFAULT_CONFIG.read_text())


def plan(operation, state, channel=None):
    request = argparse.Namespace(operation=operation, config=DEFAULT_CONFIG, catalog=None, channel=channel,
                                 quality="HD", stage="formal")
    return build_plan(request, CONFIG, load_inventory(state, CONFIG["game"]))


def installed(channel):
    # 由真实目标清单生成回执，当作核心已成功执行且全部文件验证通过的事实。
    receipt = plan("install", {}, channel)["receipt"]
    return {"installed": receipt, "files": receipt["files"]}


class PlanningTests(unittest.TestCase):
    def test_install_and_repair(self):
        local = installed("cn")
        current = plan("update", local)
        self.assertEqual(current["downloads"], [])
        self.assertEqual(current["reused_files"], len(local["files"]))
        # 只损坏一项且大小不变，待下载量应等于该文件自身的大小。
        first, *others = local["files"]
        local["files"] = [{**first, "md5": "0" * 32}, *others]
        repair = plan("update", local)
        self.assertEqual([f["path"] for f in repair["downloads"]], [first["path"]])
        self.assertEqual(repair["download_bytes"], first["size"])

    def test_switch_with_verified_cache(self):
        local = installed("cn")
        cold = plan("switch", local, "bilibili")
        # 按冷缓存请求填满目标服缓存；相互切服时匹配项应全部从本机恢复。
        local["cache"] = {"bilibili": {"version": cold["version"], "files": cold["downloads"]}}
        warm = plan("switch", local, "bilibili")
        self.assertEqual(warm["download_bytes"], 0)
        # 缓存命中数由两服当天的文件表决定，不绑定某个发行版本的固定数量。
        restored = [a for a in warm["actions"] if a["action"] == "restore_cached"]
        self.assertEqual(len(restored), len(cold["downloads"]))
        self.assertEqual(warm["finalize"][-2]["value"]["appId"], CONFIG["channels"]["bilibili"]["app_id"])
        local["cache"]["bilibili"]["version"] = "0.0.test"
        expired = plan("switch", local, "bilibili")
        self.assertEqual(expired["downloads"], cold["downloads"])
        # 国际服使用自己整套清单比对，不使用官/B 服的差异缓存。
        self.assertEqual(plan("switch", local, "global")["prepare"], [])

    def test_uninstall_preserves_modified_files(self):
        # 未列入回执的存档和用户改写的原文件都不能作为删除目标。
        local = installed("global")
        first = local["files"][0]
        local["files"] = [{**first, "md5": "0" * 32}, {"path": "save.dat", "size": 0, "md5": "a" * 32}]
        result = plan("uninstall", local)
        self.assertEqual(result["actions"], [])
        self.assertEqual(result["preserved"], [first["path"]])
        self.assertEqual(result["finalize"][-1]["action"], "remove_receipt")

    def test_requires_own_receipt(self):
        with self.assertRaises(ValueError):
            plan("switch", {}, "global")
        local = installed("cn")
        with self.assertRaises(ValueError):
            plan("update", local, "bilibili")


class RiskRuleTests(unittest.TestCase):
    def test_declarative_file_rules(self):
        target = {"channel": "cn", "quality": "HD", "version": "test"}
        # 仅使用测试路径，生产配置没有任何默认禁用文件或字段。
        field = {"action": "set_field", "format": "json", "key_path": ["ABC"], "from": "102", "to": "205"}
        text = {"action": "replace_text", "from": "ABC=102", "to": "ABC=205"}
        rules = [{"action": "disable", "path": "example.txt"}, {**field, "path": "example.json"},
                 {**text, "path": "example.cfg"}, {**text, "path": "b.txt", "channels": ["bilibili"]}]
        actions = risk_actions({"risk_control": {"rules": rules}}, target)
        self.assertEqual(actions[0]["target"], "example.txt.disable")
        self.assertEqual(actions[1]["to"], "205")
        self.assertEqual(actions[2]["expected_matches"], 1)
        for action, rule in zip(actions, rules[:3], strict=True):
            self.assertEqual(action["path"], rule["path"])
        # 源路径不能通过上级目录访问游戏根目录以外的文件。
        rules[0]["path"] = "../external"
        with self.assertRaises(ValueError):
            risk_actions({"risk_control": {"rules": rules}}, target)
