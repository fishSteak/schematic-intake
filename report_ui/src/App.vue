<script setup>
import { computed, onMounted, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { loadWorkspace, workspace, selectBoard } from './workspace';
import StatusBadge from './components/StatusBadge.vue';
const route = useRoute(); const router = useRouter();
const nav = [{ path: '/', label: '项目' }, { path: '/materials', label: '材料' }, { path: '/runs', label: '运行' }, { path: '/reports', label: '评审工作台' }, { path: '/models', label: '模型设置' }];
const localState = computed(() => workspace.error ? '服务未连接' : workspace.loading ? '正在连接' : '本机服务已连接');
function navigateBoard(event) {
  selectBoard(event.target.value);
  router.push({ path: route.path, query: workspace.boardId ? { board: workspace.boardId } : {} });
}
function applyRouteBoard() {
  const id = String(route.query.board || '');
  if (id && workspace.boards.some(b => String(b.id) === id)) selectBoard(id);
}
watch(() => route.query.board, applyRouteBoard);
watch(() => workspace.boards, applyRouteBoard);
onMounted(loadWorkspace);
</script>

<template>
  <div class="app-shell">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <header class="app-header">
      <router-link class="app-brand" to="/" aria-label="原理图评审工作台首页"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i></span><span>原理图评审<small>SCHEMATIC WORKBENCH</small></span></router-link>
      <nav class="primary-nav" aria-label="主要导航"><router-link v-for="item in nav" :key="item.path" :to="{path:item.path,query:workspace.boardId ? {board:workspace.boardId} : {}}" :class="{active:route.path===item.path}" :aria-current="route.path===item.path ? 'page' : undefined">{{ item.label }}</router-link></nav>
      <div class="header-project"><label for="global-project" class="sr-only">当前项目</label><select id="global-project" :value="workspace.boardId" @change="navigateBoard" :disabled="!workspace.boards.length"><option v-if="!workspace.boards.length" value="">尚无项目</option><option v-for="board in workspace.boards" :key="board.id" :value="board.id">{{ board.name }}</option></select></div>
    </header>
    <div v-if="workspace.error" class="service-banner" role="alert"><span>{{ workspace.error }}</span><button class="button small" @click="loadWorkspace">重新连接</button></div>
    <main id="main-content" class="app-main" :class="{'wide-main':route.path==='/reports'}"><router-view /></main>
    <footer class="app-footer"><StatusBadge :tone="workspace.error ? 'danger' : workspace.loading ? 'neutral' : 'success'">{{ localState }}</StatusBadge><span>材料与评审记录保存在本机</span></footer>
  </div>
</template>
<style src="./workbench.css"></style>
