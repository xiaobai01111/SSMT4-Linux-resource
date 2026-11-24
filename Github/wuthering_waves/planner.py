"""根据固定资源快照和本机核验结果生成鸣潮安装、更新、转服或卸载计划。"""

import json
from dataclasses import dataclass, field
from pathlib import Path

from .records import relative_path
from .inventory import blob_key, channel_cache, fingerprint, matches, retired_files, uninstall_plan
from .rules import risk_actions
from .storage import read_target
from .updates import Routes, plan_updates


@dataclass
class Selection:
    manifest: dict
    release: dict
    reference: dict
    root: Path
    cache: dict | None = None
    routes: Routes = field(default_factory=Routes)
    full_files: bool = False


def target_options(args, config, local):
    # 已经安装时继承原服和档位，避免普通更新意外变成跨服操作。
    previous = local.receipt
    operation = args.operation
    if operation in ("switch", "update") and not previous:
        raise ValueError("更新或转服必须提供已安装回执")
    channel = args.channel or previous.get("channel", config["default_channel"])
    quality = args.quality or previous.get("quality", config["default_quality"])
    changed = bool(previous) and previous["channel"] != channel
    if changed != (operation == "switch"):
        raise ValueError("转服需显式指定不同的目标渠道，跨渠道操作请使用 switch")
    return channel, quality


def select_target(args, config, local):
    channel, quality = target_options(args, config, local)
    default_path = relative_path(config["entrypoints"]["catalog"])
    path = args.catalog or args.config.parent / default_path
    index = json.loads(path.read_bytes())
    if index["schema"] != 1 or index["game"] != local.game:
        raise ValueError("资源目录协议或游戏不匹配")
    # 总索引、目标表、更新对象都从一个固定提交的根目录打开。
    stages = index["channels"][channel]
    if args.stage not in stages:
        raise ValueError(f"{channel} 没有已发布的 {args.stage} 清单")
    if args.stage == "predownload":
        formal = read_target(path.parent, stages["formal"]["qualities"][quality])
        require_current_formal(formal, local)
    release = stages[args.stage]
    reference = release["qualities"][quality]
    target = read_target(path.parent, reference)
    identity = (target["game"], target["channel"], target["quality"], target["stage"], target["version"])
    if identity != (local.game, channel, quality, args.stage, release["version"]):
        raise ValueError("目标清单与资源目录登记不匹配")
    return Selection(target, release, reference, path.parent)


def require_current_formal(formal, local):
    # 先完成正式版再预下载；不能提前替换游戏文件、换档位或登记未来版本。
    if local.receipt["quality"] != formal["quality"]:
        raise ValueError("预下载应使用当前安装档位")
    versions = local.receipt["packs"]
    current = all(versions.get(p["name"]) == p["version"] for p in formal["packs"])
    if not current:
        raise ValueError("请先完成当前正式版更新")


def registration(target, channel):
    # 只合并所选档位的登记，核心须保留用户配置其他字段并记下还原所需的原值。
    version, quality = target["version"], target["quality"]
    tier = relative_path(quality.lower())
    bundle = {"state": "", "version": version, "resourcePacks": [p["name"] for p in target["packs"]]}
    return [{"action": "merge_json", "path": f"launcherDownloadConfig/{tier}.json",
             "value": {"packName": tier, "version": version}},
            {"action": "merge_json", "path": "launcherDownloadConfig.json",
             "value": {"group": "default", "appId": channel["app_id"], "version": version, "bundles": {quality: bundle}}},
            {"action": "commit_receipt"}]


def planned_receipt(selection):
    target = selection.manifest
    # 回执模板描述期望状态，核心只在全部动作完成后登记实际持有的文件。
    return {"game": target["game"], "channel": target["channel"], "quality": target["quality"],
            "version": target["version"], "target_sha256": selection.reference["sha256"],
            "packs": {p["name"]: p["version"] for p in target["packs"]},
            "files": [fingerprint(row) for row in target["files"]]}


def empty_plan(operation, selection, local):
    # 核心按阶段处理这个顺序。下载失败不能使游戏登记为新版本。
    target = selection.manifest
    # 显式带回原服，隔离源文件与恢复目标缓存时使用不同命名空间。
    source_channel = local.receipt.get("channel")
    return {"schema": 1, "operation": operation, "game": local.game, "channel": target["channel"],
            "quality": target["quality"], "version": target["version"], "stage": target["stage"],
            "target_sha256": selection.reference["sha256"], "from_channel": source_channel,
            "prepare": local.restore_risk(),
            "downloads": [], "patches": [], "actions": [], "risk_control": [], "finalize": [],
            "reused_files": 0}


