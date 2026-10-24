<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { api } from '../api';
import { workspace, currentBoard, loadWorkspace } from '../workspace';
import AppIcon from '../components/AppIcon.vue';
import EmptyState from '../components/EmptyState.vue';
import StatusBadge from '../components/StatusBadge.vue';

const materials = ref(null), preflight = ref(null), loading = ref(false), busy = ref('');
const error = ref(''), notice = ref('');
let generation = 0, operation = 0, mounted = true;
const labels = { schematic_pdf:'原理图 PDF', netlist:'网表', bom:'物料清单 BOM', eprj2:'原始工程文件', operating_conditions:'工况说明', design_task_book:'设计任务书', design_task_book_docx:'任务书原文', drc:'DRC 检查记录' };
const accepts = { schematic_pdf:'.pdf', netlist:'.tel', bom:'.csv,.xlsx,.xlsm', eprj2:'.eprj2,.epro2', operating_conditions:'.yaml,.yml,.json,.txt,.docx', design_task_book:'.yaml,.yml,.json,.txt,.docx', design_task_book_docx:'.docx', drc:'.txt,.json' };
const requiredRoles = new Set(['schematic_pdf','bom','netlist','eprj2','operating_conditions','design_task_book']);
const descriptions = { schematic_pdf:'用于原图定位与证据核对', netlist:'用于识别器件引脚与连接网络', bom:'用于核对位号、型号和器件参数', eprj2:'用于核对设计工程与原图信息', operating_conditions:'说明供电、环境和正常工作范围', design_task_book:'说明接口要求及设计约束', design_task_book_docx:'保留任务书原文供追溯', drc:'补充设计规则检查记录' };
const rows = computed(() => {
  const byRole = Object.fromEntries((materials.value?.materials || []).map(row => [row.role, row]));
  return Object.entries(labels).map(([role,label]) => ({role,label,exists:false,required:requiredRoles.has(role),...byRole[role]}));
});
const groups = computed(() => [
  {id:'required', title:'必要材料', description:'完整评审所需的图纸、连接事实与设计约束。', rows:rows.value.filter(row => row.required)},
  {id:'supplementary', title:'补充材料', description:'按项目情况添加；未导入不会计入必要材料缺口。', rows:rows.value.filter(row => !row.required)},
]);
const requiredRows = computed(() => rows.value.filter(row => row.required));
const importedRequired = computed(() => requiredRows.value.filter(row => row.exists).length);
const missing = computed(() => materials.value?.missing || requiredRows.value.filter(row => !row.exists).map(row => row.role));
const checks = computed(() => preflight.value?.checks || []);
const failedChecks = computed(() => checks.value.filter(check => !check.ok));
const summary = computed(() => {
  if (busy.value === 'check') return {tone:'info', title:'正在检查当前材料', detail:'核对文件完整性与解析结果，完成后可设置评审范围。'};
  if (busy.value) return {tone:'info', title:'正在导入材料', detail:'导入完成后，请检查更新后的材料版本。'};
  if (missing.value.length) return {tone:'warning', title:'还需补齐 ' + missing.value.length + ' 项必要材料', detail:'选择下方材料入口，导入与当前板卡版本一致的文件。'};
  if (preflight.value?.ok) return {tone:'success', title:'当前材料检查通过', detail:'文件完整性与解析检查已完成，可以设置评审范围。电气结论由后续评审给出。'};
  if (preflight.value) return {tone:'warning', title:'材料检查发现 ' + failedChecks.value.length + ' 项待处理', detail:'按检查结果修正材料，替换后重新检查。'};
  return {tone:'neutral', title:'必要文件已导入，等待检查', detail:'文件存在不等于可解析。请先检查当前版本，再设置评审范围。'};
});
const checkLabel = item => labels[item.name] || ({netlist_parse:'网表解析',bom_parse:'BOM 解析',pdf_parse:'原理图解析',eprj2_parse:'工程文件解析',operating_conditions_parse:'工况说明解析',design_task_book_parse:'设计任务书解析'})[item.name] || item.name || item.label || item.key;
const statLabel = key => ({components:'网表器件',nets:'连接网络',refs_found:'已定位位号',pages:'原理图页数',pdf_pages:'原理图页数',bom_components:'BOM 位号'})[key] || key;
const sizeLabel = size => size ? size < 1024 ? size + ' B' : size < 1024*1024 ? (size/1024).toFixed(1) + ' KB' : (size/(1024*1024)).toFixed(1) + ' MB' : '—';
const current = (id, token) => mounted && workspace.boardId === id && generation === token;
function focusMaterial(role) { document.getElementById('material-' + role)?.focus(); }

