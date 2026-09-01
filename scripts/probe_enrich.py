#!/usr/bin/env python3
"""富化检测探针：对任意 URL 跑与 _enrich_one 相同的抓取+检测序列，不写库。

用途：检测器升级后用真实站点验证覆盖（开源集成实证原则 / 词表运营），
也用于验收「这家站的联系方式为什么没爬到」类问题——四层抓取原因分层打印。

用法（backend 目录下）：
    uv run python ../scripts/probe_enrich.py https://example.com [https://another.com ...]
"""

from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.collectors.website_enrich import (  # noqa: E402
    _fetch_impersonated,
    _fetch_site,
    _fetch_site_detailed,
    _INNER_CONTACT_WORDS_RE,
    _make_client,
    _MAX_INNER_PAGES,
    _resolve_url,
    detect_contact_persons,
    detect_email,
    detect_jsonld_contacts,
    detect_overseas_signals,
    detect_saas_signals,
    detect_scenes,
    detect_social,
    detect_tel_phones,
    detect_text_phones,
    detect_wa_business,
    detect_whatsapp,
    detect_whatsapp_groups,
    detect_whatsapp_numbers,
    find_inner_page_urls,
    find_wildcard_page_urls,
)
from app.collectors.normalize import extract_domain  # noqa: E402

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# 常规联系路径（与 _enrich_one 内联列表保持一致）
_FALLBACK_PATHS = (
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
)


def _title(html: str) -> str:
    m = _TITLE_RE.search(html)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:80] if m else "(无 <title>)"


async def fetch_homepage(base: str) -> tuple[str | None, list[str]]:
    """四层抓取：httpx 双通道 → curl_cffi 指纹（渲染层探针不启，成本高）。"""
    async with _make_client() as primary, _make_client(verify=False, trust_env=False) as loose:
        homepage, reasons = await _fetch_site_detailed((primary, loose), base)
    layer = "httpx"
    if homepage is None:
        homepage, imp_reason = await _fetch_impersonated(base)
        if homepage is not None:
            layer = "curl_cffi 指纹"
        elif imp_reason:
            reasons.append(imp_reason)
    return homepage, ([f"[{layer}] " + r for r in reasons] if reasons else ([layer] if homepage else reasons))


async def probe(url: str) -> None:
    base = url if url.startswith(("http://", "https://")) else f"https://{url}"
    print(f"\n{'=' * 72}\n🔎 {base}")
    homepage, reasons = await fetch_homepage(base)
    if homepage is None:
        print(f"  ❌ 首页抓取失败：{'；'.join(reasons) or '未知'}")
        return
    print(f"  ✅ 首页 [{reasons[0] if reasons else 'httpx'}]  title: {_title(homepage)}")

    base_domain = extract_domain(base)
    inner_urls = find_inner_page_urls(homepage, base, base_domain)
    print(f"  内页选取（≤{_MAX_INNER_PAGES}，联系类优先）: {[u[len(base):] or u for u in inner_urls]}")
    if not any(_INNER_CONTACT_WORDS_RE.search(u) for u in inner_urls):
        for path in _FALLBACK_PATHS:
            probe_url = _resolve_url(base, path)
            if not probe_url or probe_url in inner_urls:
                continue
            async with _make_client() as primary, _make_client(verify=False, trust_env=False) as loose:
                html_probe = await _fetch_site((primary, loose), probe_url)
            if html_probe and ("联系" in html_probe or "contact" in html_probe.lower()):
                inner_urls.insert(0, probe_url)
                del inner_urls[_MAX_INNER_PAGES:]
                print(f"  🔍 常规路径探测命中联系页：{path}")
                break
    if not inner_urls:
        wild = find_wildcard_page_urls(homepage, base, base_domain)
        if wild:
            inner_urls = wild
            print(f"  🧭 无联系/产品内页，取首页同域链接兜底：{wild}")

    pages = [homepage]
    async with _make_client() as primary, _make_client(verify=False, trust_env=False) as loose:
        for u in inner_urls:
            html = await _fetch_site((primary, loose), u)
            if html:
                pages.append(html)
                print(f"    + 内页抓到 {u[len(base):] or u}  title: {_title(html)}")
            else:
                print(f"    - 内页失败 {u}")

    wa_hit, wa_url = detect_whatsapp(pages)
    wa_numbers = detect_whatsapp_numbers(pages)
    jsonld = detect_jsonld_contacts(pages)
    mailto_email = detect_email(pages, mailto_only=True)
    regex_email = detect_email(pages)
    tel_phones = detect_tel_phones(pages)
    text_phones = detect_text_phones(pages)
    persons = detect_contact_persons(pages)

    print("  ── WhatsApp ──")
    print(f"    hit={wa_hit}  wa_url={wa_url}  numbers={wa_numbers}")
    print(f"    wa_business={detect_wa_business(pages)}  groups={detect_whatsapp_groups(pages)}")
    print("  ── 联系方式 ──")
    print(f"    email: mailto={mailto_email}  jsonld={jsonld.get('email')}  regex={regex_email}")
    print(f"    tel链接电话={tel_phones}")
    print(f"    明文电话={text_phones}")
    print(f"    jsonld电话={jsonld.get('telephone')}  jsonld地址={jsonld.get('address')}")
    if persons:
        for p in persons:
            print(f"    具名联系人: {p}")
    else:
        print("    具名联系人: （无）")
    social = detect_social(pages)
    print(f"  ── 社媒（{len(social)}）── {list(social)}")
    scenes = detect_scenes(pages)
    print(f"  ── 场景（{len(scenes)}）── {sorted(scenes)}")
    saas = detect_saas_signals(pages)
    print(f"  ── SaaS信号（{len(saas)}）── {sorted(saas)}")
    overseas = detect_overseas_signals(pages)
    print(f"  ── 出海信号（{len(overseas)}）── {{{', '.join(f'{k}={v}' for k, v in sorted(overseas.items()))}}}")


async def main() -> None:
    urls = sys.argv[1:] or ["https://mugroup.com"]
    for u in urls:
        try:
            await probe(u)
        except Exception as exc:  # noqa: BLE001
            print(f"  ❌ 探针异常：{type(exc).__name__}: {exc}")
    print(f"\n{'=' * 72}\n完成 {len(urls)} 个站点")


if __name__ == "__main__":
    asyncio.run(main())
