"""website_enrich 采集器：对库里有网站的线索批量检测 WhatsApp / 邮箱 / 社媒 / 场景 / SaaS 需求。

不是独立采集源——直接改存量 Lead 行：
    - WhatsApp 插件/链接指纹：wa.me、api.whatsapp.com、ht-ctc / joinchat / getbutton /
      chaty / elfsight 等常见插件
    - 公开邮箱（mailto 优先）、社媒链接（FB/IG/LinkedIn/TG/TikTok）
    - WhatsApp 场景（客服/营销/交易/SaaS）与 SaaS 需求信号（CRM/工单/Chatbot…）
      的关键词识别（collectors/scenes.py）
    - 抓到公开邮箱 → 自动生成「待补全」联系人（crud/contact.py）
    - 请求预算：首页 ≤4 次（双通道+换 scheme+宽松 SSL，反爬站再加指纹/渲染
      兜底）；内页 ≤3 页与首页同等待遇（联系类失败上指纹层，SPA 壳上渲染层）；
      首页无联系页时探测惯例路径（≤16 个，命中即止）；全部落空才取 ≤2 个同域
      首页链接兜底（老式 asp-bin 站）
    - 24h 跳过以成功为准：只在富化成功时写 enriched_at，失败/超时下次重跑

任务范围（文档定的）：params.lead_ids 指定（列表勾选入口）；不指定 = 全库
「有网站 且 24h 内未成功富化」。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy import select as _sa_select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.base import Collector, TaskContext
from app.collectors.normalize import extract_domain
from app.collectors.overseas import detect_domain_tld, detect_overseas_signals
from app.collectors.scenes import (
    SAAS_LABELS_ZH,
    SCENE_LABELS_ZH,
    detect_saas_signals,
    detect_scenes,
    page_text,
)
from app.core.config import settings

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TIMEOUT = httpx.Timeout(15.0, connect=10.0)

# WhatsApp 指纹：直链 + 常见 WordPress/SAAS 聊天插件特征串
_WHATSAPP_PATTERNS = [
    re.compile(r"(?:https?://)?wa\.me/(\+?\d{6,15})", re.IGNORECASE),
    # send 链接的三个子域形态：api（插件标准）/ wp（短链跳转）/ web（人工从
    # WhatsApp 里复制的分享链接——2026-09-01 实测 mugroup.com 漏检根因：
    # web 子域没覆盖，导致 hit=True 却捕获不到号码，wa_url/号码/自动联系人全空）
    re.compile(
        r"(?:https?://)?(?:api|web|wp)\.whatsapp\.com/send[^\s\"'<>]*?phone=(\+?\d{6,15})",
        re.IGNORECASE,
    ),
    re.compile(r"whatsapp[^a-z0-9]{0,20}(?:send|chat|message)", re.IGNORECASE),
    # 插件 token 恒小写 + 词边界，**大小写敏感**：裸词会打中驼峰标识符——
    # ecovacs.com 实锤 `captchaType` 含 chaTy / `getButtonType` 含 getbutton /
    # `joinChat()`，误评 site_whatsapp +25（2026-09-01 探针实证）
    re.compile(r"\b(?:ht-ctc|joinchat|getbutton|chaty|elfsight|click-to-chat|whatsapp-chat)\b"),
]
# 群组邀请链接（PRD §4.1）：chat.whatsapp.com/xxx = 已在运营 WhatsApp 社群（私域证据）
_GROUP_LINK_RE = re.compile(r"https?://chat\.whatsapp\.com/[A-Za-z0-9_-]{5,}")
# 命中前 2 条之一的捕获组 → 拿到号码还原标准链接；插件指纹命中则只置标记
_PHONE_PATTERNS = _WHATSAPP_PATTERNS[:2]
# 插件特征（末条）：ht-ctc/joinchat/getbutton/chaty/elfsight/click-to-chat
_PLUGIN_PATTERNS = _WHATSAPP_PATTERNS[3:]

# WhatsApp Business 使用代理判定（§4.1「号码类型/入口形态」）：页面自述在用
# WhatsApp Business（业务号而非个人号）——文本或类属性出现即认为命中
_WA_BUSINESS_RES = [
    re.compile(r"whatsapp\s+business", re.I),
    re.compile(r"wa\s+business\s+(?:account|number|api)", re.I),
    re.compile(r"whatsapp\s*商业号|whatsapp\s*企业号", re.I),
]

# ICP 备案号（中国网站强证据）：页脚「京ICP备12345678号-1」等——纯英文站的
# 中国出海企业常保留备案信息，是防误杀的关键 CN 证据
_ICP_LICENSE_RE = re.compile(
    r"[京沪津渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川黔滇藏陕甘青宁新港澳]"
    r"ICP[备证]\s*\d{6,10}\s*号(?:-\d{1,3})?"
)


def detect_icp_license(html_list: list[str] | list[str | None] | None) -> str | None:
    """页面是否含 ICP 备案号（中国企业强证据）。返回命中的备案号原文。"""
    if not html_list:
        return None
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return None
    m = _ICP_LICENSE_RE.search(joined)
    return m.group(0) if m else None


_TEXT_PHONE_RE = re.compile(r"\+\d{1,3}[\s\-]?(?:\d[\s\-]?){7,12}\d")
# 400 热线（中国企业客服标配形态，误报极低）与国内座机（0xx-xxxxxxxx）
# 2026-09-01 用户反馈「连最基础的电话都没有」——此前只认国际前缀明文，
# 而国内官网联系页九成是 400/座机。校验交给 phonenumbers（region=CN）
# 官网归属校验（2026-09-01）：错配官网的实测形态——站点是知名平台/产品站
# （酷狗音乐/QQ邮箱/汉典词典等），标题带明显品牌词而与公司名零重叠。这类
# 「张冠李戴」不清除的话，从错站抓的邮箱/电话/信号全是别人的数据
_SITE_BRAND_DISTRACTORS: tuple[str, ...] = (
    "酷狗",
    "酷我",
    "qq音乐",
    "QQ音乐",
    "汉典",
    "百度",
    "京东",
    "淘宝",
    "天猫",
    "拼多多",
    "知乎",
    "哔哩",
    "bilibili",
    "抖音",
    "快手",
    "网易",
    "新浪",
    "搜狐",
    "腾讯",
    "qq邮箱",
    "qq 邮箱",
    "微信",
    "支付宝",
    "携程",
    "美团",
    "饿了么",
    "高德",
    "滴滴",
    "哈啰",
    "下载",
    "下载站",
    "win10",
    "windows",
    "android",
    "apk",
)
_CN_MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d[-\s]?\d{4}[-\s]?\d{4}(?!\d)")
# 具名联系人（2026-09-01 kaadas 实测金矿）：页面「XX部门（业务） 联系人：屈先生
# （负责人） 联系电话：189-2522-1831」——人名+部门+手机号 = 「找谁」的最优答案
_CONTACT_PERSON_RE = re.compile(
    r"([一-鿿A-Za-z&/（）()、·]{2,24})?\s*(?:联系人|联系人:|contact)\s*[:：]?\s*"
    r"([一-鿿·]{2,6})(?:[（(]([^）)]{1,12})[）)])?\s*"
    r"(?:联系电话|联系方式|电话|mobile|tel)\s*[:：]?\s*"
    r"((?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4})",
    re.I,
)
# 第 5 形态（2026-09-01 Shoptop 实测，用户报「联系信息爬取不对」）：无
# 「联系人：」前缀，直接「城市 Name Tel：17891981788 微信同号」——地区销售/
# 代理建站服务商的常见写法；名字可为拉丁（Lisa/Akon）或中文（李伟），
# 手机可带分隔（178-9198-1788），微信同号=手机即微信号（对销售是关键信息）
_CONTACT_PERSON_TEL_RE = re.compile(
    r"(?P<ctx>[一-鿿][一-鿿/·、]{0,11})?\s*"
    r"(?P<name>[A-Za-z][A-Za-z .\-]{1,23}|[一-鿿·]{2,4})"
    r"\s*(?:联系电话|联系方式|电话|手机|mobile|tel)\s*[:：]?\s*"
    r"(?P<phone>(?:\+?86[-\s]?)?1[3-9]\d[-\s]?\d{4}[-\s]?\d{4})"
    r"(?:\s*(?P<wechat>微信同号|微信号同号))?",
    re.I,
)
# 不是人名的词（客服/电话/致电…）——第 5 形态没有「联系人：」锚，靠词表防误判。
# 职务词（经理/主管…）不进这里（search 语义会误杀「张经理」），裸职务由
# _CONTACT_TITLE_WORD_RE 全词匹配兜住（2026-09-01 审计：name=「经理」的假联系人）
_CONTACT_NAME_JUNK_RE = re.compile(
    r"客服|电话|咨询|联系|我们|热线|致电|服务|销售|商务|市场|技术|支持|微信|同号"
    r"|邮箱|地址|传真|时间|工作|更多|详情|留言|提交|tel|phone|mobile|contact|wa$|^qq",
    re.I,
)
_CONTACT_TITLE_WORDS = "经理|主管|总监|部长|负责人|专员|助理|课长|组长|队长"
_CONTACT_TITLE_WORD_RE = re.compile(rf"^(?:{_CONTACT_TITLE_WORDS})$")
# 「部门+姓+职务」连写（商务部张经理）修复：贪婪 ctx 吃掉姓、name 落到裸职务
# 词或被 keyword 撕碎（销售张总监 联系电话→name=联系）——ctx 尾部两种形态都
# 把「姓+职务」还给人名
_CTX_TITLE_TAIL_RE = re.compile(
    rf"^(?P<rest>.*?)(?P<surname>[一-鿿])(?P<title>{_CONTACT_TITLE_WORDS})$"
)


def _norm_cn_mobile(phone: str) -> str | None:
    """手机号原文 → 11 位数字（剥 +86/分隔）；不合法返回 None。"""
    digits = re.sub(r"[\s\-+]", "", phone)
    if len(digits) > 11 and digits.startswith("86"):
        digits = digits[2:]
    return digits if re.fullmatch(r"1[3-9]\d{9}", digits) else None


def detect_contact_persons(html_list: list[str]) -> list[dict[str, str]]:
    """页面上的具名联系人 → [{name, title, phone}]（title=部门/角色/地区，可空）。

    覆盖三种实测形态（联系方式是一级产出，宁可多识别一个形态不能漏一种写法）：
    - kaadas：「海外事业部（加盟合作/OEM/ODM） 联系人：屈先生 联系电话：189-2522-1831」
    - Shoptop：「上海 Lisa Tel：17891981788 微信同号」（城市+拉丁名+Tel+微信同号）
    - 中文名无前缀：「李伟 电话：138-1234-5678」
    """
    text = page_text(html_list, keep_case=True)
    out: list[dict[str, str]] = []

    def _add(name: str, title: str, phone_digits: str) -> None:
        entry = {"name": name, "title": title[:60], "phone": phone_digits}
        if not any(e["name"] == name and e["phone"] == phone_digits for e in out):
            out.append(entry)

    for m in _CONTACT_PERSON_RE.finditer(text):
        dept, name, role, phone = (
            (m.group(1) or "").strip(),
            m.group(2).strip(),
            (m.group(3) or "").strip(),
            m.group(4),
        )
        digits = _norm_cn_mobile(phone)
        if not digits:
            continue
        _add(name, " ".join(x for x in (dept, role) if x), digits)

    for m in _CONTACT_PERSON_TEL_RE.finditer(text):
        ctx, name, phone, wechat = (
            (m.group("ctx") or "").strip(),
            m.group("name").strip(),
            m.group("phone"),
            m.group("wechat"),
        )
        digits = _norm_cn_mobile(phone)
        # 「部门+姓+职务」连写修复：name 是裸职务词/被 keyword 撕碎（=junk）时，
        # 看 ctx 尾——「姓+职务」整体（销售张总监）或只剩姓（商务部张+经理）
        if ctx and (_CONTACT_NAME_JUNK_RE.search(name) or _CONTACT_TITLE_WORD_RE.fullmatch(name)):
            mt = _CTX_TITLE_TAIL_RE.match(ctx)
            if mt:
                name, ctx = mt["surname"] + mt["title"], mt["rest"].strip()
            elif _CONTACT_TITLE_WORD_RE.fullmatch(name):
                name, ctx = ctx[-1] + name, ctx[:-1].strip()
        # 词表里的客服/电话类词与裸职务词不是人名；ctx 混入 微信/同号 等残留时丢弃
        if (
            not digits
            or _CONTACT_NAME_JUNK_RE.search(name)
            or _CONTACT_TITLE_WORD_RE.fullmatch(name)
        ):
            continue
        if any(w in ctx for w in ("微信", "同号", "电话", "联系")):
            ctx = ""
        title = " ".join(x for x in (ctx, "微信同号" if wechat else "") if x)
        _add(name, title, digits)
    return out[:8]


_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)


def site_matches_company(homepage_html: str, company_name: str) -> tuple[bool, str]:
    """首页标题与公司名的一致性粗判：返回 (是否通过, 站点标题)。

    判「错配」的条件（保守——两证齐全才判错）：
    ① 标题含知名平台/产品品牌词（干扰词表）且 ② 公司名主词不出现在标题里。
    只缺一证不判（凯越 vs "MU Group" 字面零重叠但不是错配——宁存疑不误杀）。
    """
    m = _TITLE_RE.search(homepage_html or "")
    title = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else ""
    if not title:
        return True, title  # 拿不到标题不判错
    core = re.sub(
        r"(股份有限公司|有限责任公司|有限公司|集团公司|集团)", "", company_name or ""
    ).strip()
    name_hit = bool(core) and any(core[i : i + 2] in title for i in range(max(1, len(core) - 1)))
    distractor = next((d for d in _SITE_BRAND_DISTRACTORS if d.lower() in title.lower()), "")
    if distractor and not name_hit:
        return False, title
    # v2（2026-09-01 呜噜网实测）：标题是短中文品牌身份（≤6 个汉字，如「呜噜网」）
    # 且与中文公司名零重叠 → 错配。只限短品牌：长标题多为产品口号（无身份主张），
    # 英文品牌站（凯越→MU Group）不触发——防误杀
    if not name_hit and _has_cjk(core) and 2 <= _cjk_len(title) <= 6:
        return False, title
    return True, title


def _has_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text or "")


def _cjk_len(text: str) -> int:
    return sum(1 for ch in text or "" if "一" <= ch <= "鿿")


_400_HOTLINE_RE = re.compile(r"(?<!\d)400-?\d{3,4}-?\d{3,4}(?!\d)")
_CN_LANDLINE_RE = re.compile(r"(?<!\d)0\d{2,3}[-\s]?\d{3,4}[-\s]?\d{4}(?!\d)")


def detect_text_phones(html_list: list[str]) -> list[str]:
    """页面明文国际格式电话（如「+86 137 3602 8159」）→ 候选串列表。

    只认带国际区号前缀的明文（+86/+65/+91…）——裸座机号误报面大不碰；
    候选串先抓宽（分隔符随意），有效性交给 phonenumbers（libphonenumber
    官方移植，号码合法性业界标准）在调用侧校验归一。
    """
    from app.collectors.scenes import page_text

    text = page_text(html_list, keep_case=True)
    spans: list[tuple[int, int]] = []
    out: list[str] = []
    for pattern in (_TEXT_PHONE_RE, _400_HOTLINE_RE, _CN_LANDLINE_RE, _CN_MOBILE_RE):
        for m in pattern.finditer(text):
            # span 去重：+86 137... 的国际命中已覆盖其中的手机段，不重复产出
            if any(a <= m.start() and m.end() <= b for a, b in spans):
                continue
            spans.append((m.start(), m.end()))
            v = m.group(0).strip()
            if v not in out:
                out.append(v)
    return out


_JSONLD_RE = re.compile(
    r"<script[^>]*type=[\"\']application/ld\+json[\"\'][^>]*>(.*?)</script>", re.S | re.I
)


def detect_jsonld_contacts(html_list: list[str]) -> dict[str, str]:
    """schema.org JSON-LD 里声明的联系方式（Organization/LocalBusiness 标准字段）。

    借标准格式而非引库：stdlib json 即可解析（extruct 等结构化库在本项目两个
    实测失败页面上无联系字段可挖，2026-09-01 验证）。声明即权威——命中时
    优先于正则启发（网站主自己写的机器可读数据，置信度最高）。
    """
    out: dict[str, str] = {}
    joined = "\n".join(h for h in html_list if h)
    for block in _JSONLD_RE.finditer(joined):
        try:
            data = json.loads(block.group(1).strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else [data]
        for it in items:
            if not isinstance(it, dict):
                continue
            addr = it.get("address")
            if isinstance(addr, dict):
                parts = [
                    addr.get(k)
                    for k in ("streetAddress", "addressLocality", "addressRegion", "addressCountry")
                ]
                addr_text = " ".join(str(x) for x in parts if x)
            elif isinstance(addr, str):
                addr_text = addr
            else:
                addr_text = ""
            for key, val in (
                ("phone", it.get("telephone")),
                ("email", it.get("email")),
                ("address", addr_text),
            ):
                if val and isinstance(val, str) and not out.get(key):
                    out[key] = val.strip()
    return out


def backfill_profile_fields(
    lead: Any,
    *,
    icp_license: str | None,
    pages: list[str],
    now: Any,
) -> bool:
    """基础画像补全（2026-09-01 用户反馈：web_search 线索基础信息大面积空白）。

    - country：来源没给国家、但本轮拿到 CN 硬证据（ICP 备案号）或既有强证据
      （+86/中国招聘站/种子）→ CN。仅 CJK 启发式（弱证据）**不**回填——
      东南亚华人本地企业同样命中，回填即误标
    - industry：空则按公司名五类归类回填中文标签（粗粒度但非空；来源已给的不覆盖）
    - address：页面「地址 / Address」行，抓到才补，没有不硬造
    返回是否补了任一字段（调用方无需额外处理，field_meta 已在函数内记录）。
    """
    from app.collectors.icp import cn_evidence_of_lead
    from app.collectors.industry_labels import INDUSTRY_GROUP_LABELS_ZH, industry_group_of
    from app.collectors.scenes import page_text
    from app.crud.lead import touch_field_meta

    touched = False
    if not lead.country and lead.is_cn and (icp_license or cn_evidence_of_lead(lead) == "strong"):
        lead.country = "CN"
        touch_field_meta(lead, "country", "website_enrich", confidence=85, now=now)
        touched = True
    if not lead.industry:
        group = industry_group_of(None, lead.name)
        if group:
            lead.industry = INDUSTRY_GROUP_LABELS_ZH[group]
            touch_field_meta(lead, "industry", "website_enrich", confidence=60, now=now)
            touched = True
    if not lead.address:
        m = re.search(
            r"(?:地\s*址|address\s*\d|address|adress\s*\d|adress|addr)\s*[：:.]?\s*"
            r"([一-鿿a-zA-Z0-9.,·\-（）()#/&\s]{8,120}?)"
            r"(?=\s*(?:address|adress|地址|电话|邮箱|传真|邮编|地图|tel|fax|email|map|$))",
            page_text(pages, keep_case=True),
            re.IGNORECASE,
        )
        if m and (addr := m.group(1).strip()):
            lead.address = addr
            touch_field_meta(lead, "address", "website_enrich", confidence=70, now=now)
            touched = True
    return touched


def detect_cn_content(html_list: list[str] | list[str | None] | None) -> bool:
    """页面是否以中文为主（中国企业官网特征）：可见文本 CJK 占比 ≥ 30%。"""
    joined = page_text(html_list)
    if not joined:
        return False
    cjk = sum(1 for ch in joined if "一" <= ch <= "鿿")
    return cjk / len(joined) >= 0.30


def detect_wa_business(html_list: list[str] | list[str | None] | None) -> bool:
    """页面是否自述使用 WhatsApp Business（业务号）。"""
    if not html_list:
        return False
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return False
    return any(rx.search(joined) for rx in _WA_BUSINESS_RES)


def detect_whatsapp_groups(html_list: list[str] | list[str | None] | None) -> list[str]:
    """页面里的 WhatsApp 群邀请链接（去重保序）——私域运营证据。"""
    if not html_list:
        return []
    joined = "\n".join(h for h in html_list if h)
    groups: list[str] = []
    for m in _GROUP_LINK_RE.finditer(joined):
        url = m.group(0)
        if url not in groups:
            groups.append(url)
    return groups


_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_SOCIAL_RES = [
    ("facebook", re.compile(r"https?://(?:www\.)?facebook\.com/[^\s\"'<>]+", re.I)),
    ("instagram", re.compile(r"https?://(?:www\.)?instagram\.com/[^\s\"'<>]+", re.I)),
    ("linkedin", re.compile(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[^\s\"'<>]+", re.I)),
    ("telegram", re.compile(r"https?://(?:t\.me|telegram\.me)/[^\s\"'<>]+", re.I)),
    ("tiktok", re.compile(r"https?://(?:www\.)?tiktok\.com/@[^\s\"'<>]+", re.I)),
    (
        "youtube",
        re.compile(r"https?://(?:www\.)?youtube\.com/(?:c|channel|user|@)[^\s\"'<>]+", re.I),
    ),
]
# Contact/About/Products 三类内页（官网四层抓取：首页 + 联系/关于/产品页）。
# Products 层是 B2B/B2C/品类与交易场景关键词的主要来源（跨境电商站尤其）。
# 关键词匹配 href **或锚文本**（2026-08-31 审计：中文官网的招聘/联系栏目
# 常是中文锚文本 + 拼音/数字路径，只匹配英文 href 词会漏掉大半内页）
_INNER_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href=["\']([^"\']{1,300})["\'][^>]*>(.*?)</a>', re.S | re.I
)
# 联系类内页关键词（优先级 1——联系方式是富化的第一产出）
_INNER_CONTACT_WORDS_RE = re.compile(r"contact|kontak|hubungi|联系|about|关于|faq|help", re.I)
_INNER_PAGE_WORDS_RE = re.compile(
    r"contact|kontak|about|hubungi|product|shop|store|catalog|collection|faq|help"
    r"|联系我们|联系方式|联系|关于我们|关于|产品|商品|店铺",
    re.I,
)
# 内页抓取上限（首页 + 最多 3 个内页；原来只 2 个联系页）
_MAX_INNER_PAGES = 3


def _page_key(url: str) -> str:
    """内页去重键：www 剥离 + 尾斜杠归一 + query 保留。

    mugroup.com 实测：首页同时挂 www.mugroup.com/who-we-are/ 与
    mugroup.com/who-we-are/ 两个写法，字符串去重认不出同页——白烧 3 个
    内页名额之一（2026-09-01 探针实证）。
    """
    try:
        u = httpx.URL(url)
    except Exception:  # noqa: BLE001  病态 URL 原样参与比较
        return url
    host = (u.host or "").removeprefix("www.")
    path = (u.path or "").rstrip("/")
    query = f"?{u.query.decode()}" if u.query else ""
    return f"{host}{path}{query}"


def find_inner_page_urls(homepage_html: str, base_url: str, base_domain: str | None) -> list[str]:
    """首页 HTML → 同域内页 URL 列表（≤_MAX_INNER_PAGES，联系页优先）。

    命中 = 内页关键词出现在 href **或** 锚文本里（中文锚文本「联系我们/
    产品中心」与英文 href /contact 一并覆盖）。**分两级选取**：联系类
    （contact/联系/关于/faq）优先于产品类（product/shop/店铺）——
    2026-09-01 TMO 实测：导航栏产品/服务链接在 DOM 里先出现，旧的先到先得
    + 3 页上限把 /contact/ 挤掉，电话全漏（联系方式是富化的第一产出）。
    www 变体按 _page_key 归一去重（同页两个写法只占一个名额）。
    """
    priority: list[str] = []
    secondary: list[str] = []
    seen = {_page_key(base_url)}
    for m in _INNER_ANCHOR_RE.finditer(homepage_html or ""):
        href, text = m.group(1), m.group(2)
        contact_hit = _INNER_CONTACT_WORDS_RE.search(href) or _INNER_CONTACT_WORDS_RE.search(text)
        page_hit = _INNER_PAGE_WORDS_RE.search(href) or _INNER_PAGE_WORDS_RE.search(text)
        if not contact_hit and not page_hit:
            continue
        url = _resolve_url(base_url, href)
        if not url:
            continue
        key = _page_key(url)
        if key in seen:
            continue
        if extract_domain(url) != base_domain:
            continue
        seen.add(key)
        (priority if contact_hit else secondary).append(url)
    return (priority + secondary)[:_MAX_INNER_PAGES]


def find_wildcard_page_urls(
    homepage_html: str, base_url: str, base_domain: str | None, limit: int = 2
) -> list[str]:
    """联系/产品词都够不着的站点 → 首页同域普通链接兜底（≤limit 个）。

    2026-09-01 laifen.com（汕头莱芬）实测：2000 年代老式站把联系信息放在
    asp-bin/GB/?page=1 这类无关键词 query 页，内页词表与惯例路径探测全落空
    ——联系方式恰在目标客群（工厂/制造商）的高发站型上。只取同域、排除
    静态资源与首页自身，预算受 limit 约束。
    """
    out: list[str] = []
    seen = {_page_key(base_url)}
    for m in _INNER_ANCHOR_RE.finditer(homepage_html or ""):
        url = _resolve_url(base_url, m.group(1))
        if not url:
            continue
        if extract_domain(url) != base_domain:
            continue
        key = _page_key(url)
        if key in seen:
            continue
        if any(key.split("?")[0].lower().endswith(ext) for ext in _ASSET_EXT):
            continue
        seen.add(key)
        out.append(url)
        if len(out) >= limit:
            break
    return out


def _classify_httpx_error(exc: Exception) -> str:
    """httpx 异常 → 短中文原因（富化失败原因描述，销售/运营可读）。"""
    import ssl

    if isinstance(exc, httpx.ConnectError):
        cause = exc.__cause__
        if isinstance(cause, ssl.SSLError) or "certificate" in str(exc).lower():
            return "TLS/证书错误"
        msg = str(exc).lower()
        if any(
            k in msg
            for k in ("getaddrinfo", "nodename nor servname", "name or service", "no address")
        ):
            return "DNS 解析失败（域名可能失效）"
        return "连接被拒绝/重置"
    if isinstance(exc, httpx.ConnectTimeout):
        return "连接超时（站点不可达或被墙）"
    if isinstance(exc, httpx.ReadTimeout | httpx.WriteTimeout | httpx.PoolTimeout):
        return "响应超时"
    return type(exc).__name__


async def _fetch_detailed(client: httpx.AsyncClient, url: str) -> tuple[str | None, str | None]:
    """抓单页，失败时带回原因（None=成功无原因；空串层不可用）。"""
    try:
        resp = await client.get(url, follow_redirects=True)
    except httpx.HTTPError as exc:
        return None, _classify_httpx_error(exc)
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code}"
    # 按字节 + header/meta charset 解码（GBK 站防线），不信 httpx 的默认 utf-8
    return (
        _decode_html(resp.content, _charset_from_content_type(resp.headers.get("content-type"))),
        None,
    )


# 采集 client 双通道（与 meta_ads / web_search 同策略）：
# - 主通道走系统代理（出海官网多在海外/CDN 后，国内直连不可达——实测
#   shein.com/banggood.com 直连超时）；未配代理环境 = 等效直连
# - 兜底通道强制直连 + 宽松 SSL（防代理对目标站软拦截返回 202 的误报，
#   primal.com.ph 案例；证书过期的小站也能抓）
_SSL_LOOSE_CLIENT_ARGS = {"verify": False, "trust_env": False}

# ── HTML 字节 → 文本的解码（GBK 站防线，laifen.com 实锤）───────────────────
# Content-Type 不带 charset 时 httpx 默认 utf-8，而中国工厂/制造商站（核心
# ICP）大量是 gb2312/gbk —— 整页乱码会让中文锚文本联系页、CJK 判 CN、中文
# 具名联系人/地址全部失效。浏览器语义：header charset 优先，缺省再看
# <meta charset> 声明，最后 utf-8 容错。
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_-]+)""", re.I)
# gb2312 是 gb18030 的子集，统一按超集解码（生僻字不炸、字数不变）
_CHARSET_ALIAS = {"gb2312": "gb18030", "gbk": "gb18030"}


