"""审计测试 — 验证 v7 文档承诺的边界场景，对照 detect/normalize/score/db/middlewares。

每条测试都对应一个具体的怀疑场景（CRIT/HIGH/MED）。只观察不修复。
"""
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request

from app import db as dbm
from app.detect import detect
from app.middlewares import PrivateNetMiddleware, RetryAfterMiddleware
from app.normalize import entity_key, normalize_phone, normalize_url
from app.pipelines import WaStorePipeline
from app.score import page_flags, score_domain
from app.spider import WaSpider


# =========================================================================
# A. detect.py
# =========================================================================

class TestDetectBoundary:
    """detect.py: 边界场景 — 真实网页里能爆雷的所有形态。"""

    def test_wa_me_message_invite_no_phone(self):
        """真实 wa.me 群组/直接消息短链形态（chat invite 无 phone 参数）—— 不应被识别为号码。"""
        html = '<a href="https://wa.me/message/abc123def456">直接聊</a>'
        res = detect(html)
        # 没有 phone 参数就不该进 candidates；可能进入 widget（已经有 _GROUP_RE 处理）
        link_layer = [c for c in res["candidates"] if c[1] == "link"]
        assert all("abc123" not in raw.lower() for raw, _ in link_layer), \
            "wa.me/message/<code> 是邀请短码，不是号码"

    def test_phone_with_non_digit_chars_in_link(self):
        """phone= 后面跟非数字（%2Babc）：当前正则如何处理？"""
        html = '<a href="https://api.whatsapp.com/send?phone=%2Babc">x</a>'
        res = detect(html)
        # 当前 LINK_PATTERNS[1] 的捕获组是 ([^&"'\\s<>#]+)，贪婪匹配所有字符
        # 会捕获 %2Babc 进入 candidates —— 后续 normalize_phone 应该拒绝
        link_layer = [(r, l) for r, l in res["candidates"] if l == "link"]
        # 不管 detect 返回什么，normalize_phone 必须拒绝
        for raw, _ in link_layer:
            ph = normalize_phone(raw, imply_plus=True)
            assert ph is None, f"号码 {raw!r} 应被 normalize_phone 拒绝"

    def test_multi_phone_in_same_href(self):
        """同一 href 含多个 phone 参数：当前只抓第几个？"""
        html = '<a href="https://api.whatsapp.com/send?phone=111&phone=222&phone=333">x</a>'
        res = detect(html)
        link_nums = [r for r, l in res["candidates"] if l == "link"]
        # 当前正则贪婪 + 后续去重逻辑：可能只抓到 "111&phone=222&phone=333"
        # → normalize 阶段应全部拒（libphonenumber 不认带 &phone= 的串）
        for raw in link_nums:
            ph = normalize_phone(raw, imply_plus=True)
            # 关键：不能默默通过
            assert ph is None, f"phone 含残留参数: {raw!r} 不应入 lead"

    def test_phone_with_extension(self):
        r"""号码含 ext/ext.：应只识别主号，分机号不进 lead。

        实测：regex r"\+?\d[\d\s\-().–—]{7,24}" 贪婪匹配会吞下 "ext" 后的字母（不在字符类内）
        因此以 "+852 2123 4567 ext 123" 形态会被截到 ext 前——主号是 +85221234567。
        验证：主号通过 normalize，分机号 "123" 不应被并入。
        """
        for raw_in in ("+852 2123 4567 ext 123", "+85221234567ext123"):
            html = f'<p>WhatsApp {raw_in}</p>'
            res = detect(html)
            text_layer = [r for r, l in res["candidates"] if l == "text"]
            if text_layer:
                ph = normalize_phone(text_layer[0])
                if ph:
                    # 主号应该是 +85221234567，ext "123" 不应被并入主体
                    assert ph[0] == "+85221234567", \
                        f"分机号 {raw_in!r} 误并入主号: got {ph[0]}"

    def test_phone_in_html_comment_ignored(self):
        """HTML 注释里的号码 —— 不应入 lead（注释不渲染，疑似泄漏信息）。"""
        html = '<!-- sales contact +86 138 0013 8000 --><p>欢迎</p>'
        res = detect(html)
        text_layer = [r for r, l in res["candidates"] if l == "text"]
        assert not any("+86" in r or "8613800138000" in r for r in text_layer), \
            f"HTML 注释里的号码不应被识别: {text_layer}"

    def test_phone_in_js_string_literal(self):
        """JS 字符串字面量里的号码 + 旁边有 'whatsapp' 关键词：当前会抓吗？"""
        html = ('<script>var config = {whatsapp_support: "+86 138 0013 8000"};</script>')
        res = detect(html)
        # 文本层只看纯文本；但 parsel 解析会把 <script> 内容也算 HTML 文本
        text_layer = [r for r, l in res["candidates"] if l == "text"]
        # 当前测试场景：脚本里的号码 + 旁边有 whatsapp 关键词 → 文本层会抓
        # 这是已知边界（spec 9.2 误报风险点 ≤5%）
        # 关键确认：是否能进入 link 层（不能）
        link_layer = [r for r, l in res["candidates"] if l == "link"]
        assert not any("8613800138000" in r or "+86" in r for r in link_layer), \
            "JS 字符串里的号码不应进 link 层"

    def test_html_entity_encoded_plus(self):
        """HTML 实体编码 &#43;86 138：当前能识别吗？"""
        html = '<p>WhatsApp: &#43;86 138 0013 8000</p>'
        res = detect(html)
        # Selector 默认会解码 HTML 实体 → 文本层看到的是 +86 138...
        text_layer = [r for r, l in res["candidates"] if l == "text"]
        assert any("+86" in r or "86 138" in r for r in text_layer), \
            f"HTML 实体编码的 + 应被识别: {text_layer}"

    def test_zero_width_chars_before_phone(self):
        """号码前有零宽字符（U+200B）—— 是否被忽略？"""
        # + 与数字之间插零宽
        html = '<p>WhatsApp: +\u200b86 138 0013 8000</p>'
        res = detect(html)
        text_layer = [r for r, l in res["candidates"] if l == "text"]
        # 零宽字符是否会被当前正则捕获？
        # _TEXT_PHONE = r"\+?\d[\d\s\-().–—]{7,24}" 不含 \u200B 类
        # 文本层看的是原始 HTML 文本（parsel text()），零宽保留
        # 真实场景：可能被抓也可能不抓，看是否影响 lead 正确性
        if text_layer:
            for raw in text_layer:
                ph = normalize_phone(raw)
                # 若带零宽进 E164 会有问题
                assert ph is None or "\u200b" not in ph[0]

    def test_data_scheme_href_with_wa(self):
        """href 含 data: 协议：当前 I5 测 ftp，但 data: 呢？"""
        # 注：link 提取走 parsel::attr(href) 对 a 标签 —— 看是否限制 scheme
        html = '<a href="data:text/html,<a href=\'wa.me/8613800138000\'>x</a>">x</a>'
        res = detect(html)
        # 当前正则只匹配 href 字符串，data: scheme 也会被检测到
        link_layer = [r for r, l in res["candidates"] if l == "link"]
        # 这是 data: URI 嵌入的 HTML —— 检测到号码字符串但不是真链接
        # 是否被识别？是否会被 normalize 后真入库？
        # 这取决于检测是否对 scheme 做过滤
        # 注：normalize_url 会拒 data: scheme → 所以即使进了 candidates，pipeline 里
        # entity_key("") 会写空键 → 已有 test_malformed_url_skipped 防护
        # 关键：detect 自身是否过滤（应该记录为可疑行为）

    def test_widget_case_variants(self):
        """widget 指纹对驼峰 / 全大写 / 含数字后缀：覆盖已测过 camelCase，看 JOINCHAT / joinChat2"""
        # JOINCHAT（全大写）：当前 (?<![a-zA-Z])...(?![a-zA-Z]) 大写不命中
        res = detect('<script>var JOINCHAT = {tel: "+62812345678"};</script>')
        # 已测过：case sensitive（test_widget_tokens_case_sensitive）
        # 但 JOINCHAT 是另一种 case 形态 → 验证大小写敏感的稳定性
        # 这里不强制断言（已是已知设计），只记录

        # joinChat2 / getButton3 含数字后缀
        res2 = detect('<div class="joinChat2"></div>')
        # 边界 (?<![a-zA-Z]) 接数字是 OK 的，但 (?![a-zA-Z]) 不允许字母
        # 当前实现应该不命中（大小写敏感）
        assert "joinchat" not in [w.lower() for w in res2["widgets"]], \
            "驼峰 joinChat2 不应被识别为 joinchat"

    def test_short_chat_invite_code(self):
        """群组邀请码长度：chat.whatsapp.com/ABCD 短码（5 字符）会漏吗？"""
        # _GROUP_RE = r"chat\.whatsapp\.com/[A-Za-z0-9_-]{5,}"
        # 5 字符是下界，ABCD = 4 字符 → 应漏
        res_short = detect('<a href="https://chat.whatsapp.com/ABCD">short</a>')
        res_long = detect('<a href="https://chat.whatsapp.com/ABCDEF">long</a>')
        # 长码应识别为 wa_group
        assert "wa_group" in res_long["widgets"], \
            "5+ 字符群组邀请码应被识别"
        # 短码（4 字符）会被漏 —— 这是设计选择还是 bug？

    def test_three_schemes_simultaneously(self):
        """三 scheme 同时出现（wa.me + api.whatsapp.com + whatsapp://）：分别抓哪个？"""
        html = ('<a href="https://wa.me/8613800138000">a</a>'
                '<a href="https://api.whatsapp.com/send?phone=8613800138001">b</a>'
                '<a href="whatsapp://send?phone=8613800138002">c</a>')
        res = detect(html)
        # 三个不同 scheme 应全部被识别
        link_nums = sorted(r for r, l in res["candidates"] if l == "link")
        assert len(link_nums) == 3, f"三 scheme 都应识别: {link_nums}"

    def test_mailto_with_url_encoded_email(self):
        """mailto 含 URL 编码：%40 解码"""
        html = '<a href="mailto:sales%40example.com?subject=hi">m</a>'
        res = detect(html)
        assert "sales@example.com" in res["emails"], \
            f"mailto URL 编码 %40 应解码: {res['emails']}"

    def test_mailto_with_plus_addressing(self):
        """邮箱含 + 别名（sales+tag@example.com）"""
        html = '<a href="mailto:sales+lead@example.com">m</a>'
        res = detect(html)
        assert any("sales+lead" in e for e in res["emails"]), \
            f"+ 别名应保留: {res['emails']}"


