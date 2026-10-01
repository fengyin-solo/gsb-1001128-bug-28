"""绿化区域切片发布的示例数据。

只描述绿化发布域用到的几张表：班组清单、版本指针、审定附件候选、旧版切片、
班组任务、地图引用、地图待办与历史修剪区间。独立成文件是为了避免 seed.py 与
服务层互相导入。
"""
from __future__ import annotations

from typing import Any


def stake(value: int) -> str:
    """米数转桩号：650 -> K0+650。"""
    return f"K{value // 1000}+{value % 1000:03d}"


def enrich_green_rows(green_rows: list[dict[str, Any]]) -> None:
    """把通用占位的绿化台账三行补成带版本键的真实样例。"""
    profiles = {
        "GREE-0001": {
            "区域名称": "中央分车绿带（北环段）",
            "植物品种": "红叶石楠、金森女贞",
            "面积": "4200㎡",
            "上次修剪": "2026-08-10",
            "上次浇水": "2026-09-20",
            "管养班组": "绿化一班",
            "管养状态": "正常",
            "status": "正常",
            "pending": False,
            "abnormal": False,
            "边界起讫": f"{stake(0)}-{stake(400)}",
            "区域版本": "v1",
            "版本键": "GREE-0001@v1",
            "最新审定附件": "附件-G1-v1",
            "发布结论": "v1 旧版生效中，v2 边界待审定",
        },
        "GREE-0002": {
            "区域名称": "行道树绿带（北环段）",
            "植物品种": "香樟、法桐",
            "面积": "3300㎡",
            "上次修剪": "2026-08-12",
            "上次浇水": "2026-09-18",
            "管养班组": "绿化二班",
            "管养状态": "待修剪",
            "status": "待修剪",
            "pending": True,
            "abnormal": False,
            "边界起讫": f"{stake(400)}-{stake(700)}",
            "区域版本": "v1",
            "版本键": "GREE-0002@v1",
            "最新审定附件": "附件-G2-v1",
            "发布结论": "v1 旧版生效中，v2 边界待审定",
        },
        "GREE-0003": {
            "区域名称": "立交匝道绿地",
            "植物品种": "麦冬、迎春",
            "面积": "5100㎡",
            "上次修剪": "2026-08-15",
            "上次浇水": "2026-09-19",
            "管养班组": "绿化三班",
            "管养状态": "正常",
            "status": "正常",
            "pending": False,
            "abnormal": False,
            "边界起讫": f"{stake(700)}-{stake(1000)}",
            "区域版本": "v1",
            "版本键": "GREE-0003@v1",
            "最新审定附件": "附件-G3-v1",
            "发布结论": "v1 旧版生效中，v2 边界待审定",
        },
    }
    for row in green_rows:
        profile = profiles.get(str(row.get("区域编号")))
        if profile:
            row.update(profile)


def _candidate_attachments() -> dict[str, list[dict[str, Any]]]:
    """每个区域的 v2 候选附件：正式审定附件与临时围挡并存时，以正式审定为准。"""
    return {
        "GREE-0001": [
            {"附件编号": "附件-G1-v2", "附件类型": "正式审定", "起点": 0, "止点": 400,
             "边界起讫": f"{stake(0)}-{stake(400)}", "审定日期": "2026-09-25", "采纳": True},
            {"附件编号": "附件-G1-v2-围挡", "附件类型": "临时围挡", "起点": 300, "止点": 460,
             "边界起讫": f"{stake(300)}-{stake(460)}", "审定日期": "2026-09-26", "采纳": False,
             "冲突说明": "临时围挡与正式审定绿线在 K0+300-K0+400 冲突，且越界到相邻区域"},
        ],
        "GREE-0002": [
            {"附件编号": "附件-G2-v2", "附件类型": "正式审定", "起点": 400, "止点": 720,
             "边界起讫": f"{stake(400)}-{stake(720)}", "审定日期": "2026-09-25", "采纳": True},
        ],
        "GREE-0003": [
            {"附件编号": "附件-G3-v2", "附件类型": "正式审定", "起点": 720, "止点": 1020,
             "边界起讫": f"{stake(720)}-{stake(1020)}", "审定日期": "2026-09-25", "采纳": True},
            {"附件编号": "附件-G3-v2-围挡", "附件类型": "临时围挡", "起点": 950, "止点": 1050,
             "边界起讫": f"{stake(950)}-{stake(1050)}", "审定日期": "2026-09-27", "采纳": False,
             "冲突说明": "临时围挡超出正式审定绿线 K1+020，冲突段以正式审定附件为准"},
        ],
    }


