"""行业词表 → 中文展示名（列表筛选下拉用）。

来源混装：google_maps 存搜索关键词、meta_ads 存 FB 类目映射、手工/种子导入
任意填——label 未收录的原样显示（value 始终保持原 token 保证筛选精确）。
"""

INDUSTRY_LABELS_ZH: dict[str, str] = {
    # shop 零售/服务
    "supermarket": "超市", "convenience": "便利店", "department_store": "百货商场", "mall": "购物中心",
    "clothes": "服装店", "shoes": "鞋店", "boutique": "精品店", "jewellery": "珠宝店", "watches": "钟表店",
    "bakery": "面包店", "butcher": "肉铺", "greengrocer": "蔬果店", "seafood": "水产店", "confectionery": "糖果店",
    "coffee": "咖啡豆店", "tea": "茶叶店", "alcohol": "酒类商店", "wine": "葡萄酒庄",
    "electronics": "电子产品店", "computer": "电脑店", "mobile_phone": "手机店", "hifi": "音响店",
    "furniture": "家具店", "interior_decoration": "室内装饰", "kitchen": "厨具店", "bed": "床品店",
    "hardware": "五金店", "doityourself": "DIY 用品", "garden_centre": "园艺中心", "florist": "花店",
    "gift": "礼品店", "toys": "玩具店", "sports": "运动用品店", "stationery": "文具店", "books": "书店",
    "newsagent": "报刊亭", "music": "音像店", "musical_instrument": "乐器店", "photo": "照相馆",
    "laundry": "洗衣店", "dry_cleaning": "干洗店", "tailor": "裁缝店", "travel_agency": "旅行社",
    "estate_agent": "房产中介", "deli": "熟食店", "frozen_food": "冷冻食品店", "cheese": "奶酪店",
    # shop 美业/健康
    "beauty": "美容店", "hairdresser": "美发店", "cosmetics": "化妆品店", "massage": "按摩店",
    "tattoo": "纹身店", "perfumery": "香水店",
    "dentist": "牙科诊所", "pharmacy": "药店", "optician": "眼镜店", "medical_supply": "医疗用品店",
    "hearing_aids": "助听器店", "herbalist": "草药店", "nutrition_supplements": "营养品店",
    "pet": "宠物店", "pet_grooming": "宠物美容", "agrarian": "农资店",
    # shop 汽车/其他
    "car": "汽车销售", "car_repair": "汽修店", "car_parts": "汽配店", "tyres": "轮胎店",
    "motorcycle": "摩托车行", "caravan": "房车行", "boat": "船具店", "bicycle": "自行车店",
    "outdoor": "户外用品店", "copyshop": "打印店", "funeral_directors": "殡葬服务", "money_transfer": "汇款服务",
    # amenity 餐饮
    "restaurant": "餐厅", "cafe": "咖啡馆", "fast_food": "快餐店", "food_court": "美食广场",
    "ice_cream": "冰淇淋店", "bar": "酒吧", "pub": "酒馆", "biergarten": "啤酒园", "nightclub": "夜店",
    # amenity 医疗/教育/金融
    "clinic": "诊所", "hospital": "医院", "doctors": "门诊", "veterinary": "宠物医院",
    "school": "学校", "college": "学院", "university": "大学", "kindergarten": "幼儿园",
    "language_school": "语言学校", "driving_school": "驾校", "music_school": "音乐学校", "dance_school": "舞蹈学校",
    "bank": "银行", "bureau_de_change": "货币兑换", "post_office": "邮局",
    # amenity 汽车及其他
    "fuel": "加油站", "car_wash": "洗车店", "charging_station": "充电站", "parking": "停车场",
    "coworking_space": "联合办公", "events_venue": "活动场地", "community_centre": "社区中心",
    # healthcare 值
    "alternative": "替代医学", "midwife": "助产士", "physiotherapist": "物理治疗", "psychotherapist": "心理治疗",
    "laboratory": "检验实验室", "optometrist": "验光中心", "rehabilitation": "康复中心", "blood_donation": "献血站",
    "counselling": "心理咨询", "audiologist": "听力中心", "speech_therapist": "语言治疗",
    # office 值
    "company": "公司", "it": "IT 公司", "financial": "金融公司", "insurance": "保险公司",
    "lawyer": "律所", "accountant": "会计", "consulting": "咨询公司", "advertising_agency": "广告公司",
    "architect": "建筑师事务所", "engineering": "工程公司", "surveyor": "测量公司", "estate_agent_office": "房产公司",
    "educational_institution": "教育机构", "travel_agent": "旅行社", "employment_agency": "职业中介",
    "government": "政府机构", "ngo": "公益组织",
    # craft 值
    "carpenter": "木工", "joiner": "细木工", "electrician": "电工", "plumber": "水管工",
    "builder": "施工队", "roofing": "屋顶施工", "painter": "油漆工", "gardener": "园艺服务",
    "locksmith": "锁匠", "shoemaker": "修鞋店", "dressmaker": "缝纫店", "photographer": "摄影工作室",
    "winery": "酿酒坊", "sawmill": "锯木厂", "metal_construction": "金属加工",
    # tourism / leisure 值
    "hotel": "酒店", "guest_house": "民宿", "hostel": "青年旅舍", "motel": "汽车旅馆", "apartment": "公寓",
    "attraction": "景点", "museum": "博物馆", "theme_park": "主题乐园", "zoo": "动物园", "aquarium": "水族馆",
    "fitness_centre": "健身房", "sports_centre": "运动中心", "fitness_station": "健身角", "dance": "舞蹈中心",
    "amusement_arcade": "游戏厅", "escape_game": "密室逃脱", "bowling_alley": "保龄球馆",
}
