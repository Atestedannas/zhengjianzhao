<template>
  <div class="user-detail" v-loading="loading">
    <template v-if="user">
      <!-- basic info -->
      <el-descriptions title="用户基本信息" :column="2" border style="margin-bottom: 20px">
        <el-descriptions-item label="用户ID">{{ user.id }}</el-descriptions-item>
        <el-descriptions-item label="昵称">{{ user.nickname || '-' }}</el-descriptions-item>
        <el-descriptions-item label="注册平台">
          <el-tag size="small">{{ platformMap[user.platform] || user.platform }}</el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="注册时间">{{ formatTime(user.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="最近登录">{{ user.last_login_at ? formatTime(user.last_login_at) : '-' }}</el-descriptions-item>
        <el-descriptions-item label="账号状态">
          <el-tag :type="user.is_active ? 'success' : 'danger'" size="small">
            {{ user.is_active ? '正常' : '已禁用' }}
          </el-tag>
        </el-descriptions-item>
        <el-descriptions-item label="剩余免费次数">{{ user.free_count }}</el-descriptions-item>
        <el-descriptions-item label="累计获得免费次数">{{ user.free_count_total }}</el-descriptions-item>
        <el-descriptions-item label="账户余额">¥{{ user.balance }}</el-descriptions-item>
        <el-descriptions-item label="累计消费">¥{{ user.total_spent }}</el-descriptions-item>
      </el-descriptions>

      <!-- processing records -->
      <el-card shadow="never" style="margin-bottom: 20px">
        <template #header>
          <span>处理记录 ({{ records.length }})</span>
        </template>
        <el-table :data="records" size="small" style="width: 100%" max-height="300">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="template_name" label="模板" min-width="120" />
          <el-table-column label="状态" width="80">
            <template #default="{ row: r }">
              <el-tag :type="recordStatusTagType(r.status)" size="small">
                {{ recordStatusText(r.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="170">
            <template #default="{ row: r }">{{ formatTime(r.created_at) }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-if="records.length === 0" description="暂无处理记录" />
      </el-card>

      <!-- orders -->
      <el-card shadow="never">
        <template #header>
          <span>充值/消费明细 ({{ orders.length }})</span>
        </template>
        <el-table :data="orders" size="small" style="width: 100%" max-height="300">
          <el-table-column prop="order_no" label="订单号" min-width="180" />
          <el-table-column prop="amount" label="金额" width="100">
            <template #default="{ row: r }">¥{{ r.amount }}</template>
          </el-table-column>
          <el-table-column label="支付方式" width="100">
            <template #default="{ row: r }">
              {{ payMethodMap[r.pay_method] || '-' }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="80">
            <template #default="{ row: r }">
              <el-tag :type="statusTypeMap[r.status]" size="small">
                {{ statusMap[r.status] }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="170">
            <template #default="{ row: r }">{{ formatTime(r.created_at) }}</template>
          </el-table-column>
        </el-table>
        <el-empty v-if="orders.length === 0" description="暂无订单记录" />
      </el-card>
    </template>
    <el-empty v-else description="无数据" />
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onMounted } from 'vue'
import { getUserDetail } from '@/api/users'
import { recordStatusTagType, recordStatusText } from '@/utils/recordStatus'
import dayjs from 'dayjs'

interface Props {
  userId: number
}

const props = defineProps<Props>()

const user = ref<any>(null)
const records = ref<any[]>([])
const orders = ref<any[]>([])
const loading = ref(false)

const platformMap: Record<string, string> = {
  wechat_mini: '小程序',
  wechat_web: '微信Web',
  alipay_web: '支付宝',
}

const payMethodMap: Record<string, string> = {
  wechat_jsapi: '微信JSAPI',
  wechat_native: '微信扫码',
  alipay: '支付宝',
}

const statusMap: Record<string, string> = {
  pending: '待支付',
  paid: '已支付',
  refunded: '已退款',
  closed: '已关闭',
}

const statusTypeMap: Record<string, string> = {
  pending: 'warning',
  paid: 'success',
  refunded: 'info',
  closed: 'info',
}

async function loadDetail() {
  if (!props.userId) return
  loading.value = true
  try {
    const result = await getUserDetail(props.userId)
    user.value = result
    records.value = result.records || []
    orders.value = result.orders || []
  } catch {
    user.value = null
    records.value = []
    orders.value = []
  }
  loading.value = false
}

onMounted(loadDetail)
watch(() => props.userId, loadDetail)

function formatTime(t: string): string {
  return dayjs(t).format('YYYY-MM-DD HH:mm:ss')
}
</script>
