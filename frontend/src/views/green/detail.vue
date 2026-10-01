<template>
  <section class="page" data-module="green-detail">
    <header class="page-head">
      <div>
        <h2>绿化区域详情 · {{ region['区域名称'] ?? '' }}</h2>
        <p class="page-desc">区域版本发布单向推进：先确认审定附件，再核对切片责任，最后执行发布。切片、班组任务与地图引用同事务落库。</p>
      </div>
      <div class="page-actions">
        <router-link class="btn" to="/green">返回列表</router-link>
        <router-link class="btn" to="/green/map">地图待办</router-link>
      </div>
    </header>

    <article class="panel">
      <h3>绿化台账</h3>
      <div class="kv-grid">
        <div><span>区域编号</span><strong>{{ region['区域编号'] ?? '—' }}</strong></div>
        <div><span>植物品种</span><strong>{{ region['植物品种'] ?? '—' }}</strong></div>
        <div><span>面积</span><strong>{{ region['面积'] ?? '—' }}</strong></div>
        <div><span>管养班组</span><strong>{{ region['管养班组'] ?? '—' }}</strong></div>
        <div><span>当前版本</span><strong>{{ region['当前版本'] ?? '—' }}</strong></div>
        <div><span>已发布版本</span><strong>{{ region['已发布版本'] ?? '—' }}</strong></div>
        <div><span>地图版本</span><strong>{{ region['地图版本'] ?? '—' }}</strong></div>
        <div>
          <span>版本一致性</span>
          <strong>
            <span v-if="releaseView['版本一致']" class="tag tag-ok">列表/详情/地图一致</span>
            <span v-else class="tag tag-warn">存在版本错位</span>
          </strong>
        </div>
        <div class="kv-wide"><span>正式审定附件</span><strong>{{ region['审定附件'] || '尚未审定' }}</strong></div>
        <div class="kv-wide"><span>历史修剪区间</span><strong>{{ region['历史修剪区间'] || '—' }}</strong></div>
      </div>
    </article>

    <!-- 发布向导 -->
    <article class="panel">
      <h3>区域切片发布单</h3>
      <ol class="steps">
        <li v-for="(label, idx) in stepLabels" :key="label"
            class="step" :class="stepClass(idx)">
          <b>{{ idx + 1 }}</b>{{ label }}
        </li>
      </ol>

      <p v-if="message" class="notice" :class="ok ? 'notice-ok' : 'notice-err'">{{ message }}</p>

      <!-- 建单 -->
      <div v-if="!order" class="wizard">
        <div class="form-row">
          <label>发布版本<input v-model="form.版本" placeholder="如 v2" /></label>
          <label>基础版本（撤回时恢复）<input v-model="form.基础版本" :placeholder="String(region['已发布版本'] || '无')" /></label>
        </div>
        <h4>审定边界段落（绿线取「正式审定」，临时围挡冲突时以正式附件为准）</h4>
        <table class="data-table seg-table">
          <thead>
            <tr><th>起(桩号)</th><th>止(桩号)</th><th>责任班组</th><th>边界来源</th><th></th></tr>
          </thead>
          <tbody>
            <tr v-for="(seg, i) in form.segments" :key="i">
              <td><input v-model="seg.起" placeholder="K0+000" /></td>
              <td><input v-model="seg.止" placeholder="K0+900" /></td>
              <td><input v-model="seg.班组" placeholder="绿化一班" /></td>
              <td>
                <select v-model="seg.来源">
                  <option value="正式审定">正式审定</option>
                  <option value="临时围挡">临时围挡</option>
                </select>
              </td>
              <td><button class="link" type="button" @click="form.segments.splice(i, 1)">删除</button></td>
            </tr>
          </tbody>
        </table>
        <button class="btn" type="button" @click="addSegment">新增边界段落</button>
        <div class="wizard-actions">
          <button class="btn primary" type="button" :disabled="loading" @click="createOrder">创建发布单（第一步：审定附件）</button>
        </div>
      </div>

      <!-- 已建单：阶段操作 -->
      <div v-else class="wizard">
        <div class="order-meta">
          <span>单号：{{ order['发布单号'] }}</span>
          <span>阶段：<b>{{ order['阶段'] }}</b></span>
          <span v-if="order['审定附件']">审定附件：{{ order['审定附件']['文件名'] }}（{{ order['审定附件']['附件来源'] }}）</span>
        </div>

        <div v-if="order['阶段'] === '待审定'" class="step-body">
          <h4>第一步 · 确认审定附件</h4>
          <div class="form-row">
            <label>审定附件文件名<input v-model="attachment.文件名" placeholder="GREE-xxxx-v2-边界审定图.pdf" /></label>
            <label>附件来源
              <select v-model="attachment.附件来源">
                <option value="正式审定">正式审定</option>
                <option value="临时围挡">临时围挡</option>
              </select>
            </label>
          </div>
          <p class="hint">只接受正式审定附件；与临时围挡冲突时以正式审定为准，并保留历史修剪区间。</p>
          <button class="btn primary" type="button" :disabled="loading" @click="confirmAttachment">确认审定附件，进入切片责任核对</button>
        </div>

        <div v-else-if="order['阶段'] === '待核对责任'" class="step-body">
          <h4>第二步 · 核对切片责任</h4>
          <p class="hint">系统先按正式附件消解围挡冲突，再对存量重叠区拆分责任，规划切片与班组工作面。</p>
          <button class="btn primary" type="button" :disabled="loading" @click="checkSlices">核对切片责任并规划切片</button>
        </div>

        <div v-else-if="order['阶段'] === '待发布'" class="step-body">
          <h4>第三步 · 执行发布</h4>
          <p class="hint">
            切片、班组工作面/排班卡、地图待办在同一事务内逐片落库，失败整批回滚并复位旧图；
            发布键 {{ order['发布键'] }} 幂等，连接断开后可从未发布切片继续。
          </p>
          <p class="hint">落库进度：{{ order['切片进度']['已落库'] }} / {{ order['切片进度']['总数'] }}</p>
          <button class="btn primary" type="button" :disabled="loading" @click="publish">
            {{ order['切片进度']['已落库'] > 0 ? '从未发布切片继续发布' : '执行发布' }}
          </button>
        </div>

        <div v-else-if="order['阶段'] === '已发布'" class="step-body">
          <h4>发布结论</h4>
          <p class="notice notice-ok">
            已发布 {{ order['发布结果']['切片数'] }} 个切片，班组任务 {{ order['发布结果']['班组任务数'] }} 条，
            地图待办 {{ order['发布结果']['地图待办数'] }} 条，重叠区迁移 {{ order['发布结果']['迁移重叠区'] }} 处；
            结论已回写绿化台账、班组清单与地图待办。
          </p>
          <button class="btn" type="button" :disabled="loading" @click="withdraw">撤回发布（级联清理并恢复旧图）</button>
        </div>

        <div v-else-if="order['阶段'] === '已撤回'" class="step-body">
          <p class="notice">发布已撤回，旧图与旧责任已恢复；如需更新边界请按新版本重新发起发布。</p>
          <button class="btn" type="button" @click="resetWizard">发起新版本发布</button>
        </div>

        <!-- 切片与迁移规划 -->
        <template v-if="order['切片'] && order['切片'].length">
          <h4>切片与责任拆分（共 {{ order['切片'].length }} 片）</h4>
          <table class="data-table">
            <thead>
              <tr><th>切片编号</th><th>工作面</th><th>责任班组</th><th>边界依据</th><th>历史修剪区间</th><th>重叠责任拆分</th><th>状态</th></tr>
            </thead>
            <tbody>
              <tr v-for="s in order['切片']" :key="s['切片编号']">
                <td>{{ s['切片编号'] }}</td>
                <td>{{ s['工作面'] }}</td>
                <td>{{ s['责任班组'] }}</td>
                <td>{{ s['边界依据'] }}</td>
                <td>{{ (s['历史修剪区间'] || []).join('；') || '—' }}</td>
                <td>
                  <p v-for="sp in s['拆分责任']" :key="sp['重叠区间']" class="cell-note">
                    {{ sp['重叠区间'] }}：{{ sp['原责任班组'] }} → {{ sp['新责任班组'] }}（{{ sp['处理'] }}）
                  </p>
                  <span v-if="!s['拆分责任'].length">—</span>
                </td>
                <td>{{ s['状态'] }}</td>
              </tr>
            </tbody>
          </table>
        </template>
      </div>
    </article>

    <!-- 发布后三表视图 -->
    <div class="panel-row">
      <article class="panel panel-half">
        <h3>生效地图切片（地图引用）</h3>
        <table class="data-table">
          <thead><tr><th>切片</th><th>版本</th><th>工作面</th><th>班组</th></tr></thead>
          <tbody>
            <tr v-for="t in releaseView['生效切片']" :key="t['切片编号']">
              <td>{{ t['切片编号'] }}</td><td>{{ t['版本'] }}</td><td>{{ t['工作面'] }}</td><td>{{ t['责任班组'] }}</td>
            </tr>
            <tr v-if="!releaseView['生效切片'].length"><td colspan="4" class="empty-state">暂无生效切片</td></tr>
          </tbody>
        </table>
      </article>

      <article class="panel panel-half">
        <h3>地图待办</h3>
        <table class="data-table">
          <thead><tr><th>待办</th><th>切片</th><th>版本</th><th>工作面</th></tr></thead>
          <tbody>
            <tr v-for="t in releaseView['地图待办']" :key="t['待办编号']">
              <td>{{ t['待办编号'] }}</td><td>{{ t['切片编号'] }}</td><td>{{ t['版本'] }}</td><td>{{ t['工作面'] }}</td>
            </tr>
            <tr v-if="!releaseView['地图待办'].length"><td colspan="4" class="empty-state">暂无地图待办</td></tr>
          </tbody>
        </table>
      </article>
    </div>

    <article class="panel">
      <h3>班组清单 · 工作面与排班卡</h3>
      <table class="data-table">
        <thead><tr><th>任务</th><th>切片</th><th>版本</th><th>班组</th><th>工作面</th><th>排班卡</th></tr></thead>
        <tbody>
          <tr v-for="t in releaseView['班组清单']" :key="t['任务编号']">
            <td>{{ t['任务编号'] }}</td>
            <td>{{ t['切片编号'] }}</td>
            <td>{{ t['版本'] }}</td>
            <td>{{ t['责任班组'] }}</td>
            <td>{{ t['工作面'] }}</td>
            <td>
              <span v-for="card in t['排班卡']" :key="card['日期'] + card['班次']" class="card-chip">
                {{ card['日期'] }} {{ card['班次'] }} · {{ card['作业内容'] }}
              </span>
            </td>
          </tr>
          <tr v-if="!releaseView['班组清单'].length"><td colspan="6" class="empty-state">未发布前不生成班组任务</td></tr>
        </tbody>
      </table>
    </article>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'

