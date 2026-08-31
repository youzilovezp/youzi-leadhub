"""基础画像补全（2026-09-01 用户反馈：web_search 线索基础信息大面积空白）。

backfill_profile_fields 是纯函数（fake lead 即可测）：
- country 只认 CN 硬证据（ICP 备案号 / +86 / 强来源），仅 CJK 启发式不回填
- industry 空时按公司名五类归类回填中文标签
- address 抓联系页「地址/Address」行，截断到电话/邮箱等后继字段
"""

from datetime import datetime, timezone

from app.collectors.website_enrich import backfill_profile_fields


class _Lead:
    def __init__(self, **kw):
        self.name = "测试跨境电商（深圳）有限公司"
        self.country = None
        self.industry = None
        self.address = None
        self.is_cn = False
        self.phone_e164 = None
        self.sources: list = []
        self.field_meta: dict = {}
        for k, v in kw.items():
            setattr(self, k, v)


_NOW = datetime.now(timezone.utc)


def test_country_backfill_requires_hard_evidence():
    """弱证据（仅 is_cn 布尔 + web_search 来源）不回填——东南亚华人企业防误标。"""
    lead = _Lead(is_cn=True, sources=[{"source": "web_search"}])
    backfill_profile_fields(lead, icp_license=None, pages=[], now=_NOW)
    assert lead.country is None

    # ICP 备案号 = 硬证据 → CN
    lead2 = _Lead(is_cn=True, sources=[{"source": "web_search"}])
    backfill_profile_fields(lead2, icp_license="粤ICP备12345678号", pages=[], now=_NOW)
    assert lead2.country == "CN"

    # +86 号码 = 强证据 → CN
    lead3 = _Lead(is_cn=True, phone_e164="+8613800138000")
    backfill_profile_fields(lead3, icp_license=None, pages=[], now=_NOW)
    assert lead3.country == "CN"

    # 已有国家不覆盖
    lead4 = _Lead(is_cn=True, country="MY", phone_e164="+8613800138000")
    backfill_profile_fields(lead4, icp_license=None, pages=[], now=_NOW)
    assert lead4.country == "MY"


def test_industry_backfill_from_company_name():
    lead = _Lead()
    backfill_profile_fields(lead, icp_license=None, pages=[], now=_NOW)
    assert lead.industry == "跨境电商/品牌DTC"

    # 来源已给的行业不覆盖
    lead2 = _Lead(industry="3C电子")
    backfill_profile_fields(lead2, icp_license=None, pages=[], now=_NOW)
    assert lead2.industry == "3C电子"

    # 名字归不了类 → 不硬造
    lead3 = _Lead(name="某某国际贸易行")
    backfill_profile_fields(lead3, icp_license=None, pages=[], now=_NOW)
    assert lead3.industry is None


def test_address_backfill_truncates_at_following_fields():
    pages = [
        "<html><body>联系我们 地址：深圳市南山区科技园8栋 电话：0755-12345678 "
        "邮箱：x@a.com</body></html>"
    ]
    lead = _Lead()
    backfill_profile_fields(lead, icp_license=None, pages=pages, now=_NOW)
    assert (lead.address or "").strip() == "深圳市南山区科技园8栋"

    # 无地址行 → 不硬造
    lead2 = _Lead()
    backfill_profile_fields(
        lead2, icp_license=None, pages=["<p>hello world</p>"], now=_NOW
    )
    assert lead2.address is None
