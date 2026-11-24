"""通过仓库内登记的适配器生成和更新每款游戏的清单快照。"""

import sys
import tempfile
import tomllib
from pathlib import Path

from .arknights_endfield.publish import publish as generate_endfield
from .hoyoplay.publish import publish as generate_hoyoplay
from .snapshot import apply_snapshot
from .wuthering_waves.publish import publish as generate_wuwa


# 资源目录跟随独立仓库位置，不从调用者的工作目录推断。
GAME_ROOT = Path(__file__).resolve().parents[1] / "Game"
GAME_ID = "wuthering-waves"
# 只运行在工具目录里显式登记的适配器，不从 Game 中加载或执行脚本。
UPDATERS = {
    GAME_ID: ("Wuthering Waves", generate_wuwa),
    "genshin-impact": ("Genshin Impact", generate_hoyoplay),
    "honkai-star-rail": ("Honkai Star Rail", generate_hoyoplay),
    "honkai-impact-3rd": ("Honkai Impact 3rd", generate_hoyoplay),
    "zenless-zone-zero": ("Zenless Zone Zero", generate_hoyoplay),
    "arknights-endfield": ("Arknights Endfield", generate_endfield),
}
SYNC_ERRORS = (OSError, ValueError, KeyError, TypeError)


def config_path(game, root=GAME_ROOT):
    folder, _ = UPDATERS[game]
    return root / folder / "sources.toml"


def manifest_dir(config):
    current = config.with_name("manifests")
    if current.is_symlink() or (current.exists() and not current.is_dir()):
        raise ValueError(f"活动清单不是普通目录：{current}")
    return current


def generate(game, output, root=GAME_ROOT):
    source = config_path(game, root)
    # 目录显示名可含空格，但配置的稳定标识必须与适配器的登记一致。
    config = tomllib.loads(source.read_text(encoding="utf-8"))
    if config.get("type") != "game" or config.get("id") != game:
        raise ValueError(f"游戏类型或标识与登记不符：{source}")
    _, generator = UPDATERS[game]
    generator(source, output, manifest_dir(source))


def sync_game(game, root):
    current = manifest_dir(config_path(game, root))
    # 完整候选通过上游校验后才计算差异，暂存与正式清单位于同一文件系统。
    with tempfile.TemporaryDirectory(prefix=".game-resources-", dir=current.parent) as work:
        candidate = Path(work, "manifests")
        generate(game, candidate, root)
        return apply_snapshot(candidate, current)


def sync_games(games, root=GAME_ROOT):
    failed = False
    for game in games:
        try:
            changed = sync_game(game, root)
        except SYNC_ERRORS as error:
            # 一个游戏的上游不可用不应阻止其他游戏；最终仍以非零状态报告失败。
            print(f"{game}：同步失败：{error}", file=sys.stderr)
            failed = True
        else:
            print(f"{game}：{'已更新' if changed else '无变化'}")
    return int(failed)
