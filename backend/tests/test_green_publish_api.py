"""区域切片发布单的接口级冒烟测试：python3 -m unittest tests.test_green_publish_api"""
from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from app.main import app


class GreenPublishApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def _full_publish(self, region: str = "GREE-0001") -> int:
        resp = self.client.post("/api/green/publish-orders", json={"region_code": region})
        self.assertEqual(resp.status_code, 200, resp.text)
        order = resp.json()["entry"]
        order_id = order["id"]
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/confirm-attachment", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        detail = resp.json()
        if region == "GREE-0002":
            overlap = next(s for s in detail["切片"] if s.get("需拆分"))
            resp = self.client.post(
                f"/api/green/publish-orders/{order_id}/slices/split",
                json={"slice_id": overlap["id"], "split_at": 700,
                      "front_crew": "绿化二班", "rear_crew": "绿化三班"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            detail = resp.json()
        for slc in detail["切片"]:
            if slc["发布状态"] == "待核对":
                resp = self.client.post(
                    f"/api/green/publish-orders/{order_id}/slices/review",
                    json={"slice_id": slc["id"]},
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                detail = resp.json()
        self.assertEqual(detail["阶段"], "待发布")
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/publish", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["阶段"], "已发布")
        return order_id

    def test_list_and_detail_routes_not_shadowed(self):
        # /api/green/publish-orders 不能被 /api/green/{entry_id} 抢走
        resp = self.client.get("/api/green/publish-orders")
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/green/regions/map")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("生效引用", resp.json())
        resp = self.client.get("/api/green/regions/GREE-0001/detail")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("审定附件候选", resp.json())

    def test_full_flow_over_http_and_version_consistency(self):
        order_id = self._full_publish("GREE-0001")
        # 列表 / 详情 / 地图三入口版本一致
        listed = self.client.get("/api/green").json()["items"]
        row = next(r for r in listed if r["区域编号"] == "GREE-0001")
        self.assertEqual(row["版本键"], "GREE-0001@v2")
        detail_entry = self.client.get("/api/green/1").json()
        self.assertEqual(detail_entry["版本键"], "GREE-0001@v2")
        map_view = self.client.get("/api/green/regions/map").json()
        map_row = next(r for r in map_view["区域版本"] if r["区域编号"] == "GREE-0001")
        self.assertEqual(map_row["版本键"], "GREE-0001@v2")
        # 地图待办闭环
        todo = next(t for t in map_view["地图待办"] if t["区域编号"] == "GREE-0001")
        self.assertEqual(todo["状态"], "已完成")
        # 发布单查询
        order = self.client.get(f"/api/green/publish-orders/{order_id}").json()
        self.assertEqual(order["审定附件编号"], "附件-G1-v2")

    def test_stage_guard_returns_409(self):
        resp = self.client.post("/api/green/publish-orders", json={"region_code": "GREE-0002"})
        order_id = resp.json()["entry"]["id"]
        # 未审定附件直接发布
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/publish", json={})
        self.assertEqual(resp.status_code, 409)
        # 临时围挡附件不能作为审定结论
        resp = self.client.post(
            f"/api/green/publish-orders/{order_id}/confirm-attachment",
            json={"attachment_no": "附件-G1-v2-围挡"},
        )
        self.assertEqual(resp.status_code, 409)
        # 清理在办单，避免影响后续用例复用判断（撤回未发布单不允许，这里直接确认+发布+撤回）
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/confirm-attachment",
                                json={"attachment_no": "附件-G2-v2"})
        self.assertEqual(resp.status_code, 200, resp.text)
        order = resp.json()
        overlap = next((s for s in order["切片"] if s.get("需拆分")), None)
        if overlap:
            resp = self.client.post(
                f"/api/green/publish-orders/{order_id}/slices/split",
                json={"slice_id": overlap["id"], "split_at": 700,
                      "front_crew": "绿化二班", "rear_crew": "绿化三班"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            order = resp.json()
        for slc in order["切片"]:
            if slc["发布状态"] == "待核对":
                self.client.post(f"/api/green/publish-orders/{order_id}/slices/review",
                                 json={"slice_id": slc["id"]})
        self.client.post(f"/api/green/publish-orders/{order_id}/publish", json={})
        self.client.post(f"/api/green/publish-orders/{order_id}/withdraw", json={})

    def test_rollback_resume_and_withdraw_over_http(self):
        resp = self.client.post("/api/green/publish-orders", json={"region_code": "GREE-0003"})
        order_id = resp.json()["entry"]["id"]
        self.client.post(f"/api/green/publish-orders/{order_id}/confirm-attachment", json={})
        order = self.client.get(f"/api/green/publish-orders/{order_id}").json()
        for slc in order["切片"]:
            r = self.client.post(f"/api/green/publish-orders/{order_id}/slices/review",
                                 json={"slice_id": slc["id"]})
            self.assertEqual(r.status_code, 200, r.text)
        # 分批发布，第二批失败整批回滚
        resp = self.client.post(
            f"/api/green/publish-orders/{order_id}/publish",
            json={"batch_size": 1, "fail_at_slice": "SL-GREE-0003-v2-02"},
        )
        self.assertEqual(resp.status_code, 409)
        # 断线重连续发
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/publish", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["阶段"], "已发布")
        # 重复发布幂等
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/publish", json={})
        self.assertEqual(resp.json()["阶段"], "已发布")
        # 撤回恢复旧图
        resp = self.client.post(f"/api/green/publish-orders/{order_id}/withdraw", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["阶段"], "已撤回")
        map_view = self.client.get("/api/green/regions/map").json()
        active = {r["版本键"] for r in map_view["生效引用"]}
        self.assertIn("GREE-0003@v1", active)
        self.assertNotIn("GREE-0003@v2", active)
        # 撤回幂等
        again = self.client.post(f"/api/green/publish-orders/{order_id}/withdraw", json={})
        self.assertEqual(again.json()["阶段"], "已撤回")


if __name__ == "__main__":
    unittest.main()