# =========================================================================
# B. normalize.py
# =========================================================================

class TestNormalizeBoundary:

    def test_double_slash_in_path(self):
        """URL 路径含 // —— I10 已测。看更多形态。"""
        # query 里的 // 应保留
        u = normalize_url("https://a.com//double//path?q=//v")
        assert u is not None
        # host 后的 // 是否被压成 /
        # urlsplit 不会动 path，normalize 不特殊处理
        # 实际行为：返回原始 //（normalize_url 没显式去重）
        # 这意味着同一资源可能多个 fingerprint

    def test_path_with_spaces(self):
        """URL 路径含空格 —— 应 urlencode 还是保留？"""
        u = normalize_url("https://a.com/path with space")
        # urlsplit 不编码 → 原样保留
        # 但 Chromium 等会 urlencode 为 %20
        # normalize_url 没做这一步
        assert u == "https://a.com/path with space", \
            f"路径含空格未被编码: {u}"

    def test_uppercase_host_in_entity_key(self):
        """entity_key 顶级域大写 —— 应小写归一"""
        k = entity_key("WWW.EXAMPLE.COM")
        assert k == "example.com", f"大写 host 应归一: {k}"

    def test_entity_key_with_port(self):
        """entity_key 含端口 —— 行为？"""
        k = entity_key("example.com:8080")
        # urlsplit 后 host 是 "example.com"，端口被剥
        # 但 entity_key 直接处理 string，没走 urlsplit
        # tldextract("example.com:8080") 可能直接返回 example.com（含端口被解析掉）
        # 实际：tldextract 会接受端口作 suffix 的一部分或忽略
        # 关键：不应 crash，应返回合理值
        assert k is not None

    def test_idn_domain(self):
        """IDN 国际化域名 —— 实际行为"""
        # 例え.テスト → xn--r8jz45g.xn--zckzah
        k = entity_key("例え.テスト")
        # tldextract 应该 punycode 转换
        # 关键：不应抛异常
        assert k is not None
        # IDN 输入应被处理（要么保留要么 punycode）
        # 实际行为待测试输出

    def test_phone_with_plus_zero_zero(self):
        """手机号码前置 +00 —— 行为？"""
        # +00 86 138 → 等价于 +86 138？
        ph = normalize_phone("+00 86 138 0013 8000")
        # libphonenumber 可能视为 +86（去前导 00）也可能视为无效
        # 关键：行为确定且可预测

    def test_phone_with_semicolon(self):
        """号码含分号（如多号共用） —— 行为？"""
        ph = normalize_phone("+86 138; 10086")
        # _SEPARATORS 不含 ; → 进入 phonenumbers 解析可能异常

    def test_phone_with_backtick(self):
        """号码含反引号 —— 行为？"""
        ph = normalize_phone("+86`138`0013`8000")
        # 反引号不会被分隔符 sub 替换

    def test_phone_852_886_imply_plus(self):
        """港台号码 852/886 在 link 层（imply_plus=True）加 +，文本层不加。

        修正：原测试用 "85212345678" 不是有效 HK 号（11 位超长），libphonenumber
        正确拒之。改用有效 HK 8 位号码 "85221234567"（已 libphonenumber 验证）。
        """
        # 文本层（imply_plus=False）：85221234567 不以 +/00 开头 → 被拒
        ph_text = normalize_phone("85221234567")
        assert ph_text is None, f"文本层应拒无 + 前缀: {ph_text}"
        # 链接层（imply_plus=True）：会加 + → 应该解析为 +852 HK
        ph_link = normalize_phone("85221234567", imply_plus=True)
        assert ph_link is not None, "link 层应接受 8 位 HK 号码"
        assert ph_link[1] == "HK", f"852 应识别为 HK: {ph_link}"
        # 同时验证台湾 +886
        ph_tw = normalize_phone("88621234567", imply_plus=True)
        if ph_tw is not None:  # 取决于 libphonenumber
            assert ph_tw[1] == "TW", f"886 应识别为 TW: {ph_tw}"


