"""区域切片发布单端到端验证（内存 TestClient + 并发线程）。"""
from __future__ import annotations

import threading

from fastapi.testclient import TestClient

from app.main import app
from app.services.green_release import GreenReleaseService, release_service
from app.store import store

client = TestClient(app)

passed: list[str] = []
failed: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (passed if cond else failed).append(name)
    print(("PASS " if cond else "FAIL ") + name + (f" :: {detail}" if detail and not cond else ""))


def post(path: str, values: dict | None = None):
    return client.post(path, json={"values": values or {}})


def get(path: str):
    return client.get(path)


# 用 GREE-0002 做完整链路：先发布 v1，再发 v2 触发重叠迁移
REGION = "GREE-0002"

# 1) 建 v1 发布单
r = post("/api/green/release/orders", {"区域编号": REGION, "版本": "v1",
                                       "边界段落": [{"起": "K0+100", "止": "K0+800", "班组": "绿化二班"}]})
check("v1 建单", r.json()["ok"], r.text)
oid_v1 = r.json()["entry"]["id"]

# 2) 未审定先核对切片 → 拒绝（不得跳级）
r = post(f"/api/green/release/orders/{oid_v1}/check-slices")
check("未审定不得切片", not r.json()["ok"] and "审定" in r.json()["message"], r.text)

# 3) 临时围挡附件不能作为依据
r = post(f"/api/green/release/orders/{oid_v1}/confirm-attachment",
         {"文件名": "围挡图.pdf", "附件来源": "临时围挡"})
check("围挡附件被拦", not r.json()["ok"] and "正式审定" in r.json()["message"], r.text)

# 4) 正式审定附件确认
r = post(f"/api/green/release/orders/{oid_v1}/confirm-attachment",
         {"文件名": "GREE-0002-v1-边界审定图.pdf"})
check("正式附件确认", r.json()["ok"] and r.json()["entry"]["阶段"] == "待核对责任", r.text)

# 5) 已审定后再用围挡覆盖 → 拒绝（冲突以正式审定为准）
r = post(f"/api/green/release/orders/{oid_v1}/confirm-attachment",
         {"文件名": "围挡图.pdf", "附件来源": "临时围挡"})
check("正式附件不被围挡覆盖", not r.json()["ok"], r.text)

# 6) 核对切片责任
r = post(f"/api/green/release/orders/{oid_v1}/check-slices")
check("v1 核对切片责任", r.json()["ok"], r.text)
slices_v1 = r.json()["entry"]["切片"]
check("v1 切出 1 片", len(slices_v1) == 1, str(slices_v1))

# 7) 发布前不得有班组任务/地图待办
check("发布前无任务", not [t for t in store.rows("green_crew_task")], "")
check("发布前无待办", not [t for t in store.rows("green_map_todo")], "")

# 8) 发布 v1
r = post(f"/api/green/release/orders/{oid_v1}/publish")
check("v1 发布成功", r.json()["ok"], r.text)
tasks = store.rows("green_crew_task")
todos = store.rows("green_map_todo")
tiles = store.rows("green_map_tile")
check("v1 任务/待办/切片同批落库", len(tasks) == 1 and len(todos) == 1 and len(tiles) == 1,
      f"tasks={len(tasks)} todos={len(todos)} tiles={len(tiles)}")
ledger = store.find("green", 2)
check("v1 结论回写台账", ledger["已发布版本"] == "v1" and ledger["地图版本"] == "v1", str(ledger))

# 9) 重复发布 v1 → 幂等，只生效一次
r = post(f"/api/green/release/orders/{oid_v1}/publish")
check("发布键幂等", r.json()["ok"] and "已生效" in r.json()["message"], r.text)
check("幂等不重复生成", len(store.rows("green_crew_task")) == 1, "")

# 10) 并发发布（已发布单被多线程重放）
messages: list[str] = []
def hit():
    resp = post(f"/api/green/release/orders/{oid_v1}/publish")
    messages.append(resp.json()["message"])
