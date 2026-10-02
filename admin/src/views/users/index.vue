<template>
  <div class="users-page">
    <div class="page-header">
      <h2 class="page-title">用户管理</h2>
    </div>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="filters" class="filter-form">
        <el-form-item label="搜索">
          <el-input
            v-model="filters.keyword"
            placeholder="用户ID / 昵称"
            clearable
            style="width: 200px"
            @keyup.enter="handleSearch"
          />
        </el-form-item>
        <el-form-item label="注册时间">
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
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="table-card">
      <div class="table-header">
        <span class="table-count">共 {{ total }} 个用户</span>
      </div>
      <el-table :data="users" v-loading="loading" stripe>
        <el-table-column prop="id" label="用户ID" width="80" align="center" />
        <el-table-column prop="nickname" label="昵称" min-width="120">
          <template #default="{ row }">
            <span class="cell-name">{{ row.nickname || '-' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="平台" width="100" align="center">
          <template #default="{ row }">
            <el-tag size="small" :type="row.platform === 'wechat_mini' ? 'success' : ''" effect="light">
              {{ platformMap[row.platform] || row.platform }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="注册时间" width="170" align="center">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="最近登录" width="170" align="center">
          <template #default="{ row }">{{ row.last_login_at ? formatTime(row.last_login_at) : '-' }}</template>
        </el-table-column>
        <el-table-column label="免费次数" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="row.free_count > 0 ? 'success' : 'info'" size="small" effect="light">
              {{ row.free_count === -1 ? '∞' : row.free_count }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="余额" width="90" align="center">
          <template #default="{ row }">¥{{ row.balance }}</template>
        </el-table-column>
        <el-table-column label="状态" width="80" align="center">
          <template #default="{ row }">
            <el-tag :type="row.is_active ? 'success' : 'danger'" size="small" effect="light">
              {{ row.is_active ? '正常' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="240" fixed="right" align="center">
          <template #default="{ row }">
            <el-button link type="primary" @click="showDetail(row.id)">详情</el-button>
            <el-button
              v-if="authStore.isSuperAdmin"
              link
              type="warning"
              @click="showFreeCountDialog(row)"
            >调次数</el-button>
            <el-popconfirm
              v-if="authStore.isSuperAdmin"
              :title="`确定${row.is_active ? '禁用' : '启用'}该用户？`"
              @confirm="handleToggle(row.id, !row.is_active)"
            >
              <template #reference>
                <el-button link :type="row.is_active ? 'danger' : 'success'">
                  {{ row.is_active ? '禁用' : '启用' }}
                </el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <Pagination v-model:page="page" v-model:page-size="pageSize" :total="total" @change="loadData" />
    </el-card>

    <el-dialog v-model="detailVisible" title="用户详情" width="800px" destroy-on-close>
      <UsersDetail v-if="detailVisible" :user-id="currentUserId" />
    </el-dialog>

    <el-dialog v-model="freeCountVisible" title="调整免费次数" width="420px">
      <el-form :model="freeCountForm" label-width="120px">
        <el-form-item label="当前免费次数">
          <span class="text-bold">{{ freeCountForm.currentCount }}</span>
        </el-form-item>
        <el-form-item label="调整为">
          <el-input-number v-model="freeCountForm.newCount" :min="0" :max="99999" style="width: 200px" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="freeCountVisible = false">取消</el-button>
        <el-button type="primary" :loading="freeCountSubmitting" @click="handleFreeCountSubmit">确认</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getUsers, adjustFreeCount, toggleUser } from '@/api/users'
import type { User, UserListParams } from '@/api/users'
import { useAuthStore } from '@/stores/auth'
import dayjs from 'dayjs'
import Pagination from '@/components/Pagination.vue'
import UsersDetail from './detail.vue'

const authStore = useAuthStore()

const users = ref<User[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const dateRange = ref<[string, string] | null>(null)

const filters = reactive<UserListParams>({ keyword: '' })

const platformMap: Record<string, string> = {
  wechat_mini: '小程序', wechat_web: '微信Web', alipay_web: '支付宝',
}

const detailVisible = ref(false)
const currentUserId = ref(0)

const freeCountVisible = ref(false)
const freeCountSubmitting = ref(false)
const freeCountForm = reactive({ userId: 0, currentCount: 0, newCount: 0 })

onMounted(loadData)

async function loadData() {
  loading.value = true
  try {
    const params: UserListParams = { page: page.value, page_size: pageSize.value, ...filters }
    if (dateRange.value) { params.start_date = dateRange.value[0]; params.end_date = dateRange.value[1] }
    const result = await getUsers(params)
    users.value = result.items
    total.value = result.total
  } catch { users.value = []; total.value = 0 }
  loading.value = false
}

function handleSearch() { page.value = 1; loadData() }
function handleReset() { dateRange.value = null; filters.keyword = ''; page.value = 1; loadData() }
function showDetail(id: number) { currentUserId.value = id; detailVisible.value = true }
function showFreeCountDialog(user: User) {
  freeCountForm.userId = user.id; freeCountForm.currentCount = user.free_count; freeCountForm.newCount = user.free_count
  freeCountVisible.value = true
}
async function handleFreeCountSubmit() {
  freeCountSubmitting.value = true
  try { await adjustFreeCount(freeCountForm.userId, freeCountForm.newCount); ElMessage.success('调整成功'); freeCountVisible.value = false; loadData() } catch { ElMessage.error('调整失败') }
  freeCountSubmitting.value = false
}
async function handleToggle(id: number, isActive: boolean) {
  try { await toggleUser(id, isActive); ElMessage.success(isActive ? '已启用' : '已禁用'); loadData() } catch { ElMessage.error('操作失败') }
}
function formatTime(t: string): string { return dayjs(t).format('YYYY-MM-DD HH:mm:ss') }
</script>

<style scoped>
.users-page { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--color-text); letter-spacing: -0.02em; }
.filter-card { margin-bottom: 16px; }
.filter-form { margin-bottom: 0; }
.filter-form :deep(.el-form-item) { margin-bottom: 0; }
.table-card { margin-bottom: 0; }
.table-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.table-count { font-size: 14px; font-weight: 600; color: var(--color-text-secondary); }
.cell-name { font-weight: 600; }
.text-bold { font-weight: 600; }
</style>