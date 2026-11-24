"""覆盖无素材快照与发布描述符之间的切换。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from Github import catalog
from Github.client_data import entry
from Github.sync import GAME_ROOT


# 待发布与已发布共用同一个规范的配置，仅图标状态不同。
CONFIG = '''
type = "game"
id = "fixture"
version = 1
game = "测试游戏"
default_channel = "cn"
[entrypoints]
catalog = "manifests/index.json"
icon = "{icon}"
background = ""
[channels.cn]
'''


def channel(_source):
    return catalog.Channel({"entry": "Game.exe"}, {"version": "1.0", "protocol": "fixture"})


class CatalogTests(unittest.TestCase):
    def test_committed_deliveries_match_payloads(self):
        # 在访问发行商之前发现未随配置、图片或清单更新的旧交付摘要。
        descriptors = sorted(GAME_ROOT.glob("*/manifests/package.json"))
        self.assertTrue(descriptors)
        for descriptor in descriptors:
            root = descriptor.parent.parent
            files = json.loads(descriptor.read_bytes())["files"]
            with self.subTest(game=root.name):
                actual = [entry(root / row["path"], row["path"]) for row in files]
                self.assertEqual(actual, files)

    def test_missing_icon_keeps_snapshot_out_of_client_discovery(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        config = root / "sources.toml"
        draft = root / "draft"
        config.write_text(CONFIG.format(icon=""), encoding="utf-8")
        catalog.publish(config, draft, channel)
        # 桌面资源发现使用 package.json。缺少图标时不让当前客户端加载半成品。
        self.assertFalse((draft / "package.json").exists())
        package = json.loads((draft / "package.pending.json").read_bytes())
        by_path = {row["path"]: row for row in package["files"]}
        # 正式发布前也只收录客户端文件，发行商原始索引单独保留。
        self.assertEqual(set(by_path), {"sources.toml", "manifests/index.json"})
        content = (draft / "index.json").read_bytes()
        self.assertEqual(by_path["manifests/index.json"]["sha256"], hashlib.sha256(content).hexdigest())
        self.assertEqual(json.loads(content), {"schema": 2, "type": "game-install", "gameId": "fixture", "releases": []})
        # 元数据不足时索引为空，不伪造无法验证的安装目标。
        publisher = json.loads((draft / "publisher.json").read_bytes())
        self.assertEqual(publisher["channels"]["cn"]["entry"], "Game.exe")
        self.assertEqual(publisher["channels"]["cn"]["formal"]["path"], "cn/formal.json")
        content = (draft / "cn/formal.json").read_bytes()
        self.assertEqual(publisher["channels"]["cn"]["formal"]["sha256"], hashlib.sha256(content).hexdigest())

    def test_icon_can_be_supplied_without_background(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        image = root / "icon.png"
        # 此处只测交付登记，不在清单测试中引入图片处理依赖。
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        config = root / "sources.toml"
        config.write_text(CONFIG.format(icon=image.name), encoding="utf-8")
        # 图标就绪后使用客户端发现名称。背景仍为空且没有任何虚构的图像引用。
        ready = root / "ready"
        catalog.publish(config, ready, channel)
        self.assertFalse((ready / "package.pending.json").exists())
        package = json.loads((ready / "package.json").read_bytes())
        self.assertIn(image.name, {row["path"] for row in package["files"]})

    def test_default_release_selection_is_explicit_and_unique(self):
        stage = Path(self.enterContext(tempfile.TemporaryDirectory()))
        # 空渠道和空画质是合法默认键，但仍须占用唯一的发布路径。
        assets = [{"path": "Install.exe", "urls": ["https://cdn.example/Install.exe"], "size": 1,
                   "hash": {"sha256": "a" * 64}}]
        release = catalog.InstallRelease("", "", "1.0", "Install.exe", assets, {"kind": "files"})
        catalog.write_install_catalog(stage, "other-game", [release])
        index = json.loads((stage / "index.json").read_bytes())
        [ref] = index["releases"]
        selection = {"channel": "", "quality": "", "version": "1.0"}
        self.assertEqual(ref["selection"], selection)
        # 交付的是目标原始字节，重排 JSON 也必须更新摘要。
        target = (stage / ref["path"].removeprefix("manifests/")).read_bytes()
        self.assertEqual(ref["sha256"], hashlib.sha256(target).hexdigest())
        self.assertEqual(json.loads(target), {"schema": 1, "type": "game-install-target", "gameId": "other-game",
                                             "selection": selection, "entry": "Install.exe", "assets": assets,
                                             "install": {"kind": "files"}})
        with self.assertRaisesRegex(ValueError, "重复"):
            catalog.write_install_catalog(stage, "other-game", [release, release])