ts = [threading.Thread(target=hit) for _ in range(5)]
[t.start() for t in ts]
[t.join() for t in ts]
check("并发重放全部幂等", all("已生效" in m for m in messages), str(messages))
check("并发后仍只一份", len(store.rows("green_crew_task")) == 1, "")

# 11) 并发对同一版本键建单 → 只建一张
def open_vx(ver, bucket):
    resp = post("/api/green/release/orders", {"区域编号": REGION, "版本": ver,
                                              "边界段落": [{"起": 0, "止": 100, "班组": "绿化二班"}]})
    bucket.append(resp.json()["entry"]["id"])
lock = threading.Lock()
created: list[int] = []
def open_concurrent():
    resp = post("/api/green/release/orders", {"区域编号": REGION, "版本": "vx",
                                              "边界段落": [{"起": 0, "止": 100, "班组": "绿化二班"}]})
    with lock:
        created.append(resp.json()["entry"]["id"])
threads = [threading.Thread(target=open_concurrent) for _ in range(4)]
[t.start() for t in threads]
[t.join() for t in threads]
check("同版本并发建单只生效一次", len(set(created)) == 1, str(created))

# 12) 发 v2：正式绿线 0~500 一班；围挡 400~900 二班 → 冲突以正式为准，围挡只剩 500~900
r = post("/api/green/release/orders", {"区域编号": REGION, "版本": "v2", "基础版本": "v1",
    "边界段落": [
        {"起": "K0+000", "止": "K0+500", "班组": "绿化一班", "来源": "正式审定"},
        {"起": "K0+400", "止": "K0+900", "班组": "绿化二班", "来源": "临时围挡"},
    ]})
check("v2 建单", r.json()["ok"], r.text)
oid_v2 = r.json()["entry"]["id"]
post(f"/api/green/release/orders/{oid_v2}/confirm-attachment", {"文件名": "GREE-0002-v2.pdf"})
r = post(f"/api/green/release/orders/{oid_v2}/check-slices")
check("v2 核对切片", r.json()["ok"], r.text)
slices_v2 = r.json()["entry"]["切片"]
faces = sorted((s["工作面"], s["责任班组"], s["边界依据"]) for s in slices_v2)
check("围挡冲突以正式审定为准(0~500正式,500~900围挡余量)",
      faces == [("K0+000~K0+500", "绿化一班", "正式审定"), ("K0+500~K0+900", "绿化二班", "临时围挡")],
      str(faces))
splits = [sp for s in slices_v2 for sp in s["拆分责任"]]
check("重叠区迁移前先拆分责任(100~500一班/二班拆分,500~800同班组顺延)",
      any("拆分责任" in sp["处理"] and sp["原责任班组"] == "绿化二班" and sp["新责任班组"] == "绿化一班"
          for sp in splits)
      and any(sp["处理"] == "同一班组顺延接管" for sp in splits),
      str(splits))

# 13) 历史修剪区间随重叠保留
seed_hist = store.find("green", 2).get("历史修剪区间")
check("历史修剪区间继承", any(seed_hist in s["历史修剪区间"] for s in slices_v2),
      f"{seed_hist} :: {slices_v2}")

# 14) 发布 v2：旧切片重叠部分迁出、余量以派生行保留
r = post(f"/api/green/release/orders/{oid_v2}/publish")
check("v2 发布", r.json()["ok"], r.text)
active_old_originals = [t for t in store.rows("green_map_tile")
                        if t["版本"] == "v1" and t["状态"] == "生效" and not t.get("派生")]
migrated_overlap = [t for t in store.rows("green_map_tile")
                    if t["版本"] == "v1" and t["状态"] == "已迁移" and t.get("派生")]
# v1 原 100~800 与 v2 的 0~500、500~900 重叠 → 余量为空，整片迁出
check("v1 原整段迁出(地图生效面不再挂旧版)", len(active_old_originals) == 0,
      str([(t["工作面"], t["状态"]) for t in active_old_originals]))
