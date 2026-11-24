"""读写单款游戏的资源版号，保留维护者编写的其余 TOML 原文。"""

import re
import tomllib


COMPONENT = r"(?:0|[1-9][0-9]{0,19})"
RELEASE = re.compile(rf"{COMPONENT}\.{COMPONENT}\.{COMPONENT}")
# 三引号版号可以跨行；普通标量在行尾或行内注释之前结束。
SCALAR = r"(?:\"\"\"[\s\S]*?\"\"\"|'''[\s\S]*?'''|[^#\r\n]+?)"
ASSIGNMENT = re.compile(
    rf"""(?m)^[ \t]*(?:version|"version"|'version')[ \t]*=[ \t]*(?P<value>{SCALAR})(?=[ \t]*(?:#|\r?$))"""
)


def version_parts(value):
    # 旧 TOML 整数使用有符号 64 位；三段文本与客户端 SemVer 的无符号范围一致。
    if type(value) is int and 0 <= value < 2**63:
        return value, 0, 0
    if not isinstance(value, str) or not RELEASE.fullmatch(value):
        raise ValueError(f"游戏资源版号无效：{value!r}")
    parts = tuple(map(int, value.split(".")))
    if max(parts) >= 2**64:
        raise ValueError(f"游戏资源版号超出范围：{value}")
    return parts


def next_version(value):
    major, minor, patch = version_parts(value)
    # 补丁位只加一，到达上限时保留原件并报告失败。
    if patch == 2**64 - 1:
        raise ValueError("游戏资源补丁版号已达上限")
    return f"{major}.{minor}.{patch + 1}"


def matches_config(text, expected):
    # 多行 TOML 字符串可以包含形似赋值的文本，试改失败时继续寻找真正的根字段。
    try:
        return tomllib.loads(text) == expected
    except tomllib.TOMLDecodeError:
        return False


def bumped_config(source):
    # 按字节读取以保留文件原有换行符。
    original = source.read_bytes().decode("utf-8")
    config = tomllib.loads(original)
    version = next_version(config["version"])
    # 只替换根版号的值。重新解析后核对整棵配置，避免误改注释、字符串或渠道里的同名键。
    for match in ASSIGNMENT.finditer(original):
        start, end = match.span("value")
        candidate = original[:start] + f'"{version}"' + original[end:]
        if matches_config(candidate, {**config, "version": version}):
            return candidate.encode("utf-8")
    raise ValueError("无法定位游戏 sources.toml 的根版号")