import { request } from '@/api/client'

type Dict = Record<string, any>

const route = useRoute()
const entryId = String(route.params.id)

const region = ref<Dict>({})
const releaseView = ref<Dict>({})
const order = ref<Dict | null>(null)
const message = ref('')
const ok = ref(true)
const loading = ref(false)

const stepLabels = ['确认审定附件', '核对切片责任', '执行发布']

const form = reactive<{ 版本: string; 基础版本: string; segments: Dict[] }>({
  版本: '',
  基础版本: '',
  segments: [{ 起: '', 止: '', 班组: '', 来源: '正式审定' }],
})
const attachment = reactive<{ 文件名: string; 附件来源: string }>({ 文件名: '', 附件来源: '正式审定' })

function addSegment() {
  form.segments.push({ 起: '', 止: '', 班组: '', 来源: '正式审定' })
}

function stepClass(idx: number): string {
  const stage = order.value?.['阶段']
  const rank: Record<string, number> = {
    待审定: 0,
    待核对责任: 1,
    待发布: 2,
    已发布: 3,
    已撤回: -1,
  }
  if (!stage || rank[stage] === undefined) return ''
  const current = rank[stage]
  if (current > idx || current === 3) return 'step-done'
  if (current === idx) return 'step-active'
  return ''
}

