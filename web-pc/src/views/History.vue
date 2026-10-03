<template>
  <div class="history-page">
    <div class="container">
      <div class="page-header">
        <div>
          <h1 class="page-title">处理记录</h1>
          <p class="page-desc">查看你最近的照片处理记录</p>
        </div>
        <el-button type="primary" @click="goEditor">
          <el-icon><EditPen /></el-icon>
          新建处理
        </el-button>
      </div>

      <div class="history-content">
        <!-- 加载状态 -->
        <div v-if="loading" class="loading-state">
          <el-icon :size="48" class="spinning"><Loading /></el-icon>
          <p>加载中...</p>
        </div>

        <!-- 空状态 -->
        <div v-else-if="photoStore.historyList.length === 0" class="empty-state">
          <el-icon :size="80" color="#dcdfe6"><Document /></el-icon>
          <h3 class="empty-title">暂无处理记录</h3>
          <p class="empty-desc">你还没有处理过照片，快去试试吧</p>
          <el-button type="primary" size="large" @click="goEditor">
            <el-icon><Camera /></el-icon>
            立即制作
          </el-button>
        </div>

        <!-- 记录列表 -->
        <div v-else class="history-grid">
          <div
            v-for="item in photoStore.historyList"
            :key="item.id"
            class="history-card"
          >
            <div class="history-preview">
              <template v-if="item.has_preview">
                <AuthImage :src="item.thumb_url" alt="处理结果" class="preview-img" />
              </template>
              <template v-else>
                <div class="preview-placeholder">
                  <el-icon :size="40" color="#c0c4cc"><Picture /></el-icon>
                </div>
              </template>
              <div class="status-badge" :class="item.status">
                {{ getStatusLabel(item.status) }}
              </div>
            </div>

            <div class="history-info">
              <div class="history-title">
                {{ item.template_name || '自定义处理' }}
              </div>
              <div class="history-meta">
                <span class="meta-item">
                  <el-icon><Rank /></el-icon>
                  {{ item.result_pixels }}
                </span>
                <span class="meta-item">
                  <el-icon><Brush /></el-icon>
                  {{ getColorLabel(item.bg_color) }}
                </span>
              </div>
              <div class="history-meta">
                <span class="meta-item">
                  <el-icon><Files /></el-icon>
                  {{ formatSize(item.result_size) }}
                </span>
                <span class="meta-item">
                  <el-icon><Timer /></el-icon>
                  {{ formatTime(item.processing_time_ms) }}
                </span>
              </div>
              <div class="history-time">
                <el-icon><Clock /></el-icon>
                {{ formatDate(item.created_at) }}
              </div>
            </div>

            <div class="history-actions">
              <el-button
                v-if="item.status === 'success'"
                type="primary"
                size="small"
                @click="handleDownload(item.id)"
              >
                <el-icon><Download /></el-icon>
                下载
              </el-button>
              <el-button
                v-if="item.status === 'success'"
                size="small"
                @click="handlePreview(item.id)"
              >
                <el-icon><View /></el-icon>
                查看
              </el-button>
              <el-button
                v-if="item.status === 'success'"
                size="small"
                @click="handleReEdit(item)"
              >
                <el-icon><RefreshRight /></el-icon>
                再编辑
              </el-button>
            </div>
          </div>
        </div>

        <!-- 加载更多 -->
        <div v-if="photoStore.historyList.length > 0" class="load-more">
          <el-divider content-position="center">
            <span class="divider-text">仅显示最近 10 条记录</span>
          </el-divider>
        </div>
      </div>
    </div>

    <!-- 图片预览弹窗 -->
    <el-dialog
      v-model="previewVisible"
      title="图片预览"
      width="auto"
      :close-on-click-modal="true"
      center
    >
      <div class="preview-dialog">
        <AuthImage :src="previewUrl" alt="预览" class="preview-dialog-img" />
      </div>
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button type="primary" @click="handleDownloadFromPreview">
          <el-icon><Download /></el-icon>
          下载图片
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import dayjs from 'dayjs'
import {
  EditPen,
  Camera,
  Document,
  Picture,
  Download,
  View,
  RefreshRight,
  Clock,
  Loading,
  Rank,
  Brush,
  Files,
  Timer,
} from '@element-plus/icons-vue'
import { usePhotoStore } from '@/stores/photo'
import { getDownloadUrl, getProtectedFile, type HistoryItem } from '@/api'
import AuthImage from '@/components/AuthImage.vue'
import { saveBlob } from '@/utils/file'

const router = useRouter()
const photoStore = usePhotoStore()

