<template>
  <div class="modal-mask" @click.self="$emit('close')">
    <div class="modal publish-wizard">
      <header class="modal-head">
        <div>
          <h3>区域切片发布单 · {{ regionCode }}</h3>
          <p class="page-desc">单向推进：① 确认审定附件 → ② 核对切片责任 → ③ 执行发布；未审定不得跳级生成班组任务。</p>
        </div>
        <button class="btn ghost" type="button" @click="$emit('close')">关闭</button>
      </header>

      <ol class="step-bar">
        <li v-for="(label, idx) in stepLabels" :key="label" :class="{ active: idx === activeStep, done: idx < activeStep }">
          <span class="step-no">{{ idx + 1 }}</span>{{ label }}
        </li>
      </ol>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      <p v-if="order && order.失败原因" class="warn-text">{{ order.失败原因 }}</p>

      <!-- 第一步：确认审定附件 -->
      <section v-if="order && order.阶段 === STAGES.attachment" class="step-panel">
        <h4>① 确认审定附件</h4>
        <p class="hint">绿线与临时围挡冲突时以「正式审定」附件为准；历史修剪区间随切片保留。</p>
        <table class="data-table">
          <thead>
            <tr><th>选择</th><th>附件编号</th><th>类型</th><th>边界起讫</th><th>审定日期</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="att in detail?.版本指针.候选附件 ?? []" :key="att.附件编号"
                :class="{ 'row-conflict': att.附件类型 !== '正式审定' }">
              <td><input v-model="chosenAttachment" type="radio" name="attachment" :value="att.附件编号"
                         :disabled="att.附件类型 !== '正式审定'" /></td>
              <td>{{ att.附件编号 }}</td>
              <td>{{ att.附件类型 }}</td>
              <td>{{ att.边界起讫 }}</td>
              <td>{{ att.审定日期 }}</td>
              <td>{{ att.冲突说明 ?? (att.附件类型 === '正式审定' ? '正式审定边界' : '非正式附件不可采纳') }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="trimHistory.length" class="history-box">
          <strong>将保留的历史修剪区间：</strong>
          <span v-for="h in trimHistory" :key="h.桩号区间" class="tag">{{ h.桩号区间 }}（{{ h.上次修剪 }}）</span>
        </div>
        <footer class="modal-foot">
          <button class="btn primary" type="button" :disabled="busy || !chosenAttachment" @click="onConfirmAttachment">
            {{ busy ? '处理中…' : '确认审定附件并生成切片' }}
          </button>
        </footer>
      </section>

      <!-- 第二步：核对切片责任 -->
      <section v-else-if="order && (order.阶段 === STAGES.slice || order.阶段 === STAGES.ready)" class="step-panel">
        <h4>② 核对切片责任{{ order.阶段 === STAGES.ready ? '（已全部核对）' : '' }}</h4>
        <p class="hint">存量重叠区迁移前先拆分责任；逐片核对，全部核对完才能发布。</p>
        <table class="data-table">
          <thead>
            <tr><th>切片编号</th><th>桩号区间</th><th>责任班组</th><th>历史修剪区间</th><th>状态</th><th>操作</th></tr>
          </thead>
          <tbody>
            <template v-for="slc in activeSlices" :key="slc.切片编号">
              <tr :class="{ 'row-warning': slc.需拆分 }">
                <td>{{ slc.切片编号 }}</td>
                <td>{{ slc.桩号区间 }}</td>
                <td>{{ slc.责任班组 ?? '待指派' }}</td>
                <td>{{ slc.历史修剪区间.join('、') || '—' }}</td>
                <td>
                  <span :class="['state-tag', stateClass(slc.发布状态)]">{{ slc.发布状态 }}</span>
                  <span v-if="slc.围挡冲突" class="tag warn">围挡冲突已按正式审定裁切</span>
                </td>
                <td class="row-actions">
                  <button v-if="slc.发布状态 === '待核对'" class="link" type="button" @click="openSplit(slc)">拆分责任</button>
                  <button v-if="slc.发布状态 === '待核对' && slc.责任班组 && !slc.需拆分"
                          class="link" type="button" @click="onReview(slc)">核对通过</button>
                </td>
              </tr>
              <tr v-if="slc.需拆分" class="row-warn-detail">
                <td colspan="6">⚠ {{ slc.拆分说明 }}</td>
              </tr>
            </template>
          </tbody>
        </table>

        <div v-if="splitting" class="split-box">
          <h4>拆分切片 {{ splitting.切片编号 }}（{{ splitting.桩号区间 }}）</h4>
          <p class="hint">在重叠边界拆分：前段沿用原责任，后段（重叠区）按责任关系指派给迁移班组。</p>
          <label>拆分位置（绝对桩号，米）
            <input v-model.number="splitForm.splitAt" type="number" :min="splitting.起点 + 1" :max="splitting.止点 - 1" />
          </label>
          <label>前段责任班组
            <input v-model="splitForm.frontCrew" :placeholder="splitting.责任班组 ?? '请输入班组名称'" />
          </label>
          <label>后段（重叠区）责任班组
            <input v-model="splitForm.rearCrew" placeholder="如：绿化三班" />
          </label>
          <div class="row-actions">
            <button class="btn primary" type="button" :disabled="busy" @click="onSplit">确认拆分</button>
            <button class="btn" type="button" @click="splitting = null">取消</button>
          </div>
        </div>

        <footer class="modal-foot">
          <button class="btn primary" type="button" :disabled="busy || order.阶段 !== STAGES.ready"
                  @click="onPublish">
            {{ busy ? '发布中…' : `③ 执行发布（${reviewedCount}/${activeSlices.length} 片已核对）` }}
          </button>
        </footer>
      </section>

      <!-- 发布中断：可续发 -->
      <section v-else-if="order && order.阶段 === STAGES.publishing" class="step-panel">
        <h4>发布中断，可续发</h4>
        <p class="warn-text">已发布 {{ order.已发布切片数 }} 片；连接断开/失败后从未发布切片继续，未成功批次已整批回滚。</p>
        <footer class="modal-foot">
          <button class="btn primary" type="button" :disabled="busy" @click="onPublish">从首个未发布切片继续</button>
        </footer>
      </section>

      <!-- 发布结果 -->
      <section v-else-if="order" class="step-panel">
        <h4>发布结论</h4>
        <table class="data-table">
          <tbody>
            <tr><th>阶段</th><td>{{ order.阶段 }}（{{ order.发布时间 ?? order.撤回时间 ?? '—' }}）</td></tr>
            <tr><th>发布版本键</th><td>{{ order.版本键 }}（幂等键：{{ order.版本键 }}:release）</td></tr>
            <tr><th>审定边界</th><td>{{ order.审定边界 }}（{{ order.审定附件编号 }}）</td></tr>
            <tr><th>结论回写</th><td>{{ order.发布结论 ?? '—' }}</td></tr>
            <tr v-if="order.重叠迁移明细.length">
              <th>重叠区迁移</th>
              <td>
                <div v-for="m in order.重叠迁移明细" :key="String(m.原记录) + String(m.迁移区间)" class="tag">
                  {{ m.原记录 }} {{ m.迁移区间 }} → {{ m.迁入责任班组 }}（{{ m.迁入版本键 }}）
                </div>
              </td>
            </tr>
            <tr v-if="order.围挡冲突处理.length">
              <th>围挡冲突</th>
              <td v-for="c in order.围挡冲突处理" :key="String(c.围挡附件)">
                {{ c.围挡附件 }}（{{ c.围挡区间 }}）：{{ c.处理结论 }}
              </td>
            </tr>
          </tbody>
        </table>
        <footer class="modal-foot">
          <button v-if="order.阶段 === STAGES.published" class="btn" type="button"
                  :disabled="busy" @click="onWithdraw">撤回发布（恢复旧区域）</button>
          <button class="btn primary" type="button" @click="finish">完成</button>
        </footer>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'

import {
  type PublishOrder,
  type RegionDetail,
  type Slice,
  confirmAttachment,
  fetchRegionDetail,
  publishOrder,
  reviewSlice,
  splitSlice,
  startOrder,
  withdrawOrder,
} from '@/api/greenPublish'

const STAGES = {
  attachment: '待审定附件',
  slice: '待核对切片',
  ready: '待发布',
  publishing: '发布中',
  published: '已发布',
  withdrawn: '已撤回',
} as const

const props = defineProps<{ regionCode: string }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'changed'): void
}>()