async function load() {
  const id = workspace.boardId, token = ++generation;
  ++operation; materials.value = null; preflight.value = null; error.value = ''; notice.value = ''; busy.value = ''; loading.value = Boolean(id);
  if (!id) return;
  try { const value = await api.materials(id); if (current(id, token)) materials.value = value; }
  catch (e) { if (current(id, token)) error.value = e.message; }
  finally { if (current(id, token)) loading.value = false; }
}
watch(() => workspace.boardId, load, {immediate:true});
async function upload(role, event) {
  const input = event.target, file = input.files?.[0];
  if (!file || !workspace.boardId || busy.value || loading.value) return;
  const id = workspace.boardId, token = generation, task = ++operation;
  const active = () => current(id, token) && task === operation;
  busy.value = role; error.value = ''; notice.value = '';
  try {
    await api.uploadMaterial(id, role, file);
    if (active()) {
      const value = await api.materials(id);
      if (active()) { materials.value = value; preflight.value = null; notice.value = '已导入 ' + file.name + '。请检查更新后的材料版本。'; }
    }
    await loadWorkspace();
  } catch (e) { if (active()) error.value = e.message; }
  finally { if (active()) busy.value = ''; input.value = ''; }
}
async function check() {
  if (!workspace.boardId || busy.value || loading.value) return;
  const id = workspace.boardId, token = generation, task = ++operation;
  const active = () => current(id, token) && task === operation;
  busy.value = 'check'; error.value = ''; notice.value = '';
  try {
    const value = await api.preflight(id);
    if (active()) {
      if (value.version && materials.value?.version && value.version !== materials.value.version) {
        preflight.value = null; error.value = '材料版本已变化，请刷新材料清单后重新检查。';
      } else preflight.value = value;
    }
  } catch (e) { if (active()) error.value = e.message; }
  finally { if (active()) busy.value = ''; }
}
onBeforeUnmount(() => { mounted = false; ++generation; ++operation; });
</script>
<template>
  <EmptyState v-if="!workspace.boardId && !workspace.loading" title="先选择一个项目" description="选择板卡后，导入同一版本的原理图、网表与物料清单。"><router-link class="button primary" to="/">前往项目</router-link></EmptyState>
  <section v-else class="materials-page">
    <div class="page-heading"><div><div class="eyebrow">材料准备</div><h1>项目材料</h1><p>{{ currentBoard?.name }} · 导入文件，检查解析结果，再设置评审范围。</p></div><div class="actions"><button class="button" :disabled="!!busy || loading" @click="load"><AppIcon name="refresh" />刷新清单</button><button class="button primary" :disabled="!!busy || loading || !materials" @click="check"><AppIcon name="check-circle" />{{ busy === 'check' ? '检查中…' : '检查材料' }}</button></div></div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div>
    <div v-if="notice" class="notice success" role="status">{{ notice }}</div>
    <div v-if="loading" class="loading-state" role="status">正在读取当前项目材料</div>
    <template v-else-if="materials">
      <div class="material-summary" :class="summary.tone" aria-live="polite"><AppIcon :name="summary.tone === 'success' ? 'check-circle' : summary.tone === 'warning' ? 'alert-circle' : 'file'" /><div class="summary-copy"><h2>{{ summary.title }}</h2><p>{{ summary.detail }}</p><div v-if="missing.length" class="missing-links"><button v-for="role in missing" :key="role" type="button" :disabled="!!busy" @click="focusMaterial(role)">{{ labels[role] || role }}<AppIcon name="arrow-right" /></button></div></div><div class="summary-count"><strong>{{ importedRequired }}<span> / {{ requiredRows.length }}</span></strong><small>必要材料已导入</small></div></div>
      <div class="material-layout">
        <div>
          <section v-for="group in groups" :key="group.id" class="section-card material-group"><div class="section-heading"><h2>{{ group.title }}</h2><span>{{ group.rows.filter(row => row.exists).length }} / {{ group.rows.length }} 已导入</span></div><p class="group-description">{{ group.description }}</p><div class="material-list"><article v-for="row in group.rows" :key="row.role" class="material-row"><div class="material-heading"><div class="file-icon" aria-hidden="true"><AppIcon name="file" /></div><div class="material-info"><div class="material-title"><h3>{{ row.label }}</h3><StatusBadge :tone="row.exists ? 'info' : row.required ? 'warning' : 'neutral'">{{ row.exists ? '已导入' : row.required ? '待补齐' : '可选' }}</StatusBadge></div><p v-if="row.exists" class="file-name">{{ row.name || row.path?.split(/[\\/]/).pop() }}</p><p v-else>{{ descriptions[row.role] }}</p><small>{{ row.exists ? sizeLabel(row.size) : accepts[row.role].replaceAll(',', ' / ') }}</small></div><label class="button upload-button" :class="{disabled:!!busy}"><AppIcon name="upload" /><span>{{ busy === row.role ? '导入中…' : row.exists ? '替换文件' : '选择文件' }}</span><input :id="'material-' + row.role" type="file" :aria-label="(row.exists ? '替换' : '导入') + row.label" :aria-describedby="'material-help-' + row.role" :accept="accepts[row.role]" :disabled="!!busy" @change="upload(row.role, $event)" /></label></div><div :id="'material-help-' + row.role" class="material-help">{{ row.exists ? descriptions[row.role] + '；替换后需重新检查。' : '支持 ' + accepts[row.role].replaceAll(',', '、') }}<details v-if="row.sha256" class="file-fingerprint"><summary>文件指纹</summary><code>{{ row.sha256 }}</code></details></div></article></div></section>
        </div>
        <aside class="material-aside"><section class="section-card check-panel"><div class="section-heading"><h2>完整性与解析</h2><StatusBadge :tone="preflight ? (preflight.ok ? 'success' : 'warning') : 'neutral'">{{ preflight ? (preflight.ok ? '检查通过' : '需要处理') : '未检查' }}</StatusBadge></div><p v-if="!preflight" class="small-copy muted">导入完成后，检查文件是否齐备、格式能否读取。检查结果只对应当前材料版本。</p><template v-else><ul class="check-list"><li v-for="(item,index) in checks" :key="index" :class="{failed:!item.ok}"><AppIcon :name="item.ok ? 'check-circle' : 'alert-circle'" /><div><strong>{{ checkLabel(item) }}</strong><span class="sr-only">{{ item.ok ? '通过' : '未通过' }}</span><p>{{ item.detail || item.message }}</p></div></li></ul><dl v-if="preflight.stats" class="parsed-stats"><div v-for="(value,key) in preflight.stats" :key="key"><dt>{{ statLabel(key) }}</dt><dd>{{ value }}</dd></div></dl></template><div class="next-step"><router-link class="button" :class="{primary:preflight?.ok}" :to="{path:'/runs',query:{board:workspace.boardId}}">设置评审范围<AppIcon name="arrow-right" /></router-link><p>材料检查不代表电气判定通过。</p></div></section><section class="section-card version-panel"><h3>当前材料版本</h3><code :title="materials.version">{{ materials.version?.slice(0,16) || '尚未建立' }}</code><p>替换文件会更新材料版本。后续任务重新核对可复用的结果，已发布报告保留原版本。</p></section></aside>
      </div>
    </template>
  </section>
