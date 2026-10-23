<script setup>
defineProps({ title: String, description: String });
</script>
<template><div class="empty-state"><span class="empty-mark" aria-hidden="true">◎</span><h2>{{ title }}</h2><p v-if="description">{{ description }}</p><slot /></div></template>
