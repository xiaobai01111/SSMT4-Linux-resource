"""生成客户端只读的数据交付清单。"""

import hashlib
import json
import re
import tomllib
from pathlib import Path


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
DESCRIPTORS = {"package.json", "package.pending.json"}


def picture(root, name):
    if Path(name).suffix.lower() not in IMAGE_TYPES or not re.fullmatch(r"[^\W_][\w .-]*", name):
        raise ValueError(f"资源包图片须位于根目录：{name}")
    return entry(root / name, name)


def validate_config(config):
    if config["type"] != "game" or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", config["id"]):
        raise ValueError("游戏数据包需要 type=game 和稳定的 id")
    if config["entrypoints"]["catalog"] != "manifests/index.json":
        raise ValueError("发行配置中的 catalog 须指向已生成的总索引")


def artwork(root, entries, allow_draft):
    names = {entries["icon"], entries["background"]}
    # 新目录可预留素材，鸣潮的旧资源约定仍要求两项图片齐全。
    if allow_draft:
        names.discard("")
    return [picture(root, name) for name in sorted(names)]


def manifest_entries(stage):
    # 不把待交付描述符收进同一包，否则更新图标会产生新的自引用。
    paths = sorted(stage.rglob("*.json"))
    return [entry(file, "manifests/" + file.relative_to(stage).as_posix())
            for file in paths if file.name not in DESCRIPTORS]


def write_descriptor(config_path, stage, allow_draft=False):
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    validate_config(config)
    entries = config["entrypoints"]
    media = artwork(config_path.parent, entries, allow_draft)
    files = [entry(config_path, "sources.toml"), *media, *manifest_entries(stage)]
    descriptor = {"schema": 1, "type": "game", "id": config["id"], "files": files}
    # 现有客户端要求图标。留空的包仍可生成摘要，但使用非发现名称。
    name = "package.json" if entries["icon"] else "package.pending.json"
    (stage / name).write_bytes(json_bytes(descriptor))