const stepLabels = ['确认审定附件', '核对切片责任', '执行发布']
const order = ref<PublishOrder | null>(null)
const detail = ref<RegionDetail | null>(null)
const chosenAttachment = ref<string>('')
const busy = ref(false)
const errorMessage = ref('')
const splitting = ref<Slice | null>(null)
const splitForm = reactive({ splitAt: 0, frontCrew: '', rearCrew: '' })

const activeStep = computed(() => {
  if (!order.value) return 0
  const stage: string = order.value.阶段
  if (stage === STAGES.attachment) return 0
  if (stage === STAGES.slice || stage === STAGES.ready || stage === STAGES.publishing) return 1
  return 2
})

const activeSlices = computed<Slice[]>(() => (order.value ? order.value.切片.filter((s) => s.发布状态 !== '已拆分') : []))
const reviewedCount = computed(() => activeSlices.value.filter((s) => s.发布状态 !== '待核对').length)
const trimHistory = computed(() => detail.value?.历史修剪区间 ?? [])

function stateClass(state: string): string {
  if (state === '已发布') return 'state-ok'
  if (state === '责任已核对') return 'state-done'
  return 'state-pending'
}

async function init() {
  busy.value = true
  errorMessage.value = ''
  try {
    detail.value = await fetchRegionDetail(props.regionCode)
    if (detail.value.在办发布单) {
      order.value = detail.value.在办发布单
    } else {
      const result = await startOrder(props.regionCode)
      if (!result.ok || !result.order) throw new Error(result.message)
      order.value = result.order
    }
    const formal = detail.value.版本指针.候选附件.find((a) => a.附件类型 === '正式审定')
    if (formal) chosenAttachment.value = formal.附件编号
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打开发布单失败'
  } finally {
    busy.value = false
  }
}

