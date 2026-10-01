<template>
  <section class="page" data-module="green">
    <header class="page-head">
      <div>
        <h2>绿化管养管理</h2>
        <p class="page-desc">区域边界确认后统一走「区域切片发布单」：审定附件 → 切片责任 → 执行发布，列表、详情、地图共用同一版本口径。</p>
      </div>
      <div class="page-actions">
        <router-link class="btn" to="/green/map">地图待办入口</router-link>
        <button class="btn primary" type="button" @click="openCreate">登记绿化区域</button>
        <button class="btn" type="button" @click="exportRows">导出绿化管养清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>当前版本</th>
          <th>已发布版本</th>
          <th>发布阶段</th>
          <th>地图待办</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td>{{ row['当前版本'] ?? '—' }}</td>
          <td>{{ row['已发布版本'] ?? '—' }}</td>
          <td>
            <span class="tag" :class="releaseTagClass(row)">{{ row['发布状态'] ?? '未发起发布' }}</span>
            <span v-if="row['版本一致'] === false" class="tag tag-warn">版本错位</span>
          </td>
          <td>{{ row['待办数'] ?? 0 }}</td>
          <td class="row-actions">
            <router-link class="link" :to="`/green/${row.id}`">详情/发布</router-link>
            <router-link class="link" to="/green/map">地图</router-link>
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 6" class="empty-state">暂无绿化管养数据，可先登记绿化区域</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条绿化管养记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/green'
const columns = ["区域编号", "区域名称", "植物品种", "面积", "上次修剪", "上次浇水", "管养班组", "管养状态"]
const actions = ["安排修剪", "安排补植", "病虫防治"]
const stats = [
  { "label": "区域总数", "value": 0 },
  { "label": "待发布版本", "value": 0 },
  { "label": "地图待办", "value": 0 },
]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function releaseTagClass(row: Row): string {
  if (row['版本一致'] === true) return 'tag-ok'
  const stage = String(row['发布状态'] ?? '')
  if (stage === '已发布') return 'tag-ok'
  if (stage === '待发布') return 'tag-warn'
  return ''
}

function resetFilters() {
  filters.value = {}
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '绿化区域登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('绿化管养动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绿化管养操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('绿化区域列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '绿化管养列表读取失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.tag {
  display: inline-block;
  font-size: 12px;
  padding: 1px 8px;
  border-radius: 10px;
  border: 1px solid var(--border);
  color: var(--muted);
  margin-right: 4px;
}
.tag-ok { color: #067647; border-color: #a6f4c5; background: #ecfdf3; }
.tag-warn { color: #b54708; border-color: #fedf89; background: #fffaeb; }
</style>