def _decode_html(content: bytes, header_charset: str | None) -> str:
    """响应字节 → 文本。header_charset 来自 Content-Type（httpx 的 resp.charset）。"""
    for cs in (header_charset,):
        if cs:
            cs = _CHARSET_ALIAS.get(cs.lower(), cs.lower())
            try:
                return content.decode(cs)
            except (LookupError, UnicodeDecodeError):
                pass
    m = _META_CHARSET_RE.search(content[:4096])
    if m:
        cs = m.group(1).decode("ascii", errors="ignore").lower()
        cs = _CHARSET_ALIAS.get(cs, cs)
        try:
            return content.decode(cs)
        except (LookupError, UnicodeDecodeError):
            pass
    return content.decode("utf-8", errors="replace")


def _charset_from_content_type(content_type: str | None) -> str | None:
    """Content-Type 头 → charset 参数（无则 None）。"""
    if not content_type or "charset=" not in content_type.lower():
        return None
    return content_type.split("charset=", 1)[1].split(";", 1)[0].strip().strip("'\"") or None


def _make_client(**kwargs: Any) -> httpx.AsyncClient:
    # trust_env=False：富化目标是中国企业官网（国内直连可达），不捡环境变量
    # 代理（2026-09-01 用户裁决：爬虫不被本机 VPN 干扰——国外出口访问国内站
    # 会被拦/变慢/内容不同）。需要代理的场景在调用方显式传 proxy=。
    base: dict[str, Any] = {"trust_env": False}
    base.update(kwargs)
    return httpx.AsyncClient(
        headers={"User-Agent": _UA, "Accept-Language": "en"},
        timeout=_TIMEOUT,
        **base,
    )


