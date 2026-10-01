"""绿化管养接口：维护绿化区域，覆盖安排修剪、安排补植、病虫防治等动作。

区域版本发布统一走「区域切片发布单」相关接口：列表页、详情页、地图入口
都只操作发布单，后端按版本键保证同一版本只生效一次。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.green import GreenService
from app.services.green_release import STAGE_GUIDE, release_service

router = APIRouter(prefix="/api/green", tags=["绿化管养"])

service = GreenService()

LIST_FIELDS = ["区域编号", "区域名称", "植物品种", "面积", "上次修剪", "上次浇水", "管养班组", "管养状态"]
STATUSES = ["正常", "待修剪", "待补植", "病虫害"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按区域编号检索"),
    status: str | None = Query(default=None, description="正常、待修剪、待补植、病虫害"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按区域编号与状态过滤绿化管养列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出绿化管养清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "green", "total": total, "items": items}


# ---------- 区域切片发布单（放在 /{entry_id} 之前，避免被参数路由截获） ----------


@router.get("/release/orders", response_model=dict)
def list_release_orders(
    region_code: str | None = Query(default=None, description="可选：只看某个区域的发布单"),
) -> dict[str, Any]:
    """发布单列表：三个入口共用，可按区域过滤。"""
    orders = release_service.list_orders(region_code=region_code)
    return {"items": orders, "total": len(orders)}


@router.get("/map/todos", response_model=dict)
def map_todos() -> dict[str, Any]:
    """地图入口待办：只返回生效中的切片引用，撤回/失败的不占地图。"""
    items = release_service.map_todos()
    return {"items": items, "total": len(items)}


@router.post("/map/entry", response_model=ActionResult)
def map_entry(payload: EntryPayload) -> ActionResult:
    """地图入口发起发布：按区域+版本键命中同一张发布单，不与列表/详情各建一份。"""
    order, message = release_service.map_entry(payload.values)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release_service.order_view(order))


@router.post("/release/orders", response_model=ActionResult)
def open_release_order(payload: EntryPayload) -> ActionResult:
    """创建区域切片发布单（第一步前置：建单后必须先确认审定附件）。"""
    order, message = release_service.open_order(payload.values)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release_service.order_view(order))


@router.get("/release/orders/{order_id}", response_model=dict)
def get_release_order(order_id: int) -> dict[str, Any]:
    """发布单详情：阶段、切片、迁移记录、断点进度都在这里。"""
    order = release_service.get_order(order_id)
    if order is None:
        raise HTTPException(status_code=404, detail=f"发布单 {order_id} 不存在")
    return {"order": order, "stage_guide": STAGE_GUIDE.get(order.get("阶段"), "")}


@router.post("/release/orders/{order_id}/confirm-attachment", response_model=ActionResult)
def confirm_attachment(order_id: int, payload: EntryPayload) -> ActionResult:
    """第一步：确认正式审定附件；与临时围挡冲突时以正式审定为准。"""
    order, message = release_service.confirm_attachment(order_id, payload.values)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release_service.order_view(order))


@router.post("/release/orders/{order_id}/check-slices", response_model=ActionResult)
def check_slices(order_id: int, payload: EntryPayload) -> ActionResult:
    """第二步：核对切片责任，规划切片并拆分存量重叠区责任。"""
    order, message = release_service.check_slices(order_id, payload.values)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release_service.order_view(order))


@router.post("/release/orders/{order_id}/publish", response_model=ActionResult)
def publish(order_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """第三步：执行发布。切片/班组任务/地图待办同事务落库；发布键幂等，支持断线续发。"""
    values = payload.values if payload is not None else {}
    order, message = release_service.publish(order_id, values)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release_service.order_view(order))


@router.post("/release/orders/{order_id}/withdraw", response_model=ActionResult)
def withdraw(order_id: int, payload: EntryPayload | None = None) -> ActionResult:
    """撤回发布：级联下线本单切片/任务/待办并恢复旧图，不留重复作业。"""
    values = payload.values if payload is not None else {}
    order, message = release_service.withdraw(order_id, values)
    if order is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=release_service.order_view(order))


# ---------- 绿化区域台账 ----------


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条绿化区域明细（含统一口径的发布视图）；不存在时给出可读的错误说明。"""
    entry = service.region_release_view(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"绿化区域 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条绿化区域，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="绿化区域已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条绿化区域执行安排修剪、安排补植、病虫防治；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
