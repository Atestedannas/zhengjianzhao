<template>
  <div class="home-page">
    <!-- Hero 区域 -->
    <section class="hero-section">
      <div class="container hero-content">
        <div class="hero-text">
          <div class="hero-badge">
            <el-icon><MagicStick /></el-icon>
            <span>AI 智能处理</span>
          </div>
          <h1 class="hero-title">
            在线制作<span class="gradient-text">专业证件照</span>
          </h1>
          <p class="hero-desc">
            一键上传，AI 自动抠图换背景，支持多种证件照规格、自然微调、格式转换，
            足不出户就能拍出满意的证件照。
          </p>
          <div class="hero-actions">
            <el-button type="primary" size="large" @click="goEditor">
              <el-icon><Camera /></el-icon>
              立即制作
            </el-button>
            <el-button size="large" @click="scrollToFeatures">
              了解更多
            </el-button>
          </div>
          <div class="hero-stats">
            <div class="stat-item">
              <div class="stat-number">50+</div>
              <div class="stat-label">证件照规格</div>
            </div>
            <div class="stat-item">
              <div class="stat-number">10s</div>
              <div class="stat-label">快速处理</div>
            </div>
            <div class="stat-item">
              <div class="stat-number">99%</div>
              <div class="stat-label">准确率</div>
            </div>
          </div>
        </div>
        <div class="hero-visual">
          <div class="photo-showcase">
            <div class="showcase-before">
              <div class="showcase-label">原图</div>
              <div class="showcase-img placeholder-before">
                <el-icon :size="64" color="#c0c4cc"><Picture /></el-icon>
              </div>
            </div>
            <div class="showcase-arrow">
              <el-icon :size="32" color="#409eff"><ArrowRight /></el-icon>
            </div>
            <div class="showcase-after">
              <div class="showcase-label">处理后</div>
              <div class="showcase-img placeholder-after">
                <el-icon :size="64" color="#67c23a"><CircleCheck /></el-icon>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 功能特性 -->
    <section id="features" class="features-section">
      <div class="container">
        <div class="section-header">
          <h2 class="section-title">核心功能</h2>
          <p class="section-desc">强大的 AI 图像处理能力，满足你的各种需求</p>
        </div>
        <div class="features-grid">
          <div
            class="feature-card"
            v-for="feature in features"
            :key="feature.key"
            role="button"
            tabindex="0"
            :aria-label="`进入${feature.title}`"
            @click="goFeature(feature.key)"
            @keyup.enter="goFeature(feature.key)"
          >
            <div class="feature-icon" :style="{ background: feature.bgColor }">
              <el-icon :size="28" color="#fff">
                <component :is="feature.icon" />
              </el-icon>
            </div>
            <h3 class="feature-title">{{ feature.title }}</h3>
            <p class="feature-desc">{{ feature.desc }}</p>
          </div>
        </div>
      </div>
    </section>

    <!-- 热门模板 -->
    <section class="templates-section">
      <div class="container">
        <div class="section-header">
          <h2 class="section-title">热门规格</h2>
          <p class="section-desc">常用证件照尺寸一键选择</p>
        </div>
        <div class="templates-grid">
          <div
            class="template-card card-hover"
            v-for="tmpl in displayTemplates"
            :key="tmpl.id"
            @click="goEditorWithTemplate(tmpl.id)"
          >
            <div class="template-preview" :style="{ background: getPreviewBg(tmpl) }">
              <div class="template-size">{{ tmpl.width_px }} × {{ tmpl.height_px }}px</div>
            </div>
            <div class="template-info">
              <h4 class="template-name">{{ tmpl.name }}</h4>
              <div class="template-meta">
                <span class="badge badge-primary">{{ tmpl.dpi }} DPI</span>
                <span class="badge badge-info">{{ tmpl.output_format }}</span>
              </div>
              <p class="template-remark">{{ tmpl.remark || '标准证件照规格' }}</p>
            </div>
          </div>
        </div>
        <div class="templates-more">
          <el-button type="primary" size="large" @click="goEditor">
            查看全部规格
            <el-icon><ArrowRight /></el-icon>
          </el-button>
        </div>
      </div>
    </section>

    <!-- 使用步骤 -->
    <section class="steps-section">
      <div class="container">
        <div class="section-header">
          <h2 class="section-title">使用步骤</h2>
          <p class="section-desc">三步完成证件照制作，简单快捷</p>
        </div>
        <div class="steps-row">
          <template v-for="(step, index) in steps" :key="step.title">
            <div class="step-item">
              <div class="step-number">{{ index + 1 }}</div>
              <div class="step-icon">
                <el-icon :size="32">
                  <component :is="step.icon" />
                </el-icon>
              </div>
              <h3 class="step-title">{{ step.title }}</h3>
              <p class="step-desc">{{ step.desc }}</p>
            </div>
            <div class="step-line" v-if="index < steps.length - 1"></div>
          </template>
        </div>
      </div>
    </section>

    <!-- CTA 区域 -->
    <section class="cta-section">
      <div class="container cta-content">
        <h2 class="cta-title">准备好制作你的证件照了吗？</h2>
        <p class="cta-desc">立即开始，免费体验 AI 智能证件照处理</p>
        <el-button type="primary" size="large" @click="goEditor">
          立即开始制作
          <el-icon><ArrowRight /></el-icon>
        </el-button>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import {
  MagicStick,
  Camera,
  Picture,
  ArrowRight,
  CircleCheck,
  Scissor,
  Brush,
  Files,
  Position,
  PictureFilled,
  UploadFilled,
  Download,
} from '@element-plus/icons-vue'
import { usePhotoStore } from '@/stores/photo'
import type { TemplateItem } from '@/api'

