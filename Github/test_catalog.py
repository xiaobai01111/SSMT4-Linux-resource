"""覆盖无素材快照与发布描述符之间的切换。"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from Github import catalog


CONFIG = '''
type = "game"
id = "fixture"
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
        self.assertEqual(set(by_path), {"sources.toml", "manifests/index.json", "manifests/cn/formal.json"})
        # 子索引和交付描述符必须用同一个文件的实际字节。
        content = (draft / "cn/formal.json").read_bytes()
        self.assertEqual(by_path["manifests/cn/formal.json"]["sha256"], hashlib.sha256(content).hexdigest())

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
