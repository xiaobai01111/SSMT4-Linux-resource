"""写入按渠道分文件的资源索引及同一快照的交付描述符。"""

import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .client_data import entry, json_bytes, write_descriptor
from .publisher import name


@dataclass
class Channel:
    details: dict
    formal: dict
    predownload: dict | None = None


def save_release(root, release, relative):
    path = root / relative
    path.write_bytes(json_bytes(release))
    # 索引单独绑定正式和预下载目标；路径相对于索引所在目录。
    return {**entry(path, relative), "version": release["version"]}


def write_channel(stage, key, channel):
    # 每个渠道须对应独立的新目录，禁止复用其他渠道的目标文件。
    (stage / name(key)).mkdir(mode=0o755)
    result = dict(channel.details)
    result["formal"] = save_release(stage, channel.formal, f"{key}/formal.json")
    if channel.predownload is not None:
        result["predownload"] = save_release(stage, channel.predownload, f"{key}/predownload.json")
    return result


def write_catalog(config, stage, load_channel):
    channels = config["channels"]
    # 默认渠道与实际产出的发布项使用同一份配置。
    if config["default_channel"] not in channels:
        raise ValueError("未配置默认发布渠道")
    results = {key: write_channel(stage, key, load_channel(source)) for key, source in channels.items()}
    index = {"schema": 1, "type": "game-resource-catalog", "gameId": config["id"], "channels": results}
    (stage / "index.json").write_bytes(json_bytes(index))


def publish(config_path, destination, load_channel):
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"输出目录已存在，请指定新目录：{destination}")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # 任一上游渠道缺失时保留整份旧快照，不发布可用渠道集合残缺的目录。
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        stage = Path(directory, "snapshot")
        stage.mkdir(mode=0o755)
        write_catalog(config, stage, load_channel)
        write_descriptor(config_path, stage, allow_draft=True)
        return stage.rename(destination)