async def _fetch_site_detailed(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], url: str
) -> tuple[str | None, list[str]]:
    """抓站点首页（换 scheme 重试 + 宽松 SSL 兜底），失败时收集各次尝试的原因。

    返回 (html, 原因列表)；html 命中时原因列表为空。
    """
    primary, loose = clients
    html, reason = await _fetch_detailed(primary, url)
    if html is not None:
        return html, []
    reasons = [reason] if reason else []
    alt = (
        url.replace("https://", "http://", 1)
        if url.startswith("https://")
        else url.replace("http://", "https://", 1)
    )
    if alt != url:
        html, reason = await _fetch_detailed(primary, alt)
        if html is not None:
            return html, []
        if reason:
            reasons.append(reason)
    html, reason = await _fetch_detailed(loose, url)
    if html is not None:
        return html, []
    if reason:
        reasons.append(f"{reason}（宽松SSL直连）")
    return None, reasons


async def _fetch_site(clients: tuple[httpx.AsyncClient, httpx.AsyncClient], url: str) -> str | None:
    """抓站点首页，失败时换 scheme 重试，最后用宽松 SSL 的兜底 client 再试一次。

    请求预算：正常 1 次；失败最多 +2 次（换 scheme、宽松 SSL）。
    联系页抓取不算在内（首页成功才有联系页），礼貌性可控。
    """
    return (await _fetch_site_detailed(clients, url))[0]


def _resolve_url(base_url: str, href: str) -> str | None:
    if href.startswith(("http://", "https://")):
        return href
    try:
        return str(httpx.URL(base_url).join(href))
    except Exception:  # noqa: BLE001  相对链接拼接失败不致命
        return None


def detect_whatsapp(html_list: list[str]) -> tuple[bool, str | None]:
    """返回 (是否命中, 标准化 wa.me 链接或 None)。"""
    joined = "\n".join(h for h in html_list if h)
    if not joined:
        return False, None
    for pattern in _PHONE_PATTERNS:
        m = pattern.search(joined)
        if m and m.group(1):
            return True, f"https://wa.me/{m.group(1).lstrip('+')}"
    for pattern in _WHATSAPP_PATTERNS:
        if pattern.search(joined):
            return True, None
    return False, None


def detect_whatsapp_numbers(html_list: list[str]) -> list[str]:
    """页面里出现的全部 WhatsApp 号码（去重保序）——「多分线 = 已规模化使用」证据（§4.1）。"""
    joined = "\n".join(h for h in html_list if h)
    numbers: list[str] = []
    for pattern in _PHONE_PATTERNS:
        for m in pattern.finditer(joined):
            raw = (m.group(1) or "").lstrip("+")
            if raw and raw not in numbers:
                numbers.append(raw)
    return numbers


_ASSET_EXT = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".css",
    ".js",
    ".pdf",
    ".woff",
    ".woff2",
    ".ico",
)
# 平台埋点/监控邮箱误报黑名单（实测 Wix 站点正文里带 Sentry 埋点邮箱）
_EMAIL_DOMAIN_BLOCKLIST = ("wixpress.com", "sentry.io", "sentry-next.com", "googlegroups.com")


def _is_email(addr: str) -> bool:
    if not _EMAIL_RE.fullmatch(addr):
        return False
    domain = addr.rsplit("@", 1)[1].lower()
    # 防图片/字体文件名误判（如 logo_250x@2x.png）
    if any(domain.endswith(ext) for ext in _ASSET_EXT):
        return False
    return not any(domain == d or domain.endswith("." + d) for d in _EMAIL_DOMAIN_BLOCKLIST)


