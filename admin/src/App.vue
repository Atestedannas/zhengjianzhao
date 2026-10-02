<template>
  <div class="app-root">
    <template v-if="authStore.isLogin">
      <div class="app-layout" :class="{ collapsed: appStore.sidebarCollapsed }">
        <AppSidebar />
        <div class="app-main">
          <AppHeader />
          <div class="app-content">
            <router-view v-slot="{ Component }">
              <transition name="page" mode="out-in">
                <component :is="Component" />
              </transition>
            </router-view>
          </div>
        </div>
      </div>
    </template>
    <template v-else>
      <router-view />
    </template>
  </div>
</template>

<script setup lang="ts">
import { onMounted } from 'vue'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import AppSidebar from '@/components/Sidebar.vue'
import AppHeader from '@/components/HeaderBar.vue'

const authStore = useAuthStore()
const appStore = useAppStore()

onMounted(() => {
  authStore.restoreLogin()
})
</script>

<style>
:root {
  --sidebar-width: 240px;
  --sidebar-collapsed-width: 68px;
  --header-height: 60px;
  --color-bg: #f1f5f9;
  --color-bg-elevated: #ffffff;
  --color-bg-hover: #f8fafc;
  --color-sidebar: #0f1222;
  --color-sidebar-hover: #1a1f36;
  --color-sidebar-active: #1e2448;
  --color-primary: #6366f1;
  --color-primary-hover: #4f46e5;
  --color-primary-light: #eef2ff;
  --color-primary-soft: rgba(99, 102, 241, 0.08);
  --color-accent: #f59e0b;
  --color-text: #1e293b;
  --color-text-secondary: #64748b;
  --color-text-muted: #94a3b8;
  --color-border: #e2e8f0;
  --color-border-light: #f1f5f9;
  --color-danger: #ef4444;
  --color-danger-soft: #fef2f2;
  --color-success: #10b981;
  --color-success-soft: #ecfdf5;
  --color-warning: #f59e0b;
  --color-warning-soft: #fffbeb;
  --color-info: #3b82f6;
  --color-info-soft: #eff6ff;
  --shadow-xs: 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06), 0 1px 2px rgba(0, 0, 0, 0.04);
  --shadow-md: 0 4px 6px -1px rgba(0, 0, 0, 0.06), 0 2px 4px -2px rgba(0, 0, 0, 0.04);
  --shadow-lg: 0 10px 15px -3px rgba(0, 0, 0, 0.06), 0 4px 6px -4px rgba(0, 0, 0, 0.04);
  --shadow-xl: 0 20px 25px -5px rgba(0, 0, 0, 0.08), 0 8px 10px -6px rgba(0, 0, 0, 0.04);
  --radius-xs: 4px;
  --radius-sm: 8px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 18px;
  --transition-fast: 0.15s cubic-bezier(0.4, 0, 0.2, 1);
  --transition: 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  --transition-slow: 0.35s cubic-bezier(0.4, 0, 0.2, 1);
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', sans-serif;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
  background: var(--color-bg);
  color: var(--color-text);
  font-size: 14px;
  line-height: 1.6;
}

.app-root {
  height: 100vh;
  overflow: hidden;
}

.app-layout {
  display: flex;
  height: 100vh;
}

.app-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--color-bg);
  min-width: 0;
}

.app-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

/* Scrollbar */
::-webkit-scrollbar {
  width: 5px;
  height: 5px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* Page transitions */
.page-enter-active,
.page-leave-active {
  transition: opacity 0.2s ease, transform 0.2s ease;
}
.page-enter-from {
  opacity: 0;
  transform: translateY(8px);
}
.page-leave-to {
  opacity: 0;
  transform: translateY(-8px);
}

/* Element Plus overrides */
.el-card {
  border-radius: var(--radius-md) !important;
  border: 1px solid var(--color-border) !important;
  box-shadow: var(--shadow-xs) !important;
  transition: box-shadow var(--transition) !important;
}
.el-card:hover {
  box-shadow: var(--shadow-sm) !important;
}
.el-card__header {
  padding: 16px 20px !important;
  border-bottom: 1px solid var(--color-border-light) !important;
  font-weight: 600;
  font-size: 15px;
  color: var(--color-text);
}
.el-card__body {
  padding: 20px !important;
}

.el-button--primary {
  --el-button-bg-color: var(--color-primary);
  --el-button-border-color: var(--color-primary);
  --el-button-hover-bg-color: var(--color-primary-hover);
  --el-button-hover-border-color: var(--color-primary-hover);
  --el-button-active-bg-color: var(--color-primary-hover);
  border-radius: var(--radius-sm) !important;
  font-weight: 500;
  transition: all var(--transition-fast) !important;
}
.el-button {
  border-radius: var(--radius-sm) !important;
  font-weight: 500;
  transition: all var(--transition-fast) !important;
}

.el-table {
  --el-table-border-color: var(--color-border-light);
  --el-table-header-bg-color: #f8fafc;
  border-radius: var(--radius-md);
  overflow: hidden;
  font-size: 13px;
}
.el-table th.el-table__cell {
  font-weight: 600;
  color: var(--color-text-secondary);
  font-size: 12px;
  height: 44px;
  text-transform: none;
  letter-spacing: 0.02em;
}
.el-table td.el-table__cell {
  font-size: 13px;
  color: var(--color-text);
}
.el-table--striped .el-table__body tr.el-table__row--striped td.el-table__cell {
  background: #fafbfc;
}

.el-pagination {
  font-weight: 500;
  padding: 16px 0 0;
  justify-content: flex-end;
}
.el-pagination .el-pager li {
  border-radius: var(--radius-xs) !important;
  font-weight: 500;
}
.el-pagination .el-pager li.is-active {
  background: var(--color-primary) !important;
}

.el-tag {
  border-radius: var(--radius-xs) !important;
  font-weight: 500;
  border: none;
}
.el-tag--small {
  padding: 0 8px;
  height: 22px;
  line-height: 22px;
  font-size: 12px;
}

.el-dialog {
  border-radius: var(--radius-lg) !important;
  box-shadow: var(--shadow-xl) !important;
}
.el-dialog__header {
  padding: 20px 24px 0 !important;
}
.el-dialog__title {
  font-weight: 700;
  font-size: 17px;
  color: var(--color-text);
}
.el-dialog__body {
  padding: 20px 24px !important;
}

.el-form-item__label {
  font-weight: 600;
  color: var(--color-text);
  font-size: 13px;
}

.el-input__wrapper {
  border-radius: var(--radius-sm) !important;
  box-shadow: 0 0 0 1px var(--color-border) inset !important;
  transition: all var(--transition-fast) !important;
}
.el-input__wrapper:hover {
  box-shadow: 0 0 0 1px #94a3b8 inset !important;
}
.el-input__wrapper.is-focus {
  box-shadow: 0 0 0 1px var(--color-primary) inset !important;
}

.el-select .el-input__wrapper {
  border-radius: var(--radius-sm) !important;
}

.el-select-dropdown__item {
  font-size: 13px;
}
.el-select-dropdown__item.is-selected {
  color: var(--color-primary);
  font-weight: 600;
}

.el-message-box {
  border-radius: var(--radius-lg) !important;
  padding-bottom: 20px !important;
}

.el-switch {
  --el-switch-on-color: var(--color-primary);
}

.el-radio {
  --el-radio-font-weight: 500;
}

.el-breadcrumb__inner {
  font-weight: 500 !important;
}

.el-empty__description {
  color: var(--color-text-muted);
}
</style>