<template>
  <aside class="sidebar" :class="{ collapsed: appStore.sidebarCollapsed }">
    <div class="sidebar-header">
      <div class="logo-wrap">
        <div class="logo-icon">
          <svg width="30" height="30" viewBox="0 0 30 30" fill="none">
            <rect width="30" height="30" rx="10" fill="url(#logo-grad)"/>
            <defs>
              <linearGradient id="logo-grad" x1="0" y1="0" x2="30" y2="30">
                <stop offset="0%" stop-color="#6366f1"/>
                <stop offset="100%" stop-color="#8b5cf6"/>
              </linearGradient>
            </defs>
            <rect x="6" y="8" width="18" height="1.5" rx="0.75" fill="#fff" opacity="0.9"/>
            <rect x="6" y="12" width="18" height="1.5" rx="0.75" fill="#fff" opacity="0.9"/>
            <rect x="6" y="16" width="18" height="1.5" rx="0.75" fill="#fff" opacity="0.9"/>
            <circle cx="20" cy="20" r="4" fill="#fbbf24" stroke="#fff" stroke-width="1.5"/>
            <circle cx="21" cy="19" r="1" fill="#1e1b4b"/>
          </svg>
        </div>
        <transition name="logo-text">
          <div v-show="!appStore.sidebarCollapsed" class="logo-text">
            <span class="logo-title">PhotoStudio</span>
            <span class="logo-subtitle">管理后台</span>
          </div>
        </transition>
      </div>
    </div>

    <nav class="sidebar-nav">
      <div class="nav-section">
        <span v-show="!appStore.sidebarCollapsed" class="nav-section-title">主菜单</span>
      </div>
      <div
        v-for="item in menuItems"
        :key="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
        @click="navigate(item.path)"
      >
        <div class="nav-item-icon">
          <el-icon :size="20"><component :is="item.icon" /></el-icon>
        </div>
        <span v-show="!appStore.sidebarCollapsed" class="nav-item-label">{{ item.title }}</span>
        <div v-if="isActive(item.path)" class="nav-item-indicator"></div>
      </div>
    </nav>

    <div class="sidebar-footer">
      <div class="nav-item collapse-trigger" @click="appStore.toggleSidebar">
        <div class="nav-item-icon">
          <el-icon :size="18"><Fold v-if="!appStore.sidebarCollapsed" /><Expand v-else /></el-icon>
        </div>
        <span v-show="!appStore.sidebarCollapsed" class="nav-item-label">收起菜单</span>
      </div>
    </div>
  </aside>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { asyncRoutes } from '@/router'
import { Fold, Expand } from '@element-plus/icons-vue'

const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const appStore = useAppStore()

const menuItems = computed(() => {
  return asyncRoutes
    .filter((r) => {
      if (r.meta?.hidden) return false
      if (r.meta?.roles) {
        return (r.meta.roles as string[]).includes(authStore.role)
      }
      return true
    })
    .map((r) => ({
      path: r.path,
      title: r.meta?.title as string,
      icon: r.meta?.icon as string,
    }))
})

function isActive(path: string) {
  return route.path === path || route.path.startsWith(path + '/')
}

function navigate(path: string) {
  router.push(path)
}
</script>

<style scoped>
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--color-sidebar);
  display: flex;
  flex-direction: column;
  transition: width var(--transition-slow);
  overflow: hidden;
  position: relative;
  z-index: 100;
  border-right: 1px solid rgba(255, 255, 255, 0.04);
}

.sidebar.collapsed {
  width: var(--sidebar-collapsed-width);
}

.sidebar-header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  padding: 0 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  flex-shrink: 0;
}

.logo-wrap {
  display: flex;
  align-items: center;
  gap: 12px;
  overflow: hidden;
  width: 100%;
}

.logo-icon {
  flex-shrink: 0;
  display: flex;
  align-items: center;
}

.logo-text {
  display: flex;
  flex-direction: column;
  gap: 0;
  overflow: hidden;
  white-space: nowrap;
}

.logo-title {
  font-size: 16px;
  font-weight: 700;
  color: #f1f5f9;
  letter-spacing: -0.03em;
  line-height: 1.3;
}

.logo-subtitle {
  font-size: 11px;
  color: #64748b;
  font-weight: 400;
  line-height: 1.3;
}

.logo-text-enter-active,
.logo-text-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.logo-text-enter-from,
.logo-text-leave-to {
  opacity: 0;
  transform: translateX(-10px);
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  padding: 10px 10px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.nav-section {
  padding: 8px 12px 6px;
}

.nav-section-title {
  font-size: 10px;
  font-weight: 700;
  color: #475569;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}

.nav-item {
  display: flex;
  align-items: center;
  padding: 0 12px;
  height: 42px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  position: relative;
  transition: all var(--transition-fast);
  color: #7c8ba0;
  gap: 12px;
  user-select: none;
}

.nav-item:hover {
  background: var(--color-sidebar-hover);
  color: #cbd5e1;
}

.nav-item.active {
  background: var(--color-sidebar-active);
  color: #a5b4fc;
}

.nav-item.active .nav-item-icon {
  color: #818cf8;
}

.nav-item-icon {
  flex-shrink: 0;
  width: 20px;
  height: 20px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: color var(--transition-fast);
}

.nav-item-label {
  font-size: 14px;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  letter-spacing: 0.01em;
}

.nav-item-indicator {
  position: absolute;
  right: 0;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 18px;
  background: #818cf8;
  border-radius: 3px 0 0 3px;
}

.sidebar-footer {
  border-top: 1px solid rgba(255, 255, 255, 0.05);
  padding: 10px;
  flex-shrink: 0;
}

.collapse-trigger {
  color: #475569;
}

.collapse-trigger:hover {
  color: #94a3b8;
  background: var(--color-sidebar-hover);
}

/* Sidebar scrollbar */
.sidebar-nav::-webkit-scrollbar {
  width: 3px;
}
.sidebar-nav::-webkit-scrollbar-track {
  background: transparent;
}
.sidebar-nav::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.06);
  border-radius: 2px;
}
</style>