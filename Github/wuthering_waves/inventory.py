"""读取核心确认的安装回执和本机事实；这里不会访问游戏目录。"""

from dataclasses import dataclass

from .records import PUBLISHER_GAME, md5_text, relative_path


def blob_key(row):
    # 核心暂存区按内容标识跨预下载和正式更新复用，同一摘要还要核对大小。
    size = row["size"]
    if type(size) is not int or size < 0:
        raise ValueError("文件大小必须为非负整数")
    return size, md5_text(row["md5"])


def fingerprint(row):
    # 只向移除、备份操作传入可重新验证的字段，保留原始路径大小写。
    size, digest = blob_key(row)
    return {"path": relative_path(row["path"]), "size": size, "md5": digest}


def indexed(rows):
    # Windows 资源名忽略大小写。重复事实会导致来源歧义，直接拒绝。
    if not isinstance(rows, list):
        raise ValueError("本地文件记录应为数组")
    entries = [fingerprint(row) for row in rows]
    result = {entry["path"].casefold(): entry for entry in entries}
    if len(result) != len(entries):
        raise ValueError("清单中有重复的文件路径")
    return result


def matches(local, target):
    # 命中要求实际字节数和 MD5 同时一致，版本号或相同文件名均不能替代校验。
    return local is not None and (local["size"], local["md5"]) == (target["size"], target["md5"])


@dataclass
class Inventory:
    receipt: dict
    files: dict
    owned: dict
    caches: dict
    capabilities: list
    staged: set

    def transfer_cost(self, record):
        return 0 if blob_key(record) in self.staged else record["size"]

    def present(self, record):
        actual = self.files.get(record["path"].casefold())
        return matches(actual, record)

    def restore_risk(self):
        # 核心使用自己的执行日志恢复，资源脚本不向回执中追加任意写盘指令。
        enabled = self.receipt.get("risk_applied", False)
        return [{"action": "restore_risk_from_receipt"}] if enabled else []


def load_inventory(raw):
    # 已装回执和磁盘事实有不同来源。回执只决定所有权，不能替代现场校验。
    if not isinstance(raw, dict):
        raise ValueError("本机状态应为 JSON 对象")
    receipt = raw.get("installed") or {}
    if receipt:
        validate_receipt(raw, receipt)
    files = indexed(raw.get("files", []))
    originals = indexed(raw.get("originals", []))
    owned = indexed(receipt.get("files", []))
    # originals 是核心可从风控回执恢复且已验过内容的原件，用于计算恢复后的状态。
    if originals and not receipt.get("risk_applied"):
        raise ValueError("恢复原件需要已应用的风控回执")
    files.update(originals)
    caches = raw.get("cache", {})
    capabilities = raw.get("capabilities", [])
    staged = set(map(blob_key, raw.get("staged", [])))
    return Inventory(receipt, files, owned, caches, capabilities, staged)


def validate_receipt(raw, receipt):
    if not isinstance(receipt, dict) or receipt["game"] != PUBLISHER_GAME or "files" not in raw:
        raise ValueError("已装回执须对应当前游戏，并提供核验后的 files")
    # 缺少一个已装包的来源版本时，仍可走全量校验；所有权列表必须明确给出。
    missing = {"channel", "quality", "packs", "files"} - receipt.keys()
    if missing:
        raise ValueError(f"已装回执缺少 {sorted(missing)}")
    if not valid_versions(receipt["packs"]):
        raise ValueError("已装分包版本应为非空字符串")


def valid_versions(versions):
    return isinstance(versions, dict) and all(isinstance(v, str) and v for v in versions.values())


def channel_cache(local, target, config):
    source = config["channels"][local.receipt["channel"]]
    group = source.get("cache_group")
    destination = config["channels"][target["channel"]]
    if not group or group != destination.get("cache_group"):
        return None
    cache = local.caches.get(target["channel"], {})
    # 缓存隔离到目标渠道和主、次版本。旧缓存不会用于另一个大版本。
    recorded = cache.get("version", "").split(".")[:2]
    if recorded != target["version"].split(".")[:2]:
        return {}
    return indexed(cache.get("files", []))


def retired_files(local, target_keys, action):
    # 仅处理上一张成功回执里的路径；未知文件和自行修改的文件继续留在磁盘。
    retired = []
    for key in sorted(local.owned.keys() - target_keys):
        old = local.owned[key]
        if local.present(old):
            retired.append({"action": action, "expect": old})
    return retired


def uninstall_plan(local):
    if not local.receipt:
        raise ValueError("卸载必须提供该游戏的安装回执")
    # 仅移除回执拥有且内容匹配的文件，手动修改的文件单独列出来供核心展示。
    actions = retired_files(local, set(), "remove_owned")
    leftover = [row["path"] for key, row in local.owned.items() if key in local.files and not local.present(row)]
    return {"schema": 1, "operation": "uninstall", "game": PUBLISHER_GAME, "prepare": local.restore_risk(),
            "downloads": [], "patches": [], "actions": actions, "preserved": leftover, "download_bytes": 0,
            "risk_control": [], "finalize": [{"action": "restore_registration_from_receipt"},
                                             {"action": "remove_receipt"}]}
