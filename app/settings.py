"""Scrapy 设置 —— 礼貌爬取全靠轮子（报告五·工程节流，Scrapy 内置对应件）。

- robots.txt 遵守：ROBOTSTXT_OBEY（RobotsTxtMiddleware）
- 自动限速：AUTOTHROTTLE（AutoThrottle 扩展）
- 大响应处理：DOWNLOAD_MAXSIZE 2MB——超限整包丢弃（Scrapy 不做截断）
- 重试/退避：RETRY_TIMES + RETRY_HTTP_CODES + RetryAfterMiddleware（HIGH #6：
  Scrapy 内置 RetryMiddleware 不读 Retry-After 头，靠本中间件兜底 sleep）
- 断点续跑：crawl --jobdir data/job1（Scrapy JOBDIR，队列/去重指纹持久化）
- utm 剥离兜底：YouziUrlDupeFilter 自定义 dupefilter 在 fingerprint 前再归一
  一次（CRIT #3：REQUEST_FINGERPRINTER_IMPLEMENTATION='2.7' 在 Scrapy 2.13 已
  移除，是 dead code；规范做法是覆盖 request_fingerprint 方法）
无绕过层：不引入 cloudscraper/stealth，被挑战即弃站（报告 7.3）。
"""
BOT_NAME = "app"
SPIDER_MODULES = ["app"]
NEWSPIDER_MODULE = "app"

USER_AGENT = "youzi-bsp-leadgen/0.1 (BSP prospecting; polite crawler; youzi99013@gmail.com)"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 32
CONCURRENT_REQUESTS_PER_DOMAIN = 1          # 每站串行：礼貌（报告 3.3 每站 ≤5 页）
DOWNLOAD_TIMEOUT = 8
DOWNLOAD_MAXSIZE = 2 * 1024 * 1024          # 2MB 截断（工程节流件 3）
DOWNLOAD_DELAY = 0.5                        # 每站延迟下限（floor）：无此项时
                                            # AutoThrottle 对快站逐响应衰减到 ~70ms
RETRY_TIMES = 2
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]
REDIRECT_MAX_TIMES = 5

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1.0              # MED 修复：0.5s 偏激进；play 12.5% 命中
                                            # 不等于主机不限速，撞 429 后才退避成本高
AUTOTHROTTLE_MAX_DELAY = 10
# 2026-09-28 审查修正：TARGET_CONCURRENCY 必须贴近 CONCURRENT_REQUESTS_PER_DOMAIN
# （Scrapy 文档明示）。旧值 16.0 vs 每域并发 1 的错配使 target_delay=latency/16，
# 快站延迟逐响应坍缩（1.0→0.5→…→~70ms），每站礼貌延迟对快站名义化。
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.5

BSP_DB = "data/leads.db"
ITEM_PIPELINES = {"app.pipelines.WaStorePipeline": 300}
# HIGH #6（2026-09-28 重写）：RetryAfterMiddleware 子类化内置重试器并取代之——
# 内置置 None，我们的类顶替 550 槽位，保证 429/503 先抬 slot delay 再重试
# （旧"优先级 500 先于内置"的注释是错的：process_response 链按优先级降序执行）
DOWNLOADER_MIDDLEWARES = {
    "app.middlewares.PrivateNetMiddleware": 10,
    "scrapy.downloadermiddlewares.retry.RetryMiddleware": None,
    "app.middlewares.RetryAfterMiddleware": 550,
}
# CRIT #3：自定义 fingerprinter——同 URL 不同 utm_* 视为同一请求
REQUEST_FINGERPRINTER_CLASS = "app.dupefilter.YouziUrlFingerprinter"

# HIGH #8：Accept-Language 兜底——海外 CDN（Cloudflare/Akamai）按此路由 edge 页面
# 语言，缺省时按客户端 IP 推断（CN IP 爬 = 中文版 → 市场分层字段错配）。
DEFAULT_REQUEST_HEADERS = {
    "Accept-Language": "*;q=0.1,en;q=0.9,zh;q=0.8,id;q=0.7,pt;q=0.7,ar;q=0.7,es;q=0.7",
}

TELNETCONSOLE_ENABLED = False
LOG_LEVEL = "INFO"  # Scrapy 2.19 正名（LOGLEVEL 旧名不生效，DEBUG 会把 item HTML 打进日志）
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
