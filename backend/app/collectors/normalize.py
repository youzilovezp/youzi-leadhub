"""归一化：电话 E.164 / 域名 / 公司名 → dedupe_key。

dedupe_key 是去重的根基，规则（需求文档已定）：
    优先级 domain > phone_e164 > md5(归一化名称+城市)
误判即误合并，所以这里只做「保守正确」的归一化，解析不了就不参与去重键。
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from urllib.parse import urlparse

import phonenumbers
from tldextract import extract as tld_extract

# 常见法律后缀（东南亚/中东/拉美目标市场为主）——剥掉后才比较公司名
_LEGAL_SUFFIXES = (
    "sdn bhd",
    "sdn.bhd",
    "sendirian berhad",
    "berhad",
    "bhd",
    "pt",
    "persero",
    "tbk",
    "ptc",
    "pte ltd",
    "pte",
    "ltd",
    "llp",
    "llc",
    "inc",
    "co",
    "corp",
    "corporation",
    "company",
    "co ltd",
    "coltd",
    "gmbh",
    "ag",
    "bv",
    "nv",
    "sa",
    "srl",
    "pty",
    "jqsc",
    "wll",
    "kft",
)
_SUFFIX_RE = re.compile(
    # 空格 → [\s.,]*：容忍 "Sdn. Bhd." / "Sdn Bhd" / "SDN_BHD" 等写法
    r"\b(" + "|".join(re.escape(s).replace(r"\ ", r"[\s.,]*") for s in _LEGAL_SUFFIXES) + r")\b\.?",
    re.IGNORECASE,
)
# 中文组织形态后缀（长词在前防「分公司」被「公司」截断）；CJK 全是 \w，
# \b 在汉字之间不成立，改用尾部锚定。多轮剥离处理「集团有限公司」叠形态。
_CN_SUFFIX_RE = re.compile(
    r"(?:股份有限公司|有限责任公司|有限公司|集团公司|总公司|分公司|控股公司|集团|公司)$"
)
_PUNCT_RE = re.compile(r"[^\w\s]+")
_WS_RE = re.compile(r"\s+")


@lru_cache(maxsize=10000)
def normalize_phone(raw: str | None, region: str | None = None) -> str | None:
    """电话 → E.164（+6012345678）。解析失败返回 None（不参与去重键）。"""
    if not raw:
        return None
    raw = raw.strip()
    if not raw:
        return None
    # 先按来源国 region 解析；失败再试无 region（号码自带 + 国家码的情况）
    for attempt_region in ([region] if region else []) + [None]:
        try:
            parsed = phonenumbers.parse(raw, attempt_region)
        except phonenumbers.NumberParseException:
            continue
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return None


@lru_cache(maxsize=10000)
def extract_domain(value: str | None) -> str | None:
    """URL 或域名 → registrable domain（example.com.sg 级别，含公共后缀）。

    tldextract 基于公共后缀表（PSL），www. / 路径 / 参数全部剥掉。
    解析不出 registrable domain 返回 None。
    """
    if not value:
        return None
    value = value.strip()
    if not value or "@" in value:  # 邮箱不是域名
        return None
    if "://" not in value:
        value = "http://" + value
    host = urlparse(value).netloc.split("@")[-1].split(":")[0].lower()
    if not host:
        return None
    ext = tld_extract(host)
    if not ext.domain or not ext.suffix:
        return None
    return f"{ext.domain}.{ext.suffix}"


@lru_cache(maxsize=10000)
def normalize_company_name(name: str | None) -> str | None:
    """公司名 → 小写 + 去标点 + 剥法律后缀（拉丁 + 中文组织形态）。"""
    if not name:
        return None
    s = name.strip().lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _SUFFIX_RE.sub(" ", s)
    # 中文后缀剥离：循环至稳定（≤3 轮足够「集团有限公司」级叠加）
    for _ in range(3):
        stripped = _CN_SUFFIX_RE.sub("", s).strip()
        if stripped == s:
            break
        s = stripped
    s = _WS_RE.sub(" ", s).strip()
    return s or None


def make_dedupe_key(
    *,
    website: str | None = None,
    phone_raw: str | None = None,
    phone_e164: str | None = None,
    name: str | None = None,
    city: str | None = None,
    region: str | None = None,
) -> str | None:
    """生成去重键。三者全空返回 None（无法去重，调用方应跳过或用随机键）。"""
    domain = extract_domain(website)
    if domain:
        return f"domain:{domain}"
    tel = phone_e164 or normalize_phone(phone_raw, region)
    if tel:
        return f"tel:{tel}"
    norm_name = normalize_company_name(name)
    if norm_name:
        digest = hashlib.md5(f"{norm_name}|{(city or '').strip().lower()}".encode()).hexdigest()
        return f"namecity:{digest}"
    return None
