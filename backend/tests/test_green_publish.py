"""区域切片发布单的业务规则测试：只用标准库，运行：python3 -m unittest -v"""
from __future__ import annotations

import threading
import unittest

from app.services.green_publish import (
    GreenPublishService,
    PublishError,
    SLICE_PENDING,
    SLICE_PUBLISHED,
    SLICE_REVIEWED,
    STAGE_ATTACHMENT,
    STAGE_PUBLISHED,
    STAGE_READY,
    STAGE_SLICE,
    STAGE_WITHDRAWN,
)
from app.store import Store


class PublishFlowTestBase(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例独立仓库 + 独立服务，避免并发锁与数据相互污染
        import app.services.green_publish as gp
        import app.services.green as green_mod

        self.gp = gp
        self.store = Store()
        gp.store = self.store
        green_mod.store = self.store
        self.service = GreenPublishService()

    def _start_order(self, region: str = "GREE-0002"):
        order, _msg, reused = self.service.start_order(region)
        self.assertFalse(reused)
        return order

    def _confirm(self, order_id: int):
        return self.service.confirm_attachment(order_id)

    def _review_all(self, order_id: int, split_plan: dict[int, tuple[int, str, str]] | None = None):
        """把待核对切片全部核对完；遇重叠切片先按 split_plan 拆分。

        split_plan: {切片id: (拆分位置(米), 前段班组或None, 后段班组)}
        """
        for _ in range(10):
            order = self.service.get_order(order_id)
            if order["阶段"] == STAGE_READY:
                return order
            progressed = False
            for slc in list(order["切片"]):
                if slc["发布状态"] != SLICE_PENDING:
                    continue
                if slc.get("需拆分") and slc["id"] in (split_plan or {}):
                    at, front, rear = split_plan[slc["id"]]
                    self.service.split_slice(order_id, slc["id"], at, front, rear)
                    progressed = True
                    break
                if slc.get("责任班组") and not slc.get("需拆分"):
                    self.service.review_slice(order_id, slc["id"])
                    progressed = True
                    break
            if not progressed:
                self.fail(f"发布单 {order_id} 无法推进：{[(s['切片编号'], s['发布状态'], s.get('需拆分')) for s in order['切片']]}")
        return self.service.get_order(order_id)

    def _publish_full(self, region: str = "GREE-0002",
                      split_plan: dict[int, tuple[int, str, str]] | None = None):
        if split_plan is None and region == "GREE-0002":
            # G2 v2 边界 400-720 与 G3 v1 在 700-720 重叠，默认按责任关系拆给绿化三班
            order0 = self._start_order(region)
            self._confirm(order0["id"])
            overlap = next((s for s in self.service.get_order(order0["id"])["切片"] if s.get("需拆分")), None)
            split_plan = {overlap["id"]: (700, "绿化二班", "绿化三班")} if overlap else {}
        else:
            order0 = self._start_order(region)
            self._confirm(order0["id"])
        self._review_all(order0["id"], split_plan)
        return self.service.publish(order0["id"])


class VersionConsistencyTest(PublishFlowTestBase):
    def test_list_detail_map_share_same_version_key(self):
        """列表页、详情页、地图入口对同一区域读到同一版本键。"""
        from app.services.green import GreenService

        items, _ = GreenService().list_entries(page=1, size=20)
        list_row = next(r for r in items if r["区域编号"] == "GREE-0001")
        detail = GreenService().get_entry(1)
        map_view = self.service.map_view()
        map_row = next(r for r in map_view["区域版本"] if r["区域编号"] == "GREE-0001")
        self.assertEqual(list_row["版本键"], "GREE-0001@v1")
        self.assertEqual(detail["版本键"], list_row["版本键"])
        self.assertEqual(map_row["版本键"], list_row["版本键"])
        # 发布后三处入口同步切版本
        self._publish_full("GREE-0001")
        items, _ = GreenService().list_entries(page=1, size=20)
        list_row = next(r for r in items if r["区域编号"] == "GREE-0001")
        detail = GreenService().get_entry(1)
        map_row = next(r for r in self.service.map_view()["区域版本"] if r["区域编号"] == "GREE-0001")
        self.assertEqual(list_row["版本键"], "GREE-0001@v2")
        self.assertEqual(detail["版本键"], "GREE-0001@v2")
        self.assertEqual(map_row["版本键"], "GREE-0001@v2")

    def test_single_active_order_per_region(self):
        first, _msg, reused = self.service.start_order("GREE-0001")
        second, _msg, reused2 = self.service.start_order("GREE-0001")
        self.assertTrue(reused2)
        self.assertEqual(first["id"], second["id"])


class StageGuardTest(PublishFlowTestBase):
    def test_must_confirm_attachment_before_slice_review(self):
        order = self._start_order()
        self.assertEqual(order["阶段"], STAGE_ATTACHMENT)
        with self.assertRaises(PublishError):
            self.service.review_slice(order["id"], 1)

    def test_no_crew_task_before_attachment_confirmed(self):
        order = self._start_order()
        self.assertFalse(any(t.get("发布单编号") == order["发布单编号"]
                             for t in self.store.rows("green_crew_task")))
        with self.assertRaises(PublishError):
            self.service.publish(order["id"])

    def test_cannot_publish_until_all_slices_reviewed(self):
        order = self._start_order()
        self._confirm(order["id"])
        with self.assertRaises(PublishError):
            self.service.publish(order["id"])

    def test_review_rejects_unassigned_slice(self):
        """重叠切片未拆分责任前不允许核对。"""
        order = self._start_order("GREE-0002")
        self._confirm(order["id"])
        overlap_slice = next(s for s in self.service.get_order(order["id"])["切片"] if s.get("需拆分"))
        with self.assertRaises(PublishError):
            self.service.review_slice(order["id"], overlap_slice["id"])

    def test_split_requires_boundary_inside_slice(self):
        order = self._start_order("GREE-0002")
        self._confirm(order["id"])
        overlap_slice = next(s for s in self.service.get_order(order["id"])["切片"] if s.get("需拆分"))
        with self.assertRaises(PublishError):
            self.service.split_slice(order["id"], overlap_slice["id"], 9999, None, "绿化三班")


class AttachmentConflictTest(PublishFlowTestBase):
    def test_formal_attachment_wins_over_fence(self):
        order = self._start_order("GREE-0001")
        confirmed = self._confirm(order["id"])
        self.assertEqual(confirmed["审定附件编号"], "附件-G1-v2")
        self.assertTrue(confirmed["围挡冲突处理"])
        conflict = confirmed["围挡冲突处理"][0]
        self.assertIn("以正式审定附件为准", conflict["处理结论"])
        # 切片只落在正式审定边界 0-400 内
        self.assertEqual(max(s["止点"] for s in confirmed["切片"]), 400)
        self.assertEqual(min(s["起点"] for s in confirmed["切片"]), 0)

    def test_temporary_fence_attachment_rejected(self):
        order = self._start_order("GREE-0001")
        with self.assertRaises(PublishError):
            self.service.confirm_attachment(order["id"], "附件-G1-v2-围挡")

    def test_historical_trim_intervals_carried(self):
        order = self._start_order("GREE-0001")
        confirmed = self._confirm(order["id"])
        carried = [iv for s in confirmed["切片"] for iv in s["历史修剪区间"]]
        self.assertIn("K0+100-K0+200", carried)
        # 发布后历史修剪表原记录仍在
        self._review_all(order["id"])
        self.service.publish(order["id"])
        history = self.store.rows("green_trim_history")
        self.assertTrue(any(h["桩号区间"] == "K0+100-K0+200" for h in history))


class OverlapMigrationTest(PublishFlowTestBase):
    def test_overlap_split_then_migrate_by_responsibility(self):
        """G2 v2(400-720) 压住 G3 v1(700-1000)：先拆分责任，再迁移重叠段。"""
        order = self._start_order("GREE-0002")
        self._confirm(order["id"])
        overlap_slice = next(s for s in self.service.get_order(order["id"])["切片"] if s.get("需拆分"))
        # 700-720 重叠段迁移给绿化三班（与存量 G3 责任关系一致）
        self.service.split_slice(order["id"], overlap_slice["id"], 700, "绿化二班", "绿化三班")
        self._review_all(order["id"])
        published = self.service.publish(order["id"])
        self.assertEqual(published["阶段"], STAGE_PUBLISHED)
        migrations = published["重叠迁移明细"]
        self.assertTrue(migrations)
        map_migration = next(m for m in migrations if m["表"] == "green_map_ref")
        self.assertEqual(map_migration["迁移区间"], "K0+700-K0+720")
        self.assertEqual(map_migration["迁入责任班组"], "绿化三班")
        self.assertEqual(map_migration["迁入版本键"], "GREE-0002@v2")
        # 旧 G3 引用被拆成三段：残留 + 迁移 + 残留
        g3_refs = [r for r in self.store.rows("green_map_ref") if r.get("区域编号") == "GREE-0003"]
        statuses = sorted(r["状态"] for r in g3_refs)
        self.assertIn("已迁移", statuses)
        # 班组清单工作面：三班新增 G2 责任
        crew3 = next(c for c in self.store.rows("green_crew") if c["班组名称"] == "绿化三班")
        self.assertIn("GREE-0002", crew3["负责区域"])


class TransactionAndResumeTest(PublishFlowTestBase):
    def test_failed_batch_resets_whole_old_map(self):
        """批次提交失败时切片、任务、引用整批回滚，旧图保持生效，可续发。"""
        order = self._start_order("GREE-0001")
        self._confirm(order["id"])
        self._review_all(order["id"])
        # 每片一批：第一批发布成功，第二批失败整批回滚
        with self.assertRaises(PublishError):
            self.service.publish(order["id"], fail_at_slice="SL-GREE-0001-v2-02", batch_size=1)
        fresh = self.service.get_order(order["id"])
        self.assertNotEqual(fresh["阶段"], STAGE_PUBLISHED)
        # 第一张切片已随第一批事务落库（续发游标前移）
        published_slice = next(s for s in fresh["切片"] if s["切片编号"] == "SL-GREE-0001-v2-01")
        self.assertEqual(published_slice["发布状态"], SLICE_PUBLISHED)
        pending_slice = next(s for s in fresh["切片"] if s["切片编号"] == "SL-GREE-0001-v2-02")
        self.assertEqual(pending_slice["发布状态"], SLICE_REVIEWED)
        # 第一张切片的任务/引用确实落库且各只有一条
        tasks_01 = [t for t in self.store.rows("green_crew_task")
                    if t["发布单编号"] == order["发布单编号"] and "v2-01" in t["切片编号"]]
        refs_01 = [r for r in self.store.rows("green_map_ref")
                   if r["发布单编号"] == order["发布单编号"] and "v2-01" in r["切片编号"]]
        self.assertEqual(len(tasks_01), 1)
        self.assertEqual(len(refs_01), 1)
        # 第二张切片没有任何任务/引用（整批回滚）
        self.assertFalse(any("v2-02" in t["切片编号"] for t in self.store.rows("green_crew_task")
                             if t.get("发布单编号") == order["发布单编号"]))
        # 旧图保持原状：G1 旧引用仍然生效（新图尚未切换）
        old_active = [r for r in self.store.rows("green_map_ref")
                      if r.get("区域编号") == "GREE-0001" and r["状态"] == "生效中"
                      and r["版本键"] == "GREE-0001@v1"]
        self.assertEqual(len(old_active), 2)

    def test_resume_from_unpublished_slice(self):
        """连接断开后重连：从未发布切片继续，最终完整发布。"""
        order = self._start_order("GREE-0001")
        self._confirm(order["id"])
        self._review_all(order["id"])
        with self.assertRaises(PublishError):
            self.service.publish(order["id"], fail_at_slice="SL-GREE-0001-v2-02", batch_size=1)
        # 重连续发
        published = self.service.publish(order["id"])
        self.assertEqual(published["阶段"], STAGE_PUBLISHED)
        self.assertEqual(published["已发布切片数"], 2)
        # 不产生重复任务/引用
        order_no = order["发布单编号"]
        tasks = [t for t in self.store.rows("green_crew_task") if t.get("发布单编号") == order_no]
        refs = [r for r in self.store.rows("green_map_ref") if r.get("发布单编号") == order_no]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len(refs), 2)

    def test_finalize_failure_resets_entire_batch(self):
        """结论回写阶段失败：最后一批切片连同任务/引用整批回滚，旧图完整保留。"""
        order = self._start_order("GREE-0001")
        self._confirm(order["id"])
        self._review_all(order["id"])
        with self.assertRaises(PublishError):
            self.service.publish(order["id"], fail_finalize=True)
        fresh = self.service.get_order(order["id"])
        self.assertEqual(fresh["阶段"], STAGE_READY)
        # 本单没有任何切片/任务/引用半落库
        order_no = order["发布单编号"]
        self.assertFalse(any(s["发布状态"] == SLICE_PUBLISHED for s in fresh["切片"]))
        self.assertFalse(any(t.get("发布单编号") == order_no for t in self.store.rows("green_crew_task")))
        self.assertFalse(any(r.get("发布单编号") == order_no for r in self.store.rows("green_map_ref")))
        # 旧图全部生效，台账仍是 v1
        old_active = [r for r in self.store.rows("green_map_ref")
                      if r.get("区域编号") == "GREE-0001" and r["状态"] == "生效中"]
        self.assertEqual(len(old_active), 2)
        ledger = next(r for r in self.store.rows("green") if r["区域编号"] == "GREE-0001")
        self.assertEqual(ledger["版本键"], "GREE-0001@v1")
        # 恢复后可以重新发布成功
        published = self.service.publish(order["id"])
        self.assertEqual(published["阶段"], STAGE_PUBLISHED)

    def test_batch_rollback_then_resume_without_duplicates(self):
        order = self._start_order("GREE-0003")
        self._confirm(order["id"])
        self._review_all(order["id"])
        # G3 v2 有两片：720-920、920-1020，第二片失败则该批回滚
        with self.assertRaises(PublishError):
            self.service.publish(order["id"], fail_at_slice="SL-GREE-0003-v2-02", batch_size=1)
        self.service.publish(order["id"], batch_size=1)
        order_no = order["发布单编号"]
        tasks = [t for t in self.store.rows("green_crew_task") if t.get("发布单编号") == order_no]
        refs = [r for r in self.store.rows("green_map_ref") if r.get("发布单编号") == order_no]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len(refs), 2)