def detect_email(
    html: str | list[str] | list[str | None] | None, *, mailto_only: bool = False
) -> str | None:
    """公开邮箱检测。输入首页或页面列表（首页+联系/关于页）。

    mailto 优先（跨全部页面先扫一遍），其次正文正则——企业邮箱主要挂在
    联系页而非首页，2026-08-31 审计前只扫首页漏掉大半。
    mailto_only=True 只扫显式 mailto 链接（调用方在 mailto 与正则之间
    插入 JSON-LD 声明：结构化声明优先于正则启发，FR-1.5）。
    """
    if not html:
        return None
    pages = [html] if isinstance(html, str) else list(html)
    for page in pages:
        for m in re.finditer(r'href=["\']mailto:([^"\'>]+)', page or "", re.I):
            # mailto 可带 ?subject= 等 query（fullmatch 会把整串当地址丢掉，
            # 白白失去 mailto 优先语义——先剥 query 再校验）
            addr = m.group(1).split("?", 1)[0].strip()
            if _is_email(addr):
                return addr
    if mailto_only:
        return None
    for page in pages:
        for m in _EMAIL_RE.finditer(page or ""):
            if _is_email(m.group(0)):
                return m.group(0)
    return None


def detect_social(html_list: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    joined = "\n".join(h for h in html_list if h)
    for platform, pattern in _SOCIAL_RES:
        m = pattern.search(joined)
        if m:
            found[platform] = m.group(0)
    return found


class WebsiteEnrichCollector(Collector):
    name = "website_enrich"
    title = "网站富化（检测 WhatsApp/邮箱/社媒）"
    logic_note = (
        "【做什么】给线索补齐关键信息：没有官网的先搜出官网，再抓官网页面，"
        "识别 WhatsApp 入口和号码、联系邮箱、联系电话（tel 链接）、社交媒体、业务场景（客服/营销/订单）、"
        "SaaS 工具需求（CRM/工单/Chatbot）、出海证据（多语言/海外货币/国际物流/投放市场）、"
        "ICP 备案号。识别完自动重新评分。邮箱和 WhatsApp 号码会自动生成联系人。\n"
        "【准确性】常规请求打不开的大站（有反爬）自动换无头浏览器渲染再抓一次；"
        "域名已失效的站点如实记失败，下一轮自动重试，不出假数据；"
        "邮箱识别带两层防误判（排除图片文件名、网站监控埋点邮箱）；"
        "判断「是不是中国企业」优先认 ICP 备案号，其次看中文内容占比。\n"
        "【循环复核】已入库的线索按等级定期重查：S 级每天、A 级每 3 天、B 级每 7 天、"
        "C 级每 30 天。每次重查都会刷新信号的最近确认时间，公司撤了 WhatsApp 按钮也能从"
        "证据时间上看出来。\n"
        "【怎么运行】不用手动建任务：搜索、招聘监控、广告库任何一个跑完，系统会自动执行本步骤；"
        "在线索列表勾选线索点「检测 WhatsApp」也会立即对选中线索执行。"
    )
    param_schema = [
        {
            "key": "lead_ids",
            "label": "指定线索 ID",
            "required": False,
            "type": "tags",
            "placeholder": "留空 = 全库待富化线索；输入 ID 回车",
            "default": "",
        },
    ]

    async def run(self, ctx: TaskContext) -> None:
        lead_ids = _parse_lead_ids(ctx.params.get("lead_ids"))
        async with _session_factory()() as session:
            leads = await _load_scope(session, lead_ids)

        # 结果统计（结束日志说清成功/失败与各自原因——失败不再淹没在「完成」里）
        ok_sites: list[str] = []
        ok_lead_ids: list[int] = []  # 成功的线索 ID（结束时统计联系方式命中率）
        fail_sites: list[tuple[str, str | None]] = []  # (website, 失败原因)

        # 浏览器渲染兜底（懒启动）：httpx 三通道都被拒的站点（如大型电商的
        # 反爬 403）用无头浏览器渲染一次，实测 banggood 403 → 渲染 200。
        # 未装 [collect] 可选依赖时静默降级（日志说明一次）。
        browser = None
        render_note_logged = False
        # 懒启动锁：富化并发（默认 5）下多个站点同时进渲染兜底会各启一个
        # Chromium，后者覆盖前者且永不关闭（进程泄漏）——双检锁收敛为单实例
        browser_lock = asyncio.Lock()

        async def get_browser():
            nonlocal browser, render_note_logged
            if browser is not None:
                return browser
            async with browser_lock:
                if browser is not None:  # 等锁期间别人已启动
                    return browser
                try:
                    from playwright.async_api import async_playwright
                except ImportError:
                    if not render_note_logged:
                        render_note_logged = True
                        await ctx.log(
                            "info",
                            "浏览器渲染兜底不可用（装 playwright 可抓反爬大站）：pip install '.[collect]'",
                        )
                    return None
                pw = await async_playwright().start()
                browser = await pw.chromium.launch(headless=True)
                return browser

        # 两个 client：正常（代理优先）+ 宽松 SSL 直连兜底（证书过期的小站常见）
        async with _make_client() as client, _make_client(**_SSL_LOOSE_CLIENT_ARGS) as loose:
            # ---------- 官网发现（补全链）：无官网线索先搜官网再富化 ----------
            # 招聘站（jobui）公司页无官网字段——缺官网的线索进不了富化/评分链路，
            # cn_domestic 永远升不了 qualified。仅全库扫描模式做（手动勾选是精确富化）
            discovered: list[tuple[int, str]] = []
            if not lead_ids:
                # 每轮上限固定 30（FR-1.5：搜索配额礼貌约束——想多补靠多轮，
                # 不靠一轮放大）。3s 间隔见 _DISCOVER_GAP。
                discover_limit = _DISCOVER_LIMIT
                async with _session_factory()() as session:
                    candidates = await _load_discoverable(session, discover_limit)
                    from sqlalchemy import func

                    from app.models.lead import Lead as LeadModel

                    backlog = (
                        await session.execute(
                            select(func.count())
                            .select_from(LeadModel)
                            .where(
                                (LeadModel.website.is_(None)) | (LeadModel.website == ""),
                                LeadModel.icp_status.notin_(("foreign", "non_buyer")),
                            )
                        )
                    ).scalar_one()
                if candidates:
                    await ctx.log(
                        "info",
                        f"库内共 {backlog} 家无官网，本轮处理 {len(candidates)} 家（按分数优先）",
                    )
                for lid, name, brand_slugs in candidates:
                    ctx.check_cancelled()
                    ws = await _discover_website(
                        (client, loose), name, log=ctx.log, brand_slugs=brand_slugs
                    )
                    if ws:
                        from app.crud.lead import touch_field_meta
                        from app.models.lead import Lead

                        dom = extract_domain(ws)
                        async with _session_factory()() as session:
                            lead = await session.get(Lead, lid)
                            if lead and not lead.website:
                                # 撞域检查：别的线索已持有该 domain 时绝不写入——
                                # 发现链直接改行会绕过 upsert 去重，同公司出现两条
                                # 线索且永远无人合并（2026-08-31 巡检 B 级 bug）。
                                # 标 dup 负缓存 7 天冷却，避免反复搜索浪费配额
                                taken = await _domain_taken(session, dom, lid) if dom else None
                                if taken is not None:
                                    touch_field_meta(
                                        lead,
                                        "website",
                                        "web_discovery_dup",
                                        confidence=0,
                                        now=datetime.now(timezone.utc),
                                    )
                                    await session.commit()
                                    await ctx.log(
                                        "info",
                                        f"[lead {lid}] 🔍 官网发现撞域跳过：{name} → {ws}"
                                        f"（线索 #{taken} 已持有 {dom}，不造重复）",
                                    )
                                else:
                                    lead.website = ws
                                    lead.domain = dom or lead.domain
                                    # 主键收敛 domain（FR-2.3）：发现链直接改行，
                                    # 不升键会让行一直挂 namecity/tel 旧键——
                                    # 后续同公司 draft 反查不中造重复
                                    from app.crud.lead import converge_dedupe_key

                                    await converge_dedupe_key(session, lead)
                                    touch_field_meta(
                                        lead,
                                        "website",
                                        "web_discovery",
                                        confidence=60,
                                        now=datetime.now(timezone.utc),
                                    )
                                    # uq_leads_domain 唯一索引兜底（并发窗口下
                                    # check-then-act 仍可能撞）：撞域回滚 + 负缓存
                                    from sqlalchemy.exc import IntegrityError

                                    try:
                                        await session.commit()
                                    except IntegrityError:
                                        await session.rollback()
                                        async with _session_factory()() as s2:
                                            fresh = await s2.get(Lead, lid)
                                            if fresh and not fresh.website:
                                                touch_field_meta(
                                                    fresh,
                                                    "website",
                                                    "web_discovery_dup",
                                                    confidence=0,
                                                    now=datetime.now(timezone.utc),
                                                )
                                                await s2.commit()
                                        await ctx.log(
                                            "info",
                                            f"[lead {lid}] 🔍 官网发现撞域（唯一索引兜底）：{name} → {ws}",
                                        )
                                        continue
                                    discovered.append((lid, ws))
                                    await ctx.log(
                                        "info", f"[lead {lid}] 🔍 官网发现：{name} → {ws}"
                                    )
                    else:
                        # 失败负缓存：7 天内不再重搜（否则失败者永远占着
                        # 分数倒序的前 N 窗口，后面的线索饿死——2026-08-31 巡检 A 级 bug）
                        from app.crud.lead import touch_field_meta
                        from app.models.lead import Lead

                        async with _session_factory()() as session:
                            lead = await session.get(Lead, lid)
                            if lead and not lead.website:
                                touch_field_meta(
                                    lead,
                                    "website",
                                    "web_discovery_miss",
                                    confidence=0,
                                    now=datetime.now(timezone.utc),
                                )
                                await session.commit()
                    await asyncio.sleep(_DISCOVER_GAP)  # 搜索礼貌间隔
                if candidates:
                    remaining = backlog - len(discovered)
                    await ctx.log(
                        "info",
                        f"官网发现：{len(discovered)}/{len(candidates)} 条命中"
                        + (
                            f"，仍有 {remaining} 家无官网待后续轮次（再点一次全库富化继续）"
                            if remaining > 0
                            else ""
                        ),
                    )
            leads = [*discovered, *leads]

            if not leads:
                await ctx.log(
                    "info", "没有待富化的线索（有网站 或 已尝试发现，且窗口内未成功富化）"
                )
                return

            ctx.set_total(len(leads))
            await ctx.log("info", f"待富化线索 {len(leads)} 条，并发 {settings.ENRICH_CONCURRENCY}")
            sem = asyncio.Semaphore(settings.ENRICH_CONCURRENCY)
            done = 0

            async def wrapped(lead_id: int, website: str) -> None:
                nonlocal done
                async with sem:
                    ctx.check_cancelled()
                    try:
                        ok, fail_reason = await _enrich_one(
                            (client, loose), ctx, lead_id, website, get_browser
                        )
                        if ok:
                            ok_sites.append(website)
                            ok_lead_ids.append(lead_id)
                        else:
                            fail_sites.append((website, fail_reason))
                    except Exception:  # noqa: BLE001  单站点失败不放大为整任务失败
                        logger.exception(f"[lead {lead_id}] 富化异常：{website}")
                        fail_sites.append((website, "富化异常（见服务端日志）"))
                done += 1
                ctx.inc_progress(1)

            await asyncio.gather(*(wrapped(lid, ws) for lid, ws in leads))

        if browser is not None:
            try:
                await browser.close()
            except Exception:  # noqa: BLE001
                pass

        # 汇总日志：成功/失败分开数，失败带真实原因（每站点：原因）——
        # 「域名失效或拒绝访问」这种一刀切文案换成分层诊断（DNS/超时/TLS/HTTP 码）
        summary = f"富化完成：成功 {len(ok_sites)}、失败 {len(fail_sites)}"
        # 联系方式命中率（2026-09-01 用户红线：联系信息爬不全 = 失败——命中率
        # 必须可见，不能只看「富化成功」）：成功站点里有多少家拿到了电话/邮箱/WA
        if ok_lead_ids:
            hits = await _contact_hit_counts(ok_lead_ids)
            if hits:
                tel, mail, wa, contact_rows = hits
                summary += (
                    f"；联系方式命中（成功 {len(ok_lead_ids)} 家中）："
                    f"邮箱 {mail}、电话 {tel}、WhatsApp {wa}、具名联系人 {contact_rows}"
                )
        if fail_sites:
            detail = "、".join(
                f"{site.replace('https://', '').replace('http://', '')}"
                f"（{reason or '未知原因'}）"
                for site, reason in fail_sites[:10]
            )
            summary += f"（失败站点：{detail} —— 下一轮自动重试，原因已写入线索详情）"
        await ctx.log("info", summary)


# ---------- 官网发现（补全链，2026-08-31） ----------
# 招聘站（jobui）公司页无官网字段——缺官网的线索进不了富化/评分链路，
# cn_domestic 永远升不了 qualified。用公司名走搜索引擎（默认引擎、零 key）
# 找官网，复用 web_search 的平台/文章页过滤与根 URL 归一。
_DISCOVER_GAP = 3.0  # 搜索礼貌间隔（秒）——必应通道对频繁请求敏感，3s 起步
_DISCOVER_LIMIT = 30  # 每轮最多发现的线索数（搜索配额友好；手动全库富化可调大）
_DISCOVER_RETRY_DAYS = 7  # 发现失败/撞域名的冷却重试天数（field_meta 负缓存）


async def _discover_website(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
    name: str,
    log=None,
    brand_slugs: list[str] | None = None,
) -> str | None:
    """公司名 → 官网（候选站**写前验证**，根 URL 归一）。找不到返回 None。

    三级尝试（2026-09-01 用户红线「联系信息必须爬准」）：
    ① 品牌域直猜：B2B 目录线索的店铺子域就是品牌名（yuanxiuhair.en.
       made-in-china.com → 官网大概率 yuanxiuhair.com），先猜后验——比
       搜索引擎对英文公司名的返回质量（实测全是企查查/Vogue/政府网垃圾）
       靠谱得多；
    ② 搜索（DDG 不可达自动切必应）：英文名引号精确匹配，中文名加「官网」；
    ③ 候选站写前验证：抓首页，标题或域名必须含公司名特征词（拉丁词 ≥5
       字符剔通用与城市软词，或中文名相邻汉字对）才写——错站数据等于
       给销售错误联系方式，宁可 miss 负缓存 7 天。
    """
    # 经模块属性调用（非 from-import 局部绑定）——测试可对 web_search.search_with_fallback 打桩
    from app.collectors import web_search as _ws

    async def _verified(url: str) -> str | None:
        page = await _fetch_site(clients, url)
        if page is None:
            return None
        m = _TITLE_RE.search(page)
        title = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else ""
        if _site_title_mentions(name, title=title, url=url):
            return url
        return None

    # ① 品牌域直猜（B2B 线索专属）：{slug}.com/.cn，写前验证同闸
    for slug in (brand_slugs or [])[:2]:
        for guess in (f"https://{slug}.com", f"https://www.{slug}.com", f"https://{slug}.cn"):
            hit = await _verified(guess)
            if hit:
                if log:
                    await log("info", f"官网发现（品牌域直猜命中）：{name} → {hit}")
                return hit
            await asyncio.sleep(_DISCOVER_GAP)

    # ② 搜索：有品牌词先搜品牌词（比公司名独特，SERP 干净）；否则公司名
    # （英文名加引号精确匹配——混「官网」中文词会把结果带偏）
    queries: list[str] = []
    if brand_slugs:
        queries.append(f'"{brand_slugs[0]}"')
    queries.append(f'"{name}"' if not _has_cjk(name) else f"{name} 官网")
    for query in queries:
        items, _err, _used = await _ws.search_with_fallback(clients, query, 8, log=log)
        if not items:
            continue
        drafts = _ws.results_to_drafts(items, params_is_cn=True)
        for d in drafts[:3]:
            if not d.website:
                continue
            page = await _fetch_site(clients, d.website)
            if page is None:
                continue  # 抓不到的站写了也富化不了
            m = _TITLE_RE.search(page)
            title = re.sub(r"<[^>]+>|\s+", " ", m.group(1)).strip() if m else ""
            if _site_title_mentions(name, title=title, url=d.website):
                return d.website
            if log:
                await log(
                    "info",
                    f"官网发现候选否决（公司名特征词不在站内）：{name} → {d.website}"
                    f"（标题：{title[:40] or '无'}）",
                )
            await asyncio.sleep(_DISCOVER_GAP)
    return None


# 发现验证的公司名通用词：英文公司名几乎必含、不构成身份特征的高频词
_DISCOVERY_GENERIC_LATIN = frozenset(
    {
        "co", "ltd", "limited", "inc", "llc", "corp", "corporation", "company",
        "group", "technology", "tech", "technologies", "industrial", "industry",
        "industries", "trading", "trade", "international", "china", "chinese",
        "products", "product", "manufacturing", "manufacture", "manufacturer",
        "official", "website", "home", "page", "network", "networks", "digital",
        "opto", "optic", "optics", "light", "lights", "lighting", "led", "hair",
        "beauty", "electronics", "electronic", "arts", "crafts", "new", "best",
    }
)
# 软词（城市/省份）：单独出现不足以确认身份（深圳的网站千千万），但可作
# 双词佐证之一（Shenzhen ATA 的标题含 shenzhen+ata 两个词 → 通过）
_DISCOVERY_SOFT_LATIN = frozenset(
    {
        "shenzhen", "beijing", "shanghai", "guangzhou", "dongguan", "ningbo",
        "xuchang", "hangzhou", "suzhou", "wuxi", "yiwu", "wuhan", "chengdu",
        "xiamen", "qingdao", "tianjin", "chongqing", "nanjing", "dalian",
        "fuzhou", "quanzhou", "shantou", "chaozhou", "zhongshan", "foshan",
        "huizhou", "changzhou", "heze", "juancheng", "guangdong", "jiangsu",
        "zhejiang", "fujian", "shandong", "hunan", "sichuan",
    }
)


def _site_title_mentions(company_name: str, *, title: str, url: str) -> bool:
    """公司名特征词是否出现在站点标题或 URL（发现链写前验证，纯函数）。

    拉丁名：剔通用词与城市软词后 ≥5 字符的词（yuanxiu/topledvision）任一
    命中即通过；不足时退化要求 ≥2 个 ≥3 字符词（城市词可作佐证之一：
    Shenzhen ATA → 「shenzhen」+「ata」；单独一个城市词不通过）。
    中文名：剥法律后缀后任一相邻汉字对出现在标题（与 site_matches_company
    的 name_hit 同口径）。
    """
    name = (company_name or "").strip()
    if not name:
        return False
    hay = f"{title} {url}".lower()
    if _has_cjk(name):
        core = re.sub(
            r"(股份有限公司|有限责任公司|有限公司|集团公司|集团)", "", name
        ).strip()
        return bool(core) and any(core[i : i + 2] in title for i in range(max(1, len(core) - 1)))
    words = [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]
    strong = [
        w
        for w in words
        if len(w) >= 5 and w not in _DISCOVERY_GENERIC_LATIN and w not in _DISCOVERY_SOFT_LATIN
    ]
    if any(w in hay for w in strong):
        return True
    weak = [w for w in words if len(w) >= 3 and w not in _DISCOVERY_GENERIC_LATIN]
    hit = {w for w in weak if w in hay}
    return len(hit) >= 2


async def _contact_hit_counts(lead_ids: list[int]) -> tuple[int, int, int, int] | None:
    """成功富化的线索里联系方式命中数：(电话, 邮箱, WA, 具名联系人)。"""
    if not lead_ids:
        return None
    async with _session_factory()() as session:
        from sqlalchemy import func as sa_func, select as sa_select, or_

        from app.models.lead import Lead as LeadModel, LeadContact as LeadContactModel

        rows = (
            await session.execute(
                sa_select(
                    sa_func.count(sa_func.nullif(LeadModel.phone_e164, "")).label("tel"),
                    sa_func.count(sa_func.nullif(LeadModel.email, "")).label("mail"),
                    sa_func.count().label("wa"),
                ).where(
                    LeadModel.id.in_(lead_ids),
                    or_(
                        LeadModel.whatsapp_url != "",
                        sa_func.json_array_length(LeadModel.whatsapp_numbers) > 0,
                    ),
                )
            )
        ).one()
        contact_rows = (
            await session.execute(
                sa_select(sa_func.count(sa_func.distinct(LeadContactModel.lead_id))).where(
                    LeadContactModel.lead_id.in_(lead_ids)
                )
            )
        ).scalar_one()
    return int(rows.tel), int(rows.mail), int(rows.wa), int(contact_rows)


async def _domain_taken(session: AsyncSession, domain: str, exclude_id: int) -> int | None:
    """该 domain 是否已被其他线索持有（返回持有者 id 或 None）。

    发现链直接改行会绕过 upsert 去重——同公司出现两条线索且永远无人合并，
    所以写入前必查（2026-08-31 巡检 B 级 bug）。
    """
    from sqlalchemy import select as sa_select

    from app.models.lead import Lead

    return (
        await session.execute(
            sa_select(Lead.id).where(Lead.domain == domain, Lead.id != exclude_id).limit(1)
        )
    ).scalar_one_or_none()


def _discovery_cooldown(meta: dict | None, now: datetime | None = None) -> bool:
    """field_meta 里 website 字段带 miss/dup 标记且在冷却期内 → 跳过本轮发现。

    没有负缓存时：发现失败的线索分数不变，永远占着「分数倒序前 N」的窗口，
    排在后面的线索饿死（2026-08-31 巡检：191 家 backlog 下覆盖率会永久卡住）。
    mismatch_clear（错配清除）同样进冷却——否则下一轮「公司名 官网」搜索
    大概率再搜回同一错站，「写入→富化→判错清除」每轮空转烧搜索配额。
    """
    w = (meta or {}).get("website") or {}
    if w.get("source") not in ("web_discovery_miss", "web_discovery_dup", "mismatch_clear"):
        return False
    try:
        tried = datetime.fromisoformat(str(w.get("updated_at")))
    except (TypeError, ValueError):
        return False
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=_DISCOVER_RETRY_DAYS)
    return tried >= cutoff


