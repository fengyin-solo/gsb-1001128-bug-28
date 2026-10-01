<template>
  <section class="page">
    <header class="page-head">
      <div>
        <h2>绿化区域详情 · {{ regionCode }}</h2>
        <p class="page-desc">版本键与列表页、地图入口同源；在办发布单一处看全。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/green">返回列表</RouterLink>
        <RouterLink class="btn" to="/green/map">地图入口</RouterLink>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="detail">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">当前版本键</span>
          <strong class="stat-value">{{ detail.版本指针.当前版本键 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">最新审定附件</span>
          <strong class="stat-value">{{ detail.版本指针.最新审定附件 }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">边界状态</span>
          <strong class="stat-value">{{ detail.版本指针.边界状态 }}</strong>
        </article>
      </div>

      <table class="data-table detail-grid">
        <tbody>
          <tr v-for="(value, key) in detail.台账" :key="String(key)">
            <th>{{ key }}</th>
            <td>{{ value ?? '—' }}</td>
          </tr>
        </tbody>
      </table>

      <h3 class="block-title">审定附件候选</h3>
      <table class="data-table">
        <thead>
          <tr><th>附件编号</th><th>类型</th><th>边界起讫</th><th>审定日期</th><th>说明</th></tr>
        </thead>
        <tbody>
          <tr v-for="att in detail.版本指针.候选附件" :key="att.附件编号"
              :class="{ 'row-conflict': att.附件类型 !== '正式审定' }">
            <td>{{ att.附件编号 }}</td>
            <td>{{ att.附件类型 }}</td>
            <td>{{ att.边界起讫 }}</td>
            <td>{{ att.审定日期 }}</td>
            <td>{{ att.冲突说明 ?? (att.附件类型 === '正式审定' ? '正式审定边界（冲突时以此为准）' : '非正式附件') }}</td>
          </tr>
        </tbody>
      </table>

      <h3 class="block-title">历史修剪区间（发布/迁移只保留不删除）</h3>
      <table class="data-table">
        <thead><tr><th>桩号区间</th><th>上次修剪</th><th>备注</th></tr></thead>
        <tbody>
          <tr v-for="h in detail.历史修剪区间" :key="h.桩号区间">
            <td>{{ h.桩号区间 }}</td>
            <td>{{ h.上次修剪 }}</td>
            <td>{{ h.备注 }}</td>
          </tr>
          <tr v-if="!detail.历史修剪区间.length"><td colspan="3" class="empty-state">暂无历史修剪区间</td></tr>
        </tbody>
      </table>

      <footer class="page-foot">
        <button class="btn primary" type="button" @click="wizardOpen = true">
          {{ detail.在办发布单 ? `继续处理发布单 ${detail.在办发布单.发布单编号}（${detail.在办发布单.阶段}）` : '发起区域切片发布单' }}
        </button>
        <span v-if="detail.在办发布单" class="muted">在办单：{{ detail.在办发布单.版本键 }} · 已发布 {{ detail.在办发布单.已发布切片数 }} 片</span>
      </footer>
    </template>

    <PublishWizard v-if="wizardOpen" :region-code="regionCode" @close="wizardOpen = false" @changed="reload" />
  </section>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { fetchRegionDetail, type RegionDetail } from '@/api/greenPublish'
import PublishWizard from './PublishWizard.vue'

const route = useRoute()
const regionCode = String(route.params.regionCode ?? '')
const detail = ref<RegionDetail | null>(null)
const errorMessage = ref('')
const wizardOpen = ref(false)

async function reload() {
  errorMessage.value = ''
  try {
    detail.value = await fetchRegionDetail(regionCode)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '区域详情读取失败'
  }
}

watch(() => route.params.regionCode, reload, { immediate: true })
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.detail-grid th { width: 160px; background: #f8fafc; color: var(--muted); }
.block-title { margin: 16px 0 8px; font-size: 15px; }
.row-conflict { background: #fffbeb; }
.muted { color: var(--muted); font-size: 12px; }
</style>
