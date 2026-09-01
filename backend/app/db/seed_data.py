"""业务种子数据（2026-09-01 导出自 dev 库终态池）：中国企业出海线索 + 采集任务。

用途：其他电脑首次启动时由 init_db.seed_business_data() 导入（仅当 leads
表为空——只初始化一次，绝不覆盖已有数据）。

- 7 条线索全部为已清洗终态（ICP 门内 qualified：S×1 + B×4 + C×2），
  此前裁决删除的 jobui 死重/外企/错配官网均不在内
- 导出的是 LeadDraft 可承载的字段——导入走 upsert_lead，dedupe_key/评分/
  ICP 状态在新机器上自动重算，与采集器同一条路径
- 3 个 cron 采集任务（meta_ads/job_posting/website_enrich）按 collector 判存在后重建
"""

from __future__ import annotations

import json
from typing import Any

SEED_PAYLOAD_JSON = """
[
 {
  "name": "宁波凯越集团",
  "country": "CN",
  "city": null,
  "industry": null,
  "address": "MU Group, Building B16 (West Area), No.2560 Yongjiang Avenue, Yinzhou District, Ningbo, China, 315048",
  "phone_raw": "+86 137 3602 8159",
  "website": "https://mugroup.com",
  "email": "marketing@mugroup.com",
  "social": {
   "facebook": "https://www.facebook.com/tr?id=1226012242193902&ev=PageView&noscript=1",
   "instagram": "https://www.instagram.com/mugroup_/",
   "linkedin": "https://www.linkedin.com/company/marketunion",
   "tiktok": "https://www.tiktok.com/@mugroup_",
   "youtube": "https://www.youtube.com/channel/UCLRUbKDDrcwQf3ZE2T8F0Fw"
  },
  "whatsapp_url": "https://wa.me/8613736028159",
  "whatsapp_job": false,
  "job_urls": [
   "https://www.liepin.com/job/1962803087.shtml"
  ],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [
   "8613736028159"
  ],
  "wa_business": false,
  "overseas_signals": {
   "currencies": [
    "USD",
    "CAD"
   ],
   "languages": [
    "ar",
    "de",
    "en",
    "es",
    "fr",
    "it",
    "ja",
    "ko",
    "nl",
    "pl",
    "pt",
    "ru",
    "tr",
    "zh-tw"
   ],
   "ecommerce": [
    "woocommerce"
   ],
   "markets": [
    "US",
    "GB",
    "AE",
    "SA",
    "BR",
    "MX",
    "ID",
    "TH",
    "MY",
    "SG",
    "PH",
    "VN",
    "DE",
    "FR",
    "AU",
    "CA",
    "JP",
    "KR",
    "IN",
    "NG",
    "EG",
    "TR",
    "RU",
    "ES",
    "IT",
    "NL",
    "QA",
    "KW"
   ],
   "export_words": [
    "international business"
   ]
  },
  "job_signals": {
   "overseas_cs": {
    "label": "海外/英文客服",
    "points": 20
   }
  },
  "ad_count": 0,
  "sources": [
   "job_posting"
  ]
 },
 {
  "name": "凯迪仕",
  "country": "CN",
  "city": null,
  "industry": null,
  "address": "深圳市南山区西丽街道西丽社区仙洞路创智云城二期B2栋11层 联系",
  "phone_raw": "0755-86668868",
  "website": "https://kaadas.com",
  "email": "kds@kaadas.com",
  "social": {},
  "whatsapp_url": null,
  "whatsapp_job": false,
  "job_urls": [
   "https://www.liepin.com/job/1985213843.shtml"
  ],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [],
  "wa_business": false,
  "overseas_signals": {
   "export_words": [
    "出海"
   ]
  },
  "job_signals": {
   "overseas_cs": {
    "label": "海外/英文客服",
    "points": 20
   }
  },
  "ad_count": 0,
  "sources": [
   "job_posting"
  ]
 },
 {
  "name": "扬腾创新(福建)信息科技股份有限公司",
  "country": "CN",
  "city": null,
  "industry": "出海SaaS/工具",
  "address": null,
  "phone_raw": null,
  "website": "https://yangtenginnovation.com",
  "email": "yangtengir@cht-group.net",
  "social": {},
  "whatsapp_url": null,
  "whatsapp_job": false,
  "job_urls": [
   "https://www.liepin.com/job/1984818125.shtml"
  ],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [],
  "wa_business": false,
  "overseas_signals": {
   "export_words": [
    "跨境"
   ]
  },
  "job_signals": {
   "overseas_cs": {
    "label": "海外/英文客服",
    "points": 20
   }
  },
  "ad_count": 0,
  "sources": [
   "job_posting"
  ]
 },
 {
  "name": "TMO Group",
  "country": "CN",
  "city": null,
  "industry": null,
  "address": null,
  "phone_raw": "+65 9106 4879",
  "website": "https://tmogroup.com.cn",
  "email": "info@tmogroup.asia",
  "social": {
   "facebook": "https://www.facebook.com/tmogroup",
   "linkedin": "https://www.linkedin.com/company/tmo-group"
  },
  "whatsapp_url": null,
  "whatsapp_job": false,
  "job_urls": [],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [],
  "wa_business": false,
  "overseas_signals": {
   "ecommerce": [
    "woocommerce",
    "magento"
   ],
   "export_words": [
    "出海"
   ],
   "currencies": [
    "USD"
   ]
  },
  "job_signals": {},
  "ad_count": 0,
  "sources": [
   "web_search"
  ]
 },
 {
  "name": "独立站_跨境电商建站_品牌出海_独立站一站式SaaS服务平台-Shoptop",
  "country": "CN",
  "city": null,
  "industry": "跨境电商/品牌DTC",
  "address": null,
  "phone_raw": "400-888-3299",
  "website": "https://shoptop.com",
  "email": "mkt@shoptop.com",
  "social": {},
  "whatsapp_url": null,
  "whatsapp_job": false,
  "job_urls": [],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [],
  "wa_business": false,
  "overseas_signals": {
   "export_words": [
    "跨境"
   ]
  },
  "job_signals": {},
  "ad_count": 0,
  "sources": []
 },
 {
  "name": "Shopline全球跨境电商建站解决方案服务商-助力品牌出海-做独立站首选shopline",
  "country": "CN",
  "city": null,
  "industry": "跨境电商/品牌DTC",
  "address": "龙岗区雅宝路1号星河双子塔西塔16楼1605、1606A 深圳市 广东省 CN",
  "phone_raw": null,
  "website": "https://shoplineapp.cn",
  "email": "support@shopline.com",
  "social": {},
  "whatsapp_url": null,
  "whatsapp_job": false,
  "job_urls": [],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [],
  "wa_business": false,
  "overseas_signals": {
   "currencies": [
    "USD",
    "BHD"
   ],
   "languages": [
    "en",
    "en-au",
    "en-gb",
    "en-hk",
    "en-my",
    "en-sg",
    "id-id"
   ],
   "ecommerce": [
    "woocommerce"
   ],
   "markets": [
    "US",
    "GB",
    "MY",
    "SG",
    "VN",
    "AU"
   ],
   "export_words": [
    "跨境"
   ]
  },
  "job_signals": {},
  "ad_count": 0,
  "sources": []
 },
 {
  "name": "DTC东泰_高端全屋五金系统解决方案专家_国际一线，天生耐用",
  "country": "CN",
  "city": null,
  "industry": null,
  "address": "广东省佛山市顺德区勒流街道新安村南国西路263号",
  "phone_raw": "0757-25332283",
  "website": "https://dtcdtc.cn",
  "email": "dtc@dtcdtc.com",
  "social": {},
  "whatsapp_url": null,
  "whatsapp_job": false,
  "job_urls": [],
  "is_cn": true,
  "fb_whatsapp": false,
  "target_countries": [],
  "whatsapp_numbers": [],
  "wa_business": false,
  "overseas_signals": {
   "markets": [
    "FR"
   ]
  },
  "job_signals": {},
  "ad_count": 0,
  "sources": [
   "web_search"
  ]
 }
]
"""

_TASKS_JSON = """
[
 {
  "name": "Meta 广告库挖掘（每日）",
  "collector": "meta_ads",
  "cron_expr": "30 2 * * *",
  "params": {
   "keywords": "smart watch,leggings,wig,shapewear,led strip light,phone case,jewelry,game",
   "countries": "MY,SG,ID,TH,PH,VN,AE,SA",
   "probe_pages": "true",
   "max_pages": "2"
  }
 },
 {
  "name": "招聘信号巡检（每日）",
  "collector": "job_posting",
  "cron_expr": "30 3 * * *",
  "params": {
   "site": "jobui",
   "keywords": "跨境电商客服,英语客服,海外社媒运营,私域运营,外贸业务员",
   "discover_new": "false"
  }
 },
 {
  "name": "网站富化·全库分级（每日）",
  "collector": "website_enrich",
  "cron_expr": "0 4 * * *",
  "params": {}
 }
]
"""

SEED_PAYLOAD: dict[str, Any] = {
    "leads": json.loads(SEED_PAYLOAD_JSON),
    "tasks": json.loads(_TASKS_JSON),
}
SEED_LEAD_COUNT = len(SEED_PAYLOAD["leads"])
