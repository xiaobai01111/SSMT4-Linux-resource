"""同一快照发布规范安装清单和仅供维护使用的上游索引。"""

import re
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from .client_data import CATALOG, entry, json_bytes, write_descriptor
from .publisher import name


# 维护索引与安装索引分名，避免把尚无执行能力的发行数据交给客户端。
PUBLISHER_INDEX = "publisher.json"
CHOICE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}")
RESERVED_PATH = re.compile(r"(?i:con|prn|aux|nul|conin\$|conout\$|com[1-9]|lpt[1-9])")


@dataclass
class InstallRelease:
    channel: str
    quality: str
    version: str
    entry: str
    assets: list[dict]
    install: dict


def release_component(value, label):
    if not value:
        return f"default.{label}"
    # 点号不会出现在选择键中，因此保留名称的转义不会与真实选择键冲突。
    if RESERVED_PATH.fullmatch(value) or value in {"scripts", "tests", "docs", "node_modules"}:
        return f"{label}.{value}"
    return value


def release_selection(release):
    keys = (release.channel, release.quality)
    if any(key and not CHOICE.fullmatch(key) for key in keys):
        raise ValueError("安装发布的渠道或画质标识无效")
    version = release.version
    # 版本不要求 SemVer，也不作为文件路径的一部分。
    trimmed = version and version == version.strip()
    size = len(version.encode("utf-8"))
    if not trimmed or size > 256 or not version.isprintable():
        raise ValueError("安装发布的版本无效")
    return {"channel": release.channel, "quality": release.quality, "version": version}


def checked_releases(releases):
    paths = set()
    for release in releases:
        selection = release_selection(release)
        relative = Path("releases", release_component(release.channel, "channel"),
                        release_component(release.quality, "quality") + ".json")
        # 由选择键生成路径；同键、大小写差异或目录保留名都不能争用同一目标。
        path_key = relative.as_posix().casefold()
        if path_key in paths:
            raise ValueError("安装目录含重复的发布选择或目标路径")
        paths.add(path_key)
        yield release, selection, relative


def write_install_catalog(stage, game_id, releases=()):
    entries = {}
    for release, selection, relative in checked_releases(releases):
        # 目标表单独固定身份；另一区服即使版本相同也不能被替换使用。
        target = {"schema": 1, "type": "game-install-target", "gameId": game_id,
                  "selection": selection, "entry": release.entry, "assets": release.assets,
                  "install": release.install}
        path = stage / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(json_bytes(target))
        ref = entry(path, f"manifests/{relative.as_posix()}")
        entries[release.channel, release.quality] = (selection, ref)
    # 仅排序小型引用；较大的正文写完即释放，不积攒到循环结束。
    ordered = [entries[key] for key in sorted(entries)]
    refs = [{"selection": selection, "path": ref["path"], "sha256": ref["sha256"]} for selection, ref in ordered]
    catalog = {"schema": 2, "type": "game-install", "gameId": game_id, "releases": refs}
    index = stage / Path(CATALOG).name
    index.write_bytes(json_bytes(catalog))
    # 按实际索引引用登记交付项，不扫描供维护者使用的上游文件。
    return [entry(index, CATALOG), *(ref for _, ref in ordered)]


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


def write_publisher_index(config, stage, load_channel):
    channels = config["channels"]
    # 默认渠道与实际产出的发布项使用同一份配置。
    if config["default_channel"] not in channels:
        raise ValueError("未配置默认发布渠道")
    releases = {}
    references = {}
    for key, source in channels.items():
        channel = load_channel(source)
        releases[key] = channel
        references[key] = write_channel(stage, key, channel)
    index = {"schema": 1, "type": "game-resource-catalog", "gameId": config["id"], "channels": references}
    (stage / PUBLISHER_INDEX).write_bytes(json_bytes(index))
    return releases


@contextmanager
def staged_snapshot(destination):
    # 候选与最终目录同盘，所有渠道校验结束才对外发布；不覆盖现有快照。
    if destination.exists() or destination.is_symlink():
        raise ValueError(f"输出目录已存在，请指定新目录：{destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as directory:
        stage = Path(directory, "snapshot")
        stage.mkdir(mode=0o755)
        yield stage
        stage.rename(target=destination)


def publish(config_path, destination, adapter):
    load_channel, install_targets = adapter
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    with staged_snapshot(destination) as stage:
        channels = write_publisher_index(config, stage, load_channel)
        # 只有适配器提供可验证的首装目标时，客户端才显示下载入口。
        releases, extra = install_targets(stage, channels) if install_targets else ((), ())
        manifests = write_install_catalog(stage, config["id"], releases)
        write_descriptor(config_path, stage, [*manifests, *extra])
    return destination
