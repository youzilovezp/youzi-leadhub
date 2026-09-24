"""Scrapy 设置 —— 礼貌爬取全靠轮子（报告五·工程节流，Scrapy 内置对应件）。

- robots.txt 遵守：ROBOTSTXT_OBEY（RobotsTxtMiddleware）
- 自动限速：AUTOTHROTTLE（AutoThrottle 扩展）
- 大响应处理：DOWNLOAD_MAXSIZE 2MB——超限整包丢弃（Scrapy 不做截断）
- 重试/退避：RETRY_TIMES + RETRY_HTTP_CODES（429 由 RetryMiddleware 处理）
- 断点续跑：crawl --jobdir data/job1（Scrapy JOBDIR，队列/去重指纹持久化）
无绕过层：不引入 cloudscraper/stealth，被挑战即弃站（报告 7.3）。
"""
BOT_NAME = "youzi_bsp"
SPIDER_MODULES = ["youzi_bsp"]
NEWSPIDER_MODULE = "youzi_bsp"

USER_AGENT = "youzi-bsp-leadgen/0.1 (BSP prospecting; polite crawler; youzi99013@gmail.com)"

ROBOTSTXT_OBEY = True
CONCURRENT_REQUESTS = 32
CONCURRENT_REQUESTS_PER_DOMAIN = 1          # 每站串行：礼貌（报告 3.3 每站 ≤5 页）
DOWNLOAD_TIMEOUT = 8
DOWNLOAD_MAXSIZE = 2 * 1024 * 1024          # 2MB 截断（工程节流件 3）
RETRY_TIMES = 2
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]
REDIRECT_MAX_TIMES = 5

AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 0.5
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 16.0

BSP_DB = "data/leads.db"
ITEM_PIPELINES = {"youzi_bsp.pipelines.WaStorePipeline": 300}
DOWNLOADER_MIDDLEWARES = {"youzi_bsp.middlewares.PrivateNetMiddleware": 10}

TELNETCONSOLE_ENABLED = False
LOG_LEVEL = "INFO"  # Scrapy 2.19 正名（LOGLEVEL 旧名不生效，DEBUG 会把 item HTML 打进日志）
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
FEED_EXPORT_ENCODING = "utf-8"
