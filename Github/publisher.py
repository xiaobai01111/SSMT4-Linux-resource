"""读取发布方的 PC 元数据，并检查规范化记录中使用的标识。"""

import gzip
import io
import json
import re
from http.client import HTTPException
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


def https(url):
    # 统一检查组装前的源地址与实际响应地址，避免保存带登录信息的 CDN 来源。
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.fragment:
        raise ValueError("上游地址必须为无登录信息的 HTTPS 地址")
    return parsed


def public_url(url):
    # 请求构建接口的参数与 CDN 资源地址分开保存，交付清单只收录长期公开地址。
    if https(url).query:
        raise ValueError("资源地址包含查询参数，无法作为固定 CDN 地址交付")
    return url


def name(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value):
        raise ValueError("上游标识含非法路径字符")
    return value


def url_name(url):
    leaf = unquote(https(url).path.rsplit("/", 1)[-1])
    # HoYoPlay 的旧版语音 ZIP 名称可带括号，但解码后仍必须是单个相对路径分量。
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9 ._()-]*", leaf):
        raise ValueError("上游归档文件名不适合本地路径")
    return leaf


def byte_count(value):
    # 发布方有时以十进制字符串表示超过 32 位的值，避免浮点转换丢失精度。
    if type(value) is str and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 0 <= value < 2**64:
        raise ValueError("上游大小须为无符号字节数")
    return value


def md5(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Fa-f0-9]{32}", value):
        raise ValueError("上游记录缺少有效 MD5")
    return value.lower()


def bounded_read(stream):
    limit = 32 << 20
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("上游元数据超过 32 MiB")
    return data


def read_response(request):
    with urlopen(request, timeout=20) as response:
        https(response.geturl())
        return bounded_read(response)


def transient(error):
    if isinstance(error, HTTPError):
        return error.code in (408, 500, 502, 503, 504)
    return True


def request_bytes(request):
    endpoint = https(request.full_url)
    # 构建和差分查询均只读。短暂连接故障重试一次，权限错误直接保留旧快照。
    for _ in range(2):
        try:
            return read_response(request)
        except (OSError, HTTPException) as error:
            failure = type(error).__name__
            if not transient(error):
                break
    # 故障日志只标识接口，不回显随请求发送的构建参数。
    raise OSError(f"{endpoint.hostname}{endpoint.path} 请求失败（{failure}）") from None


def document(url, *, method="GET"):
    request = Request(url, method=method, headers={"Accept-Encoding": "gzip, identity"})
    raw = request_bytes(request)
    data = json.loads(decode_body(raw))
    if not isinstance(data, dict):
        raise ValueError("上游元数据须为 JSON 对象")
    return data


def decode_body(raw):
    # 有些端点返回 gzip 文件，有些返回普通 JSON。传输与解压均受相同的大小限制。
    if not raw.startswith(b"\x1f\x8b"):
        return raw
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
            return bounded_read(stream)
    except EOFError as error:
        raise ValueError("上游 gzip 元数据在传输中被截断") from error
