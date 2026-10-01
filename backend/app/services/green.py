"""绿化管养业务规则：状态流转、字段校验与筛选口径都收在这里。

区域版本相关的结论统一委托给 :mod:`app.services.green_release`，
列表页、详情页、地图入口都只读发布单回写后的同一份口径，避免版本错位。
"""
from __future__ import annotations

from typing import Any

from app.services.green_release import release_service
from app.store import store

MODULE = "green"
REQUIRED_FIELDS = ["区域编号", "区域名称", "植物品种"]
STATUS_ORDER = ["正常", "待修剪", "待补植", "病虫害"]
ACTION_RULES = {"安排修剪": "待修剪", "安排补植": "待补植", "病虫防治": "病虫害"}
NEGATIVE_ACTIONS = []


class GreenService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("区域编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        page_rows = rows[start:start + size]
        # 列表页直接用发布单口径补版本/待办，不与详情页、地图各算各的
        return release_service.annotate_ledger(page_rows), total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def region_release_view(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None
        view = dict(entry)
        view["发布视图"] = release_service.region_view(entry)
        return view

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": store.next_id(MODULE)}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"绿化区域 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于绿化管养可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"绿化区域已{action}"