check("迁出碎片覆盖 100~500 与 500~800",
      sorted(t["工作面"] for t in migrated_overlap) == ["K0+100~K0+500", "K0+500~K0+800"],
      str(sorted(t["工作面"] for t in migrated_overlap)))
check("旧图无残留余量派生行",
      not [t for t in store.rows("green_map_tile") if t["版本"] == "v1" and t.get("派生") and t["状态"] == "生效"],
      "")
check("v2 台账地图版本一致", ledger["已发布版本"] == "v2" and ledger["地图版本"] == "v2", str(ledger))

# 15) 地图入口只看到 v2 生效待办
r = get("/api/green/map/todos")
versions = sorted({i["版本"] for i in r.json()["items"] if i["区域编号"] == REGION})
check("地图待办只挂当前版本", versions == ["v2"], str(versions))

# 16) 地图入口命中同一发布单（按版本键）
r = post("/api/green/map/entry", {"区域编号": REGION, "版本": "v2"})
check("地图入口复用发布单", r.json()["ok"] and r.json()["entry"]["id"] == oid_v2, r.text)

# 17) 列表页与详情页口径一致（版本一致标记来自发布单）
r = get("/api/green?keyword=GREE-0002")
row = r.json()["items"][0]
r2 = get("/api/green/2")
check("列表/详情/地图共用版本口径",
      row["已发布版本"] == "v2"
      and r2.json()["发布视图"]["已发布版本"] == "v2"
      and row["版本一致"] is True
      and r2.json()["发布视图"]["版本一致"] is True, "")

# 18) 撤回 v2 → 级联清理、旧图恢复、不留重复
r = post(f"/api/green/release/orders/{oid_v2}/withdraw")
check("撤回成功", r.json()["ok"], r.text)
active_v2 = [t for t in store.rows("green_map_tile") if t["版本"] == "v2" and t["状态"] == "生效"]
restored = [t for t in store.rows("green_map_tile") if t["版本"] == "v1" and t["状态"] == "生效"]
check("撤回后 v2 切片全下线", len(active_v2) == 0, "")
check("撤回后 v1 原整段恢复(单一工作面)",
      len(restored) == 1 and restored[0]["工作面"] == "K0+100~K0+800" and not restored[0].get("派生"),
      str([(t["工作面"], t["状态"], t.get("派生")) for t in restored]))
check("撤回后派生碎片下线(地图无重复作业)",
      not [t for t in store.rows("green_map_tile") if t["版本"] == "v1" and t.get("派生") and t["状态"] == "生效"],
      "")
check("撤回后任务/待办同步下线",
      not [t for t in store.rows("green_crew_task") if t["发布键"] == f"{REGION}@v2" and t["状态"] == "生效"]
      and not [t for t in store.rows("green_map_todo") if t["发布键"] == f"{REGION}@v2" and t["状态"] == "待处理"],
      "")
check("撤回后台账恢复基础版本", ledger["已发布版本"] == "v1" and ledger["地图版本"] == "v1", str(ledger))
# 重复撤回幂等
r = post(f"/api/green/release/orders/{oid_v2}/withdraw")
check("重复撤回不产生重复作业", r.json()["ok"] and "未重复清理" in r.json()["message"], r.text)

# 19) 断线续发：多切片单，发布时在第 1 片后“断开”
r = post("/api/green/release/orders", {"区域编号": "GREE-0003", "版本": "v6",
    "边界段落": [
        {"起": "K3+000", "止": "K3+400", "班组": "绿化一班"},
        {"起": "K3+400", "止": "K3+800", "班组": "绿化二班"},
        {"起": "K3+800", "止": "K4+100", "班组": "绿化一班"},
    ]})
oid_v3 = r.json()["entry"]["id"]
post(f"/api/green/release/orders/{oid_v3}/confirm-attachment", {"文件名": "GREE-0003-v6.pdf"})
post(f"/api/green/release/orders/{oid_v3}/check-slices")
before_tiles = len(store.rows("green_map_tile"))
r = post(f"/api/green/release/orders/{oid_v3}/publish", {"fail_at_slice": 0})
check("连接断开后第1片已落库", "断开" in r.json()["message"] and r.json()["entry"]["断点游标"] == 1, r.text)
check("断点处仅1片落库", len(store.rows("green_map_tile")) == before_tiles + 1, "")
check("断开时台账未提前回写", store.find("green", 3)["地图版本"] != "v6",
      f"实际地图版本={store.find('green', 3)['地图版本']}")
