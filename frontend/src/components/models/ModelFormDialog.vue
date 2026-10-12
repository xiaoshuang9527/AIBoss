<script setup lang="ts">
/**
 * 模型新增 / 编辑弹窗。
 *
 * 联动规则（全部由后端元数据驱动，前端不硬编码）：
 * * 选供应商 → 自动填默认 API 地址；不可编辑地址的供应商会锁定输入框
 * * 选供应商 → 能力多选框只显示该供应商支持的能力
 * * 供应商不需要密钥（如本地 Ollama）→ 隐藏密钥输入
 * * 编辑已有模型但没改密钥 → 测试连接时复用库里已存的 Key
 */
import { computed, reactive, ref, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { modelApi } from '@/api/model'
import { useModelStore } from '@/stores/model'
import ModelTestResultPanel from './ModelTestResultPanel.vue'
import type { ModelConfig, ModelTestResult } from '@/types/model'

const props = defineProps<{
  model: ModelConfig | null
}>()

const visible = defineModel<boolean>({ required: true })

const emit = defineEmits<{ (event: 'saved'): void }>()

const store = useModelStore()
const formRef = ref<FormInstance>()
const saving = ref(false)
const testing = ref(false)
const testResult = ref<ModelTestResult | null>(null)

const isEdit = computed(() => props.model !== null)

const form = reactive({
  display_name: '',
  provider: '',
  model_id: '',
  base_url: '',
  api_key: '',
  capabilities: [] as string[],
  is_enabled: true,
  is_default: false,
  remark: '',
})

/** 当前选中的供应商元数据 */
const currentProvider = computed(() => store.providerMap[form.provider] ?? null)

/** 该供应商支持的能力（用于限制多选框选项） */
const availableCapabilities = computed(() => {
  const meta = currentProvider.value
  if (!meta) return store.capabilityOptions
  const allowed = new Set(meta.supported_capabilities)
  return store.capabilityOptions.filter((item) => allowed.has(item.value))
})

const requiresApiKey = computed(() => currentProvider.value?.requires_api_key ?? true)
const baseUrlEditable = computed(() => currentProvider.value?.base_url_editable ?? true)
const suggestedModels = computed(() => currentProvider.value?.suggested_models ?? [])
const providerNotes = computed(() => currentProvider.value?.notes ?? [])

/** 编辑已有模型且原本就有密钥，且本次没重新输入 */
const keepsExistingKey = computed(
  () => isEdit.value && Boolean(props.model?.has_api_key) && !form.api_key.trim(),
)

const rules: FormRules = {
  display_name: [{ required: true, message: '请输入显示名称', trigger: 'blur' }],
  provider: [{ required: true, message: '请选择供应商', trigger: 'change' }],
  model_id: [{ required: true, message: '请输入模型 ID', trigger: 'blur' }],
  capabilities: [
    {
      type: 'array',
      required: true,
      min: 1,
      message: '至少选择一项能力，否则模型无法参与对话',
      trigger: 'change',
    },
  ],
}

/** 供应商变化时重置能力选择（避免残留上一个供应商的、不受支持的标签） */
watch(
  () => form.provider,
  (provider, previous) => {
    if (!provider || provider === previous) return
    const meta = store.providerMap[provider]
    if (!meta) return

    // 自动填默认地址
    if (meta.default_base_url) {
      form.base_url = meta.default_base_url
    } else if (!meta.base_url_editable) {
      form.base_url = ''
    }

    // 过滤掉新供应商不支持的能力
    const allowed = new Set(meta.supported_capabilities)
    const filtered = form.capabilities.filter((item) => allowed.has(item))
    if (filtered.length !== form.capabilities.length) {
      form.capabilities = filtered
      if (previous) {
        ElMessage.info('已移除当前供应商不支持的能力标签')
      }
    }

    // 不需要密钥的供应商，清掉误填的内容
    if (!meta.requires_api_key) {
      form.api_key = ''
    }
  },
)

/** 打开弹窗时初始化表单 */
watch(
  () => [visible.value, props.model] as const,
  async ([open]) => {
    if (!open) return
    testResult.value = null
    await store.loadMetadata().catch(() => undefined)

    const source = props.model
    if (source) {
      Object.assign(form, {
        display_name: source.display_name,
        provider: source.provider,
        model_id: source.model_id,
        base_url: source.base_url ?? '',
        api_key: '', // 密钥不回显，留空表示不修改
        capabilities: [...source.capabilities],
        is_enabled: source.is_enabled,
        is_default: source.is_default,
        remark: source.remark ?? '',
      })
    } else {
      const first = store.providers[0]
      Object.assign(form, {
        display_name: '',
        provider: first?.provider ?? '',
        model_id: '',
        base_url: first?.default_base_url ?? '',
        api_key: '',
        capabilities: first ? [...first.supported_capabilities].filter((c) => c === 'text') : ['text'],
        is_enabled: true,
        is_default: false,
        remark: '',
      })
    }
    formRef.value?.clearValidate()
  },
  { immediate: true },
)

function buildTestPayload() {
  return {
    provider: form.provider,
    model_id: form.model_id,
    base_url: form.base_url || null,
    // 没填密钥且是编辑模式 → 让后端复用库里已存的密钥
    api_key: form.api_key.trim() || null,
    capabilities: form.capabilities,
    model_config_id: keepsExistingKey.value ? props.model?.id ?? null : null,
  }
}

async function onTest() {
  if (!formRef.value) return
  // 测试只需要供应商 + 模型 ID 有效，不强制校验全部字段
  const valid = await formRef.value
    .validateField(['provider', 'model_id'])
    .then(() => true)
    .catch(() => false)
  if (!valid) return

  testing.value = true
  testResult.value = null
  try {
    testResult.value = await modelApi.testDraft(buildTestPayload())
  } catch {
    testResult.value = {
      ok: false,
      latency_ms: 0,
      model_echo: null,
      message: '测试请求失败',
      error_type: 'unknown',
      suggestion: '请确认后端服务正常，然后重试。',
      detail: null,
    }
  } finally {
    testing.value = false
  }
}

async function onSave() {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  if (requiresApiKey.value && !form.api_key.trim() && !keepsExistingKey.value) {
    ElMessage.warning('该供应商需要 API Key，请填写后再保存')
    return
  }

  saving.value = true
  try {
    if (isEdit.value && props.model) {
      await store.updateModel(props.model.id, {
        display_name: form.display_name.trim(),
        provider: form.provider,
        model_id: form.model_id.trim(),
        base_url: form.base_url.trim() || null,
        // 留空 = 保持原密钥不变
        api_key: form.api_key.trim() || undefined,
        capabilities: form.capabilities,
        is_enabled: form.is_enabled,
        is_default: form.is_default,
        remark: form.remark.trim() || null,
      })
      ElMessage.success('已保存')
    } else {
      await store.createModel({
        display_name: form.display_name.trim(),
        provider: form.provider,
        model_id: form.model_id.trim(),
        base_url: form.base_url.trim() || null,
        api_key: form.api_key.trim() || null,
        capabilities: form.capabilities,
        is_enabled: form.is_enabled,
        is_default: form.is_default,
        remark: form.remark.trim() || null,
      })
      ElMessage.success('已添加')
    }
    emit('saved')
  } catch {
    // http 拦截器已提示（含 409 重复、422 校验失败）
  } finally {
    saving.value = false
  }
}

function onCancel() {
  visible.value = false
}

function applySuggested(modelId: string) {
  form.model_id = modelId
  if (!form.display_name.trim()) {
    form.display_name = modelId
  }
}
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="isEdit ? `编辑模型 · ${props.model?.display_name ?? ''}` : '添加模型'"
    width="660px"
    top="6vh"
    destroy-on-close
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="104px" label-position="right">
      <el-form-item label="显示名称" prop="display_name">
        <el-input
          v-model="form.display_name"
          placeholder="如：DeepSeek V4.1 Flash"
          maxlength="128"
          show-word-limit
        />
      </el-form-item>

      <el-form-item label="供应商" prop="provider">
        <el-select v-model="form.provider" placeholder="请选择" style="width: 100%">
          <el-option
            v-for="item in store.providers"
            :key="item.provider"
            :label="item.display_name"
            :value="item.provider"
          >
            <div class="form-dialog__option">
              <span>{{ item.display_name }}</span>
              <span v-if="!item.requires_api_key" class="bzp-muted form-dialog__option-note">
                无需密钥
              </span>
            </div>
          </el-option>
        </el-select>
        <div v-if="currentProvider?.doc_url" class="form-dialog__hint">
          <a :href="currentProvider.doc_url" target="_blank" rel="noopener noreferrer">
            查看 {{ currentProvider.display_name }} 接口文档
          </a>
        </div>
      </el-form-item>

      <el-form-item label="模型 ID" prop="model_id">
        <el-input v-model="form.model_id" placeholder="如：deepseek-flash" maxlength="128" />
        <div v-if="suggestedModels.length" class="form-dialog__suggest">
          <span class="bzp-muted">常用：</span>
          <el-tag
            v-for="item in suggestedModels"
            :key="item"
            size="small"
            effect="plain"
            class="form-dialog__suggest-tag"
            @click="applySuggested(item)"
          >
            {{ item }}
          </el-tag>
        </div>
      </el-form-item>

      <el-form-item label="API 地址">
        <el-input
          v-model="form.base_url"
          :disabled="!baseUrlEditable"
          placeholder="留空则使用供应商默认地址"
        />
        <div class="form-dialog__hint">
          留空时使用该供应商的默认地址<template v-if="currentProvider?.default_base_url">
            （{{ currentProvider.default_base_url }}）</template>。
        </div>
      </el-form-item>

      <el-form-item v-if="requiresApiKey" label="API 密钥">
        <el-input
          v-model="form.api_key"
          type="password"
          show-password
          :placeholder="isEdit && props.model?.has_api_key ? '留空表示不修改现有密钥' : '请粘贴 API Key'"
        />
        <div class="form-dialog__hint">
          <template v-if="keepsExistingKey">
            当前已保存密钥 <span class="bzp-mono">{{ props.model?.api_key_masked }}</span>，
            留空即可保持不变；只有填入新值才会覆盖。
          </template>
          <template v-else>
            密钥会加密存储，界面与接口都只会显示掩码，不会回传明文。
          </template>
        </div>
      </el-form-item>

      <el-form-item label="能力标签" prop="capabilities">
        <el-checkbox-group v-model="form.capabilities">
          <el-checkbox
            v-for="item in availableCapabilities"
            :key="item.value"
            :value="item.value"
          >
            <el-tooltip :content="item.description" placement="top">
              <span>{{ item.label }}</span>
            </el-tooltip>
          </el-checkbox>
        </el-checkbox-group>
        <div class="form-dialog__hint">
          只显示该供应商支持的能力。勾选结果会用于调用前的前置校验 ——
          模型缺少所需能力时会直接报错，而不是等接口返回失败。
        </div>
      </el-form-item>

      <el-form-item label="备注">
        <el-input v-model="form.remark" placeholder="选填，便于日后识别" maxlength="255" />
      </el-form-item>

      <el-form-item label="状态">
        <el-switch v-model="form.is_enabled" active-text="启用" inactive-text="禁用" />
        <el-checkbox v-model="form.is_default" class="form-dialog__default">
          设为默认模型
        </el-checkbox>
      </el-form-item>

      <el-alert
        v-for="(note, index) in providerNotes"
        :key="index"
        type="info"
        :closable="false"
        class="form-dialog__note"
      >
        {{ note }}
      </el-alert>

      <!-- 测试结果就地展示，不用另开弹窗 -->
      <div v-if="testResult || testing" class="form-dialog__test">
        <ModelTestResultPanel :result="testResult" :loading="testing" />
      </div>
    </el-form>

    <template #footer>
      <div class="form-dialog__footer">
        <el-button :loading="testing" @click="onTest">
          <el-icon><Link /></el-icon>
          测试连接
        </el-button>
        <div class="form-dialog__footer-right">
          <el-button @click="onCancel">取消</el-button>
          <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
        </div>
      </div>
    </template>
  </el-dialog>
</template>

<style scoped>
.form-dialog__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.form-dialog__option-note {
  font-size: 11px;
}

.form-dialog__hint {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--bzp-text-secondary);
  line-height: 1.7;
}

.form-dialog__hint a {
  color: var(--bzp-primary);
}

.form-dialog__suggest {
  margin-top: 6px;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  font-size: 12px;
}

.form-dialog__suggest-tag {
  cursor: pointer;
}

.form-dialog__default {
  margin-left: 16px;
}

.form-dialog__note {
  margin-bottom: 8px;
}

.form-dialog__test {
  margin-top: 12px;
}

.form-dialog__footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.form-dialog__footer-right {
  display: flex;
  gap: 8px;
}
</style>
