<template>
  <section class="page" data-module="version">
    <header class="page-head">
      <div>
        <h2>产品版本管理管理</h2>
        <p class="page-desc">维护产品版本的变更日志、兼容标识与灰度策略，为产品版本管理提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记产品版本</button>
        <button class="btn" type="button" @click="exportRows">导出产品版本管理清单</button>
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
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="row in rows" :key="String(row.id)">
          <tr>
            <td v-for="column in columns" :key="column">{{ displayCell(row, column) }}</td>
            <td class="row-actions">
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
          <tr v-if="snapshotOf(row)" class="snapshot-row">
            <td :colspan="columns.length + 1">
              <div class="snapshot-panel">
                <span class="snapshot-title">兼容快照 {{ snapshotOf(row)?.快照编号 }}</span>
                <span>兼容标识：{{ snapshotOf(row)?.兼容标识 }}</span>
                <span class="snapshot-adopted">
                  采用：{{ snapshotOf(row)?.采用规则?.来源渠道 }}
                  {{ snapshotOf(row)?.采用规则?.灰度比例 }}（规则版本 {{ snapshotOf(row)?.采用规则?.规则版本 }}）
                </span>
                <span
                  v-for="rule in snapshotOf(row)?.忽略规则 ?? []"
                  :key="String(rule.来源渠道)"
                  class="snapshot-ignored"
                >
                  忽略：{{ rule.来源渠道 }} {{ rule.灰度比例 }}（规则版本 {{ rule.规则版本 }}）
                </span>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无产品版本管理数据，可先登记产品版本</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条产品版本管理记录</span>
      <span v-if="noticeMessage" class="notice-text">{{ noticeMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

interface GrayRule {
  来源渠道?: string
  灰度比例?: string
  规则版本?: number
}

interface CompatSnapshot {
  快照编号: string
  版本编号?: string
  兼容标识: string
  采用规则: GrayRule
  忽略规则: GrayRule[]
  生成时间?: string
}

type Row = Record<string, unknown> & { id?: string | number }

const ENDPOINT = '/api/version'
const columns = ["版本编号", "产品名称", "版本号", "变更日志", "兼容标识", "灰度比例", "全量时间", "版本状态"]
const actions = ["灰度发布", "全量发布", "归档版本"]
const statuses = ["草稿", "灰度中", "全量发布", "已归档"]
const stats = [{"label": "产品版本总数", "value": 0}, {"label": "灰度中版本", "value": 0}, {"label": "全量发布版本", "value": 0}]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const noticeMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)

function snapshotOf(row: Row): CompatSnapshot | null {
  const snapshot = row['兼容快照']
  if (snapshot && typeof snapshot === 'object') {
    return snapshot as CompatSnapshot
  }
  return null
}

function displayCell(row: Row, column: string): string {
  const value = row[column]
  if (value === null || value === undefined || value === '') {
    return '—'
  }
  if (typeof value === 'object') {
    return JSON.stringify(value)
  }
  return String(value)
}

function resetFilters() {
  filters.value = {}
  errorMessage.value = ''
  noticeMessage.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  errorMessage.value = '产品版本登记入口尚未接入审批流'
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  noticeMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const result = await response.json().catch(() => null)
    if (!response.ok) {
      throw new Error(result?.detail ?? '产品版本管理动作未生效，请稍后重试')
    }
    if (result?.ok) {
      noticeMessage.value = result.message ?? '产品版本管理动作已生效'
    } else {
      errorMessage.value = result?.message ?? '产品版本管理动作未生效，请稍后重试'
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '产品版本管理操作失败'
  }
}

async function reload() {
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('产品版本列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '产品版本管理列表读取失败'
  }
}

onMounted(reload)
</script>