r = post(f"/api/green/release/orders/{oid_v3}/publish")
check("从未发布切片续发成功", r.json()["ok"] and r.json()["entry"]["阶段"] == "已发布", r.text)
check("续发后共3片且无重复", len([t for t in store.rows("green_map_tile")
      if t["发布键"] == "GREE-0003@v6" and t["状态"] == "生效"]) == 3, "")

# 20) 最终失败复位整张旧图：模拟回写台账抛错
orig = GreenReleaseService._write_back
def boom(self, order):
    raise RuntimeError("台账库连接失败")
GreenReleaseService._write_back = boom
try:
    r = post("/api/green/release/orders", {"区域编号": "GREE-0001", "版本": "v4",
        "边界段落": [{"起": "K1+200", "止": "K2+000", "班组": "绿化一班"},
                    {"起": "K2+000", "止": "K2+800", "班组": "绿化二班"}]})
    oid_v4 = r.json()["entry"]["id"]
    post(f"/api/green/release/orders/{oid_v4}/confirm-attachment", {"文件名": "GREE-0001-v4.pdf"})
    post(f"/api/green/release/orders/{oid_v4}/check-slices")
    r = post(f"/api/green/release/orders/{oid_v4}/publish")
    check("最终失败整批回滚", not r.json()["ok"] and "回滚" in r.json()["message"], r.text)
    order = store.find("green_release_order", oid_v4)
    reset_ok = (
        order["阶段"] == "待发布"
        and int(order["断点游标"]) == 0
        and list(order["已发布切片"]) == []
    )
    check("失败后复位旧图/游标归零可重发", reset_ok,
          f"阶段={order['阶段']} 游标={order['断点游标']} 已发布={order['已发布切片']}")
    check("失败半成品不占地图待办",
          not [t for t in store.rows("green_map_todo") if t["发布键"] == "GREE-0001@v4" and t["状态"] == "待处理"],
          "")
finally:
    GreenReleaseService._write_back = orig

# 21) 失败复位后重发可成功
r = post(f"/api/green/release/orders/{oid_v4}/publish")
check("复位后重发成功", r.json()["ok"] and r.json()["entry"]["阶段"] == "已发布", r.text)

# 22) 切片成功但任务落库失败 → 同事务连切片一起回滚（三张表原子性）
import app.services.green_release as gr_mod

r = post("/api/green/release/orders", {"区域编号": "GREE-0003", "版本": "v5",
    "边界段落": [{"起": "K3+000", "止": "K3+500", "班组": "绿化一班"},
                {"起": "K3+500", "止": "K4+000", "班组": "绿化二班"}]})
oid_v5 = r.json()["entry"]["id"]
post(f"/api/green/release/orders/{oid_v5}/confirm-attachment", {"文件名": "GREE-0003-v5.pdf"})
post(f"/api/green/release/orders/{oid_v5}/check-slices")
real_next_id = gr_mod.store.next_id
def flaky_next_id(table: str):
    if table == "green_crew_task":
        raise RuntimeError("班组清单库暂时不可用")
    return real_next_id(table)
gr_mod.store.next_id = flaky_next_id
try:
    tiles_before = len(store.rows("green_map_tile"))
    todos_before = len(store.rows("green_map_todo"))
    r = post(f"/api/green/release/orders/{oid_v5}/publish")
finally:
    gr_mod.store.next_id = real_next_id
check("任务失败导致整批回滚", not r.json()["ok"] and "回滚" in r.json()["message"], r.text)
check("回滚后切片/待办均未落库(三表原子)",
      len(store.rows("green_map_tile")) == tiles_before
      and len(store.rows("green_map_todo")) == todos_before, "")
