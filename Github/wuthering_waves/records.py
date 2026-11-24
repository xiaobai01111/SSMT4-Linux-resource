"""鸣潮全量与差分清单共用的路径、摘要和文件描述。"""

import re
from urllib.parse import quote


def relative_path(value):
    # dest 同时用于本地相对路径和 CDN 路径，必须保留原始大小写和分隔方式。
    if not isinstance(value, str) or re.search(r"[\\:\x00-\x1f\x7f]", value):
        raise ValueError(f"无效的资源路径 {value!r}")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise ValueError(f"资源路径包含无效分量 {value!r}")
    return value


def content_url(base, path):
    return base.rstrip("/") + "/" + quote(relative_path(path), safe="/")


def md5_text(value):
    # 库洛在发布索引中提供清单摘要，在清单中另行提供每个文件的摘要。
    if not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{32}", value):
        raise ValueError(f"无效的 MD5 {value!r}")
    return value.lower()


def checked_chunk(path, offset, chunk):
    start, end = chunk["start"], chunk["end"]
    if (type(start), type(end)) != (int, int) or start != offset or end < start:
        raise ValueError(f"{path} 的分块范围无效")
    return {"start": start, "end": end, "md5": md5_text(chunk["md5"])}


def chunks_for(path, size, infos):
    # 官方分块的起止偏移均包含在块内。只发布连续且覆盖整份文件的分块表。
    chunks = []
    offset = 0
    for chunk in infos:
        record = checked_chunk(path, offset, chunk)
        chunks.append(record)
        offset = record["end"] + 1
    if offset != size:
        raise ValueError(f"{path} 的分块总长度不符")
    return chunks


def file_record(row, base, pack):
    path = row["dest"]
    size = row["size"]
    # 整数按原始字节数保留，包括空文件和超过 4 GiB 的游戏包。
    if type(size) is not int or size < 0:
        raise ValueError(f"{path} 的文件大小无效")
    entry = {"pack": pack, "path": path, "size": size, "md5": md5_text(row["md5"]),
             "url": content_url(base, path)}
    infos = row.get("chunkInfos")
    if infos:
        entry["chunks"] = chunks_for(path, size, infos)
    return entry


def merge_files(files, rows):
    # 多个包可登记同一文件。相同内容只下载一次，冲突时整份结果作废。
    for row in rows:
        previous = files.setdefault(row["path"].casefold(), row)
        if (previous["size"], previous["md5"]) != (row["size"], row["md5"]):
            raise ValueError(f"分包之间存在冲突文件 {row['path']}")
