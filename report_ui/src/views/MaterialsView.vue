<script setup>
import { computed, ref, watch } from 'vue';
import { api } from '../api';
import { workspace, currentBoard, loadWorkspace } from '../workspace';
import EmptyState from '../components/EmptyState.vue';
import StatusBadge from '../components/StatusBadge.vue';
const materials=ref(null); const preflight=ref(null); const loading=ref(false); const busy=ref(''); const error=ref(''); const notice=ref(''); let generation=0;
const labels={schematic_pdf:'原理图 PDF',netlist:'网表',bom:'物料清单 BOM',eprj2:'原始工程文件',operating_conditions:'工况说明',design_task_book:'设计任务书',design_task_book_docx:'任务书原文',drc:'DRC 检查记录'};
const accepts={schematic_pdf:'.pdf',netlist:'.tel',bom:'.csv,.xlsx,.xlsm',eprj2:'.eprj2,.epro2',operating_conditions:'.yaml,.yml,.json,.txt,.docx',design_task_book:'.yaml,.yml,.json,.txt,.docx',design_task_book_docx:'.docx',drc:'.txt,.json'};
const rows=computed(()=>{
 const existing=materials.value?.materials||[];
 const byRole=Object.fromEntries(existing.map(m=>[m.role,m]));
 return Object.entries(labels).map(([role,label])=>({role,label,...(byRole[role]||{exists:false})}));
});
const readyCount=computed(()=>rows.value.filter(r=>r.exists).length);
const checks=computed(()=>preflight.value?.checks||[]);
const fileTags={schematic_pdf:'PDF',netlist:'NET',bom:'BOM',eprj2:'PRJ',drc:'DRC'};
const sizeLabel=size=>size?size<1024?`${size} B`:size<1024*1024?`${(size/1024).toFixed(1)} KB`:`${(size/1024/1024).toFixed(1)} MB`:'—';
async function load(){const id=workspace.boardId; const token=++generation; materials.value=null;preflight.value=null;error.value='';notice.value='';if(!id)return;loading.value=true;try{const value=await api.materials(id);if(token===generation)materials.value=value;}catch(e){if(token===generation)error.value=e.message;}finally{if(token===generation)loading.value=false;}}
watch(()=>workspace.boardId,load,{immediate:true});
async function upload(role,event){const file=event.target.files?.[0];if(!file)return;const id=workspace.boardId;busy.value=role;error.value='';notice.value='';try{await api.uploadMaterial(id,role,file);if(workspace.boardId===id){materials.value=await api.materials(id);preflight.value=null;notice.value=`已导入 ${file.name}。请重新检查材料，确认解析结果。`;}await loadWorkspace();}catch(e){error.value=e.message;}finally{busy.value='';event.target.value='';}}
async function check(){if(!workspace.boardId)return;busy.value='check';error.value='';notice.value='';try{preflight.value=await api.preflight(workspace.boardId);}catch(e){error.value=e.message;}finally{busy.value='';}}
</script>
<template>
  <EmptyState v-if="!workspace.boardId&&!workspace.loading" title="先选择一个项目" description="在项目页新建项目，或在顶栏切换到已有板卡。"><router-link class="button primary" to="/">前往项目</router-link></EmptyState>
  <section v-else>
    <div class="page-heading"><div><h1>材料</h1><p>{{ currentBoard?.name }} · 按用途导入文件，检查通过后再设置评审范围。图纸、器件与连接事实须来自同一版本。</p></div><div class="actions"><button class="button" :disabled="!!busy||loading" @click="check">{{ busy==='check'?'检查中…':'检查材料' }}</button><router-link class="button primary" :to="{path:'/runs',query:{board:workspace.boardId}}">设置评审范围 →</router-link></div></div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="notice" class="notice success" role="status">{{ notice }}</div>
    <div v-if="loading" class="loading-state">正在读取材料清单</div>
    <div v-else class="split-layout">
      <div class="section-card flush"><div class="section-heading"><h2>文件</h2><span>{{ readyCount }} / {{ rows.length }} 已导入</span></div><ul class="material-list"><li v-for="row in rows" :key="row.role" class="material-row" :class="{missing:!row.exists}"><span class="file-icon" aria-hidden="true">{{ fileTags[row.role]||'DOC' }}</span><div class="material-info"><strong>{{ row.label }}</strong><p :title="row.name || row.path">{{ row.name || (row.exists ? row.path?.split(/[\\/]/).pop() : '未导入') }}</p></div><div class="material-meta mono"><template v-if="row.exists">{{ sizeLabel(row.size) }}<span v-if="row.sha256"> · {{ row.sha256.slice(0,8) }}</span></template></div><label class="button small upload-button" :class="{disabled:!!busy}"><span>{{ busy===row.role?'导入中…':row.exists?'替换':'选择文件' }}</span><input type="file" :aria-label="`${row.exists?'替换':'导入'}${row.label}`" :accept="accepts[row.role]" :disabled="!!busy" @change="upload(row.role,$event)" /></label></li></ul></div>
      <aside><div class="section-card"><div class="section-heading"><h2>材料检查</h2><StatusBadge v-if="preflight" :tone="preflight.ok?'success':'warning'">{{ preflight.ok?'检查通过':'需要处理' }}</StatusBadge><StatusBadge v-else tone="neutral">未检查</StatusBadge></div><p v-if="!preflight" class="small-copy muted">点击“检查材料”，核对当前版本的完整性与解析结果。材料检查不代表电气判定通过。</p><div v-else class="check-list"><div v-for="(item,index) in checks" :key="index" class="check-row"><span :class="item.ok?'check-pass':'check-fail'" aria-hidden="true">{{ item.ok?'✓':'!' }}</span><div><strong>{{ labels[item.name] || {netlist_parse:'网表解析',bom_parse:'BOM 解析',pdf_parse:'原理图解析',eprj2_parse:'工程文件解析'}[item.name] || item.name || item.label || item.key }}</strong><p>{{ item.detail || item.message }}</p></div></div><div v-if="preflight.missing?.length" class="notice warning">待补齐：{{ preflight.missing.map(key=>labels[key]||key).join('、') }}</div><dl v-if="preflight.stats" class="metadata-list"><div v-for="(value,key) in preflight.stats" :key="key"><dt>{{ {components:'器件',nets:'网络',refs_found:'已定位位号',pages:'原理图页数',pdf_pages:'原理图页数',bom_components:'BOM 器件'}[key]||key }}</dt><dd class="mono">{{ value }}</dd></div></dl></div></div><div class="section-card"><div class="section-heading"><h2>材料版本</h2></div><p class="version-hash mono">{{ materials?.version?.slice(0,16) || '尚未建立' }}</p><p class="small-copy muted">替换任一文件都会生成新版本；已发布的报告保留原版本，后续运行重新核对可复用的结果。</p></div></aside>
    </div>
  </section>
