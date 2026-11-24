"""将官方 Sophon 全量索引交付给客户端，不把旧版归档冒充当前版本。"""

import hashlib
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request

from ..catalog import InstallRelease
from ..client_data import entry
from ..publisher import md5, public_url, request_bytes


# 下载的是已定版构建索引，不是游戏分块；交付预算与客户端资源包约束一致。
LIMIT = 32 << 20


def checked_manifest(path, length, digest):
    # 独立限制 zstd 的窗口和输出；超出官方长度时终止，不等待继续展开。
    command = ("zstd", "-qdc", "--memory=32MB", "--", str(path))
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as proc:
        data = proc.stdout.read(length + 1)
        if len(data) > length:
            proc.kill()
            return False
        return proc.wait() == 0 and len(data) == length and hashlib.md5(data, usedforsecurity=False).hexdigest() == digest


def fetch_manifest(destination, info):
    # 构建查询串不进入交付单；这里只请求已经规范化的公开 CDN 对象。
    request = Request(public_url(info["url"]), headers={"Accept-Encoding": "identity"})
    data = request_bytes(request)
    if len(data) != info["compressedBytes"]:
        raise ValueError("Sophon 索引的压缩长度与发布记录不符")
    destination.write_bytes(data)


@dataclass
class IndexStore:
    # 解压 MD5 是发行方版本标识。已核验的索引在同一资源包内只交付一次。
    directory: Path
    previous: Path
    lengths: dict = field(default_factory=dict)

    def materialize(self, info, digest):
        name = f"{digest}.zst"
        path, cached = self.directory / name, self.previous / name
        length = info["unpackedBytes"]
        # 摘要匹配才允许复用。损坏的缓存不影响原快照，重新取得官方原件。
        if cached.is_file() and not cached.is_symlink() and checked_manifest(cached, length, digest):
            shutil.copyfile(cached, path)
            return
        fetch_manifest(path, info)
        if not checked_manifest(path, length, digest):
            raise ValueError(f"Sophon 索引与发布方解压摘要不符：{digest}")

    def component(self, row):
        info, chunks = row["manifest"], row["chunks"]
        digest = md5(info["unpackedMd5"])
        length = info["unpackedBytes"]
        if not (0 < length <= LIMIT and 0 < info["compressedBytes"] <= LIMIT):
            raise ValueError("Sophon 索引超出交付限额")
        if (info["compression"], chunks["compression"]) != ("zstd", "zstd"):
            raise ValueError("不支持此 Sophon 索引或块压缩方式")
        # 同一摘要可能跨区或跨语音复用；源基址仍来自每个组件的实际交付记录。
        if digest not in self.lengths:
            self.materialize(info, digest)
            self.lengths[digest] = length
        if self.lengths[digest] != length:
            raise ValueError("相同 Sophon 摘要声明了不同长度")
        return {"manifest": f"manifests/sophon/{digest}.zst", "unpackedBytes": length,
                "unpackedMd5": digest, "chunkPrefix": public_url(chunks["urlPrefix"])}


def install_components(channel, formal):
    # 本体包括额外资源组件；只选一个默认语音，不将 mini 与正式语音混装。
    voice = "zh-cn" if channel == "cn" else "en-us"
    return (row for row in formal["components"]
            if row["categoryType"] == "CATEGORY_TYPE_RESOURCE" or row["matchingField"] in ("game", voice))


def targets(stage, channels, cache):
    store = IndexStore(stage / "sophon", cache / "sophon")
    # 当前候选按已验证的内容重建，旧快照目录不需要可写。
    store.directory.mkdir(mode=0o755)
    releases = [target(store, key, channel) for key, channel in channels.items()]
    proofs = [entry(file, f"manifests/sophon/{file.name}") for file in sorted(store.directory.iterdir())]
    return releases, proofs


def target(store, key, channel):
    # 冻结器对每个组件和同一发布项的累计展开长度分别限额。
    formal = channel.formal
    manifests = [store.component(row) for row in install_components(key, formal)]
    if not (0 < sum(row["unpackedBytes"] for row in manifests) <= 4 * LIMIT):
        raise ValueError("Sophon 正式版索引总量超限或为空")
    return InstallRelease(key, "", formal["version"], channel.details["entry"], [],
                          {"kind": "sophon", "components": manifests})
