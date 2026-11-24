"""生成客户端只读的数据交付清单。"""

import hashlib
import json
import re
import tomllib
from pathlib import Path

from .game_version import bumped_config, version_parts
from .snapshot import same_file

CATALOG = "manifests/index.json"


def json_bytes(value):
    # 固定编码和字段顺序，让内容相同的重新生成保留同一个 Git 版本。
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def entry(file, name):
    # 所有相对路径以 Game 下的单个包目录为基准，摘要覆盖实际分发字节。
    if file.is_symlink() or not file.is_file():
        raise ValueError(f"缺少游戏资源普通文件 {name}")
    size = file.stat().st_size
    with file.open(mode="rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": name, "size": size, "sha256": digest}


IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".webp"}


def picture(root, name):
    if Path(name).suffix.lower() not in IMAGE_TYPES or not re.fullmatch(r"[^\W_][\w .-]*", name):
        raise ValueError(f"资源包图片须位于根目录：{name}")
    return entry(root / name, name)


def validate_config(config):
    if config["type"] != "game" or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,95}", config["id"]):
        raise ValueError("游戏数据包需要 type=game 和稳定的 id")
    version_parts(config["version"])
    if not config["game"].strip():
        raise ValueError("游戏配置需要非空显示名")
    if config["entrypoints"]["catalog"] != CATALOG:
        raise ValueError("发行配置中的 catalog 须指向已生成的总索引")


def artwork(root, entries):
    # 背景可缺省；未登记图标的包永远只写待发布描述符。
    names = {entries["icon"], entries.get("background", "")} - {""}
    return [picture(root, name) for name in sorted(names)]


def write_descriptor(config_path, stage, manifests):
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    entries = config["entrypoints"]
    media = artwork(config_path.parent, entries)
    files = [entry(config_path, "sources.toml"), *media, *manifests]
    descriptor = {"schema": 1, "type": "game", "id": config["id"], "files": files}
    # 现有客户端要求图标。留空的包仍可生成摘要，但使用非发现名称。
    name = "package.json" if entries["icon"] else "package.pending.json"
    (stage / name).write_bytes(json_bytes(descriptor))


def stage_game_version(source, stage, current):
    # 先用原版号生成交付单，与客户端一样比较整包摘要覆盖的内容。
    package = stage / "package.json"
    if not package.exists():
        # 缺图标的游戏也保留可验证的候选，补齐素材后才允许桌面端发现。
        package = stage / "package.pending.json"
    if same_file(package, current / package.name):
        return None
    config = stage.parent / "sources.toml"
    config.write_bytes(bumped_config(source))
    descriptor = json.loads(package.read_bytes())
    # 配置本身也受交付单保护，两个文件必须随同一份候选发布和回滚。
    proof = entry(config, "sources.toml")
    for item in descriptor["files"]:
        if item["path"] == proof["path"]:
            item.update(proof)
            break
    else:
        raise ValueError("游戏交付单缺少 sources.toml")
    package.write_bytes(json_bytes(descriptor))
    return config, source
