"""业务种子数据（2026-08-31 导出自 dev 库）：中国企业出海线索 + 采集任务。

用途：其他电脑首次启动时由 init_db.seed_business_data() 导入（仅当 leads
表为空——只初始化一次，绝不覆盖已有数据）。

- 93 条线索全部为中国企业（ICP 门内：qualified 4 种子企业 + cn_domestic 89
  jobui 招聘线索），外国企业历史数据已清理
- 导出的是 LeadDraft 可承载的字段——导入走 upsert_lead，dedupe_key/评分/
  ICP 状态在新机器上自动重算，与采集器同一条路径
- 3 个 cron 采集任务（meta_ads/job_posting/website_enrich）按 collector
  判存在后重建
"""

from __future__ import annotations

import json
from typing import Any

SEED_PAYLOAD_JSON = """
{
 "leads": [
  {
   "name": "深圳星河智能科技",
   "country": "CN",
   "city": "深圳",
   "industry": "跨境电商",
   "address": null,
   "phone_raw": "+8675512340001",
   "email": null,
   "website": "https://xinghe-smart.com",
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "广州蓝鲸互动",
   "country": "CN",
   "city": "广州",
   "industry": "游戏出海",
   "address": null,
   "phone_raw": "+8620856720002",
   "email": null,
   "website": "https://bluewhale.games",
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
     "USD"
    ]
   },
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "上海赫冷制冷机电设备有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/315174730/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "日志动力传送系统（上海）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/332284665/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海派能能源科技股份有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/335203766/",
    "https://www.jobui.com/job/335279253/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "北京外企德科人力资源服务上海有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/336638661/",
    "https://www.jobui.com/job/335689999/",
    "https://www.jobui.com/job/335690005/",
    "https://www.jobui.com/job/335598285/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海博华国际展览有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/339042138/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "四联创业集团股份有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/339169487/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "深圳传音控股股份有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340037133/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "海隆石油工业集团有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340301976/",
    "https://www.jobui.com/job/341080749/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海肉包机器人科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340682867/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海奥格利环保工程有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340950770/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "哈尔滨敷尔佳科技股份有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340975395/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海高顿教育科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341374633/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海郡帅特种电缆有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341740239/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "深圳枭龙云科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341897631/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海麦酷酷电子商务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/342079718/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海月迹信息科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/342153752/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海旷印新材料科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/342168464/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海趣拉科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/342191252/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海琨达国际货运代理有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/258618769/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海泰税利信息技术有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/320308852/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海中城卫安全管理咨询服务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/324779204/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "安吉汽车租赁有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/330343756/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海姚记科技股份有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/332464085/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海千亚国际货物运输代理有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/338054726/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "克瑞国际商贸（北京）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/338975553/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "万宝盛华人力资源（中国）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/339379002/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "万欣和（上海）企业服务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340100916/",
    "https://www.jobui.com/job/340100910/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "合肥鼠宝信息服务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340127405/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "佛山市寰球英才人力资源有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": "https://talent-inchina.com",
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/319108454/",
    "https://www.jobui.com/job/325046035/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海南霞松实业有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/321181073/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "无锡市锦钿新材料科技发展有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/325633513/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "广东善世企业服务集团有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/332556510/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "孝感霖云信息技术有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/335696297/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "携程计算机技术（上海）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337678408/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "安徽希诺控股有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340307777/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海得翼国际货运代理有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": "https://alliance-acl.com",
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/307774663/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "雪伏特物流科技（上海）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/307774750/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "嘉林国际物流有限公司上海分公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/312853498/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海中外运伟运国际物流有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/334483737/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "上海赞佟国际物流有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/336842265/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "深圳洋星通运国际货运代理有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/338751416/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "深圳市长帆国际物流股份有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341720700/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
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
   "name": "安克创新科技股份有限公司",
   "country": "CN",
   "city": "深圳",
   "industry": "消费电子出海",
   "address": null,
   "phone_raw": null,
   "email": "support@anker.com",
   "website": "https://www.anker.com",
   "social": {
    "facebook": "https://www.facebook.com/Anker.fans",
    "instagram": "https://www.instagram.com/anker_official/",
    "tiktok": "https://www.tiktok.com/@ankerofficial?ref=footer",
    "youtube": "https://www.youtube.com/user/AnkerOceanwing"
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
    "currencies": [
     "USD",
     "QAR",
     "KWD",
     "BHD",
     "MYR"
    ],
    "languages": [
     "en-au",
     "en-ca",
     "en-eu",
     "en-gb",
     "en-my",
     "en-nz",
     "en-us",
     "fr",
     "fr-ca",
     "fr-fr",
     "pl-pl",
     "vi-vn"
    ],
    "ecommerce": [
     "shopify"
    ],
    "markets": [
     "US",
     "GB",
     "AE",
     "MX",
     "DE",
     "FR",
     "AU",
     "CA",
     "IT",
     "NL"
    ]
   },
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "希音(SHEIN)",
   "country": "CN",
   "city": "南京",
   "industry": "跨境电商",
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": "https://shein.com",
   "social": {
    "instagram": "https://www.instagram.com/sheinofficial",
    "tiktok": "https://www.tiktok.com/@shein_official"
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
    "currencies": [
     "USD",
     "EUR",
     "GBP",
     "AED",
     "QAR",
     "KWD",
     "BHD",
     "JPY",
     "AUD",
     "CAD"
    ],
    "languages": [
     "ar-at",
     "ar-au",
     "ar-ch",
     "ar-de",
     "ar-il",
     "ar-jo",
     "ar-ma",
     "ar-nl",
     "ar-sa",
     "ar-se",
     "bg-bg",
     "cs-cz",
     "da-dk",
     "de-at",
     "de-ch",
     "de-de",
     "el-gr",
     "en",
     "en-au",
     "en-ca",
     "en-ch",
     "en-cl",
     "en-co",
     "en-cz",
     "en-de",
     "en-es",
     "en-fi",
     "en-fr",
     "en-gb",
     "en-gr",
     "en-id",
     "en-il",
     "en-in",
     "en-it",
     "en-jo",
     "en-jp",
     "en-kr",
     "en-mx",
     "en-my",
     "en-nl",
     "en-nz",
     "en-ph",
     "en-pl",
     "en-pt",
     "en-ro",
     "en-se",
     "en-sg",
     "en-th",
     "en-tr",
     "en-tw",
     "en-us",
     "en-vn",
     "en-za",
     "es",
     "es-ar",
     "es-au",
     "es-ch",
     "es-cl",
     "es-co",
     "es-ec",
     "es-es",
     "es-mx",
     "es-nl",
     "es-pe",
     "es-pt",
     "es-us",
     "fi-fi",
     "fr-ca",
     "fr-ch",
     "fr-fr",
     "fr-ma",
     "he-il",
     "hu-at",
     "hu-ro",
     "id-sg",
     "id-tw",
     "it-ch",
     "it-it",
     "ja-jp",
     "ko-kr",
     "ms-my",
     "nl-nl",
     "pl-nl",
     "pl-pl",
     "pt",
     "pt-br",
     "pt-ch",
     "pt-nl",
     "pt-pt",
     "ro-at",
     "ro-nl",
     "ro-ro",
     "ru-il",
     "ru-nl",
     "ru-ru",
     "sk-sk",
     "sr-ua",
     "sv-se",
     "th-th",
     "tr-at",
     "tr-nl",
     "tr-tr",
     "uk-pl",
     "vi-vn",
     "zh-hk",
     "zh-my",
     "zh-sg"
    ],
    "markets": [
     "US",
     "ID",
     "MY",
     "NL"
    ]
   },
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "深圳市浩方科技",
   "country": "CN",
   "city": "深圳",
   "industry": "跨境电商",
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": "https://www.hofan.com",
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "广州棒谷科技",
   "country": "CN",
   "city": "广州",
   "industry": "跨境电商",
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": "https://www.banggood.com",
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "宁波乐歌人体工学科技股份有限公司",
   "country": "CN",
   "city": "宁波",
   "industry": "家居出海",
   "address": null,
   "phone_raw": null,
   "email": "contact@flexispot.com",
   "website": "https://www.flexispot.com",
   "social": {
    "facebook": "https://www.facebook.com/tr?id=178276095341397&ev=PageView&noscript=1"
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
    "currencies": [
     "USD",
     "QAR",
     "KWD",
     "BHD",
     "MYR"
    ],
    "markets": [
     "US",
     "GB",
     "PH",
     "DE",
     "FR",
     "AU",
     "CA",
     "JP",
     "ES",
     "IT",
     "NL"
    ]
   },
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "seed_import"
   ]
  },
  {
   "name": "上海利物盛企业集团有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/312937078/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "无锡尚佰环球电子商务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/317389938/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海广为电器集团有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/319100309/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海甦毓信息技术有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/319172166/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海卓越荟广告科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/321124005/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海乐漾电子商务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/328817885/",
    "https://www.jobui.com/job/333214516/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "青岛简沐电商运营管理有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/332355598/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海立庭轩居家用品有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/334556197/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "苏州克斯宝德电子科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/336439124/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海新百达制冷设备有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337044439/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海威胜科技发展有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337150165/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "德州明佳数控机械设备有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337582939/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "莱沐企业管理咨询（上海）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/338510959/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海绿弋能源科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340128818/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "临沂格然装饰材料有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340128820/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海姿东智能科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340943345/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海炘涛电子商务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341263126/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "华佩贸易（上海）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341919234/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海尧骅国际贸易有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341991896/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "吉林省大乾文化传媒有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/332476012/",
    "https://www.jobui.com/job/338413361/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {
    "social_ops": {
     "label": "海外社媒运营",
     "points": 15
    }
   },
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "北京京东世纪贸易有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/333168280/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "深圳南区人瑞人力资源服务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340583005/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {
    "social_ops": {
     "label": "海外社媒运营",
     "points": 15
    }
   },
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海宏皇信息技术有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341314973/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海乘势电子商务有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341919665/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海寻梦信息技术有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/110219690/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海宏竹通信设备工程有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/317653938/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海德旺美文化有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/330232457/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海畅居智慧信息技术有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/330401932/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "全速创想数字科技（杭州）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/331013023/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海禾赛科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/333353126/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "美敦力（上海）管理有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/335482872/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海好财网企业管理集团有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/335690427/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海智航创网络科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/336402288/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "大连高名兴隆科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/336402289/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "苏州旷途科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337065088/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海光键半导体设备有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337238627/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海天战信息科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/337428734/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "江苏成功企业管理有限公司苏州分公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/338548160/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "时代云驰交通工具技术（苏州）有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/339432960/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "济南东策网络科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340051219/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "河北浅银新能科技有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340051220/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "广东省电信规划设计院有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/340426151/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "安科瑞有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/341919073/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  },
  {
   "name": "上海蔚来汽车有限公司",
   "country": "CN",
   "city": null,
   "industry": null,
   "address": null,
   "phone_raw": null,
   "email": null,
   "website": null,
   "social": {},
   "whatsapp_url": null,
   "whatsapp_job": false,
   "job_urls": [
    "https://www.jobui.com/job/342170296/"
   ],
   "is_cn": true,
   "fb_whatsapp": false,
   "target_countries": [],
   "whatsapp_numbers": [],
   "wa_business": false,
   "overseas_signals": {},
   "job_signals": {},
   "ad_count": 0,
   "sources": [
    "job_posting"
   ]
  }
 ],
 "tasks": [
  {
   "name": "Meta 广告库（出海投放挖掘）",
   "collector": "meta_ads",
   "params": {
    "keywords": "smart watch,leggings,wig,led light,crossbody bag",
    "countries": "MY,SG,ID,TH,SA,AE"
   },
   "cron_expr": "0 7 * * *"
  },
  {
   "name": "中国招聘网站监控（jobui）",
   "collector": "job_posting",
   "params": {
    "keywords": "海外客服,跨境电商客服,海外社媒运营,私域运营,外贸业务员",
    "max_pages": "2"
   },
   "cron_expr": "30 7 * * *"
  },
  {
   "name": "网站富化（检测 WhatsApp/邮箱/社媒）",
   "collector": "website_enrich",
   "params": {
    "lead_ids": ""
   },
   "cron_expr": "0 8 * * *"
  }
 ]
}
"""

SEED_PAYLOAD: dict[str, Any] = json.loads(SEED_PAYLOAD_JSON)

# 线索数（文档自检用；导入时以此校验完整性）
SEED_LEAD_COUNT = len(SEED_PAYLOAD["leads"])
