<script setup lang="ts">
/**
 * 免责声明与合规说明。
 * 对应需求中的「合规与提示」：醒目位置提示数据仅供个人学习分析。
 */
import { computed } from 'vue'
import { useWorkbenchStore } from '@/stores/workbench'

const store = useWorkbenchStore()
const disclaimer = computed(
  () =>
    store.disclaimer ||
    '本工具仅供个人学习与求职分析使用，数据来源于公开职位页面，严禁用于商业倒卖或大规模抓取。',
)
</script>

<template>
  <div class="bzp-page">
    <section class="bzp-panel">
      <div class="bzp-panel__header">
        <div class="bzp-panel__title">
          <el-icon><Warning /></el-icon>
          免责声明与合规提示
        </div>
      </div>

      <div class="bzp-panel__body disclaimer">
        <el-alert type="warning" :closable="false" show-icon class="disclaimer__banner">
          <template #title>请务必阅读并遵守以下约定</template>
          <template #default>{{ disclaimer }}</template>
        </el-alert>

        <div class="disclaimer__block">
          <h3 class="disclaimer__title">一、工具性质</h3>
          <p>
            本工作台是一个<strong>个人求职辅助工具</strong>，用于把公开的招聘信息聚合、整理，
            便于求职者集中浏览与比较。
            <strong>它不是商业产品，不提供任何付费服务，也不存在额度、套餐、充值或计费机制。</strong>
          </p>
        </div>

        <div class="disclaimer__block">
          <h3 class="disclaimer__title">二、数据用途</h3>
          <p>
            抓取到的岗位数据仅供<strong>个人学习与求职分析</strong>使用。
            严禁用于商业倒卖、批量转售、二次分发，或任何违反
            <a href="https://www.zhipin.com/about/protocol.html" target="_blank" rel="noopener noreferrer">
              BOSS 直聘用户协议
            </a>
            及法律法规的用途。
          </p>
        </div>

        <div class="disclaimer__block">
          <h3 class="disclaimer__title">三、抓取频率与访问节奏</h3>
          <ul class="disclaimer__list">
            <li>单次任务建议不超过 2-3 页；系统硬性限制单任务最多 10 页。</li>
            <li>任务之间会自动插入随机延迟，默认同时最多执行 2 个任务。</li>
            <li>遇到「环境存在异常」「访问频繁」等提示时，请立即停止抓取并等待较长时间。</li>
            <li>系统只在被动捕获页面的搜索接口响应，不会注入额外请求，也不会绕过验证码。</li>
          </ul>
        </div>

        <div class="disclaimer__block">
          <h3 class="disclaimer__title">四、登录态与隐私</h3>
          <ul class="disclaimer__list">
            <li>登录态保存在工作台自有的隔离浏览器 profile 目录中，不读取你日常使用的浏览器数据。</li>
            <li>Cookie 快照会加密存储，数据库只记录文件路径与指纹，不含明文凭据。</li>
            <li>请确保服务端目录权限可控；如与他人共用服务器，务必修改 <code>BZP_SECRET_KEY</code>。</li>
          </ul>
        </div>

        <div class="disclaimer__block">
          <h3 class="disclaimer__title">五、责任划分</h3>
          <p>
            因使用本工具产生的一切后果由使用者自行承担。请在使用前确认你的行为符合目标网站的服务条款
            以及所在地区的法律法规。若目标网站明确要求停止访问，请立即停止使用本工具。
          </p>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.disclaimer {
  max-width: 900px;
  line-height: 1.9;
  font-size: 13.5px;
  color: #33415c;
}

.disclaimer__banner {
  margin-bottom: 22px;
}

.disclaimer__block {
  margin-bottom: 22px;
}

.disclaimer__title {
  margin: 0 0 8px;
  font-size: 14.5px;
  color: var(--bzp-text);
}

.disclaimer__list {
  margin: 0;
  padding-left: 20px;
}

.disclaimer__list li {
  margin-bottom: 4px;
}

code {
  background: #f1f4f9;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12.5px;
}

a {
  color: var(--bzp-primary);
}
</style>
