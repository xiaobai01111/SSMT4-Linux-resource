"""在发布目录写入不可变对象；消费者按总索引和摘要读取同一快照。"""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from ..client_data import json_bytes
from .manifests import MAX_METADATA, bounded_read, first_response, json_object, verified_bytes
from .records import content_url, md5_text, relative_path


def object_path(digest):
    # 保留库洛原件的字节表示，补丁可用官方公布的 MD5 独立复核。
    return Path("upstream", md5_text(digest) + ".json")


def target_path(target):
    # 目录直接展示渠道和发布类型，限制每一项为单个可移植的文件名。
    parts = [target[key] for key in ("channel", "stage", "quality")]
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", part) for part in parts):
        raise ValueError("渠道、发布类型或档位含有非法文件名")
    return Path(*parts).with_suffix(".json")


def read_target(root, reference):
    # 发布与维护计划均从同一快照读取，并核对该快照引用的原始目标字节。
    path = Path(relative_path(reference["path"]))
    data = (root / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != reference["sha256"]:
        raise ValueError(f"目标清单损坏 {path}")
    target = json_object(data)
    if path != target_path(target):
        raise ValueError("目标清单位置与其渠道或档位不符")
    return target


def cached_bytes(cache, digest):
    # 每次复用已有对象时仍按本次上游索引校验，损坏的缓存可从镜像重新获取。
    path = cache / object_path(digest)
    if not path.is_file():
        return None
    size = path.stat().st_size
    if size > MAX_METADATA:
        return None
    with path.open(mode="rb") as stream:
        data = bounded_read(stream)
    if hashlib.md5(data, usedforsecurity=False).hexdigest() == digest:
        return data
    return None


def add_index(requests, spec, cdns):
    digest = md5_text(spec["indexFileMd5"])
    urls = requests.setdefault(digest, {})
    # 多个旧版本可能共用同一个补丁索引摘要，保留所有已知的可用入口。
    for cdn in cdns:
        urls[content_url(cdn, spec["indexFile"])] = None


def collect_objects(release):
    cdns = release["cdns"]
    requests = {}
    for spec in release["packs"].values():
        entries = [spec, *spec.get("patchConfig", [])]
        for entry in entries:
            add_index(requests, entry, cdns)
    return requests


@dataclass
class ObjectStore:
    stage: Path
    cache: Path

    def copy_index(self, item):
        digest, urls = item
        destination = self.stage / object_path(digest)
        if destination.exists():
            return
        data = cached_bytes(self.cache, digest)
        if data is None:
            reader = partial(verified_bytes, expected_md5=digest)
            _, data = first_response(urls, reader)
        # 摘要正确但不是 JSON 对象的响应不应出现在资源仓库中。
        json_object(data)
        destination.write_bytes(data)

    def load_release(self, release):
        # 同一个阶段先按摘要去重。各线程写不同对象；任何线程失败都阻止发布索引。
        requests = collect_objects(release)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(self.copy_index, requests.items()))

    def full_pack(self, spec):
        path = self.stage / object_path(spec["indexFileMd5"])
        return json_object(path.read_bytes())

    def write_target(self, manifest):
        # 最终下载表与原生补丁索引分别命名，避免混用两种摘要。
        data = json_bytes(manifest)
        digest = hashlib.sha256(data).hexdigest()
        path = target_path(manifest)
        (self.stage / path).parent.mkdir(parents=True, exist_ok=True)
        (self.stage / path).write_bytes(data)
        return {"path": path.as_posix(), "sha256": digest}
