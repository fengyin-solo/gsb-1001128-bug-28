<template>
  <section class="page" data-module="green-map">
    <header class="page-head">
      <div>
        <h2>地图待办 · 区域切片发布</h2>
        <p class="page-desc">地图只展示当前生效版本的切片引用；每条待办都带版本键，点「去发布」进入与列表页、详情页同一张区域切片发布单。</p>
      </div>
      <div class="page-actions">
        <router-link class="btn" to="/green">返回绿化列表</router-link>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">生效地图待办</span>
        <strong class="stat-value">{{ todos.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">涉及区域</span>
        <strong class="stat-value">{{ regionCount }}</strong>
      </article>
    </div>

    <p v-if="message" class="notice" :class="ok ? 'notice-ok' : 'notice-err'">{{ message }}</p>

    <table class="data-table">
      <thead>
        <tr>
          <th>待办编号</th>
          <th>区域</th>
          <th>版本键</th>
          <th>切片</th>
          <th>工作面</th>
          <th>责任班组</th>
          <th>地图引用</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="todo in todos" :key="todo['待办编号']">
          <td>{{ todo['待办编号'] }}</td>
          <td>{{ todo['区域编号'] }} {{ todo['区域名称'] ?? '' }}</td>
          <td><span class="mono">{{ todo['版本键'] }}</span></td>
          <td>{{ todo['切片编号'] }}</td>
          <td>{{ todo['工作面'] }}</td>
          <td>{{ todo['责任班组'] }}</td>
          <td class="mono small">{{ todo['地图引用'] }}</td>
          <td class="row-actions">
            <router-link class="link" :to="`/green/${todo['区域台账ID']}`">去发布/查看</router-link>
          </td>
        </tr>
        <tr v-if="!todos.length">
          <td colspan="8" class="empty-state">暂无生效地图待办，撤回或未成功的发布不占用地图</td>
        </tr>
      </tbody>
    </table>

    <article class="panel">
      <h3>从地图发起新版本发布</h3>
      <p class="hint">输入区域编号与版本后，后端按「区域@版本」键查找：已有发布单则直接复用（与详情页一致），没有才新建。</p>
      <form class="filter-bar" @submit.prevent="enterFromMap">
        <label class="filter-item"><span>区域编号</span><input v-model="entry.区域编号" placeholder="GREE-0002" /></label>
        <label class="filter-item"><span>版本</span><input v-model="entry.版本" placeholder="v2" /></label>
        <button class="btn primary" type="submit">进入发布单</button>
      </form>
    </article>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { request } from '@/api/client'

type Dict = Record<string, any>

const router = useRouter()
const todos = ref<Dict[]>([])
const message = ref('')
const ok = ref(true)
const entry = reactive({ 区域编号: '', 版本: '' })

const regionCount = computed(() => new Set(todos.value.map((t) => t['区域编号'])).size)

async function reload() {
  try {
    const res = await request('/api/green/map/todos')
    if (!res.ok) throw new Error('地图待办读取失败')
    const payload = await res.json()
    todos.value = payload.items ?? []
  } catch (error) {
    message.value = error instanceof Error ? error.message : '地图待办读取失败'
    ok.value = false
  }
}

async function enterFromMap() {
  if (!entry.区域编号.trim() || !entry.版本.trim()) {
    message.value = '请填写区域编号与版本'
    ok.value = false
    return
  }
  try {
    const res = await request('/api/green/map/entry', {
      method: 'POST',
      body: JSON.stringify({ values: { 区域编号: entry.区域编号.trim(), 版本: entry.版本.trim() } }),
    })
    const payload = await res.json()
    message.value = payload.message ?? ''
    ok.value = Boolean(payload.ok)
    if (payload.ok && payload.entry?.['区域台账ID'] != null) {
      await router.push(`/green/${payload.entry['区域台账ID']}`)
    }
  } catch (error) {
    message.value = error instanceof Error ? error.message : '地图入口请求失败'
    ok.value = false
  }
}

onMounted(reload)
</script>

<style scoped>
.panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; margin-top: 14px; }
.panel h3 { margin: 0 0 6px; font-size: 15px; }
.hint { font-size: 12px; color: var(--muted); }
.mono { font-family: ui-monospace, monospace; font-size: 12px; }
.small { color: var(--muted); }
.notice { font-size: 13px; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--border); }
.notice-ok { background: #ecfdf3; border-color: #a6f4c5; color: #067647; }
.notice-err { background: #fef3f2; border-color: #fecdca; color: #b42318; }
</style>