class IdempotencyTest(PublishFlowTestBase):
    def test_publish_key_idempotent_under_concurrency(self):
        """并发发布按版本键只生效一次。"""
        order = self._start_order("GREE-0001")
        self._confirm(order["id"])
        self._review_all(order["id"])
        results: list[str] = []
        errors: list[Exception] = []

        def run() -> None:
            try:
                out = self.service.publish(order["id"])
                results.append(out["阶段"])
            except PublishError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertFalse(errors)
        self.assertEqual(results, [STAGE_PUBLISHED] * 8)
        order_no = order["发布单编号"]
        tasks = [t for t in self.store.rows("green_crew_task") if t.get("发布单编号") == order_no]
        refs = [r for r in self.store.rows("green_map_ref") if r.get("发布单编号") == order_no]
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len(refs), 2)

    def test_republish_after_published_is_noop(self):
        published = self._publish_full("GREE-0001")
        again = self.service.publish(published["id"])
        self.assertEqual(again["阶段"], STAGE_PUBLISHED)
        order_no = published["发布单编号"]
        tasks = [t for t in self.store.rows("green_crew_task") if t.get("发布单编号") == order_no]
        self.assertEqual(len(tasks), 2)


class WritebackTest(PublishFlowTestBase):
    def test_conclusion_writeback_to_ledger_crew_todo(self):
        published = self._publish_full("GREE-0001")
        order_no = published["发布单编号"]
        ledger = next(r for r in self.store.rows("green") if r["区域编号"] == "GREE-0001")
        self.assertEqual(ledger["版本键"], "GREE-0001@v2")
        self.assertIn("v2 已发布", ledger["发布结论"])
        self.assertEqual(ledger["最新审定附件"], "附件-G1-v2")
        todo = next(t for t in self.store.rows("green_map_todo") if t["发布单编号"] == order_no)
        self.assertEqual(todo["状态"], "已完成")
        self.assertIn("发布完成", todo["结论"])
        crew = next(c for c in self.store.rows("green_crew") if c["班组名称"] == "绿化一班")
        self.assertEqual(crew["当前版本键"], "GREE-0001@v2")
        self.assertEqual(crew["工作面"], "K0+000-K0+400")

    def test_old_tasks_closed_to_avoid_duplicate_work(self):
        self._publish_full("GREE-0001")
        old_tasks = [t for t in self.store.rows("green_crew_task")
                     if t.get("版本键") == "GREE-0001@v1"]
        self.assertTrue(old_tasks)
        self.assertTrue(all(t["状态"] == "已随版本切换关闭" for t in old_tasks))