# =========================================================================
# C. score.py
# =========================================================================

class TestScoreBoundary:

    def test_empty_signals_returns_zero(self):
        """空 flags + 空 phones + 空 entity → 0 分不报错"""
        res = score_domain({"lang": None, "icp": False, "hreflang_zh": False,
                            "title_zh": False, "gambling": False},
                           [], "x.com", "")
        assert res["p0"] == 0
        assert res["score"] == 0
        assert res["market"] is None
        assert res["market_group"] is None

    def test_entity_dot_cn_with_en_lang_us_market(self):
        """.cn TLD + lang=en + market=US → entity 强信号保证 P0"""
        res = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                            "title_zh": False, "gambling": False},
                           ["US"], "vstarcam.cn", "")
        # .cn → P0
        assert res["p0"] == 1, ".cn 强信号应保证 P0"

    def test_icp_beian_loose_match_under_review(self):
        """ICP 备案号模糊匹配：'备案中' 是否误判？"""
        # 当前正则：[京沪粤浙苏皖闽赣鲁豫鄂湘桂琼渝川黔滇陕甘青宁新津冀晋蒙辽吉黑]
        # ICP[备证]?\s?第?\d+号?|ICP备\d+号
        # "备案中" 不匹配（缺 ICP 前缀/数字）
        html = '<footer>本站备案中</footer>'
        assert page_flags(html)["icp"] is False, "'备案中' 不应被识别为 ICP"
        # 但 "京ICP备123号" 应识别
        html2 = '<footer>京ICP备123456号</footer>'
        assert page_flags(html2)["icp"] is True

    def test_gambling_word_booking_not_matched(self):
        """'booking' 含 'book' —— 但赌博关键词是 casino/betting 等，不应误判"""
        flags = page_flags("<html lang='en'><title>booking site</title>welcome</html>")
        assert flags["gambling"] is False, "booking 不应被识别为赌博"

    def test_market_group_other_for_hk_mo_tw(self):
        """HK/MO/TW/CN 现在归入独立 CN 组（不是 OTHER）—— P0 销售分组清晰化

        S1 修复：score._market_group() 显式把 CN/HK/MO/TW 归到 "CN" 组，避免都落 OTHER。
        之前测试断言落 OTHER（因为 _MARKET_GROUP 不含这些）—— 已升级到新行为。
        """
        for c in ("HK", "MO", "TW", "CN"):
            res = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                                "title_zh": False, "gambling": False},
                               [c], "x.com")
            assert res["market_group"] == "CN", \
                f"{c} 应归 CN 市场组（不是 OTHER）：got {res['market_group']}"

    def test_zh_weak_double_track(self):
        """zh_weak 双轨：CN + hreflang_zh 的多语种网站（GitHub 类）—— 实战如何"""
        # GitHub：lang=en + hreflang=zh + market=US → 不应是 P0
        res = score_domain({"lang": "en", "icp": False, "hreflang_zh": True,
                            "title_zh": False, "gambling": False},
                           ["US"], "github.com")
        assert res["p0"] == 0, "GitHub 类不应误判 P0"
        # 但 zh_weak 加分
        assert res["score"] == 3 + 2, "应有 zh_weak 加分 +2"

    def test_wa_group_score_addition(self):
        """wa_group + wa_business 加 +2，但分母是？"""
        # 当前：score += 2 if wa_group
        # 没有分母（不是概率），是累加 → 多个信号累加
        flags = {"lang": "en", "icp": False, "hreflang_zh": False,
                 "title_zh": False, "gambling": False,
                 "wa_group": True, "wa_business": True}
        res = score_domain(flags, ["US"], "x.com")
        # base 3 (countries) + 2 (wa_group) + 2 (wa_business) = 7
        assert res["score"] == 7

    def test_same_lead_multiple_sightings_market_mode(self):
        """同一 lead 多 sighting 累加 market —— market 取众数"""
        # 3 sighting: ID, ID, BR → market=ID (2 次)
        countries = ["ID", "ID", "BR"]
        res = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                            "title_zh": False, "gambling": False},
                           countries, "x.com")
        assert res["market"] == "ID"
        # 平票时排序后取最大（max(sorted(...), key=count)）→ 平票时取字典序最大
        countries_tie = ["BR", "ID"]
        res2 = score_domain({"lang": "en", "icp": False, "hreflang_zh": False,
                             "title_zh": False, "gambling": False},
                            countries_tie, "x.com")
        # 平票 → sorted 后 max → "ID"
        assert res2["market"] in ("ID", "BR")


