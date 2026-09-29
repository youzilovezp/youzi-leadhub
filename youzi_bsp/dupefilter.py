"""自定义 RequestFingerprinter —— 同 URL 不同 utm/fbclid/gclid/mc_* 必须去重
（spec 3.4 URL 级归一）。

why：Scrapy 2.19 默认 fingerprinter 只排序 query / 去 fragment / 规范化 percent
case，不剥 utm_/fbclid/gclid。`REQUEST_FINGERPRINTER_IMPLEMENTATION='2.7'` 在
Scrapy 2.13 已移除、是死代码。normalize_url 在 Request 创建前已经剥过一遍，但
外部来源（如 sitemap.xml 解析、第三方插件）创建 Request 时不一定走 normalize_url
——所以在 fingerprinter 层级兜底，确保 fingerprint 永远基于"归一后 URL"。

ponytail: Scrapy ≥2.13 推荐用 REQUEST_FINGERPRINTER_CLASS + RequestFingerprinter
子类（继承 scrapy.utils.request.RequestFingerprinter）——比覆盖 RFPDupeFilter
少一层。20 行够用。

⚠️ Scrapy 内部 _fingerprint_cache: WeakKeyDictionary 按 (include_headers,
keep_fragments, verbatim_url) 缓存 fingerprint——纯改 request._url 不会让缓存失效，
下次调用仍返回旧的带 utm 的 fingerprint。解决：临时设 verbatim_url=True 让缓存
走新 key（verbatim_url=True 时直接用 request.url，不再调 canonicalize_url，
确保我们传入的已归一 URL 被原样使用）。
"""
from __future__ import annotations

from scrapy.utils.request import RequestFingerprinter
from scrapy.utils.request import fingerprint as _scrapy_fp

from youzi_bsp.normalize import normalize_url


class YouziUrlFingerprinter(RequestFingerprinter):
    def fingerprint(self, request):
        normalized = normalize_url(request.url)
        if not normalized or normalized == request.url:
            return _scrapy_fp(request)
        # 关键两步：(1) 临时 _url = 已归一 URL，(2) meta['verbatim_url']=True
        # 让 Scrapy 走新 cache key，避开先前的带 utm fingerprint 缓存
        original_url = request.url
        request._url = normalized
        request.meta["verbatim_url"] = True
        try:
            return _scrapy_fp(request)
        finally:
            request._url = original_url
            request.meta.pop("verbatim_url", None)