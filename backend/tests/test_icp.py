

def test_labor_service_providers_are_non_buyer():
    """劳动型服务商族出池（2026-09-01 裁决）：人力资源/劳务派遣/管理咨询/
    企业服务/信息服务 与客服外包同族——替别人招人/卖服务，不是卖货方。"""
    from app.collectors.icp import is_non_buyer

    for name in (
        "佛山市寰球英才人力资源有限公司",
        "北京外企德科人力资源服务上海有限公司",
        "上海中城卫安全管理咨询服务有限公司",
        "广东善世企业服务集团有限公司",
        "合肥鼠宝信息服务有限公司",
    ):
        assert is_non_buyer(name=name), name
    # 卖货企业不受影响（词表宁可窄不可误杀）
    for name in ("鄱阳县黑金刚钓具有限责任公司", "克瑞国际商贸（北京）有限公司", "宁波凯越国际贸易有限公司"):
        assert not is_non_buyer(name=name), name
