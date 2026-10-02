<template>
  <div class="orders-page">
    <div class="page-header">
      <h2 class="page-title">订单管理</h2>
    </div>

    <el-card shadow="never" class="filter-card">
      <el-form :inline="true" :model="filters" class="filter-form">
        <el-form-item label="状态">
          <el-select v-model="filters.status" placeholder="全部" clearable style="width: 130px">
            <el-option label="待支付" value="pending" />
            <el-option label="已支付" value="paid" />
            <el-option label="已退款" value="refunded" />
            <el-option label="已关闭" value="closed" />
          </el-select>
        </el-form-item>
        <el-form-item label="支付方式">
          <el-select v-model="filters.pay_method" placeholder="全部" clearable style="width: 140px">
            <el-option label="微信JSAPI" value="wechat_jsapi" />
            <el-option label="微信扫码" value="wechat_native" />
            <el-option label="支付宝" value="alipay" />
          </el-select>
        </el-form-item>
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
        <el-form-item>
          <el-button type="primary" @click="handleSearch">搜索</el-button>
          <el-button @click="handleReset">重置</el-button>
        </el-form-item>
      </el-form>
    </el-card>

    <el-card shadow="never" class="table-card">
      <div class="table-header">
        <span class="table-count">共 {{ total }} 条订单</span>
      </div>
      <el-table :data="orders" v-loading="loading" stripe>
        <el-table-column prop="order_no" label="订单号" min-width="190">
          <template #default="{ row }">
            <span class="cell-mono">{{ row.order_no }}</span>
          </template>
        </el-table-column>
        <el-table-column prop="user_nickname" label="用户" min-width="130">
          <template #default="{ row }">
            <span class="cell-name">{{ row.user_nickname || `用户${row.user_id}` }}</span>
          </template>
        </el-table-column>
        <el-table-column label="金额" width="100" align="center">
          <template #default="{ row }">
            <span class="cell-amount">¥{{ row.amount }}</span>
          </template>
        </el-table-column>
        <el-table-column label="支付方式" width="110" align="center">
          <template #default="{ row }">{{ payMethodMap[row.pay_method] || '-' }}</template>
        </el-table-column>
        <el-table-column label="状态" width="90" align="center">
          <template #default="{ row }">
            <el-tag :type="statusTypeMap[row.status]" size="small" effect="light">
              {{ statusMap[row.status] }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="下单时间" width="170" align="center">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="支付时间" width="170" align="center">
          <template #default="{ row }">{{ row.paid_at ? formatTime(row.paid_at) : '-' }}</template>
        </el-table-column>
        <el-table-column v-if="authStore.isSuperAdmin" label="操作" width="90" fixed="right" align="center">
          <template #default="{ row }">
            <el-popconfirm
              v-if="row.status === 'paid'"
              title="确定要退款吗？此操作不可撤销"
              confirm-button-text="确认退款"
              @confirm="handleRefund(row.id)"
            >
              <template #reference>
                <el-button link type="danger">退款</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
      <Pagination v-model:page="page" v-model:page-size="pageSize" :total="total" @change="loadData" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage } from 'element-plus'
import { getOrders, refundOrder } from '@/api/orders'
import type { Order, OrderListParams } from '@/api/orders'
import { useAuthStore } from '@/stores/auth'
import dayjs from 'dayjs'
import Pagination from '@/components/Pagination.vue'

const authStore = useAuthStore()

const orders = ref<Order[]>([])
const loading = ref(false)
const total = ref(0)
const page = ref(1)
const pageSize = ref(10)
const dateRange = ref<[string, string] | null>(null)

const filters = reactive<OrderListParams>({ status: '', pay_method: '' })

const payMethodMap: Record<string, string> = {
  wechat_jsapi: '微信JSAPI', wechat_native: '微信扫码', alipay: '支付宝',
}
const statusMap: Record<string, string> = {
  pending: '待支付', paid: '已支付', refunded: '已退款', closed: '已关闭',
}
const statusTypeMap: Record<string, string> = {
  pending: 'warning', paid: 'success', refunded: 'info', closed: 'info',
}

onMounted(loadData)

async function loadData() {
  loading.value = true
  try {
    const params: OrderListParams = { page: page.value, page_size: pageSize.value, ...filters }
    if (dateRange.value) { params.start_date = dateRange.value[0]; params.end_date = dateRange.value[1] }
    const result = await getOrders(params)
    orders.value = result.items
    total.value = result.total
  } catch { orders.value = []; total.value = 0 }
  loading.value = false
}

function handleSearch() { page.value = 1; loadData() }
function handleReset() { dateRange.value = null; filters.status = ''; filters.pay_method = ''; page.value = 1; loadData() }
async function handleRefund(id: number) {
  try { await refundOrder(id); ElMessage.success('退款成功'); loadData() } catch { ElMessage.error('退款失败') }
}
function formatTime(t: string): string { return dayjs(t).format('YYYY-MM-DD HH:mm:ss') }
</script>

<style scoped>
.orders-page { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--color-text); letter-spacing: -0.02em; }
.filter-card { margin-bottom: 16px; }
.filter-form { margin-bottom: 0; }
.filter-form :deep(.el-form-item) { margin-bottom: 0; }
.table-card { margin-bottom: 0; }
.table-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; }
.table-count { font-size: 14px; font-weight: 600; color: var(--color-text-secondary); }
.cell-mono { font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }
.cell-name { font-weight: 600; }
.cell-amount { font-weight: 600; color: var(--color-text); }
</style>