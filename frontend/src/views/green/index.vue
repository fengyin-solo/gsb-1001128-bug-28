<template>
  <section class="page" data-module="green">
    <header class="page-head">
      <div>
        <h2>绿化管养管理</h2>
        <p class="page-desc">维护绿化区域，围绕区域编号、区域名称、植物品种、面积做登记、筛选与状态流转；版本键与详情页、地图入口同源。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记绿化区域</button>
        <RouterLink class="btn" to="/green/map">地图入口</RouterLink>
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
          <th>区域版本</th>
          <th>发布结论</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td><span class="version-key">{{ row['版本键'] ?? '—' }}</span></td>
          <td class="conclusion-cell">{{ row['发布结论'] ?? '—' }}</td>
          <td class="row-actions">
            <RouterLink class="link" :to="`/green/regions/${row['区域编号']}`">详情</RouterLink>
            <button class="link primary-link" type="button" @click="openWizard(String(row['区域编号']))">区域切片发布</button>
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
          <td :colspan="columns.length + 3" class="empty-state">暂无绿化管养数据，可先登记绿化区域</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条绿化管养记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <PublishWizard v-if="wizardRegion" :region-code="wizardRegion" @close="wizardRegion = ''" @changed="reload" />
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'
import PublishWizard from './PublishWizard.vue'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/green'
const columns = ["区域编号", "区域名称", "植物品种", "面积", "上次修剪", "上次浇水", "管养班组", "管养状态"]
const actions = ["安排修剪", "安排补植", "病虫防治"]
const statuses = ["正常", "待修剪", "待补植", "病虫害"]
const stats = [{"label": "正常区域", "value": 0}, {"label": "待修剪区域", "value": 0}, {"label": "病虫害区域", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const wizardRegion = ref('')

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

function openWizard(regionCode: string) {
  errorMessage.value = ''
  wizardRegion.value = regionCode
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
.page-actions { display: flex; gap: 8px; }
.version-key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #eff6ff; color: #1d4ed8; border-radius: 4px; padding: 1px 6px; white-space: nowrap; }
.conclusion-cell { max-width: 260px; font-size: 12px; color: var(--muted); }
.primary-link { font-weight: 600; }
</style>
