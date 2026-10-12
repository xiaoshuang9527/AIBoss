<script setup lang="ts">
/**
 * 模型管理面板（系统设置页的第二个 Tab）。
 *
 * 设计要点：
 * * 供应商与能力清单全部来自后端 `/api/models/providers`，前端**不硬编码**，
 *   因此后端新增供应商时这里无需改动；
 * * 列表支持卡片/表格两种视图 —— 模型数量少时卡片好看，多起来表格更高效；
 * * 密钥只显示掩码，明文永远不会出现在前端。
 */
import { computed, onMounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import ModelCard from './ModelCard.vue'
import ModelFormDialog from './ModelFormDialog.vue'
import ModelTestResultPanel from './ModelTestResultPanel.vue'
import { useModelStore } from '@/stores/model'
import { modelApi } from '@/api/model'
import type { ModelConfig, ModelTestResult } from '@/types/model'

const store = useModelStore()

const viewMode = ref<'card' | 'table'>('card')
const formVisible = ref(false)
const editing = ref<ModelConfig | null>(null)
const testingId = ref<number | null>(null)

/** 测试结果面板（列表页快捷测试时用） */
const testPanelVisible = ref(false)
const testPanelTitle = ref('')
const testPanelResult = ref<ModelTestResult | null>(null)

const isEmpty = computed(() => !store.loading && store.models.length === 0)

onMounted(async () => {
  try {
    await store.initialize()
  } catch {
    ElMessage.error('读取模型配置失败，请确认后端服务正常')
  }
})

function openCreate() {
  editing.value = null
  formVisible.value = true
}

function openEdit(model: ModelConfig) {
  editing.value = model
  formVisible.value = true
}

async function onSaved() {
  formVisible.value = false
  await store.loadModels()
}

async function toggleEnabled(model: ModelConfig, enabled: boolean) {
  try {
    await store.toggleEnabled(model, enabled)
    ElMessage.success(enabled ? '已启用' : '已禁用')
  } catch {
    ElMessage.error('操作失败')
  }
}

async function setDefault(model: ModelConfig) {
  if (model.is_default) return
  try {
    await store.setDefault(model.id)
    ElMessage.success(`已将「${model.display_name}」设为默认模型`)
  } catch {
    ElMessage.error('设置默认模型失败')
  }
}

async function cloneModel(model: ModelConfig) {
  try {
    const clone = await store.cloneModel(model.id, `${model.display_name} 副本`)
    ElMessage.success(`已克隆为「${clone.display_name}」，默认处于禁用状态`)
  } catch {
    ElMessage.error('克隆失败')
  }
}

async function removeModel(model: ModelConfig) {
  try {
    await ElMessageBox.confirm(
      `确认删除模型「${model.display_name}」？该操作不可撤销。`,
      '删除模型',
      { type: 'warning' },
    )
  } catch {
    return
  }
  try {
    await store.removeModel(model.id)
    ElMessage.success('已删除')
  } catch {
    ElMessage.error('删除失败')
  }
}

/** 列表页快捷测试连接 */
async function quickTest(model: ModelConfig) {
  testingId.value = model.id
  testPanelTitle.value = `${model.display_name} · 连通性测试`
  testPanelResult.value = null
  testPanelVisible.value = true
  try {
    testPanelResult.value = await store.testModel(model.id)
  } catch {
    testPanelResult.value = {
      ok: false,
      latency_ms: 0,
      model_echo: null,
      message: '测试请求失败',
      error_type: 'unknown',
      suggestion: '请确认后端服务正常，然后重试。',
      detail: null,
    }
  } finally {
    testingId.value = null
  }
}

function formatTime(value: string | null): string {
  if (!value) return '从未测试'
  const date = new Date(/(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : `${value}Z`)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <section class="bzp-panel">
    <div class="bzp-panel__header">
      <div class="bzp-panel__title">
        <el-icon><Cpu /></el-icon>
        模型管理
        <span class="bzp-panel__subtitle">
          共 {{ store.models.length }} 个 · 已启用 {{ store.enabledModels.length }} 个
        </span>
      </div>

      <div class="models__toolbar">
        <el-radio-group v-model="viewMode" size="small">
          <el-radio-button value="card">
            <el-icon><Grid /></el-icon>
            卡片
          </el-radio-button>
          <el-radio-button value="table">
            <el-icon><List /></el-icon>
            表格
          </el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="store.initialize()">
          <el-icon><Refresh /></el-icon>
          刷新
        </el-button>
        <el-button size="small" type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          添加模型
        </el-button>
      </div>
    </div>

    <div class="bzp-panel__body">
      <el-alert type="info" :closable="false" show-icon class="models__intro">
        <template #title>这里是所有大模型的接入配置</template>
        <template #default>
          添加模型后，AI 对话模块会在顶部提供模型选择下拉框（只列出<strong>已启用</strong>的模型）。
          API 密钥加密存储，界面上只显示掩码，任何接口都不会回传明文。
        </template>
      </el-alert>

      <!-- ============ 空态 ============ -->
      <el-empty v-if="isEmpty" description="还没有配置任何模型">
        <el-button type="primary" @click="openCreate">
          <el-icon><Plus /></el-icon>
          添加第一个模型
        </el-button>
        <div class="models__empty-hint">
          推荐先接入 DeepSeek：供应商选「DeepSeek」，模型 ID 填
          <code>deepseek-flash</code>，API 地址会自动填好。
        </div>
      </el-empty>

      <!-- ============ 卡片视图 ============ -->
      <div v-else-if="viewMode === 'card'" v-loading="store.loading" class="models__cards">
        <ModelCard
          v-for="model in store.models"
          :key="model.id"
          :model="model"
          :testing="testingId === model.id"
          :capability-label="store.capabilityLabel"
          :format-time="formatTime"
          @edit="openEdit"
          @test="quickTest"
          @clone="cloneModel"
          @remove="removeModel"
          @set-default="setDefault"
          @toggle-enabled="toggleEnabled"
        />
      </div>

      <!-- ============ 表格视图 ============ -->
      <el-table v-else v-loading="store.loading" :data="store.models" stripe>
        <el-table-column label="显示名称" min-width="180">
          <template #default="{ row }">
            <div class="models__name-cell">
              <span class="models__name">{{ row.display_name }}</span>
              <el-tag v-if="row.is_default" type="primary" size="small" effect="dark" round>
                默认
              </el-tag>
            </div>
            <div v-if="row.remark" class="bzp-muted models__sub">{{ row.remark }}</div>
          </template>
        </el-table-column>

        <el-table-column label="供应商" width="130">
          <template #default="{ row }">
            <el-tag size="small" effect="plain">{{ row.provider_label }}</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="模型 ID" min-width="170">
          <template #default="{ row }">
            <span class="bzp-mono">{{ row.model_id }}</span>
          </template>
        </el-table-column>

        <el-table-column label="能力" min-width="200">
          <template #default="{ row }">
            <el-tag
              v-for="cap in row.capabilities"
              :key="cap"
              size="small"
              effect="plain"
              class="models__cap"
            >
              {{ store.capabilityLabel(cap) }}
            </el-tag>
          </template>
        </el-table-column>

        <el-table-column label="密钥" width="140">
          <template #default="{ row }">
            <span v-if="row.has_api_key" class="bzp-mono bzp-muted">{{ row.api_key_masked }}</span>
            <el-tag v-else size="small" type="warning" effect="plain">未配置</el-tag>
          </template>
        </el-table-column>

        <el-table-column label="上次测试" min-width="170">
          <template #default="{ row }">
            <el-tag
              v-if="row.last_test_ok === true"
              size="small"
              type="success"
              effect="light"
            >
              成功 {{ row.last_test_latency_ms }}ms
            </el-tag>
            <el-tooltip
              v-else-if="row.last_test_ok === false"
              :content="row.last_test_message || ''"
              placement="top"
            >
              <el-tag size="small" type="danger" effect="light">失败</el-tag>
            </el-tooltip>
            <span v-else class="bzp-muted">未测试</span>
            <div class="bzp-muted models__sub">{{ formatTime(row.last_tested_at) }}</div>
          </template>
        </el-table-column>

        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-switch
              :model-value="row.is_enabled"
              @update:model-value="(value: boolean) => toggleEnabled(row, value)"
            />
          </template>
        </el-table-column>

        <el-table-column label="操作" width="230" fixed="right">
          <template #default="{ row }">
            <el-button link size="small" type="primary" :loading="testingId === row.id" @click="quickTest(row)">
              测试
            </el-button>
            <el-button link size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link size="small" @click="cloneModel(row)">克隆</el-button>
            <el-dropdown trigger="click" @command="(cmd: string) => cmd === 'default' ? setDefault(row) : removeModel(row)">
              <el-button link size="small">更多<el-icon><ArrowDown /></el-icon></el-button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="default" :disabled="row.is_default">
                    设为默认
                  </el-dropdown-item>
                  <el-dropdown-item command="delete" divided>删除</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 编辑 / 新增弹窗 -->
    <ModelFormDialog v-model="formVisible" :model="editing" @saved="onSaved" />

    <!-- 快捷测试结果 -->
    <el-dialog v-model="testPanelVisible" :title="testPanelTitle" width="560px">
      <ModelTestResultPanel :result="testPanelResult" :loading="testingId !== null" />
    </el-dialog>
  </section>
</template>

<style scoped>
.models__toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.models__intro {
  margin-bottom: 16px;
}

.models__empty-hint {
  margin-top: 12px;
  font-size: 12.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.8;
}

.models__empty-hint code {
  background: #f1f4f9;
  padding: 1px 5px;
  border-radius: 4px;
  font-size: 12px;
}

.models__cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}

.models__name-cell {
  display: flex;
  align-items: center;
  gap: 6px;
}

.models__name {
  font-weight: 600;
}

.models__sub {
  font-size: 11.5px;
  margin-top: 2px;
}

.models__cap {
  margin: 0 4px 4px 0;
}
</style>
