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
const sizeLabel=size=>size?size<1024?`${size} B`:size<1024*1024?`${(size/1024).toFixed(1)} KB`:`${(size/1024/1024).toFixed(1)} MB`:'—';
async function load(){const id=workspace.boardId; const token=++generation; materials.value=null;preflight.value=null;error.value='';notice.value='';if(!id)return;loading.value=true;try{const value=await api.materials(id);if(token===generation)materials.value=value;}catch(e){if(token===generation)error.value=e.message;}finally{if(token===generation)loading.value=false;}}
watch(()=>workspace.boardId,load,{immediate:true});
async function upload(role,event){const file=event.target.files?.[0];if(!file)return;const id=workspace.boardId;busy.value=role;error.value='';notice.value='';try{await api.uploadMaterial(id,role,file);if(workspace.boardId===id){materials.value=await api.materials(id);preflight.value=null;notice.value=`已导入 ${file.name}。请重新检查材料，确认解析结果。`;}await loadWorkspace();}catch(e){error.value=e.message;}finally{busy.value='';event.target.value='';}}
async function check(){if(!workspace.boardId)return;busy.value='check';error.value='';notice.value='';try{preflight.value=await api.preflight(workspace.boardId);}catch(e){error.value=e.message;}finally{busy.value='';}}
</script>
<template>
  <EmptyState v-if="!workspace.boardId&&!workspace.loading" title="先选择一个项目" description="在项目页新建项目，或使用右上角选择已有板卡。"><router-link class="button primary" to="/">前往项目</router-link></EmptyState>
  <section v-else>
    <div class="page-heading"><div><div class="eyebrow">MATERIALS</div><h1>准备评审材料</h1><p>{{ currentBoard?.name }} · 导入文件后先检查完整性，确保图纸、器件和连接事实来自同一版本。</p></div><div class="actions"><button class="button" :disabled="!!busy||loading" @click="check">{{ busy==='check'?'检查中…':'检查材料' }}</button><router-link class="button primary" :to="{path:'/runs',query:{board:workspace.boardId}}">设置评审范围 →</router-link></div></div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="notice" class="notice success" role="status">{{ notice }}</div>
    <div v-if="loading" class="loading-state">正在读取材料清单</div>
    <div v-else class="split-layout">
      <div class="section-card"><div class="section-heading"><h2>文件与角色</h2><span>{{ readyCount }} 个文件已就绪</span></div><p class="small-copy muted">按文件用途导入材料。替换文件会形成新的材料版本，后续评审重新检查可复用的结果。</p><div class="material-list"><article v-for="row in rows" :key="row.role" class="material-row"><div class="file-icon" :class="{missing:!row.exists}" aria-hidden="true">{{ row.role==='schematic_pdf'?'PDF':row.role==='bom'?'BOM':'DOC' }}</div><div class="material-info"><div><strong>{{ row.label }}</strong><StatusBadge :tone="row.exists?'success':'neutral'">{{ row.exists?'已导入':'未导入' }}</StatusBadge></div><p>{{ row.name || (row.exists ? row.path?.split(/[\\/]/).pop() : '选择与当前板卡对应的文件') }}</p><small v-if="row.exists">{{ sizeLabel(row.size) }}<span v-if="row.sha256"> · {{ row.sha256.slice(0,12) }}</span></small></div><label class="button small upload-button" :class="{disabled:!!busy}"><span>{{ busy===row.role?'导入中…':row.exists?'替换':'选择文件' }}</span><input type="file" :aria-label="`${row.exists?'替换':'导入'}${row.label}`" :accept="accepts[row.role]" :disabled="!!busy" @change="upload(row.role,$event)" /></label></article></div></div>
      <aside><div class="section-card"><div class="section-heading"><h2>材料检查</h2><StatusBadge v-if="preflight" :tone="preflight.ok?'success':'warning'">{{ preflight.ok?'检查通过':'需要处理' }}</StatusBadge></div><p v-if="!preflight" class="small-copy muted">点击“检查材料”获取当前版本的完整性和解析状态。</p><div v-else class="check-list"><div v-for="(item,index) in checks" :key="index" class="check-row"><span :class="item.ok?'check-pass':'check-fail'" aria-hidden="true">{{ item.ok?'✓':'!' }}</span><div><strong>{{ labels[item.name] || {netlist_parse:'网表解析',bom_parse:'BOM 解析',pdf_parse:'原理图解析',eprj2_parse:'工程文件解析'}[item.name] || item.name || item.label || item.key }}</strong><p>{{ item.detail || item.message }}</p></div></div><div v-if="preflight.missing?.length" class="notice warning">待补齐：{{ preflight.missing.map(key=>labels[key]||key).join('、') }}</div><dl v-if="preflight.stats" class="metadata-list"><div v-for="(value,key) in preflight.stats" :key="key"><dt>{{ {components:'器件',nets:'网络',refs_found:'已定位位号',pages:'原理图页数',pdf_pages:'原理图页数',bom_components:'BOM 器件'}[key]||key }}</dt><dd>{{ value }}</dd></div></dl></div></div><div class="section-card"><h3>当前材料版本</h3><p class="small-copy mono">{{ materials?.version?.slice(0,16) || '尚未建立' }}</p><p class="small-copy muted">材料检查与电气判定分别记录。文件齐备后，再选择要执行的检查项。</p></div></aside>
    </div>
  </section>
</template>
<style scoped>
.material-list{margin-top:22px}.material-row{display:flex;align-items:center;gap:14px;padding:20px 0;border-top:1px solid #edf1ef}.material-info{flex:1;min-width:0}.material-info>div{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.material-info strong{font-size:14px;font-weight:600}.material-info p{font-size:12px;color:#647970;margin:5px 0 2px;overflow-wrap:anywhere}.material-info small{font-size:11px;color:#77887e}.file-icon{width:43px;height:49px;border-radius:7px;background:#eaf3ee;color:#427d60;display:grid;place-items:center;font-size:10px;font-weight:700;letter-spacing:.5px;flex-shrink:0}.file-icon.missing{background:#f2f5f3;color:#87938e}.upload-button{position:relative;overflow:hidden;flex-shrink:0;cursor:pointer}.upload-button input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}.upload-button:focus-within{outline:3px solid #54a99b;outline-offset:3px}.upload-button.disabled{opacity:.5}.check-list{display:flex;flex-direction:column;gap:18px}.check-row{display:flex;gap:10px;font-size:13px;align-items:flex-start}.check-row strong{font-weight:550}.check-row p{font-size:12px;color:#6e8177;margin:4px 0 0}.check-pass,.check-fail{width:20px;height:20px;border-radius:50%;display:grid;place-items:center;flex-shrink:0;font-size:12px;background:#e7f5ed;color:#347b54}.check-fail{background:#fff2d9;color:#946324}@media(max-width:560px){.material-row{gap:10px;flex-wrap:wrap}.material-info{min-width:180px}.upload-button{margin-left:53px}.file-icon{width:35px;height:43px}.material-info>div{gap:7px}}
</style>