const router = useRouter()
const photoStore = usePhotoStore()

const displayTemplates = ref<TemplateItem[]>([])

const features = [
  {
    key: 'cutout',
    icon: Scissor,
    title: '智能抠图',
    desc: 'AI 自动识别人像轮廓，精准抠图，边缘自然过渡',
    bgColor: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
  },
  {
    key: 'bg',
    icon: Brush,
    title: '背景替换',
    desc: '支持白、蓝、红等多种背景色，满足不同证件照要求',
    bgColor: 'linear-gradient(135deg, #f093fb 0%, #f5576c 100%)',
  },
  {
    key: 'beautify',
    icon: PictureFilled,
    title: '自然微调',
    desc: '轻度匀肤提亮、去除临时瑕疵，不改五官比例、保留皮肤原生纹理',
    bgColor: 'linear-gradient(135deg, #4facfe 0%, #00f2fe 100%)',
  },
  {
    key: 'size',
    icon: Position,
    title: '尺寸裁剪',
    desc: '支持一寸、二寸、小一寸等 50+ 种证件照规格',
    bgColor: 'linear-gradient(135deg, #43e97b 0%, #38f9d7 100%)',
  },
  {
    key: 'format',
    icon: Files,
    title: '格式转换',
    desc: '支持 JPG、PNG、PDF、WEBP 等多种格式输出',
    bgColor: 'linear-gradient(135deg, #fa709a 0%, #fee140 100%)',
  },
  {
    key: 'dpi',
    icon: Download,
    title: '高清输出',
    desc: '300DPI 高清输出，满足打印和网上报名等各种需求',
    bgColor: 'linear-gradient(135deg, #a8edea 0%, #fed6e3 100%)',
  },
]

const steps = [
  {
    icon: UploadFilled,
    title: '上传照片',
    desc: '选择一张正面免冠照片上传',
  },
  {
    icon: Brush,
    title: '选择规格',
    desc: '选择需要的证件照尺寸和背景色',
  },
  {
    icon: Download,
    title: '下载结果',
    desc: '一键下载处理好的证件照',
  },
]

onMounted(() => {
  photoStore.loadTemplates().then(() => {
    displayTemplates.value = photoStore.templates.slice(0, 6)
  })
})

function goEditor() {
  router.push('/editor')
}

function goEditorWithTemplate(templateId: number) {
  router.push({ path: '/editor', query: { template: String(templateId) } })
}

/** 功能卡片 → 编辑器对应页签（编辑器按 ?feature= 预置参数） */
function goFeature(featureKey: string) {
  router.push({ path: '/editor', query: { feature: featureKey } })
}

function scrollToFeatures() {
  const el = document.getElementById('features')
  el?.scrollIntoView({ behavior: 'smooth' })
}

function getPreviewBg(tmpl: TemplateItem): string {
  const colors = tmpl.allowed_bg_colors
  if (!colors || colors.length === 0) return '#f5f7fa'
  const firstColor = colors[0]
  const colorMap: Record<string, string> = {
    white: '#ffffff',
    blue: '#2b75d9',
    red: '#d92b2b',
    gray: '#808080',
  }
  return colorMap[firstColor] || '#f5f7fa'
}
</script>

<style scoped>
.home-page {
  min-height: 100vh;
}

/* ===== Hero Section ===== */
.hero-section {
  background: linear-gradient(180deg, #eef2ff 0%, #f5f7fa 100%);
  padding: 80px 0 100px;
}

.hero-content {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 60px;
  align-items: center;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: #e0e7ff;
  color: #4f46e5;
  border-radius: 20px;
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 20px;
}

.hero-title {
  font-size: 48px;
  font-weight: 700;
  color: #1f2d3d;
  line-height: 1.2;
  margin-bottom: 20px;
}

.hero-desc {
  font-size: 16px;
  color: #606266;
  line-height: 1.8;
  margin-bottom: 32px;
  max-width: 500px;
}

.hero-actions {
  display: flex;
  gap: 16px;
  margin-bottom: 48px;
}

.hero-actions .el-button {
  padding: 14px 28px;
  font-size: 16px;
  border-radius: 8px;
}

.hero-stats {
  display: flex;
  gap: 48px;
}

.stat-item {
  text-align: center;
}

.stat-number {
  font-size: 32px;
  font-weight: 700;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

.stat-label {
  font-size: 13px;
  color: #909399;
  margin-top: 4px;
}

.hero-visual {
  display: flex;
  justify-content: center;
}

.photo-showcase {
  display: flex;
  align-items: center;
  gap: 20px;
  background: #fff;
  padding: 32px;
  border-radius: 16px;
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.08);
}

.showcase-before,
.showcase-after {
  text-align: center;
}

.showcase-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 12px;
  font-weight: 500;
}