</template>
<style scoped>
.material-summary{display:flex;align-items:flex-start;gap:14px;padding:20px;border:1px solid var(--wb-border);border-radius:12px;background:var(--wb-surface);margin-bottom:22px}.material-summary>svg{width:22px;height:22px;flex-shrink:0;margin-top:2px;color:var(--wb-muted)}.material-summary.warning>svg{color:var(--wb-warning)}.material-summary.success>svg{color:var(--wb-success)}.material-summary.info>svg{color:var(--wb-primary)}.summary-copy{flex:1;min-width:0}.summary-copy h2{font-size:16px;margin:0 0 6px}.summary-copy p{font-size:13px;color:var(--wb-muted);margin:0;line-height:1.7}.summary-count{border-left:1px solid var(--wb-border);padding-left:24px;flex-shrink:0}.summary-count strong{display:block;font-size:28px;line-height:1.2;font-variant-numeric:tabular-nums}.summary-count strong span{font-size:16px;color:var(--wb-muted);font-weight:500}.summary-count small{display:block;font-size:12px;color:var(--wb-muted);margin-top:7px}.missing-links{display:flex;gap:6px;flex-wrap:wrap;margin-top:12px}.missing-links button{display:inline-flex;gap:6px;align-items:center;min-height:34px;border:1px solid var(--wb-border);border-radius:6px;background:var(--wb-bg);padding:5px 9px;color:var(--wb-ink);font-size:12px}.missing-links svg{width:14px;height:14px}.material-layout{display:grid;grid-template-columns:minmax(0,1fr) 310px;gap:22px;align-items:start}.material-group{padding:22px}.group-description{font-size:13px;color:var(--wb-muted);margin:-8px 0 18px}.material-row{border-top:1px solid var(--wb-border);padding:17px 0}.material-row:last-child{padding-bottom:0}.material-heading{display:flex;gap:12px;align-items:center}.file-icon{width:36px;height:40px;display:grid;place-items:center;background:var(--wb-soft);border:1px solid var(--wb-border);border-radius:8px;color:var(--wb-muted);flex-shrink:0}.file-icon svg{width:19px;height:19px}.material-info{min-width:0;flex:1}.material-title{display:flex;align-items:center;gap:9px;flex-wrap:wrap}.material-title h3{font-size:14px;margin:0;line-height:1.5}.material-info p{font-size:13px;color:var(--wb-muted);margin:5px 0;overflow-wrap:anywhere;line-height:1.6}.material-info .file-name{color:var(--wb-ink)}.material-info small,.material-help{font-size:12px;color:var(--wb-muted);line-height:1.65}.material-help{margin:9px 0 0 48px}.upload-button{position:relative;overflow:hidden;flex-shrink:0;font-size:12px}.upload-button svg{width:16px;height:16px}.upload-button input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}.upload-button:focus-within{outline:3px solid var(--wb-primary);outline-offset:3px}.upload-button.disabled{opacity:.55}.file-fingerprint{margin-top:7px}.file-fingerprint summary{cursor:pointer;display:list-item}.file-fingerprint code{display:block;margin-top:6px;overflow-wrap:anywhere;font-size:12px;line-height:1.6}.check-list{list-style:none;padding:0;margin:0;display:grid;gap:15px}.check-list li{display:flex;gap:9px;align-items:flex-start}.check-list svg{width:18px;height:18px;flex-shrink:0;color:var(--wb-success);margin-top:1px}.check-list li.failed svg{color:var(--wb-warning)}.check-list strong{font-size:13px}.check-list p{font-size:12px;color:var(--wb-muted);margin:4px 0 0;overflow-wrap:anywhere;line-height:1.7}.parsed-stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:18px;margin:20px 0 0;border-top:1px solid var(--wb-border)}.parsed-stats dt{font-size:12px;color:var(--wb-muted)}.parsed-stats dd{font-size:20px;font-weight:600;font-variant-numeric:tabular-nums;margin:5px 0 0}.next-step{padding-top:18px;margin-top:20px;border-top:1px solid var(--wb-border)}.next-step .button{width:100%}.next-step p,.version-panel p{font-size:12px;color:var(--wb-muted);line-height:1.75;margin:10px 0 0}.version-panel code{font-size:13px;overflow-wrap:anywhere}.materials-page .button>svg{width:17px;height:17px}@media(max-width:1050px){.material-layout{grid-template-columns:minmax(0,1fr) 275px;gap:16px}}@media(max-width:800px){.material-layout{grid-template-columns:1fr}.material-aside{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.material-summary{padding:17px}.material-group{padding:18px}}@media(max-width:560px){.material-summary{flex-wrap:wrap}.summary-count{width:100%;border-left:0;border-top:1px solid var(--wb-border);padding:12px 0 0 36px;display:flex;align-items:baseline;gap:12px}.summary-count strong{font-size:22px}.summary-count small{margin:0}.material-aside{display:block}.material-heading{flex-wrap:wrap;gap:10px}.material-info{flex-basis:calc(100% - 48px)}.upload-button{margin-left:46px;min-height:44px}.material-help{margin-left:46px}.missing-links button{min-height:44px}.file-fingerprint summary{min-height:32px;padding-top:5px}.material-group{padding:16px}}
</style>
