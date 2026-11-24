"""读取鸣潮 PC 正式版的全量安装清单，输出 JSON。需要 Python 3.11 或以上。"""

import gzip
import hashlib
import io
import json
import tempfile
from functools import partial
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from zlib import error as ZlibError

from .records import PUBLISHER_GAME, content_url, file_record, md5_text, merge_files


# 预算仅用于索引和清单。网络传输及 gzip 展开后的内容分别受限。
MAX_METADATA = 32 * 1024 * 1024


def bounded_read(stream):
    data = stream.read(MAX_METADATA + 1)
    if len(data) > MAX_METADATA:
        raise ValueError("清单超过 32 MiB")
    return data


def https_url(value):
    # 入口与文件 CDN 均使用公开的 HTTPS 地址，登录信息不参与清单请求。
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None:
        raise ValueError(f"无效的 HTTPS 来源地址 {value!r}")
    return value


def metadata_bytes(url):
    request = Request(https_url(url), headers={"Accept-Encoding": "gzip, identity"})
    with urlopen(request, timeout=20) as response:
        https_url(response.geturl())
        data = bounded_read(response)
    # 部分官方端点直接返回 gzip 文件；按字节识别，两种传输方式共用解析逻辑。
    if data.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            return bounded_read(stream)
    return data


def verified_bytes(url, expected_md5):
    # indexFileMd5 的输入是解压传输编码后的原始字节，重新排版 JSON 会改变摘要。
    data = metadata_bytes(url)
    if expected_md5 and hashlib.md5(data, usedforsecurity=False).hexdigest() != expected_md5:
        raise ValueError("清单 MD5 与本次发布索引不符")
    return data


def json_object(data):
    result = json.loads(data)
    if not isinstance(result, dict):
        raise ValueError("清单应为 JSON 对象")
    return result


def should_request(error):
    # 已确定的地址、格式或摘要错误留给其他镜像；网络中断可在一轮之后重试。
    if error is None:
        return True
    if isinstance(error, HTTPError):
        return error.code in (408, 500, 502, 503, 504)
    return isinstance(error, (URLError, TimeoutError, ConnectionError, HTTPException, EOFError))


def first_response(urls, reader):
    # 同一次分包请求的所有镜像共享相同的路径和摘要。失败时保留各镜像诊断。
    if not urls:
        raise ValueError("没有可用的官方清单地址")
    failures = {}
    for url in (*urls, *urls):
        last_error = failures.get(url)
        if not should_request(last_error):
            continue
        try:
            return url, reader(url)
        except (OSError, EOFError, HTTPException, ValueError, ZlibError) as error:
            failures[url] = error
    details = "；".join(f"{url}（{error}）" for url, error in failures.items())
    raise ValueError("无法取得清单：" + details)


def read_document(url, expected_md5):
    return json_object(verified_bytes(url, expected_md5))


def first_document(urls, expected_md5=None):
    # 发布器保留校验过的原始字节，交互入口则直接返回解析结果。
    return first_response(urls, partial(read_document, expected_md5=expected_md5))


def published_cdns(nodes):
    # 库洛用 K1/K2 标记节点可用性，P 决定优先级。备用节点仍须校验同一份摘要。
    eligible = [node for node in nodes if (node["K1"], node["K2"]) == (1, 1) and node["P"] > 0]
    return [https_url(node["url"]) for node in sorted(eligible, key=lambda node: -node["P"])]


def pack_contents(spec, cdn, index):
    # cdn 是一个发布节点，清单地址和文件前缀分别来自同一包的两个字段。
    name = spec["name"]
    base = content_url(cdn, spec["baseUrl"].rstrip("/"))
    rows = [file_record(row, base, name) for row in index["resource"]]
    size = sum(row["size"] for row in rows)
    if size != spec["unCompressSize"]:
        raise ValueError(f"{name} 文件大小合计与发布索引不符")
    # 分包版本分别记录；整套游戏的版本取同一发布索引中的 common。
    pack = {"name": name, "version": spec["version"], "index_url": content_url(cdn, spec["indexFile"]),
            "index_md5": md5_text(spec["indexFileMd5"]), "file_count": len(rows), "total_size": size}
    return pack, rows


def read_pack(name, spec, cdns):
    # 交互查询优先返回实际应答的节点，网络中断时可用同渠道的其他镜像。
    urls = {content_url(cdn, spec["indexFile"]): cdn for cdn in cdns}
    url, index = first_document(urls, md5_text(spec["indexFileMd5"]))
    return pack_contents({**spec, "name": name}, urls[url], index)


def bundle_names(release, quality):
    bundles = release["bundles"]
    if quality not in bundles:
        raise ValueError(f"可用画质：{', '.join(bundles)}")
    names = bundles[quality]["resourcePacks"]
    if not isinstance(names, list) or "common" not in names or len(names) != len(set(names)):
        raise ValueError(f"{quality} 的分包列表无效")
    # 发布切换期间，公共包与画质包必须能组成同一个目标版本。
    specs = release["resourcePacks"]
    versions = {specs[name]["version"] for name in names}
    if len(versions) != 1 or not specs["common"]["version"]:
        raise ValueError(f"{quality} 的分包版本不一致或为空")
    return names


def combine_packs(parts):
    # 以游戏内路径为键汇总，避免两份官方分包重复计算同一文件的下载量。
    files = {}
    packs = []
    for pack, rows in parts:
        merge_files(files, rows)
        packs.append(pack)
    if not files:
        raise ValueError("所选分包没有可下载文件")
    return {"packs": packs, "file_count": len(files),
            "total_size": sum(row["size"] for row in files.values()), "files": list(files.values())}


def manifest(config, channel, quality):
    url, release = first_document(config["channels"][channel]["index_urls"])
    cdns = published_cdns(release["cdnList"])
    parts = [read_pack(name, release["resourcePacks"][name], cdns) for name in bundle_names(release, quality)]
    return {"game": PUBLISHER_GAME, "channel": channel, "version": release["resourcePacks"]["common"]["version"],
            "quality": quality, "source": url, **combine_packs(parts)}


def save_manifest(result, output):
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output is None:
        print(text)
        return
    # 临时文件与目标同目录。全部内容写出并关闭成功后，才替换上次生成的清单。
    temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=output.parent, delete=False)
    staged = Path(temporary.name)
    try:
        with temporary:
            print(text, file=temporary)
        staged.replace(output)
    finally:
        staged.unlink(missing_ok=True)
