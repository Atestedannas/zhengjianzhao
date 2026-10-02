<template>
  <div class="dashboard">
    <!-- Stats Cards -->
    <div class="stats-grid">
      <div
        v-for="card in statCards"
        :key="card.label"
        class="stat-card"
        :style="{ '--card-color': card.color, '--card-color-soft': card.color + '15' }"
      >
        <div class="stat-card-inner">
          <div class="stat-card-left">
            <span class="stat-label">{{ card.label }}</span>
            <span class="stat-value">
              <span v-if="card.loading" class="stat-skeleton"></span>
              <span v-else>{{ card.value }}</span>
            </span>
            <span v-if="card.sub && !card.loading" class="stat-sub">{{ card.sub }}</span>
          </div>
          <div class="stat-card-icon">
            <el-icon :size="24"><component :is="card.icon" /></el-icon>
          </div>
        </div>
        <div class="stat-card-bar"></div>
      </div>
    </div>

    <!-- Charts Row -->
    <div class="charts-row">
      <div class="chart-card">
        <div class="chart-header">
          <div class="chart-header-left">
            <div class="chart-dot" style="background: #6366f1"></div>
            <h3 class="chart-title">处理趋势</h3>
          </div>
          <div class="chart-tabs">
            <button
              v-for="d in [7, 30]"
              :key="d"
              class="chart-tab"
              :class="{ active: trendDays === d }"
              @click="trendDays = d; loadTrendData()"
            >近 {{ d }} 天</button>
          </div>
        </div>
        <div class="chart-body">
          <v-chart v-if="trendData.dates.length" :option="trendOption" autoresize />
          <div v-else class="chart-empty">
            <el-icon :size="40"><DataAnalysis /></el-icon>
            <span>暂无数据</span>
          </div>
        </div>
      </div>

      <div class="chart-card">
        <div class="chart-header">
          <div class="chart-header-left">
            <div class="chart-dot" style="background: #10b981"></div>
            <h3 class="chart-title">模板使用占比</h3>
          </div>
        </div>
        <div class="chart-body">
          <v-chart v-if="pieData.names.length" :option="pieOption" autoresize />
          <div v-else class="chart-empty">
            <el-icon :size="40"><PieChart /></el-icon>
            <span>暂无数据</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Revenue Chart -->
    <div class="chart-card full-width">
      <div class="chart-header">
        <div class="chart-header-left">
          <div class="chart-dot" style="background: #f59e0b"></div>
          <h3 class="chart-title">收入趋势</h3>
        </div>
      </div>
      <div class="chart-body chart-body-bar">
        <v-chart v-if="trendData.dates.length" :option="revenueOption" autoresize />
        <div v-else class="chart-empty">
          <el-icon :size="40"><Money /></el-icon>
          <span>暂无收入数据</span>
        </div>
      </div>
    </div>

    <!-- Recent Records -->
    <div class="chart-card full-width">
      <div class="chart-header">
        <div class="chart-header-left">
          <div class="chart-dot" style="background: #3b82f6"></div>
          <h3 class="chart-title">最近处理记录</h3>
        </div>
        <el-button text type="primary" @click="router.push('/records')">
          查看全部
          <el-icon style="margin-left: 4px"><ArrowRight /></el-icon>
        </el-button>
      </div>
      <div class="table-wrap">
        <el-table :data="recentRecords" v-loading="recordsLoading" size="small" stripe :empty-text="'暂无处理记录'">
          <el-table-column prop="id" label="ID" width="70" align="center" />
          <el-table-column prop="user_nickname" label="用户" min-width="120">
            <template #default="{ row }">
              <span class="table-user">{{ row.user_nickname || '匿名用户' }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="template_name" label="模板" min-width="120">
            <template #default="{ row }">
              <el-tag v-if="row.template_name" type="info" size="small" effect="light">{{ row.template_name }}</el-tag>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>
          <el-table-column label="输出大小" width="100" align="center">
            <template #default="{ row }">{{ formatSize(row.result_size) }}</template>
          </el-table-column>
          <el-table-column label="耗时" width="100" align="center">
            <template #default="{ row }">
              <span :class="{ 'text-success': row.processing_time_ms < 1000, 'text-warning': row.processing_time_ms >= 1000 }">
                {{ (row.processing_time_ms / 1000).toFixed(2) }}s
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="90" align="center">
            <template #default="{ row }">
              <el-tag
                :type="row.status === 'success' ? 'success' : 'danger'"
                size="small"
                effect="light"
              >
                {{ row.status === 'success' ? '成功' : '失败' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="时间" width="170" align="center">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
        </el-table>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { LineChart, PieChart, BarChart } from 'echarts/charts'
import { TitleComponent, TooltipComponent, LegendComponent, GridComponent } from 'echarts/components'
import { getStats, getTrend, getTemplateUsage } from '@/api/dashboard'
import { getRecords } from '@/api/records'
import type { DashboardStats, TrendData } from '@/api/dashboard'
import type { ProcessRecord } from '@/api/records'
import {
  Picture, DataAnalysis, Money, ShoppingCart,
  PieChart as PieChartIcon, WarningFilled, ArrowRight,
} from '@element-plus/icons-vue'
import dayjs from 'dayjs'

use([CanvasRenderer, LineChart, PieChart, BarChart, TitleComponent, TooltipComponent, LegendComponent, GridComponent])

const router = useRouter()
const statsLoading = ref(true)
const stats = ref<DashboardStats>({
  today_processed: 0, total_processed: 0, today_revenue: 0,
  paid_orders_count: 0, free_usage_ratio: 0, rembg_fail_rate: 0,
})

const statCards = computed(() => [
  {
    label: '今日处理', value: stats.value.today_processed, icon: Picture,
    color: '#6366f1', loading: statsLoading.value,
  },
  {
    label: '总处理次数', value: stats.value.total_processed, icon: DataAnalysis,
    color: '#10b981', loading: statsLoading.value,
  },
  {
    label: '今日收入', value: `¥${stats.value.today_revenue.toFixed(2)}`, icon: Money,
    color: '#f59e0b', loading: statsLoading.value,
  },
  {
    label: '付费订单', value: stats.value.paid_orders_count, icon: ShoppingCart,
    color: '#ef4444', loading: statsLoading.value,
  },
  {
    label: '免费消耗比', value: `${(stats.value.free_usage_ratio * 100).toFixed(1)}%`, icon: PieChartIcon,
    color: '#8b5cf6', loading: statsLoading.value,
  },
  {
    label: '失败率', value: `${(stats.value.rembg_fail_rate * 100).toFixed(1)}%`, icon: WarningFilled,
    color: '#06b6d4', loading: statsLoading.value,
  },
])

const trendDays = ref(7)
const trendData = ref<TrendData>({ dates: [], process_counts: [], revenue_amounts: [] })

const trendOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#fff',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    textStyle: { color: '#334155', fontSize: 13 },
    boxShadow: '0 8px 24px rgba(0,0,0,0.08)',
    padding: [12, 16],
    extraCssText: 'border-radius: 10px;',
  },
  legend: {
    bottom: 0,
    textStyle: { color: '#94a3b8', fontSize: 12 },
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 20,
  },
  grid: { left: 10, right: 20, bottom: 36, top: 10, containLabel: true },
  xAxis: {
    type: 'category',
    data: trendData.value.dates,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#94a3b8', fontSize: 11 },
  },
  yAxis: [
    {
      type: 'value',
      name: '次数',
      nameTextStyle: { color: '#94a3b8', fontSize: 11, padding: [0, 0, 0, 0] },
      splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' } },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
    {
      type: 'value',
      name: '金额',
      nameTextStyle: { color: '#94a3b8', fontSize: 11 },
      splitLine: { show: false },
      axisLabel: { color: '#94a3b8', fontSize: 11 },
    },
  ],
  series: [
    {
      name: '处理次数',
      type: 'line',
      data: trendData.value.process_counts,
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2.5, color: '#6366f1' },
      itemStyle: { color: '#6366f1' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(99,102,241,0.12)' },
            { offset: 1, color: 'rgba(99,102,241,0)' },
          ],
        },
      },
    },
    {
      name: '收入(元)',
      type: 'line',
      yAxisIndex: 1,
      data: trendData.value.revenue_amounts,
      smooth: true,
      symbol: 'circle',
      symbolSize: 6,
      lineStyle: { width: 2.5, color: '#f59e0b' },
      itemStyle: { color: '#f59e0b' },
      areaStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(245,158,11,0.12)' },
            { offset: 1, color: 'rgba(245,158,11,0)' },
          ],
        },
      },
    },
  ],
}))