# =========================================================================
# D. pipelines.py / db.py / middlewares.py
# =========================================================================

class TestPipelineBoundary:

    def test_pipeline_item_with_empty_html_processes(self):
        """html="" 的 item 会被 process_item 处理吗？"""
        # spec 没明示 —— 测一下
        with tempfile.TemporaryDirectory() as td:
            dbp = str(Path(td) / "t.db")
            pl = WaStorePipeline(db_path=dbp)
            pl.open_spider(None)
            pl.process_item({"url": "https://x.com/", "html": "",
                            "channel": "sample", "seed_host": "x.com"}, None)
            pl.close_spider(None)
            conn = dbm.connect(dbp)
            assert conn.execute("SELECT COUNT(*) FROM domain").fetchone()[0] == 1

    def test_pipeline_atom_merge_concurrent(self):
        """两个进程并发调用 _atomic_merge_csv 会丢吗？
        （spec 说 SQLite 单写者 WAL 下安全，但并发跑两次同表会怎样？）"""
        # 单进程串行不会丢（已测 test_widget_email_atomic_merge）
        # 真正并发需要 multiprocessing —— 这里仅记录假设

    def test_pipeline_rollback_on_exception(self):
        """process_item 中途异常 → 之前写入的 entity 是否会留半成品？"""
        # detect() 在 item["html"] 是 None 时会抛 TypeError
        with tempfile.TemporaryDirectory() as td:
            dbp = str(Path(td) / "t.db")
            pl = WaStorePipeline(db_path=dbp)
            pl.open_spider(None)
            try:
                pl.process_item({"url": "https://x.com/", "html": None,
                                "channel": "sample", "seed_host": "x.com"}, None)
            except Exception:
                pass
            # 即使 process_item 失败，upsert_domain 已写入 → 是否回滚？
            conn = dbm.connect(dbp)
            n = conn.execute("SELECT COUNT(*) FROM domain").fetchone()[0]
            # 当前实现：每 item 后 commit → 部分写入会留下
            # 这是当前设计选择：单 item 失败不污染后续 item
            # 接受 —— 无需修复

    def test_pipeline_widget_value_too_long(self):
        """email 字段防 bloat：超 4KB 的值应被截断。

        E1 修复：_atomic_merge_csv 单个 token 限长 4KB。2026-09-28 修正：widget 指纹
        是固定 token 名（joinchat/tidio…），不可能超 4KB——旧用例拿 10KB 假 widget 名
        根本不匹配 WIDGET_RE，截断逻辑从未执行（空测试）。email 本地部是唯一真实
        可达的超长路径（mailto 正则不限制本地部长度）。
        """
        with tempfile.TemporaryDirectory() as td:
            dbp = str(Path(td) / "t.db")
            pl = WaStorePipeline(db_path=dbp)
            pl.open_spider(None)
            # 注入超长 email 本地部（>4KB，mailto 最低校验只查 @ 与 TLD 形式）
            long_local = "a" * 10_000
            html = f'<a href="mailto:{long_local}@x.com">m</a>'
            pl.process_item({"url": "https://x.com/", "html": html,
                             "channel": "sample", "seed_host": "x.com"}, None)
            pl.close_spider(None)
            conn = dbm.connect(dbp)
            email_val = conn.execute("SELECT email FROM domain").fetchone()["email"] or ""
            # 单 token 被截到 4KB
            assert 0 < len(email_val) <= 4096, \
                f"email 单 token 应 ≤ 4KB: got {len(email_val)}"


