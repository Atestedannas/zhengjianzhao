<template>
  <div class="pricing-page">
    <div class="page-header">
      <h2 class="page-title">价格策略</h2>
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
          <h4 class="section-title">基础定价</h4>
          <el-form-item label="单次处理价格（元）" prop="unit_price">
            <el-input-number
              v-model="form.unit_price"
              :precision="2"
              :min="0"
              :step="0.1"
              style="width: 200px"
            />
            <span class="form-hint">用户每次照片处理需支付的费用</span>
          </el-form-item>
          <el-form-item label="新用户注册赠送次数" prop="register_bonus">
            <el-input-number
              v-model="form.register_bonus"
              :min="0"
              style="width: 200px"
            />
            <span class="form-hint">新用户注册后可免费处理的次数</span>
          </el-form-item>
        </div>

        <el-divider />

        <div class="form-section">
          <h4 class="section-title">每日免费</h4>
          <el-form-item label="每日免费次数">
            <el-switch v-model="form.daily_bonus_enabled" />
            <span class="form-hint" style="margin-left: 12px">
              {{ form.daily_bonus_enabled ? '已开启 - 用户每天可领取免费次数' : '已关闭' }}
            </span>
          </el-form-item>
          <el-form-item v-if="form.daily_bonus_enabled" label="每日赠送数量" prop="daily_bonus_count">
            <el-input-number
              v-model="form.daily_bonus_count"
              :min="1"
              style="width: 200px"
            />
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
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { getPricing, updatePricing } from '@/api/pricing'
import type { PricingConfig } from '@/api/pricing'

const formRef = ref<FormInstance>()
const loading = ref(false)
const submitting = ref(false)

const form = reactive<PricingConfig>({
  unit_price: 0.99,
  register_bonus: 3,
  daily_bonus_enabled: true,
  daily_bonus_count: 1,
})

const rules: FormRules = {
  unit_price: [{ required: true, message: '请输入价格' }],
  register_bonus: [{ required: true, message: '请输入赠送次数' }],
}

onMounted(async () => {
  loading.value = true
  try {
    const data = await getPricing()
    if (data) {
      form.unit_price = data.unit_price ?? form.unit_price
      form.register_bonus = data.register_bonus ?? form.register_bonus
      form.daily_bonus_enabled = data.daily_bonus_enabled ?? form.daily_bonus_enabled
      form.daily_bonus_count = data.daily_bonus_count ?? form.daily_bonus_count
    }
  } catch { /* use defaults */ }
  loading.value = false
})

async function handleSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return
  submitting.value = true
  try {
    await updatePricing({ ...form })
    ElMessage.success('价格策略已更新')
  } catch { ElMessage.error('保存失败') }
  submitting.value = false
}
</script>

<style scoped>
.pricing-page { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header { margin-bottom: 16px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--color-text); letter-spacing: -0.02em; }
.config-form { max-width: 640px; padding-top: 4px; }
.form-section { margin-bottom: 8px; }
.section-title { font-size: 14px; font-weight: 700; color: var(--color-text); margin: 0 0 12px; }
.form-hint { font-size: 12px; color: var(--color-text-muted); margin-left: 12px; }
</style>