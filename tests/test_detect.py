"""两层检测器测试（金标准 fixtures，报告 9.1）。"""
from pathlib import Path

from youzi_bsp.detect import detect

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
