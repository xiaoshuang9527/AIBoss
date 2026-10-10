/**
 * 岗位结果列表。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'
import { jobApi, type JobQuery } from '@/api'
import type { Job, JobFacets } from '@/types'

export const useJobStore = defineStore('job', () => {
  const jobs = ref<Job[]>([])
  const facets = ref<JobFacets | null>(null)
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const loading = ref(false)
  const lastQuery = ref<JobQuery>({})

  async function loadJobs(query: JobQuery = {}) {
    loading.value = true
    try {
      const merged: JobQuery = {
        page: query.page ?? page.value,
        page_size: query.page_size ?? pageSize.value,
        order_by: query.order_by ?? 'created_desc',
        ...query,
      }
      lastQuery.value = merged
      const response = await jobApi.list(merged)
      jobs.value = response.items
      total.value = response.meta.total
      page.value = response.meta.page
      pageSize.value = response.meta.page_size
    } finally {
      loading.value = false
    }
  }

  async function loadFacets() {
    facets.value = await jobApi.facets()
  }

  async function toggleFavorite(job: Job) {
    const result = await jobApi.toggleFavorite(job.id)
    const index = jobs.value.findIndex((item) => item.id === job.id)
    if (index >= 0) {
      jobs.value[index] = { ...jobs.value[index], is_favorite: result.is_favorite }
    }
    return result.is_favorite
  }

  async function removeJobs(ids: number[]) {
    const result = await jobApi.remove(ids)
    jobs.value = jobs.value.filter((item) => !ids.includes(item.id))
    total.value = Math.max(0, total.value - result.deleted)
    return result.deleted
  }

  function reset() {
    jobs.value = []
    total.value = 0
    page.value = 1
  }

  return {
    jobs,
    facets,
    total,
    page,
    pageSize,
    loading,
    lastQuery,
    loadJobs,
    loadFacets,
    toggleFavorite,
    removeJobs,
    reset,
  }
})
