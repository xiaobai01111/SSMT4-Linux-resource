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
        rows = [{"dest": name + ".txt", "size": 1, "md5": "a" * 32}]
        raw = json.dumps({"resource": rows}).encode()
        digest = hashlib.md5(raw, usedforsecurity=False).hexdigest()
        packs[name] = {"version": "1.0", "indexFile": name, "indexFileMd5": digest,
                       "unCompressSize": 1, "baseUrl": "files"}
        payloads["https://cdn.example/" + name] = raw
    section = {"bundles": {"HD": {"resourcePacks": ["common", "hd"]}}, "packs": packs,
               "cdns": ["https://cdn.example"]}
    return section, payloads


def snapshot_files(path):
    return {file.relative_to(path): file.read_bytes() for file in path.rglob("*.json")}


class PublicationTests(unittest.TestCase):
    def test_publish_replay_and_failure(self):
        section, data = package_fixture()

        # 两次发布读取相同入口，第二次只从已有快照取子清单。
        sections = patch.object(publisher, "channel_sections", return_value=[("formal", section)])
        with tempfile.TemporaryDirectory() as directory, sections:
            root = Path(directory)
            config = root / "sources.toml"
            config.write_text('type="game"\nid="test"\ngame="Test"\n[entrypoints]\nicon="a.png"\n'
                              'background="a.png"\ncatalog="manifests/index.json"\n'
                              '[channels.test]\nindex_urls=["https://release.example"]\n'
                              '[channels.extra]\nindex_urls=["https://release.example"]\n')
            (root / "a.png").write_bytes(b"test artwork")
            # 即便资源包目录里有脚本，客户端交付清单也不会登记它。
            (root / "ignored.py").write_text("raise AssertionError")
            with patch.object(manifests, "metadata_bytes", side_effect=data.__getitem__):
                publisher.publish(config, root / "first", root)
            fresh = json.loads((root / "first/index.json").read_bytes())
            self.assertEqual(set(fresh["channels"]), {"test", "extra"})
            shipped = json.loads((root / "first/package.json").read_bytes())
            self.assertNotIn("ignored.py", [item["path"] for item in shipped["files"]])
            with patch.object(manifests, "metadata_bytes", side_effect=AssertionError("访问外网")):
                publisher.publish(config, root / "second", root / "first")
            self.assertEqual(snapshot_files(root / "first"), snapshot_files(root / "second"))
            # 错误摘要使整个临时快照作废，既不会覆盖上次结果也不会漏出半份清单。
            with patch.object(manifests, "metadata_bytes", return_value=b"corrupt"), self.assertRaisesRegex(ValueError, "MD5"):
                publisher.publish(config, root / "failed", root)
            self.assertFalse((root / "failed").exists())

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
        index = json.loads((ROOT / "index.json").read_bytes())
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
