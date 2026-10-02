<template>
  <div class="login-page">
    <div class="login-bg">
      <div class="bg-gradient"></div>
      <div class="bg-grid"></div>
      <div class="bg-orb orb-1"></div>
      <div class="bg-orb orb-2"></div>
      <div class="bg-orb orb-3"></div>
    </div>

    <div class="login-container">
      <div class="login-card">
        <div class="login-brand">
          <div class="brand-icon">
            <svg width="44" height="44" viewBox="0 0 44 44" fill="none">
              <rect width="44" height="44" rx="14" fill="url(#icon-grad)"/>
              <defs>
                <linearGradient id="icon-grad" x1="0" y1="0" x2="44" y2="44">
                  <stop offset="0%" stop-color="#6366f1"/>
                  <stop offset="100%" stop-color="#8b5cf6"/>
                </linearGradient>
              </defs>
              <rect x="8" y="12" width="28" height="2" rx="1" fill="#fff" opacity="0.9"/>
              <rect x="8" y="18" width="28" height="2" rx="1" fill="#fff" opacity="0.9"/>
              <rect x="8" y="24" width="28" height="2" rx="1" fill="#fff" opacity="0.9"/>
              <circle cx="30" cy="29" r="6" fill="#fbbf24" stroke="#fff" stroke-width="2"/>
              <circle cx="31.5" cy="27.5" r="1.5" fill="#1e1b4b"/>
            </svg>
          </div>
          <h1 class="brand-title">PhotoStudio</h1>
          <p class="brand-desc">照片合规处理系统 · 管理后台</p>
        </div>

        <el-form
          ref="formRef"
          :model="form"
          :rules="rules"
          @keyup.enter="handleLogin"
          class="login-form"
        >
          <div class="form-item-custom">
            <label class="form-label">用户名</label>
            <div class="input-wrapper">
              <el-icon class="input-icon" :size="18"><User /></el-icon>
              <input
                v-model="form.username"
                type="text"
                placeholder="请输入用户名"
                class="custom-input"
                autocomplete="username"
              />
            </div>
          </div>

          <div class="form-item-custom">
            <label class="form-label">密码</label>
            <div class="input-wrapper">
              <el-icon class="input-icon" :size="18"><Lock /></el-icon>
              <input
                v-model="form.password"
                :type="showPassword ? 'text' : 'password'"
                placeholder="请输入密码"
                class="custom-input"
                autocomplete="current-password"
              />
              <div class="input-suffix" @click="showPassword = !showPassword">
                <el-icon :size="16"><View v-if="showPassword" /><Hide v-else /></el-icon>
              </div>
            </div>
          </div>

          <button
            class="login-btn"
            :disabled="loading"
            @click="handleLogin"
          >
            <span v-if="!loading">登 录</span>
            <span v-else class="loading-dots">
              <span></span><span></span><span></span>
            </span>
          </button>
        </el-form>

        <div class="login-footer">
          <span>PhotoStudio &copy; 2026</span>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { User, Lock, View, Hide } from '@element-plus/icons-vue'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const authStore = useAuthStore()
const formRef = ref<FormInstance>()
const loading = ref(false)
const showPassword = ref(false)

const form = reactive({
  username: '',
  password: '',
})

const rules: FormRules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

async function handleLogin() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  loading.value = true
  try {
    await authStore.login({ username: form.username, password: form.password })
    ElMessage.success('登录成功')
    const redirect = (route.query.redirect as string) || '/dashboard'
    router.push(redirect)
  } catch (err: any) {
    ElMessage.error(err.message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
  overflow: hidden;
  background: #0a0a1a;
}

.login-bg {
  position: absolute;
  inset: 0;
  overflow: hidden;
}

.bg-gradient {
  position: absolute;
  inset: 0;
  background:
    radial-gradient(ellipse 80% 60% at 20% 50%, rgba(99, 102, 241, 0.12) 0%, transparent 60%),
    radial-gradient(ellipse 60% 80% at 80% 30%, rgba(139, 92, 246, 0.08) 0%, transparent 60%),
    radial-gradient(ellipse 50% 50% at 50% 80%, rgba(245, 158, 11, 0.06) 0%, transparent 60%);
}

.bg-grid {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(rgba(255,255,255,0.02) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.02) 1px, transparent 1px);
  background-size: 60px 60px;
  mask-image: radial-gradient(ellipse 70% 70% at 50% 50%, black 30%, transparent 70%);
}

.bg-orb {
  position: absolute;
  border-radius: 50%;
  filter: blur(100px);
}

.orb-1 {
  width: 500px;
  height: 500px;
  background: rgba(99, 102, 241, 0.1);
  top: -150px;
  right: -80px;
  animation: orbFloat 18s ease-in-out infinite;
}

