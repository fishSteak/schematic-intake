<script setup>
import { ref } from 'vue';
import { ElDialog } from 'element-plus';
import 'element-plus/es/components/dialog/style/css';
import { useRouter } from 'vue-router';
import { api, dateLabel, runLabels } from '../api';
import { workspace, loadWorkspace, selectBoard } from '../workspace';
import EmptyState from '../components/EmptyState.vue';
import StatusBadge from '../components/StatusBadge.vue';
const router = useRouter();
const creating = ref(false); const name = ref(''); const busy = ref(false); const error = ref('');
function open(board, path='/materials') { selectBoard(board.id); router.push({path,query:{board:board.id}}); }
async function create() {
  if (!name.value.trim()) { error.value='请填写项目名称。'; return; }
  busy.value=true; error.value='';
  try { const value=await api.createBoard(name.value.trim()); await loadWorkspace(); creating.value=false; name.value=''; open(value.board || value); }
  catch(e) { error.value=e.message; } finally { busy.value=false; }
}
const reportId = value => typeof value === 'string' ? value : value?.id || value?.run_id;
const reportTime = board => board.latest_report?.finished_at || board.latest_report?.created_at ? dateLabel(board.latest_report.finished_at || board.latest_report.created_at) : '—';
function openReport(board) { selectBoard(board.id); router.push({path:'/reports',query:{board:board.id,...(reportId(board.latest_report)?{run:reportId(board.latest_report)}:{})}}); }
</script>
<template>
  <section>
    <div class="page-heading"><div><h1>项目</h1><p>每个板卡项目独立保存材料版本、运行记录与评审报告：导入材料 → 运行检查 → 复核结论。</p></div><div class="actions"><button class="button" :disabled="workspace.loading" @click="loadWorkspace">刷新</button><button class="button primary" @click="creating=true"><span aria-hidden="true">＋</span>新建项目</button></div></div>
    <div v-if="workspace.loading&&!workspace.initialized" class="loading-state" aria-live="polite">正在加载项目</div>
    <EmptyState v-else-if="!workspace.boards.length&&!workspace.error" title="创建第一个评审项目" description="为板卡命名，然后导入原理图、网表与 BOM。项目中的每次运行都会留下独立记录。"><button class="button primary" @click="creating=true">新建项目</button></EmptyState>
    <div v-else class="section-card flush project-table">
      <div class="project-head" aria-hidden="true"><span>项目</span><span>状态</span><span>材料版本</span><span>最近报告</span><span></span></div>
      <ul class="project-list"><li v-for="board in workspace.boards" :key="board.id" class="project-row" :class="{current:String(board.id)===workspace.boardId}">
        <div class="project-name"><span class="project-symbol" aria-hidden="true">{{ board.name.slice(0,1) }}</span><div><button class="project-link" @click="open(board)">{{ board.name }}</button><span v-if="String(board.id)===workspace.boardId" class="current-tag">当前</span><small :title="board.path">{{ board.path || '本机项目' }}</small></div></div>
        <div class="project-cell" data-label="状态"><StatusBadge v-if="board.active_run" tone="info">{{ runLabels[board.active_run.status] || '有运行任务' }}</StatusBadge><StatusBadge v-else :tone="board.latest_report?'success':'neutral'">{{ board.latest_report?'已有报告':'待评审' }}</StatusBadge></div>
        <div class="project-cell mono" data-label="材料版本">{{ board.material_version ? String(board.material_version).slice(0,10) : '尚未建立' }}</div>
        <div class="project-cell" data-label="最近报告">{{ reportTime(board) }}</div>
        <div class="project-actions"><button class="button small" @click="open(board)">材料</button><button v-if="board.latest_report" class="button small" @click="openReport(board)">查看报告</button><button v-else class="button small" @click="open(board,'/runs')">运行检查</button></div>
      </li></ul>
    </div>
    <el-dialog v-model="creating" title="新建项目" width="min(440px, calc(100vw - 32px))" :close-on-click-modal="!busy"><form @submit.prevent="create"><div class="field"><label for="project-name">项目名称</label><input id="project-name" v-model="name" maxlength="100" placeholder="例如：电源控制板 R2" autofocus :disabled="busy" required /><span class="help">材料导入后，可以在项目内建立独立的评审批次。</span></div><div v-if="error" class="notice error" role="alert">{{ error }}</div><div class="actions dialog-actions"><button class="button" type="button" :disabled="busy" @click="creating=false">取消</button><button class="button primary" :disabled="busy">{{ busy?'正在创建…':'创建项目' }}</button></div></form></el-dialog>
  </section>
</template>
<style scoped>
.project-head,.project-row{display:grid;grid-template-columns:minmax(220px,2.4fr) minmax(110px,1fr) minmax(110px,1fr) minmax(150px,1.1fr) 170px;align-items:center;gap:16px;padding:0 20px}
.project-head{min-height:38px;font-size:12px;font-weight:500;color:var(--wb-muted);background:#f9fafb;border-bottom:1px solid var(--wb-border);border-radius:8px 8px 0 0}
.project-list{list-style:none;margin:0;padding:0}
.project-row{min-height:64px;padding-top:10px;padding-bottom:10px;border-bottom:1px solid var(--wb-divider)}
.project-row:last-child{border-bottom:0}
.project-row:hover{background:var(--wb-hover)}
.project-name{display:flex;align-items:center;gap:12px;min-width:0}
.project-name>div{min-width:0}
.project-symbol{width:32px;height:32px;flex-shrink:0;border-radius:7px;display:grid;place-items:center;background:var(--wb-subtle);border:1px solid var(--wb-border);color:var(--wb-text);font-size:14px;font-weight:600}
.project-row.current .project-symbol{background:var(--wb-accent-soft);border-color:var(--wb-accent-line);color:var(--wb-accent-hover)}
.project-link{border:0;background:none;padding:0;font-size:14px;font-weight:600;color:var(--wb-ink);text-align:left}
.project-link:hover{color:var(--wb-accent);text-decoration:underline;text-underline-offset:3px}
.current-tag{margin-left:8px;font-size:11px;color:var(--wb-accent-hover);background:var(--wb-accent-soft);padding:1px 6px;border-radius:4px;vertical-align:1px}
.project-name small{display:block;margin-top:2px;font-size:12px;color:var(--wb-faint);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.project-cell{font-size:13px;color:var(--wb-text);min-width:0}
.project-cell.mono{font-size:12px}
.project-actions{display:flex;gap:6px;justify-content:flex-end}
.dialog-actions{justify-content:flex-end;margin-top:4px}
@media(max-width:860px){.project-head{display:none}.project-row{grid-template-columns:1fr 1fr;gap:10px 16px;padding:14px 16px}.project-name,.project-actions{grid-column:1/-1}.project-cell:before{content:attr(data-label);display:block;font-size:11px;color:var(--wb-muted);margin-bottom:2px;font-family:inherit}.project-actions{justify-content:flex-start}.project-actions .button{flex:1}}
</style>
