<template>
  <div class="settings-page">
    <div class="page-header">
      <h2 class="page-title">系统配置</h2>
    </div>

    <el-card shadow="never" v-loading="loading">
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="200px"
        class="config-form"
      >
        <div class="form-section">
          <h4 class="section-title">文件与存储</h4>
          <el-form-item label="上传文件上限 (MB)" prop="upload_max_mb">
            <el-input-number v-model="form.upload_max_mb" :min="1" :max="50" style="width: 200px" />
            <span class="form-hint">用户单次上传照片的最大体积</span>
          </el-form-item>
          <el-form-item label="临时文件保留 (分钟)" prop="temp_file_ttl_minutes">
            <el-input-number v-model="form.temp_file_ttl_minutes" :min="1" :max="1440" style="width: 200px" />
            <span class="form-hint">处理后临时文件的保留时长</span>
          </el-form-item>
        </div>

        <el-divider />

        <div class="form-section">
          <h4 class="section-title">安全与限流</h4>
          <el-form-item label="API频率限制 (次/分钟)" prop="rate_limit_per_minute">
            <el-input-number v-model="form.rate_limit_per_minute" :min="10" :max="10000" style="width: 200px" />
            <span class="form-hint">每个IP每分钟最大请求数</span>
          </el-form-item>
        </div>

        <el-divider />

        <div class="form-section">
          <h4 class="section-title">系统状态</h4>
          <el-form-item label="维护模式">
            <el-switch
              v-model="form.maintenance_mode"
              :before-change="beforeMaintenanceToggle"
            />
            <span class="form-hint" style="margin-left: 12px">
              {{ form.maintenance_mode ? '已开启 - 用户端暂停服务' : '已关闭 - 正常运行' }}
            </span>
          </el-form-item>
        </div>

        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="handleSubmit" size="large">
            保存配置
          </el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { getSettings, updateSettings } from '@/api/settings'
import type { SystemSettings } from '@/api/settings'

const formRef = ref<FormInstance>()
const loading = ref(false)
const submitting = ref(false)

const form = reactive<SystemSettings>({
  upload_max_mb: 10,
  temp_file_ttl_minutes: 5,
  rate_limit_per_minute: 60,
  maintenance_mode: false,
})

const rules: FormRules = {
  upload_max_mb: [{ required: true, message: '请输入上传上限' }],
  temp_file_ttl_minutes: [{ required: true, message: '请输入保留时间' }],
  rate_limit_per_minute: [{ required: true, message: '请输入频率限制' }],
}

onMounted(async () => {
  loading.value = true
  try {
    const data = await getSettings()
    if (data) {
      form.upload_max_mb = data.upload_max_mb ?? form.upload_max_mb
      form.temp_file_ttl_minutes = data.temp_file_ttl_minutes ?? form.temp_file_ttl_minutes
      form.rate_limit_per_minute = data.rate_limit_per_minute ?? form.rate_limit_per_minute
      form.maintenance_mode = !!data.maintenance_mode
    }
  } catch { /* use defaults */ }
  loading.value = false
})

async function beforeMaintenanceToggle(value: boolean): Promise<boolean> {
  if (value) {
    try {
      await ElMessageBox.confirm(
        '开启维护模式后，用户端将暂停服务，所有非管理员请求将被拒绝。确定继续？',
        '确认开启维护模式',
        { confirmButtonText: '确定开启', cancelButtonText: '取消', type: 'warning' }
      )
    } catch { return false }
  }
  return true
}

async function handleSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await updateSettings({ ...form })
    ElMessage.success('系统配置已更新')
  } catch { ElMessage.error('保存失败') }
  submitting.value = false
}
</script>

<style scoped>
.settings-page { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--color-text); letter-spacing: -0.02em; }
.config-form { max-width: 640px; padding-top: 4px; }
.form-section { margin-bottom: 8px; }
.section-title { font-size: 14px; font-weight: 700; color: var(--color-text); margin: 0 0 12px; }
.form-hint { font-size: 12px; color: var(--color-text-muted); margin-left: 12px; }
</style>