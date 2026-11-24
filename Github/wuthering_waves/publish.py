"""按资源配置生成所有渠道的正式清单及已开放的预下载清单。需要 Python 3.11+。"""

import tempfile
import tomllib
from pathlib import Path

from .manifests import bundle_names, combine_packs, first_document, pack_contents, published_cdns
from .records import md5_text, relative_path
from .storage import ObjectStore, read_target
from ..catalog import InstallRelease, PUBLISHER_INDEX, write_install_catalog
from ..client_data import json_bytes, write_descriptor
from ..publisher import public_url


# 启动入口属于发布适配器；客户端只读取目标清单的 entry。
LAUNCH_ENTRY = "Client/Binaries/Win64/Client-Win64-Shipping.exe"


def release_section(section, nodes):
    bundles = section["bundles"]
    if not bundles:
        raise ValueError("发布索引没有可用画质")
    # 只发布有 bundle 引用的包，分包名称和画质从官方索引发现。
    names = dict.fromkeys(name for quality in bundles for name in bundle_names(section, quality))
    packs = {name: section["resourcePacks"][name] for name in names}
    cdns = published_cdns(nodes)
    if not cdns:
        raise ValueError("发布索引没有可用 CDN")
    return {"bundles": bundles, "packs": packs, "cdns": cdns}


def channel_sections(source):
    _, formal = first_document(source["index_urls"])
    yield "formal", release_section(formal, formal["cdnList"])
    pre = formal.get("predownload")
    if pre is not None:
        # 预下载与正式版使用独立目标，部分版本在预下载段继承外层的 CDN。
        nodes = pre.get("cdnList") or formal["cdnList"]
        available = complete_bundles(pre)
        yield "predownload", release_section({**pre, "bundles": available}, nodes) if available else None


def complete_bundles(section):
    # 例如 B 服 common 可能尚未开放。只发布同渠道能拼出完整目标的预下载档位。
    packs = section.get("resourcePacks", {})
    bundles = section.get("bundles", {})
    fields = {"version", "indexFile", "indexFileMd5", "baseUrl", "unCompressSize"}
    ready = {name for name, spec in packs.items() if isinstance(spec, dict) and fields <= spec.keys()}
    result = {}
    for quality, bundle in bundles.items():
        names = set(bundle["resourcePacks"])
        if "common" in names and names <= ready:
            result[quality] = bundle
    return result


def patch_table(spec):
    # 版本键指向确切的旧包版本。同一个版本出现两次时不能推断应使用哪份补丁。
    patches = spec.get("patchConfig", [])
    result = {patch["version"]: {"md5": md5_text(patch["indexFileMd5"]),
                                 "base_path": relative_path(patch["baseUrl"].rstrip("/"))}
              for patch in patches}
    if len(result) != len(patches):
        raise ValueError(f"{spec['version']} 发布索引包含重复的补丁源版本")
    return result


def generate_release(store, release, identity):
    store.load_release(release)
    # 抓取优先级可随 CDN 繁忙度变化。发布地址按可用节点名固定排序，避免相同发布反复提交。
    cdns = sorted({prefix.rstrip("/") + "/" for prefix in release["cdns"]})
    packs = {}
    patches = {}
    for name, spec in release["packs"].items():
        packs[name] = pack_contents({**spec, "name": name}, cdns[0], store.full_pack(spec))
        patches[name] = patch_table(spec)
    version = release["packs"]["common"]["version"]
    qualities = {}
    # 同一份 common 在不同画质之间复用，整轮生成只读取一次渠道入口。
    for quality, bundle in release["bundles"].items():
        files = combine_packs(packs[name] for name in bundle["resourcePacks"])
        target = {**identity, "version": version, "quality": quality, **files}
        qualities[quality] = store.write_target(target)
    return {"version": version, "qualities": qualities, "patches": patches, "cdns": cdns}


def generate_channel(store, config, channel):
    # source 记录资源包配置中的主入口，即便本轮从该渠道的备用入口取得了索引。
    source = config["channels"][channel]
    releases = {}
    for stage, release in channel_sections(source):
        if release is None:
            releases["formal"]["predownload_pending"] = True
            continue
        identity = {"game": config["game"], "channel": channel, "stage": stage, "source": source["index_urls"][0]}
        releases[stage] = generate_release(store, release, identity)
    return releases


def cdn_prefix(url):
    if not url.endswith("/") or any(c in url for c in "\\?#") or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise ValueError("无效的鸣潮 CDN 前缀")
    return public_url(url)


def checked_files(stage, reference, selection):
    target = read_target(stage, reference)
    # 比较发布者快照中原有身份，不从目标文件名推断区服或版本。
    if tuple(target[key] for key in ("game", "channel", "quality", "stage", "version")) != selection:
        raise ValueError("目标清单与正式发布索引登记不匹配")
    files = target["files"]
    if (len(files), sum(row["size"] for row in files)) != (target["file_count"], target["total_size"]):
        raise ValueError("目标清单的文件数量或总大小不符")
    return files


def install_asset(row, cdns):
    # 只替换同服的已核验首节点前缀，保留上游文件名和 URL 编码。
    primary = cdns[0]
    suffix = row["url"].removeprefix(primary)
    if not suffix or suffix == row["url"] or suffix.startswith("/"):
        raise ValueError(f"文件来源不属于本服首节点：{row['path']}")
    urls = [row["url"], *(base + suffix for base in cdns[1:])]
    return {"path": row["path"], "urls": urls, "size": row["size"], "hash": {"md5": row["md5"]}}


def channel_installs(stage, identity, formal):
    # 预下载和上游补丁另存为维护数据，不伪装成完整安装发布项。
    game, channel = identity
    cdns = [cdn_prefix(url) for url in formal["cdns"]]
    if not cdns:
        raise ValueError("正式发布没有文件 CDN")
    version = formal["version"]
    for quality, reference in formal["qualities"].items():
        files = checked_files(stage, reference, (game, channel, quality, "formal", version))
        if not any(row["path"] == LAUNCH_ENTRY for row in files):
            raise ValueError("正式发布缺少启动入口")
        assets = [install_asset(row, cdns) for row in files]
        yield InstallRelease(channel, quality, version, LAUNCH_ENTRY, assets, {"kind": "files"})


def install_releases(stage, index):
    for channel, stages in index["channels"].items():
        yield from channel_installs(stage, (index["game"], channel), stages["formal"])


def publish(config_path, destination, cache):
    # 输出目录须是新路径。临时目录与其同盘，所有渠道验证成功后一起改名交付。
    if destination.exists():
        raise ValueError(f"输出目录已存在，请指定新目录：{destination}")
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        stage = Path(temporary, "snapshot")
        (stage / "upstream").mkdir(parents=True)
        store = ObjectStore(stage, cache)
        channels = {key: generate_channel(store, config, key) for key in config["channels"]}
        if not channels:
            raise ValueError("资源包没有配置渠道")
        index = {"schema": 1, "game": config["game"], "channels": channels}
        (stage / PUBLISHER_INDEX).write_bytes(json_bytes(index))
        manifests = write_install_catalog(stage, config["id"], install_releases(stage, index))
        write_descriptor(config_path, stage, manifests)
        # 外部只在所有子清单都可读时看见目标目录。
        stage.rename(target=destination)