def build_green_publish_seed() -> dict[str, list[dict[str, Any]]]:
    """构造绿化发布域的全部样例表。"""
    attachments = _candidate_attachments()

    crews = [
        {"id": 1, "班组名称": "绿化一班", "负责区域": ["GREE-0001"],
         "当前版本键": "GREE-0001@v1", "工作面": f"{stake(0)}-{stake(400)}",
         "排班卡": "周一/三/五 08:00-11:00", "状态": "在岗"},
        {"id": 2, "班组名称": "绿化二班", "负责区域": ["GREE-0002"],
         "当前版本键": "GREE-0002@v1", "工作面": f"{stake(400)}-{stake(700)}",
         "排班卡": "周二/四 08:00-11:00", "状态": "在岗"},
        {"id": 3, "班组名称": "绿化三班", "负责区域": ["GREE-0003"],
         "当前版本键": "GREE-0003@v1", "工作面": f"{stake(700)}-{stake(1000)}",
         "排班卡": "周一至周五 13:30-16:30", "状态": "在岗"},
    ]

    # 历史修剪区间：发布与迁移都只能携带、不能删除这些区间。
    trim_history = [
        {"id": 1, "区域编号": "GREE-0001", "版本键": "GREE-0001@v1",
         "桩号区间": f"{stake(100)}-{stake(200)}", "起点": 100, "止点": 200,
         "上次修剪": "2026-08-10", "备注": "历史修剪区间随切片保留"},
        {"id": 2, "区域编号": "GREE-0001", "版本键": "GREE-0001@v1",
         "桩号区间": f"{stake(200)}-{stake(300)}", "起点": 200, "止点": 300,
         "上次修剪": "2026-07-15", "备注": "历史修剪区间随切片保留"},
        {"id": 3, "区域编号": "GREE-0002", "版本键": "GREE-0002@v1",
         "桩号区间": f"{stake(450)}-{stake(550)}", "起点": 450, "止点": 550,
         "上次修剪": "2026-08-12", "备注": "历史修剪区间随切片保留"},
        {"id": 4, "区域编号": "GREE-0003", "版本键": "GREE-0003@v1",
         "桩号区间": f"{stake(800)}-{stake(900)}", "起点": 800, "止点": 900,
         "上次修剪": "2026-08-15", "备注": "历史修剪区间随切片保留"},
        {"id": 5, "区域编号": "GREE-0003", "版本键": "GREE-0003@v1",
         "桩号区间": f"{stake(950)}-{stake(1000)}", "起点": 950, "止点": 1000,
         "上次修剪": "2026-07-20", "备注": "历史修剪区间随切片保留"},
    ]

    pointers = []
    for idx, code in enumerate(("GREE-0001", "GREE-0002", "GREE-0003"), start=1):
        pointers.append({
            "id": idx,
            "区域编号": code,
            "当前发布版本": "v1",
            "当前版本键": f"{code}@v1",
            "草稿版本": "v2",
            "草稿版本键": f"{code}@v2",
            "最新审定附件": f"附件-G{idx}-v1",
            "边界状态": "v2 待审定",
            "候选附件": attachments[code],
        })

    # 旧版已发布切片（200 米一片，最后一段允许不足 200 米）。
    old_slices_spec = [
        ("SL-G1-v1-01", "GREE-0001", 0, 200, "绿化一班"),
        ("SL-G1-v1-02", "GREE-0001", 200, 400, "绿化一班"),
        ("SL-G2-v1-01", "GREE-0002", 400, 600, "绿化二班"),
        ("SL-G2-v1-02", "GREE-0002", 600, 700, "绿化二班"),
        ("SL-G3-v1-01", "GREE-0003", 700, 900, "绿化三班"),
        ("SL-G3-v1-02", "GREE-0003", 900, 1000, "绿化三班"),
    ]
    slices: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []
    map_refs: list[dict[str, Any]] = []
    for seq, (code_no, code, start, end, crew) in enumerate(old_slices_spec, start=1):
        interval = f"{stake(start)}-{stake(end)}"
        slices.append({
            "id": seq, "切片编号": code_no, "发布单编号": None,
            "区域编号": code, "版本键": f"{code}@v1", "起点": start, "止点": end,
            "桩号区间": interval, "责任班组": crew, "发布状态": "已发布",
            "历史修剪区间": [h["桩号区间"] for h in trim_history
                          if h["区域编号"] == code and h["起点"] < end and h["止点"] > start],
            "父切片": None, "需拆分": False, "迁移信息": None,
        })
        tasks.append({
            "id": seq, "任务编号": f"TASK-v1-{seq:03d}", "发布单编号": None,
            "切片编号": code_no, "区域编号": code, "版本键": f"{code}@v1",
            "班组名称": crew, "工作面": interval,
            "起点": start, "止点": end,
            "排班卡": next(c["排班卡"] for c in crews if c["班组名称"] == crew),
            "状态": "已排班", "幂等键": f"{code}@v1:{code_no}:task",
        })
        map_refs.append({
            "id": seq, "地图引用编号": f"MAP-v1-{seq:03d}", "发布单编号": None,
            "切片编号": code_no, "区域编号": code, "版本键": f"{code}@v1",
            "起点": start, "止点": end, "桩号区间": interval,
            "引用类型": "绿线", "状态": "生效中", "替换自": None,
            "幂等键": f"{code}@v1:{code_no}:map",
        })

    todos = []
    for idx, code in enumerate(("GREE-0001", "GREE-0002", "GREE-0003"), start=1):
        todos.append({
            "id": idx, "待办编号": f"TODO-G{idx}-v2",
            "区域编号": code, "版本键": f"{code}@v2",
            "事项": f"确认 {code} v2 绿化边界，核对切片责任并发布",
            "状态": "待处理", "发布单编号": None, "结论": None,
        })

    return {
        "green_crew": crews,
        "green_trim_history": trim_history,
        "green_version_pointer": pointers,
        "green_publish_order": [],
        "green_slice": slices,
        "green_crew_task": tasks,
        "green_map_ref": map_refs,
        "green_map_todo": todos,
    }
