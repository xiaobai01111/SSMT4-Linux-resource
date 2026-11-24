"""从发行商的正式发布和已安装版本预下载接口生成有序卷表。"""

from urllib.parse import urlencode

from .. import catalog
from ..publisher import byte_count, document, md5, name, public_url
from .install import targets
from .volumes import archives


def latest(source, installed=None):
    fields = {key: source[key] for key in ("appcode", "launcher_appcode", "channel", "sub_channel")}
    # 当前正式版的首次请求读取全量 ZIP，带版本的请求单独探测预下载状态。
    fields.update(launcher_sub_channel=source["sub_channel"], disk_type=0)
    if installed is not None:
        fields["version"] = installed
    return document(f"{public_url(source['latest_url'])}?{urlencode(fields)}")


def verification(pkg):
    # 发布方提供的是文件 CDN 根和索引标识；检查实际文件仍需该发行协议的校验逻辑。
    result = {"fileRoot": public_url(pkg["file_path"]), "gameFilesMd5": md5(pkg["game_files_md5"])}
    current = pkg.get("new_game_files_md5")
    if current:
        result["newGameFilesMd5"] = md5(current)
    return result


def predownload(source, installed):
    status = latest(source, installed)
    if (status["version"], status["request_version"]) != (installed, installed):
        raise ValueError("探测预下载时终末地正式版本已改变")
    patch = status.get("pre_patch")
    if not patch:
        return None
    # 预下载发布的是基于已安装版的差分介质，独立记录源版与目标版。
    return {"protocol": "hgdiff", "version": name(patch["version"]), "fromVersion": installed,
            "clientVersion": name(patch["client_version"]), **archives(patch, "patches")}


def load_channel(source):
    data = latest(source)
    version = name(data["version"])
    pkg = data["pkg"]
    # 同一个游戏 ID 也可对应 B 服的独立包，不能仅按游戏版本匹配。
    if byte_count(pkg["sub_channel"]) != source["sub_channel"] or pkg["package_type"] != "ALL_PACKAGE":
        raise ValueError("终末地返回了其他渠道或非完整发行包")
    formal = {"version": version, "protocol": "split-zip", **archives(pkg, "packs"),
              "verification": verification(pkg)}
    details = {"publisherGameId": source["appcode"], "entry": name(source["entry"])}
    return catalog.Channel(details, formal, predownload(source, version))


def publish(config, output, _cache):
    catalog.publish(config, output, (load_channel, targets))
