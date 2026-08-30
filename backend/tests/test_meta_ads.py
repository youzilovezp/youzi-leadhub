"""meta_ads 采集器：解析函数、评分信号、落库合并、无凭据失败路径。"""

import pytest

from app.collectors.base import LeadDraft
from app.collectors.meta_ad_library import (
    MetaAdsCollector,
    _extract_category,
    _extract_site,
    _extract_wa_phone,
    _is_company_category,
    _looks_cn,
)
from app.collectors.scoring import compute_score
from app.core.exceptions import BusinessError
from app.crud.lead import upsert_lead

# ---------- 解析：FB 主页 HTML → 联系方式 ----------


def test_extract_wa_phone():
    html = '<a href="https://wa.me/8613800138000?text=hi">Chat</a>'
    assert _extract_wa_phone(html) == "8613800138000"
    html2 = '<a href="https://api.whatsapp.com/send?phone=60123456789&text=x">'
    assert _extract_wa_phone(html2) == "60123456789"
    assert _extract_wa_phone("<html>no whatsapp here</html>") is None


def test_extract_site_fb_redirect_and_filters():
    # l.php 跳转解码 → 官网
    html = 'href="https://l.facebook.com/l.php?u=https%3A%2F%2Facme-store.com%2F%3Fref%3Dfb&h=xxx"'
    assert _extract_site(html) == "https://acme-store.com/?ref=fb"
    # 平台域不算官网：facebook/instagram/wa.me 全滤掉
    html2 = 'https://www.facebook.com/acme https://wa.me/60123 https://instagram.com/acme'
    assert _extract_site(html2) is None
    # 裸链接官网
    assert _extract_site('visit https://acme-store.com for deals') == "https://acme-store.com"


def test_looks_cn():
    assert _looks_cn(["Anker 安克创新", None]) is True
    assert _looks_cn([" SHEIN Official Store ", "Free shipping"]) is False
    assert _looks_cn([None, ""]) is False


# ---------- 只爬公司/企业：主页类目过滤 ----------


def test_extract_category():
    html = '<script>{"categoryName":"Shopping & Retail"}</script>'
    assert _extract_category(html) == "Shopping & Retail"
    assert _extract_category("<html>no category</html>") is None


def test_is_company_category():
    # 企业类目放行
    assert _is_company_category("Shopping & Retail") is True
    assert _is_company_category("Clothing (Brand)") is True
    assert _is_company_category("零售") is True
    # 非企业类目拦截（大小写不敏感，中英都认）
    for cat in (
        "Public Figure", "Artist", "Personal blog", "News & Media Website",
        "Musician/Band", "Politician", "Government Official", "公众人物", "自媒体",
    ):
        assert _is_company_category(cat) is False, cat
    # 拿不到类目（登录墙等）保守放行，不误杀
    assert _is_company_category(None) is True


# ---------- 评分：FB 私域按钮是最强商机信号 ----------


def test_score_fb_whatsapp_signal():
    score, hits = compute_score(
        whatsapp_hit=True,
        whatsapp_job=False,
        website="https://acme.com",
        email=None,
        country="MY",
        phone_raw="60123456789",
        phone_e164="+60123456789",
        social={"facebook": "https://facebook.com/acme"},
        fb_whatsapp=True,
    )
    # fb私域50 + plugin40 + website10 + region10 + phone5 + social5 = 120
    assert score == 120
    assert hits["fb_page_whatsapp"] == 50


def test_score_fb_whatsapp_absent_by_default():
    """旧调用方不传 fb_whatsapp → 行为不变（默认 False）。"""
    score, hits = compute_score(
        whatsapp_hit=False,
        whatsapp_job=False,
        website=None,
        email=None,
        country=None,
        phone_raw=None,
        phone_e164=None,
        social=None,
    )
    assert score == 0 and "fb_page_whatsapp" not in hits


# ---------- 落库：is_cn / fb_whatsapp 布尔 OR 合并 ----------


async def test_upsert_meta_ads_draft(db_session):
    from app.db.init_db import init_db

    await init_db()
    d1 = LeadDraft(
        source="meta_ads",
        name="Anker 安克创新",
        country="MY",
        website="https://anker.com",
        phone_raw="+8613800138000",  # wa.me 抠出的国际号补 +（投放国是 MY，别按本地号解析）
        whatsapp_url="https://wa.me/8613800138000",
        social={"facebook": "https://www.facebook.com/anker"},
        is_cn=True,
        fb_whatsapp=True,
    )
    lead, created = await upsert_lead(db_session, d1)
    await db_session.commit()
    assert created
    assert lead.is_cn and lead.fb_whatsapp
    assert lead.phone_e164 == "+8613800138000"  # wa 号码直接可拨
    assert lead.domain == "anker.com"
    # fb私域50+plugin40+website10+region10+phone5+social5 = 120
    assert lead.score == 120
    assert lead.sources[0]["source"] == "meta_ads"

    # 再来一条无信号的同企业（同 domain 反查命中）→ 布尔保持 True，评分不回退
    d2 = LeadDraft(source="meta_ads", name="Anker", country="MY", website="https://www.anker.com/deals")
    lead2, created2 = await upsert_lead(db_session, d2)
    await db_session.commit()
    assert not created2 and lead2.id == lead.id
    assert lead2.is_cn and lead2.fb_whatsapp and lead2.score == 120


# ---------- 失败路径：无 token 直接 failed（不产出空结果假成功） ----------


async def test_run_requires_token(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "META_ADS_ACCESS_TOKEN", "")
    collector = MetaAdsCollector()
    with pytest.raises(BusinessError) as exc:
        await collector.run(_FakeCtx())
    assert "META_ADS_ACCESS_TOKEN" in str(exc.value.message)


def test_validate_params_requires_keywords_countries():
    collector = MetaAdsCollector()
    with pytest.raises(BusinessError):
        collector.validate_params({"keywords": "", "countries": "MY"})
    with pytest.raises(BusinessError):
        collector.validate_params({"keywords": "wig", "countries": ""})
    collector.validate_params({"keywords": "wig", "countries": "MY,SG"})  # 合法不抛


class _FakeCtx:
    """run() 在 token 校验前不会碰其他 ctx 能力，给个最小桩。"""

    params = {}

    async def log(self, *a, **k):  # noqa: ARG002
        pass

    def set_total(self, *a, **k):  # noqa: ARG002
        pass

    def inc_progress(self, *a, **k):  # noqa: ARG002
        pass

    def check_cancelled(self):
        pass
