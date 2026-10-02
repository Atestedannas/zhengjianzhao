<template>
  <header class="header-bar">
    <div class="header-left">
      <el-breadcrumb separator="/">
        <el-breadcrumb-item :to="{ path: '/dashboard' }">
          <el-icon style="vertical-align: -2px"><HomeFilled /></el-icon>
          <span style="margin-left: 5px">首页</span>
        </el-breadcrumb-item>
        <el-breadcrumb-item v-if="currentTitle && currentTitle !== '数据看板'">
          {{ currentTitle }}
        </el-breadcrumb-item>
      </el-breadcrumb>
    </div>
    <div class="header-right">
      <div class="header-actions">
        <el-tooltip content="全屏显示" placement="bottom">
          <div class="action-btn" @click="toggleFullscreen">
            <el-icon :size="17"><FullScreen /></el-icon>
          </div>
        </el-tooltip>
      </div>
      <el-dropdown trigger="click" @command="handleCommand" placement="bottom-end">
        <div class="user-info">
          <div class="user-avatar">
            {{ authStore.username.charAt(0).toUpperCase() }}
          </div>
          <div class="user-meta">
            <span class="user-name">{{ authStore.username }}</span>
            <span class="user-role">{{ authStore.isSuperAdmin ? '超级管理员' : '管理员' }}</span>
          </div>
          <el-icon class="dropdown-arrow" :size="14"><ArrowDown /></el-icon>
        </div>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item disabled>
              <div class="dropdown-header">
                <div class="dropdown-avatar">{{ authStore.username.charAt(0).toUpperCase() }}</div>
                <div>
                  <div class="dropdown-name">{{ authStore.username }}</div>
                  <el-tag :type="authStore.isSuperAdmin ? 'danger' : 'primary'" size="small" effect="light">
                    {{ authStore.isSuperAdmin ? '超级管理员' : '管理员' }}
                  </el-tag>
                </div>
              </div>
            </el-dropdown-item>
            <el-dropdown-item divided command="logout">
              <el-icon><SwitchButton /></el-icon>
              <span>退出登录</span>
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useAuthStore } from '@/stores/auth'
import { HomeFilled, FullScreen, ArrowDown, SwitchButton } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()

const currentTitle = computed(() => route.meta?.title as string)

function toggleFullscreen() {
  if (!document.fullscreenElement) {
    document.documentElement.requestFullscreen()
  } else {
    document.exitFullscreen()
  }
}

async function handleCommand(command: string) {
  if (command === 'logout') {
    try {
      await ElMessageBox.confirm('确定要退出登录吗？', '退出确认', {
        confirmButtonText: '确定退出',
        cancelButtonText: '取消',
        type: 'warning',
        customClass: 'logout-confirm-box',
      })
    } catch {
      return
    }
    await authStore.logout()
    router.push('/login')
  }
}
</script>

<style scoped>
.header-bar {
  height: var(--header-height);
  background: var(--color-bg-elevated);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 24px;
  flex-shrink: 0;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  z-index: 50;
}

.header-left {
  display: flex;
  align-items: center;
}

.header-right {
  display: flex;
  align-items: center;
  gap: 4px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 2px;
}

.action-btn {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--color-text-secondary);
  transition: all var(--transition-fast);
}

.action-btn:hover {
  background: var(--color-bg);
  color: var(--color-text);
}

.user-info {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 5px 10px 5px 5px;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--transition-fast);
  margin-left: 4px;
}

.user-info:hover {
  background: var(--color-bg);
}

.user-avatar {
  width: 34px;
  height: 34px;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 14px;
  font-weight: 700;
  flex-shrink: 0;
  letter-spacing: -0.02em;
}

.user-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.3;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text);
}

.user-role {
  font-size: 11px;
  color: var(--color-text-secondary);
  font-weight: 400;
}

.dropdown-arrow {
  color: var(--color-text-muted);
  transition: transform var(--transition-fast);
}

.user-info:hover .dropdown-arrow {
  color: var(--color-text-secondary);
}

.dropdown-header {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 2px 0;
}

.dropdown-avatar {
  width: 36px;
  height: 36px;
  border-radius: var(--radius-sm);
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 15px;
  font-weight: 700;
  flex-shrink: 0;
}

.dropdown-name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: 4px;
}
</style>