<script setup lang="ts">
/**
 * markdown 渲染。
 *
 * AI 的输出天然是 markdown（列表、加粗、代码块、表格）。用 markdown-it 渲染，
 * 并**关闭 raw HTML**（`html: false`）—— 模型输出属于不可信内容，
 * 允许内联 HTML 会引入 XSS 风险。
 */
import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const props = defineProps<{ source: string }>()

const md = new MarkdownIt({
  html: false,        // 安全：禁止内联 HTML
  linkify: true,      // 自动识别链接
  breaks: true,       // 单个换行也视为换行（中文排版更符合直觉）
  typographer: false,
})

// 外链一律新窗口打开，并加上 noopener 防止 tabnabbing
const defaultLinkOpen =
  md.renderer.rules.link_open ??
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options))

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  tokens[idx].attrSet('target', '_blank')
  tokens[idx].attrSet('rel', 'noopener noreferrer')
  return defaultLinkOpen(tokens, idx, options, env, self)
}

const html = computed(() => md.render(props.source || ''))
</script>

<template>
  <!-- eslint-disable-next-line vue/no-v-html -- 已在上面关闭 raw HTML，输出是安全的 -->
  <div class="md" v-html="html" />
</template>

<style scoped>
.md {
  line-height: 1.8;
  font-size: 13.5px;
  word-break: break-word;
}

.md :deep(p) {
  margin: 0 0 8px;
}

.md :deep(p:last-child) {
  margin-bottom: 0;
}

.md :deep(ul),
.md :deep(ol) {
  margin: 6px 0 10px;
  padding-left: 22px;
}

.md :deep(li) {
  margin-bottom: 3px;
}

.md :deep(h1),
.md :deep(h2),
.md :deep(h3),
.md :deep(h4) {
  margin: 14px 0 8px;
  font-weight: 600;
  line-height: 1.4;
}

.md :deep(h1) {
  font-size: 17px;
}
.md :deep(h2) {
  font-size: 15.5px;
}
.md :deep(h3) {
  font-size: 14.5px;
}

.md :deep(code) {
  background: #f1f4f9;
  padding: 1px 5px;
  border-radius: 4px;
  font-family: 'JetBrains Mono', Consolas, monospace;
  font-size: 12.3px;
}

.md :deep(pre) {
  background: #0d1421;
  color: #cbd6e6;
  padding: 12px 14px;
  border-radius: 8px;
  overflow-x: auto;
  margin: 10px 0;
}

.md :deep(pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
  font-size: 12.3px;
}

.md :deep(blockquote) {
  margin: 10px 0;
  padding: 6px 12px;
  border-left: 3px solid #cddffd;
  background: #f6f9ff;
  color: #4a5b76;
  border-radius: 0 6px 6px 0;
}

.md :deep(table) {
  border-collapse: collapse;
  margin: 10px 0;
  width: 100%;
  font-size: 12.8px;
}

.md :deep(th),
.md :deep(td) {
  border: 1px solid var(--bzp-border);
  padding: 6px 10px;
  text-align: left;
}

.md :deep(th) {
  background: #f8fafd;
  font-weight: 600;
}

.md :deep(a) {
  color: var(--bzp-primary);
}

.md :deep(hr) {
  border: none;
  border-top: 1px solid var(--bzp-border);
  margin: 14px 0;
}
</style>