async function onConfirmAttachment() {
  if (!order.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    order.value = await confirmAttachment(order.value.id, chosenAttachment.value)
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '确认审定附件失败'
  } finally {
    busy.value = false
  }
}

function openSplit(slc: Slice) {
  splitting.value = slc
  splitForm.splitAt = slc.止点 - 20 < slc.起点 + 1 ? slc.起点 + 1 : slc.止点 - 20
  splitForm.frontCrew = slc.责任班组 ?? ''
  splitForm.rearCrew = ''
}

async function onSplit() {
  if (!order.value || !splitting.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    order.value = await splitSlice(
      order.value.id,
      splitting.value.id,
      splitForm.splitAt,
      splitForm.frontCrew.trim() || null,
      splitForm.rearCrew.trim(),
    )
    splitting.value = null
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '拆分责任失败'
  } finally {
    busy.value = false
  }
}

async function onReview(slc: Slice) {
  if (!order.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    order.value = await reviewSlice(order.value.id, slc.id)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '核对切片失败'
  } finally {
    busy.value = false
  }
}

async function onPublish() {
  if (!order.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    order.value = await publishOrder(order.value.id)
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '发布失败'
    // 发布中断后刷新发布单，拿到续发游标
    const fresh = await fetchRegionDetail(props.regionCode)
    if (fresh.在办发布单) order.value = fresh.在办发布单
  } finally {
    busy.value = false
  }
}

async function onWithdraw() {
  if (!order.value) return
  busy.value = true
  errorMessage.value = ''
  try {
    order.value = await withdrawOrder(order.value.id)
    emit('changed')
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤回失败'
  } finally {
    busy.value = false
  }
}

function finish() {
  emit('changed')
  emit('close')
}

watch(() => props.regionCode, init, { immediate: true })
</script>

<style scoped>
.modal-mask { position: fixed; inset: 0; background: rgba(15, 23, 42, 0.45); display: flex; align-items: flex-start; justify-content: center; z-index: 50; padding: 32px 16px; overflow-y: auto; }
.modal { background: #fff; border-radius: 10px; border: 1px solid var(--border); width: min(960px, 100%); padding: 18px 20px; }
.modal-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 14px; }
.step-bar { display: flex; gap: 8px; list-style: none; padding: 0; margin: 8px 0 14px; }
.step-bar li { flex: 1; border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 13px; color: var(--muted); background: #f8fafc; }
.step-bar li.active { border-color: var(--brand); color: var(--brand); background: #eff6ff; font-weight: 600; }
.step-bar li.done { color: #047857; border-color: #a7f3d0; background: #ecfdf5; }
.step-no { display: inline-flex; width: 20px; height: 20px; border-radius: 50%; background: #e2e8f0; align-items: center; justify-content: center; margin-right: 6px; font-size: 12px; }
.step-panel h4 { margin: 10px 0 6px; }
.hint { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.warn-text { color: #b45309; font-size: 13px; }
.row-conflict { background: #fffbeb; }
.row-warning { background: #fef2f2; }
.row-warn-detail td { color: #b42318; font-size: 12px; background: #fff1f2; }
.tag { display: inline-block; border: 1px solid #cbd5e1; border-radius: 999px; padding: 2px 8px; font-size: 12px; margin: 2px 4px 2px 0; background: #f8fafc; }
.tag.warn { border-color: #fcd34d; background: #fffbeb; color: #92400e; }
.state-tag { border-radius: 4px; padding: 1px 6px; font-size: 12px; }
.state-ok { background: #dcfce7; color: #166534; }
.state-done { background: #dbeafe; color: #1e40af; }
.state-pending { background: #f1f5f9; color: #475569; }
.history-box, .split-box { margin-top: 10px; border: 1px dashed var(--border); border-radius: 8px; padding: 10px 12px; }
.split-box label { display: inline-flex; flex-direction: column; font-size: 12px; color: var(--muted); margin: 6px 10px 6px 0; }
.split-box input { margin-top: 4px; min-width: 180px; }
</style>
