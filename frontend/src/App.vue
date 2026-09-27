<script setup>
import { computed, onMounted } from 'vue'
import { darkTheme, dateZhCN, NConfigProvider, NDialogProvider, NMessageProvider, zhCN } from 'naive-ui'
import { useThemeStore } from './stores/theme.js'
import { themeOverrides } from './theme/naiveTheme.js'
import AppShell from './layouts/AppShell.vue'

const theme = useThemeStore()
const isDark = computed(() => theme.resolved === 'dark')
const overrides = computed(() => themeOverrides(isDark.value))
onMounted(() => theme.init())
</script>

<template>
  <NConfigProvider :theme="isDark ? darkTheme : null" :theme-overrides="overrides" :locale="zhCN" :date-locale="dateZhCN">
    <NMessageProvider>
      <NDialogProvider>
        <AppShell />
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>
