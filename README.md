# 市政道路桥梁养护管理平台

覆盖道路巡查、桥隧定检、路面病害、交安设施、绿化管养、除雪防汛及养护工程管理的市政道桥全要素养护后台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/            每个业务模块一个页面
│   ├── src/api/              统一请求封装
│   ├── src/stores/           会话与筛选状态
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/          每个业务模块一组接口
│   ├── app/services/         业务规则与状态流转
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm install
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 路段管理 | `road_section` | 管养路段 | 路段编号、路段名称、起止桩号 |
| 日常巡查 | `patrol` | 巡查记录 | 巡查编号、巡查路段、巡查日期 |
| 路面病害 | `pavement` | 病害记录 | 病害编号、所属路段、病害类型 |
| 桥梁定检 | `bridge` | 检测记录 | 检测编号、桥梁名称、检测类型 |
| 桥梁档案 | `bridge_info` | 桥梁 | 桥梁编号、桥梁名称、桥型结构 |
| 隧道管养 | `tunnel` | 隧道 | 隧道编号、隧道名称、隧道长度 |
| 交安设施 | `traffic_facility` | 交安设施 | 设施编号、设施类型、所属路段 |
| 排水设施 | `drainage` | 排水设施 | 设施编号、设施类型、所属路段 |
| 绿化管养 | `green` | 绿化区域 | 区域编号、区域名称、植物品种 |
| 路灯照明 | `lighting` | 路灯设施 | 灯具编号、灯具类型、功率 |
| 除雪防滑 | `winter` | 除雪作业 | 作业编号、作业路段、作业日期 |
| 防汛应急 | `flood` | 防汛记录 | 记录编号、预警级别、影响路段 |
| 边坡防护 | `slope` | 边坡 | 边坡编号、所属路段、边坡类型 |
| 伸缩缝管理 | `expansion` | 伸缩缝 | 缝编号、所属桥梁、缝类型 |
| 支座维护 | `bearing` | 桥梁支座 | 支座编号、所属桥梁、支座类型 |
| 养护工程 | `project` | 养护工程 | 工程编号、工程名称、工程类型 |
| 养护车辆 | `vehicle` | 养护车辆 | 车辆编号、车辆类型、车牌号 |
| 养护材料 | `material` | 养护材料 | 材料编号、材料名称、材料类别 |

## 约定

- 每个模块的前端页面在 `frontend/src/views/<模块>/index.vue`，后端接口在
  `backend/app/routers/<模块>.py`，业务规则在 `backend/app/services/<模块>.py`。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 绿化区域切片发布单

绿化边界文件确认后，为避免地图切片、班组工作面/排班卡仍落在旧区域、以及撤回后留下
重复作业，绿化区域的版本发布统一收进「区域切片发布单」
（`backend/app/services/green_release.py`）。列表页、详情页（`views/green/detail.vue`）
与地图入口（`views/green/map.vue`）只读发布单回写后的同一份口径，从三个入口复现的
版本结论保持一致。

发布单向推进，不能跳级：

```
待审定 ──确认审定附件──▶ 待核对责任 ──核对切片责任──▶ 待发布 ──执行发布──▶ 已发布
```

- 未确认正式审定附件不能切片，更不会生成班组任务；
- 绿线与临时围挡冲突时以正式审定附件为准（围挡段扣掉与正式段重叠的余量），
  历史修剪区间随切片继承保留；
- 存量重叠区迁移前先按责任关系拆分：换班组的重叠段记「拆分责任后迁移」，
  同班组的记「顺延接管」；
- 执行发布时，地图切片、班组任务（工作面＋排班卡）、地图引用（地图待办）
  逐片在同一事务内落库，任一步失败整批回滚并复位整张旧图（`store.transaction`）；
- 发布键为 `区域编号@版本`，并发发布按版本键加锁、只生效一次（`store.key_lock`），
  重复提交直接返回既有发布结论，不重复生成任务；
- 连接断开后按发布单上的断点游标从未发布切片继续；最终未成功时游标归零、
  已落库半成品下线，旧图恢复；
- 撤回发布级联下线本单切片/任务/待办，恢复旧版本切片与责任，不留重复作业。

主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/green/release/orders` | 发布单列表（可按 `region_code` 过滤） |
| POST | `/api/green/release/orders` | 创建发布单 |
| GET | `/api/green/release/orders/{id}` | 发布单详情（阶段/切片/迁移/断点） |
| POST | `/api/green/release/orders/{id}/confirm-attachment` | 第一步：确认正式审定附件 |
| POST | `/api/green/release/orders/{id}/check-slices` | 第二步：核对切片责任、拆分重叠区 |
| POST | `/api/green/release/orders/{id}/publish` | 第三步：执行发布（幂等、可续发） |
| POST | `/api/green/release/orders/{id}/withdraw` | 撤回发布并恢复旧图 |
| GET | `/api/green/map/todos` | 地图入口：当前生效版本的切片待办 |
| POST | `/api/green/map/entry` | 地图入口按版本键命中/创建同一张发布单 |

发布规则的端到端验证在 `backend/test_release_flow.py`（含并发幂等、断线续发、
失败复位、事务原子性、撤回恢复等 51 项）：

```bash
cd backend && PYTHONPATH=. python3 test_release_flow.py
```
