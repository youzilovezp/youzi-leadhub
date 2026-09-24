"""youzi-bsp —— WhatsApp BSP 线索获取管道。

依据 research-2026-09/whatsapp-bsp-leadgen-可行性分析.md（v6.1）实施。
技术选型（GitHub 视角、全新评估，不参考本地历史方案）：Scrapy（爬取/礼貌/断点）、
phonenumbers（号码归一）、tldextract（注册域）、pytest；自写仅业务层——
两层检测（指纹取主流挂件厂商公开前端特征）、分层打分、三表 schema。

合规红线（报告第七节）：本工具只做线索发现与准备，不包含任何外联/发送能力。
"""
__version__ = "0.1.0"