.showcase-img {
  width: 140px;
  height: 180px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 2px dashed #e4e7ed;
}

.placeholder-before {
  background: #f5f7fa;
}

.placeholder-after {
  background: #f0f9eb;
  border-color: #67c23a;
}

.showcase-arrow {
  display: flex;
  align-items: center;
}

/* ===== Section Common ===== */
.section-header {
  text-align: center;
  margin-bottom: 48px;
}

.section-title {
  font-size: 32px;
  font-weight: 700;
  color: #1f2d3d;
  margin-bottom: 12px;
}

.section-desc {
  font-size: 16px;
  color: #909399;
}

/* ===== Features Section ===== */
.features-section {
  padding: 80px 0;
  background: #fff;
}

.features-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 24px;
}

.feature-card {
  padding: 32px 24px;
  border-radius: 12px;
  background: #fafafa;
  transition: all 0.3s;
  cursor: pointer;
}

.feature-card:hover {
  background: #fff;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  transform: translateY(-4px);
}

.feature-icon {
  width: 56px;
  height: 56px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-bottom: 20px;
}

.feature-title {
  font-size: 18px;
  font-weight: 600;
  color: #1f2d3d;
  margin-bottom: 10px;
}

.feature-desc {
  font-size: 14px;
  color: #909399;
  line-height: 1.6;
  margin: 0;
}

/* ===== Templates Section ===== */
.templates-section {
  padding: 80px 0;
  background: #f5f7fa;
}

.templates-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 20px;
  margin-bottom: 40px;
}

.template-card {
  background: #fff;
  border-radius: 12px;
  overflow: hidden;
  cursor: pointer;
  transition: all 0.3s;
}

.template-card:hover {
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.1);
  transform: translateY(-4px);
}

.template-preview {
  height: 160px;
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.template-size {
  background: rgba(0, 0, 0, 0.5);
  color: #fff;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 13px;
}

.template-info {
  padding: 20px;
}

.template-name {
  font-size: 16px;
  font-weight: 600;
  color: #1f2d3d;
  margin: 0 0 10px 0;
}

.template-meta {
  display: flex;
  gap: 8px;
  margin-bottom: 10px;
}

.template-remark {
  font-size: 13px;
  color: #909399;
  margin: 0;
  line-height: 1.5;
}

.templates-more {
  text-align: center;
}

/* ===== Steps Section ===== */
.steps-section {
  padding: 80px 0;
  background: #fff;
}

.steps-row {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  position: relative;
}

.step-item {
  flex: 1;
  max-width: 280px;
  text-align: center;
  position: relative;
  z-index: 1;
}

.step-number {
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: #fff;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 16px;
}

.step-icon {
  width: 80px;
  height: 80px;
  border-radius: 20px;
  background: #f0f7ff;
  color: #409eff;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 20px;
}

.step-title {
  font-size: 18px;
  font-weight: 600;
  color: #1f2d3d;
  margin-bottom: 8px;
}

.step-desc {
  font-size: 14px;
  color: #909399;
  line-height: 1.6;
  margin: 0;
}

.step-line {
  flex: 0 0 60px;
  height: 2px;
  background: #e4e7ed;
  margin-top: 56px;
}

/* ===== CTA Section ===== */
.cta-section {
  padding: 80px 0;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

.cta-content {
  text-align: center;
  color: #fff;
}

.cta-title {
  font-size: 32px;
  font-weight: 700;
  margin-bottom: 12px;
}

.cta-desc {
  font-size: 16px;
  opacity: 0.9;
  margin-bottom: 32px;
}

.cta-content .el-button {
  padding: 14px 32px;
  font-size: 16px;
  border-radius: 8px;
}

/* ===== Responsive ===== */
@media (max-width: 768px) {
  .hero-section {
    padding: 40px 0 60px;
  }

  .hero-content {
    grid-template-columns: 1fr;
    gap: 40px;
  }

  .hero-title {
    font-size: 32px;
  }

  .hero-stats {
    gap: 24px;
  }

  .stat-number {
    font-size: 24px;
  }

  .photo-showcase {
    padding: 20px;
    flex-direction: column;
  }

  .showcase-arrow {
    transform: rotate(90deg);
  }

  .section-title {
    font-size: 24px;
  }

  .features-grid,
  .templates-grid {
    grid-template-columns: 1fr;
  }

  .steps-row {
    flex-direction: column;
    align-items: center;
  }

  .step-line {
    width: 2px;
    height: 40px;
    flex: 0 0 40px;
    margin-top: 0;
  }

  .cta-title {
    font-size: 24px;
  }
}
</style>