r = post(f"/api/green/release/orders/{oid_v5}/publish")
check("回滚后可重新发布成功", r.json()["ok"] and r.json()["entry"]["阶段"] == "已发布", r.text)
v5_tiles = [t for t in store.rows("green_map_tile") if t.get("版本") == "v5" and t["状态"] == "生效"]
v5_tasks = [t for t in store.rows("green_crew_task") if t.get("版本") == "v5" and t["状态"] == "生效"]
v5_todos = [t for t in store.rows("green_map_todo") if t.get("版本") == "v5" and t["状态"] == "待处理"]
check("重发后切片/任务/待办数量齐整",
      len(v5_tiles) == 2 and len(v5_tasks) == 2 and len(v5_todos) == 2,
      f"tiles={len(v5_tiles)} tasks={len(v5_tasks)} todos={len(v5_todos)}")
# 迁移后地图生效面不允许任何重叠（v5 两片 + v6 余量 4000~4100 应首尾相接）
active_faces = sorted((t["工作面_起"], t["工作面_止"]) for t in store.rows("green_map_tile")
                      if t.get("区域编号") == "GREE-0003" and t["状态"] == "生效")
no_overlap = all(active_faces[i][1] <= active_faces[i + 1][0] for i in range(len(active_faces) - 1))
check("迁移后地图工作面无重叠/重复作业", no_overlap, str(active_faces))

# 23) 断线续发后再次失败：断线前已落库的切片也必须回滚，旧图整体复位、游标归零
r = post("/api/green/release/orders", {"区域编号": "GREE-0003", "版本": "v7",
    "边界段落": [{"起": "K3+000", "止": "K3+400", "班组": "绿化一班"},
                {"起": "K3+400", "止": "K3+800", "班组": "绿化二班"},
                {"起": "K3+800", "止": "K4+100", "班组": "绿化一班"}]})
oid_v7 = r.json()["entry"]["id"]
post(f"/api/green/release/orders/{oid_v7}/confirm-attachment", {"文件名": "GREE-0003-v7.pdf"})
post(f"/api/green/release/orders/{oid_v7}/check-slices")
post(f"/api/green/release/orders/{oid_v7}/publish", {"fail_at_slice": 0})  # 第1片后断线
order_v7 = store.find("green_release_order", oid_v7)
check("断线后游标=1", int(order_v7["断点游标"]) == 1, str(order_v7["断点游标"]))
# 续发时让台账回写（最后一片）失败
real_write_back = GreenReleaseService._write_back
def boom2(self, order):
    raise RuntimeError("台账库再次不可用")
GreenReleaseService._write_back = boom2
try:
    r = post(f"/api/green/release/orders/{oid_v7}/publish")
finally:
    GreenReleaseService._write_back = real_write_back
check("续发失败整批复位", not r.json()["ok"] and "回滚" in r.json()["message"], r.text)
order_v7 = store.find("green_release_order", oid_v7)
check("复位后游标归零/无已发布切片",
      int(order_v7["断点游标"]) == 0 and list(order_v7["已发布切片"]) == [],
      f"游标={order_v7['断点游标']} 已发布={order_v7['已发布切片']}")
check("断线前第1片也被回滚(无v7生效切片)",
      not [t for t in store.rows("green_map_tile") if t.get("版本") == "v7" and t["状态"] == "生效"], "")
check("v7 无半成品任务/待办",
      not [t for t in store.rows("green_crew_task") if t.get("版本") == "v7" and t["状态"] == "生效"]
      and not [t for t in store.rows("green_map_todo") if t.get("版本") == "v7" and t["状态"] == "待处理"], "")
# 复位后再次续发（不带故障）应成功
r = post(f"/api/green/release/orders/{oid_v7}/publish")
check("复位后再次发布成功", r.json()["ok"] and r.json()["entry"]["阶段"] == "已发布", r.text)

print(f"\n==== {len(passed)} passed, {len(failed)} failed ====")
if failed:
    print("FAILED:", failed)
    raise SystemExit(1)