async def _load_discoverable(session: AsyncSession, limit: int) -> list[tuple[int, str, list[str]]]:
    """无官网且在 ICP 门内（foreign/non_buyer 排除）的线索（分数倒序——高分商机优先补全）。

    多取 3 倍候选，内存里滤掉冷却中的（失败/撞域名 7 天内不再消耗搜索配额）；
    SQL 侧不比较 JSON——PG json 类型无 = 操作符。
    终态（won/invalid）与富化范围同口径排除（FR-1.5「非终态」；NULL 放行）。

    返回 (lead_id, name, brand_slugs)：B2B 目录线索从证据链里提取店铺子域
    品牌名（yuanxiuhair），发现链优先猜品牌域——搜索引擎对英文公司名的
    返回质量不可靠（2026-09-01 实测：企查查/Vogue/政府网霸榜）。
    """
    from sqlalchemy import or_

    from app.models.lead import Lead, LeadSignal

    rows = (
        await session.execute(
            select(Lead.id, Lead.name, Lead.field_meta)
            .where(
                (Lead.website.is_(None)) | (Lead.website == ""),
                Lead.icp_status.notin_(("foreign", "non_buyer")),
                or_(Lead.follow_status.is_(None), Lead.follow_status.notin_(["won", "invalid"])),
            )
            .order_by(Lead.score.desc())
            .limit(limit * 3 + 30)
        )
    ).all()
    out: list[tuple[int, str, list[str]]] = []
    prefilter: list[tuple[int, str]] = []
    for lid, name, meta in rows:
        if _discovery_cooldown(meta):
            continue
        prefilter.append((lid, name))
        if len(prefilter) >= limit:
            break
    if not prefilter:
        return out
    # B2B 证据里的店铺子域 → 品牌名（批量一条查询，避免 N+1）
    sig_rows = (
        await session.execute(
            select(LeadSignal.lead_id, LeadSignal.evidence_url).where(
                LeadSignal.lead_id.in_([r[0] for r in prefilter]),
                LeadSignal.source == "b2b_supplier",
            )
        )
    ).all()
    slug_by_lead: dict[int, list[str]] = {}
    for sig_lead_id, ev_url in sig_rows:
        m = re.search(r"https://([a-z0-9-]{2,})\.en\.made-in-china\.com", ev_url or "")
        if m:
            slug_by_lead.setdefault(sig_lead_id, []).append(m.group(1))
    for lid, name in prefilter:
        out.append((lid, name, slug_by_lead.get(lid, [])))
    return out


