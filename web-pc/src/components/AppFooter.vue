<template>
  <footer class="app-footer">
    <div class="container footer-inner">
      <div class="footer-section brand-section">
        <div class="footer-logo">
          <div class="logo-icon">
            <el-icon :size="24" color="#fff">
              <Picture />
            </el-icon>
          </div>
          <span class="footer-brand">智能证件照</span>
        </div>
        <p class="footer-desc">
          基于 AI 技术的在线证件照处理平台，支持智能抠图、背景替换、自然微调、尺寸裁剪等功能。
        </p>
      </div>

      <div class="footer-section">
        <h4 class="footer-title">功能服务</h4>
        <ul class="footer-links">
          <li><router-link to="/editor">证件照制作</router-link></li>
          <li><router-link to="/editor">背景替换</router-link></li>
          <li><router-link to="/editor">自然微调</router-link></li>
          <li><router-link to="/editor">尺寸调整</router-link></li>
        </ul>
      </div>

      <div class="footer-section">
        <h4 class="footer-title">帮助中心</h4>
        <ul class="footer-links">
          <li><a href="#">使用教程</a></li>
          <li><a href="#">常见问题</a></li>
          <li><a href="#">规格说明</a></li>
          <li><a href="#">联系我们</a></li>
        </ul>
      </div>

      <div class="footer-section">
        <h4 class="footer-title">关于我们</h4>
        <ul class="footer-links">
          <li><a href="#">产品介绍</a></li>
          <li><a href="#">隐私政策</a></li>
          <li><a href="#">服务条款</a></li>
          <li><a href="#">意见反馈</a></li>
        </ul>
      </div>
    </div>

    <div class="footer-bottom">
      <div class="container footer-bottom-inner">
        <p>© {{ currentYear }} 智能证件照. All rights reserved.</p>
        <div class="footer-tech">
          <span>Powered by AI</span>
          <el-icon><Cpu /></el-icon>
        </div>
      </div>
    </div>
  </footer>
</template>

<script setup lang="ts">
import { Picture, Cpu } from '@element-plus/icons-vue'

const currentYear = new Date().getFullYear()
</script>

<style scoped>
.app-footer {
  background: #1f2d3d;
  color: #c0c4cc;
  margin-top: auto;
}

.footer-inner {
  display: grid;
  grid-template-columns: 2fr 1fr 1fr 1fr;
  gap: 40px;
  padding: 48px 20px 32px;
}

.footer-section {
  display: flex;
  flex-direction: column;
}

.brand-section {
  max-width: 320px;
}

.footer-logo {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.logo-icon {
  width: 36px;
  height: 36px;
  border-radius: 8px;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  display: flex;
  align-items: center;
  justify-content: center;
}

.footer-brand {
  font-size: 18px;
  font-weight: 600;
  color: #fff;
}

.footer-desc {
  font-size: 13px;
  line-height: 1.7;
  color: #909399;
  margin: 0;
}

.footer-title {
  font-size: 15px;
  font-weight: 600;
  color: #fff;
  margin: 0 0 16px 0;
}

.footer-links {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.footer-links a {
  color: #909399;
  font-size: 13px;
  text-decoration: none;
  transition: color 0.2s;
}

.footer-links a:hover {
  color: #409eff;
}

.footer-bottom {
  border-top: 1px solid #304156;
  padding: 16px 0;
}

.footer-bottom-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  color: #606266;
}

.footer-bottom-inner p {
  margin: 0;
}

.footer-tech {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #606266;
}

@media (max-width: 768px) {
  .footer-inner {
    grid-template-columns: 1fr 1fr;
    gap: 24px;
    padding: 32px 16px 24px;
  }

  .brand-section {
    grid-column: 1 / -1;
    max-width: 100%;
  }

  .footer-bottom-inner {
    flex-direction: column;
    gap: 8px;
    text-align: center;
  }
}
</style>
