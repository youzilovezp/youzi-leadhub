"""场景 / SaaS 需求信号关键词检测。"""

from app.collectors.scenes import detect_saas_signals, detect_scenes, page_text

_HOME = """
<html><head><style>.btn{color:red}</style>
<script>var config = {chat: true};</script></head>
<body>
  <h1>Global Gadgets</h1>
  <a href="https://wa.me/60123456789">Chat on WhatsApp</a>
  <div>Contact us for customer service &amp; order support</div>
  <div>Big promotion! 50% discount coupon inside</div>
  <div>Order tracking / shipping worldwide</div>
</body></html>
"""


def test_detect_scenes_en():
    scenes = detect_scenes([_HOME])
    assert "customer_service" in scenes  # contact us / customer service / order support
    assert "marketing" in scenes  # promotion / discount / coupon
    assert "transactional" in scenes  # order tracking / shipping


def test_detect_scenes_zh():
    html = "<html><body>在线客服 7x24 售后服务 | 限时促销 领取优惠券 | 支持订单查询与物流跟踪</body></html>"
    scenes = detect_scenes([html])
    assert "customer_service" in scenes
    assert "marketing" in scenes
    assert "transactional" in scenes


def test_keyword_word_boundary_no_false_positive():
    """ASCII 关键词整词命中：bot 不得打进 about，sale 不得打进 wholesale。"""
    html = "<html><body>About our wholesale story</body></html>"
    assert detect_scenes([html]) == []  # about ≠ bot；wholesale ≠ sale
    assert detect_saas_signals([html]) == {}  # story 无信号


def test_script_style_content_ignored():
    html = """<html><head><script>var s = "customer service";</script>
    <style>/* promotion */</style></head><body>plain</body></html>"""
    assert detect_scenes([html]) == []


def test_detect_saas_signals_counts():
    html = """
    <html><body>
      Powered by Zendesk helpdesk &amp; HubSpot CRM.
      Try our chatbot for AI customer service (GPT inside).
      Marketing automation via Mailchimp; omnichannel inbox.
    </body></html>
    """
    hits = detect_saas_signals([html])
    assert hits.get("crm") == 2  # crm + hubspot
    assert hits.get("helpdesk") == 2  # helpdesk + zendesk
    assert hits.get("chatbot") == 1
    assert hits.get("ai_service") == 2  # ai customer service + gpt
    assert hits.get("marketing_automation") == 2  # marketing automation + mailchimp
    assert hits.get("omnichannel") == 1


def test_saas_signals_zh():
    html = "<html><body>我们使用智能客服与工单系统，并接入全渠道收件箱</body></html>"
    hits = detect_saas_signals([html])
    assert hits.get("ai_service") == 1  # 智能客服
    assert hits.get("helpdesk") == 1  # 工单（工单系统含工单）
    assert hits.get("omnichannel") == 1


def test_page_text_empty_inputs():
    assert page_text(None) == ""
    assert page_text([]) == ""
    assert detect_scenes(None) == []
    assert detect_saas_signals([]) == {}


def test_detect_brand_stack_in_raw_html():
    """品牌 widget 嵌在 script 标签里（正文剥掉后无痕），必须在 raw HTML 命中。"""
    html = """<html><body>Our shop
    <script src="https://widget.intercom.io/widget/abc123"></script>
    <script src="https://static.zdassets.com/ekr/snippet.js"></script>
    </body></html>"""
    hits = detect_saas_signals([html])
    assert "brand_stack" in hits


def test_brand_stack_not_matched_by_plain_text_brand_words():
    """正文里没有品牌词、raw 里也没有 → 不命中（空页面不误报）。"""
    assert "brand_stack" not in detect_saas_signals(["<p>we sell shoes</p>"])