class TestDbBoundary:

    def test_db_no_index_for_query_columns(self):
        """market_group, score, p0, last_crawled 没索引 → 大表查询会慢"""
        # 仅记录：当前 schema 只索引了 channel
        with tempfile.TemporaryDirectory() as td:
            conn = dbm.connect(Path(td) / "t.db")
            # 大表查询时无索引
            idx = [r["name"] for r in conn.execute("PRAGMA index_list(domain)").fetchall()]
            # 缺：market_group, score, p0, last_crawled, status
            assert "idx_market_group" not in idx

    def test_db_sighting_pk_includes_url_same_lead_different_urls(self):
        """sighting PK = (entity_key, e164, url) —— 同一 lead 多页挂同号 → 多 sighting"""
        # 这是 spec 设计：合规留痕。但 CSV 导出 GROUP_CONCAT(DISTINCT e164)
        # 重复号码不会重复入库（sighting URL 不同但 GROUP 时合并）
        with tempfile.TemporaryDirectory() as td:
            conn = dbm.connect(Path(td) / "t.db")
            dbm.upsert_domain(conn, "a.com", "x", None)
            dbm.upsert_phone(conn, "+8613800138000", "CN")
            dbm.upsert_sighting(conn, "a.com", "+8613800138000", "https://a.com/", "link")
            dbm.upsert_sighting(conn, "a.com", "+8613800138000", "https://a.com/contact", "link")
            n = conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0]
            assert n == 2, "不同 URL 应留痕为不同 sighting（合规留痕）"

    def test_db_no_cascade_no_foreign_keys_on_delete(self):
        """sightinig FK 没声明 ON DELETE CASCADE —— 删 domain 时是否会留 orphan？"""
        # 当前 schema: REFERENCES domain(entity_key) 无 ON DELETE CASCADE
        # 但 forget() 显式 DELETE FROM sighting WHERE entity_key=?
        with tempfile.TemporaryDirectory() as td:
            conn = dbm.connect(Path(td) / "t.db")
            dbm.upsert_domain(conn, "a.com", "x", None)
            dbm.upsert_phone(conn, "+8613800138000", "CN")
            dbm.upsert_sighting(conn, "a.com", "+8613800138000", "https://a.com/", "link")
            # 直接删 domain（绕过 forget） → FK 约束会拦
            try:
                conn.execute("DELETE FROM domain WHERE entity_key='a.com'")
                conn.commit()
                # 若 PRAGMA foreign_keys=ON，会拦
                # 当前 db.connect() 开启了 foreign_keys=ON
                n_sighting = conn.execute("SELECT COUNT(*) FROM sighting").fetchone()[0]
                # 没忘删 → 1
                # 但 FK 应该阻止 delete 成功
                assert False, "FK 应阻止裸删 domain"
            except sqlite3.IntegrityError:
                pass  # 符合预期

    def test_db_wal_mode_check_same_thread(self):
        """WAL 模式 + check_same_thread=True → 多线程并发写会报 ProgrammingError"""
        # db.connect 默认 check_same_thread=True
        # 若 api.py 多线程请求同一连接 → 报错
        with tempfile.TemporaryDirectory() as td:
            conn = dbm.connect(Path(td) / "t.db")
            # 验证 WAL 已开
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"


