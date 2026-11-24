"""将已校验的游戏候选快照按文件差异发布。"""

import shutil
import tempfile
from contextlib import ExitStack
from filecmp import cmp
from pathlib import Path


def same_file(staged, active):
    # 不按版本号、时间或文件大小判断内容。活动符号链接只作为待替换项。
    return not active.is_symlink() and staged.is_file() and active.is_file() and cmp(staged, active, shallow=False)


def entry_changes(staged, active):
    # 同名普通目录继续向下比对，保留本地目录及内部未变文件。
    if not active.is_symlink() and staged.is_dir() and active.is_dir():
        yield from tree_changes(staged, active)
    elif not same_file(staged, active):
        yield staged, active


def tree_changes(staged, active):
    names = {item.name for item in staged.iterdir()} | {item.name for item in active.iterdir()}
    # 描述符引用新快照的正文，最后更新它。
    for name in sorted(names, key=lambda name: (name == "package.json", name)):
        yield from entry_changes(staged / name, active / name)


def apply_change(change, backup, undo):
    staged, active = change
    if active.is_symlink() or active.exists():
        previous = active.rename(backup)
        undo.callback(previous.rename, active)
    if staged.exists():
        installed = staged.rename(active)
        undo.callback(installed.rename, staged)


def publish(changes, backup):
    # 删除和覆盖先移到备份。某项写入失败时，按逆序还原已执行的同批操作。
    with ExitStack() as undo:
        for index, change in enumerate(changes):
            apply_change(change, backup / str(index), undo)
        undo.pop_all()


def apply_snapshot(staged, active, config_change):
    changes = list(entry_changes(staged, active))
    if config_change:
        changes.insert(0, config_change)
    if not changes:
        return False
    # 备份独立于候选暂存目录；回滚失败时保留尚可恢复的原件。
    backup = Path(tempfile.mkdtemp(prefix=".game-resources-backup-", dir=active.parent))
    try:
        publish(changes, backup)
    except OSError as error:
        if any(backup.iterdir()):
            raise OSError(f"清单恢复未完成，原件保留在 {backup}：{error}") from error
        shutil.rmtree(backup)
        raise
    shutil.rmtree(backup)
    return True
