"""在独立资源树中验证游戏之间不会串写，也不会因单个上游失败而阻塞。"""

import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from Github import sync
from Github.client_data import entry, write_descriptor
from Github.game_version import bumped_config, next_version


def make_generator(outputs):
    def generate(config, output, _cache):
        # 上游数据由测试直接替换；先写候选再模拟失败，验证不会盖过已发布的快照。
        game = config.parent.name.split()[-1].lower()
        files = outputs[game]
        output.mkdir(mode=0o700)
        for name, data in files.items():
            target = output / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        if "index.json" not in files:
            raise ValueError("上游索引不可用")
        delivered = [entry(output / name, f"manifests/{name}") for name in sorted(files) if name != "publisher.json"]
        write_descriptor(config, output, delivered)
    return generate


class SyncTests(unittest.TestCase):
    def fixture(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()), "Game")
        self.outputs = {game: {"index.json": b'{"version":1}', "formal/HD.json": b'{"files":[]}'}
                        for game in ("alpha", "beta")}
        # 两款游戏共用同步逻辑，但各自持有独立的来源配置和活动目录。
        generate = make_generator(self.outputs)
        self.games = {"alpha": ("Game Alpha", generate), "beta": ("Game Beta", generate)}
        self.enterContext(patch.object(sync, "UPDATERS", self.games))
        for game in self.games:
            config = sync.config_path(game, self.root)
            config.parent.mkdir(parents=True)
            # 从已有的整数版快照开始，两款游戏的上游更新可分别控制。
            config.write_text(f'type = "game"\nid = "{game}"\ngame = "{game}"\nversion = 1 # 资源\n'
                              '[entrypoints]\ncatalog = "manifests/index.json"\nicon = "icon.png"\n', encoding="utf-8")
            config.with_name("icon.png").write_bytes(b"icon")
            sync.generate(game, self.manifest(game), self.root)

    def sync_all(self):
        # 抓取两个流，区分同步成功的状态报告和单款游戏的错误。
        log, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(log), redirect_stderr(errors):
            result = sync.sync_games(self.games, self.root)
        return result, log.getvalue(), errors.getvalue()

    def manifest(self, game):
        return sync.config_path(game, self.root).with_name("manifests")

    def check_version(self, game, expected):
        config = sync.config_path(game, self.root)
        self.assertEqual(tomllib.loads(config.read_text(encoding="utf-8"))["version"], expected)
        # 校验发布后的真实字节，避免版号正确而桌面端拒收旧摘要。
        package = json.loads((self.manifest(game) / "package.json").read_bytes())
        self.assertIn(entry(config, "sources.toml"), package["files"])

    def test_bump_preserves_comments_newlines_and_nested_versions(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / "sources.toml"
        # 多行说明和渠道的同名键不能被当作资源版号。
        original = b"note = '''\r\nversion = 9\r\n'''\r\n'version' = \"\"\"\r\n3.9.19\"\"\"  # release\r\n[channel]\r\nversion = 4\r\n"
        source.write_bytes(original)
        self.assertEqual(bumped_config(source), original.replace(b'"""\r\n3.9.19"""', b'"3.9.20"'))
        for value in (True, -1, "1.02.3", "1.0", "1.0.1-rc", "1.0.18446744073709551615"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                next_version(value)

    def test_only_changed_delivery_increments_once(self):
        self.fixture()
        config = sync.config_path("beta", self.root)
        original = config.read_bytes()
        for version in ("1.0.1", "1.0.2"):
            self.outputs["beta"]["index.json"] += b"\n"
            self.assertEqual(self.sync_all()[0], 0)
            self.check_version("beta", version)
            self.check_version("alpha", 1)
            self.assertEqual(config.read_bytes(), original.replace(b"version = 1", f'version = "{version}"'.encode()))
            self.assertEqual(self.sync_all(), (0, "alpha：无变化\nbeta：无变化\n", ""))
        # 发行端私有缓存的变化不会改变玩家实际收到的包。
        self.outputs["beta"]["publisher.json"] = b"{}"
        self.assertEqual(self.sync_all()[0], 0)
        self.check_version("beta", "1.0.2")

    def test_update_keeps_unchanged_files_in_changed_game(self):
        self.fixture()
        self.assertEqual(self.sync_all()[0], 0)
        alpha, beta = self.manifest("alpha"), self.manifest("beta")
        # 字节相同的快照应该留在原处，而不是重写出一份内容相同的目录。
        unchanged = (alpha / "index.json").stat()
        unchanged_target = beta / "formal/HD.json"
        target_before = unchanged_target.stat()
        # 根目录也必须保留；只检查文件摘要会漏掉整目录替换。
        directory_before = beta.stat()
        (beta / "retired.json").write_text("{}", encoding="utf-8")
        # 上游索引版本不变，仍应按实际内容和文件集合更新。
        self.outputs["beta"]["index.json"] = b'{"version":1,"quality":"HD"}'
        self.outputs["beta"]["formal/SD.json"] = b'{"files":["new"]}'
        status, log, _ = self.sync_all()
        self.assertEqual(status, 0)
        self.assertIn("alpha：无变化", log)
        current = (alpha / "index.json").stat()
        target_after = unchanged_target.stat()
        directory_after = beta.stat()
        self.assertEqual((current.st_ino, current.st_mtime_ns), (unchanged.st_ino, unchanged.st_mtime_ns))
        self.assertEqual((target_after.st_ino, target_after.st_mtime_ns),
                         (target_before.st_ino, target_before.st_mtime_ns))
        self.assertEqual(directory_before.st_ino, directory_after.st_ino)
        self.assertEqual((beta / "index.json").read_bytes(), self.outputs["beta"]["index.json"])
        self.assertEqual((beta / "formal/SD.json").read_bytes(), self.outputs["beta"]["formal/SD.json"])
        # 旧候选已撤回的条目应从发布树消失，不和新清单一起提交。
        self.assertFalse((beta / "retired.json").exists())

    def test_failed_game_keeps_previous_and_next_game_updates(self):
        # 第一款失败应保留旧内容，第二款仍能在同一次调用中完成。
        self.fixture()
        self.assertEqual(self.sync_all()[0], 0)
        previous = self.outputs["alpha"]["index.json"]
        self.outputs["alpha"] = {"partial.json": b"{}"}
        self.outputs["beta"]["index.json"] = b'{"version":2}'
        status, _, errors = self.sync_all()
        self.assertEqual(status, 1)
        self.assertIn("alpha：同步失败：上游索引不可用", errors)
        self.assertEqual((self.manifest("alpha") / "index.json").read_bytes(), previous)
        self.assertEqual((self.manifest("beta") / "index.json").read_bytes(), self.outputs["beta"]["index.json"])
        self.assertFalse((self.manifest("alpha") / "partial.json").exists())
        self.check_version("alpha", 1)
        self.check_version("beta", "1.0.1")

    def test_publish_error_restores_previous_files(self):
        self.fixture()
        self.assertEqual(self.sync_all()[0], 0)
        active = self.manifest("alpha")
        self.outputs["alpha"]["formal/HD.json"] = b'{"files":["new"]}'
        self.outputs["alpha"]["index.json"] = b'{"version":2}'
        rename = Path.rename

        def reject_index(source, destination):
            # 先让目标清单写入，再在安装索引时失败，覆盖已经生效的两次移动。
            if source.name == "index.json" and destination == active / "index.json":
                raise OSError("索引无法写入")
            return rename(source, destination)

        with patch.object(Path, "rename", reject_index):
            status, _, errors = self.sync_all()
        self.assertEqual(status, 1)
        self.assertIn("索引无法写入", errors)
        self.assertEqual((active / "formal/HD.json").read_bytes(), b'{"files":[]}')
        self.assertEqual((active / "index.json").read_bytes(), b'{"version":1}')
        self.check_version("alpha", 1)
        # 正常回滚后不留下需要人工处理的备份或暂存目录。
        self.assertFalse(list(active.parent.glob(".game-resources-*")))
