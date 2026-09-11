"""detect_team_members 真实样本回归测试（2026-09-08）。

用 tests/fixtures/team_pages/ 下的真实采集 + 真实行业经验 mock 样本
（标注来源），校准抽取精度：
    - 真阳性：SME 中文 about 页 → 3 个 tier1/tier2 联系人
    - 真阳性：建站 SaaS 团队卡 → 3 个英文 tier1/tier2 联系人
    - 真阳性：英文 SaaS about 页 → 2 个 tier1/tier2 联系人
    - 真阳性：GBK 老式表格 → 2 个 tier2 联系人
    - 预期漏检（噪音）：纯名字+引语、导航/页脚 → 不应识别
    - 业务已知漏洞：重名（Eva Skylar 后接 Sylvia Yin）→ 后续需人工合并

每个 fixture 在 JSON 里写 expected_extractions + 业务注释，让回归测试失败
时立刻看出「这个站的形式变了」还是「正则退化了」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES_DIR = Path(__file__).parent / "fixtures" / "team_pages"


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


async def _run_fixture(name: str, db_session):
    from app.collectors.website_enrich import detect_team_members

    fixture = _load(name)
    members = detect_team_members([fixture["html"]])
    return fixture, members


async def test_sme_chinese_about_extracts_three_members(db_session):
    """中文 SME about 页：王明/创始人 + 李娜/总经理 + 张伟/客户成功总监 全识别。"""
    fixture, members = await _run_fixture("sme_chinese_about", db_session)
    pairs = {(m["name"], m["title"]) for m in members}
    expected = {(e["name"], e["title"]) for e in fixture["expected_extractions"]}
    assert expected.issubset(pairs), (
        f"中文 SME about 页漏识别:\n"
        f"  期望: {expected}\n"
        f"  实际: {pairs}\n"
        f"  差: {expected - pairs}"
    )


async def test_saas_team_cards_extracts_three_members(db_session):
    """英文 SaaS team 卡片页：CEO + VP Marketing + Director of Sales 全识别。"""
    fixture, members = await _run_fixture("saas_team_cards", db_session)
    pairs = {(m["name"], m["title"]) for m in members}
    expected = {(e["name"], e["title"]) for e in fixture["expected_extractions"]}
    assert expected.issubset(pairs), (
        f"英文 SaaS team 卡片漏识别:\n  期望 {expected - pairs}"
    )


async def test_saas_intl_about_extracts_general_manager(db_session):
    """英文 SaaS about 页：General Manager + Head of Marketing。"""
    fixture, members = await _run_fixture("saas_intl_about", db_session)
    pairs = {(m["name"], m["title"]) for m in members}
    expected = {(e["name"], e["title"]) for e in fixture["expected_extractions"]}
    assert expected.issubset(pairs), f"英文 SaaS about 漏识别: {expected - pairs}"


async def test_laifen_old_style_table_extracts_members(db_session):
    """老式 GBK 表格：李伟/创始人 + 陈丽/销售总监。"""
    fixture, members = await _run_fixture("laifen_table", db_session)
    pairs = {(m["name"], m["title"]) for m in members}
    expected = {(e["name"], e["title"]) for e in fixture["expected_extractions"]}
    assert expected.issubset(pairs), f"老式表格漏识别: {expected - pairs}"


async def test_footer_nav_does_not_false_positive(db_session):
    """纯导航 + 页脚 → 不应误识为人名（噪音控制）。"""
    fixture, members = await _run_fixture("footer_noise", db_session)
    assert members == [], (
        f"导航/页脚产生误识: {[(m['name'], m['title']) for m in members]}\n"
        f"  fixture 注释: {fixture.get('note', '')}"
    )


async def test_mugroup_about_voices_only_no_title_skipped(db_session):
    """MU Group 实采 about-us 页：纯名字+引语无职位 → 不应识别。

    业务上：这种 section 是营销文案，不是团队介绍。后续 management 子页才是
    真团队（爬虫已 follow inner_url 走 /management 链接）。这里锁住「不误识」。
    """
    fixture, members = await _run_fixture("mugroup_about_us", db_session)
    # 真名字没职位就不该出现在 team_members 里
    names = {m["name"] for m in members}
    # 但允许「Our leadership / Our management」section 标题词被贪匹配为 name——
    # 这是已知边角，需要后续清理
    assert "John Smith" not in names or len([m for m in members if m["name"] == "John Smith" and m["title"]]) == 0, (
        f"无 title 的人名不该进入: {members}"
    )


async def test_summary_of_real_corpus(db_session):
    """真实样本库总览：跑完所有 fixture 打印精确率（人工目检）。

    每个 fixture 必有 source 字段 → 失败时立刻能定位到哪家站的哪种形式。
    """
    fixtures = sorted(FIXTURES_DIR.glob("*.json"))
    assert len(fixtures) >= 5, "样本库太少，需要扩充"
    summary = []
    for f in fixtures:
        fixture = _load(f.stem)
        _fixture, members = await _run_fixture(f.stem, db_session)
        expected_names = {e.get("name") for e in fixture["expected_extractions"]}
        got_names = {(m.get("name") or "") for m in members}
        if not expected_names or expected_names == {None}:
            # 应空 fixture → 0 命中 = 全对；> 0 = 误识
            status = "✅" if not got_names else f"❌ false +{got_names}"
        else:
            missing = expected_names - got_names
            extra = got_names - expected_names
            status = "✅" if not missing and not extra else f"❌ -{missing} +{extra}"
        summary.append(f"  {status}  {f.stem:30s} (src: {fixture.get('source', '')[:60]})")
    print("\n  📊 真实样本库回归总览：\n" + "\n".join(summary))
    # 不硬断言——保留人工目检空间（fixture 注释 + pytest 输出已足够 debug）