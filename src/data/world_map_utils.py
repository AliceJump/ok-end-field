from src.data.world_map import (
    canonicalize_area,
    canonicalize_stage_category,
    goods_dict_by_id,
    outpost_dict_by_id,
    stages_dict_by_id,
    stage_category_id_to_label,
    area_id_to_label,
)


def get_area_by_outpost_name(outpost_name: str) -> str:
    """
    根据据点名称获取该据点所在区域
    参数:
        outpost_name: 据点名称
    返回值:
        该据点所在区域，如果据点不存在返回空字符串
    """
    for area_id, outposts in outpost_dict_by_id.items():
        if outpost_name in outposts:
            return area_id_to_label(area_id)
    return ""


def get_goods_by_outpost_name(outpost_name: str) -> list[str]:
    """
    根据据点名称获取该据点可交易的货物列表
    参数:
        outpost_name: 据点名称
    返回值:
        该据点的货物列表，如果据点不存在返回空列表
    """
    for area_id, outposts in outpost_dict_by_id.items():
        if outpost_name in outposts:
            return goods_dict_by_id.get(area_id, [])
    return []


def get_stage_category(stage_name):
    for category_id, stages in stages_dict_by_id.items():
        if stage_name in stages:
            return stage_category_id_to_label(category_id)
    return None


def get_area_id(area_or_id: str) -> str:
    return canonicalize_area(area_or_id)


def get_area_id_by_outpost_name(outpost_name: str) -> str:
    for area_id, outposts in outpost_dict_by_id.items():
        if outpost_name in outposts:
            return area_id
    return ""


def get_stage_category_id(category_or_id: str) -> str:
    return canonicalize_stage_category(category_or_id)
