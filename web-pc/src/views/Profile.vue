<template>
  <div class="profile-page">
    <div class="container">
      <div class="profile-layout">
        <!-- 左侧：用户信息卡片 -->
        <div class="user-card">
          <div class="user-avatar-section">
            <el-avatar :size="80" :src="authStore.userInfo.avatar_url">
              {{ authStore.userInfo.nickname?.charAt(0) || 'U' }}
            </el-avatar>
            <h2 class="user-name">{{ authStore.userInfo.nickname || '用户' }}</h2>
            <p class="user-id">ID: {{ authStore.userInfo.id || '-' }}</p>
          </div>

          <div class="user-stats">
            <div class="stat-card free-count">
              <div class="stat-icon">
                <el-icon><Coin /></el-icon>
              </div>
              <div class="stat-info">
                <div class="stat-value">{{ authStore.freeCountText }}</div>
                <div class="stat-label">免费次数</div>
              </div>
            </div>
            <div class="stat-card balance">
              <div class="stat-icon">
                <el-icon><Wallet /></el-icon>
              </div>
              <div class="stat-info">
                <div class="stat-value">¥{{ authStore.userInfo.balance?.toFixed(2) || '0.00' }}</div>
                <div class="stat-label">账户余额</div>
              </div>
            </div>
          </div>

          <div class="user-detail">
            <div class="detail-row">
              <span class="detail-label">累计消费</span>
              <span class="detail-value">¥{{ authStore.userInfo.total_spent?.toFixed(2) || '0.00' }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">注册时间</span>
              <span class="detail-value">{{ formatDate(authStore.userInfo.created_at) }}</span>
            </div>
            <div class="detail-row">
              <span class="detail-label">最后登录</span>
              <span class="detail-value">{{ formatDate(authStore.userInfo.last_login_at) }}</span>
            </div>
          </div>

          <div class="user-actions">
            <el-button type="danger" plain @click="handleLogout">
              <el-icon><SwitchButton /></el-icon>
              退出登录
            </el-button>
          </div>
        </div>

        <!-- 右侧：功能菜单 -->
        <div class="profile-menu">
          <h3 class="menu-title">我的服务</h3>
          <div class="menu-grid">
            <div class="menu-item" @click="goHistory">
              <div class="menu-icon history">
                <el-icon :size="28"><Clock /></el-icon>
              </div>
              <div class="menu-content">
                <h4 class="menu-name">处理记录</h4>
                <p class="menu-desc">查看历史处理记录</p>
              </div>
              <el-icon class="menu-arrow"><ArrowRight /></el-icon>
            </div>

            <div class="menu-item" @click="goEditor">
              <div class="menu-icon editor">
                <el-icon :size="28"><EditPen /></el-icon>
              </div>
              <div class="menu-content">
                <h4 class="menu-name">照片编辑</h4>
                <p class="menu-desc">开始制作证件照</p>
              </div>
              <el-icon class="menu-arrow"><ArrowRight /></el-icon>
            </div>

            <div class="menu-item" @click="showRechargeDialog = true">
              <div class="menu-icon recharge">
                <el-icon :size="28"><CreditCard /></el-icon>
              </div>
              <div class="menu-content">
                <h4 class="menu-name">充值次数</h4>
                <p class="menu-desc">购买更多处理次数</p>
              </div>
              <el-icon class="menu-arrow"><ArrowRight /></el-icon>
            </div>

            <div class="menu-item" @click="showHelpDialog = true">
              <div class="menu-icon help">
                <el-icon :size="28"><QuestionFilled /></el-icon>
              </div>
              <div class="menu-content">
                <h4 class="menu-name">帮助中心</h4>
                <p class="menu-desc">常见问题与使用指南</p>
              </div>
              <el-icon class="menu-arrow"><ArrowRight /></el-icon>
            </div>
          </div>

          <!-- 使用说明 -->
          <div class="tips-card">
            <div class="tips-header">
              <el-icon color="#e6a23c"><Warning /></el-icon>
              <span>温馨提示</span>
            </div>
            <ul class="tips-list">
              <li>建议上传正面免冠、光线均匀的照片</li>
              <li>免费次数用完后可联系管理员充值</li>
              <li>处理后的照片会保留在历史记录中</li>
              <li>如有问题请联系客服获取帮助</li>
            </ul>
          </div>
        </div>
      </div>
    </div>

    <!-- 充值弹窗 -->
    <el-dialog
      v-model="showRechargeDialog"
      title="充值次数"
      width="480px"
      center
    >
      <div class="recharge-content">
        <p class="recharge-tip">
          <el-icon><InfoFilled /></el-icon>
          请联系系统管理员进行充值
        </p>
        <div class="recharge-packages">
          <div class="package-card" v-for="pkg in packages" :key="pkg.name">
            <div class="pkg-name">{{ pkg.name }}</div>
            <div class="pkg-price">¥{{ pkg.price }}</div>
            <div class="pkg-desc">{{ pkg.desc }}</div>
          </div>
        </div>
      </div>
      <template #footer>
        <el-button @click="showRechargeDialog = false">关闭</el-button>
      </template>
    </el-dialog>

    <!-- 帮助弹窗 -->
    <el-dialog
      v-model="showHelpDialog"
      title="帮助中心"
      width="560px"
    >
      <el-collapse>
        <el-collapse-item title="如何制作证件照？" name="1">
          <ol class="help-list">
            <li>点击首页或导航栏的"照片编辑"进入编辑器</li>
            <li>上传一张正面免冠照片</li>
            <li>选择需要的证件照规格和背景颜色</li>
            <li>按需开启"自然微调"（可选，仅轻度匀肤提亮）</li>
            <li>点击"开始处理"按钮</li>
            <li>处理完成后点击"下载结果"保存照片</li>
          </ol>
        </el-collapse-item>
        <el-collapse-item title="支持哪些照片规格？" name="2">
          <p>支持一寸、二寸、小一寸、小二寸、大一寸等多种常用证件照规格，包括身份证、护照、签证、驾驶证、考试报名等各类证件照片要求。</p>
        </el-collapse-item>
        <el-collapse-item title="免费次数用完了怎么办？" name="3">
          <p>免费次数用完后，可以联系系统管理员进行充值购买更多处理次数。充值后即可继续使用所有功能。</p>
        </el-collapse-item>
        <el-collapse-item title="照片处理失败怎么办？" name="4">
          <p>请尝试以下方法：</p>
          <ul class="help-list">
            <li>确保上传的是正面清晰的人像照片</li>
            <li>照片中人脸占比适中，不要过大或过小</li>
            <li>光线均匀，避免强光或阴影</li>
            <li>尝试更换不同的照片重试</li>
          </ul>
        </el-collapse-item>
        <el-collapse-item title="处理记录会保存多久？" name="5">
          <p>系统会保留最近的处理记录，方便您随时下载和查看。建议下载后妥善保存到本地。</p>
        </el-collapse-item>
      </el-collapse>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import dayjs from 'dayjs'
import {
  Coin,
  Wallet,
  SwitchButton,
  Clock,
  EditPen,
  CreditCard,
  QuestionFilled,
  ArrowRight,
  Warning,
  InfoFilled,
} from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const showRechargeDialog = ref(false)
const showHelpDialog = ref(false)

const packages = [
  { name: '基础包', price: '9.9', desc: '10 次处理次数' },
  { name: '标准包', price: '29.9', desc: '50 次处理次数' },
  { name: '专业包', price: '59.9', desc: '100 次处理次数' },
]

onMounted(() => {
  if (authStore.isLogin) {
    authStore.loadUserProfile()
  }
})

function formatDate(dateStr: string | null | undefined): string {
  if (!dateStr) return '-'
  return dayjs(dateStr).format('YYYY-MM-DD HH:mm')
}

function goHistory() {
  router.push('/history')
}

function goEditor() {
  router.push('/editor')
}

async function handleLogout() {
  try {
    await ElMessageBox.confirm('确定要退出登录吗？', '提示', {
      confirmButtonText: '确定',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await authStore.logout()
    ElMessage.success('已退出登录')
    router.push('/')
  } catch {
    // 用户取消
  }
}
</script>

<style scoped>
.profile-page {
  padding: 40px 0;
  min-height: calc(100vh - 64px);
  background: #f5f7fa;
}

.profile-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 24px;
  align-items: start;
}

/* ===== User Card ===== */
.user-card {
  background: #fff;
  border-radius: 12px;
  padding: 32px 24px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
  position: sticky;
  top: 88px;
}

.user-avatar-section {
  text-align: center;
  padding-bottom: 24px;
  border-bottom: 1px solid #f0f2f5;
}

.user-avatar-section :deep(.el-avatar) {
  margin-bottom: 16px;
  border: 3px solid #ecf5ff;
}

.user-name {
  font-size: 20px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0 0 6px 0;
}

.user-id {
  font-size: 13px;
  color: #909399;
  margin: 0;
}

.user-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 20px 0;
  border-bottom: 1px solid #f0f2f5;
}

.stat-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px;
  border-radius: 8px;
  background: #fafafa;
}

