"""生成客户端只读的数据交付清单。"""

import hashlib
import json
import re
import tomllib
from pathlib import Path


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
    # 布尔值在 Python 是整数的子类，但不能用作 TOML 版号。
    version = config["version"]
    if type(version) is not int or not 0 <= version < 2**63 or not config["game"].strip():
        raise ValueError("游戏配置需要非负 version 和非空显示名")
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