const loading = ref(true)
const previewVisible = ref(false)
const previewUrl = ref('')
const previewRecordId = ref<number | null>(null)

const colorLabels: Record<string, string> = {
  white: '白色背景',
  blue: '蓝色背景',
  red: '红色背景',
  gray: '灰色背景',
  black: '黑色背景',
}

function getColorLabel(color: string): string {
  return colorLabels[color] || color
}

function getStatusLabel(status: string): string {
  const map: Record<string, string> = {
    success: '成功',
    failed: '失败',
    processing: '处理中',
    // 异步改造后新增的待处理状态，同样展示为「处理中」
    pending: '处理中',
  }
  return map[status] || status
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function formatTime(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatDate(dateStr: string | null): string {
  if (!dateStr) return '-'
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

onMounted(async () => {
  try {
    await photoStore.loadHistory()
  } finally {
    loading.value = false
  }
})

function goEditor() {
  router.push('/editor')
}

function handleDownload(recordId: number) {
  // 下载接口要 Bearer 鉴权，<a href> 带不上请求头，所以取 Blob 再保存
  getProtectedFile(getDownloadUrl(recordId))
    .then(({ blob, filename }) => saveBlob(blob, filename || `证件照_${recordId}.jpg`))
    .catch(() => {
      // 失败提示由响应拦截器统一处理
    })
}

function handlePreview(recordId: number) {
  previewRecordId.value = recordId
  previewUrl.value = `/api/v1/process/${recordId}/preview`
  previewVisible.value = true
}

function handleDownloadFromPreview() {
  if (previewRecordId.value) {
    handleDownload(previewRecordId.value)
  }
}

function handleReEdit(item: HistoryItem) {
  // 跳转到编辑器，可以带上模板 ID
  if (item.template_id) {
    router.push({ path: '/editor', query: { template: String(item.template_id) } })
  } else {
    router.push('/editor')
  }
}
</script>

<style scoped>
.history-page {
  padding: 40px 0;
  min-height: calc(100vh - 64px);
  background: #f5f7fa;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 32px;
}

.page-title {
  font-size: 28px;
  font-weight: 700;
  color: #1f2d3d;
  margin: 0 0 6px 0;
}

.page-desc {
  font-size: 14px;
  color: #909399;
  margin: 0;
}

.history-content {
  min-height: 400px;
}

/* Loading & Empty */
.loading-state,
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 80px 0;
}

.loading-state {
  color: #409eff;
}

.spinning {
  animation: spin 1s linear infinite;
  margin-bottom: 16px;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.empty-title {
  font-size: 18px;
  color: #606266;
  margin: 20px 0 8px 0;
}

.empty-desc {
  font-size: 14px;
  color: #909399;
  margin: 0 0 24px 0;
}

/* History Grid */
.history-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 20px;
}

.history-card {
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  transition: all 0.3s;
  display: flex;
  flex-direction: column;
}

.history-card:hover {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.history-preview {
  position: relative;
  width: 100%;
  aspect-ratio: 3/4;
  background: #f5f7fa;
  overflow: hidden;
}

.preview-img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.preview-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-badge {
  position: absolute;
  top: 12px;
  right: 12px;
  padding: 4px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
  background: rgba(255, 255, 255, 0.9);
  backdrop-filter: blur(4px);
}

.status-badge.success {
  color: #67c23a;
}

.status-badge.failed {
  color: #f56c6c;
}

.status-badge.processing,
.status-badge.pending {
  color: #e6a23c;
}

.history-info {
  padding: 16px;
  flex: 1;
}

.history-title {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
  margin-bottom: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.history-meta {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
}

.meta-item {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #909399;
}

.meta-item .el-icon {
  font-size: 13px;
}

.history-time {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 8px;
}

.history-actions {
  padding: 12px 16px 16px;
  display: flex;
  gap: 8px;
  border-top: 1px solid #f0f2f5;
}

.history-actions .el-button {
  flex: 1;
}

.load-more {
  margin-top: 32px;
}

.divider-text {
  color: #c0c4cc;
  font-size: 13px;
}

/* Preview Dialog */
.preview-dialog {
  max-width: 600px;
  max-height: 70vh;
  display: flex;
  justify-content: center;
}

.preview-dialog-img {
  max-width: 100%;
  max-height: 70vh;
  object-fit: contain;
}

@media (max-width: 768px) {
  .history-page {
    padding: 20px 0;
  }

  .page-header {
    flex-direction: column;
    align-items: flex-start;
    gap: 16px;
  }

  .page-title {
    font-size: 22px;
  }

  .history-grid {
    grid-template-columns: 1fr;
  }
}
</style>