.stat-icon {
  width: 40px;
  height: 40px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.free-count .stat-icon {
  background: linear-gradient(135deg, #ffecd2 0%, #fcb69f 100%);
  color: #e6a23c;
}

.balance .stat-icon {
  background: linear-gradient(135deg, #a8edea 0%, #fed6e3 100%);
  color: #67c23a;
}

.stat-value {
  font-size: 18px;
  font-weight: 700;
  color: #1f2d3d;
  line-height: 1.2;
}

.stat-label {
  font-size: 11px;
  color: #909399;
  margin-top: 2px;
}

.user-detail {
  padding: 20px 0;
  border-bottom: 1px solid #f0f2f5;
}

.detail-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  font-size: 14px;
}

.detail-label {
  color: #909399;
}

.detail-value {
  color: #303133;
  font-weight: 500;
}

.user-actions {
  padding-top: 20px;
}

.user-actions .el-button {
  width: 100%;
}

/* ===== Profile Menu ===== */
.profile-menu {
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.menu-title {
  font-size: 18px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0 0 4px 0;
}

.menu-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}

.menu-item {
  background: #fff;
  border-radius: 12px;
  padding: 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  cursor: pointer;
  transition: all 0.3s;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
}

.menu-item:hover {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  transform: translateY(-2px);
}

.menu-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  color: #fff;
}

.menu-icon.history {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.menu-icon.editor {
  background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
}

.menu-icon.recharge {
  background: linear-gradient(135deg, #4facfe 0%, #00f2fe 100%);
}

.menu-icon.help {
  background: linear-gradient(135deg, #43e97b 0%, #38f9d7 100%);
}

.menu-content {
  flex: 1;
  min-width: 0;
}

.menu-name {
  font-size: 16px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0 0 4px 0;
}

.menu-desc {
  font-size: 13px;
  color: #909399;
  margin: 0;
}

.menu-arrow {
  color: #c0c4cc;
  font-size: 18px;
  flex-shrink: 0;
  transition: transform 0.2s;
}

.menu-item:hover .menu-arrow {
  transform: translateX(4px);
  color: #409eff;
}

/* ===== Tips Card ===== */
.tips-card {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 12px;
  padding: 20px;
}

.tips-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: #92400e;
  margin-bottom: 12px;
}

.tips-list {
  margin: 0;
  padding-left: 20px;
  color: #78350f;
  font-size: 13px;
  line-height: 2;
}

/* ===== Recharge Dialog ===== */
.recharge-tip {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #e6a23c;
  font-size: 14px;
  margin: 0 0 20px 0;
}

.recharge-packages {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.package-card {
  text-align: center;
  padding: 20px 16px;
  border: 1px solid #e4e7ed;
  border-radius: 8px;
  transition: all 0.3s;
}

.package-card:hover {
  border-color: #409eff;
  background: #f0f7ff;
}

.pkg-name {
  font-size: 15px;
  font-weight: 600;
  color: #1f2d3d;
  margin-bottom: 8px;
}

.pkg-price {
  font-size: 24px;
  font-weight: 700;
  color: #f56c6c;
  margin-bottom: 6px;
}

.pkg-desc {
  font-size: 12px;
  color: #909399;
  margin: 0;
}

/* ===== Help Dialog ===== */
.help-list {
  padding-left: 20px;
  color: #606266;
  line-height: 2;
  margin: 8px 0;
}

/* ===== Responsive ===== */
@media (max-width: 1024px) {
  .profile-layout {
    grid-template-columns: 1fr;
  }

  .user-card {
    position: static;
  }
}

@media (max-width: 768px) {
  .profile-page {
    padding: 20px 0;
  }

  .menu-grid {
    grid-template-columns: 1fr;
  }

  .recharge-packages {
    grid-template-columns: 1fr;
  }
}
</style>
