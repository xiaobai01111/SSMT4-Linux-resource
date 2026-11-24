"""把固定快照中的库洛补丁索引翻译成传输清单和分组暂存要求。"""

from dataclasses import dataclass, field

from .inventory import Inventory, fingerprint, matches
from .manifests import json_object
from .records import content_url, file_record, relative_path
from .storage import cached_bytes


@dataclass
class Routes:
    outputs: dict = field(default_factory=dict)
    complete_urls: dict = field(default_factory=dict)


def resource_file(row):
    # 库洛使用 dest，核心消费 path；字段翻译集中保留字节大小与整文件摘要。
    return fingerprint({"path": row["dest"], "size": row["size"], "md5": row["md5"]})


def numeric_version(value):
    parts = value.split(".")
    if not all(part.isdecimal() for part in parts):
        return None
    return tuple(map(int, parts))


def check_direction(source, target):
    # 仅比较可解析数字版本。字符串版本仍按上游的原始值精确匹配补丁。
    old, new = numeric_version(source), numeric_version(target)
    if old is not None and new is not None and old > new:
        raise ValueError(f"本机分包 {source} 高于已选目标 {target}，请核对资源快照")


def patch_document(root, descriptor):
    digest = descriptor["md5"]
    raw = cached_bytes(root, digest)
    if raw is None:
        raise ValueError(f"固定提交中的更新索引缺失或损坏 {digest}")
    index = json_object(raw)
    # 删除列表或完整文件列表均可能单独构成一份补丁。
    keys = {"resource", "groupInfos", "deleteFiles"}
    valid_lists = all(isinstance(index.get(k, []), list) for k in keys)
    if keys.isdisjoint(index) or not valid_lists:
        raise ValueError("无法识别补丁索引的文件列表")
    return index


@dataclass
class PackPatch:
    name: str
    cdn: str
    descriptor: dict
    targets: dict
    local: Inventory
    routes: Routes
    plan: dict
    transports: dict = field(default_factory=dict)
    seen: set = field(default_factory=set)

    def target(self, item):
        record = resource_file(item)
        key = record["path"].casefold()
        expected = self.targets.get(key)
        # 尤其是 B 服 common，每个输出都必须出现在所选渠道、所选包的最终表中。
        if not matches(expected, record) or expected["pack"] != self.name:
            raise ValueError(f"补丁输出与 {self.name} 目标清单不符：{record['path']}")
        return fingerprint(expected)

    def add_transfer(self, row):
        # 补丁传输文件只进暂存区，正式游戏路径只来自目标包的完整清单。
        path = relative_path(row["dest"])
        if path.lower().endswith((".krpdiff", ".krdiff")):
            base = content_url(self.cdn, self.descriptor["base_path"])
            if path in self.transports:
                raise ValueError(f"补丁传输记录重复 {path}")
            self.transports[path] = file_record(row, base, self.name)
            return
        record = self.target(row)
        key = record["path"].casefold()
        # 完整文件优先采用该条目自己的 fromFolder；未提供时使用正式目标表的 URL。
        folder = row.get("fromFolder")
        url = self.targets[key]["url"]
        if folder:
            url = content_url(content_url(self.cdn, folder.rstrip("/")), path)
        self.routes.complete_urls[key] = url

    def outputs(self, group):
        # 多个分组同时写一个目标会使应用顺序决定最终结果，整单拒绝。
        records = [self.target(row) for row in group["dstFiles"]]
        keys = {row["path"].casefold() for row in records}
        if len(keys) != len(records) or not keys or not keys.isdisjoint(self.seen):
            raise ValueError("差分组输出为空或存在重复路径")
        self.seen.update(keys)
        return records

    def add_group(self, group):
        name = relative_path(group["dest"])
        transfer = self.transports[name]
        inputs = [resource_file(row) for row in group["srcFiles"]]
        outputs = self.outputs(group)
        if not inputs:
            raise ValueError("补丁缺少源文件")
        if not all(map(self.local.present, inputs)):
            return
        # 只有源内容全部匹配且可以减少传输字节时才选择该组，否则继续按整文件规划。
        if not self.use_group(outputs, transfer):
            return
        identifier = f"patch:{self.name}:{name}"
        self.plan["downloads"].append({"id": identifier, "kind": "patch", **transfer})
        self.plan["patches"].append({"download": identifier, "pack": self.name, "inputs": inputs, "outputs": outputs})
        self.routes.outputs.update({row["path"].casefold(): identifier for row in outputs})

    def use_group(self, outputs, transfer):
        # 显式完整文件的优先级高于差分输出，保留官方给出的完整文件目录。
        if any(row["path"].casefold() in self.routes.complete_urls for row in outputs):
            return False
        missing_size = sum(self.local.transfer_cost(row) for row in outputs if not self.local.present(row))
        return self.local.transfer_cost(transfer) < missing_size

    def translate(self, index):
        resources = index.get("resource", [])
        groups = index.get("groupInfos", [])
        deletions = index.get("deleteFiles", [])
        for row in resources:
            self.add_transfer(row)
        for group in groups:
            self.add_group(group)
        # 删除项只能指向目标已退休的路径。最终动作还要与核心回执的所有权求交。
        if any(relative_path(path).casefold() in self.targets for path in deletions):
            raise ValueError("补丁试图删除目标版本仍需要的文件")


def pack_strategy(pack, local, selection):
    # 同版本仍核验文件；缺失分包或缺少原版本补丁时，完整目标表可用于修复。
    name, target = pack["name"], pack["version"]
    source = local.receipt["packs"].get(name)
    if not source:
        return None, "uninstalled_pack"
    check_direction(source, target)
    if source == target:
        return None, "verify_current"
    if selection.full_files:
        return None, "full_files_requested"
    descriptor = selection.release["patches"][name].get(source)
    if not descriptor:
        return None, "no_matching_patch"
    if "krpdiff" not in local.capabilities:
        return None, "full_files"
    return descriptor, "matched_patch"


def plan_updates(selection, local, plan):
    # 同服才进入此路径。渠道切换永远不会借用原渠道某个同版本的差分。
    targets = {row["path"].casefold(): row for row in selection.manifest["files"]}
    strategies = {}
    for pack in selection.manifest["packs"]:
        name = pack["name"]
        descriptor, mode = pack_strategy(pack, local, selection)
        strategies[name] = mode
        if descriptor:
            # 每个包独立选择旧版本和传输目录，可以只更新已变更的包。
            patch = PackPatch(name, selection.release["cdns"][0], descriptor, targets, local, selection.routes, plan)
            patch.translate(patch_document(selection.root, descriptor))
    plan["pack_strategies"] = strategies
