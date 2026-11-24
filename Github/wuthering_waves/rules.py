"""把本资源包的风控要求转换成核心能执行的声明式动作。"""

from .records import relative_path


def disable_rule(rule):
    # 核心检查两个名字是否冲突，回执只登记本次真正完成的改名。
    path = relative_path(rule["path"])
    if path.casefold().endswith(".disable"):
        raise ValueError(f"已含 .disable 后缀的风控源路径 {path}")
    return {"action": "disable", "path": path, "target": path + ".disable"}


def valid_key(key):
    # 数组下标必须是非负整数。布尔值在 Python 中也是 int 的子类，需要区分。
    return isinstance(key, str) or (type(key) is int and key >= 0)


def field_rule(rule):
    keys = rule["key_path"]
    if not isinstance(keys, list) or not keys or not all(map(valid_key, keys)):
        raise ValueError("set_field 的 key_path 必须是非空键路径")
    if rule["format"] != "json":
        raise ValueError("set_field 当前支持 json 格式")
    # 保留 JSON 类型，核心遇到旧值或类型不匹配时报告冲突。
    return {"action": "set_field", "path": relative_path(rule["path"]), "format": "json",
            "key_path": keys, "from": rule["from"], "to": rule["to"]}


def text_rule(rule):
    old, new = rule["from"], rule["to"]
    if not isinstance(old, str) or not old or not isinstance(new, str):
        raise ValueError("replace_text 需要非空来源文本和字符串目标")
    mode = rule.get("match", "whole_line")
    count = rule.get("expected_matches", 1)
    if mode not in ("whole_line", "literal") or type(count) is not int or count < 1:
        raise ValueError("replace_text 的匹配方式或次数无效")
    # 允许不同后缀的文本文件，保留原编码、未命中的行和换行方式。
    return {"action": "replace_text", "path": relative_path(rule["path"]), "match": mode,
            "from": old, "to": new, "expected_matches": count}


def risk_actions(config, target):
    handlers = {"disable": disable_rule, "set_field": field_rule, "replace_text": text_rule}
    scopes = {"channels": target["channel"], "qualities": target["quality"], "versions": target["version"]}
    section = config.get("risk_control", {})
    rules = section.get("rules", [])
    actions = []
    for rule in rules:
        action = handlers[rule["action"]](rule)
        if applies_to(rule, scopes):
            actions.append(action)
    return actions


def applies_to(rule, scopes):
    # 未指定范围表示所有发布都适用。拒绝拼错的范围名，避免扩大规则的适用面。
    common = {"action", "path", "channels", "qualities", "versions"}
    fields = {"disable": set(), "set_field": {"format", "key_path", "from", "to"},
              "replace_text": {"match", "from", "to", "expected_matches"}}
    unknown = rule.keys() - common - fields[rule["action"]]
    if unknown:
        raise ValueError(f"未知风控字段 {sorted(unknown)}")
    # 先验证全部条件，某个渠道不匹配也不会掩盖下一项格式错误。
    selections = [scope_match(key, value, rule) for key, value in scopes.items()]
    return all(selections)


def scope_match(key, value, rule):
    options = rule.get(key, [value])
    if not isinstance(options, list) or not all(isinstance(option, str) for option in options):
        raise ValueError(f"风控范围 {key} 应为字符串列表")
    return value in options
