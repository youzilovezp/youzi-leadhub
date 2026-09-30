"""两层检测器测试（金标准 fixtures，报告 9.1）。"""
from pathlib import Path

from app.detect import detect

FIX = Path(__file__).parent / "fixtures"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_link_layer_wa_me():
    res = detect(_html("wa_link.html"))
    assert ("8613800138000", "link") in res["candidates"]
    # 同一链接出现两次 → 去重后仅一条
    assert sum(1 for raw, layer in res["candidates"] if raw == "8613800138000") == 1


def test_link_layer_api_whatsapp_encoded():
    res = detect(_html("wa_api.html"))
    assert ("%2B14155552671", "link") in res["candidates"]


def test_text_layer_international_only():
    res = detect(_html("wa_text.html"))
    assert ("+852 2123 4567", "text") in res["candidates"]
    # 021-5555-8888 本地格式热线不在任何候选里（文本层只收 +/00）
    assert not any("5555" in raw and layer == "text" for raw, layer in res["candidates"])


def test_widget_getbutton_config_is_text_layer():
    # GetButton 内联配置里的号码 → 文本层命中（+ 前缀）
    res = detect(_html("widget_getbutton.html"))
    assert ("+14155552671", "text") in res["candidates"]


def test_widget_elfsight_static_invisible():
    # SaaS widget：静态 HTML 无号码，只记指纹（报告 v6.1 静态可见性分层）
    res = detect(_html("widget_elfsight.html"))
    assert res["candidates"] == []
    assert "elfsight" in res["widgets"]


def test_widget_tokens_case_sensitive():
    """leadhub 遗产教训：驼峰标识符不得误中插件 token（captchaType/joinChat()）。"""
    res = detect('<script>const captchaType="x";function joinChat(){};'
                 'let getButtonType=1;</script>')
    assert res["widgets"] == []                      # 驼峰全部不中
    # 指纹只认小写嵌入代码（class/资产名）；品牌大写展示文本不算指纹
    assert "elfsight" in detect('<div class="elfsight-app"></div>')["widgets"]
    assert detect("powered by Elfsight")["widgets"] == []


def test_web_subdomain_send_link():
    """leadhub 遗产：web.whatsapp.com 分享链接形态（mugroup.com 漏检根因）。
    URL 编码号码（%2B）由 normalize_phone 层解码，detect 只透传原始捕获。"""
    res = detect('<a href="https://web.whatsapp.com/send?phone=%2B6281234567890">wa</a>')
    assert ("%2B6281234567890", "link") in res["candidates"]


def test_group_link_and_business_signals():
    res = detect('<a href="https://chat.whatsapp.com/F3jKd92xQ1mZ">社群</a>'
                 "<p>Message our WhatsApp Business account</p>")
    assert "wa_group" in res["widgets"]              # 私域社群运营证据
    assert "wa_business" in res["widgets"]           # 业务号自述


def test_none():
    res = detect(_html("none.html"))
    assert res["candidates"] == []
    assert res["widgets"] == []


def test_link_layer_wa_me_url_encoded_plus():
    """C2 修复：wa.me/<URL-encoded +号码> 必须命中（浏览器 + 服务器都允许）。"""
    res = detect(_html("wa_url_encoded.html"))
    # 捕获组是数字部分（%2B 已识别）；号码本身被识别即可
    assert ("628123456789", "link") in res["candidates"]


def test_text_layer_spanish_separators():
    """MED 验证：文本层字符类收 ·（西/葡语常见排版点）——`+52 55 4444 1234` 与
    中间排版点都能被识别为完整号码。"""
    res = detect(_html("wa_text_spanish.html"))
    raws = [raw for raw, layer in res["candidates"] if layer == "text"]
    assert any("+52" in r for r in raws)
    assert any("55 4444" in r or "55 4444 1234" in r for r in raws)


def test_email_format_validation():
    """H2 修复：mailto 链接必须经最低格式校验——`bad@`/`no-tld@x`/`x@y` 都不入邮箱集合。
    合法邮件保留、查询串剥除、归一化小写保持原行为。"""
    html = (
        '<a href="mailto:Sales@Example.com?subject=hi">ok</a>'
        '<a href="mailto:bad@">b1</a>'
        '<a href="mailto:no-tld@x">b2</a>'
        '<a href="mailto:foo@bar">b3</a>'
    )
    res = detect(html)
    assert res["emails"] == ["sales@example.com"]


def test_widget_joinchat_static_visible():
    """WordPress joinchat 插件最常用：变量名 `joinchat_settings` 静态可见。
    新 widget 边界 (?<![a-zA-Z])...(?![a-zA-Z]) 允许下划线连接。"""
    html = ('<script>'
            'var joinchat_settings = {tel: "+62812345678", button_text: "Hubungi"};'
            '</script>')
    res = detect(html)
    assert "joinchat" in res["widgets"]
    # 号码在 JS 内联配置中，不在 <a href>，也不带 "whatsapp" 关键词——不会进 candidates。
    # 该检测期望的只是 widget 指纹识别（特征 diff 信号）。


# ============================================================================
# 2026-09-30 修复回归：文本层字符类 + 全角 + 识别
# ============================================================================

def test_text_layer_layout_separators():
    """修复：文本层字符类补 `·⁄　、`（西/葡/印尼/中文排版点）。
    旧 fixture 是骗人的——真正数字用了普通空格；真实西语 `+52·55·4444·1234` 漏检。
    """
    res = detect('<html><body><p>WhatsApp: +52·55·4444·1234</p></body></html>')
    raws = [r for r, l in res["candidates"] if l == "text"]
    assert any("+52" in r for r in raws), f"西语排版点 `·` 应识别: {raws}"
    # 印尼分隔 `⁄`
    res2 = detect('<html><body><p>WhatsApp: +62⁄21⁄1234567</p></body></html>')
    raws2 = [r for r, l in res2["candidates"] if l == "text"]
    assert any("+62" in r for r in raws2), f"印尼排版点 `⁄` 应识别: {raws2}"


def test_text_layer_fullwidth_plus():
    """修复：中文站常把 `+` 渲染成全角 `＋` (U+FF0B)——视为等价。
    """
    res = detect('<html><body><p>WhatsApp: ＋86 138 0013 8000</p></body></html>')
    raws = [r for r, l in res["candidates"] if l == "text"]
    assert any("86" in r and ("+86" in r or "＋86" in r) for r in raws), \
        f"全角 + 应被识别为国际格式: {raws}"