class TestSpiderBoundary:

    def test_spider_start_requests_compat_path(self):
        """Scrapy 2.13+ 只调 start()，start_requests 是否真在跑？"""
        # 当前两者实现相同 —— 兼容性 OK，但有重复代码
        sp = WaSpider(channel="play")
        sp.start_urls = ["https://a.com/", "https://b.com/"]
        # 调用两者都得 yield
        import asyncio
        async def _go():
            s_reqs = [r async for r in sp.start()]
            return s_reqs
        s_reqs = asyncio.run(_go())
        sr_reqs = list(sp.start_requests())
        assert len(s_reqs) == len(sr_reqs) == 2

    def test_spider_redirect_seed_host_not_updated(self):
        """_request 拼的 meta seed_host —— 重定向后 seed_host 不更新，可能错位"""
        sp = WaSpider(max_pages=3, channel="play")
        # _dev_names["https://original.example.com/"] = "深圳市某某科技"
        sp._dev_names["https://original.example.com/"] = "深圳A科技"
        # 重定向到 https://redirected.com/
        req = sp._request("https://redirected.com/", "original.example.com")
        # 当前实现：seed_host 是原始的，但 Request URL 是重定向后的
        # 重定向后 Scrapy 不改 meta.seed_host（除非自己实现 redirect middleware）
        # 实际行为：pipeline 拿到的 item seed_host 是 original（错位）
        assert req.meta["seed_host"] == "original.example.com"
        # 这意味着 data lineage 失真

    def test_spider_contact_le_about_us_overmatch(self):
        """contact_le LinkExtractor 的 about-us 含连字符 —— 是否会过匹配"""
        # LinkExtractor 内部用正则匹配 URL —— 当前 CONTACT_WORDS 含 "about-us|aboutus"
        # "about-us" 在很多博客页面作为分类名（不是联系页）
        # LinkExtractor(allow=(CONTACT_WORDS,)) 默认只 match 完整 URL，词边界？
        # 注：LinkExtractor.allow 是 regex，不是词边界 → "about-us" 会匹配 /about-us-xxx
        sp = WaSpider(max_pages=20, channel="play")
        from scrapy.http import HtmlResponse
        html = ('<html><body>'
                '<a href="https://x.com/about-us">a</a>'
                '<a href="https://x.com/about-us-team">b</a>'
                '<a href="https://x.com/about">c</a>'
                '</body></html>')
        resp = HtmlResponse(url="https://x.com/", body=html.encode())
        resp.request = Request("https://x.com/")
        out = list(sp.parse(resp))
        reqs = [o for o in out if isinstance(o, Request)]
        urls = [r.url for r in reqs]
        # 当前实现：LinkExtractor 会把 /about-us-team 也算联系页（设计选择）

    def test_spider_html_size_unbounded_in_memory(self):
        """DOWNLOAD_MAXSIZE=2MB 截断由 Scrapy 实施 —— 但中间件 / detect 完整解析？"""
        # 当前 detect() 接收整段 HTML（response.text）→ 内存里塞 2MB
        # 风险：爬 5 万站并发 32 → 160MB HTML 同时在内存
        # （Scrapy 用 Twisted reactor 单线程，但 item pipeline 可能在不同线程）
        # 这是设计选择（不修）

    def test_spider_throttle_target_concurrency(self):
        """AUTOTHROTTLE_TARGET_CONCURRENCY=16 —— IP 429 风险？"""
        # 当前 settings.py: 16.0 + CONCURRENT_REQUESTS_PER_DOMAIN=1
        # → 全局 16 并发，每域 1 个（串行）
        # 16 IP × per-domain 1 是 16 个不同 host 并发 → 总 QPS 上限 = 16 / delay
        # 实际：delay 由 autothrottle 调，命中 429 后退避
        # 风险：play 渠道 6038 站 × 5 页 = 30190 请求，AUTOTHROTTLE_START_DELAY=1s
        # → 理论耗时 = 30190 / 16 ≈ 1900s ≈ 32min（实测可能更长）


