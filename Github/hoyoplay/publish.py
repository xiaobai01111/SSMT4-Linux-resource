"""将 HoYoPlay 的游戏分支、启动参数和构建目录编成资源包。"""

from functools import cache
from urllib.parse import urlencode

from .. import catalog
from ..publisher import byte_count, document, md5, name, public_url, url_name
from .install import targets
from .sophon import category_map, full_components, patch_components


def response(url, **kwargs):
    data = document(url, **kwargs)
    if data["retcode"] != 0:
        raise ValueError(f"HoYoPlay 发布接口返回错误码 {data['retcode']}")
    return data["data"]


def combo_url(source):
    fields = {key: source[key] for key in ("launcher_id", "channel", "sub_channel")}
    return f"{public_url(source['combo_url'])}?{urlencode(fields)}"


def app(items, identity):
    matches = [row for row in items if row["game"] == identity]
    if len(matches) != 1:
        raise ValueError(f"HoYoPlay 未唯一返回游戏 {identity['id']}")
    return matches[0]


def build_query(source, branch, predownload):
    fields = {key: branch[key] for key in ("branch", "package_id", "password")}
    fields["plat_app"] = source["game_id"]
    # 正式分支固定版本。预下载接口通过分支标识定位当前开放的构建。
    if not predownload:
        fields["tag"] = branch["tag"]
    return urlencode(fields)


def build(api, query, method):
    endpoint = "getBuild" if method == "GET" else "getPatchBuild"
    # 请求参数包含构建凭据，只在接口调用里使用，输出由以下组件白名单重组。
    data = response(f"{api}/{endpoint}?{query}", method=method)
    return data["manifests"]


def sophon(source, branch, predownload=False):
    categories = category_map(branch)
    api = public_url(source["build_api"]).rstrip("/")
    query = build_query(source, branch, predownload)
    rows = build(api, query, "GET")
    result = {"version": name(branch["tag"]), "protocol": "sophon",
              "components": full_components(rows, categories)}
    versions = set(map(name, branch["diff_tags"]))
    # 没有声明任何差分源版时，不把空差分响应误判为没有正式游戏。
    if versions:
        diff = build(api, query, "POST")
        result["patches"] = patch_components(diff, categories, versions)
    return result


def archive(row):
    url = public_url(row["url"])
    return {"path": url_name(url), "url": url, "md5": md5(row["md5"]),
            "size": byte_count(row["size"]), "reportedUnpackedBytes": byte_count(row["decompressed_size"])}


def legacy(package):
    main = (package.get("main") or {}).get("major") or {}
    parts = main.get("game_pkgs")
    if not parts:
        return None
    # 旧压缩包接口常停留在旧版。每个包保留独立版本，正式版本始终来自 Sophon 分支。
    audio = [{**archive(row), "language": name(row["language"])} for row in main["audio_pkgs"]]
    return {"version": name(main["version"]), "parts": list(map(archive, parts)),
            "audio": sorted(audio, key=lambda row: row["language"])}


def load_channel(source, data):
    # 海外崩坏 3 的多个地区共享 biz，必须同时用游戏 ID 选择数据。
    identity = {"id": source["game_id"], "biz": source["biz"]}
    configs = app(data["launch_configs"], identity)
    branches = app(data["game_branches"], identity)
    details = {"publisherGameId": identity["id"], "publisherBiz": identity["biz"],
               "entry": name(configs["exe_file_name"]), "installFolder": configs["installation_dir"]}
    formal = sophon(source, branches["main"])
    packages = app(data["game_packages"], identity)
    formal["legacyArchives"] = legacy(packages)
    # 预下载分支可为空对象或 null，这两种状态都只发布正式版。
    branch = branches.get("pre_download") or {}
    pre_tag = branch.get("tag")
    pre = sophon(source, branch, predownload=True) if pre_tag else None
    return catalog.Channel(details, formal, pre)


def publish(config, output, previous):
    # 海外各区共用一次分支响应。下一轮生成重新查询已发布的版本。
    combo = cache(response)
    # 只把正式组件交给目标生成器；差分和预下载仍保留在维护索引。
    def load(source):
        return load_channel(source, combo(combo_url(source)))

    def deliver(stage, channels):
        return targets(stage, channels, previous)

    catalog.publish(config, output, (load, deliver))
