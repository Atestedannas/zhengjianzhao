<template>
  <div class="template-edit">
    <div class="page-header">
      <el-button link @click="router.back()" class="back-btn">
        <el-icon><ArrowLeft /></el-icon> 返回
      </el-button>
      <h2 class="page-title">{{ isEdit ? '编辑模板' : '新增模板' }}</h2>
    </div>

    <el-card shadow="never" v-loading="pageLoading">
      <el-form
        ref="formRef"
        :model="form"
        :rules="rules"
        label-width="140px"
        class="edit-form"
      >
        <div class="form-section">
          <h4 class="section-title">基本信息</h4>
          <el-form-item label="模板名称" prop="name">
            <el-input v-model="form.name" placeholder="如 教师招聘2寸" style="max-width: 360px" />
          </el-form-item>
          <el-row :gutter="20" style="max-width: 520px">
            <el-col :span="12">
              <el-form-item label="宽度(px)" prop="width_px">
                <el-input-number v-model="form.width_px" :min="0" :max="10000" style="width: 100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="高度(px)" prop="height_px">
                <el-input-number v-model="form.height_px" :min="0" :max="10000" style="width: 100%" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="DPI" prop="dpi">
            <el-input-number v-model="form.dpi" :min="1" :max="2400" style="width: 200px" />
          </el-form-item>
        </div>

        <el-divider />

        <div class="form-section">
          <h4 class="section-title">输出规格</h4>
          <el-row :gutter="20" style="max-width: 520px">
            <el-col :span="12">
              <el-form-item label="最小KB" prop="min_kb">
                <el-input-number v-model="form.min_kb" :min="0" :max="10240" style="width: 100%" />
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="最大KB" prop="max_kb">
                <el-input-number v-model="form.max_kb" :min="1" :max="10240" style="width: 100%" />
              </el-form-item>
            </el-col>
          </el-row>
          <el-form-item label="背景色" prop="allowed_bg_colors">
            <el-checkbox-group v-model="form.allowed_bg_colors">
              <el-checkbox label="white">白色</el-checkbox>
              <el-checkbox label="blue">蓝色</el-checkbox>
              <el-checkbox label="red">红色</el-checkbox>
            </el-checkbox-group>
          </el-form-item>
          <el-form-item label="输出格式" prop="output_format">
            <el-select v-model="form.output_format" style="width: 200px">
              <el-option label="JPEG" value="JPEG" />
              <el-option label="PNG" value="PNG" />
              <el-option label="WebP" value="WebP" />
            </el-select>
          </el-form-item>
        </div>

        <el-divider />

        <div class="form-section">
          <h4 class="section-title">附加信息</h4>
          <el-form-item label="物理尺寸">
            <el-input v-model="form.physical_size_mm" placeholder="如 35×45mm" style="max-width: 360px" />
          </el-form-item>
          <el-form-item label="备注">
            <el-input
              v-model="form.remark"
              type="textarea"
              :rows="3"
              placeholder="适用场景说明、公告链接等"
              style="max-width: 480px"
            />
          </el-form-item>
        </div>

        <el-form-item>
          <el-button type="primary" :loading="submitting" @click="handleSubmit" size="large">保存</el-button>
          <el-button @click="router.back()" size="large">取消</el-button>
        </el-form-item>
      </el-form>
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { FormInstance, FormRules } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { getTemplates, createTemplate, updateTemplate } from '@/api/templates'
import type { TemplateForm } from '@/api/templates'

const router = useRouter()
const route = useRoute()
const formRef = ref<FormInstance>()
const submitting = ref(false)
const pageLoading = ref(false)

const templateId = computed(() => {
  const id = route.params.id
  return id ? Number(id) : null
})
const isEdit = computed(() => !!templateId.value)

const form = reactive<TemplateForm>({
  name: '',
  width_px: 413,
  height_px: 579,
  dpi: 350,
  min_kb: 0,
  max_kb: 100,
  allowed_bg_colors: ['white'],
  output_format: 'JPEG',
  physical_size_mm: '',
  remark: '',
})

const rules: FormRules = {
  name: [{ required: true, message: '请输入模板名称', trigger: 'blur' }],
  width_px: [{ required: true, message: '请输入宽度' }],
  height_px: [{ required: true, message: '请输入高度' }],
  dpi: [{ required: true, message: '请输入DPI' }],
  min_kb: [{ required: true, message: '请输入最小KB' }],
  max_kb: [{ required: true, message: '请输入最大KB' }],
  allowed_bg_colors: [{ required: true, message: '至少选择一个背景色' }],
  output_format: [{ required: true, message: '请选择输出格式' }],
}

onMounted(async () => {
  if (isEdit.value) {
    pageLoading.value = true
    try {
      const templates = await getTemplates()
      const t = templates.find((t) => t.id === templateId.value)
      if (t) {
        form.name = t.name
        form.width_px = t.width_px
        form.height_px = t.height_px
        form.dpi = t.dpi
        form.min_kb = t.min_kb
        form.max_kb = t.max_kb
        form.allowed_bg_colors = [...t.allowed_bg_colors]
        form.output_format = t.output_format
        form.physical_size_mm = t.physical_size_mm || ''
        form.remark = t.remark || ''
      } else {
        ElMessage.error('模板不存在')
        router.back()
      }
    } catch {
      ElMessage.error('加载模板失败')
      router.back()
    }
    pageLoading.value = false
  }
})

async function handleSubmit() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  submitting.value = true
  try {
    if (isEdit.value) {
      await updateTemplate(templateId.value!, { ...form })
      ElMessage.success('更新成功')
    } else {
      await createTemplate({ ...form })
      ElMessage.success('创建成功')
    }
    router.push('/templates')
  } catch {
    ElMessage.error(isEdit.value ? '更新失败' : '创建失败')
  }
  submitting.value = false
}
</script>

<style scoped>
.template-edit { animation: fadeIn 0.3s ease; }
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
.page-header { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; }
.back-btn { font-size: 14px; }
.page-title { font-size: 20px; font-weight: 700; color: var(--color-text); letter-spacing: -0.02em; }
.edit-form { max-width: 720px; padding-top: 4px; }
.form-section { margin-bottom: 8px; }
.section-title { font-size: 14px; font-weight: 700; color: var(--color-text); margin: 0 0 12px; }
</style>