const pieData = ref<{ names: string[]; counts: number[] }>({ names: [], counts: [] })
const pieColors = ['#6366f1', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#f97316', '#3b82f6', '#84cc16']

const pieOption = computed(() => ({
  tooltip: {
    trigger: 'item',
    formatter: '{b}: {c} ({d}%)',
    backgroundColor: '#fff',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    textStyle: { color: '#334155', fontSize: 13 },
    boxShadow: '0 8px 24px rgba(0,0,0,0.08)',
    padding: [12, 16],
    extraCssText: 'border-radius: 10px;',
  },
  legend: {
    orient: 'vertical',
    right: 10,
    top: 'center',
    textStyle: { color: '#64748b', fontSize: 12 },
    itemWidth: 8,
    itemHeight: 8,
    itemGap: 14,
  },
  series: [{
    type: 'pie',
    radius: ['48%', '78%'],
    center: ['38%', '50%'],
    avoidLabelOverlap: false,
    itemStyle: {
      borderRadius: 5,
      borderColor: '#fff',
      borderWidth: 3,
    },
    label: { show: false },
    emphasis: {
      label: { show: true, fontSize: 14, fontWeight: 'bold' },
      scaleSize: 8,
    },
    data: pieData.value.names.map((name, i) => ({
      name,
      value: pieData.value.counts[i],
      itemStyle: { color: pieColors[i % pieColors.length] },
    })),
  }],
}))

const revenueOption = computed(() => ({
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#fff',
    borderColor: '#e2e8f0',
    borderWidth: 1,
    textStyle: { color: '#334155', fontSize: 13 },
    boxShadow: '0 8px 24px rgba(0,0,0,0.08)',
    padding: [12, 16],
    extraCssText: 'border-radius: 10px;',
  },
  grid: { left: 10, right: 20, bottom: 10, top: 10, containLabel: true },
  xAxis: {
    type: 'category',
    data: trendData.value.dates,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: '#94a3b8', fontSize: 11 },
  },
  yAxis: {
    type: 'value',
    name: '金额(元)',
    nameTextStyle: { color: '#94a3b8', fontSize: 11 },
    splitLine: { lineStyle: { color: '#f1f5f9', type: 'dashed' } },
    axisLabel: { color: '#94a3b8', fontSize: 11 },
  },
  series: [{
    name: '收入',
    type: 'bar',
    data: trendData.value.revenue_amounts,
    itemStyle: {
      borderRadius: [8, 8, 0, 0],
      color: {
        type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [
          { offset: 0, color: '#6366f1' },
          { offset: 1, color: '#a5b4fc' },
        ],
      },
    },
    barWidth: '50%',
    emphasis: {
      itemStyle: {
        color: {
          type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: '#4f46e5' },
            { offset: 1, color: '#818cf8' },
          ],
        },
      },
    },
  }],
}))

