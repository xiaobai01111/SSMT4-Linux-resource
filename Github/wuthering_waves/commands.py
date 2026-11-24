"""鸣潮维护命令的参数及处理；由通用资源入口注册。"""

import json
import tomllib
from pathlib import Path

from .inventory import load_inventory
from .manifests import manifest, save_manifest
from .planner import build_plan


def fetch_command(args, config):
    # 不指定渠道或档位时，以这份资源配置中的默认值为准。
    channel = args.channel or config["default_channel"]
    quality = args.quality or config["default_quality"]
    save_manifest(manifest(config, channel, quality), args.output)


def plan_command(args, config):
    # 空安装可省略状态文件。更新、转服和卸载须使用核心实际核验的回执。
    state = json.loads(args.state.read_bytes()) if args.state else {}
    local = load_inventory(state)
    save_manifest(build_plan(args, config, local), args.output)


def plan_arguments(commands):
    parser = commands.add_parser("plan", help="从已提交的清单和本机状态计算执行计划")
    parser.add_argument("operation", choices=("install", "update", "switch", "uninstall"))
    parser.add_argument("--catalog", type=Path, help="固定提交中的 manifests/publisher.json")
    parser.add_argument("--state", type=Path, help="本机文件、缓存与安装回执 JSON")
    parser.add_argument("--channel", help="目标渠道，转服时必须指定")
    parser.add_argument("--quality", type=str.upper)
    parser.add_argument("--stage", choices=("formal", "predownload"), default="formal", help="更新目标发布类型")
    parser.add_argument("--full-files", action="store_true", help="更新时显式采用已校验的整文件差额")
    parser.add_argument("-o", "--output", type=Path)
    parser.set_defaults(run=plan_command)


def register(commands, game_id, config_path):
    game = commands.add_parser(game_id, help="读取鸣潮上游清单或生成执行计划")
    commands = game.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="在线读取一个渠道和画质的正式版文件清单")
    fetch.add_argument("--channel")
    fetch.add_argument("--quality", type=str.upper)
    fetch.add_argument("-o", "--output", type=Path)
    fetch.set_defaults(run=fetch_command)
    plan_arguments(commands)
    # 两个命令默认使用已登记的游戏配置，也允许维护者指定本地样例。
    for command in commands.choices.values():
        command.add_argument("--config", type=Path, default=config_path)


def run(args):
    # 计划与在线读取共用资源配置，但只有 fetch 读取官方网络来源。
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    args.run(args, config)
