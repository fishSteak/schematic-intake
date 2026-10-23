<script setup>
import { computed, ref } from 'vue';
import { ElDialog } from 'element-plus';
import 'element-plus/es/components/dialog/style/css';
import { useRouter } from 'vue-router';
import { api, dateLabel, runLabels } from '../api';
import { workspace, loadWorkspace, selectBoard } from '../workspace';
import EmptyState from '../components/EmptyState.vue';
import StatusBadge from '../components/StatusBadge.vue';
const router = useRouter();
const creating = ref(false); const name = ref(''); const busy = ref(false); const error = ref('');
const example = computed(() => workspace.boards.find(board => board.name === '0918全量'));
function open(board, path='/materials') { selectBoard(board.id); router.push({path,query:{board:board.id}}); }
async function create() {
  if (!name.value.trim()) { error.value='请填写项目名称。'; return; }
  busy.value=true; error.value='';
  try { const value=await api.createBoard(name.value.trim()); await loadWorkspace(); creating.value=false; name.value=''; open(value.board || value); }
  catch(e) { error.value=e.message; } finally { busy.value=false; }
}
const reportId = value => typeof value === 'string' ? value : value?.id || value?.run_id;
function openReport(board) { selectBoard(board.id); router.push({path:'/reports',query:{board:board.id,...(reportId(board.latest_report)?{run:reportId(board.latest_report)}:{})}}); }
</script>
<template>
  <section>
    <div class="page-heading"><div><div class="eyebrow">LOCAL REVIEW WORKSPACE</div><h1>从一份原理图，开始评审</h1><p>集中管理设计材料、检查任务和评审证据，让每个结论都能回到来源。</p></div><button class="button primary" @click="creating=true">＋ 新建项目</button></div>
    <div class="workflow-strip" aria-label="评审流程"><div><span>01</span><strong>准备材料</strong><small>核对网表、BOM 与图纸</small></div><i aria-hidden="true">→</i><div><span>02</span><strong>执行检查</strong><small>选择范围，追踪后台任务</small></div><i aria-hidden="true">→</i><div><span>03</span><strong>核对证据</strong><small>定位问题，记录复核意见</small></div></div>
    <div class="section-heading project-list-title"><h2>本地项目 <span class="count-label">{{ workspace.boards.length }}</span></h2><button class="button ghost small" :disabled="workspace.loading" @click="loadWorkspace">刷新列表</button></div>
    <div v-if="workspace.loading&&!workspace.initialized" class="loading-state" aria-live="polite">正在加载本地项目</div>
    <EmptyState v-else-if="!workspace.boards.length&&!workspace.error" title="创建第一个评审项目" description="为板卡命名，然后导入本地材料。项目中的每次运行都会留下独立记录。"><button class="button primary" @click="creating=true">新建项目</button></EmptyState>
    <div v-else class="project-grid"><article v-for="board in workspace.boards" :key="board.id" class="project-card"><div class="project-card-top"><span class="project-symbol" aria-hidden="true">{{ board.name.slice(0,1) }}</span><StatusBadge v-if="board.active_run" tone="info">{{ runLabels[board.active_run.status] || '有运行任务' }}</StatusBadge><StatusBadge v-else :tone="board.latest_report?'success':'neutral'">{{ board.latest_report?'已有报告':'待评审' }}</StatusBadge></div><h3>{{ board.name }}</h3><p class="project-path" :title="board.path">{{ board.path || '本机项目' }}</p><div class="project-card-meta"><span>材料版本</span><span class="mono">{{ board.material_version ? String(board.material_version).slice(0,10) : '尚未建立' }}</span></div><div v-if="board.updated_at" class="project-card-meta"><span>最近更新</span><span>{{ dateLabel(board.updated_at) }}</span></div><div class="project-card-actions"><button class="button primary" @click="open(board)">打开项目 <span aria-hidden="true">→</span></button><button v-if="board.latest_report" class="button ghost" @click="openReport(board)">查看报告</button><button v-else class="button ghost" @click="open(board,'/runs')">运行检查</button></div></article></div>
    <div v-if="example" class="example-callout"><div><strong>先看看一个完整材料项目</strong><p>仓库自带的「0918全量」可用于了解材料检查和评审操作。</p></div><button class="button" @click="open(example)">打开示例项目</button></div>
    <el-dialog v-model="creating" title="新建本地项目" width="min(480px, calc(100vw - 32px))" :close-on-click-modal="!busy"><form @submit.prevent="create"><div class="field"><label for="project-name">项目名称</label><input id="project-name" v-model="name" maxlength="100" placeholder="例如：电源控制板 R2" autofocus :disabled="busy" required /><span class="help">材料导入后，可以在项目内建立独立的评审批次。</span></div><div v-if="error" class="notice error" role="alert">{{ error }}</div><div class="actions"><button class="button primary" :disabled="busy">{{ busy?'正在创建…':'创建项目' }}</button><button class="button" type="button" :disabled="busy" @click="creating=false">取消</button></div></form></el-dialog>
  </section>
</template>
<style scoped>
.workflow-strip{display:flex;align-items:center;justify-content:space-between;gap:18px;padding:26px 30px;background:#edf4f1;border:1px solid #dce9e2;border-radius:11px;margin:26px 0 34px}.workflow-strip>div{display:grid;grid-template-columns:32px 1fr;gap:5px 12px}.workflow-strip span{grid-row:1/3;font-family:ui-monospace,monospace;font-size:12px;color:#557d6e;margin-top:4px}.workflow-strip strong{font-size:14px;font-weight:600}.workflow-strip small{color:#647c72;font-size:12px}.workflow-strip>i{color:#8da69b;font-style:normal}.project-list-title{margin-bottom:16px}.count-label{font-size:12px;color:#6d8379;font-weight:500;margin-left:7px}.project-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr));gap:20px}.project-card{padding:24px;border:1px solid var(--wb-border);border-radius:11px;background:#fff;transition:border-color .15s,box-shadow .15s}.project-card:hover{border-color:#a9c8bb;box-shadow:0 5px 18px #254b4010}.project-card-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:22px}.project-symbol{width:40px;height:40px;background:#e8f1ec;border:1px solid #d8e5dd;color:#39745c;border-radius:9px;display:grid;place-items:center;font-size:16px;font-weight:650}.project-card h3{font-size:20px;line-height:1.4;margin-bottom:9px}.project-path{font-size:12px;line-height:1.65;color:#74857f;margin:0 0 24px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.project-card-meta{display:flex;justify-content:space-between;gap:12px;font-size:12px;color:#697f75;margin:10px 0}.project-card-actions{display:flex;gap:9px;margin-top:24px;padding-top:18px;border-top:1px solid #edf1ee;flex-wrap:wrap}.example-callout{display:flex;align-items:center;justify-content:space-between;gap:24px;margin-top:30px;padding:20px 24px;border:1px dashed #cbded4;border-radius:10px}.example-callout strong{font-size:14px;font-weight:550}.example-callout p{font-size:12px;color:var(--wb-muted);margin:6px 0 0}@media(max-width:760px){.workflow-strip{padding:20px;flex-direction:column;align-items:stretch;gap:18px}.workflow-strip>i{display:none}.example-callout{align-items:flex-start;flex-direction:column;padding:20px}.project-card{padding:20px}}
</style>