const recentRecords = ref<ProcessRecord[]>([])
const recordsLoading = ref(false)

onMounted(async () => {
  await Promise.all([loadStats(), loadTrendData(), loadPieData(), loadRecentRecords()])
})

async function loadStats() {
  statsLoading.value = true
  try { stats.value = await getStats() } catch { /* empty */ }
  statsLoading.value = false
}

async function loadTrendData() {
  try { trendData.value = await getTrend(trendDays.value) } catch { /* empty */ }
}

async function loadPieData() {
  try { pieData.value = await getTemplateUsage() } catch { /* empty */ }
}

async function loadRecentRecords() {
  recordsLoading.value = true
  try {
    const result = await getRecords({ page: 1, page_size: 10 })
    recentRecords.value = result.items || []
  } catch { /* empty */ }
  recordsLoading.value = false
}

function formatSize(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  return `${(bytes / 1024).toFixed(1)} KB`
}

function formatTime(t: string): string {
  return dayjs(t).format('YYYY-MM-DD HH:mm:ss')
}
</script>

<style scoped>
.dashboard {
  max-width: 1440px;
  animation: fadeIn 0.4s ease;
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Stats Grid */
.stats-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 16px;
  margin-bottom: 20px;
}

.stat-card {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  overflow: hidden;
  transition: all var(--transition);
  position: relative;
}

