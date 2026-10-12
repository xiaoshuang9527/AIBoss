import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { modelApi } from '@/api/model'
import type {
  ModelConfig,
  ModelConfigCreatePayload,
  ModelConfigUpdatePayload,
  CapabilityOption,
  ProviderMetadata,
} from '@/types/model'

/**
 * 模型管理状态。
 *
 * `providers` 与 `capabilityOptions` 全部来自后端 `/api/models/providers`：
 * 前端不硬编码供应商清单，因此后端新增供应商时前端**无需改动**。
 */
export const useModelStore = defineStore('model', () => {
  const models = ref<ModelConfig[]>([])
  const providers = ref<ProviderMetadata[]>([])
  const capabilityOptions = ref<CapabilityOption[]>([])
  const loading = ref(false)
  const metaLoaded = ref(false)

  /** 已启用的模型（对话页下拉只展示这些） */
  const enabledModels = computed(() => models.value.filter((item) => item.is_enabled))

  const defaultModel = computed(
    () => models.value.find((item) => item.is_default && item.is_enabled) ?? null,
  )

  const providerMap = computed(() => {
    const map: Record<string, ProviderMetadata> = {}
    for (const item of providers.value) map[item.provider] = item
    return map
  })

  function capabilityLabel(value: string): string {
    return capabilityOptions.value.find((item) => item.value === value)?.label ?? value
  }

  /** 拉取供应商元数据（只需一次，之后走缓存） */
  async function loadMetadata(force = false) {
    if (metaLoaded.value && !force) return
    const data = await modelApi.providers()
    providers.value = data.providers
    capabilityOptions.value = data.capabilities
    metaLoaded.value = true
  }

  async function loadModels() {
    loading.value = true
    try {
      models.value = await modelApi.list()
    } finally {
      loading.value = false
    }
  }

  /** 首次进入模型管理页时一次性准备数据 */
  async function initialize() {
    await Promise.all([loadMetadata(), loadModels()])
  }

  async function createModel(payload: ModelConfigCreatePayload) {
    const created = await modelApi.create(payload)
    await loadModels()
    return created
  }

  async function updateModel(id: number, payload: ModelConfigUpdatePayload) {
    const updated = await modelApi.update(id, payload)
    await loadModels()
    return updated
  }

  async function removeModel(id: number) {
    await modelApi.remove(id)
    models.value = models.value.filter((item) => item.id !== id)
  }

  async function toggleEnabled(model: ModelConfig, enabled: boolean) {
    const updated = await modelApi.setEnabled(model.id, enabled)
    // 设为默认会连带影响其他行，重新拉一次最稳
    await loadModels()
    return updated
  }

  async function setDefault(id: number) {
    await modelApi.setDefault(id)
    await loadModels()
  }

  async function cloneModel(id: number, name?: string) {
    const clone = await modelApi.clone(id, name)
    await loadModels()
    return clone
  }

  async function testModel(id: number) {
    const result = await modelApi.test(id)
    // 测试结果会落库，同步到本地列表
    const index = models.value.findIndex((item) => item.id === id)
    if (index >= 0) {
      models.value[index] = {
        ...models.value[index],
        last_tested_at: new Date().toISOString(),
        last_test_ok: result.ok,
        last_test_message: result.suggestion
          ? `${result.message}｜${result.suggestion}`
          : result.message,
        last_test_latency_ms: result.latency_ms,
      }
    }
    return result
  }

  return {
    models,
    providers,
    capabilityOptions,
    loading,
    enabledModels,
    defaultModel,
    providerMap,
    capabilityLabel,
    loadMetadata,
    loadModels,
    initialize,
    createModel,
    updateModel,
    removeModel,
    toggleEnabled,
    setDefault,
    cloneModel,
    testModel,
  }
})
