<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { NButton, NInput, NSelect } from 'naive-ui'

const props = defineProps({ data: { type: Object, default: () => ({}) }, busy: Boolean, writable: Boolean })
const emit = defineEmits(['save'])

const hfPresets = [
  { label: '官方直连', value: '' },
  { label: 'HF-Mirror · hf-mirror.com', value: 'https://hf-mirror.com' },
  { label: 'HF-Mirror · hf-mirror.net', value: 'https://hf-mirror.net' },
  { label: '自定义地址', value: 'custom' },
]
const githubPresets = [
  { label: '官方直连', value: '' },
  { label: 'GH-Proxy · gh-proxy.com', value: 'https://gh-proxy.com/' },
  { label: 'GHProxy · ghproxy.net', value: 'https://ghproxy.net/' },
  { label: '自定义前缀', value: 'custom' },
]
const values = reactive({ hfChoice: '', hfCustom: '', githubChoice: '', githubCustom: '' })
const githubInput = ref('')
const error = ref('')
const backendReady = computed(() => Object.hasOwn(props.data, 'hf_endpoint') && Object.hasOwn(props.data, 'github_mirror_url'))

function reset() {
  const hf = props.data.hf_endpoint || ''
  const github = props.data.github_mirror_url || ''
  values.hfChoice = hfPresets.some((item) => item.value === hf) ? hf : 'custom'
  values.hfCustom = values.hfChoice === 'custom' ? hf : ''
  values.githubChoice = githubPresets.some((item) => item.value === github) ? github : 'custom'
  values.githubCustom = values.githubChoice === 'custom' ? github : ''
  error.value = ''
}
watch(() => props.data, reset, { immediate: true })

const hfUrl = computed(() => values.hfChoice === 'custom' ? values.hfCustom.trim() : values.hfChoice)
const githubUrl = computed(() => values.githubChoice === 'custom' ? values.githubCustom.trim() : values.githubChoice)
const acceleratedUrl = computed(() => {
  if (!githubUrl.value || !githubInput.value.trim()) return ''
  try {
    const mirror = new URL(githubUrl.value)
    if (!['https:', 'http:'].includes(mirror.protocol) || !mirror.hostname || mirror.username || mirror.password || mirror.search || mirror.hash) return ''
    const url = new URL(githubInput.value.trim())
    if (url.protocol !== 'https:' || !['github.com', 'raw.githubusercontent.com', 'gist.githubusercontent.com'].includes(url.hostname) || url.username || url.password || url.search || url.hash) return ''
    return `${githubUrl.value.replace(/\/+$/, '')}/${url.href}`
  } catch { return '' }
})

function save() {
  if (!backendReady.value) {
    error.value = '当前服务仍在运行旧版本。请重启后端服务并刷新页面，再保存镜像设置。'
    return
  }
  if ((values.hfChoice === 'custom' && !hfUrl.value) || (values.githubChoice === 'custom' && !githubUrl.value)) {
    error.value = '自定义镜像地址不能为空；如需直连，请选择“官方直连”。'
    return
  }
  const payload = {}
  if (hfUrl.value !== (props.data.hf_endpoint || '')) payload.hf_endpoint = hfUrl.value
  if (githubUrl.value !== (props.data.github_mirror_url || '')) payload.github_mirror_url = githubUrl.value
  error.value = ''
  emit('save', payload)
}
</script>

<template>
  <div class="mirror-settings">
    <p v-if="!backendReady" role="alert" class="error-text">当前服务仍在运行旧版本。请重启后端服务并刷新页面，再保存镜像设置。</p>
    <p class="intro">公开服务适用于公开模型或仓库。可选择预设，也可输入自己的镜像站。服务可用性由提供方决定。</p>
    <form class="mirror-form" autocomplete="off" @submit.prevent="save">
      <div class="mirror-field">
        <label for="hf-mirror">Hugging Face 模型下载</label>
        <NSelect id="hf-mirror" v-model:value="values.hfChoice" :options="hfPresets" aria-label="Hugging Face 镜像" />
        <NInput v-if="values.hfChoice === 'custom'" v-model:value="values.hfCustom" aria-label="自定义 Hugging Face 镜像地址" placeholder="https://your-hf-mirror.example" />
        <small>写入 HF_ENDPOINT；保存后需重启服务。<a href="https://hf-mirror.com/" target="_blank" rel="noopener noreferrer">镜像用法</a></small>
      </div>
      <div class="mirror-field">
        <label for="github-mirror">GitHub 公共文件加速</label>
        <NSelect id="github-mirror" v-model:value="values.githubChoice" :options="githubPresets" aria-label="GitHub 文件加速服务" />
        <NInput v-if="values.githubChoice === 'custom'" v-model:value="values.githubCustom" aria-label="自定义 GitHub 加速前缀" placeholder="https://your-github-mirror.example/" />
        <small>这是原始链接前缀，适用于公开的 Release、源码包和 Raw 文件；不会作为 HTTP 代理使用。<a href="https://gh-proxy.com/docs/quick-start" target="_blank" rel="noopener noreferrer">使用说明</a></small>
      </div>
      <p v-if="error" role="alert" class="error-text">{{ error }}</p>
      <div class="settings-actions"><NButton type="primary" attr-type="submit" :loading="busy" :disabled="!writable || !backendReady">保存镜像设置</NButton><NButton :disabled="busy" @click="reset">重置</NButton></div>
    </form>
    <div v-if="githubUrl" class="link-builder">
      <label for="github-source">生成 GitHub 加速链接</label>
      <NInput id="github-source" v-model:value="githubInput" placeholder="粘贴公开的 GitHub 文件链接" aria-label="原始 GitHub 文件链接" />
      <output v-if="acceleratedUrl"><a :href="acceleratedUrl" target="_blank" rel="noopener noreferrer">{{ acceleratedUrl }}</a></output>
      <small v-else>输入 github.com、raw.githubusercontent.com 或 gist.githubusercontent.com 的公开 HTTPS 链接，不含访问令牌或查询参数。</small>
    </div>
  </div>
</template>

<style scoped>
.mirror-settings { max-width: 780px; }
.intro { margin: 0 0 18px; color: var(--sz-text-muted); line-height: 1.65; }
.mirror-form { display: grid; gap: 20px; }
.mirror-field, .link-builder { display: grid; gap: 8px; }
.mirror-field label, .link-builder label { font-weight: 650; }
.mirror-field small, .link-builder small { color: var(--sz-text-muted); line-height: 1.6; font-size: 13px; }
.mirror-field a, .link-builder a { color: var(--sz-accent); }
.settings-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 3px; }
.link-builder { margin-top: 22px; padding-top: 20px; border-top: 1px solid var(--sz-border); }
.link-builder output { overflow-wrap: anywhere; font-size: 13px; }
</style>
