"""b2b_supplier 采集器测试：解析层对着真实站点夹具验，run() 打桩网络。

夹具（tests/fixtures/，2026-09-01 真实页面快照）：
    mic_search_wig.html        品类搜索页（wig）
    mic_supplier_root.html     店铺首页（yuanxiuhair = 许昌元秀）
    mic_company_profile.html   公司档案页（Main Markets 等公开字段）
"""

from pathlib import Path

import pytest

from app.collectors import get_collector
from app.collectors.base import LeadDraft
from app.collectors.b2b_supplier import (
    DEFAULT_KEYWORDS,
    build_draft,
    parse_company_profile,
    parse_search_page,
    parse_supplier_root,
)
from app.collectors.icp import cn_evidence_of, compute_icp_status
from app.core.exceptions import BusinessError

FIX = Path(__file__).parent / "fixtures"


def _fx(name: str) -> str:
    return (FIX / name).read_text(errors="ignore")


# ---------- 解析层（真实夹具） ----------


def test_parse_search_page_finds_supplier_slugs():
    slugs = parse_search_page(_fx("mic_search_wig.html"))
    assert "yuanxiuhair" in slugs
    assert "cf-hair" in slugs
    assert len(slugs) >= 10  # 单页约 15 家店铺
    assert "www" not in slugs  # 平台自身域排除
    assert len(slugs) == len(set(slugs))  # 去重


def test_parse_search_page_empty_html():
    assert parse_search_page("") == []


def test_parse_supplier_root_name_and_profile_path():
    info = parse_supplier_root(_fx("mic_supplier_root.html"))
    assert info["name"] == "Xuchang Yuanxiu Crafts Co., Ltd"
    assert info["profile_path"]
    assert "company-Xuchang-Yuanxiu-Crafts-Co-Ltd" in info["profile_path"]


def test_parse_supplier_root_title_fallback():
    # 无 h1 时回退 title 末段「… Supplier - {Company}」
    html = "<html><head><title>Widget Supplier - Acme Co., Ltd</title></head><body></body></html>"
    assert parse_supplier_root(html)["name"] == "Acme Co., Ltd"


def test_parse_company_profile_public_fields():
    prof = parse_company_profile(_fx("mic_company_profile.html"))
    assert "North America" in prof["markets"]
    assert len(prof["markets"]) >= 3  # 出口市场清单是一手出海证据
    assert prof["address"] and "Huangjin" in prof["address"]
    assert "FOB" in prof["incoterms"]
    assert prof["main_products"]  # Main Products 非空
    # 该供应商档案未填官网字段（实测多数不填——官网靠发现链补）
    assert prof["website"] is None


def test_parse_company_profile_website_field():
    html = ('<div class="info-item"><div class="info-label">Web Site:</div>'
            '<div class="info-fields"><a href="http://www.acme-widget.com">www.acme-widget.com</a></div></div>')
    prof = parse_company_profile(html)
    assert prof["website"] == "http://www.acme-widget.com"


def test_parse_company_profile_rejects_platform_link_as_website():
    # 「Verify Now」认证外链（bvcerchina.cn）不是公司官网：官网字段只认显式标签行
    html = ('<div class="info-item"><div class="info-label">BV Audit Report No. :</div>'
            '<div class="info-fields">MIC-ASI1</div></div>'
            '<span><a href="http://www.bvcerchina.cn">Verify Now</a></span>')
    assert parse_company_profile(html)["website"] is None


# ---------- 草稿 → ICP（需求文档对齐：CN 强证据 + 出海证据 → qualified） ----------


def _draft() -> LeadDraft:
    return build_draft(
        name="Xuchang Yuanxiu Crafts Co., Ltd",
        keyword="wig",
        profile={
            "main_products": ["Human Hair"],
            "address": "No.5 Huangjin Avenue",
            "markets": ["North America", "Eastern Europe", "Oceania"],
            "incoterms": ["FOB", "EXW"],
            "website": None,
        },
    )


def test_draft_is_cn_strong_and_qualified():
    d = _draft()
    assert d.source == "b2b_supplier"
    assert d.is_cn and d.country == "CN"
    assert d.overseas_signals.get("markets") == ["North America", "Eastern Europe", "Oceania"]
    status = compute_icp_status(
        name=d.name,
        is_cn=d.is_cn,
        country=d.country,
        overseas_signals=d.overseas_signals,
    )
    assert status == "qualified"
    # 来源算 CN 强证据（中国 B2B 出口平台，与 job_posting 同级）
    assert (
        cn_evidence_of(
            is_cn=d.is_cn,
            country=d.country,
            sources=[{"source": "b2b_supplier"}],
        )
        == "strong"
    )


def test_draft_without_markets_still_overseas_via_listing():
    d = build_draft(name="Acme Co., Ltd", keyword="wig", profile={})
    assert d.overseas_signals["b2b_export"]  # 挂单本身就是出海证据
    assert compute_icp_status(
        name=d.name, is_cn=d.is_cn, country=d.country, overseas_signals=d.overseas_signals
    ) == "qualified"