class TestMiddlewareBoundary:

    def test_middleware_octal_ip_strict_format(self):
        """0177.0.0.1 octal —— 当前 _allowed 用 socket.inet_aton 解析，
        macOS 会规范化成 177.0.0.1 → 不会判为私网（实测代码注释里说 macOS 偏错）"""
        # 但实际上：inet_aton("0177.0.0.1") 在 macOS/Linux 上行为可能不同
        # 测试跨平台
        with pytest.raises(IgnoreRequest):
            PrivateNetMiddleware().process_request(
                Request("http://0177.0.0.1/"), None)

    def test_middleware_dns_rebinding_toc_toe(self):
        """DNS rebinding：第一次解析为公网 IP，第二次解析为私网 IP"""
        # 当前代码注释："DNS rebinding TOCTOU 若要封死需接管 DNS 解析层，暂留此上限"
        # 真实场景：rebinding.example.com 第一次解析 1.2.3.4（公网），第二次解析 127.0.0.1
        # 当前实现：缓存第一次结果 → 放行 → 第二次 Scrapy 解析到私网
        # 这是已知设计上限
        # 验证：调用 _allowed 两次不同结果时，按缓存返回
        mw = PrivateNetMiddleware()
        # 直接注入 cache 模拟
        mw._verdict["x.com"] = True
        assert mw._allowed("x.com") is True
        # 即使此时真实解析已变，也不会重新校验

    def test_middleware_retry_after_max_delay_capped(self):
        """RetryAfterMiddleware max_delay=60 生效——Retry-After:9999 抬 slot.delay 截到 60。

        2026-09-28 重写对齐：中间件已改为 RetryMiddleware 子类（抬下载槽 delay），
        不再注入已死的 download_delay meta。
        """
        from types import SimpleNamespace

        from scrapy.http import Response as _R
        from scrapy.settings import Settings as _S
        from scrapy.statscollectors import StatsCollector as _SC

        crawler = SimpleNamespace(settings=_S(), stats=None, spider=None,
                                  engine=SimpleNamespace(downloader=SimpleNamespace(
                                      slots={"x.com": SimpleNamespace(delay=0.0)})))
        crawler.stats = _SC(crawler)
        crawler.spider = SimpleNamespace(crawler=crawler)
        mw = RetryAfterMiddleware.from_crawler(crawler)
        req = Request("http://x.com/", meta={"download_slot": "x.com"})
        res = _R(url="http://x.com/", status=503,
                 headers={"Retry-After": "9999"}, body=b"")
        mw.process_response(req, res, None)
        slot = crawler.engine.downloader.slots["x.com"]
        assert slot.delay == 60.0, f"Retry-After:9999 应 cap 到 60: got {slot.delay}"

    def test_middleware_ipv4_mapped_ipv6_in_url_bracket_stripped(self):
        """urlsplit('[::ffff:127.0.0.1]') → hostname='::ffff:127.0.0.1' → 内部再解析"""
        # 测试当前代码：host 提取时 strip("[]") 是否正确
        with pytest.raises(IgnoreRequest):
            PrivateNetMiddleware().process_request(
                Request("http://[::ffff:127.0.0.1]/"), None)


