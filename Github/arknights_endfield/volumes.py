"""校验连续 ZIP 分卷并保留足以重新签发下载地址的定位信息。"""

import re
from urllib.parse import parse_qs

from ..publisher import byte_count, https, md5, url_name


def location(url):
    parsed = https(url)
    if not parsed.query:
        return {"url": url}
    params = parse_qs(parsed.query, keep_blank_values=True)
    signature = params.pop("auth_key", [])
    if params or len(signature) != 1 or not signature[0]:
        raise ValueError("终末地分卷地址包含未知的授权约定")
    # 有时效的签名留在响应内。消费者按已冻结的卷名、大小和 MD5 换取新地址。
    return {"locator": parsed._replace(query="").geturl(), "urlRequiresRefresh": True}


def member(row):
    url = row["url"]
    size = byte_count(row["package_size"])
    if size == 0:
        raise ValueError("终末地发布了空归档卷")
    return {"path": url_name(url), **location(url), "size": size, "md5": md5(row["md5"])}


def volume(part):
    match = re.fullmatch(r"(.+\.zip)\.([0-9]{3,})", part["path"], re.IGNORECASE)
    if match is None:
        raise ValueError("终末地分卷应形如 .zip.001")
    return int(match[2]), match[1], part


def check_volumes(volumes):
    roots = {base for _, base, _ in volumes}
    if len(roots) != 1:
        raise ValueError("终末地发布未返回同一 ZIP 的全量卷表")
    # 同一个归档从首卷开始连续编号，防止拼入遗漏、重复的输入流。
    ordinals = [ordinal for ordinal, _, _ in volumes]
    if ordinals != list(range(1, len(volumes) + 1)):
        raise ValueError("终末地分卷表缺卷或卷号重复")


def ordered_parts(rows):
    parts = list(map(member, rows))
    if len(parts) == 1 and parts[0]["path"].lower().endswith(".zip"):
        return parts
    # 保留原始卷名作为下载及复验的成员标识，只用数字卷号决定写入顺序。
    volumes = sorted(map(volume, parts), key=lambda value: value[0])
    check_volumes(volumes)
    return [part for _, _, part in volumes]


def archives(pkg, parts_key):
    # 空 packs 允许发行商回到单个完整 ZIP。发布方也可能只给逐卷大小。
    rows = pkg.get(parts_key) or [pkg]
    parts = ordered_parts(rows)
    compressed = sum(part["size"] for part in parts)
    declared = byte_count(pkg["package_size"])
    if declared and declared != compressed:
        raise ValueError("终末地分卷字节数和发布方总量不符")
    return {"format": "zip", "parts": parts, "compressedBytes": compressed,
            "reportedUnpackedBytes": byte_count(pkg["total_size"])}