def test_draft_carries_website_when_profile_filled():
    d = build_draft(
        name="Acme",
        keyword="wig",
        profile={"website": "http://www.acme.com", "main_products": [], "address": None, "markets": [], "incoterms": []},
    )
    assert d.website == "http://www.acme.com"


# ---------- run()（网络打桩） ----------


class _FakeCtx:
    def __init__(self, params):
        self.params = params
        self.emitted: list[LeadDraft] = []
        self.logs: list[tuple[str, str]] = []
        self.total = 0
        self.progress = 0

    async def emit(self, draft, **kw):  # noqa: ARG002
        self.emitted.append(draft)
        return len(self.emitted), True

    async def log(self, level, message):
        self.logs.append((level, message))

    def set_total(self, n):
        self.total = n

    def inc_progress(self, n=1):
        self.progress += n

    def check_cancelled(self):
        pass


def _stub_fetch(monkeypatch, *, search: str, root: str, profile: str | None):
    """按 URL 形态返回夹具：品类页 / 店铺首页 / 档案页。"""

    async def fake_fetch(client, url):  # noqa: ARG001
        if "products-search" in url:
            return search
        if "/company-" in url:
            return profile
        if ".en.made-in-china.com/" in url:
            return root
        return None

    import app.collectors.b2b_supplier as mod

    monkeypatch.setattr(mod, "_fetch", fake_fetch)
    monkeypatch.setattr(mod, "_PAGE_GAP", 0.0)
    monkeypatch.setattr(mod, "_SUP_GAP", 0.0)


def _stub_fetch_none(monkeypatch):
    async def fake_fetch(client, url):  # noqa: ARG001, ARG002
        return None

    import app.collectors.b2b_supplier as mod

    monkeypatch.setattr(mod, "_fetch", fake_fetch)
    monkeypatch.setattr(mod, "_PAGE_GAP", 0.0)
    monkeypatch.setattr(mod, "_SUP_GAP", 0.0)


@pytest.mark.asyncio
async def test_run_emits_drafts_and_writes_signal(monkeypatch):
    _stub_fetch(
        monkeypatch,
        search=_fx("mic_search_wig.html"),
        root=_fx("mic_supplier_root.html"),
        profile=_fx("mic_company_profile.html"),
    )
    # 信号写库走真库太重：打桩 upsert_signal 捕获调用
    written: list[tuple[int, str]] = []

    class _Sig:
        async def __call__(self, session, lead_id, type_, content, **kw):  # noqa: ARG002
            written.append((lead_id, content))
            return None

    monkeypatch.setattr("app.crud.lead_signals.upsert_signal", _Sig())

    ctx = _FakeCtx({"keywords": "wig", "max_suppliers": "5"})
    collector = get_collector("b2b_supplier")
    assert collector is not None
    await collector.run(ctx)  # type: ignore[arg-type]

    assert ctx.emitted, "应产出至少一条草稿"
    d = ctx.emitted[0]
    assert d.source == "b2b_supplier"
    assert d.name == "Xuchang Yuanxiu Crafts Co., Ltd"
    assert d.is_cn and d.country == "CN"
    assert d.overseas_signals
    assert len(ctx.emitted) <= 5  # max_suppliers 预算生效
    assert ctx.total == len(ctx.emitted)  # set_total 与实际处理数一致（无失败时）
    assert written, "出海证据应写入信号链"
    assert "中国制造网" in written[0][1]
    assert any("出口市场" in c for _, c in written), "出口市场清单应进信号内容"


@pytest.mark.asyncio
async def test_run_tolerates_profile_failure(monkeypatch):
    # 档案页失败：只带店铺信息（公司名）入库，不算任务失败
    _stub_fetch(
        monkeypatch,
        search=_fx("mic_search_wig.html"),
        root=_fx("mic_supplier_root.html"),
        profile=None,
    )
    ctx = _FakeCtx({"keywords": "wig", "max_suppliers": "3"})
    collector = get_collector("b2b_supplier")
    assert collector is not None
    await collector.run(ctx)  # type: ignore[arg-type]
    assert ctx.emitted
    assert ctx.emitted[0].overseas_signals["b2b_export"]  # 挂单证据仍在


@pytest.mark.asyncio
async def test_run_all_pages_failed_raises(monkeypatch):
    _stub_fetch_none(monkeypatch)
    ctx = _FakeCtx({"keywords": "wig"})
    collector = get_collector("b2b_supplier")
    assert collector is not None
    with pytest.raises(BusinessError):
        await collector.run(ctx)  # type: ignore[arg-type]


def test_default_keywords_are_category_words():
    assert "wig" in DEFAULT_KEYWORDS
    assert len(DEFAULT_KEYWORDS.split(",")) >= 3


def test_registered_in_registry_and_chain():
    from app.collectors import list_collectors
    from app.services.task_runner import TaskRunner

    names = [c["name"] for c in list_collectors()]
    assert "b2b_supplier" in names
    # 发现类采集器：任务成功后自动接力全库富化（FR-8）
    assert "b2b_supplier" in TaskRunner._CHAIN_ENRICH_AFTER