.orb-2 {
  width: 350px;
  height: 350px;
  background: rgba(139, 92, 246, 0.08);
  bottom: -120px;
  left: -60px;
  animation: orbFloat 22s ease-in-out infinite reverse;
}

.orb-3 {
  width: 250px;
  height: 250px;
  background: rgba(245, 158, 11, 0.06);
  top: 40%;
  left: 55%;
  animation: orbFloat 14s ease-in-out infinite 3s;
}

@keyframes orbFloat {
  0%, 100% { transform: translate(0, 0) scale(1); }
  25% { transform: translate(40px, -30px) scale(1.08); }
  50% { transform: translate(-20px, 25px) scale(0.94); }
  75% { transform: translate(-35px, -15px) scale(1.04); }
}

.login-container {
  position: relative;
  z-index: 1;
  animation: cardIn 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}

@keyframes cardIn {
  from { opacity: 0; transform: translateY(20px); }
  to { opacity: 1; transform: translateY(0); }
}

.login-card {
  width: 420px;
  padding: 52px 44px 36px;
  background: rgba(15, 15, 35, 0.85);
  backdrop-filter: blur(24px);
  border-radius: 20px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  box-shadow:
    0 4px 24px rgba(0, 0, 0, 0.3),
    0 0 0 1px rgba(255, 255, 255, 0.03) inset;
}

.login-brand {
  text-align: center;
  margin-bottom: 40px;
}

.brand-icon {
  display: inline-flex;
  margin-bottom: 20px;
  animation: iconPulse 3s ease-in-out infinite;
}

@keyframes iconPulse {
  0%, 100% { transform: scale(1); }
  50% { transform: scale(1.05); }
}

.brand-title {
  font-size: 26px;
  font-weight: 700;
  color: #f1f5f9;
  margin: 0 0 8px;
  letter-spacing: -0.03em;
}

.brand-desc {
  font-size: 13px;
  color: #94a3b8;
  margin: 0;
  letter-spacing: 0.02em;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 22px;
}

.form-item-custom {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.form-label {
  font-size: 13px;
  font-weight: 600;
  color: #cbd5e1;
}

.input-wrapper {
  position: relative;
  display: flex;
  align-items: center;
}

.input-icon {
  position: absolute;
  left: 16px;
  color: #64748b;
  pointer-events: none;
  z-index: 1;
  transition: color 0.2s;
}

.input-wrapper:focus-within .input-icon {
  color: #818cf8;
}

.input-suffix {
  position: absolute;
  right: 14px;
  color: #64748b;
  cursor: pointer;
  z-index: 1;
  padding: 4px;
  border-radius: 4px;
  transition: color 0.2s;
}

.input-suffix:hover {
  color: #94a3b8;
}

.custom-input {
  width: 100%;
  height: 48px;
  padding: 0 44px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  font-size: 14px;
  color: #f1f5f9;
  background: rgba(255, 255, 255, 0.04);
  outline: none;
  transition: all 0.25s;
  font-family: inherit;
  letter-spacing: 0.01em;
}

.custom-input::placeholder {
  color: #475569;
}

.custom-input:focus {
  border-color: rgba(129, 140, 248, 0.5);
  background: rgba(255, 255, 255, 0.06);
  box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.15);
}

.login-btn {
  height: 48px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  border: none;
  border-radius: 10px;
  font-size: 15px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.25s;
  margin-top: 6px;
  letter-spacing: 0.3em;
  font-family: inherit;
  position: relative;
  overflow: hidden;
}

.login-btn::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(135deg, rgba(255,255,255,0.1), transparent);
  opacity: 0;
  transition: opacity 0.25s;
}

.login-btn:hover:not(:disabled)::before {
  opacity: 1;
}

.login-btn:hover:not(:disabled) {
  box-shadow: 0 8px 24px rgba(99, 102, 241, 0.35);
  transform: translateY(-1px);
}

.login-btn:active:not(:disabled) {
  transform: translateY(0);
}

.login-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.loading-dots {
  display: flex;
  gap: 7px;
  justify-content: center;
  align-items: center;
}

.loading-dots span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #fff;
  animation: dot 1.4s ease-in-out infinite;
}

.loading-dots span:nth-child(2) { animation-delay: 0.2s; }
.loading-dots span:nth-child(3) { animation-delay: 0.4s; }

@keyframes dot {
  0%, 80%, 100% { opacity: 0.2; transform: scale(0.6); }
  40% { opacity: 1; transform: scale(1); }
}

.login-footer {
  text-align: center;
  margin-top: 28px;
  font-size: 12px;
  color: #475569;
}
</style>