function setMessage(text: string, success: boolean) {
  message.value = text
  ok.value = success
}

async function call(path: string, body: Dict = {}): Promise<any | null> {
  loading.value = true
  try {
    const res = await request(path, { method: 'POST', body: JSON.stringify(body) })
    const payload = await res.json()
    setMessage(payload.message ?? '', Boolean(payload.ok))
    return payload
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '请求失败', false)
    return null
  } finally {
    loading.value = false
  }
}

function normalizeSegments(): Dict[] {
  return form.segments
    .filter((s) => String(s.起 ?? '').trim() && String(s.止 ?? '').trim() && String(s.班组 ?? '').trim())
    .map((s) => ({ 起: s.起, 止: s.止, 班组: s.班组, 来源: s.来源 }))
}

async function createOrder() {
  if (!form.版本.trim()) {
    setMessage('请填写发布版本', false)
    return
  }
  const payload = await call('/api/green/release/orders', {
    values: {
      区域编号: region.value['区域编号'],
      版本: form.版本.trim(),
      基础版本: form.基础版本.trim(),
      边界段落: normalizeSegments(),
    },
  })
  if (payload?.ok) await reload()
}

async function confirmAttachment() {
  if (!attachment.文件名.trim()) {
    setMessage('请先填写并确认正式审定附件', false)
    return
  }
  const payload = await call(`/api/green/release/orders/${order.value!['id']}/confirm-attachment`, {
    values: { 文件名: attachment.文件名.trim(), 附件来源: attachment.附件来源 },
  })
  if (payload?.ok) await reload()
}