async def _load_scope(session: AsyncSession, lead_ids: list[Any]) -> list[tuple[int, str]]:
    """返回 [(lead_id, website)]。lead_ids 指定 → 只取有网站的；否则全库 eligible。"""
    from app.models.lead import Lead

    now = datetime.now(timezone.utc)
    if lead_ids:
        # 手动勾选不受刷新窗口限制
        stmt = select(Lead.id, Lead.website).where(
            Lead.id.in_(lead_ids), Lead.website.is_not(None), Lead.website != ""
        )
    else:
        # 分级增量重爬（补充需求 §九）：高价值线索检查更勤——S 每天 / A 3 天 / B 7 天 / C 30 天；
        # ENRICH_INTERVAL_HOURS 作为 C 级（兜底档）的可配置上限。
        # foreign/non_buyer 行不在服务范围（ICP 门已排除销售池），不消耗抓取配额
        from sqlalchemy import or_

        def _stale(grade: str, days: int):
            cutoff = now - timedelta(days=days)
            return (Lead.grade == grade) & (
                (Lead.enriched_at.is_(None)) | (Lead.enriched_at < cutoff)
            )

        c_days = max(1, settings.ENRICH_INTERVAL_HOURS // 24)
        # 终态不再富化（2026-08-31 审计）：won=已是客户（该进客户成功流程），
        # invalid=已判无效——两态继续重爬只浪费抓取配额。NULL（从未跟进）必须
        # 用 or_ 显式放行：SQL 里 NULL NOT IN (...) 为假值，会把共享池全部滤掉
        stmt = select(Lead.id, Lead.website).where(
            Lead.website.is_not(None),
            Lead.website != "",
            Lead.icp_status.notin_(("foreign", "non_buyer")),
            or_(_stale("S", 1), _stale("A", 3), _stale("B", 7), _stale("C", c_days)),
            or_(Lead.follow_status.is_(None), Lead.follow_status.notin_(["won", "invalid"])),
        )
    rows = (await session.execute(stmt)).all()
    return [(r[0], r[1]) for r in rows]


def _parse_lead_ids(raw: Any) -> list[int]:
    """lead_ids 参数归一化：勾选入口传 list[int]；手动任务表单传的是字符串
    "12,34"——直接喂给 in_() 会被 SQLAlchemy 拒（ArgumentError），任务 failed。
    非法项丢弃（宁漏勿错富化）。
    """
    if not raw:
        return []
    items = raw if isinstance(raw, list | tuple) else str(raw).split(",")
    out: list[int] = []
    for x in items:
        try:
            out.append(int(str(x).strip()))
        except (TypeError, ValueError):
            continue
    return out


def _session_factory():
    from app.db.session import async_session

    return async_session


async def _fetch_impersonated(url: str) -> tuple[str | None, str | None]:
    """Chrome TLS/HTTP2 指纹伪装请求（curl_cffi）：大多数反爬只认指纹不开浏览器就能过。

    抓取三层递进的第二层——httpx（快、通用）→ 本层（指纹伪装，毫秒级开销，
    实测 banggood 403 → 200）→ 无头浏览器渲染（JS 挑战才需要）。
    返回 (html, 原因)；未安装可选依赖时 (None, None)——层不可用不算失败原因。
    """
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError:
        return None, None
    try:
        async with AsyncSession(impersonate="chrome", timeout=20) as s:
            resp = await s.get(url)
            if resp.status_code == 200 and len(resp.content) > 500:
                return (
                    _decode_html(
                        resp.content, _charset_from_content_type(resp.headers.get("content-type"))
                    ),
                    None,
                )
            if resp.status_code != 200:
                return None, f"指纹层 HTTP {resp.status_code}"
            return None, "指纹层内容过短"
    except Exception as exc:  # noqa: BLE001  指纹层失败留给浏览器层
        return None, f"指纹层 {type(exc).__name__}"


async def _render_with_browser(browser, url: str) -> str | None:
    """无头浏览器渲染兜底：httpx 被反爬拒绝（403）的大站用渲染抓。失败返回 None。"""
    try:
        page = await browser.new_page(locale="zh-CN", viewport={"width": 1440, "height": 900})
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            if resp is not None and resp.status >= 400:
                return None
            return await page.content()
        finally:
            await page.close()
    except Exception:  # noqa: BLE001  渲染失败（超时/崩溃）不算数
        return None


# tel: 链接里的电话（富化补电话：联系方式的直接来源）
_TEL_LINK_RE = re.compile(r'href=["\']tel:([+\d][\d\-()\s]{5,25})["\']', re.I)


def detect_tel_phones(html_list: list[str]) -> list[str]:
    """页面 tel: 链接电话（去空格/横线/括号，去重保序）。"""
    joined = "\n".join(h for h in html_list if h)
    phones: list[str] = []
    for m in _TEL_LINK_RE.finditer(joined):
        raw = re.sub(r"[\s\-()]", "", m.group(1))
        if raw and raw not in phones:
            phones.append(raw)
    return phones


async def _record_enrich_fail(lead_id: int, website: str, reason: str) -> None:
    """富化失败原因落到线索 field_meta.enrich_fail（详情页可见；成功富化时自愈清除）。

    独立短事务：失败路径此时还没有打开过的写会话，且不碰 enriched_at——
    失败线索保留在下一轮重爬范围里。
    """
    from app.models.lead import Lead

    async with _session_factory()() as session:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return
        meta = dict(lead.field_meta or {})
        meta["enrich_fail"] = {
            "reason": reason[:500],
            "website": website,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        lead.field_meta = meta
        await session.commit()


async def _clear_mismatched_website(
    lead_id: int,
    website: str,
    site_title: str,
    session: AsyncSession | None = None,
) -> None:
    """官网错配清除（2026-09-01）：张冠李戴的站抓来的邮箱/电话/信号/联系人
    全是别人的数据——全清 + 重评分（大概率成空壳，交给 prune 规则），
    field_meta.website 记错配原因。session 缺省自建短事务；测试可注入共库会话。
    """
    from sqlalchemy import delete as _delete
    from sqlalchemy import select as _select

    from app.crud.lead_events import rescore_and_log
    from app.models.lead import Lead, LeadContact, LeadSignal

    owns = session is None
    if owns:
        session = _session_factory()()
    try:
        lead = await session.get(Lead, lead_id)
        if lead is None:
            return
        lead.website = None
        lead.domain = None
        lead.email = None
        lead.phone_raw = None
        lead.phone_e164 = None
        # WA 字段按来源分流（2026-09-01 审计）：whatsapp_url/hit/numbers 是双源
        # 字段——官网富化写的清；meta_ads 主页探测写的（field_meta 有来源）是
        # FB 主页数据、与错站无关，保留（fb_whatsapp/CTWA 证据同理）
        wa_source = (lead.field_meta or {}).get("whatsapp_url", {}).get("source")
        if wa_source != "meta_ads":
            lead.whatsapp_hit = False
            lead.whatsapp_url = None
            lead.whatsapp_numbers = []
        lead.wa_business = False
        lead.scenes = []
        lead.saas_signals = {}
        lead.overseas_signals = {}
        lead.social = {}
        # 信号证据链（lead_signals）同样产自错站——一并清，否则详情页证据卡
        # 仍展示错站的 WA/出海证据（FR-5.1「全部信号全清」）；meta_ads 来源的
        # 信号（FB 主页探测/在投广告）不是错站数据，保留
        await session.execute(
            _delete(LeadSignal).where(
                LeadSignal.lead_id == lead_id,
                LeadSignal.source != "meta_ads",
            )
        )
        # 身份键回退「无官网」状态：domain: 键失效（否则官网发现再找到真身时
        # 会反向并入这条错配行），tel 已清 → namecity 兜底
        from app.collectors.normalize import make_dedupe_key as _mk_key
        from app.crud.lead import _namecity_key

        lead.namecity_key = _namecity_key(lead.name, lead.city)
        new_key = (
            _mk_key(name=lead.name, city=lead.city) or f"raw:{lead.name.strip().lower()}|manual"
        )
        if new_key != lead.dedupe_key:
            conflict = (
                await session.execute(
                    _select(Lead.id).where(Lead.dedupe_key == new_key, Lead.id != lead_id)
                )
            ).scalar_one_or_none()
            if conflict is None:
                lead.dedupe_key = new_key
            else:
                # namecity 撞键：退 raw 兜底——保留旧 domain: 键会让错站域名
                # 再被搜到时反向并入这条已清空的行（2026-09-01 富化层审计）
                lead.dedupe_key = f"raw:{lead.name.strip().lower()}|mismatch"
        meta = dict(lead.field_meta or {})
        meta["website"] = {
            "source": "mismatch_clear",
            "reason": f"错配官网已清除：站点标题「{site_title[:60]}」与公司名无关联",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "confidence": 100,
        }
        lead.field_meta = meta
        # 错站来源的自动联系人全删（不限 source=website_enrich——draft 落库时
        # 建的联系人带的是采集器 source 如 web_search，2026-09-01 调试实锤）；
        # manual 人工录入与 meta_ads 主页探测的联系人不是错站数据，保留
        rows = (
            (
                await session.execute(
                    _select(LeadContact).where(
                        LeadContact.lead_id == lead_id,
                        LeadContact.source.notin_(("manual", "meta_ads")),
                    )
                )
            )
            .scalars()
            .all()
        )
        for c in rows:
            await session.delete(c)
        await rescore_and_log(session, lead)
        if owns:
            await session.commit()
        else:
            await session.flush()
    finally:
        if owns:
            await session.close()


async def _probe_export_en_pages(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient], base: str, base_domain: str | None
) -> list[tuple[str, str]]:
    """外销站英文联系页探测（2026-09-01 靶心补强）。

    中国公司国内站常不挂 WA，外销版（en. 子域=同注册人、同域 /en/ 路径）才挂
    wa.me——「在用 WA 承接客户」的高发位。只探零错配风险的形态（不换 TLD：
    .cn→.com 可能撞到别人的域）；页面须含联系方式才收；预算 ≤4 次抓取、
    页间 0.5s（与内页翻页同档礼貌间隔）。
    """
    from urllib.parse import urlparse

    host = urlparse(base).hostname or ""
    cands: list[str] = []
    if base_domain:
        cands += [f"https://en.{base_domain}/", f"https://en.{base_domain}/contact-us"]
    if host:
        cands += [f"https://{host}/en/contact-us", f"https://{host}/en/contact"]
    out: list[tuple[str, str]] = []
    for url in cands[:4]:
        html = await _fetch_site(clients, url)
        if html is not None and (
            detect_whatsapp([html])[0]
            or detect_whatsapp_numbers([html])
            or detect_email([html])
            or detect_text_phones([html])
        ):
            out.append((html, url))
        await asyncio.sleep(0.5)
    return out


async def _enrich_one(
    clients: tuple[httpx.AsyncClient, httpx.AsyncClient],
    ctx: TaskContext,
    lead_id: int,
    website: str,
    get_browser: Callable[[], Awaitable[Any]] | None = None,
) -> tuple[bool, str | None]:
    """富化单个站点并回写。返回 (是否成功, 失败原因)——成功时原因为 None。

    只在「成功抓到首页」时更新 enriched_at；httpx 三通道全失败时尝试
    无头浏览器渲染兜底（反爬 403 的大站），仍失败留待下一轮自动重试。
    失败原因分层收集（DNS/超时/TLS/HTTP 状态码/指纹层/渲染层），写入
    field_meta.enrich_fail 供详情页展示（用户需求：每条线索的失败原因描述）。
    """
    base = website if website.startswith(("http://", "https://")) else f"https://{website}"
    homepage, fail_reasons = await _fetch_site_detailed(clients, base)
    if homepage is None:
        # 第二层：Chrome 指纹伪装（大多数反爬站到此为止，不用开浏览器）
        homepage, imp_reason = await _fetch_impersonated(base)
        if homepage is not None:
            await ctx.log("info", f"[lead {lead_id}] 🥷 Chrome 指纹伪装通过：{website}")
        elif imp_reason:
            fail_reasons.append(imp_reason)
    render_attempted = False
    if homepage is None and get_browser is not None:
        # 第三层：无头浏览器渲染（JS 验证挑战站才需要）
        browser = await get_browser()
        if browser is not None:
            render_attempted = True
            homepage = await _render_with_browser(browser, base)
            if homepage is not None:
                await ctx.log("info", f"[lead {lead_id}] 🌐 浏览器渲染兜底成功：{website}")
    if homepage is None:
        if render_attempted:
            fail_reasons.append("浏览器渲染失败")
        reason = "；".join(dict.fromkeys(r for r in fail_reasons if r)) or "未知原因"
        await ctx.log("warn", f"[lead {lead_id}] 首页抓取失败：{website} —— {reason}")
        await _record_enrich_fail(lead_id, website, reason)
        return False, reason

    # 官网归属校验（2026-09-01）：标题=知名平台且与公司名零重叠 → 张冠李戴
    # （实测：酷集科技→酷狗音乐、艾普锐→QQ邮箱镜像、宸星→汉典词典）。
    # 两证齐全才判错——凯越 vs "MU Group" 字面零重叠但不是错配，宁存疑不误杀
    from app.models.lead import Lead as _Lead

    async with _session_factory()() as _s:
        _row = await _s.get(_Lead, lead_id)
        _company_name = _row.name if _row else ""
    # 中国企业标记（外销站英文页探测的触发条件）：国内站常无 WA，外销版才挂
    _lead_is_cn = bool(_row and (_row.is_cn or _row.country == "CN"))
    owns_site, site_title = site_matches_company(homepage, _company_name)
    if not owns_site:
        await ctx.log(
            "warn",
            f"[lead {lead_id}] ⚠️ 官网错配清除：{website} 站点是「{site_title[:40]}」，与「{_company_name}」无关联",
        )
        await _clear_mismatched_website(lead_id, website, site_title)
        return False, f"官网错配已清除（站点={site_title[:30]}）"

    # 首页里找内页链接（联系/关于/产品，最多 3 个，域名相同才跟）
    base_domain = extract_domain(base)
    inner_urls = find_inner_page_urls(homepage, base, base_domain)
    # 常规联系路径探测（2026-09-01 用户需求：每家联系页路径不一样）——首页
    # 链接里没有联系页时（导航 JS 渲染/路径冷门），直接探惯例路径，抓到即补；
    # /lxwm 等老式路径是 GBK 工厂站（laifen 实测）的常见形态
    has_contact_page = any(_INNER_CONTACT_WORDS_RE.search(u) for u in inner_urls)
    if not has_contact_page:
        for path in (
            "/contact",
            "/contact/",
            "/contact-us",
            "/contact-us/",
            "/contact.html",
            "/Contact/contact.html",
            "/lianxi",
            "/lianxiwomen",
            "/lianxi.html",
            "/lxwm",
            "/lxwm.asp",
            "/contact.asp",
            "/about",
            "/about-us",
            "/support",
            "/get-in-touch",
        ):
            probe = _resolve_url(base, path)
            if probe and probe not in inner_urls:
                html_probe = await _fetch_site(clients, probe)
                if html_probe and ("联系" in html_probe or "contact" in html_probe.lower()):
                    inner_urls.insert(0, probe)  # 联系页优先补入
                    # 补入后截断：联系页把优先级最低的产品页挤出去，内页仍 ≤3
                    del inner_urls[_MAX_INNER_PAGES:]
                    await ctx.log("info", f"[lead {lead_id}] 🔍 常规路径探测命中联系页：{path}")
                    break
    if not inner_urls:
        # 老式站兜底（2026-09-01 laifen 实测）：联系信息挂在 asp-bin/GB/?page=1
        # 这类无关键词 query 页，词表与惯例路径全够不着——取首页同域普通链接
        wild = find_wildcard_page_urls(homepage, base, base_domain)
        if wild:
            inner_urls = wild
            await ctx.log(
                "info",
                f"[lead {lead_id}] 🧭 无联系/产品内页，取首页同域链接兜底：{wild}",
            )
    pages = [homepage]
    page_urls = [base]  # 与 pages 平行：每页 HTML 对应的 URL（证据链用）
    for url in inner_urls:
        # 内页与首页同等待遇（2026-08-31 审计：此前只有主 client 单次机会——
        # 代理软拦截/证书问题的站点首页成功、联系页全挂，而 WA/邮箱恰在联系页）
        html = await _fetch_site(clients, url)
        if html is None and _INNER_CONTACT_WORDS_RE.search(url):
            # 联系类内页补第二层（FR-1.5「与首页同等待遇」）：httpx 打不开的
            # 反爬站（banggood 型 403）联系方式恰在联系页，指纹层毫秒级开销
            html, _imp_reason = await _fetch_impersonated(url)
        if html:
            pages.append(html)
            page_urls.append(url)

    # 联系页 SPA 壳渲染兜底（2026-09-01 govee 实测形态）：/contact 返回 200
    # 但是应用壳（Shopify 型），联系方式全在 JS 挂件里——整站联系方式证据为
    # 零且存在联系类内页时，渲染该页一次（预算：每站 ≤1 次渲染）
    contact_url = next((u for u in inner_urls if _INNER_CONTACT_WORDS_RE.search(u)), None)
    if (
        contact_url
        and get_browser is not None
        and not (
            detect_email(pages, mailto_only=True)
            or detect_email(pages)
            or detect_jsonld_contacts(pages).get("email")
            or detect_tel_phones(pages)
            or detect_text_phones(pages)
            or detect_contact_persons(pages)
            or detect_whatsapp(pages)[0]
        )
    ):
        browser = await get_browser()
        if browser is not None:
            rendered = await _render_with_browser(browser, contact_url)
            if rendered:
                pages.append(rendered)
                page_urls.append(contact_url)
                await ctx.log(
                    "info", f"[lead {lead_id}] 🌐 联系页为 SPA 壳，渲染兜底：{contact_url}"
                )

    whatsapp_hit, whatsapp_url = detect_whatsapp(pages)
    wa_numbers = detect_whatsapp_numbers(pages)
    # 外销站英文联系页（2026-09-01 靶心补强）：中国企业国内站全页无 WA 时，
    # WA 常挂在外销版（en. 子域=同注册人 / 同域 /en/ 路径，零错配风险）——
    # 补抓有联系信息的英文页后再检测一轮（靶心「在用 WA 承接客户」高发位）
    if _lead_is_cn and not whatsapp_hit and not wa_numbers:
        for html, url in await _probe_export_en_pages(clients, base, base_domain):
            pages.append(html)
            page_urls.append(url)
        if detect_whatsapp(pages)[0] or detect_whatsapp_numbers(pages):
            await ctx.log(
                "info", f"[lead {lead_id}] 🌍 外销站英文页命中 WhatsApp（en.子域 或 /en/ 路径）"
            )
            whatsapp_hit, whatsapp_url = detect_whatsapp(pages)
            wa_numbers = detect_whatsapp_numbers(pages)
    # schema.org JSON-LD 声明的联系方式——网站主的机器可读数据，权威度高于正则启发；
    # 邮箱取值次序（FR-1.5）：mailto 显式链接 > JSON-LD 声明 > 正文正则
    jsonld = detect_jsonld_contacts(pages)
    jsonld_email = jsonld.get("email")
    if jsonld_email and not _is_email(jsonld_email):
        jsonld_email = None  # JSON-LD 声明同样过埋点黑名单（模板误填不采信）
    email = detect_email(pages, mailto_only=True) or jsonld_email or detect_email(pages)
    social = detect_social(pages)
    scenes = detect_scenes(pages)
    saas_signals = detect_saas_signals(pages)
    # 出海信号（PRD §4.2）：货币/多语言/电商栈/配送/市场/出海自述/海外域名
    overseas = detect_overseas_signals(pages)
    tld = detect_domain_tld(base)
    if tld:
        overseas.setdefault("domain_tld", []).append(tld)
    # WhatsApp Business 使用（§4.1）+ 群组链接（§4.1 私域证据）
    wa_business = detect_wa_business(pages)
    wa_groups = detect_whatsapp_groups(pages)

    async with _session_factory()() as session:
        from app.crud.contact import auto_create_from_email
        from app.crud.lead import touch_field_meta
        from app.crud.lead_events import rescore_and_log, snapshot_lead
        from app.models.lead import Lead

        lead = await session.get(Lead, lead_id)
        if lead is None:
            return False, "线索已不存在"
        before = snapshot_lead(lead)
        now = datetime.now(timezone.utc)
        if whatsapp_hit:
            lead.whatsapp_hit = True
            if whatsapp_url:
                lead.whatsapp_url = whatsapp_url
            # WhatsApp 检测来源=官网，置信度高（§32）
            touch_field_meta(lead, "whatsapp_url", "website_enrich", confidence=98, now=now)
        elif (
            before.get("whatsapp_hit")
            and (lead.field_meta or {}).get("whatsapp_url", {}).get("source") == "website_enrich"
        ):
            # 负证据（2026-08-31 审计）：此前检测到官网 WA 入口、本轮成功抓到
            # 首页但未复现。不翻 whatsapp_hit 布尔列（历史事实保留、评分不动），
            # 只发 whatsapp_gone 事件进时间线——销售能看到"信号可能过期"，
            # 建联前先核验。此前该事件类型只有词表没有写入方，负证据闭环缺失。
            # 来源门槛（2026-09-01 审计）：whatsapp_hit/url 也会被 meta_ads 主页
            # 探测写入（FB 主页挂 WA ≠ 官网入口）——只有官网富化写入过的入口
            # 消失才算「官网入口未复现」，否则 FB-WA 线索每轮都误发 gone 事件。
            from app.crud.lead_events import add_event

            add_event(
                session,
                lead,
                "whatsapp_gone",
                payload={"checked_pages": len(pages), "website": base},
                note=(
                    f"复查 {len(pages)} 个页面未复现 WhatsApp 入口"
                    f"（原入口：{lead.whatsapp_url or '官网'}）——信号可能已过期，建联前建议核验"
                ),
            )
        if wa_numbers:
            # 多号码证据链（§4.1）：Sales/Support 分线 = 规模化私域，只增不减
            merged_numbers = list(lead.whatsapp_numbers or [])
            for n in wa_numbers:
                if n not in merged_numbers:
                    merged_numbers.append(n)
            lead.whatsapp_numbers = merged_numbers
            touch_field_meta(lead, "whatsapp_numbers", "website_enrich", confidence=95, now=now)
        if email and not lead.email:
            lead.email = email
            touch_field_meta(lead, "email", "website_enrich", confidence=90, now=now)
        if social:
            merged = dict(lead.social or {})
            merged.update(social)
            lead.social = merged
            touch_field_meta(lead, "social", "website_enrich", confidence=95, now=now)
        if scenes:
            merged_scenes = list(lead.scenes or [])
            for s in scenes:
                if s not in merged_scenes:
                    merged_scenes.append(s)
            lead.scenes = merged_scenes  # 重新赋值触发 JSON 变更追踪
            touch_field_meta(lead, "scenes", "website_enrich", confidence=80, now=now)
        if saas_signals:
            merged_saas = dict(lead.saas_signals or {})
            for k, v in saas_signals.items():
                merged_saas[k] = max(merged_saas.get(k, 0), v)
            lead.saas_signals = merged_saas
            touch_field_meta(lead, "saas_signals", "website_enrich", confidence=75, now=now)
        if wa_business and not lead.wa_business:
            lead.wa_business = True
            touch_field_meta(lead, "wa_business", "website_enrich", confidence=75, now=now)
        # 中国企业证据（ICP 二重门·门1）：ICP 备案号 > 中文内容占比——
        # 纯英文站的中国出海企业常保留备案号，是防误杀为 foreign 的关键证据
        icp_license = detect_icp_license(pages)
        if icp_license and not lead.is_cn:
            lead.is_cn = True
            touch_field_meta(lead, "is_cn", "website_enrich", confidence=98, now=now)
        elif not lead.is_cn and detect_cn_content(pages):
            # 官网含显著中文内容——中文站服务海外市场 = 出海企业
            lead.is_cn = True
            touch_field_meta(lead, "is_cn", "website_enrich", confidence=85, now=now)
        # JSON-LD 声明的地址优先于正则启发（无标签正文）
        if not lead.address and jsonld.get("address"):
            lead.address = jsonld["address"]
            touch_field_meta(lead, "address", "website_enrich", confidence=95, now=now)
        # 基础画像补全（country/industry/address）——富化只补信号不补画像的空白
        backfill_profile_fields(lead, icp_license=icp_license, pages=pages, now=now)
        if overseas:
            merged_ov = dict(lead.overseas_signals or {})
            for k, vals in overseas.items():
                bucket = list(merged_ov.get(k) or [])
                for v in vals or []:
                    if v not in bucket:
                        bucket.append(v)
                merged_ov[k] = bucket
            lead.overseas_signals = merged_ov
            touch_field_meta(lead, "overseas_signals", "website_enrich", confidence=85, now=now)
        # ---------- 信号级证据链（§4.1：类型/值/来源页面/原文/置信度/时间） ----------
        from app.crud.lead_signals import upsert_signal

        # 群邀请链接 = 社群私域运营证据（§4.1/§4.4-E）
        for g in wa_groups:
            await upsert_signal(
                session,
                lead.id,
                "whatsapp_group",
                g,
                source="website_enrich",
                evidence_url=base,
                evidence_raw=g,
                confidence=90,
            )
        if wa_business:
            await upsert_signal(
                session,
                lead.id,
                "wa_business",
                "WhatsApp Business",
                source="website_enrich",
                evidence_url=base,
                evidence_raw="页面自述使用 WhatsApp Business",
                confidence=75,
            )
        if icp_license:
            # CN 证据入证据链：销售可见"为什么判定是中国企业"
            await upsert_signal(
                session,
                lead.id,
                "cn_icp",
                icp_license,
                source="website_enrich",
                evidence_url=base,
                evidence_raw=f"页脚备案号：{icp_license}",
                confidence=98,
            )

        for i, page_html in enumerate(pages):
            page_url = page_urls[i] if i < len(page_urls) else base
            for pat in _PHONE_PATTERNS:
                for m in pat.finditer(page_html or ""):
                    raw = (m.group(1) or "").lstrip("+")
                    if raw:
                        await upsert_signal(
                            session,
                            lead.id,
                            "whatsapp_number",
                            raw,
                            source="website_enrich",
                            evidence_url=page_url,
                            evidence_raw=m.group(0),
                            confidence=95,
                        )
            for pat in _PLUGIN_PATTERNS:
                m = pat.search(page_html or "")
                if m:
                    await upsert_signal(
                        session,
                        lead.id,
                        "whatsapp_plugin",
                        "detected",
                        source="website_enrich",
                        evidence_url=page_url,
                        evidence_raw=m.group(0)[:200],
                        confidence=75,
                    )
        if whatsapp_url:
            await upsert_signal(
                session,
                lead.id,
                "whatsapp_link",
                whatsapp_url,
                source="website_enrich",
                evidence_url=base,
                evidence_raw=whatsapp_url,
                confidence=98,
            )
        _OV_TYPE_MAP = {
            "currencies": ("overseas_currency", 85),
            "languages": ("multilang", 80),
            "ecommerce": ("ecommerce_stack", 90),
            "shipping": ("intl_shipping", 80),
            "markets": ("market_mention", 75),
            "export_words": ("export_word", 75),
        }
        for ov_key, vals in (overseas or {}).items():
            sig_type, conf = _OV_TYPE_MAP.get(ov_key, (ov_key, 75))
            for v in vals or []:
                await upsert_signal(
                    session,
                    lead.id,
                    sig_type,
                    str(v),
                    source="website_enrich",
                    evidence_url=base,
                    evidence_raw=str(v)[:200],
                    confidence=conf,
                )
        if email:
            # 抓到公开邮箱 → 自动生成「待补全」联系人（同邮箱已存在则跳过）
            await auto_create_from_email(session, lead, email)
        # ---------- 联系方式补全（「找谁」是爬取最关键的产出） ----------
        # tel: 链接电话 → 线索电话（此前只抓邮箱不抓电话）
        from app.collectors.normalize import normalize_phone

        tel_phones = detect_tel_phones(pages)
        # 明文国际格式电话（「CONTACT US +86 137 3602 8159」类——2026-09-01 实测
        # mugroup.com：多数联系页不写 tel: 链接，电话就是正文文本）
        text_phones = detect_text_phones(pages)

        def _phone_rank(raw: str) -> int:
            # 选取定序（2026-09-01 kaadas 实测教训：先抓到的 400 是招商热线，
            # 不是对外联系电话）：座机总机 0 → 国际 1 → 400 热线 2 → 手机 3
            # （手机归具名联系人所有，只作总机/热线全缺时的兜底）
            r = raw.strip()
            if r.startswith("0"):
                return 0
            if r.startswith("+"):
                return 1
            if r.startswith("400"):
                return 2
            return 3

        phone_candidates = [x for x in (jsonld.get("phone"), *tel_phones, *text_phones) if x]
        phone_candidates.sort(key=_phone_rank)
        best_phone: tuple[str, str] | None = None
        for raw in phone_candidates:
            region = "CN" if (lead.is_cn or (lead.country or "").upper() == "CN") else None
            e164 = normalize_phone(raw, region)
            if e164:
                best_phone = (raw, e164)
                break
        # 更优序位可替换（2026-09-01 kaadas 教训：旧轮先抓到的 400 招商热线
        # 占住字段后，新一轮的总机座机永远进不来——只填空不更新会锁死错选）
        if best_phone and (
            not lead.phone_e164 or _phone_rank(best_phone[0]) < _phone_rank(lead.phone_raw or "")
        ):
            lead.phone_raw = best_phone[0]
            lead.phone_e164 = best_phone[1]
            touch_field_meta(lead, "phone_e164", "website_enrich", confidence=85, now=now)
        # WhatsApp 号码 → 自动联系人（name 待补全、电话即号码——销售的直接建联对象）
        from app.crud.contact import auto_create_from_phone

        for n in wa_numbers[:3]:
            await auto_create_from_phone(session, lead, n)
        # 具名联系人（2026-09-01 kaadas 实测：「海外事业部 联系人：屈先生
        # 联系电话：189-2522-1831」——比泛邮箱强一个量级的「找谁」答案）
        persons = detect_contact_persons(pages)
        if persons:
            from app.models.lead import LeadContact

            existing_phones = {
                c.phone
                for c in (
                    await session.execute(
                        _sa_select(LeadContact).where(LeadContact.lead_id == lead.id)
                    )
                )
                .scalars()
                .all()
            }
            added = 0
            for psn in persons:
                if psn["phone"] in existing_phones or added >= 6:
                    continue
                title = psn.get("title", "")
                seniority = (
                    "tier1"
                    if re.search(r"负责人|总监|总经理|创始人|CEO", title)
                    else "tier2"
                    if re.search(r"经理|主管|部长", title)
                    else "unknown"
                )
                session.add(
                    LeadContact(
                        lead_id=lead.id,
                        name=psn["name"],
                        job_title=title or None,
                        phone=psn["phone"],
                        seniority=seniority,
                        source="website_enrich",
                        confidence=88,
                    )
                )
                existing_phones.add(psn["phone"])
                added += 1
            if added:
                touch_field_meta(lead, "contacts_persons", "website_enrich", confidence=88, now=now)
                await ctx.log("info", f"[lead {lead_id}] 👤 具名联系人 +{added}（含部门/手机号）")
        lead.enriched_at = now
        # 成功自愈：清除历史失败标记（field_meta.enrich_fail 只反映最近一次富化）
        meta = dict(lead.field_meta or {})
        if meta.pop("enrich_fail", None) is not None:
            lead.field_meta = meta
        # 统一重评钩子：意向分重算（读 ORM 行属性，fb_whatsapp 不再漏传）+ 事件发射
        await rescore_and_log(session, lead, before=before)
        await session.commit()
    if whatsapp_hit:
        await ctx.log("info", f"[lead {lead_id}] ✅ 检测到 WhatsApp：{website}")
    if scenes:
        labels = "、".join(SCENE_LABELS_ZH.get(s, s) for s in scenes)
        await ctx.log("info", f"[lead {lead_id}] 场景命中：{labels}")
    if saas_signals:
        labels = "、".join(SAAS_LABELS_ZH.get(k, k) for k in saas_signals)
        await ctx.log("info", f"[lead {lead_id}] SaaS 需求信号：{labels}")
    return True, None
