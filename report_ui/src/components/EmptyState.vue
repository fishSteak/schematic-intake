<script setup>
defineProps({ title: String, description: String });
</script>
<template><div class="empty-state"><span class="empty-mark" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="4.5" width="17" height="15" rx="2" stroke-dasharray="3 2.4" /><path d="M7 12h2.2l1-2.4 1.8 4.8 1.8-4.8 1 2.4H17" /></svg></span><h2>{{ title }}</h2><p v-if="description">{{ description }}</p><slot /></div></template>