class WithdrawTest(PublishFlowTestBase):
    def test_withdraw_restores_old_region_without_duplicates(self):
        published = self._publish_full("GREE-0002", split_plan=None)
        order_id = published["id"]
        order_no = published["发布单编号"]
        withdrawn = self.service.withdraw(order_id)
        self.assertEqual(withdrawn["阶段"], STAGE_WITHDRAWN)
        # 版本指针/台账恢复
        pointer = self.service.pointer("GREE-0002")
        self.assertEqual(pointer["当前版本键"], "GREE-0002@v1")
        ledger = next(r for r in self.store.rows("green") if r["区域编号"] == "GREE-0002")
        self.assertEqual(ledger["版本键"], "GREE-0002@v1")
        # 旧地图引用恢复生效，新引用撤回
        old_refs = [r for r in self.store.rows("green_map_ref")
                    if r.get("版本键") == "GREE-0002@v1"]
        self.assertTrue(all(r["状态"] == "生效中" for r in old_refs))
        new_refs = [r for r in self.store.rows("green_map_ref") if r.get("发布单编号") == order_no]
        self.assertTrue(all(r["状态"] == "已撤回" for r in new_refs))
        # 旧任务恢复排班，新任务撤回
        old_tasks = [t for t in self.store.rows("green_crew_task")
                     if t.get("版本键") == "GREE-0002@v1"]
        self.assertTrue(all(t["状态"] == "已排班" for t in old_tasks))
        new_tasks = [t for t in self.store.rows("green_crew_task") if t.get("发布单编号") == order_no]
        self.assertTrue(all(t["状态"] == "已撤回" for t in new_tasks))
        # 地图待办回到待处理
        todo = next(t for t in self.store.rows("green_map_todo") if t["发布单编号"] == order_no)
        self.assertEqual(todo["状态"], "待处理")
        # 工作面恢复
        crew2 = next(c for c in self.store.rows("green_crew") if c["班组名称"] == "绿化二班")
        self.assertEqual(crew2["工作面"], "K0+400-K0+700")

    def test_withdraw_undoes_migration_and_is_idempotent(self):
        order = self._start_order("GREE-0002")
        self._confirm(order["id"])
        overlap_slice = next(s for s in self.service.get_order(order["id"])["切片"] if s.get("需拆分"))
        self.service.split_slice(order["id"], overlap_slice["id"], 700, "绿化二班", "绿化三班")
        self._review_all(order["id"])
        self.service.publish(order["id"])
        self.service.withdraw(order["id"])
        # 迁移产生的子记录失效，G3 原引用恢复
        g3_old = next(r for r in self.store.rows("green_map_ref")
                      if r["地图引用编号"] == "MAP-v1-006")
        self.assertEqual(g3_old["状态"], "生效中")
        self.assertFalse(any(r.get("状态") == "已迁移"
                             for r in self.store.rows("green_map_ref")))
        # 重复撤回幂等
        again = self.service.withdraw(order["id"])
        self.assertEqual(again["阶段"], STAGE_WITHDRAWN)

    def test_publish_then_withdraw_keeps_map_view_consistent(self):
        """撤回后地图入口不再引用新区域。"""
        published = self._publish_full("GREE-0001")
        self.service.withdraw(published["id"])
        map_view = self.service.map_view()
        active_keys = {r["版本键"] for r in map_view["生效引用"]}
        self.assertNotIn("GREE-0001@v2", active_keys)
        self.assertIn("GREE-0001@v1", active_keys)
        region = next(r for r in map_view["区域版本"] if r["区域编号"] == "GREE-0001")
        self.assertEqual(region["版本键"], "GREE-0001@v1")


if __name__ == "__main__":
    unittest.main()