async function checkSlices() {
  const payload = await call(`/api/green/release/orders/${order.value!['id']}/check-slices`, { values: {} })
  if (payload?.ok) await reload()
}

async function publish() {
  const payload = await call(`/api/green/release/orders/${order.value!['id']}/publish`, { values: {} })
  await reload()
  if (payload?.ok) setMessage(payload.message, true)
}

async function withdraw() {
  const payload = await call(`/api/green/release/orders/${order.value!['id']}/withdraw`, { values: {} })
  await reload()
  if (payload?.ok) setMessage(payload.message, true)
}

function resetWizard() {
  order.value = null
  form.版本 = ''
  form.segments = [{ 起: '', 止: '', 班组: '', 来源: '正式审定' }]
}

async function reload() {
  try {
    const res = await request(`/api/green/${entryId}`)
    if (!res.ok) throw new Error('绿化区域详情读取失败')
    const payload = await res.json()
    region.value = payload
    releaseView.value = payload['发布视图'] ?? {}
    order.value = releaseView.value['发布单'] ?? null
    if (order.value && !form.版本) form.版本 = String(order.value['版本'] ?? '')
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '绿化区域详情读取失败', false)
  }
}

onMounted(reload)
</script>

<style scoped>
.panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
}
.panel h3 { margin: 0 0 10px; font-size: 15px; }
.panel-row { display: flex; gap: 12px; }
.panel-half { flex: 1; }
.kv-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px 16px; }
.kv-grid > div span { display: block; font-size: 12px; color: var(--muted); }
.kv-grid > div strong { font-size: 13px; font-weight: 600; }
.kv-wide { grid-column: span 2; }
.steps { display: flex; gap: 18px; list-style: none; padding: 0; margin: 6px 0 14px; }
.step { display: flex; align-items: center; gap: 6px; font-size: 13px; color: var(--muted); }
.step b {
  display: inline-flex; width: 22px; height: 22px; border-radius: 50%;
  align-items: center; justify-content: center; border: 1px solid var(--border);
}
.step-active { color: #1f6feb; font-weight: 600; }
.step-active b { background: #1f6feb; color: #fff; border-color: #1f6feb; }
.step-done b { background: #ecfdf3; border-color: #a6f4c5; color: #067647; }
.wizard { border-top: 1px dashed var(--border); padding-top: 12px; }
.form-row { display: flex; gap: 14px; flex-wrap: wrap; margin-bottom: 8px; }
.form-row label { font-size: 12px; color: var(--muted); display: flex; flex-direction: column; gap: 4px; }
.form-row input, .form-row select, .seg-table input, .seg-table select { width: 220px; padding: 5px 8px; border: 1px solid var(--border); border-radius: 6px; }
.seg-table { margin: 8px 0; }
.seg-table input, .seg-table select { width: auto; }
.wizard-actions { margin-top: 12px; }
.step-body { margin: 10px 0; }
.hint { font-size: 12px; color: var(--muted); }
.order-meta { display: flex; gap: 16px; flex-wrap: wrap; font-size: 13px; margin-bottom: 8px; }
.notice { font-size: 13px; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--border); }
.notice-ok { background: #ecfdf3; border-color: #a6f4c5; color: #067647; }
.notice-err { background: #fef3f2; border-color: #fecdca; color: #b42318; }
.cell-note { margin: 0; font-size: 12px; color: var(--muted); }
.card-chip { display: inline-block; font-size: 12px; background: #f2f4f7; border-radius: 10px; padding: 1px 8px; margin: 0 4px 4px 0; }
.tag { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 10px; border: 1px solid var(--border); }
.tag-ok { color: #067647; border-color: #a6f4c5; background: #ecfdf3; }
.tag-warn { color: #b54708; border-color: #fedf89; background: #fffaeb; }
</style>
