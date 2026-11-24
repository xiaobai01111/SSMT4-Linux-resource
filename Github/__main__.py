"""独立资源仓库的统一游戏清单维护入口。"""

import argparse
import sys
from pathlib import Path

from .sync import GAME_ID, SYNC_ERRORS, UPDATERS, config_path, generate, sync_games
from .wuthering_waves import commands as wuwa


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    update = commands.add_parser("update", help="比较并更新全部已登记游戏，或用 --game 限定一款")
    # 自动化默认为全量；单款选择以登记 ID 为准，不接受任意目录。
    update.add_argument("--game", choices=UPDATERS)
    snapshot = commands.add_parser("snapshot", help="生成单个游戏的快照，不修改本地清单")
    # 单独生成候选便于审查，使用新目录避免覆盖活动清单。
    snapshot.add_argument("--game", choices=UPDATERS, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    # 保留游戏特有的维护功能，但只从这一个模块启动。
    wuwa.register(commands, GAME_ID, config_path(GAME_ID))
    return parser.parse_args()


def main():
    args = arguments()
    try:
        if args.action == "update":
            return sync_games((args.game,) if args.game else UPDATERS)
        if args.action == "snapshot":
            generate(args.game, args.output)
        else:
            wuwa.run(args)
    except SYNC_ERRORS as error:
        # 诊断写到 stderr，以便调用方仍可将游戏计划作为 JSON 管道消费。
        print(f"游戏资源操作失败：{error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
