<template>
  <section class="page" data-module="green-map">
    <header class="page-head">
      <div>
        <h2>绿化地图 · 区域切片版本</h2>
        <p class="page-desc">地图切片、班组工作面、排班卡按版本键对齐；发布结论回写后这里同步切换，杜绝落在旧区域。</p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" to="/green">返回绿化列表</RouterLink>
        <button class="btn" type="button" @click="reload">刷新地图</button>
      </div>
    </header>

    <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>

    <template v-if="view">
      <h3 class="block-title">区域版本（与列表页、详情页同一版本指针）</h3>
      <table class="data-table">
        <thead>
          <tr><th>区域编号</th><th>区域名称</th><th>当前版本键</th><th>发布结论</th><th>在办发布单</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in view.区域版本" :key="row.区域编号">
            <td>{{ row.区域编号 }}</td>
            <td>{{ row.区域名称 }}</td>
            <td><span class="version-key">{{ row.版本键 }}</span></td>
            <td>{{ row.发布结论 }}</td>
            <td>{{ row.在办发布单 ?? '—' }}</td>
            <td>
              <RouterLink class="link" :to="`/green/regions/${row.区域编号}`">详情</RouterLink>
              <button class="link" type="button" @click="openWizard(row.区域编号)">区域切片发布</button>
            </td>
          </tr>
        </tbody>
      </table>

      <h3 class="block-title">生效绿线切片（地图引用）</h3>
      <table class="data-table">
        <thead>
          <tr><th>地图引用编号</th><th>区域编号</th><th>版本键</th><th>桩号区间</th><th>迁入责任班组</th></tr>
        </thead>
        <tbody>
          <tr v-for="ref in view.生效引用" :key="ref.地图引用编号">
            <td>{{ ref.地图引用编号 }}</td>
            <td>{{ ref.区域编号 }}</td>
            <td>{{ ref.版本键 }}</td>
            <td>{{ ref.桩号区间 }}</td>
            <td>{{ ref.迁入责任班组 ?? '—' }}</td>
          </tr>
          <tr v-if="!view.生效引用.length"><td colspan="5" class="empty-state">暂无生效引用</td></tr>
        </tbody>
      </table>

      <h3 class="block-title">班组工作面与排班卡（随发布结论回写）</h3>
      <table class="data-table">
        <thead>
          <tr><th>班组名称</th><th>当前版本键</th><th>工作面</th><th>排班卡</th><th>负责区域</th><th>状态</th></tr>
        </thead>
        <tbody>
          <tr v-for="crew in view.班组工作面" :key="crew.班组名称">
            <td>{{ crew.班组名称 }}</td>
            <td>{{ crew.当前版本键 }}</td>
            <td>{{ crew.工作面 }}</td>
            <td>{{ crew.排班卡 }}</td>
            <td>{{ crew.负责区域.join('、') }}</td>
            <td>{{ crew.状态 }}</td>
          </tr>
        </tbody>
      </table>

      <h3 class="block-title">地图待办</h3>
      <table class="data-table">
        <thead>
          <tr><th>待办编号</th><th>区域编号</th><th>版本键</th><th>事项</th><th>状态</th><th>发布单</th></tr>
        </thead>
        <tbody>
          <tr v-for="todo in view.地图待办" :key="todo.待办编号"
              :class="{ 'todo-done': todo.状态 === '已完成', 'todo-open': todo.状态 === '待处理' }">
            <td>{{ todo.待办编号 }}</td>
            <td>{{ todo.区域编号 }}</td>
            <td>{{ todo.版本键 }}</td>
            <td>{{ todo.事项 }}</td>
            <td>{{ todo.状态 }}</td>
            <td>{{ todo.发布单编号 ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </template>

    <PublishWizard v-if="wizardRegion" :region-code="wizardRegion" @close="wizardRegion = ''" @changed="reload" />
  </section>
</template>

<script setup lang="ts">
import { ref } from 'vue'

import { fetchMapView, type MapView } from '@/api/greenPublish'
import PublishWizard from './PublishWizard.vue'

const view = ref<MapView | null>(null)
const errorMessage = ref('')
const wizardRegion = ref('')

function openWizard(regionCode: string) {
  wizardRegion.value = regionCode
}

async function reload() {
  errorMessage.value = ''
  try {
    view.value = await fetchMapView()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '地图数据读取失败'
  }
}

reload()
</script>

<style scoped>
.page-actions { display: flex; gap: 8px; }
.block-title { margin: 16px 0 8px; font-size: 15px; }
.version-key { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; background: #eff6ff; color: #1d4ed8; border-radius: 4px; padding: 1px 6px; }
.todo-done td { color: #047857; }
.todo-open td { color: #92400e; }
</style>
