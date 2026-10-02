import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'

const constantRoutes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/views/login/index.vue'),
    meta: { title: '登录' },
  },
  {
    path: '/',
    redirect: '/dashboard',
  },
]


export const asyncRoutes: RouteRecordRaw[] = [
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: () => import('@/views/dashboard/index.vue'),
    meta: { title: '数据看板', icon: 'DataAnalysis', roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/records',
    name: 'Records',
    component: () => import('@/views/records/index.vue'),
    meta: { title: '处理记录', icon: 'Document', roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/records/:id',
    name: 'RecordDetail',
    component: () => import('@/views/records/detail.vue'),
    meta: { title: '记录详情', hidden: true, roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/templates',
    name: 'Templates',
    component: () => import('@/views/templates/index.vue'),
    meta: { title: '模板管理', icon: 'SetUp', roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/templates/edit/:id?',
    name: 'TemplateEdit',
    component: () => import('@/views/templates/edit.vue'),
    meta: { title: '模板编辑', hidden: true, roles: ['super_admin'] },
  },
  {
    path: '/users',
    name: 'Users',
    component: () => import('@/views/users/index.vue'),
    meta: { title: '用户管理', icon: 'User', roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/users/:id',
    name: 'UserDetail',
    component: () => import('@/views/users/detail.vue'),
    meta: { title: '用户详情', hidden: true, roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/orders',
    name: 'Orders',
    component: () => import('@/views/orders/index.vue'),
    meta: { title: '订单管理', icon: 'ShoppingCart', roles: ['super_admin', 'normal_admin'] },
  },
  {
    path: '/pricing',
    name: 'Pricing',
    component: () => import('@/views/pricing/index.vue'),
    meta: { title: '价格策略', icon: 'PriceTag', roles: ['super_admin'] },
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('@/views/settings/index.vue'),
    meta: { title: '系统配置', icon: 'Setting', roles: ['super_admin'] },
  },
  {
    path: '/:pathMatch(.*)*',
    redirect: '/dashboard',
  },
]

const router = createRouter({
  history: createWebHistory('/admin/'),
  routes: [...constantRoutes, ...asyncRoutes],
})

router.beforeEach(async (to, _from, next) => {
  const authStore = useAuthStore()

  if (to.path === '/login') {
    if (authStore.isLogin) {
      return next('/dashboard')
    }
    return next()
  }

  if (!authStore.isLogin) {
    await authStore.restoreLogin()
    if (!authStore.isLogin) {
      return next(`/login?redirect=${to.path}`)
    }
  }

  if (to.meta.roles) {
    const roles = to.meta.roles as string[]
    if (!roles.includes(authStore.role)) {
      return next('/dashboard')
    }
  }

  next()
})

export default router