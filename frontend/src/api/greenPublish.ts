/** 区域切片发布单接口封装：列表页、详情页、地图入口共用同一套调用。 */
import { request } from '@/api/client'

export type Slice = {
  id: number
  切片编号: string
  发布单编号: string
  区域编号: string
  版本键: string
  起点: number
  止点: number
  桩号区间: string
  责任班组: string | null
  发布状态: string
  历史修剪区间: string[]
  父切片: string | null
  需拆分: boolean
  拆分说明: string | null
  迁移信息: string | null
  围挡冲突?: boolean
}

export type PublishOrder = {
  id: number
  发布单编号: string
  区域编号: string
  版本键: string
  发布版本: string
  阶段: string
  审定附件编号: string | null
  审定边界: string | null
  围挡冲突处理: Record<string, unknown>[]
  历史修剪区间: { 桩号区间: string }[]
  已发布切片数: number
  发布结论: string | null
  创建时间: string
  发布时间: string | null
  撤回时间: string | null
  失败原因: string | null
  切片: Slice[]
  班组任务: Record<string, unknown>[]
  地图引用: Record<string, unknown>[]
  重叠迁移明细: Record<string, unknown>[]
}

export type RegionDetail = {
  台账: Record<string, string | number | null>
  版本指针: {
    区域编号: string
    当前发布版本: string
    当前版本键: string
    草稿版本: string
    草稿版本键: string
    最新审定附件: string
    边界状态: string
    候选附件: {
      附件编号: string
      附件类型: string
      边界起讫: string
      审定日期: string
      采纳: boolean
      冲突说明?: string
    }[]
  }
  在办发布单: PublishOrder | null
  历史修剪区间: { 桩号区间: string; 上次修剪: string; 备注?: string }[]
}

export type MapView = {
  区域版本: { 区域编号: string; 区域名称: string; 版本键: string; 区域版本: string; 发布结论: string; 在办发布单: string | null }[]
  生效引用: { 地图引用编号: string; 区域编号: string; 版本键: string; 桩号区间: string; 状态: string; 迁入责任班组?: string | null }[]
  全部引用: Record<string, unknown>[]
  班组工作面: { 班组名称: string; 当前版本键: string; 工作面: string; 排班卡: string; 负责区域: string[]; 状态: string }[]
  地图待办: { 待办编号: string; 区域编号: string; 版本键: string; 事项: string; 状态: string; 发布单编号: string | null }[]
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await request(path, init)
  if (!response.ok) {
    let message = `接口返回 ${response.status}`
    try {
      const payload = (await response.json()) as { detail?: string }
      if (payload.detail) message = payload.detail
    } catch {
      // 保留默认错误文案
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

export function fetchRegionDetail(regionCode: string): Promise<RegionDetail> {
  return call<RegionDetail>(`/api/green/regions/${encodeURIComponent(regionCode)}/detail`)
}

export function fetchMapView(): Promise<MapView> {
  return call<MapView>('/api/green/regions/map')
}

export function fetchOrder(orderId: number): Promise<PublishOrder> {
  return call<PublishOrder>(`/api/green/publish-orders/${orderId}`)
}

export async function startOrder(regionCode: string): Promise<{ ok: boolean; message: string; order?: PublishOrder }> {
  const response = await request('/api/green/publish-orders', {
    method: 'POST',
    body: JSON.stringify({ region_code: regionCode }),
  })
  const payload = (await response.json()) as { ok: boolean; message: string; entry?: PublishOrder }
  return { ok: payload.ok, message: payload.message, order: payload.entry }
}

export function confirmAttachment(orderId: number, attachmentNo?: string): Promise<PublishOrder> {
  return call<PublishOrder>(`/api/green/publish-orders/${orderId}/confirm-attachment`, {
    method: 'POST',
    body: JSON.stringify({ attachment_no: attachmentNo ?? null }),
  })
}

export function splitSlice(
  orderId: number,
  sliceId: number,
  splitAt: number,
  frontCrew: string | null,
  rearCrew: string,
): Promise<PublishOrder> {
  return call<PublishOrder>(`/api/green/publish-orders/${orderId}/slices/split`, {
    method: 'POST',
    body: JSON.stringify({ slice_id: sliceId, split_at: splitAt, front_crew: frontCrew, rear_crew: rearCrew }),
  })
}

export function reviewSlice(orderId: number, sliceId: number): Promise<PublishOrder> {
  return call<PublishOrder>(`/api/green/publish-orders/${orderId}/slices/review`, {
    method: 'POST',
    body: JSON.stringify({ slice_id: sliceId }),
  })
}

export function publishOrder(
  orderId: number,
  options: { failAtSlice?: string; failFinalize?: boolean; batchSize?: number } = {},
): Promise<PublishOrder> {
  return call<PublishOrder>(`/api/green/publish-orders/${orderId}/publish`, {
    method: 'POST',
    body: JSON.stringify({
      fail_at_slice: options.failAtSlice ?? null,
      fail_finalize: options.failFinalize ?? false,
      batch_size: options.batchSize ?? null,
    }),
  })
}

export function withdrawOrder(orderId: number): Promise<PublishOrder> {
  return call<PublishOrder>(`/api/green/publish-orders/${orderId}/withdraw`, { method: 'POST' })
}
