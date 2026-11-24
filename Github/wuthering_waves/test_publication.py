"""发布测试只读取资源包元数据。坏摘要和发布中断均在临时目录复现。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Github.sync import GAME_ID, config_path
from Github.wuthering_waves import manifests, publish as publisher
from Github.wuthering_waves.storage import object_path, read_target


ROOT = config_path(GAME_ID).with_name("manifests")


def package_fixture():
    # 两个包故意使用不同文件名，避免错误的去重逻辑掩盖画质包缺失。
    payloads = {}
    packs = {}
    for name in ("common", "hd"):
        path = publisher.LAUNCH_ENTRY if name == "common" else "画质@2.txt"
        rows = [{"dest": path, "size": 1, "md5": "a" * 32}]
        raw = json.dumps({"resource": rows}).encode()
        digest = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        packs[name] = {"version": "1.0", "indexFile": name, "indexFileMd5": digest,
                       "unCompressSize": 1, "baseUrl": "files"}
        payloads["https://cdn.example/root/" + name] = raw
        payloads["https://mirror.example/root/" + name] = raw
    section = {"bundles": {"HD": {"resourcePacks": ["common", "hd"]}}, "packs": packs,
               "cdns": ["https://mirror.example/root/", "https://cdn.example/root/"]}
    return section, payloads


def snapshot_files(path):
    return {file.relative_to(path): file.read_bytes() for file in path.rglob("*.json")}


class PublicationTests(unittest.TestCase):
    def first_snapshot(self):
        section, payloads = package_fixture()
        releases = [("formal", section), ("predownload", section)]
        self.enterContext(patch.object(publisher, "channel_sections", return_value=releases))
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = root / "sources.toml"
        config.write_text('type="game"\nid="test"\ngame="Test"\nversion=1\n[entrypoints]\n'
                          'icon="a.png"\nbackground="a.png"\ncatalog="manifests/index.json"\n'
                          '[channels.test]\nindex_urls=["https://release.example"]\n'
                          '[channels.extra]\nindex_urls=["https://release.example"]\n')
        (root / "a.png").write_bytes(b"test artwork")
        # 即便资源包目录里有脚本，客户端交付清单也不会登记它。
        (root / "ignored.py").write_text("raise AssertionError")
        with patch.object(manifests, "metadata_bytes", side_effect=payloads.__getitem__):
            publisher.publish(config, root / "first", root)
        return root, config

    def test_publish_replay_and_failure(self):
        root, config = self.first_snapshot()
        fresh = json.loads((root / "first/publisher.json").read_bytes())
        self.assertEqual(set(fresh["channels"]), {"test", "extra"})
        # 预下载保留在维护索引，不占用正式版的安装项。
        self.assertEqual(set(fresh["channels"]["test"]), {"formal", "predownload"})
        self.assert_install_snapshot(root / "first", "test")
        shipped = json.loads((root / "first/package.json").read_bytes())
        self.assertNotIn("ignored.py", {item["path"] for item in shipped["files"]})
        fresh["channels"]["test"]["formal"]["cdns"][0] = "https://cdn.example/root-other/"
        self.assertRaisesRegex(ValueError, "首节点", lambda: list(publisher.install_releases(root / "first", fresh)))
        # 同一上游重放必须完全复用已经验证的原件。
        with patch.object(manifests, "metadata_bytes", side_effect=AssertionError("访问外网")):
            publisher.publish(config, root / "second", root / "first")
        self.assertEqual(snapshot_files(root / "first"), snapshot_files(root / "second"))
        # 错误摘要使候选作废，既不覆盖上次结果，也不留下发布目录。
        with patch.object(manifests, "metadata_bytes", return_value=b"corrupt"):
            self.assertRaisesRegex(ValueError, "MD5", publisher.publish, config, root / "failed", root)
        self.assertFalse((root / "failed").exists())

    def assert_install_snapshot(self, root, game_id):
        upstream = json.loads((root / "publisher.json").read_bytes())
        index_bytes = (root / "index.json").read_bytes()
        index = json.loads(index_bytes)
        self.assertEqual(set(index), {"schema", "type", "gameId", "releases"})
        self.assertEqual((index["schema"], index["type"], index["gameId"]), (2, "game-install", game_id))
        desc = json.loads((root / "package.json").read_bytes())
        shipped = {item["path"]: item for item in desc["files"]}
        # 维护索引和上游原件不进入客户端下载白名单。
        manifest_paths = {path for path in shipped if path.startswith("manifests/")}
        self.assertEqual(manifest_paths, {"manifests/index.json", *(ref["path"] for ref in index["releases"])})
        self.assertEqual(shipped["manifests/index.json"]["sha256"], hashlib.sha256(index_bytes).hexdigest())
        expected = {(channel, quality)
                    for channel, stages in upstream["channels"].items() for quality in stages["formal"]["qualities"]}
        # 对比组合多重集，避免把重复组合误认成已覆盖所有区服和档位。
        selections = [(row["selection"]["channel"], row["selection"]["quality"]) for row in index["releases"]]
        self.assertCountEqual(selections, expected)
        for release in index["releases"]:
            # 按快照中的渠道、画质回查同一份发布记录。
            selection = release["selection"]
            formal = upstream["channels"][selection["channel"]]["formal"]
            self.assertEqual(selection["version"], formal["version"])
            data = (root / release["path"].removeprefix("manifests/")).read_bytes()
            self.assertEqual(release["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(shipped[release["path"]],
                             {"path": release["path"], "size": len(data), "sha256": release["sha256"]})
            target = json.loads(data)
            self.assertEqual((target["schema"], target["type"], target["gameId"]), (1, "game-install-target", game_id))
            self.assertEqual(target["selection"], selection)
            self.assert_deployment(target, (root, formal))

    def assert_deployment(self, target, source):
        root, formal = source
        # 入口是显式数据；所有镜像都保留该正式版自身的 URL 后缀。
        self.assertEqual(target["entry"], publisher.LAUNCH_ENTRY)
        self.assertEqual(target["install"], {"kind": "files"})
        reference = formal["qualities"][target["selection"]["quality"]]
        official = read_target(root, reference)
        self.assertIn(target["entry"], {row["path"] for row in target["assets"]})
        primary, *mirrors = formal["cdns"]
        for asset, raw in zip(target["assets"], official["files"], strict=True):
            self.assertTrue(raw["url"].startswith(primary))
            self.assertEqual(asset, {"path": raw["path"], "size": raw["size"], "hash": {"md5": raw["md5"]},
                                     "urls": [raw["url"], *(base + raw["url"][len(primary):] for base in mirrors)]})

    def check_stage(self, stage):
        # 加载路径和 SHA-256 同时核对，文件数和字节数从展开后的最终列表独立复算。
        for reference in stage["qualities"].values():
            target = read_target(ROOT, reference)
            self.assertEqual(target["file_count"], len(target["files"]))
            self.assertEqual(target["total_size"], sum(row["size"] for row in target["files"]))
        # 每个可用的源版本必须在同一提交中找到自己的补丁原件。
        for versions in stage["patches"].values():
            self.assertTrue(all((ROOT / object_path(p["md5"])).is_file() for p in versions.values()))

    def test_committed_snapshot(self):
        # 发布前的提交可能比修改后的来源配置少一个新渠道。这里检查已提交快照自身。
        index = json.loads((ROOT / "publisher.json").read_bytes())
        self.assert_install_snapshot(ROOT, GAME_ID)
        self.assertEqual(index["schema"], 1)
        self.assertTrue(index["channels"])
        for stages in index["channels"].values():
            self.assertIn("formal", stages)
            self.assertTrue(stages["formal"]["qualities"])
            for stage in stages.values():
                self.check_stage(stage)

    def test_committed_objects(self):
        # 覆盖包括跨渠道复用的原件，确认没有被文本格式转换改变字节。
        for path in (ROOT / "upstream").glob("*.json"):
            digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
            self.assertEqual(path.relative_to(ROOT), object_path(digest))
