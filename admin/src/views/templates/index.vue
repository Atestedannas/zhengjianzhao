<template>
  <div class="templates-page">
    <div class="page-header">
      <h2 class="page-title">模板管理</h2>
      <el-button v-if="authStore.isSuperAdmin" type="primary" @click="router.push('/templates/edit')">
        <el-icon style="margin-right: 4px"><Plus /></el-icon>新增模板
      </el-button>
    </div>

    <el-card shadow="never">
      <div class="table-header">
        <span class="table-count">共 {{ templates.length }} 个模板</span>
      </div>
      <el-table :data="templates" v-loading="loading" stripe>
        <el-table-column prop="id" label="ID" width="70" align="center" />
        <el-table-column prop="name" label="模板名称" min-width="150">
          <template #default="{ row }">
            <span class="cell-name">{{ row.name }}</span>
          </template>
        </el-table-column>
        <el-table-column label="像素" width="130" align="center">
          <template #default="{ row }">
            <span class="cell-mono">{{ row.width_px }} × {{ row.height_px }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="dpi" label="DPI" width="70" align="center" />
        <el-table-column label="大小范围" width="140" align="center">
          <template #default="{ row }">{{ row.min_kb }} - {{ row.max_kb }} KB</template>
        </el-table-column>
        <el-table-column label="背景色" min-width="150">
          <template #default="{ row }">
            <el-tag
              v-for="color in row.allowed_bg_colors"
              :key="color"
              size="small"
              effect="light"
              style="margin-right: 4px"
            >{{ colorMap[color] || color }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="output_format" label="格式" width="70" align="center" />
        <el-table-column label="启用" width="80" align="center">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_active"
              @change="(val: boolean) => handleToggle(row.id, val)"
              :disabled="!authStore.isSuperAdmin"
              size="small"
            />
          </template>
        </el-table-column>
        <el-table-column v-if="authStore.isSuperAdmin" label="操作" width="160" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/templates/edit/${row.id}`)">编辑</el-button>
            <el-popconfirm
              title="确定删除该模板？"
              confirm-button-text="确定"
              cancel-button-text="取消"
              @confirm="handleDelete(row.id)"
            >
              <template #reference>
                <el-button link type="danger">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus } from '@element-plus/icons-vue'
import { getTemplates, deleteTemplate, toggleTemplate } from '@/api/templates'
import type { Template } from '@/api/templates'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const templates = ref<Template[]>([])
const loading = ref(false)

const colorMap: Record<string, string> = {
  white: '白色', blue: '蓝色', red: '红色', keep: '保持原色',
}

onMounted(loadData)

async function loadData() {
  loading.value = true
  try { templates.value = await getTemplates() } catch { templates.value = [] }
  loading.value = false
}

async function handleDelete(id: number) {
  try { await deleteTemplate(id); ElMessage.success('删除成功'); loadData() } catch { ElMessage.error('删除失败') }
}

async function handleToggle(id: number, val: boolean) {
  try { await toggleTemplate(id, val); ElMessage.success(val ? '已启用' : '已禁用'); loadData() } catch { ElMessage.error('操作失败') }
}
</script>

<style scoped>
.templates-page { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--color-text); letter-spacing: -0.02em; }
.table-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.table-count { font-size: 14px; font-weight: 600; color: var(--color-text-secondary); }
.cell-name { font-weight: 600; }
.cell-mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 13px; }
</style>