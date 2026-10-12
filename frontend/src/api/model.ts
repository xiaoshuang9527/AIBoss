import { del, get, patch, post, put } from './http'
import type {
  ModelConfig,
  ModelConfigCreatePayload,
  ModelConfigUpdatePayload,
  ModelProvidersResponse,
  ModelTestDraftPayload,
  ModelTestResult,
} from '@/types/model'

/** 模型管理接口。 */
export const modelApi = {
  /** 供应商与能力元数据 —— 前端所有联动都基于它，不硬编码 */
  providers: () => get<ModelProvidersResponse>('/models/providers'),

  list: (params?: { provider?: string; enabled_only?: boolean }) =>
    get<ModelConfig[]>('/models', { ...params }),

  detail: (id: number) => get<ModelConfig>(`/models/${id}`),

  create: (payload: ModelConfigCreatePayload) => post<ModelConfig>('/models', payload),

  update: (id: number, payload: ModelConfigUpdatePayload) =>
    put<ModelConfig>(`/models/${id}`, payload),

  remove: (id: number) => del<{ deleted: boolean }>(`/models/${id}`),

  setEnabled: (id: number, enabled: boolean) =>
    patch<ModelConfig>(`/models/${id}/enabled?enabled=${enabled ? 'true' : 'false'}`),

  setDefault: (id: number) => patch<ModelConfig>(`/models/${id}/default`),

  clone: (id: number, name?: string) =>
    post<ModelConfig>(`/models/${id}/clone${name ? `?name=${encodeURIComponent(name)}` : ''}`),

  /** 测试已保存模型的连通性 */
  test: (id: number) => post<ModelTestResult>(`/models/${id}/test`),

  /** 测试尚未保存的配置（弹窗里点「测试连接」） */
  testDraft: (payload: ModelTestDraftPayload) =>
    post<ModelTestResult>('/models/test-draft', payload),
}
