"""规范化 Sophon 构建目录，保留描述组件选择和字节重组所需的字段。"""

from ..publisher import byte_count, md5, name, public_url


def delivery(row):
    # 当前发布的组件没有加密或带时效的 URL 后缀，遇到不同传输约定时保留旧快照。
    has_secret = row.get("password") or row.get("url_suffix")
    if row["encryption"] != 0 or has_secret:
        raise ValueError("Sophon 组件包含此交付协议未支持的传输字段")
    return {"urlPrefix": public_url(row["url_prefix"]).rstrip("/"),
            "compression": {0: "none", 1: "zstd"}[row["compression"]]}


def manifest(row):
    info = row["manifest"]
    source = delivery(row["manifest_download"])
    # 发布方 checksum 覆盖解压后的 protobuf；传输字节数用于下载前建立预算。
    return {"url": f"{source['urlPrefix']}/{name(info['id'])}",
            "compression": source["compression"], "unpackedMd5": md5(info["checksum"]),
            "compressedBytes": byte_count(info["compressed_size"]),
            "unpackedBytes": byte_count(info["uncompressed_size"])}


def stats(row):
    names = {"compressed_size": "compressedBytes", "uncompressed_size": "unpackedBytes",
             "file_count": "fileCount", "chunk_count": "chunkCount"}
    # 差分和未启用的组件可能省略统计计数；大小仍由发布的统计字段给出。
    return {output: byte_count(row[key]) for key, output in names.items() if key in row}


def category_map(branch):
    # 场景及类别含义以 HoYoPlay 的分支表为准，数字组件也必须保留。
    rows = branch["categories"]
    result = {name(row["category_id"]): row for row in rows}
    if len(result) != len(rows):
        raise ValueError("Sophon 分支包含重复组件 ID")
    return result


def component(row, categories):
    category_id = name(row["category_id"])
    matching = name(row["matching_field"])
    category = categories.get(category_id, {})
    declared = category.get("matching_field", matching)
    if declared != matching:
        raise ValueError("Sophon 构建和分支组件标识不一致")
    # 绝区零的 mini 语音有构建记录，但分支未声明它们属于哪个场景。
    kind = category.get("type")
    scenarios = sorted(map(name, category.get("scenarios", [])))
    return {"categoryId": category_id, "name": row["category_name"],
            "matchingField": matching, "categoryType": kind, "scenarios": scenarios, "manifest": manifest(row)}


def ordered(rows):
    result = sorted(rows, key=lambda row: (row["matching_field"] != "game", row["category_id"]))
    ids = {row["category_id"] for row in result}
    if len(ids) != len(result):
        raise ValueError("Sophon 构建包含重复组件 ID")
    return result


def full_components(rows, categories):
    if sum(row["matching_field"] == "game" for row in rows) != 1:
        raise ValueError("Sophon 构建须包含一个游戏主体组件")
    # 不因分卷场景或语音语言过滤构建，安装方用同一份场景声明选择组件。
    return [{**component(row, categories), "chunks": delivery(row["chunk_download"]),
             "stats": stats(row["stats"])} for row in ordered(rows)]


def patch_component(row, categories, versions):
    sizes = {version: stats(row["stats"][version]) for version in versions}
    return {**component(row, categories), "chunks": delivery(row["diff_download"]), "fromVersions": sizes}


def patch_components(rows, categories, from_versions):
    # 空 stats 的组件没有可用源版本。不同旧版可以共用同一份差分索引。
    patches = []
    for row in ordered(rows):
        versions = row["stats"].keys() & from_versions
        if versions:
            patches.append(patch_component(row, categories, versions))
    return patches