.stat-card:hover {
  box-shadow: var(--shadow-md);
  transform: translateY(-2px);
  border-color: var(--card-color);
}

.stat-card-inner {
  padding: 20px;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}

.stat-card-left {
  display: flex;
  flex-direction: column;
  gap: 5px;
  min-width: 0;
}

.stat-label {
  font-size: 12px;
  color: var(--color-text-secondary);
  font-weight: 500;
  letter-spacing: 0.01em;
}

.stat-value {
  font-size: 26px;
  font-weight: 700;
  color: var(--color-text);
  letter-spacing: -0.03em;
  line-height: 1.2;
}

.stat-skeleton {
  display: inline-block;
  width: 60px;
  height: 28px;
  background: var(--color-border-light);
  border-radius: 4px;
  animation: shimmer 1.5s ease-in-out infinite;
}

@keyframes shimmer {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}

.stat-sub {
  font-size: 11px;
  color: var(--color-text-muted);
  margin-top: 1px;
}

.stat-card-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--radius-sm);
  background: var(--card-color-soft);
  color: var(--card-color);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.stat-card-bar {
  height: 3px;
  background: var(--card-color);
  opacity: 0;
  transition: opacity var(--transition);
}

.stat-card:hover .stat-card-bar {
  opacity: 1;
}

/* Charts */
.charts-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-bottom: 16px;
}

.chart-card {
  background: var(--color-bg-elevated);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  overflow: hidden;
  transition: box-shadow var(--transition);
}

.chart-card:hover {
  box-shadow: var(--shadow-sm);
}

.chart-card.full-width {
  margin-bottom: 16px;
}

.chart-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px 0;
}

.chart-header-left {
  display: flex;
  align-items: center;
  gap: 10px;
}

.chart-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.chart-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  letter-spacing: -0.01em;
}

.chart-tabs {
  display: flex;
  background: var(--color-bg);
  border-radius: var(--radius-sm);
  padding: 3px;
  gap: 2px;
}

.chart-tab {
  padding: 5px 14px;
  border: none;
  background: transparent;
  border-radius: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: all var(--transition-fast);
  font-family: inherit;
}

.chart-tab.active {
  background: #fff;
  color: var(--color-primary);
  box-shadow: var(--shadow-xs);
}

.chart-tab:hover:not(.active) {
  color: var(--color-text);
}

.chart-body {
  padding: 8px 8px 0;
  height: 320px;
}

.chart-body-bar {
  height: 240px;
}

.chart-empty {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: var(--color-text-muted);
  gap: 8px;
  font-size: 13px;
}

/* Table */
.table-wrap {
  padding: 0 0 4px;
}

.table-wrap :deep(.el-table) {
  border-radius: 0 0 var(--radius-md) var(--radius-md);
}

.table-user {
  font-weight: 500;
  color: var(--color-text);
}

.text-muted {
  color: var(--color-text-muted);
}

.text-success {
  color: var(--color-success);
  font-weight: 500;
}

.text-warning {
  color: var(--color-warning);
  font-weight: 500;
}

/* Responsive */
@media (max-width: 1500px) {
  .stats-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

@media (max-width: 1100px) {
  .stats-grid {
    grid-template-columns: repeat(2, 1fr);
  }
  .charts-row {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .stats-grid {
    grid-template-columns: 1fr;
  }
}
</style>