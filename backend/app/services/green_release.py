"""区域切片发布单：绿化区域版本发布的唯一中枢。

列表页、详情页、地图入口看到的版本结论都从这里取，避免三处各读各的造成
区域版本错位。发布单向推进：

    待审定 ──确认审定附件──▶ 待核对责任 ──核对切片责任──▶ 待发布 ──执行发布──▶ 已发布
                                                                      │
                                                              （连接断开可续发）
                                                                      ▼
                                                                   失败复位

硬规则：

- 审定附件后才能切片，未审定不得跳级生成班组任务；
- 绿线与临时围挡冲突时以正式审定附件为准，并保留历史修剪区间；
- 存量重叠区迁移前先按责任关系拆分；
- 地图切片、班组任务（班组工作面＋排班卡）、地图引用（地图待办）在同一事务
  内整批落库，失败整批回滚；
- 发布按版本键幂等：同一区域同一版本并发发布只生效一次；
- 连接断开后从未发布切片继续续发；最终未成功则复位整张旧图，不残留半成品；
- 撤回发布级联清理本单全部切片/任务/待办并恢复旧图，不留重复作业。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.store import store

MODULE = "green"
TABLE_ORDER = "green_release_order"
TABLE_TILE = "green_map_tile"
TABLE_TASK = "green_crew_task"
TABLE_TODO = "green_map_todo"

STAGE_DRAFT = "待审定"
STAGE_RESPONSIBILITY = "待核对责任"
STAGE_READY = "待发布"
STAGE_PUBLISHED = "已发布"
STAGE_WITHDRAWN = "已撤回"

SOURCE_FORMAL = "正式审定"
SOURCE_TEMP = "临时围挡"

TILE_ACTIVE = "生效"
TILE_MIGRATED = "已迁移"
TILE_WITHDRAWN = "已撤回"
TASK_ACTIVE = "生效"
TASK_WITHDRAWN = "已撤回"
TODO_OPEN = "待处理"
TODO_DONE = "已处理"
TODO_WITHDRAWN = "已撤回"


class ReleaseError(Exception):
    """发布流程被业务规则拦下；消息可直接提示给用户。"""


def version_key(region_code: str, version: str) -> str:
    return f"{region_code}@{version}"


def parse_meter(value: Any) -> float:
    """把桩号「K1+200」或米数统一成米。"""
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().upper().replace("Ｋ", "K")
    if "K" in text:
        km, rest = text.split("K", 1)
        km = km.strip() or "0"
        meter = rest.replace("+", "").strip() or "0"
        return float(km) * 1000 + float(meter)
    return float(text)


def fmt_chainage(meter: float) -> str:
    meter = int(round(meter))
    return f"K{meter // 1000}+{meter % 1000:03d}"


def interval_text(start: float, end: float) -> str:
    return f"{fmt_chainage(start)}~{fmt_chainage(end)}"


def _find_region(region_code: str) -> dict[str, Any] | None:
    for row in store.rows(MODULE):
        if str(row.get("区域编号", "")) == region_code:
            return row
    return None


def _find_order_by_id(order_id: int) -> dict[str, Any] | None:
    return store.find(TABLE_ORDER, order_id)


def _find_order_by_key(key: str) -> dict[str, Any] | None:
    for order in store.rows(TABLE_ORDER):
        if order.get("版本键") == key and order.get("阶段") != STAGE_WITHDRAWN:
            return order
    return None


def _today_plus(days: int) -> str:
    return (date(2026, 10, 1) + timedelta(days=days)).isoformat()


class GreenReleaseService:
    """区域切片发布单应用服务；无状态，数据都在 store 里。"""

    # ---------- 发布单 ----------

    def open_order(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        region_code = str(values.get("区域编号") or "").strip()
        version = str(values.get("版本") or "").strip()
        region = _find_region(region_code)
        if region is None:
            return None, f"绿化区域 {region_code} 不存在，无法创建发布单"
        if not version:
            return None, "发布版本不能为空"
        key = version_key(region_code, version)
        with store.key_lock(f"release:{key}"):
            existing = _find_order_by_key(key)
            if existing is not None:
                return existing, f"版本 {version} 的发布单已存在，继续按既有发布单推进"
            segments = self._normalize_segments(values.get("边界段落") or [])
            base_version = str(values.get("基础版本") or region.get("已发布版本") or "").strip()
            order = {
                "id": store.next_id(TABLE_ORDER),
                "发布单号": f"PUB-{region_code}-{version}",
                "区域台账ID": region.get("id"),
                "区域编号": region_code,
                "区域名称": region.get("区域名称"),
                "版本": version,
                "版本键": key,
                "基础版本": base_version,
                "阶段": STAGE_DRAFT,
                "审定附件": None,
                "原始边界段落": segments,
                "切片": [],
                "迁移记录": [],
                "断点游标": 0,
                "发布键": key,
                "已发布切片": [],
                "发布结果": None,
                "撤回记录": None,
                "创建时间": _today_plus(0),
                "更新时间": _today_plus(0),
                "操作人": str(values.get("操作人") or "值班员"),
            }
            store.rows(TABLE_ORDER).append(order)
            return order, "区域切片发布单已创建，请先确认审定附件"

    def confirm_attachment(self, order_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        order = _find_order_by_id(order_id)
        if order is None:
            return None, f"发布单 {order_id} 不存在"
        with store.key_lock(f"release:{order['版本键']}"):
            if order["阶段"] == STAGE_PUBLISHED:
                return order, "该版本已发布，审定结论以已发布的正式附件为准"
            if order["阶段"] != STAGE_DRAFT:
                return None, f"当前阶段为「{order['阶段']}」，审定附件只能在第一步确认，不能回退重提"
            filename = str(values.get("文件名") or "").strip()
            source = str(values.get("附件来源") or SOURCE_FORMAL).strip()
            if not filename:
                return None, "请先上传并确认审定附件，未审定不得进入切片环节"
            if source not in (SOURCE_FORMAL, SOURCE_TEMP):
                return None, "附件来源只能是「正式审定」或「临时围挡」"
            current = order.get("审定附件")
            if current and current.get("附件来源") == SOURCE_FORMAL and source == SOURCE_TEMP:
                return None, "正式审定附件已确认，与临时围挡冲突时以正式审定附件为准，不得用围挡资料覆盖"
            if source != SOURCE_FORMAL:
                return None, (
                    "当前附件来自临时围挡，不能作为发布依据；绿线与围挡冲突以正式审定附件为准，"
                    "请补充正式审定附件后再确认"
                )
            order["审定附件"] = {
                "文件名": filename,
                "附件来源": SOURCE_FORMAL,
                "审定结论": str(values.get("审定结论") or "边界与绿线一致，同意发布").strip(),
                "确认人": str(values.get("确认人") or values.get("操作人") or "值班员"),
                "确认时间": _today_plus(0),
            }
            order["阶段"] = STAGE_RESPONSIBILITY
            order["更新时间"] = _today_plus(0)
            return order, "审定附件已确认，下一步核对切片责任"

    def check_slices(self, order_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """审定通过后规划切片：先按正式附件消解围挡冲突，再拆分存量重叠区责任。"""
        order = _find_order_by_id(order_id)
        if order is None:
            return None, f"发布单 {order_id} 不存在"
        with store.key_lock(f"release:{order['版本键']}"):
            if order["阶段"] == STAGE_PUBLISHED:
                return order, "该版本已发布，切片责任随发布结论冻结"
            if order["阶段"] != STAGE_RESPONSIBILITY:
                return None, "必须先确认正式审定附件，才能核对切片责任，不得跳级生成班组任务"
            segments = self._normalize_segments(values.get("边界段落") or order["原始边界段落"])
            if not segments:
                return None, "审定附件未给出可切片的边界段落，无法核对责任"
            resolved = self._resolve_boundary(segments)
            region = _find_region(order["区域编号"])
            history_seed = self._parse_history(region.get("历史修剪区间") if region else "")
            slices: list[dict[str, Any]] = []
            migrations: list[dict[str, Any]] = []
            old_tiles = [
                tile for tile in store.rows(TABLE_TILE)
                if tile.get("区域编号") == order["区域编号"]
                and tile.get("状态") == TILE_ACTIVE
                and tile.get("版本") != order["版本"]
            ]
            for index, seg in enumerate(resolved, start=1):
                code = f"T-{order['区域编号']}-{order['版本']}-{index:02d}"
                splits: list[dict[str, Any]] = []
                inherited_history = [text for text in history_seed
                                     if self._history_overlaps(text, seg["起"], seg["止"])]
                for old in old_tiles:
                    old_start, old_end = old["工作面_起"], old["工作面_止"]
                    overlap_start = max(old_start, seg["起"])
                    overlap_end = min(old_end, seg["止"])
                    if overlap_start < overlap_end:
                        old_crew = old.get("责任班组")
                        # 重叠区迁移前先拆分责任：新区间班组以正式审定附件段落为准
                        if old_crew != seg["班组"]:
                            splits.append({
                                "重叠区间": interval_text(overlap_start, overlap_end),
                                "原责任班组": old_crew,
                                "新责任班组": seg["班组"],
                                "处理": "按正式审定附件拆分责任后迁移",
                            })
                        else:
                            splits.append({
                                "重叠区间": interval_text(overlap_start, overlap_end),
                                "原责任班组": old_crew,
                                "新责任班组": seg["班组"],
                                "处理": "同一班组顺延接管",
                            })
                        migrations.append({
                            "旧切片": old.get("切片编号"),
                            "新切片": code,
                            "重叠区间": interval_text(overlap_start, overlap_end),
                            "原班组": old_crew,
                            "新班组": seg["班组"],
                        })
                        inherited_history.extend(
                            text for text in old.get("历史修剪区间", [])
                            if self._history_overlaps(text, seg["起"], seg["止"])
                        )
                slices.append({
                    "切片编号": code,
                    "起": seg["起"],
                    "止": seg["止"],
                    "工作面": interval_text(seg["起"], seg["止"]),
                    "责任班组": seg["班组"],
                    "边界依据": seg["来源"],
                    "历史修剪区间": sorted(set(inherited_history)),
                    "拆分责任": splits,
                    "状态": "待发布",
                })
            order["切片"] = slices
            order["迁移记录"] = migrations
            order["阶段"] = STAGE_READY
            order["更新时间"] = _today_plus(0)
            unowned = [item["切片编号"] for item in slices if not item["责任班组"]]
            if unowned:
                return None, f"切片 {'、'.join(unowned)} 未落实责任班组，核对未通过"
            return order, f"已按审定附件规划 {len(slices)} 个切片，重叠区责任拆分完成，可以执行发布"

    def publish(self, order_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        order = _find_order_by_id(order_id)
        if order is None:
            return None, f"发布单 {order_id} 不存在"
        key = order["版本键"]
        with store.key_lock(f"release:{key}"):
            if order["阶段"] == STAGE_PUBLISHED:
                # 发布键幂等：并发/断线重放时同一版本只生效一次
                return order, f"发布键 {key} 已生效，直接返回既有发布结论，未重复生成任务"
            if order["阶段"] == STAGE_WITHDRAWN:
                return None, "发布单已撤回，请基于新版本重新发起发布"
            if order["阶段"] != STAGE_READY:
                return None, f"当前阶段为「{order['阶段']}」，审定附件与切片责任未完成，不能发布"
            fail_at = values.get("fail_at_slice")
            fail_at = int(fail_at) if fail_at is not None else None
            slices = order["切片"]
            cursor = int(order["断点游标"])
            for index in range(cursor, len(slices)):
                try:
                    with store.transaction():
                        # 事务可能整体换表，发布单也必须在同一事务内重新取，
                        # 否则回滚后持有的是脱离表的旧对象引用，复位落不回去
                        current_order = _find_order_by_id(order_id)
                        self._commit_slice(current_order, slices[index], seq=index)
                        current_order["断点游标"] = index + 1
                        if index == len(slices) - 1:
                            self._write_back(current_order)
                            current_order["阶段"] = STAGE_PUBLISHED
                            current_order["发布结果"] = {
                                "发布时间": _today_plus(0),
                                "切片数": len(slices),
                                "班组任务数": len(slices),
                                "地图待办数": len(slices),
                                "地图版本": order["版本"],
                                "迁移重叠区": len(order["迁移记录"]),
                            }
                        current_order["更新时间"] = _today_plus(0)
                    order = _find_order_by_id(order_id)
                    if fail_at is not None and index == fail_at:
                        # 演练用：模拟连接在该切片提交后断开；游标已落库，重放可续发
                        return order, f"连接在第 {index + 1} 个切片后断开，该切片已落库，可继续续发"
                except Exception as exc:  # 本批失败：整批已回滚，随后复位整张旧图
                    self._reset_map(order_id)
                    return None, f"第 {index + 1} 个切片落库失败（{exc}），本批已回滚并复位整张旧图，可重新发布"
            order = _find_order_by_id(order_id)
            return order, (
                f"区域切片发布单已发布：{len(slices)} 个切片、班组任务与地图引用同事务落库，"
                f"结论已回写绿化台账、班组清单与地图待办"
            )

    def withdraw(self, order_id: int, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        values = values or {}
        order = _find_order_by_id(order_id)
        if order is None:
            return None, f"发布单 {order_id} 不存在"
        with store.key_lock(f"release:{order['版本键']}"):
            if order["阶段"] == STAGE_WITHDRAWN:
                return order, "发布单已撤回，未重复清理"
            if order["阶段"] != STAGE_PUBLISHED:
                return None, f"当前阶段为「{order['阶段']}」，只有已发布的版本可以撤回"
            with store.transaction():
                self._retract_artifacts(order)
                region = _find_region(order["区域编号"])
                if region is not None:
                    region["已发布版本"] = order["基础版本"] or ""
                    region["地图版本"] = order["基础版本"] or ""
                    region["pending"] = True
                order["阶段"] = STAGE_WITHDRAWN
                order["撤回记录"] = {
                    "撤回人": str(values.get("操作人") or "值班员"),
                    "撤回时间": _today_plus(0),
                    "清理切片数": len(order.get("已发布切片", [])),
                    "恢复旧图版本": order["基础版本"],
                }
                order["更新时间"] = _today_plus(0)
            return order, "发布已撤回：本单切片、班组任务与地图待办全部下线，旧图与旧责任已恢复，无重复作业"

    def _retract_artifacts(self, order: dict[str, Any]) -> None:
        """下线本单全部产物并恢复旧图（撤回与失败复位共用，保证不残留重复作业）。

        只动各张表的行；发布单自身字段由调用方在同一事务内用重新读取的
        order 对象回写，避免持有事务外的旧引用导致回滚后复位丢失。
        """
        codes = set(order.get("已发布切片", []))
        key = order["发布键"]
        for tile in store.rows(TABLE_TILE):
            if tile.get("发布键") == key and tile.get("版本") == order["版本"] and tile.get("状态") == TILE_ACTIVE:
                tile["状态"] = TILE_WITHDRAWN
        for task in store.rows(TABLE_TASK):
            if task.get("发布键") == key and task.get("版本") == order["版本"] and task.get("状态") == TASK_ACTIVE:
                task["状态"] = TASK_WITHDRAWN
            elif task.get("迁移去向") in codes and task.get("状态") == "已迁移":
                task["状态"] = TASK_ACTIVE
                task.pop("迁移去向", None)
        for todo in store.rows(TABLE_TODO):
            if todo.get("发布键") == key and todo.get("版本") == order["版本"] and todo.get("状态") != TODO_WITHDRAWN:
                todo["状态"] = TODO_WITHDRAWN
            elif todo.get("迁移去向") in codes and todo.get("状态") == "已迁移":
                todo["状态"] = TODO_OPEN
                todo.pop("迁移去向", None)
        # 恢复旧图：本单迁出的原始旧切片整段恢复；迁移产生的派生行（重叠碎片/余量）下线
        migrated_source_codes = {
            tile.get("切片编号") for tile in store.rows(TABLE_TILE)
            if tile.get("发布键") == key
            and tile.get("版本") != order["版本"]
            and not tile.get("派生")
            and tile.get("迁移去向") in codes
        }
        for tile in store.rows(TABLE_TILE):
            if tile.get("版本") == order["版本"]:
                continue
            if tile.get("派生") and tile.get("源切片") in migrated_source_codes:
                tile["状态"] = TILE_WITHDRAWN
            elif (not tile.get("派生")
                  and tile.get("迁移去向") in codes
                  and tile.get("状态") == TILE_MIGRATED):
                tile["状态"] = TILE_ACTIVE
                tile.pop("迁移去向", None)
        for slice_item in order.get("切片", []):
            if slice_item.get("状态") == TILE_ACTIVE:
                slice_item["状态"] = "待发布"

    def _reset_map(self, order_id: int) -> None:
        """最终未成功时复位整张旧图：撤掉本单已落库内容、恢复迁走的旧切片。"""
        with store.transaction():
            order = _find_order_by_id(order_id)
            self._retract_artifacts(order)
            order["已发布切片"] = []
            order["断点游标"] = 0
            order["阶段"] = STAGE_READY
            order["更新时间"] = _today_plus(0)

    # ---------- 落库细节 ----------

    def _commit_slice(self, order: dict[str, Any], slice_item: dict[str, Any], *, seq: int) -> None:
        """一个切片对应一条事务：切片 + 班组任务 + 地图引用一起进，一起回滚。"""
        code = slice_item["切片编号"]
        tile = {
            "id": store.next_id(TABLE_TILE),
            "切片编号": code,
            "区域编号": order["区域编号"],
            "版本": order["版本"],
            "发布单号": order["发布单号"],
            "发布键": order["发布键"],
            "工作面_起": slice_item["起"],
            "工作面_止": slice_item["止"],
            "工作面": slice_item["工作面"],
            "责任班组": slice_item["责任班组"],
            "边界依据": slice_item["边界依据"],
            "历史修剪区间": list(slice_item["历史修剪区间"]),
            "状态": TILE_ACTIVE,
        }
        store.rows(TABLE_TILE).append(tile)

        # 班组清单：工作面 + 排班卡，只有发布到这一步才生成（未审定跳不过来）
        task = {
            "id": store.next_id(TABLE_TASK),
            "任务编号": f"C-{order['区域编号']}-{order['版本']}-{seq + 1:02d}",
            "区域编号": order["区域编号"],
            "版本": order["版本"],
            "切片编号": code,
            "发布单号": order["发布单号"],
            "发布键": order["发布键"],
            "责任班组": slice_item["责任班组"],
            "工作面": slice_item["工作面"],
            "历史修剪区间": list(slice_item["历史修剪区间"]),
            "排班卡": [
                {"日期": _today_plus(1 + seq * 2), "班次": "白班", "作业内容": "修剪造型"},
                {"日期": _today_plus(2 + seq * 2), "班次": "夜班", "作业内容": "清运浇水"},
            ],
            "状态": TASK_ACTIVE,
        }
        store.rows(TABLE_TASK).append(task)

        # 地图引用：地图待办挂到同一发布键上
        todo = {
            "id": store.next_id(TABLE_TODO),
            "待办编号": f"M-{order['区域编号']}-{order['版本']}-{seq + 1:02d}",
            "区域编号": order["区域编号"],
            "区域名称": order["区域名称"],
            "版本": order["版本"],
            "切片编号": code,
            "发布单号": order["发布单号"],
            "发布键": order["发布键"],
            "地图引用": f"图层:green/{order['区域编号']}/{code}",
            "工作面": slice_item["工作面"],
            "责任班组": slice_item["责任班组"],
            "状态": TODO_OPEN,
        }
        store.rows(TABLE_TODO).append(todo)

        self._migrate_overlap(order, tile)

        if code not in order["已发布切片"]:
            order["已发布切片"].append(code)
        slice_item["状态"] = TILE_ACTIVE

    def _migrate_overlap(self, order: dict[str, Any], new_tile: dict[str, Any]) -> None:
        """旧切片与新切片重叠的部分迁出。

        原切片保留原始区间、只改状态（撤回时整段恢复）；重叠碎片/非重叠余量
        另立"派生行"。每落一片都基于本版本已落库的全部新切片，对每个旧切片
        重新计算一遍派生行，保证逐片提交（含断线续发）时结果幂等、不出现同一
        区间既生效又迁移的矛盾。
        """
        key = order["发布键"]
        region_code = order["区域编号"]
        new_version = order["版本"]
        committed_new = [
            t for t in store.rows(TABLE_TILE)
            if t.get("区域编号") == region_code
            and t.get("版本") == new_version
            and t.get("状态") == TILE_ACTIVE
        ]

        def subtract(interval: tuple[float, float],
                     covers: list[tuple[float, float]]) -> list[tuple[float, float]]:
            pieces = [interval]
            for cover in covers:
                cut: list[tuple[float, float]] = []
                for a, b in pieces:
                    if a < cover[0]:
                        cut.append((a, min(b, cover[0])))
                    if b > cover[1]:
                        cut.append((max(a, cover[1]), b))
                pieces = [p for p in cut if p[0] < p[1]]
            return pieces

        old_originals = [
            t for t in store.rows(TABLE_TILE)
            if t.get("区域编号") == region_code
            and t.get("版本") != new_version
            and not t.get("派生")
            and t.get("状态") in (TILE_ACTIVE, TILE_MIGRATED)
            and not (t.get("状态") == TILE_MIGRATED and t.get("发布键") != key)
        ]
        for old in old_originals:
            source_code = old.get("源切片", old["切片编号"])
            covers = sorted(
                (max(old["工作面_起"], nt["工作面_起"]), min(old["工作面_止"], nt["工作面_止"]), nt["切片编号"])
                for nt in committed_new
                if max(old["工作面_起"], nt["工作面_起"]) < min(old["工作面_止"], nt["工作面_止"])
            )
            # 清掉本单此前为该旧切片生成的派生行，按最新覆盖情况重建
            for piece in [p for p in store.rows(TABLE_TILE)
                          if p.get("派生") and p.get("源切片") == source_code and p.get("发布键") == key]:
                store.rows(TABLE_TILE).remove(piece)
            cover_ranges = [(a, b) for a, b, _ in covers]
            remaining = subtract((old["工作面_起"], old["工作面_止"]), cover_ranges)
            migrated_codes = [code for _, _, code in covers]

            def make_piece(a: float, b: float, status: str, target: str | None) -> dict[str, Any]:
                piece = dict(old)
                piece.update({
                    "id": store.next_id(TABLE_TILE),
                    "切片编号": f"{source_code}→{target}" if status == TILE_MIGRATED
                               else f"{source_code}余量-{a:.0f}",
                    "工作面_起": a,
                    "工作面_止": b,
                    "工作面": interval_text(a, b),
                    "状态": status,
                    "发布键": key,
                    "派生": True,
                    "源切片": source_code,
                })
                if status == TILE_MIGRATED:
                    piece["迁移去向"] = target
                else:
                    piece.pop("迁移去向", None)
                return piece

            for (a, b), target in zip(cover_ranges, migrated_codes):
                store.rows(TABLE_TILE).append(make_piece(a, b, TILE_MIGRATED, target))
            for a, b in remaining:
                store.rows(TABLE_TILE).append(make_piece(a, b, TILE_ACTIVE, None))

            old["发布键"] = key
            if cover_ranges:
                old["状态"] = TILE_MIGRATED
                old["迁移去向"] = migrated_codes[-1]
                # 旧切片被接管后，它此前生成的班组任务与地图待办同步关闭，
                # 避免地图/班组清单上新旧版本并存造成重复作业
                for task in store.rows(TABLE_TASK):
                    if task.get("切片编号") == source_code and task.get("状态") == TASK_ACTIVE:
                        task["状态"] = "已迁移"
                        task["迁移去向"] = migrated_codes[-1]
                for todo in store.rows(TABLE_TODO):
                    if todo.get("切片编号") == source_code and todo.get("状态") == TODO_OPEN:
                        todo["状态"] = "已迁移"
                        todo["迁移去向"] = migrated_codes[-1]
            else:
                old["状态"] = TILE_ACTIVE
                old.pop("迁移去向", None)

    def _write_back(self, order: dict[str, Any]) -> None:
        """发布结论回写绿化台账；班组清单/地图待办已随切片同事务落库。"""
        region = _find_region(order["区域编号"])
        if region is None:
            raise ReleaseError(f"绿化区域 {order['区域编号']} 已不存在，发布结论无法回写台账")
        region["当前版本"] = order["版本"]
        region["已发布版本"] = order["版本"]
        region["地图版本"] = order["版本"]
        region["审定附件"] = order["审定附件"]["文件名"]
        region["管养状态"] = "正常"
        region["status"] = "正常"
        region["pending"] = False
        region["abnormal"] = False
        region["发布单号"] = order["发布单号"]

    # ---------- 查询视图（列表/详情/地图共用同一口径） ----------

    def list_orders(self, region_code: str | None = None) -> list[dict[str, Any]]:
        orders = store.rows(TABLE_ORDER)
        if region_code:
            orders = [o for o in orders if o.get("区域编号") == region_code]
        return [self.order_view(o) for o in sorted(orders, key=lambda x: int(x["id"]), reverse=True)]

    def get_order(self, order_id: int) -> dict[str, Any] | None:
        order = _find_order_by_id(order_id)
        return self.order_view(order) if order else None

    def order_view(self, order: dict[str, Any]) -> dict[str, Any]:
        view = dict(order)
        view["切片进度"] = {
            "总数": len(order.get("切片", [])),
            "已落库": int(order.get("断点游标", 0)),
            "待落库": max(len(order.get("切片", [])) - int(order.get("断点游标", 0)), 0),
        }
        return view

    def region_view(self, region: dict[str, Any]) -> dict[str, Any]:
        """绿化详情页的发布视图：版本结论直接以发布单为准。"""
        published_version = region.get("已发布版本") or region.get("地图版本") or ""
        candidates = [
            order for order in store.rows(TABLE_ORDER)
            if order.get("区域编号") == region.get("区域编号") and order.get("阶段") != STAGE_WITHDRAWN
        ]
        current_order = max(candidates, key=lambda x: int(x["id"]), default=None)
        tiles = [
            t for t in store.rows(TABLE_TILE)
            if t.get("区域编号") == region.get("区域编号") and t.get("状态") == TILE_ACTIVE
        ]
        tasks = [
            t for t in store.rows(TABLE_TASK)
            if t.get("区域编号") == region.get("区域编号") and t.get("状态") == TASK_ACTIVE
        ]
        todos = [
            t for t in store.rows(TABLE_TODO)
            if t.get("区域编号") == region.get("区域编号") and t.get("状态") == TODO_OPEN
        ]
        return {
            "当前版本": region.get("当前版本"),
            "已发布版本": published_version,
            "地图版本": region.get("地图版本"),
            "版本一致": region.get("当前版本") == published_version == region.get("地图版本") and bool(published_version),
            "发布单": self.order_view(current_order) if current_order else None,
            "生效切片": [self._tile_brief(t) for t in tiles],
            "班组清单": [self._task_brief(t) for t in tasks],
            "地图待办": [self._todo_brief(t) for t in todos],
        }

    def annotate_ledger(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """给绿化台账列表补上统一口径的发布状态，列表页不再自己猜版本。"""
        annotated = []
        for row in rows:
            item = dict(row)
            view = self.region_view(row)
            item["发布状态"] = (
                view["发布单"]["阶段"] if view["发布单"]
                else ("已发布" if view["版本一致"] else "未发起发布")
            )
            item["版本一致"] = view["版本一致"]
            item["待办数"] = len(view["地图待办"])
            annotated.append(item)
        return annotated

    def map_todos(self) -> list[dict[str, Any]]:
        """地图入口：只展示生效待办，每条都带版本键与发布单号，点进去就是同一张发布单。"""
        todos = [t for t in store.rows(TABLE_TODO) if t.get("状态") == TODO_OPEN]
        return [self._todo_brief(t) for t in todos]

    def map_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """地图入口发起发布：与列表页/详情页共用发布单，按版本键取单，不另起炉灶。"""
        region_code = str(values.get("区域编号") or "").strip()
        version = str(values.get("版本") or "").strip()
        if not region_code or not version:
            return None, "地图入口需要指定区域编号与版本"
        key = version_key(region_code, version)
        order = _find_order_by_key(key)
        if order is None:
            order, message = self.open_order(values)
            if order is None:
                return None, message
            return order, f"地图入口已创建发布单 {order['发布单号']}，请按 审定附件→切片责任→发布 推进"
        return order, f"地图入口命中既有发布单 {order['发布单号']}（阶段：{order['阶段']}）"

    # ---------- 边界/区间工具 ------------

    def _normalize_segments(self, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
        segments: list[dict[str, Any]] = []
        for item in raw:
            start, end = parse_meter(item.get("起")), parse_meter(item.get("止"))
            if start >= end:
                continue
            crew = str(item.get("班组") or "").strip()
            if not crew:
                continue
            source = str(item.get("来源") or SOURCE_FORMAL).strip()
            if source not in (SOURCE_FORMAL, SOURCE_TEMP):
                source = SOURCE_FORMAL
            segments.append({"起": start, "止": end, "班组": crew, "来源": source})
        segments.sort(key=lambda s: (s["起"], s["止"]))
        return segments

    def _resolve_boundary(self, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """绿线（正式审定）与临时围挡冲突时以正式审定为准：围挡段落扣掉与正式段重叠部分。"""
        formal = [s for s in segments if s["来源"] == SOURCE_FORMAL]
        if not formal:
            return []
        resolved = [dict(s) for s in formal]
        for temp in (s for s in segments if s["来源"] == SOURCE_TEMP):
            pieces: list[tuple[float, float]] = [(temp["起"], temp["止"])]
            for rule in formal:
                cut: list[tuple[float, float]] = []
                for a, b in pieces:
                    if a < rule["起"]:
                        cut.append((a, min(b, rule["起"])))
                    if b > rule["止"]:
                        cut.append((max(a, rule["止"]), b))
                pieces = [p for p in cut if p[0] < p[1]]
            for a, b in pieces:
                resolved.append({"起": a, "止": b, "班组": temp["班组"], "来源": SOURCE_TEMP})
        resolved.sort(key=lambda s: (s["起"], s["止"]))
        return resolved

    def _parse_history(self, text: Any) -> list[str]:
        if not text:
            return []
        return [part.strip() for part in str(text).split("；") if "~" in part]

    def _history_overlaps(self, text: str, start: float, end: float) -> bool:
        try:
            left, right = text.split("~", 1)
            a, b = parse_meter(left), parse_meter(right)
        except (ValueError, TypeError):
            return False
        return max(a, start) < min(b, end)

    def _tile_brief(self, tile: dict[str, Any]) -> dict[str, Any]:
        return {
            "切片编号": tile["切片编号"],
            "版本": tile["版本"],
            "工作面": tile["工作面"],
            "责任班组": tile["责任班组"],
            "历史修剪区间": tile.get("历史修剪区间", []),
        }

    def _task_brief(self, task: dict[str, Any]) -> dict[str, Any]:
        return {
            "任务编号": task["任务编号"],
            "切片编号": task["切片编号"],
            "版本": task["版本"],
            "责任班组": task["责任班组"],
            "工作面": task["工作面"],
            "排班卡": task.get("排班卡", []),
        }

    def _todo_brief(self, todo: dict[str, Any]) -> dict[str, Any]:
        region = _find_region(todo["区域编号"])
        return {
            "待办编号": todo["待办编号"],
            "区域台账ID": region.get("id") if region else None,
            "区域编号": todo["区域编号"],
            "区域名称": todo.get("区域名称"),
            "版本": todo["版本"],
            "切片编号": todo["切片编号"],
            "发布单号": todo["发布单号"],
            "版本键": version_key(todo["区域编号"], todo["版本"]),
            "地图引用": todo["地图引用"],
            "工作面": todo["工作面"],
            "责任班组": todo["责任班组"],
            "状态": todo["状态"],
        }


release_service = GreenReleaseService()

# 序列化时给前端的阶段推进提示，避免三处入口各写一份文案
STAGE_GUIDE = {
    STAGE_DRAFT: "第一步：确认审定附件（正式审定）",
    STAGE_RESPONSIBILITY: "第二步：核对切片责任与重叠区迁移",
    STAGE_READY: "第三步：执行发布（发布键幂等，可断线续发）",
    STAGE_PUBLISHED: "已发布：结论已回写台账、班组清单与地图待办",
    STAGE_WITHDRAWN: "已撤回：旧图与旧责任已恢复",
}
