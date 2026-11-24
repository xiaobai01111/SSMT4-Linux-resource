"""用可复现的小型官方格式索引验证补丁、整文件回退和预下载边界。"""

import argparse
import hashlib
import tempfile
import unittest
from pathlib import Path
from dataclasses import dataclass, field

from Github.wuthering_waves.inventory import load_inventory
from Github.wuthering_waves.planner import build_plan
from Github.wuthering_waves.publish import complete_bundles
from Github.wuthering_waves.storage import ObjectStore, json_bytes, object_path


def official(path, digest, size):
    return {"dest": path, "md5": digest * 32, "size": size}


def local_file(row):
    return {"path": row["dest"], "md5": row["md5"], "size": row["size"]}


# 输入和输出拥有不同大小、不同摘要，才能观测到差分和回退的不同传输量。
OLD = official("client.pak", "1", 100)
NEW = official("client.pak", "2", 120)
DIRECT = {**official("info.txt", "3", 12), "fromFolder": "special/"}
TRANSPORT = official("delta.krpdiff", "4", 7)
CONFIG = {"game": "Test", "default_channel": "bilibili", "default_quality": "HD",
          "channels": {"bilibili": {"app_id": "10004"}}}


def example_index():
    # 每个测试独立持有删除列表与差分组，可构造冲突而不污染其他用例。
    return {"resource": [DIRECT, TRANSPORT], "deleteFiles": ["retired.bin"],
            "groupInfos": [{"dest": "delta.krpdiff", "srcFiles": [OLD], "dstFiles": [NEW]}]}


@dataclass
class UpdateFixture:
    root: Path
    index: dict = field(default_factory=example_index)

    def state(self):
        # 故意只提供差分组实际所需的旧文件，直接完整文件在本机缺失。
        old_files = [local_file(OLD)]
        receipt = {"game": "Test", "channel": "bilibili", "quality": "HD", "version": "1.0",
                   "packs": {"common": "1.0"}, "files": old_files}
        return {"installed": receipt, "files": old_files, "capabilities": ["krpdiff"]}

    def release(self, stage, version):
        (self.root / "upstream").mkdir(exist_ok=True)
        wire = json_bytes(self.index)
        digest = hashlib.md5(wire, usedforsecurity=False).hexdigest()
        (self.root / object_path(digest)).write_bytes(wire)
        # 正式与预下载各有带身份及摘要的目标文件，公共包以外的档位由单独的发布测试覆盖。
        rows = [{**local_file(r), "url": "https://cdn.example/full/" + r["dest"], "pack": "common"}
                for r in (NEW, DIRECT)]
        target = {"game": "Test", "channel": "bilibili", "stage": stage, "version": version, "quality": "HD",
                  "packs": [{"name": "common", "version": version}], "files": rows}
        ref = ObjectStore(self.root, self.root).write_target(target)
        return {"version": version, "qualities": {"HD": ref}, "cdns": ["https://cdn.example", "https://mirror.example"],
                "patches": {"common": {"1.0": {"md5": digest, "base_path": "delta"}}}}

    def plan(self, state, stage="formal"):
        # 预下载测试的正式版与本机一致，用同一套选择逻辑验发布阶段。
        formal_version = "1.0" if stage == "predownload" else "2.0"
        releases = {"formal": self.release("formal", formal_version)}
        if stage == "predownload":
            releases[stage] = self.release(stage, "2.0")
        index = {"schema": 1, "game": "Test", "channels": {"bilibili": releases}}
        (self.root / "publisher.json").write_bytes(json_bytes(index))
        args = argparse.Namespace(config=self.root.parent / "sources.toml", channel=None, quality=None,
                                  operation="update", catalog=None, stage=stage, full_files=False)
        return build_plan(args, CONFIG, load_inventory(state, "Test"))


class UpdateTests(unittest.TestCase):
    def fixture(self):
        # 测试只在临时资源仓库生成索引，不写入已提交的官方样本。
        root = Path(self.enterContext(tempfile.TemporaryDirectory()), "manifests")
        root.mkdir(mode=0o700)
        return UpdateFixture(root)

    def test_patch_and_complete_urls(self):
        case = self.fixture()
        plan = case.plan(case.state())
        # 传输量等于补丁加完整文件，不是输出文件的大小。
        self.assertEqual(plan["download_bytes"], 19)
        self.assertEqual(plan["patches"][0]["outputs"], [local_file(NEW)])
        self.assertEqual([d["url"] for d in plan["downloads"]],
                         ["https://cdn.example/delta/delta.krpdiff", "https://cdn.example/special/info.txt"])

    def test_staged_transfer(self):
        case = self.fixture()
        plan = case.plan({**case.state(), "staged": [local_file(TRANSPORT)]})
        self.assertEqual(plan["download_bytes"], DIRECT["size"])
        self.assertEqual(plan["staged"][0]["id"], plan["patches"][0]["download"])

    def test_bad_source_uses_full_files(self):
        case = self.fixture()
        state = case.state()
        # 同大小、不同 MD5 是另一渠道旧文件也可能出现的真实形态。
        state["files"] = [{**local_file(OLD), "md5": "7" * 32}]
        plan = case.plan(state)
        self.assertFalse(plan["patches"])
        self.assertEqual(plan["download_bytes"], 132)

    def test_invalid_patch_rejected(self):
        case = self.fixture()
        case.index["deleteFiles"] = [DIRECT["dest"]]
        self.assertRaisesRegex(ValueError, "删除", case.plan, case.state())
        # 全量目标的一项摘要不同，即使下载索引来自正确渠道也要拒绝。
        case.index["deleteFiles"].clear()
        case.index["groupInfos"][0]["dstFiles"] = [{**NEW, "md5": "8" * 32}]
        self.assertRaisesRegex(ValueError, "目标清单", case.plan, case.state())

    def test_predownload_only_stages_payload(self):
        case = self.fixture()
        plan = case.plan(case.state(), "predownload")
        # 预下载就绪也不能交付可更改本机渠道、版本或游戏文件的动作。
        self.assertEqual(plan["stage"], "predownload")
        self.assertEqual(plan["download_bytes"], 19)
        for key in ("prepare", "patches", "actions", "risk_control", "finalize"):
            self.assertEqual(plan[key], [])
        self.assertNotIn("receipt", plan)

    def test_incomplete_predownload(self):
        # B 服未发布目标公共包时仍可正常生成正式版快照。
        section = {"resourcePacks": {"common": {}}, "bundles": {"HD": {"resourcePacks": ["common", "hd"]}}}
        self.assertEqual(complete_bundles(section), {})
