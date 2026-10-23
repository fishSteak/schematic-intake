<script setup>
defineProps({ tone: { type: String, default: 'neutral' } });
</script>
<template><span class="status-badge" :class="tone"><span aria-hidden="true" class="status-dot"></span><slot /></span></template>
