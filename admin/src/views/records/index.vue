<template>
  <div class="records-page">
    <div class="page-header">
      <h2 class="page-title">处理记录</h2>
      <ExportButton :export-fn="doExport" filename="处理记录.xlsx" />
    </div>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="filters" class="filter-form">
        <el-form-item label="时间范围">
          <el-date-picker
            v-model="dateRange"
            type="daterange"
            range-separator="至"
            start-placeholder="开始日期"
            end-placeholder="结束日期"
            value-format="YYYY-MM-DD"
            style="width: 260px"
          />
        </el-form-item>
        <el-form-item label="模板">
          <el-select v-model="filters.template_id" placeholder="全部" clearable style="width: 160px">
            <el-option v-for="t in templates" :key="t.id" :label="t.name" :value="t.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" clearable style="width: 120px">
            <el-option label="成功" value="success" />
            <el-option label="失败" value="failed" />
          </el-select>
        </el-form-item>
        <el-form-item label="付费">
          <el-select v-model="filters.is_paid" placeholder="全部" clearable style="width: 120px">
            <el-option label="付费" :value="true" />
            <el-option label="免费" :value="false" />
          </el-select>
        </el-form-item>
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="table-card">
      <div class="table-header">
        <span class="table-count">共 {{ total }} 条记录</span>
      </div>
      <el-table :data="records" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="80" align="center" />
        <el-table-column prop="user_nickname" label="用户" min-width="120">
          <template #default="{ row }">
            <span class="cell-user">{{ row.user_nickname || '匿名用户' }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="template_name" label="模板" min-width="120">
          <template #default="{ row }">
            <el-tag v-if="row.template_name" type="info" size="small" effect="light">{{ row.template_name }}</el-tag>
            <span v-else class="text-muted">-</span>
          </template>
        </el-table-column>
        <el-table-column label="原始大小" width="100" align="center">
          <template #default="{ row }">{{ formatSize(row.original_size) }}</template>
        </el-table-column>
        <el-table-column label="输出大小" width="100" align="center">
          <template #default="{ row }">{{ formatSize(row.result_size) }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="90" align="center">
          <template #default="{ row }">{{ (row.processing_time_ms / 1000).toFixed(2) }}s</template>
        </el-table-column>
        <el-table-column prop="status" label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="recordStatusTagType(row.status)" size="small" effect="light">
              {{ recordStatusText(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170" align="center">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="90" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="showDetail(row.id)">详情</el-button>
          </template>
        </el-table-column>
      </el-table>
      <Pagination
        v-model:page="page"
        v-model:page-size="pageSize"
        :total="total"
        @change="loadData"
      />
    </el-card>

    <el-dialog v-model="detailVisible" title="记录详情" width="800px" destroy-on-close>
      <RecordsDetail v-if="detailVisible" :record-id="currentRecordId" />
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { getRecords, exportRecords } from '@/api/records'
import { getTemplates } from '@/api/templates'
import type { ProcessRecord, RecordListParams } from '@/api/records'
import { recordStatusTagType, recordStatusText } from '@/utils/recordStatus'
import type { Template } from '@/api/templates'
import dayjs from 'dayjs'
import Pagination from '@/components/Pagination.vue'
import ExportButton from '@/components/ExportButton.vue'
import RecordsDetail from './detail.vue'

const records = ref<ProcessRecord[]>([])
const templates = ref<Template[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const dateRange = ref<[string, string] | null>(null)

const filters = reactive<{
  template_id?: number
  status: string
  is_paid: boolean | undefined
}>({
  template_id: undefined,
  status: '',
  is_paid: undefined,
})

const detailVisible = ref(false)
const currentRecordId = ref(0)

onMounted(async () => {
  await Promise.all([loadData(), loadTemplates()])
})

async function loadData() {
  loading.value = true
  try {
    const params: RecordListParams = {
      page: page.value,
      page_size: pageSize.value,
      template_id: filters.template_id,
    }
    if (filters.status) params.status = filters.status
    if (filters.is_paid !== undefined) params.is_paid = filters.is_paid
    if (dateRange.value) {
      params.start_date = dateRange.value[0]
      params.end_date = dateRange.value[1]
    }
    const result = await getRecords(params)
    records.value = result.items
    total.value = result.total
  } catch {
    records.value = []
    total.value = 0
  }
  loading.value = false
}

async function loadTemplates() {
  try { templates.value = await getTemplates() } catch { /* */ }
}

function handleSearch() { page.value = 1; loadData() }
function handleReset() {
  dateRange.value = null
  filters.template_id = undefined
  filters.status = ''
  filters.is_paid = undefined
  page.value = 1
  loadData()
}

function showDetail(id: number) { currentRecordId.value = id; detailVisible.value = true }

async function doExport(): Promise<Blob> {
  const params: RecordListParams = {
    template_id: filters.template_id,
  }
  if (filters.status) params.status = filters.status
  if (filters.is_paid !== undefined) params.is_paid = filters.is_paid
  if (dateRange.value) { params.start_date = dateRange.value[0]; params.end_date = dateRange.value[1] }
  return await exportRecords(params)
}

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function formatTime(t: string): string { return dayjs(t).format('YYYY-MM-DD HH:mm:ss') }
</script>

<style scoped>
.records-page {
  animation: fadeIn 0.3s ease;
}
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.page-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--color-text);
  letter-spacing: -0.02em;
}
.filter-card { margin-bottom: 16px; }
.filter-form { margin-bottom: 0; }
.filter-form :deep(.el-form-item) { margin-bottom: 0; }
.table-card { margin-bottom: 0; }
.table-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.table-count { font-size: 14px; font-weight: 600; color: var(--color-text-secondary); }
.cell-user { font-weight: 500; }
.text-muted { color: var(--color-text-muted); }
</style>