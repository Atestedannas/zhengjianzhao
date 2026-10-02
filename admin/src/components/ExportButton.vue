<template>
  <el-button type="primary" :loading="loading" @click="handleExport">
    <el-icon><Download /></el-icon>
    导出Excel
  </el-button>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'

interface Props {
  exportFn: () => Promise<Blob>
  filename?: string
}

const props = withDefaults(defineProps<Props>(), {
  filename: 'export.xlsx',
})

const loading = ref(false)

async function handleExport() {
  loading.value = true
  try {
    const blob = await props.exportFn()
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = props.filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    ElMessage.success('导出成功')
  } catch {
    ElMessage.error('导出失败')
  } finally {
    loading.value = false
  }
}
</script>
