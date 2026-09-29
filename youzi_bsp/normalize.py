"""三层归一（报告 3.4）：URL 级 → 号码级 → 实体级。

轮子：phonenumbers（号码）、tldextract（注册域）。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, unquote

import tldextract

# ponytail: suffix_list_urls=() 用内置 PSL 快照，离线确定性（测试不依赖网络）；
# include_psl_private_domains=True 才会把 myshopify.com 等 PSL 私有段标为 is_private
# （默认不含私有段——平台后缀回退逻辑依赖它）。要最新 PSL 时去掉 suffix 参数即可。
_EXTRACT = tldextract.TLDExtract(suffix_list_urls=(), include_psl_private_domains=True)

_UTM_RE = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$)", re.I)
_SEPARATORS = re.compile(r"[\s\-().–—]")


def normalize_url(url: str) -> str | None:
    """小写 host、去 fragment、剥 UTM、统一尾斜杠。非法输入返回 None。"""
    if not isinstance(url, str) or not url:
        return None
    # 拒 ASCII 控制字符（\x00-\x1f, \x7f）——URL 不该含这些，浏览器/服务器也会拒
    if any(ord(c) < 0x20 or ord(c) == 0x7f for c in url):
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    # 端口必须保留（P2 修复：hostname 重建 netloc 曾把 :8443 种子打到 443 错 origin）；
    # 默认端口（80/443）规范化去除——:443 与裸 https 保持指纹同源。
    # parts.port 对畸形端口（host:abc）抛 ValueError，与 urlsplit 同防线处理。
    try:
        port = parts.port
    except ValueError:
        return None
    netloc = parts.hostname.lower()
    if port is not None and port not in (80, 443):
        netloc = f"{netloc}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    query = urlencode(
        [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
         if not _UTM_RE.match(k)]
    )
    return urlunsplit((parts.scheme, netloc, path, query, ""))


def normalize_phone(raw: str, imply_plus: bool = False) -> tuple[str, str | None] | None:
    """宽松进严格出：清洗 → E164 → libphonenumber 有效性。返回 (e164, country) 或 None。

    imply_plus=True 用于链接层（wa.me/8613800138000 按规范无 + 前缀，补 + 解析）；
    文本层保持 False —— 只收 +/00 开头的国际格式，控误报（报告 9.2 最大风险点）。

    C1 fix：raw=None/非字符串直接返 None，不抛 TypeError。
    """
    if not isinstance(raw, str):
        return None
    import phonenumbers

    s = unquote(raw).strip()
    if s.startswith("00"):
        s = "+" + s[2:]
    if not s.startswith("+") and imply_plus:
        s = "+" + s
    s = _SEPARATORS.sub("", s)
    digits = s.lstrip("+")
    if not (s == "+" + digits and digits.isdigit()):
        return None
    if not 8 <= len(digits) <= 15:
        return None
    try:
        num = phonenumbers.parse(s, None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    e164 = phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)
    return e164, phonenumbers.region_code_for_number(num)


def entity_key(host: str) -> str:
    """eTLD+1；平台公共后缀（myshopify.com/blogspot.com 在 PSL 私有段）回退完整 host。"""
    host = (host or "").lower().rstrip(".")
    if not host:
        return host
    ext = _EXTRACT(host)
    if getattr(ext, "is_private", False):
        return host  # PSL 私有段：注册域是平台本身，实体键必须到 host 粒度
    if ext.domain and ext.suffix:
        return f"{ext.domain}.{ext.suffix}"
    return host
