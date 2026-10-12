/** 模型管理相关类型，与后端 schemas.py 一一对应。 */

export interface ModelConfig {
  id: number
  display_name: string
  provider: string
  /** 后端给的供应商中文名，避免前端再维护一份映射 */
  provider_label: string
  model_id: string
  base_url: string | null
  capabilities: string[]
  extra_config: Record<string, unknown>

  is_enabled: boolean
  is_default: boolean
  sort_order: number
  remark: string | null

  /** ⚠️ 后端永不返回明文密钥，只有掩码 */
  has_api_key: boolean
  api_key_masked: string | null

  last_tested_at: string | null
  last_test_ok: boolean | null
  last_test_message: string | null
  last_test_latency_ms: number | null

  created_at: string
  updated_at: string
}

export interface ModelConfigCreatePayload {
  display_name: string
  provider: string
  model_id: string
  base_url?: string | null
  api_key?: string | null
  capabilities: string[]
  extra_config?: Record<string, unknown>
  is_enabled?: boolean
  is_default?: boolean
  sort_order?: number
  remark?: string | null
}

/**
 * 更新负载。
 *
 * ⚠️ 语义约定：`api_key` 不传（undefined）或为空 = 保持原密钥不变。
 * 想清空必须显式传 `clear_api_key: true`。
 */
export interface ModelConfigUpdatePayload {
  display_name?: string
  provider?: string
  model_id?: string
  base_url?: string | null
  api_key?: string | null
  clear_api_key?: boolean
  capabilities?: string[]
  extra_config?: Record<string, unknown>
  is_enabled?: boolean
  is_default?: boolean
  sort_order?: number
  remark?: string | null
}

export interface CapabilityOption {
  value: string
  label: string
  description: string
}

export interface ProviderExtraField {
  key: string
  label: string
  type?: string
  placeholder?: string | null
  description?: string | null
}

/** 供应商元数据 —— 前端下拉与能力联动的唯一数据源，由后端驱动 */
export interface ProviderMetadata {
  provider: string
  display_name: string
  default_base_url: string | null
  requires_api_key: boolean
  base_url_editable: boolean
  supported_capabilities: string[]
  doc_url: string | null
  suggested_models?: string[]
  notes?: string[]
  extra_fields?: ProviderExtraField[]
}

export interface ModelProvidersResponse {
  providers: ProviderMetadata[]
  capabilities: CapabilityOption[]
}

export interface ModelTestResult {
  ok: boolean
  latency_ms: number
  model_echo: string | null
  message: string
  error_type: string | null
  suggestion: string | null
  detail: string | null
}

export interface ModelTestDraftPayload {
  provider: string
  model_id: string
  base_url?: string | null
  api_key?: string | null
  capabilities: string[]
  extra_config?: Record<string, unknown>
  /** 编辑已有模型但没改密钥时，让后端复用库里已存的 Key */
  model_config_id?: number | null
}
