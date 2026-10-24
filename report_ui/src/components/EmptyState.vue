<script setup>
import AppIcon from './AppIcon.vue';
defineProps({ title: String, description: String });
</script>
<template><div class="empty-state"><span class="empty-mark"><AppIcon name="folder" :size="27" /></span><h2>{{ title }}</h2><p v-if="description">{{ description }}</p><slot /></div></template>