</template>
<style scoped>
.material-list{list-style:none;margin:0;padding:0}
.material-row{display:grid;grid-template-columns:32px minmax(0,1fr) 160px 92px;align-items:center;gap:14px;padding:12px 20px;border-bottom:1px solid var(--wb-divider)}
.material-row:last-child{border-bottom:0}
.material-row:hover{background:var(--wb-hover)}
.file-icon{width:32px;height:32px;border-radius:6px;display:grid;place-items:center;background:var(--wb-subtle);border:1px solid var(--wb-border);color:var(--wb-text);font-family:var(--wb-mono);font-size:9.5px;font-weight:600;letter-spacing:.3px}
.material-row.missing .file-icon{background:#fff;border-style:dashed;color:var(--wb-faint)}
.material-info{min-width:0}
.material-info strong{display:block;font-size:13.5px;font-weight:600;color:var(--wb-ink)}
.material-info p{margin:1px 0 0;font-size:12.5px;color:var(--wb-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.material-row.missing .material-info strong{color:var(--wb-text);font-weight:500}
.material-row.missing .material-info p{color:var(--wb-faint)}
.material-meta{font-size:11.5px;color:var(--wb-muted);text-align:right;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.upload-button{position:relative;overflow:hidden;cursor:pointer;justify-self:end;min-width:76px}
.upload-button input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}
.upload-button:focus-within{outline:2px solid var(--wb-accent);outline-offset:2px}
.upload-button.disabled{opacity:.5}
.check-list{display:flex;flex-direction:column;gap:14px}
.check-row{display:flex;gap:10px;font-size:13px;align-items:flex-start}
.check-row strong{font-weight:500;color:var(--wb-ink)}
.check-row p{font-size:12px;color:var(--wb-muted);margin:2px 0 0;line-height:1.55}
.check-pass,.check-fail{width:18px;height:18px;border-radius:50%;display:grid;place-items:center;flex-shrink:0;font-size:11px;font-weight:700;background:#ecfdf3;color:var(--wb-success);margin-top:1px}
.check-fail{background:#fffaeb;color:var(--wb-warning)}
.version-hash{margin:0 0 8px;font-size:13px;color:var(--wb-ink);background:var(--wb-subtle);border-radius:6px;padding:6px 10px;overflow-wrap:anywhere}
@media(max-width:1100px){.material-row{grid-template-columns:32px minmax(0,1fr) 92px}.material-meta{display:none}}
@media(max-width:560px){.material-row{grid-template-columns:32px minmax(0,1fr) auto;padding:12px 16px;gap:10px 12px}.upload-button{min-width:0}}
</style>
