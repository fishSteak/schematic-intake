<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { loadWorkspace, workspace, selectBoard } from './workspace';
import StatusBadge from './components/StatusBadge.vue';
import AppIcon from './components/AppIcon.vue';
const route = useRoute(); const router = useRouter();
const nav = [{ path: '/', label: '项目总览', icon: 'folder', caption: '管理板卡与评审记录' }, { path: '/materials', label: '评审材料', icon: 'file', caption: '导入与检查设计资料' }, { path: '/runs', label: '运行任务', icon: 'play', caption: '检查范围与运行进度' }, { path: '/reports', label: '评审工作台', icon: 'layers', caption: '图纸、分析与证据' }, { path: '/models', label: '模型设置', icon: 'settings', caption: '模型服务与能力' }];
const localState = computed(() => workspace.error ? '服务未连接' : workspace.loading ? '正在连接' : '工作台已连接');
const page = computed(() => nav.find(item => item.path === route.path) || nav[0]);
const boardName = computed(() => workspace.boards.find(board => String(board.id) === workspace.boardId)?.name || '尚未选择项目');
const expanded = ref(false);
const compact = computed(() => route.path === '/reports' && !expanded.value);
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
  <div class="app-shell" :class="{'compact-navigation':compact,'report-shell':route.path==='/reports'}">
    <a class="skip-link" href="#main-content">跳到主要内容</a>
    <aside class="app-sidebar" aria-label="工作台导航">
      <router-link class="app-brand" to="/" aria-label="原理图评审工作台首页"><span class="brand-mark"><AppIcon name="circuit" :size="24" /></span><span class="brand-copy">原理图评审<small>SCHEMATIC REVIEW</small></span></router-link>
      <span class="nav-section-label">工作空间</span>
      <nav class="primary-nav" aria-label="主要导航"><router-link v-for="item in nav" :key="item.path" :to="{path:item.path,query:workspace.boardId ? {board:workspace.boardId} : {}}" :class="{active:route.path===item.path}" :aria-label="item.label" :aria-current="route.path===item.path ? 'page' : undefined"><AppIcon :name="item.icon" :size="20" /><span class="nav-label">{{ item.label }}</span><span v-if="route.path===item.path" class="nav-active-dot" aria-hidden="true"></span></router-link></nav>
      <div class="sidebar-bottom"><div class="sidebar-workspace"><AppIcon name="circuit" :size="18" /><div><strong>工程评审工作区</strong><span>材料 · 检查 · 证据</span></div></div><button v-if="route.path==='/reports'" class="navigation-toggle" @click="expanded=!expanded" :aria-label="compact?'展开导航':'收起导航'" :aria-expanded="!compact"><AppIcon name="panel-left" /><span>{{ compact?'展开导航':'收起导航' }}</span></button><span class="sidebar-version">SCHEMATIC INTAKE</span></div>
    </aside>
    <header class="app-header">
      <div class="topbar-location"><span class="mobile-brand"><AppIcon name="circuit" :size="21" /></span><span class="topbar-workspace">工作空间</span><AppIcon class="breadcrumb-divider" name="chevron-right" :size="14" /><strong>{{ page.label }}</strong><span class="topbar-caption">{{ page.caption }}</span></div>
      <div class="topbar-actions"><StatusBadge class="connection-state" :tone="workspace.error ? 'danger' : workspace.loading ? 'neutral' : 'success'">{{ localState }}</StatusBadge><div class="header-project"><label for="global-project"><AppIcon name="folder" :size="15" /><span>当前项目</span></label><select id="global-project" :value="workspace.boardId" @change="navigateBoard" :disabled="!workspace.boards.length" :title="boardName"><option v-if="!workspace.boards.length" value="">尚无项目</option><option v-for="board in workspace.boards" :key="board.id" :value="board.id">{{ board.name }}</option></select></div></div>
    </header>
    <div v-if="workspace.error" class="service-banner" role="alert"><span>{{ workspace.error }}</span><button class="button small" @click="loadWorkspace">重新连接</button></div>
    <main id="main-content" class="app-main" :class="{'wide-main':route.path==='/reports'}" tabindex="-1"><router-view /></main>
    <footer class="app-footer"><span>原理图评审工作台</span><span>材料与评审记录保存在工作台服务端</span></footer>
  </div>
</template>
<style src="./workbench.css"></style>
