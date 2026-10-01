"""区域切片发布单：绿化边界从审定到发布的单向流转。

流程固定为三段，只能逐段向前：
  1. 确认审定附件（绿线与临时围挡冲突时，以正式审定附件为准）
  2. 核对切片责任（存量重叠区迁移前先拆分责任，未审定不得跳级生成班组任务）
  3. 执行发布（切片、班组任务、地图引用同一事务落库；发布键幂等；
     连接断开从未发布切片继续；未成功复位整张旧图；结论回写绿化台账、
     班组清单、地图待办；撤回幂等，不留重复作业）

列表页、详情页、地图入口统一从版本指针取版本键，杜绝区域版本错位。
"""
from __future__ import annotations

import threading
from datetime import datetime
from typing import Any

from app.store import store
from app.services.green_publish_seed import stake

MODULE = "green"
STAGE_ATTACHMENT = "待审定附件"
STAGE_SLICE = "待核对切片"
STAGE_READY = "待发布"
STAGE_PUBLISHING = "发布中"
STAGE_PUBLISHED = "已发布"
STAGE_WITHDRAWN = "已撤回"
ACTIVE_STAGES = {STAGE_ATTACHMENT, STAGE_SLICE, STAGE_READY, STAGE_PUBLISHING}

SLICE_PENDING = "待核对"
SLICE_REVIEWED = "责任已核对"
SLICE_PUBLISHED = "已发布"
SLICE_SPLIT = "已拆分"
SLICE_WITHDRAWN = "已撤回"

SLICE_LENGTH = 200  # 默认每 200 米一片