def file_action(row, selection, plan):
    # 调用方只传同渠道、同主次版本的缓存，使用前仍核对整文件摘要。
    key = row["path"].casefold()
    expected = fingerprint(row)
    staged = selection.routes.outputs.get(key)
    if staged:
        plan["actions"].append({"action": "install_patch_output", "patch": staged, "file": expected})
        return
    cache = selection.cache or {}
    cached = cache.get(key)
    if matches(cached, expected):
        plan["actions"].append({"action": "restore_cached", "channel": plan["channel"], "file": expected})
        return
    # 下载记录和安装动作由标识关联，核心先暂存并验算，再放入正式路径。
    url = selection.routes.complete_urls.get(key, row["url"])
    transfer = {"id": "file:" + row["path"], "kind": "file", **row, "url": url}
    plan["downloads"].append(transfer)
    plan["actions"].append({"action": "install_file", "download": transfer["id"], "file": expected})


def reconcile_files(local, selection, plan):
    target = selection.manifest
    target_keys = {row["path"].casefold() for row in target["files"]}
    for row in target["files"]:
        if local.present(row):
            plan["reused_files"] += 1
        else:
            file_action(row, selection, plan)
    # 转服把源服独有内容隔离，普通同服更新只清理自己曾安装且仍匹配的文件。
    action = "quarantine_owned" if plan["operation"] == "switch" else "remove_owned"
    plan["actions"].extend(retired_files(local, target_keys, action))


def backups(local, target, plan):
    # 共用缓存组才备份被替换的源服版本。只源服拥有的文件由隔离动作负责。
    for row in target["files"]:
        old = local.owned.get(row["path"].casefold())
        if old and local.present(old) and not matches(old, row):
            plan["prepare"].append({"action": "cache_source", "channel": local.receipt["channel"], "file": old})


def transfer_plan(args, selection, local):
    plan = empty_plan(args.operation, selection, local)
    if selection.cache is not None:
        backups(local, selection.manifest, plan)
    if args.operation == "update":
        plan_updates(selection, local, plan)
    reconcile_files(local, selection, plan)
    add_mirrors(plan["downloads"], selection.release["cdns"])
    # 预下载得到的传输物可在正式发布后继续供安装动作引用。
    separate_staged(local, plan)
    plan["download_bytes"] = sum(item["size"] for item in plan["downloads"])
    return plan


def separate_staged(local, plan):
    # 计划中的传输 ID 对两组都有效；已缓存项只占用本机暂存空间。
    pending = []
    cached = []
    for item in plan["downloads"]:
        if blob_key(item) in local.staged:
            cached.append(item)
        else:
            pending.append(item)
    plan["downloads"] = pending
    plan["staged"] = cached


def add_mirrors(downloads, cdns):
    # 目标表选用固定的主节点，所有可用备用地址都由同一快照的节点和路径派生。
    prefix = cdns[0].rstrip("/") + "/"
    for item in downloads:
        if not item["url"].startswith(prefix):
            raise ValueError("下载地址与本次资源快照不符")
        suffix = item["url"][len(prefix):]
        item["urls"] = [cdn.rstrip("/") + "/" + suffix for cdn in cdns]


def already_applied(plan, receipt):
    # 快照和实际生效规则必须同时匹配，用户更换资源配置时需要重新应用。
    return (not plan["actions"] and receipt.get("risk_rules") == plan["risk_control"]
            and receipt.get("target_sha256") == plan["target_sha256"])


def build_plan(args, config, local):
    # 卸载仅依赖本地回执，因此能够在没有发布快照的情况下生成计划。
    if args.stage == "predownload" and args.operation != "update":
        raise ValueError("仅 update 可生成预下载计划")
    if args.operation == "uninstall":
        return uninstall_plan(local)
    selection = select_target(args, config, local)
    selection.full_files = getattr(args, "full_files", False)
    target = selection.manifest
    if args.operation == "switch":
        selection.cache = channel_cache(local, target, config)
    plan = transfer_plan(args, selection, local)
    if args.stage == "predownload":
        # 仅暂存下载；正式开放后按正式快照重新生成安装计划。
        plan.update(prepare=[], patches=[], actions=[])
        return plan
    plan["risk_control"] = risk_actions(config, target)
    # 已就绪且规则相同时只让核心复核幂等动作，避免把禁用文件反复改名。
    if already_applied(plan, local.receipt):
        plan["prepare"] = []
    plan["finalize"] = registration(target, config["channels"][target["channel"]])
    plan["receipt"] = planned_receipt(selection)
    plan["receipt"]["risk_rules"] = plan["risk_control"]
    return plan
