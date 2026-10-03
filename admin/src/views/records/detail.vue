<template>
  <div class="record-detail" v-loading="loading">
    <template v-if="record">
      <!-- basic info -->
      <el-descriptions title="基本信息" :column="2" border style="margin-bottom: 20px">
        <el-descriptions-item label="记录ID">{{ record.id }}</el-descriptions-item>
        <el-descriptions-item label="用户">{{ record.user_nickname || '匿名' }}</el-descriptions-item>
        <el-descriptions-item label="使用模板">{{ record.template_name || '自定义' }}</el-descriptions-item>
        <el-descriptions-item label="原始大小">{{ formatSize(record.original_size) }}</el-descriptions-item>
        <el-descriptions-item label="输出大小">{{ formatSize(record.result_size) }}</el-descriptions-item>
        <el-descriptions-item label="输出像素">{{ record.result_pixels || '-' }}</el-descriptions-item>
        <el-descriptions-item label="DPI">{{ record.result_dpi || '-' }}</el-descriptions-item>
        <el-descriptions-item label="背景色">{{ record.bg_color || '-' }}</el-descriptions-item>
        <el-descriptions-item label="处理耗时">{{ (record.processing_time_ms / 1000).toFixed(2) }}s</el-descriptions-item>
        <el-descriptions-item label="付费状态">
          <el-tag :type="record.is_paid ? 'warning' : 'success'" size="small">
            {{ record.is_paid ? `付费 ¥${record.paid_amount}` : '免费' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="处理状态">
          <el-tag :type="recordStatusTagType(record.status)" size="small">
            {{ recordStatusText(record.status) }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="处理时间">{{ formatTime(record.created_at) }}</el-descriptions-item>
      </el-descriptions>

      <!-- params -->
      <el-card shadow="never" style="margin-bottom: 20px">
        <template #header>处理参数</template>
        <pre class="params-json">{{ JSON.stringify(record.request_params, null, 2) }}</pre>
      </el-card>

      <!-- error -->
      <el-alert
        v-if="record.status === 'failed' && record.error_message"
        :title="record.error_message"
        type="error"
        show-icon
        :closable="false"
        style="margin-bottom: 20px"
      />
    </template>
    <el-empty v-else description="无数据" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { getRecordDetail } from '@/api/records'
import type { ProcessRecord } from '@/api/records'
import { recordStatusTagType, recordStatusText } from '@/utils/recordStatus'
import dayjs from 'dayjs'

interface Props {
  recordId: number
}

const props = defineProps<Props>()

interface RecordDetail extends ProcessRecord {
  request_params: any
}

const record = ref<RecordDetail | null>(null)
const loading = ref(false)

async function loadDetail() {
  if (!props.recordId) return
  loading.value = true
  try {
    record.value = await getRecordDetail(props.recordId) as RecordDetail
  } catch {
    record.value = null
  }
  loading.value = false
}

onMounted(loadDetail)
watch(() => props.recordId, loadDetail)

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1048576).toFixed(1)} MB`
}

function formatTime(t: string): string {
  return dayjs(t).format('YYYY-MM-DD HH:mm:ss')
}
</script>

<style scoped>
.params-json {
  background: #f5f7fa;
  padding: 16px;
  border-radius: 6px;
  font-size: 13px;
  max-height: 300px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
