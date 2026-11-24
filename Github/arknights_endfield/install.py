"""将正式版的官方 game_files 校验表转换为可冻结的完整文件树。"""

import hashlib
import json
import subprocess
from unicodedata import normalize
from urllib.parse import quote
from urllib.request import Request

from ..catalog import InstallRelease
from ..publisher import byte_count, md5, public_url, request_bytes


# 公开 PC 文件表的固定解码参数，不是 CDN 签名或账户凭据。
# 协议参照 0sour/endfield-launcher@5c7424e 的 games/endfield/crypto.rs。
GAME_FILES_KEY = "c0f30e1ce763bbc21cc355a34303ac50399444bff68c4a22af398c0a166ee143"
GAME_FILES_IV = "33467861192750649501937264608400"


def decrypt(raw):
    # 密文已限长且先验证官方 MD5；CBC 解码输出不会大于输入。
    command = ("openssl", "enc", "-d", "-aes-256-cbc", "-K", GAME_FILES_KEY, "-iv", GAME_FILES_IV)
    try:
        return subprocess.run(command, input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True).stdout
    except subprocess.CalledProcessError as error:
        raise ValueError(f"无法解码终末地正式版 game_files（OpenSSL 退出码 {error.returncode}）") from None


def filename(value):
    path = value.replace("\\", "/")
    # Windows 分隔符统一为协议斜杠；相对分量及原有文件名必须保持完整。
    if any(part in ("", ".", "..") for part in path.split("/")) or ":" in path:
        raise ValueError("终末地文件表含无效相对路径")
    return path


def asset(root, path, identity):
    size, digest = identity
    return {"path": path, "urls": [root + quote(path, safe="/")], "size": size, "hash": {"md5": digest}}


def assets_from_index(root, raw, expected):
    # gameFilesMd5 绑定原始密文。此文件也在首装卷中，但不可能在自己的正文表中登记。
    if hashlib.md5(raw, usedforsecurity=False).hexdigest() != expected:
        raise ValueError("终末地 game_files 与官方 MD5 不符")
    files = [asset(root, "game_files", (len(raw), expected))]
    names = {"game_files"}
    for line in filter(bytes.strip, decrypt(raw).splitlines()):
        row = json.loads(line)
        path = filename(row["path"])
        key = normalize("NFC", normalize("NFD", path).casefold())
        if key in names:
            raise ValueError(f"终末地文件表含重复路径：{path}")
        names.add(key)
        files.append(asset(root, path, (byte_count(row["size"]), md5(row["md5"]))))
    return files


def target(channel, release):
    formal = release.formal
    info = formal["verification"]
    root = public_url(info["fileRoot"]).rstrip("/") + "/"
    # 只使用正式版提供的公开文件根，不把卷签名或预下载状态写进交付单。
    request = Request(root + "game_files", headers={"Accept-Encoding": "identity", "User-Agent": "SSMT4-Linux"})
    files = assets_from_index(root, request_bytes(request), md5(info["gameFilesMd5"]))
    entry = release.details["entry"]
    if not any(item["path"] == entry and item["size"] > 0 for item in files):
        raise ValueError("终末地完整文件表未登记正式启动入口")
    return InstallRelease(channel, "", formal["version"], entry, files, {"kind": "files"})


def targets(_stage, channels):
    # 客户端收到统一文件树，不执行厂商脚本或现场解码私有格式。
    return [target(channel, release) for channel, release in channels.items()], ()
