"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

发布类业务需要两条额外保证，也在这一层统一提供：

- ``transaction``：整批落库的快照事务，事务内任何一步抛错都整批回滚，
  保证切片、班组任务、地图引用要么一起生效、要么一起不存在；
- ``key_lock``：按业务键（发布单里就是区域版本键）互斥，并发发布同一版本时
  串行化，第二个请求只能看到已发布结果，从而做到同一版本只生效一次。
"""
from __future__ import annotations

import contextlib
import copy
import threading
from typing import Any, Iterator

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._key_locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def next_id(self, module: str) -> int:
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def key_lock(self, key: str) -> threading.RLock:
        """取某个业务键专属的可重入锁；同一把锁在所有请求间共享。"""
        with self._locks_guard:
            return self._key_locks.setdefault(key, threading.RLock())

    @contextlib.contextmanager
    def transaction(self) -> Iterator["Store"]:
        """快照事务：正常退出时改动保留，中途异常时整批回滚到进入前状态。

        回滚会整体替换表字典，因此事务内不要缓存旧的表行引用，需要重新
        通过 ``rows``/``find`` 读取。
        """
        snapshot = copy.deepcopy(self._tables)
        try:
            yield self
        except Exception:
            self._tables = snapshot
            raise

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
