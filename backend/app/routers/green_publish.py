"""区域切片发布单接口。

三段单向推进：确认审定附件 → 核对切片责任（重叠先拆分）→ 执行发布。
列表页、详情页、地图入口都走这里的版本指针与地图视图，杜绝区域版本错位。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.green_publish import PublishError, green_publish_service as publish

router = APIRouter(prefix="/api/green", tags=["区域切片发布单"])


class StartOrderPayload(BaseModel):
    region_code: str = Field(..., description="绿化区域编号，如 GREE-0001")


class AttachmentPayload(BaseModel):
    attachment_no: str | None = Field(default=None, description="审定附件编号；不传时自动选正式审定附件")


class SplitPayload(BaseModel):
    slice_id: int
    split_at: int = Field(..., description="拆分位置（米，距起点绝对桩号）")
    front_crew: str | None = Field(default=None, description="前段责任班组；不传则沿用切片当前班组")
    rear_crew: str = Field(..., description="后段（重叠区）责任班组，迁移前必须先拆分责任")


class SlicePayload(BaseModel):
    slice_id: int


class PublishPayload(BaseModel):
    fail_at_slice: str | None = Field(default=None, description="测试用：让指定切片落库失败，验证整批回滚与续发")
    fail_finalize: bool = Field(default=False, description="测试用：让结论回写失败，验证整批复位旧图")
    batch_size: int | None = Field(default=None, description="测试用：分批提交，验证连接断开后续发")


def _raise(error: PublishError) -> None:
    raise HTTPException(status_code=409, detail=str(error))


@router.get("/regions/map")
def map_view() -> dict[str, Any]:
    """地图入口：生效绿线、班组工作面/排班卡、地图待办与各区域当前版本键。"""
    return publish.map_view()


@router.get("/regions/{region_code}/detail")
def region_detail(region_code: str) -> dict[str, Any]:
    """详情页：台账行、版本指针、审定附件候选、历史修剪区间、在办发布单一处取齐。"""
    try:
        return publish.region_detail(region_code)
    except PublishError as exc:
        _raise(exc)


@router.get("/publish-orders")
def list_orders(
    region_code: str | None = Query(default=None),
    stage: str | None = Query(default=None),
) -> dict[str, Any]:
    """发布单列表，可按区域与阶段过滤。"""
    orders = publish.list_orders(region_code=region_code, stage=stage)
    return {"total": len(orders), "items": orders}


@router.post("/publish-orders", response_model=ActionResult)
def start_order(payload: StartOrderPayload) -> ActionResult:
    """发起区域切片发布单：同一区域只允许一张在办单（并发按版本键只生效一次）。"""
    try:
        order, message, reused = publish.start_order(payload.region_code.strip())
    except PublishError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=message, entry=order)


@router.get("/publish-orders/{order_id}")
def get_order(order_id: int) -> dict[str, Any]:
    """读取发布单（含切片、班组任务、地图引用明细）。"""
    try:
        return publish.get_order(order_id)
    except PublishError as exc:
        _raise(exc)


@router.post("/publish-orders/{order_id}/confirm-attachment")
def confirm_attachment(order_id: int, payload: AttachmentPayload) -> dict[str, Any]:
    """第一步：确认审定附件。绿线与临时围挡冲突时以正式审定附件为准，并保留历史修剪区间。"""
    try:
        return publish.confirm_attachment(order_id, payload.attachment_no)
    except PublishError as exc:
        _raise(exc)


@router.post("/publish-orders/{order_id}/slices/split")
def split_slice(order_id: int, payload: SplitPayload) -> dict[str, Any]:
    """第二步-a：存量重叠区迁移前先拆分责任，拆完再逐片核对。"""
    try:
        return publish.split_slice(
            order_id,
            payload.slice_id,
            payload.split_at,
            payload.front_crew,
            payload.rear_crew,
        )
    except PublishError as exc:
        _raise(exc)


@router.post("/publish-orders/{order_id}/slices/review")
def review_slice(order_id: int, payload: SlicePayload) -> dict[str, Any]:
    """第二步-b：逐片核对责任班组；全部核对完才允许进入发布。"""
    try:
        return publish.review_slice(order_id, payload.slice_id)
    except PublishError as exc:
        _raise(exc)


@router.post("/publish-orders/{order_id}/publish")
def publish_order(order_id: int, payload: PublishPayload) -> dict[str, Any]:
    """第三步：执行发布。

    切片、班组任务、地图引用同一事务落库，失败整批回滚（复位整张旧图）；
    发布键幂等，并发/断线重连从未发布切片继续；结论回写台账、班组清单、地图待办。
    """
    try:
        return publish.publish(
            order_id,
            fail_at_slice=payload.fail_at_slice,
            fail_finalize=payload.fail_finalize,
            batch_size=payload.batch_size,
        )
    except PublishError as exc:
        _raise(exc)


@router.post("/publish-orders/{order_id}/withdraw")
def withdraw_order(order_id: int) -> dict[str, Any]:
    """撤回发布：整事务恢复旧区域，重复撤回幂等，不留下重复作业。"""
    try:
        return publish.withdraw(order_id)
    except PublishError as exc:
        _raise(exc)
