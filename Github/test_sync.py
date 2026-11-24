"""在独立资源树中验证游戏之间不会串写，也不会因单个上游失败而阻塞。"""

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from Github import sync


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
            config.write_text(f'type = "game"\nid = "{game}"\n', encoding="utf-8")

    def sync_all(self):
        # 抓取两个流，区分同步成功的状态报告和单款游戏的错误。
        log, errors = io.StringIO(), io.StringIO()
        with redirect_stdout(log), redirect_stderr(errors):
            result = sync.sync_games(self.games, self.root)
        return result, log.getvalue(), errors.getvalue()

    def manifest(self, game):
        return sync.config_path(game, self.root).with_name("manifests")

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
        # 版本号不变，只有内容和文件名集合变化。
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
        # 正常回滚后不留下需要人工处理的备份或暂存目录。
        self.assertFalse(list(active.parent.glob(".game-resources-*")))