class PublishError(Exception):
    """业务规则被违反时抛出，由路由层翻译成可读提示。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _next_id(table: str) -> int:
    return max((int(row.get("id", 0)) for row in store.rows(table)), default=0) + 1


def _interval(start: int, end: int) -> str:
    return f"{stake(start)}-{stake(end)}"


def _intervals_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def _intersection(a_start: int, a_end: int, b_start: int, b_end: int) -> tuple[int, int] | None:
    start, end = max(a_start, b_start), min(a_end, b_end)
    return (start, end) if start < end else None


class GreenPublishService:
    def __init__(self) -> None:
        self._order_seq_lock = threading.Lock()
        self._publish_lock_registry: dict[str, threading.Lock] = {}
        self._registry_guard = threading.Lock()

    # ------------------------------------------------------------------
    # 版本视图：列表页 / 详情页 / 地图入口共用同一版本指针
    # ------------------------------------------------------------------
    def pointer(self, region_code: str) -> dict[str, Any] | None:
        for row in store.rows("green_version_pointer"):
            if row.get("区域编号") == region_code:
                return row
        return None

    def decorate_ledger_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """把版本指针上的结论盖到台账行上：三处入口读到的版本键必然一致。"""
        pointer = self.pointer(str(row.get("区域编号", "")))
        if pointer is not None:
            row["区域版本"] = pointer["当前发布版本"]
            row["版本键"] = pointer["当前版本键"]
            row["最新审定附件"] = pointer["最新审定附件"]
            if not row.get("发布结论"):
                row["发布结论"] = f"{pointer['当前发布版本']} 生效中"
            active = self.active_order(str(row.get("区域编号", "")))
            row["在办发布单"] = active["发布单编号"] if active else None
        return row

    def active_order(self, region_code: str) -> dict[str, Any] | None:
        for order in store.rows("green_publish_order"):
            if order.get("区域编号") == region_code and order.get("阶段") in ACTIVE_STAGES:
                return order
        return None

    def region_detail(self, region_code: str) -> dict[str, Any]:
        ledger = next((row for row in store.rows(MODULE) if row.get("区域编号") == region_code), None)
        if ledger is None:
            raise PublishError(f"绿化区域 {region_code} 不存在或已归档")
        pointer = self.pointer(region_code)
        if pointer is None:
            raise PublishError(f"绿化区域 {region_code} 没有版本指针，无法发起切片发布")
        active = self.active_order(region_code)
        history = [h for h in store.rows("green_trim_history") if h.get("区域编号") == region_code]
        return {
            "台账": self.decorate_ledger_row(dict(ledger)),
            "版本指针": pointer,
            "审定附件候选": pointer.get("候选附件", []),
            "在办发布单": self.serialize_order(active) if active else None,
            "历史修剪区间": history,
        }

    def map_view(self) -> dict[str, Any]:
        """地图入口：切片、地图引用、班组工作面、地图待办都按版本键展示。"""
        refs = store.rows("green_map_ref")
        active_refs = [r for r in refs if r.get("状态") in ("生效中",)]
        crews = []
        for crew in store.rows("green_crew"):
            crews.append({
                "班组名称": crew["班组名称"],
                "当前版本键": crew.get("当前版本键"),
                "工作面": crew.get("工作面"),
                "排班卡": crew.get("排班卡"),
                "负责区域": crew.get("负责区域", []),
                "状态": crew.get("状态"),
            })
        todos = [dict(t) for t in store.rows("green_map_todo")]
        regions = [self.decorate_ledger_row(dict(r)) for r in store.rows(MODULE)]
        return {
            "区域版本": [
                {"区域编号": r.get("区域编号"), "区域名称": r.get("区域名称"),
                 "版本键": r.get("版本键"), "区域版本": r.get("区域版本"),
                 "发布结论": r.get("发布结论"), "在办发布单": r.get("在办发布单")}
                for r in regions
            ],
            "生效引用": [dict(r) for r in active_refs],
            "全部引用": [dict(r) for r in refs],
            "班组工作面": crews,
            "地图待办": todos,
        }

    # ------------------------------------------------------------------
    # 发布单查询
    # ------------------------------------------------------------------
    def list_orders(self, region_code: str | None = None, stage: str | None = None) -> list[dict[str, Any]]:
        orders = store.rows("green_publish_order")
        if region_code:
            orders = [o for o in orders if o.get("区域编号") == region_code]
        if stage:
            orders = [o for o in orders if o.get("阶段") == stage]
        return [self.serialize_order(o) for o in orders]

    def get_order(self, order_id: int) -> dict[str, Any]:
        order = store.find("green_publish_order", order_id)
        if order is None:
            raise PublishError(f"区域切片发布单 {order_id} 不存在")
        return self.serialize_order(order)

    def serialize_order(self, order: dict[str, Any]) -> dict[str, Any]:
        slices = [s for s in store.rows("green_slice") if s.get("发布单编号") == order["发布单编号"]]
        tasks = [t for t in store.rows("green_crew_task") if t.get("发布单编号") == order["发布单编号"]]
        refs = [r for r in store.rows("green_map_ref") if r.get("发布单编号") == order["发布单编号"]]
        payload = dict(order)
        payload["切片"] = [dict(s) for s in slices]
        payload["班组任务"] = [dict(t) for t in tasks]
        payload["地图引用"] = [dict(r) for r in refs]
        return payload

    def _find_order(self, order_id: int) -> dict[str, Any]:
        order = store.find("green_publish_order", order_id)
        if order is None:
            raise PublishError(f"区域切片发布单 {order_id} 不存在")
        return order

    def _require_stage(self, order: dict[str, Any], *stages: str) -> None:
        if order.get("阶段") not in stages:
            raise PublishError(
                f"发布单 {order['发布单编号']} 当前阶段为「{order.get('阶段')}」，"
                f"需处于「{'、'.join(stages)}」才能继续，区域版本发布只能单向推进"
            )

    def _find_slice(self, order: dict[str, Any], slice_id: int) -> dict[str, Any]:
        for row in store.rows("green_slice"):
            if int(row.get("id", 0)) == slice_id and row.get("发布单编号") == order["发布单编号"]:
                return row
        raise PublishError(f"切片 {slice_id} 不属于发布单 {order['发布单编号']}")

    # ------------------------------------------------------------------
    # 第一步：发起发布单
    # ------------------------------------------------------------------
    def start_order(self, region_code: str) -> tuple[dict[str, Any], str, bool]:
        """返回 (发布单, 提示, 是否复用在办单)。并发发起只保留一张在办单。"""
        pointer = self.pointer(region_code)
        if pointer is None:
            raise PublishError(f"绿化区域 {region_code} 不存在，无法发起区域切片发布单")
        with self._order_seq_lock:
            existing = self.active_order(region_code)
            if existing is not None:
                return self.serialize_order(existing), f"{region_code} 已有在办发布单 {existing['发布单编号']}，请继续处理", True
            new_id = _next_id("green_publish_order")
            order_no = f"RSP-{new_id:04d}"
            draft_key = f"{region_code}@{pointer['草稿版本']}"
            order = {
                "id": new_id,
                "发布单编号": order_no,
                "区域编号": region_code,
                "版本键": draft_key,
                "发布版本": pointer["草稿版本"],
                "发布键": f"{draft_key}:release",
                "阶段": STAGE_ATTACHMENT,
                "审定附件编号": None,
                "审定边界": None,
                "围挡冲突处理": [],
                "历史修剪区间": [],
                "起点": None,
                "止点": None,
                "已发布切片数": 0,
                "发布结论": None,
                "创建时间": _now(),
                "发布时间": None,
                "撤回时间": None,
                "撤回前快照": None,
                "旧版引用编号": [],
                "旧版任务幂等键": [],
                "重叠迁移明细": [],
                "失败原因": None,
            }
            store.rows("green_publish_order").append(order)
            for todo in store.rows("green_map_todo"):
                if todo.get("区域编号") == region_code and todo.get("状态") == "待处理":
                    todo["发布单编号"] = order_no
            return self.serialize_order(order), f"已创建区域切片发布单 {order_no}，请先确认审定附件", False

    # ------------------------------------------------------------------
    # 第二步：确认审定附件 -> 生成切片
    # ------------------------------------------------------------------
    def confirm_attachment(self, order_id: int, attachment_no: str | None = None) -> dict[str, Any]:
        order = self._find_order(order_id)
        self._require_stage(order, STAGE_ATTACHMENT)
        pointer = self.pointer(order["区域编号"])
        candidates = pointer.get("候选附件", [])

        formal = [c for c in candidates if c.get("附件类型") == "正式审定"]
        chosen = None
        if attachment_no:
            chosen = next((c for c in candidates if c.get("附件编号") == attachment_no), None)
            if chosen is None:
                raise PublishError(f"附件 {attachment_no} 不在 {order['区域编号']} 的审定候选清单里")
            if chosen.get("附件类型") != "正式审定":
                raise PublishError(
                    f"附件 {attachment_no} 是{chosen.get('附件类型')}，绿线与临时围挡冲突时以正式审定附件为准，"
                    f"请选择正式审定附件（{ '、'.join(c['附件编号'] for c in formal) }）"
                )
        else:
            if not formal:
                raise PublishError(f"{order['区域编号']} 缺少正式审定附件，不能确认边界")
            chosen = formal[0]

        start, end = int(chosen["起点"]), int(chosen["止点"])

        # 临时围挡冲突：正式审定边界之外的围挡一律不采纳，冲突段逐条留痕。
        conflicts: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate is chosen or candidate.get("附件类型") == "正式审定":
                continue
            f_start, f_end = int(candidate["起点"]), int(candidate["止点"])
            overlap = _intersection(start, end, f_start, f_end)
            conflicts.append({
                "围挡附件": candidate.get("附件编号"),
                "围挡区间": candidate.get("边界起讫"),
                "正式审定区间": _interval(start, end),
                "冲突区间": _interval(*overlap) if overlap else None,
                "越界区间": _interval(max(f_start, end), f_end) if f_end > end else None,
                "处理结论": "以正式审定附件为准，围挡冲突段与越界段均不进入切片",
            })

        with store.transaction():
            order["审定附件编号"] = chosen["附件编号"]
            order["起点"], order["止点"] = start, end
            order["审定边界"] = _interval(start, end)
            order["围挡冲突处理"] = conflicts
            order["历史修剪区间"] = [
                dict(h) for h in store.rows("green_trim_history")
                if h.get("区域编号") == order["区域编号"] and _intervals_overlap(
                    start, end, int(h["起点"]), int(h["止点"])
                )
            ]
            order["阶段"] = STAGE_SLICE
            self._generate_slices(order, start, end, conflicts)
            pointer["最新审定附件"] = chosen["附件编号"]
            pointer["边界状态"] = f"{order['发布版本']} 边界已按 {chosen['附件编号']} 审定，待核对切片责任"
        return self.get_order(order_id)

    def _crew_for_segment(self, region_code: str, start: int, end: int) -> tuple[str | None, str | None]:
        """按存量责任关系推断切片班组：命中同区域旧切片则沿用，跨区域重叠则留空待拆分。"""
        same_region_owner: str | None = None
        cross_overlap: str | None = None
        for old in store.rows("green_slice"):
            if old.get("发布状态") != SLICE_PUBLISHED:
                continue
            if not _intervals_overlap(start, end, int(old["起点"]), int(old["止点"])):
                continue
            if old.get("区域编号") == region_code:
                same_region_owner = old.get("责任班组")
            else:
                cross_overlap = f"{old.get('区域编号')} {old.get('版本键')} {old.get('桩号区间')}"
        return same_region_owner, cross_overlap

    def _generate_slices(self, order: dict[str, Any], start: int, end: int,
                         conflicts: list[dict[str, Any]]) -> None:
        """按审定边界切片并携带历史修剪区间；未审定附件之前不会生成任何班组任务。"""
        region_code = order["区域编号"]
        order_no = order["发布单编号"]
        version_key = order["版本键"]
        history = store.rows("green_trim_history")
        cursor = start
        index = 0
        while cursor < end:
            index += 1
            seg_end = min(cursor + SLICE_LENGTH, end)
            crew, cross_overlap = self._crew_for_segment(region_code, cursor, seg_end)
            carried = [
                h["桩号区间"] for h in history
                if h.get("区域编号") == region_code
                and _intervals_overlap(cursor, seg_end, int(h["起点"]), int(h["止点"]))
            ]
            fence_conflict = any(
                c["冲突区间"] and _intervals_overlap(
                    cursor, seg_end,
                    *self._parse_conflict(c),
                )
                for c in conflicts
            )
            slice_no = f"SL-{region_code}-{order['发布版本']}-{index:02d}"
            row = {
                "id": _next_id("green_slice"),
                "切片编号": slice_no,
                "发布单编号": order_no,
                "区域编号": region_code,
                "版本键": version_key,
                "起点": cursor,
                "止点": seg_end,
                "桩号区间": _interval(cursor, seg_end),
                "责任班组": crew,
                "发布状态": SLICE_PENDING,
                "历史修剪区间": carried,
                "父切片": None,
                "需拆分": False,
                "拆分说明": None,
                "迁移信息": None,
                "围挡冲突": fence_conflict,
            }
            if cross_overlap:
                row["需拆分"] = True
                row["拆分说明"] = f"与存量 {cross_overlap} 重叠，迁移前先拆分责任"
            store.rows("green_slice").append(row)
            cursor = seg_end

    @staticmethod
    def _parse_conflict(conflict: dict[str, Any]) -> tuple[int, int]:
        text = conflict.get("冲突区间") or ""
        return GreenPublishService._parse_span_text(text)

    @staticmethod
    def _parse_span_text(text: str) -> tuple[int, int]:
        # "K0+300-K0+400" -> (300, 400)
        def one(part: str) -> int:
            part = part.strip().removeprefix("K")
            km, meter = part.split("+")
            return int(km) * 1000 + int(meter)

        left, right = text.split("-")
        return one(left), one(right)

    # ------------------------------------------------------------------
    # 第三步：重叠区先拆分责任，再逐片核对
    # ------------------------------------------------------------------
    def split_slice(self, order_id: int, slice_id: int, split_at: int,
                    front_crew: str | None, rear_crew: str) -> dict[str, Any]:
        order = self._find_order(order_id)
        self._require_stage(order, STAGE_SLICE)
        target = self._find_slice(order, slice_id)
        if target["发布状态"] != SLICE_PENDING:
            raise PublishError(f"切片 {target['切片编号']} 状态为「{target['发布状态']}」，不能再拆分")
        start, end = int(target["起点"]), int(target["止点"])
        if not (start < split_at < end):
            raise PublishError(f"拆分桩号 {stake(split_at)} 不在切片 {target['桩号区间']} 之内")
        if not rear_crew:
            raise PublishError("重叠区迁移前必须先指定后段责任班组")
        front_crew = front_crew or target.get("责任班组")
        if not front_crew:
            raise PublishError("前段切片尚无责任班组，请同时指定前段班组")

        def carried(c_start: int, c_end: int) -> list[str]:
            return [h for h in target.get("历史修剪区间", [])
                    if (lambda span: _intervals_overlap(c_start, c_end, *span))(
                        self._parse_span_text(h))]

        with store.transaction():
            target["发布状态"] = SLICE_SPLIT
            target["需拆分"] = False
            target["拆分说明"] = f"已在 {stake(split_at)} 拆分责任：前段 {front_crew}，后段 {rear_crew}"
            base_no = target["切片编号"]
            children_spec = [
                (start, split_at, front_crew, f"{base_no}-A", None),
                (split_at, end, rear_crew, f"{base_no}-B",
                 "重叠区责任拆分后按责任关系迁移到本发布版本"),
            ]
            for c_start, c_end, crew, no, migration in children_spec:
                store.rows("green_slice").append({
                    "id": _next_id("green_slice"),
                    "切片编号": no,
                    "发布单编号": order["发布单编号"],
                    "区域编号": target["区域编号"],
                    "版本键": target["版本键"],
                    "起点": c_start,
                    "止点": c_end,
                    "桩号区间": _interval(c_start, c_end),
                    "责任班组": crew,
                    "发布状态": SLICE_PENDING,
                    "历史修剪区间": carried(c_start, c_end),
                    "父切片": base_no,
                    "需拆分": False,
                    "拆分说明": None,
                    "迁移信息": migration,
                    "围挡冲突": target.get("围挡冲突", False),
                })
        return self.get_order(order_id)

    def review_slice(self, order_id: int, slice_id: int) -> dict[str, Any]:
        order = self._find_order(order_id)
        self._require_stage(order, STAGE_SLICE)
        target = self._find_slice(order, slice_id)
        if target["发布状态"] != SLICE_PENDING:
            raise PublishError(f"切片 {target['切片编号']} 状态为「{target['发布状态']}」，无需重复核对")
        if target.get("需拆分"):
            raise PublishError(
                f"切片 {target['切片编号']} 存在存量重叠：{target.get('拆分说明')}，"
                "重叠区迁移前先拆分责任，拆分后才能核对"
            )
        if not target.get("责任班组"):
            raise PublishError(f"切片 {target['切片编号']} 还没有责任班组，请先拆分并指派责任")
        with store.transaction():
            target["发布状态"] = SLICE_REVIEWED
            pending = self._effective_slices(order)
            if all(s["发布状态"] == SLICE_REVIEWED for s in pending):
                order["阶段"] = STAGE_READY
                order["失败原因"] = None
        return self.get_order(order_id)

    def _effective_slices(self, order: dict[str, Any]) -> list[dict[str, Any]]:
        """实际参与发布的切片：已拆分的父片由子片顶替。"""
        slices = [s for s in store.rows("green_slice") if s.get("发布单编号") == order["发布单编号"]]
        return [s for s in slices if s["发布状态"] != SLICE_SPLIT]

    # ------------------------------------------------------------------
    # 第四步：执行发布（幂等、可续发、整批事务、失败复位旧图）
    # ------------------------------------------------------------------
    def _publish_lock(self, key: str) -> threading.Lock:
        with self._registry_guard:
            lock = self._publish_lock_registry.get(key)
            if lock is None:
                lock = threading.Lock()
                self._publish_lock_registry[key] = lock
            return lock

    def publish(self, order_id: int, *, fail_at_slice: str | None = None,
                fail_finalize: bool = False, batch_size: int | None = None) -> dict[str, Any]:
        order = self._find_order(order_id)
        lock = self._publish_lock(order["发布键"])
        with lock:
            return self._publish_locked(
                order,
                fail_at_slice=fail_at_slice,
                fail_finalize=fail_finalize,
                batch_size=batch_size,
            )

    def _publish_locked(self, order: dict[str, Any], *, fail_at_slice: str | None,
                        fail_finalize: bool, batch_size: int | None) -> dict[str, Any]:
        if order["阶段"] == STAGE_PUBLISHED:
            # 发布键幂等：并发/断线重连重复请求只生效一次
            return self.serialize_order(order)
        self._require_stage(order, STAGE_READY, STAGE_PUBLISHING)

        effective = self._effective_slices(order)
        if any(s["发布状态"] == SLICE_PENDING for s in effective):
            raise PublishError("还有切片未核对责任，不能发布")
        if any(s.get("需拆分") for s in effective):
            raise PublishError("还有存量重叠切片未拆分责任，不能发布")

        pending = [s for s in effective if s["发布状态"] == SLICE_REVIEWED]
        if batch_size is not None and batch_size <= 0:
            raise PublishError("批量大小必须为正整数")

        order["阶段"] = STAGE_PUBLISHING
        order["失败原因"] = None
        cursor = 0
        total = len(effective)
        while cursor < len(pending):
            chunk = pending[cursor: cursor + batch_size] if batch_size else pending[cursor:]
            try:
                with store.transaction():
                    for slice_row in chunk:
                        if fail_at_slice and slice_row["切片编号"] == fail_at_slice:
                            raise RuntimeError(f"模拟发布中断：切片 {fail_at_slice} 落库失败")
                        self._commit_slice(order, slice_row)
                    cursor += len(chunk)
                    order["已发布切片数"] = total - len(pending) + cursor
                    if cursor >= len(pending):
                        if fail_finalize:
                            raise RuntimeError("模拟发布中断：结论回写失败")
                        self._finalize(order)
            except RuntimeError as exc:
                # 整批回滚后 _tables 已被快照替换，order 局部引用可能已脱离仓库，
                # 重新取回仓库里的发布单再落失败结论；已发布切片数随快照恢复。
                restored = store.find("green_publish_order", int(order["id"]))
                if restored is not None:
                    order = restored
                # 本批回滚：本批切片、任务、地图引用全部不落库，旧图保持原状
                order["阶段"] = STAGE_PUBLISHING if order.get("已发布切片数") else STAGE_READY
                order["失败原因"] = f"发布中断，已整批回滚，可从未发布切片继续：{exc}"
                raise PublishError(order["失败原因"])
        return self.serialize_order(order)

    def _commit_slice(self, order: dict[str, Any], slice_row: dict[str, Any]) -> None:
        """切片 + 班组任务 + 地图引用同一事务落库；幂等键保证重试不产生重复作业。"""
        order_no = order["发布单编号"]
        task_key = f"{order_no}:{slice_row['切片编号']}:task"
        map_key = f"{order_no}:{slice_row['切片编号']}:map"
        existing_tasks = {t["幂等键"] for t in store.rows("green_crew_task")}
        existing_maps = {r["幂等键"] for r in store.rows("green_map_ref")}
        if task_key not in existing_tasks:
            crew = next((c for c in store.rows("green_crew")
                         if c["班组名称"] == slice_row["责任班组"]), None)
            schedule = crew.get("排班卡") if crew else None
            store.rows("green_crew_task").append({
                "id": _next_id("green_crew_task"),
                "任务编号": f"TASK-{order_no}-{slice_row['切片编号']}",
                "发布单编号": order_no,
                "切片编号": slice_row["切片编号"],
                "区域编号": slice_row["区域编号"],
                "版本键": slice_row["版本键"],
                "班组名称": slice_row["责任班组"],
                "工作面": slice_row["桩号区间"],
                "起点": slice_row["起点"],
                "止点": slice_row["止点"],
                "排班卡": schedule,
                "状态": "已排班",
                "幂等键": task_key,
            })
        if map_key not in existing_maps:
            store.rows("green_map_ref").append({
                "id": _next_id("green_map_ref"),
                "地图引用编号": f"MAP-{order_no}-{slice_row['切片编号']}",
                "发布单编号": order_no,
                "切片编号": slice_row["切片编号"],
                "区域编号": slice_row["区域编号"],
                "版本键": slice_row["版本键"],
                "起点": slice_row["起点"],
                "止点": slice_row["止点"],
                "桩号区间": slice_row["桩号区间"],
                "引用类型": "绿线",
                "状态": "生效中",
                "替换自": None,
                "幂等键": map_key,
            })
        slice_row["发布状态"] = SLICE_PUBLISHED

    def _finalize(self, order: dict[str, Any]) -> None:
        """最后一批事务内切换旧图、迁移重叠区、回写台账/班组/待办，失败则整批复位旧图。"""
        order_no = order["发布单编号"]
        region_code = order["区域编号"]
        new_refs = [r for r in store.rows("green_map_ref") if r.get("发布单编号") == order_no]

        old_ref_ids: list[Any] = []
        # 1) 旧版本地图引用全部切为「被替换」；旧版未撤回任务同步关闭，避免重复作业
        #    （复位动作只在事务失败时由快照完成）
        for ref in store.rows("green_map_ref"):
            if ref.get("区域编号") == region_code and ref.get("状态") == "生效中" \
                    and ref.get("版本键") != order["版本键"]:
                ref["状态"] = "被替换"
                new_ref = next((n for n in new_refs if _intervals_overlap(
                    int(n["起点"]), int(n["止点"]), int(ref["起点"]), int(ref["止点"]))), None)
                if new_ref:
                    ref["替换自"] = new_ref["地图引用编号"]
                old_ref_ids.append(ref["id"])
        old_task_keys: list[str] = []
        for task in store.rows("green_crew_task"):
            if task.get("区域编号") == region_code and task.get("状态") == "已排班" \
                    and task.get("版本键") != order["版本键"]:
                task["状态"] = "已随版本切换关闭"
                old_task_keys.append(task["幂等键"])

        # 2) 存量重叠区：先拆分责任（已在切片阶段完成），这里按责任关系迁移旧作业
        migrations = self._migrate_overlaps(order, new_refs)

        # 3) 版本指针 + 绿化台账结论回写
        pointer = self.pointer(region_code)
        snapshot = {
            "当前发布版本": pointer["当前发布版本"],
            "当前版本键": pointer["当前版本键"],
            "最新审定附件": pointer["最新审定附件"],
            "边界状态": pointer["边界状态"],
        }
        pointer["当前发布版本"] = order["发布版本"]
        pointer["当前版本键"] = order["版本键"]
        pointer["最新审定附件"] = order["审定附件编号"]
        pointer["边界状态"] = f"{order['发布版本']} 已发布"

        ledger = next((r for r in store.rows(MODULE) if r.get("区域编号") == region_code), None)
        crew_snapshot: dict[str, dict[str, Any]] = {}
        if ledger is not None:
            snapshot["台账"] = {
                "区域版本": ledger.get("区域版本"),
                "版本键": ledger.get("版本键"),
                "最新审定附件": ledger.get("最新审定附件"),
                "边界起讫": ledger.get("边界起讫"),
                "发布结论": ledger.get("发布结论"),
                "管养班组": ledger.get("管养班组"),
            }
            ledger["区域版本"] = order["发布版本"]
            ledger["版本键"] = order["版本键"]
            ledger["最新审定附件"] = order["审定附件编号"]
            ledger["边界起讫"] = order["审定边界"]

        # 4) 班组清单回写：工作面 = 本单新切片 ∪ 该班组仍承担的其他区域生效引用；
        #    排班卡随新任务落位。迁移失段后自动收缩，新接管段自动并入。
        own_slices = [s for s in self._effective_slices(order) if s["发布状态"] == SLICE_PUBLISHED]
        crews_in_order = sorted({s["责任班组"] for s in own_slices})
        for crew in store.rows("green_crew"):
            if crew["班组名称"] not in crews_in_order:
                continue
            crew_snapshot[crew["班组名称"]] = {
                "当前版本键": crew.get("当前版本键"),
                "工作面": crew.get("工作面"),
                "负责区域": list(crew.get("负责区域", [])),
            }
            spans = [(int(s["起点"]), int(s["止点"])) for s in own_slices
                     if s["责任班组"] == crew["班组名称"]]
            # 并入该班组名下、且未被本次发布覆盖的旧区域生效引用（残留段）
            touched_codes = {region_code}
            for ref in store.rows("green_map_ref"):
                if ref.get("状态") != "生效中":
                    continue
                ref_code = str(ref.get("版本键", "")).split("@")[0]
                if ref.get("迁入责任班组") == crew["班组名称"]:
                    spans.append((int(ref["起点"]), int(ref["止点"])))
                elif ref_code in crew.get("负责区域", []) and ref_code not in touched_codes \
                        and ref.get("迁入责任班组") is None:
                    spans.append((int(ref["起点"]), int(ref["止点"])))
            if spans:
                crew["工作面"] = _interval(min(a for a, _ in spans), max(b for _, b in spans))
            crew["当前版本键"] = order["版本键"]
            if region_code not in crew.setdefault("负责区域", []):
                crew["负责区域"].append(region_code)
        self._apply_crew_surface_after_migration(crew_snapshot)

        if ledger is not None:
            ledger["管养班组"] = "、".join(crews_in_order)
            conclusion = f"{order['发布版本']} 已发布（{order['审定附件编号']}，{order['审定边界']}）"
            if migrations:
                conclusion += f"；迁移存量重叠区 {len(migrations)} 处"
            if order["围挡冲突处理"]:
                conclusion += "；围挡冲突以正式审定附件为准"
            ledger["发布结论"] = conclusion

        # 5) 地图待办闭环
        for todo in store.rows("green_map_todo"):
            if todo.get("区域编号") == region_code and todo.get("发布单编号") == order_no:
                todo["状态"] = "已完成"
                todo["结论"] = f"{order_no} 发布完成，地图已切换 {order['版本键']}"

        order["阶段"] = STAGE_PUBLISHED
        order["发布时间"] = _now()
        order["旧版引用编号"] = old_ref_ids
        order["旧版任务幂等键"] = old_task_keys
        order["重叠迁移明细"] = migrations
        order["发布结论"] = ledger["发布结论"] if ledger is not None else f"{order['发布版本']} 已发布"
        order["撤回前快照"] = {
            "版本指针": snapshot,
            "班组": crew_snapshot,
        }

    def _migrate_overlaps(self, order: dict[str, Any],
                          new_refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """新边界压住其他区域存量作业时，把旧切片/任务/地图引用按重叠段拆开并迁移。"""
        migrations: list[dict[str, Any]] = []
        for new_ref in list(new_refs):
            n_start, n_end = int(new_ref["起点"]), int(new_ref["止点"])
            target_slice = next((s for s in self._effective_slices(order)
                                 if s["切片编号"] == new_ref["切片编号"]), None)
            for table, status_field in (
                ("green_map_ref", "状态"),
                ("green_slice", "发布状态"),
                ("green_crew_task", "状态"),
            ):
                for old in list(store.rows(table)):
                    if old.get("发布单编号") == order["发布单编号"]:
                        continue
                    if old.get("区域编号") == order["区域编号"]:
                        continue
                    if old.get(status_field) not in ("生效中", SLICE_PUBLISHED, "已排班"):
                        continue
                    if not _intervals_overlap(n_start, n_end, int(old["起点"]), int(old["止点"])):
                        continue
                    overlap = _intersection(n_start, n_end, int(old["起点"]), int(old["止点"]))
                    assert overlap is not None
                    migrations.append(self._split_old_row(
                        table, old, overlap, order, target_slice, status_field))
        return migrations

    def _split_old_row(self, table: str, old: dict[str, Any], overlap: tuple[int, int],
                       order: dict[str, Any], target_slice: dict[str, Any] | None,
                       status_field: str) -> dict[str, Any]:
        o_start, o_end = int(old["起点"]), int(old["止点"])
        x_start, x_end = overlap
        old[status_field] = "已被拆分"
        old_code = old.get("地图引用编号") or old.get("切片编号") or old.get("任务编号")
        pieces = []
        residue_index = 0
        for p_start, p_end, migrated in (
            (o_start, x_start, False),
            (x_start, x_end, True),
            (x_end, o_end, False),
        ):
            if p_start >= p_end:
                continue
            child = dict(old)
            child["id"] = _next_id(table)
            child["起点"], child["止点"] = p_start, p_end
            child["桩号区间"] = _interval(p_start, p_end)
            child["父记录"] = old_code
            child["迁移来源"] = None
            child["迁移到"] = None
            child.pop("迁入责任班组", None)
            if migrated:
                suffix, new_status = "迁移", "已迁移"
            else:
                residue_index += 1
                suffix, new_status = f"残留{residue_index}", None
            if "地图引用编号" in child:
                child["地图引用编号"] = f"{old_code}-{suffix}"
                child["幂等键"] = f"{child['幂等键']}:{p_start}-{p_end}"
                child["状态"] = "生效中" if new_status is None else new_status
            elif "切片编号" in child:
                child["切片编号"] = f"{old_code}-{suffix}"
                child["发布状态"] = SLICE_PUBLISHED if new_status is None else new_status
            else:
                child["任务编号"] = f"{old_code}-{suffix}"
                child["幂等键"] = f"{child['幂等键']}:{p_start}-{p_end}"
                child["状态"] = "已排班" if new_status is None else new_status
            if migrated:
                child["迁移到"] = order["版本键"]
                child["迁移来源"] = old.get("版本键")
                if target_slice is not None:
                    child["迁入责任班组"] = target_slice.get("责任班组")
                    if table == "green_crew_task":
                        child["班组名称"] = target_slice.get("责任班组")
                        child["工作面"] = _interval(p_start, p_end)
            pieces.append(child)
            store.rows(table).append(child)
        return {
            "表": table,
            "原记录": old_code,
            "原版本键": old.get("版本键"),
            "原责任班组": old.get("责任班组") or old.get("班组名称"),
            "迁移区间": _interval(x_start, x_end),
            "迁入版本键": order["版本键"],
            "迁入责任班组": target_slice.get("责任班组") if target_slice else None,
            "拆出记录": [p.get("地图引用编号") or p.get("切片编号") or p.get("任务编号") for p in pieces],
        }

    def _apply_crew_surface_after_migration(self, touched: dict[str, dict[str, Any]]) -> None:
        """迁移后按仍然生效的引用重算受影响班组工作面（失段收缩）。"""
        touched_codes = set()
        for crew in store.rows("green_crew"):
            if crew["班组名称"] in touched:
                touched_codes.update(crew.get("负责区域", []))
        for crew in store.rows("green_crew"):
            if crew["班组名称"] in touched or not crew.get("当前版本键", "").endswith("@v1"):
                continue
            own_codes = [code for code in crew.get("负责区域", []) if code not in touched_codes]
            spans = [
                (int(r["起点"]), int(r["止点"]))
                for r in store.rows("green_map_ref")
                if r.get("状态") == "生效中"
                and r.get("迁入责任班组") is None
                and r.get("版本键") in [f"{code}@v1" for code in own_codes]
            ]
            if spans:
                crew["工作面"] = _interval(min(a for a, _ in spans), max(b for _, b in spans))

    # ------------------------------------------------------------------
    # 撤回：整事务恢复旧区域，幂等不产生重复作业
    # ------------------------------------------------------------------
    def withdraw(self, order_id: int) -> dict[str, Any]:
        order = self._find_order(order_id)
        lock = self._publish_lock(order["发布键"])
        with lock:
            if order["阶段"] == STAGE_WITHDRAWN:
                return self.serialize_order(order)
            self._require_stage(order, STAGE_PUBLISHED)
            with store.transaction():
                self._withdraw_locked(order)
        return self.serialize_order(order)

    def _withdraw_locked(self, order: dict[str, Any]) -> None:
        order_no = order["发布单编号"]
        region_code = order["区域编号"]

        # 本单新切片/任务/地图引用全部撤回
        for slice_row in store.rows("green_slice"):
            if slice_row.get("发布单编号") == order_no and slice_row["发布状态"] == SLICE_PUBLISHED:
                slice_row["发布状态"] = SLICE_WITHDRAWN
        for task in store.rows("green_crew_task"):
            if task.get("发布单编号") == order_no and task.get("状态") == "已排班":
                task["状态"] = "已撤回"
        for ref in store.rows("green_map_ref"):
            if ref.get("发布单编号") == order_no and ref.get("状态") == "生效中":
                ref["状态"] = "已撤回"

        # 迁移产物失效：只处理本单迁移明细里的原记录及其拆分子记录，避免误伤其他发布单
        migration_codes = {
            (m["表"], m["原记录"]) for m in order.get("重叠迁移明细", [])
        }
        affected_parents: dict[str, set[str]] = {"green_map_ref": set(), "green_slice": set(),
                                                 "green_crew_task": set()}
        for table, code in migration_codes:
            affected_parents[table].add(code)

        for table, status_field in (
            ("green_map_ref", "状态"),
            ("green_slice", "发布状态"),
            ("green_crew_task", "状态"),
        ):
            parents = affected_parents[table]
            active_value = "生效中" if table == "green_map_ref" else (
                SLICE_PUBLISHED if table == "green_slice" else "已排班")
            for row in store.rows(table):
                code = row.get("地图引用编号") or row.get("切片编号") or row.get("任务编号")
                # 迁移拆出来的子记录（残留段 + 迁移段）整体作废
                if row.get("父记录") in parents:
                    row[status_field] = "失效"
                    continue
                # 被拆分的原始旧记录恢复生效
                if code in parents and row.get(status_field) == "已被拆分":
                    row[status_field] = active_value
                    row.pop("替换自", None)

        # 本区域旧版本引用与任务恢复，撤回幂等不产生重复作业
        for ref in store.rows("green_map_ref"):
            if ref.get("区域编号") == region_code and ref.get("状态") == "被替换" \
                    and ref.get("版本键") != order["版本键"]:
                ref["状态"] = "生效中"
                ref.pop("替换自", None)
        closed_task_keys = set(order.get("旧版任务幂等键", []))
        for task in store.rows("green_crew_task"):
            if task.get("幂等键") in closed_task_keys and task.get("状态") == "已随版本切换关闭":
                task["状态"] = "已排班"

        # 版本指针 / 台账 / 班组 / 待办按撤回前快照回写
        snapshot = order.get("撤回前快照") or {}
        pointer = self.pointer(region_code)
        if pointer is not None and "版本指针" in snapshot:
            for key, value in snapshot["版本指针"].items():
                pointer[key] = value
            pointer["边界状态"] = f"{order['发布版本']} 已撤回，恢复 {snapshot['版本指针']['当前版本键']}"
        ledger = next((r for r in store.rows(MODULE) if r.get("区域编号") == region_code), None)
        if ledger is not None:
            old_ledger = snapshot.get("版本指针", {})
            ledger["区域版本"] = old_ledger.get("当前发布版本", "v1")
            ledger["版本键"] = old_ledger.get("当前版本键", f"{region_code}@v1")
            ledger["最新审定附件"] = old_ledger.get("最新审定附件")
            saved_ledger = snapshot.get("台账") or {}
            ledger["边界起讫"] = saved_ledger.get("边界起讫")
            ledger["管养班组"] = saved_ledger.get("管养班组")
            ledger["发布结论"] = f"发布单 {order_no} 已撤回，{ledger['版本键']} 旧区域恢复生效，不保留重复作业"
        for crew in store.rows("green_crew"):
            saved = (snapshot.get("班组") or {}).get(crew["班组名称"])
            if saved:
                crew["当前版本键"] = saved["当前版本键"]
                crew["工作面"] = saved["工作面"]
                crew["负责区域"] = saved["负责区域"]
        for todo in store.rows("green_map_todo"):
            if todo.get("区域编号") == region_code and todo.get("发布单编号") == order_no:
                todo["状态"] = "待处理"
                todo["结论"] = None

        order["阶段"] = STAGE_WITHDRAWN
        order["撤回时间"] = _now()
        order["发布结论"] = ledger["发布结论"] if ledger is not None else "发布已撤回"


green_publish_service = GreenPublishService()