# =========================================================================
# E. crawl_api / cli / seeds
# =========================================================================

class TestCrawlApiBoundary:

    def test_crawl_api_jobs_dict_grows_unbounded(self):
        """_JOBS_RUNNING 内存 dict 永不清理 —— 长期跑批会内存泄漏？"""
        # 注：当前实现：job 完成后不会从 dict 移除（status='exited' 后保留）
        # 这是已知设计：便于历史查询
        # 但 spawn 1000 次 → dict 1000 项 → 内存持续增长
        from app import crawl_api
        before = len(crawl_api._JOBS_RUNNING)
        crawl_api._JOBS_RUNNING["test-fake"] = "fake"
        after = len(crawl_api._JOBS_RUNNING)
        assert after == before + 1
        # 不清理 → 内存泄漏（设计选择）
        # 清理一下避免污染
        crawl_api._JOBS_RUNNING.pop("test-fake", None)

    def test_crawl_api_status_endpoint_exposes_log(self):
        """/api/crawl/status 暴露日志末尾 30 行 —— 是否含 PII/内部栈？"""
        # 当前 _tail 返回原始日志内容
        # 若日志含 stack trace 或用户提交的电话/邮箱 → PII 泄漏
        # 仅记录：需脱敏或截断（设计选择）


class TestCliBoundary:

    def test_cli_import_osm_command_exists(self):
        """cli import-osm 子命令是否真连了 osm_direct？"""
        # 看 cli.py: from app.osm_direct import import_osm
        # 若 osm_direct.py 有 bug → cli 也炸
        from app.cli import main
        # 仅检查 import 不报错
        import app.cli
        import app.osm_direct
        assert callable(app.osm_direct.import_osm)


class TestSeedsBoundary:

    def test_seeds_tranco_no_retry_on_failure(self):
        """seeds.tranco 无重试 —— 网络抖动就死"""
        # 当前：httpx.get(...).raise_for_status() → 网络错就抛
        # 测试：仅检查函数 import + 调用签名（不实际跑网络）
        from app.seeds import tranco
        assert callable(tranco)

    def test_seeds_play_no_timeout_on_subprocess(self):
        """seeds.play 用 subprocess.run 无 timeout —— node 卡死会挂住 CLI"""
        # 当前：subprocess.run(..., check=True) 无 timeout
        # 若 play-seeds.mjs 卡在网络 → 永远等
        # 仅记录

    def test_seeds_osm_id_bbox_coverage(self):
        """印尼 ID 4 个 bbox 是否覆盖全印尼"""
        # 看 seeds.py _ID_BBOXES
        from app.seeds import _ID_BBOXES
        # 经度范围 95-141，纬度范围 -11 到 6
        # 印尼东部（如巴布亚）经度 ~141 也在范围内
        # 验证：4 个 bbox 是否无重叠且覆盖印尼群岛
        bbox_list = _ID_BBOXES
        # 简单校验：经度跨度
        min_lon = min(b[1] for b in bbox_list)
        max_lon = max(b[3] for b in bbox_list)
        # 印尼跨 95°E 到 141°E → 46 度
        assert min_lon <= 96 and max_lon >= 140, \
            f"bbox 经度未覆盖全印尼: {min_lon}-{max_lon}"


# =========================================================================
# 辅助函数
# =========================================================================
