<script setup>
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, assetUrl, withBasePath, dateLabel, verdictLabel, verdictClass } from '../api';
import { workspace, currentBoard } from '../workspace';
import EmptyState from '../components/EmptyState.vue';
import StatusBadge from '../components/StatusBadge.vue';
import ReviewContractDetail from '../components/ReviewContractDetail.vue';
import PathViewer from '../components/PathViewer.vue';
const route=useRoute(),router=useRouter();
const reports=ref([]), report=ref(null), loading=ref(false), reportLoading=ref(false), error=ref(''), feedback=ref({}), profiles=ref([]), profileId=ref('');
const runId=ref(''), selectedKey=ref(''), search=ref(''), ruleFilter=ref(String(route.query.rule||'')), verdictFilter=ref(''), reviewFilter=ref(''), mobileTab=ref('issues');
const mark=ref('unreviewed'), note=ref(''), feedbackBusy=ref(false), feedbackNotice=ref(''), question=ref(''), questionHistory=ref([]), historyLoading=ref(false), historyError=ref(''), asking=ref(false), questionError=ref('');
const viewer=ref(null), historyOpen=ref(false), pageIndex=ref(0), compareId=ref(''), comparison=ref(null), comparing=ref(false), compareError=ref('');
let boardGen=0,reportGen=0,questionGen=0,historyGen=0;
const reportKey=r=>String(r.id||r.run_id||'');
const assetBase=computed(()=>report.value?.asset_base||`/reports/${encodeURIComponent(runId.value)}`);
const selectedReport=computed(()=>reports.value.find(r=>reportKey(r)===runId.value));
function cardsFor(data){
 const packs=Array.isArray(data?.evidence)?Object.fromEntries(data.evidence.map(pack=>[pack.rule_id,pack])):data?.evidence||{};
 return (data?.verdicts?.results||[]).flatMap(rule=>{
  const pack=packs[rule.rule_id]||{};
  return (pack.object_cards||rule.object_cards||[]).map(card=>({...card,rule_id:rule.rule_id,rule_title:rule.title||pack.name||rule.rule_id,key:`${rule.rule_id}::${card.object}`,view:card.review_detail||data?.report_data?.review_details?.[rule.rule_id]?.[card.object]}));
 });
}
const allCards=computed(()=>cardsFor(report.value));
const ruleOptions=computed(()=>report.value?.verdicts?.results||[]);
const filtered=computed(()=>allCards.value.filter(card=>{
 const text=`${card.rule_id} ${card.rule_title} ${card.object} ${card.headline_zh||card.conclusion||card.summary||''}`.toLowerCase();
 const state=feedback.value[card.key]?.mark||'unreviewed';
 return (!search.value||text.includes(search.value.toLowerCase()))&&(!ruleFilter.value||card.rule_id===ruleFilter.value)&&(!verdictFilter.value||verdictLabel(card.verdict_zh)===verdictFilter.value)&&(!reviewFilter.value||state===reviewFilter.value);
}));
const selected=computed(()=>allCards.value.find(card=>card.key===selectedKey.value));
const pages=computed(()=>selected.value?.view?.resources?.pages||[]);
const selectedPage=computed(()=>pages.value[pageIndex.value]||pages.value[0]);
const pdfUrl=computed(()=>{const page=selectedPage.value;if(!page?.pdf)return '';return assetUrl(assetBase.value,'pdfjs/web/viewer.html')+'?file='+encodeURIComponent(assetUrl(assetBase.value,page.pdf))+'#page='+(page.page||1);});
const countByVerdict=computed(()=>allCards.value.reduce((counts,card)=>{const label=verdictLabel(card.verdict_zh);counts[label]=(counts[label]||0)+1;return counts;},{}));
const feedbackLabels={unreviewed:'未复核',confirmed:'已确认',disputed:'有异议'};
const changes=computed(()=>{
 if(!comparison.value)return [];
 const before=new Map(cardsFor(comparison.value).map(card=>[card.key,card]));const after=new Map(allCards.value.map(card=>[card.key,card]));
 return [...new Set([...before.keys(),...after.keys()])].flatMap(key=>{
  const prev=before.get(key),next=after.get(key);
  if(prev&&next&&prev.verdict_zh===next.verdict_zh)return [];
  return [{key,rule_id:(next||prev).rule_id,object:(next||prev).object,before:prev?verdictLabel(prev.verdict_zh):'未纳入',after:next?verdictLabel(next.verdict_zh):'未纳入'}];
 });
});
async function loadBoardsReports(){const id=workspace.boardId,token=++boardGen;reportGen++;questionGen++;reports.value=[];report.value=null;runId.value='';feedback.value={};comparison.value=null;compareId.value='';error.value='';if(!id)return;loading.value=true;try{const [data,pp]=await Promise.all([api.reports(id),api.profiles()]);if(token!==boardGen)return;reports.value=data.reports||[];profiles.value=pp.profiles||[];profileId.value=pp.default_profile_id||profiles.value[0]?.id||'';const desired=String(route.query.run||'');const target=reports.value.find(r=>reportKey(r)===desired)||reports.value[0];if(target)await loadReport(reportKey(target));else if(desired)await loadReport(desired);}catch(e){if(token===boardGen)error.value=e.message;}finally{if(token===boardGen)loading.value=false;}}
async function loadReport(id){const token=++reportGen;questionGen++;reportLoading.value=true;report.value=null;runId.value=id;selectedKey.value='';feedback.value={};comparison.value=null;compareId.value='';questionHistory.value=[];question.value='';error.value='';try{const [data,fb]=await Promise.all([api.report(id),api.feedback(id)]);if(token!==reportGen)return;report.value=data;feedback.value=fb.feedback||{};const desired=route.query.rule&&route.query.object?`${route.query.rule}::${route.query.object}`:'';selectedKey.value=allCards.value.some(c=>c.key===desired)?desired:allCards.value.find(c=>!ruleFilter.value||c.rule_id===ruleFilter.value)?.key||allCards.value[0]?.key||'';}catch(e){if(token===reportGen)error.value=e.message;}finally{if(token===reportGen)reportLoading.value=false;}}
watch(()=>workspace.boardId,loadBoardsReports,{immediate:true});
watch(()=>route.query.run,id=>{if(id&&id!==runId.value&&!loading.value)loadReport(String(id));});
watch(()=>[route.query.rule,route.query.object],()=>{const key=`${route.query.rule}::${route.query.object}`;if(!route.query.object||key!==selectedKey.value)ruleFilter.value=String(route.query.rule||'');if(allCards.value.some(c=>c.key===key))selectedKey.value=key;});
watch(selectedKey,()=>{pageIndex.value=0;questionGen++;asking.value=false;question.value='';questionHistory.value=[];questionError.value='';feedbackNotice.value='';const value=feedback.value[selectedKey.value];mark.value=value?.mark||'unreviewed';note.value=value?.note||'';});
watch(filtered,list=>{if(list.length&&!list.some(c=>c.key===selectedKey.value))selectedKey.value=list[0].key;});
async function loadQuestionHistory(){
 const token=++historyGen,card=selected.value,run=runId.value;
 questionHistory.value=[];historyError.value='';historyLoading.value=!!card;
 if(!card)return;
 try{const data=await api.questions(run,card.rule_id,card.object);if(token===historyGen&&run===runId.value&&card.key===selectedKey.value)questionHistory.value=data.questions||[];}
 catch(e){if(token===historyGen&&run===runId.value&&card.key===selectedKey.value)historyError.value=e.message;}
 finally{if(token===historyGen)historyLoading.value=false;}
}
watch(()=>[runId.value,selectedKey.value],loadQuestionHistory);

function select(card){selectedKey.value=card.key;router.replace({path:'/reports',query:{board:workspace.boardId,run:runId.value,rule:card.rule_id,object:card.object}});mobileTab.value='detail';}
function changeReport(event){router.replace({path:'/reports',query:{board:workspace.boardId,run:event.target.value}});}
async function saveFeedback(){const card=selected.value;if(!card)return;const key=card.key,run=runId.value;feedbackBusy.value=true;feedbackNotice.value='';try{const payload={mark:mark.value,note:note.value};const value=await api.saveFeedback(run,card.rule_id,card.object,payload);if(run===runId.value){feedback.value={...feedback.value,[key]:value.feedback||value.record||value};if(selectedKey.value===key)feedbackNotice.value='复核意见已保存。';}}catch(e){error.value=e.message;}finally{feedbackBusy.value=false;}}
async function ask(){
 const card=selected.value;if(!card||!question.value.trim()||asking.value||historyLoading.value)return;
 const token=++questionGen;asking.value=true;questionError.value='';
 try{const data=await api.ask(runId.value,card.rule_id,card.object,{question:question.value.trim(),profile_id:profileId.value||null});if(token===questionGen){questionHistory.value=[...questionHistory.value.filter(row=>row.id!==data.id),data];question.value='';}}
 catch(e){if(token===questionGen)questionError.value=e.message;}
 finally{if(token===questionGen)asking.value=false;}
}
async function compare(){comparison.value=null;compareError.value='';if(!compareId.value)return;comparing.value=true;const id=compareId.value;try{const data=await api.report(id);if(compareId.value===id)comparison.value=data;}catch(e){compareError.value=e.message;}finally{comparing.value=false;}}
async function rerun(){if(!selected.value)return;const card=selected.value;router.push({path:'/runs',query:{board:workspace.boardId,rule:card.rule_id,object:card.object,mode:'review'}});}
function openPath(state){const p=state.figure_pages?.length?state.figure_pages:state.figure?[{src:state.figure,label:state.title||''}]:[];viewer.value={pages:p.map(row=>({...row,src:assetUrl(assetBase.value,row.src||row.figure)})),lines:(state.calculations||[]).map(c=>({expr:c.expression,src:''})),who:`${selected.value.rule_id} / ${selected.value.object}`};}
onBeforeUnmount(()=>{boardGen++;reportGen++;questionGen++;historyGen++;});
</script>
<template>
  <EmptyState v-if="!workspace.boardId&&!workspace.loading" title="先选择一个项目" description="在项目中完成一次评审后，可在这里核对问题与证据。"><router-link class="button primary" to="/">前往项目</router-link></EmptyState>
  <section v-else class="report-workspace">
    <div class="report-bar"><div class="report-title"><h1>评审工作台</h1><span>{{ currentBoard?.name }}</span></div><template v-if="report"><div class="report-selector"><label for="report-version">批次</label><select id="report-version" :value="runId" @change="changeReport"><option v-for="r in reports" :key="reportKey(r)" :value="reportKey(r)">{{ r.title || dateLabel(r.finished_at||r.created_at) }} · {{ reportKey(r).slice(0,8) }}</option><option v-if="!selectedReport" :value="runId">{{ runId.slice(0,12) }}</option></select></div><div class="report-counts" aria-label="结论统计"><span v-for="label in ['通过','不通过','待确认','不适用']" :key="label" class="verdict-count" :class="[verdictClass(label),{zero:!countByVerdict[label]}]"><i aria-hidden="true"></i>{{ label }}<b>{{ countByVerdict[label]||0 }}</b></span><span class="report-object-count">共 {{ allCards.length }} 个对象</span></div></template><div class="actions report-actions"><button v-if="report" class="button small" :class="{active:historyOpen}" :aria-expanded="historyOpen" aria-controls="report-history" @click="historyOpen=!historyOpen">历史与导出</button><router-link class="button small" :to="{path:'/runs',query:{board:workspace.boardId}}">新建评审</router-link><a v-if="report" class="button small" :href="assetUrl(assetBase,'expert_review.html')" target="_blank" rel="noopener">完整报告 ↗</a></div></div>
    <div v-if="error" class="notice error report-notice" role="alert">{{ error }}<button class="button small" @click="loadBoardsReports">重新加载</button></div>
    <div v-if="loading||reportLoading" class="loading-state" aria-live="polite">正在加载评审报告</div>
    <EmptyState v-else-if="!report&&!error" title="还没有已发布的报告" description="在运行页选择检查范围并启动任务。通过校验的报告会出现在这里，未完成的结果不会覆盖已有报告。"><router-link class="button primary" :to="{path:'/runs',query:{board:workspace.boardId}}">开始一次评审</router-link></EmptyState>
    <template v-else-if="report"><div v-if="historyOpen" id="report-history" class="history-panel"><div class="actions"><label for="compare-report">与历史批次比较</label><select id="compare-report" v-model="compareId" @change="compare"><option value="">选择已发布报告</option><option v-for="r in reports.filter(r=>reportKey(r)!==runId)" :key="reportKey(r)" :value="reportKey(r)">{{ dateLabel(r.finished_at||r.created_at) }} · {{ reportKey(r).slice(0,8) }}</option></select><span class="history-spacer"></span><a class="button small primary" :href="withBasePath(`/api/reports/${encodeURIComponent(runId)}/download`)" download>下载完整报告包</a><a class="button small" :href="assetUrl(assetBase,'report_data.json')" download>报告数据</a><a class="button small" :href="assetUrl(assetBase,'expert_review.html')" download>HTML</a></div><p class="inline-note">比较同一规则与对象的工程结论；“未纳入”表示该次报告未覆盖此对象。HTML 的图纸和附件需在工作台中查看。</p><p v-if="comparing" class="small-copy muted">正在读取历史报告…</p><div v-if="compareError" class="notice error">{{ compareError }}</div><div v-if="comparison" class="data-table-wrap"><table v-if="changes.length" class="data-table"><thead><tr><th>检查项</th><th>对象</th><th>历史结论</th><th>本次结论</th></tr></thead><tbody><tr v-for="change in changes" :key="change.key"><td class="mono">{{ change.rule_id }}</td><td class="mono">{{ change.object }}</td><td>{{ change.before }}</td><td>{{ change.after }}</td></tr></tbody></table><p v-else class="small-copy">两次报告中，同一规则与对象的工程结论没有变化。</p></div></div>
      <nav class="inline-tabs report-mobile-tabs" aria-label="工作区视图"><button v-for="(label,key) in {issues:'问题清单',drawing:'原理图',detail:'分析与复核'}" :key="key" :class="{active:mobileTab===key}" @click="mobileTab=key">{{ label }}</button></nav>
      <div class="review-columns" :data-mobile-tab="mobileTab"><aside class="issues-pane"><div class="pane-heading"><h2>问题清单</h2><span>{{ filtered.length }} / {{ allCards.length }}</span></div><div class="issue-filters"><label class="sr-only" for="object-search">搜索对象或问题</label><input id="object-search" v-model="search" class="search-input" placeholder="搜索位号、规则或问题" /><label class="sr-only" for="rule-filter">筛选检查项</label><select id="rule-filter" v-model="ruleFilter"><option value="">全部检查项</option><option v-for="rule in ruleOptions" :key="rule.rule_id" :value="rule.rule_id">{{ rule.rule_id }} · {{ rule.title }}</option></select><div><label class="sr-only" for="verdict-filter">筛选结论</label><select id="verdict-filter" v-model="verdictFilter"><option value="">全部结论</option><option v-for="label in ['通过','不通过','待确认','不适用']" :key="label">{{ label }}</option></select><label class="sr-only" for="review-filter">筛选复核状态</label><select id="review-filter" v-model="reviewFilter"><option value="">全部复核状态</option><option v-for="(label,key) in feedbackLabels" :key="key" :value="key">{{ label }}</option></select></div></div><div class="issue-list"><button v-for="card in filtered" :key="card.key" class="issue-row" :class="{selected:card.key===selectedKey}" @click="select(card)" :aria-pressed="card.key===selectedKey"><div class="issue-row-top"><strong>{{ card.object }}</strong><StatusBadge :tone="verdictClass(card.verdict_zh)">{{ verdictLabel(card.verdict_zh) }}</StatusBadge></div><span class="issue-rule">{{ card.rule_id }} · {{ card.rule_title }}</span><p>{{ card.headline_zh||card.conclusion||card.summary||'打开查看评审依据' }}</p><small :class="feedback[card.key]?.mark||'unreviewed'">{{ feedbackLabels[feedback[card.key]?.mark]||'未复核' }}</small></button><p v-if="!filtered.length" class="no-filter-results">没有匹配的对象，试试调整筛选条件。</p></div></aside>
      <section class="drawing-pane"><div class="pane-heading"><h2>原理图</h2><a v-if="pdfUrl" :href="pdfUrl" target="_blank" rel="noopener">独立窗口 ↗</a></div><div v-if="pages.length" class="drawing-controls"><label for="schematic-page">关联图幅</label><select id="schematic-page" v-model="pageIndex"><option v-for="(page,index) in pages" :key="index" :value="index">第 {{ page.page }} 页{{ page.title?' · '+page.title:'' }}</option></select><span>{{ selected?.object }}</span></div><iframe v-if="pdfUrl" :key="pdfUrl" :src="pdfUrl" :title="`${selected?.object} 的关联原理图第 ${selectedPage?.page} 页`"></iframe><EmptyState v-else title="当前对象尚未关联图幅" description="这里展示报告中已有的可靠定位。可以先在右侧核对材料与证据。" /></section>
      <section class="analysis-pane"><div class="pane-heading"><h2><span class="pane-object">{{ selected?.object||'对象详情' }}</span> <span v-if="selected">{{ selected.rule_id }}</span></h2><StatusBadge v-if="selected" :tone="verdictClass(selected.verdict_zh)">{{ verdictLabel(selected.verdict_zh) }}</StatusBadge></div><div v-if="selected" class="analysis-content"><ReviewContractDetail v-if="selected.view" :view="selected.view" :asset-base="assetBase" :source-assets="report.source_assets || {}" @open-path="openPath" /><div v-else class="notice warning">这份报告尚未生成统一分析详情。请在运行页重新生成完整报告。</div>
      <form class="feedback-panel" @submit.prevent="saveFeedback"><h3>人工复核</h3><p>复核意见独立记录，不修改原始工程判定。</p><div class="feedback-options" role="radiogroup" aria-label="复核状态"><label v-for="(label,key) in feedbackLabels" :key="key"><input type="radio" name="review-mark" v-model="mark" :value="key" />{{ label }}</label></div><label class="sr-only" for="feedback-note">复核意见</label><textarea id="feedback-note" v-model="note" rows="3" placeholder="记录核查依据、疑问或需要补充的材料" maxlength="10000"></textarea><div class="actions"><button class="button small primary" :disabled="feedbackBusy">{{ feedbackBusy?'保存中…':'保存复核意见' }}</button><span v-if="feedbackNotice" class="save-notice" role="status">{{ feedbackNotice }}</span></div></form>
      <details class="question-panel"><summary>围绕此对象追问</summary><p>回答基于本次报告与证据，追问不会修改原始判定。</p><div v-if="!profiles.length" class="notice warning">请先在<router-link to="/models">模型设置</router-link>中配置服务。</div><form @submit.prevent="ask"><div class="field"><label for="question-profile">使用模型</label><select id="question-profile" v-model="profileId"><option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name||p.model }}</option></select></div><label class="sr-only" for="object-question">针对当前对象的问题</label><textarea id="object-question" v-model="question" rows="3" placeholder="例如：这个结论依赖哪些材料？还有哪些条件需要确认？" required></textarea><div class="actions"><button class="button small" :disabled="asking||historyLoading||!profileId||!question.trim()">{{ asking?'正在分析…':'发送问题' }}</button><button class="button small ghost" type="button" @click="rerun">设置此对象复核 →</button></div></form><div v-if="questionError" class="notice error" role="alert">{{ questionError }}</div><p v-if="historyLoading" class="small-copy muted" role="status">正在加载此对象的已保存问答…</p><div v-if="historyError" class="notice error" role="alert">问答记录加载失败：{{ historyError }}<button class="button small" type="button" :disabled="asking||historyLoading" @click="loadQuestionHistory">重试加载</button></div><p v-if="!historyLoading&&!historyError&&!questionHistory.length" class="small-copy muted">此对象还没有已保存的问答。</p><article v-for="entry in questionHistory.slice().reverse()" :key="entry.id" class="question-answer" :data-question-id="entry.id"><div class="question-record-meta"><span>{{ dateLabel(entry.created_at) }}</span><span v-if="entry.metadata?.model">{{ entry.metadata.model }}</span></div><h4>{{ entry.question }}</h4><p>{{ entry.answer||entry.text }}</p><ul v-if="entry.citations?.length"><li v-for="(citation,index) in entry.citations" :key="index">{{ typeof citation==='string'?citation:[citation.source_id||citation.label||citation.source,citation.quote].filter(Boolean).join(' · ')||JSON.stringify(citation) }}</li></ul></article></details>
      </div><EmptyState v-else title="选择一个对象" description="从问题清单中选择对象，查看它的分析与复核记录。" /></section></div>
    </template>
    <PathViewer v-if="viewer" :pages="viewer.pages" :lines="viewer.lines" :who="viewer.who" @close="viewer=null" />
  </section>
</template>
<style scoped>
.report-workspace{display:flex;flex-direction:column;gap:12px;height:calc(100vh - var(--wb-header));height:calc(100dvh - var(--wb-header));min-height:620px;padding:12px 16px 16px}
.report-workspace>.empty-state{margin:auto}
.report-bar{display:flex;align-items:center;flex-wrap:wrap;gap:10px 20px;flex-shrink:0;min-height:32px}
.report-title{display:flex;align-items:baseline;gap:8px;min-width:0}
.report-title h1{font-size:15px;margin:0;white-space:nowrap}
.report-title span{font-size:13px;color:var(--wb-muted);white-space:nowrap}
.report-selector{display:flex;align-items:center;gap:8px;min-width:0}
.report-selector label,.history-panel label{font-size:12px;color:var(--wb-muted);white-space:nowrap}
.report-selector select,.history-panel select{min-height:30px;padding-top:4px;padding-bottom:4px;font-size:12.5px;max-width:320px}
.report-counts{display:flex;align-items:center;flex-wrap:wrap;gap:6px 14px;font-size:12.5px;color:var(--wb-text)}
.verdict-count{display:inline-flex;align-items:center;gap:6px;white-space:nowrap}
.verdict-count i{width:7px;height:7px;border-radius:50%;background:#98a2b3}
.verdict-count b{font-weight:600;font-variant-numeric:tabular-nums;color:var(--wb-ink)}
.verdict-count.success i{background:#12b76a}
.verdict-count.danger i{background:#f04438}
.verdict-count.warning i{background:#f79009}
.verdict-count.zero{color:var(--wb-faint)}
.verdict-count.zero i{background:#d0d5dd}
.verdict-count.zero b{color:var(--wb-faint);font-weight:500}
.report-object-count{color:var(--wb-muted);padding-left:14px;border-left:1px solid var(--wb-border)}
.report-actions{margin-left:auto}
.report-notice{margin:0;flex-shrink:0}
.history-panel{flex-shrink:0;max-height:42vh;overflow:auto;padding:14px 16px;background:#fff;border:1px solid var(--wb-border);border-radius:8px}
.history-panel .inline-note{margin:8px 0 0}
.history-panel .data-table-wrap{margin-top:10px;border:1px solid var(--wb-border);border-radius:6px}
.history-spacer{flex:1}
.review-columns{flex:1;min-height:0;display:grid;grid-template-columns:280px minmax(0,1fr) minmax(380px,460px);background:#fff;border:1px solid var(--wb-border);border-radius:8px;overflow:hidden}
.issues-pane,.drawing-pane,.analysis-pane{display:flex;flex-direction:column;min-width:0;min-height:0;background:#fff}
.drawing-pane,.analysis-pane{border-left:1px solid var(--wb-border)}
.pane-heading{flex-shrink:0;height:44px;padding:0 14px;border-bottom:1px solid var(--wb-border);display:flex;align-items:center;justify-content:space-between;gap:8px}
.pane-heading h2{font-size:13px;font-weight:600;margin:0;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.pane-heading h2>span:not(.pane-object){font-size:12px;font-family:var(--wb-mono);color:var(--wb-muted);font-weight:400;margin-left:4px}
.pane-object{font-family:var(--wb-mono);font-size:14px}
.pane-heading>span{font-size:12px;color:var(--wb-muted);font-variant-numeric:tabular-nums}
.pane-heading>a{font-size:12px;color:var(--wb-text);text-decoration:none;padding:3px 6px;border-radius:4px}
.pane-heading>a:hover{background:var(--wb-subtle);color:var(--wb-ink)}
.issue-filters{flex-shrink:0;padding:10px;border-bottom:1px solid var(--wb-border);display:grid;gap:6px;background:#fcfcfd}
.issue-filters :is(input,select){width:100%;min-height:30px;padding-top:4px;padding-bottom:4px;font-size:12.5px}
.issue-filters>div{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.issue-list{flex:1;min-height:0;overflow:auto}
.issue-row{display:block;width:100%;padding:11px 14px 11px 16px;background:#fff;border:0;border-bottom:1px solid var(--wb-divider);text-align:left;position:relative;color:inherit}
.issue-row:hover{background:var(--wb-hover)}
.issue-row.selected{background:var(--wb-accent-soft)}
.issue-row.selected:before{content:'';position:absolute;left:0;top:0;bottom:0;width:2px;background:var(--wb-accent)}
.issue-row:focus-visible{outline-offset:-2px}
.issue-row-top{display:flex;align-items:center;justify-content:space-between;gap:8px}
.issue-row-top strong{font-family:var(--wb-mono);font-size:13.5px;font-weight:600;color:var(--wb-ink)}
.issue-row .status-badge{min-height:20px;font-size:11.5px;padding:1px 7px}
.issue-rule{display:block;margin-top:4px;color:var(--wb-muted);font-size:11.5px;line-height:1.5;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.issue-row p{font-size:12.5px;line-height:1.6;margin:4px 0 6px;color:var(--wb-text);display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.issue-row small{font-size:11px;color:var(--wb-faint)}
.issue-row small.confirmed{color:var(--wb-success)}
.issue-row small.disputed{color:var(--wb-danger)}
.no-filter-results{padding:20px 14px;font-size:12.5px;color:var(--wb-muted)}
.drawing-controls{flex-shrink:0;display:flex;align-items:center;gap:8px;padding:6px 12px;background:#fcfcfd;border-bottom:1px solid var(--wb-border);font-size:12px;color:var(--wb-muted)}
.drawing-controls select{min-width:0;max-width:65%;min-height:28px;padding-top:3px;padding-bottom:3px;font-size:12px}
.drawing-controls>span{margin-left:auto;font-family:var(--wb-mono);color:var(--wb-text)}
.drawing-pane iframe{flex:1;min-height:0;width:100%;border:0;display:block;background:#eef0f3}
.drawing-pane .empty-state{margin:auto;padding:24px 16px}
.analysis-content{flex:1;min-height:0;overflow:auto}
.analysis-content>.notice{margin:14px}
.analysis-content :deep(.review-detail){font-family:inherit;color:var(--wb-text)}
.analysis-content :deep(.review-detail .rv-summary),.analysis-content :deep(.review-detail .rv-premise),.analysis-content :deep(.review-detail .rv1 .rv-panel),.analysis-content :deep(.review-detail .rv-extract),.analysis-content :deep(.review-detail .rv-sheets){margin:0;padding:16px 18px;border:0;border-bottom:1px solid var(--wb-divider);border-radius:0;background:#fff;box-shadow:none}
.analysis-content :deep(.review-detail .rv-summary){background:#fafbfc}
.analysis-content :deep(.review-detail .rv-summary>p){font-size:14px;font-weight:500;line-height:1.75;color:var(--wb-ink)}
.analysis-content :deep(.review-detail .rv1 .rv-primary){border-left:0;background:#fff}
.analysis-content :deep(.review-detail .rv-section-title),.analysis-content :deep(.review-detail h3.rv-section-title),.analysis-content :deep(.review-detail .rv1 h3.rv-section-title){display:block;font-size:12px;font-weight:600;color:var(--wb-muted);letter-spacing:.02em;margin:0 0 10px}
.analysis-content :deep(.review-detail .rv-section-title::before){display:none}
.analysis-content :deep(.review-detail .rv1){font-size:13px;line-height:1.7;color:var(--wb-text)}
.analysis-content :deep(.review-detail .rv-premise){font-size:13px;line-height:1.7;color:var(--wb-text)}
.analysis-content :deep(.review-detail h4),.analysis-content :deep(.review-detail .premise-label),.analysis-content :deep(.review-detail .rv-subtitle){font-size:12px;font-weight:600;color:var(--wb-text);margin:0 0 6px}
.analysis-content :deep(.review-detail .inputs p){margin:2px 0;color:var(--wb-ink)}
.analysis-content :deep(.review-detail .rv-premise .premise-reason){grid-template-columns:1fr;gap:8px;padding-top:12px;border-top:1px solid var(--wb-divider)}
.analysis-content :deep(.review-detail .rv-premise .rv-reason-steps li){grid-template-columns:20px minmax(0,1fr);gap:8px;font-size:13px;line-height:1.7;color:var(--wb-text)}
.analysis-content :deep(.review-detail .rv-premise .rv-reason-steps li::before){height:20px;line-height:20px;font-size:11px;color:var(--wb-muted);background:var(--wb-subtle);border-radius:4px;font-family:var(--wb-mono)}
.analysis-content :deep(.review-detail .rv-assumption-pending),.analysis-content :deep(.review-detail .rv1 .rv-tag){color:var(--wb-warning);background:#fffaeb;border-radius:4px}
.analysis-content :deep(.review-detail .rv1 .rv-tag-neutral){color:var(--wb-text);background:var(--wb-subtle)}
.analysis-content :deep(.review-detail .rv1 .rv-metrics),.analysis-content :deep(.review-detail .rv1 .rv-decision .rv-metrics){grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin-bottom:8px}
.analysis-content :deep(.review-detail .rv1 .rv-decision .rv-metrics>div),.analysis-content :deep(.review-detail .rv1 .rv-decision .rv-metrics>div+div){border:1px solid var(--wb-border);border-radius:6px;padding:8px 12px;background:#fff}
.analysis-content :deep(.review-detail .rv1 .rv-metrics small){font-size:11.5px;color:var(--wb-muted)}
.analysis-content :deep(.review-detail .rv1 .rv-metrics strong),.analysis-content :deep(.review-detail .rv1 .rv-decision .rv-metrics strong){font-size:17px;font-weight:600;line-height:1.4;margin-top:2px;color:var(--wb-ink);font-variant-numeric:tabular-nums}
.analysis-content :deep(.review-detail .rv1 .rv-decision){background:#fff}
.analysis-content :deep(.review-detail .rv1 .rv-decision>p){color:var(--wb-text);font-size:13px}
.analysis-content :deep(.review-detail .rv1 .rv-route),.analysis-content :deep(.review-detail .rv1 .rv-primary .rv-route){padding:10px 12px;margin:10px 0;border:0;border-radius:6px;background:var(--wb-subtle);color:var(--wb-ink);font-size:13px;font-weight:500;line-height:1.7}
.analysis-content :deep(.review-detail .rv1 .rv-case-title){font-size:14px;color:var(--wb-ink)}
.analysis-content :deep(.review-detail .rv1 .rv-condition){font-size:12.5px;color:var(--wb-muted)}
.analysis-content :deep(.review-detail .rv1 .rv-condition b){color:var(--wb-text)}
.analysis-content :deep(.review-detail .rv1 .rv-calcs){border:1px solid var(--wb-border);border-radius:6px}
.analysis-content :deep(.review-detail .rv1 .rv-calcs li){padding:7px 12px;background:#fafbfc;font-size:12.5px;color:var(--wb-ink)}
.analysis-content :deep(.review-detail .rv1 .rv-calcs li+li){border-top-color:var(--wb-divider)}
.analysis-content :deep(.review-detail .rv1 .rv-calcs li::before){color:var(--wb-faint);font-family:var(--wb-mono);font-size:11px}
.analysis-content :deep(.review-detail .rv1 .rv-value){color:var(--wb-ink)}
.analysis-content :deep(.review-detail .figure){border:1px solid var(--wb-border);border-radius:6px;padding:6px;color:var(--wb-text);font-size:12px}
.analysis-content :deep(.review-detail .rv1 .rv-audit){padding:0;background:#fff}
.analysis-content :deep(.review-detail .rv1 .rv-audit>summary){padding:13px 18px;font-size:13px;font-weight:600;color:var(--wb-ink)}
.analysis-content :deep(.review-detail .rv1 .rv-audit>summary::before){display:none}
.analysis-content :deep(.review-detail .rv1 .rv-audit>summary>span){display:block;margin:2px 0 0;font-size:11.5px;font-weight:400;color:var(--wb-muted)}
.analysis-content :deep(.review-detail .rv1 .rv-audit>summary::after){color:var(--wb-faint);margin-top:-18px}
.analysis-content :deep(.review-detail .rv1 .rv-audit-body){padding:0 18px 16px;border-top:1px solid var(--wb-divider)}
.analysis-content :deep(.review-detail .rv-audit-body h4){display:block;background:none;padding:0;color:var(--wb-text)}
.analysis-content :deep(.review-detail .rv1 .rv-audit-list article){border:1px solid var(--wb-border);border-radius:6px;padding:9px 12px}
.analysis-content :deep(.review-detail .rv1 .rv-audit-list p){color:var(--wb-muted);font-size:12.5px}
.analysis-content :deep(.review-detail .rv1 .rv-note),.analysis-content :deep(.review-detail .rv-basis-scope){background:var(--wb-subtle);border:0;border-radius:6px}
.analysis-content :deep(.review-detail .rv-extract p),.analysis-content :deep(.review-detail .rv-sheets p){font-size:12.5px;margin:4px 0}
.analysis-content :deep(.review-detail .rv-extract a),.analysis-content :deep(.review-detail .rv-sheets a){display:inline-block;margin:6px 0;font-size:12.5px;font-weight:500;text-decoration:none}
.analysis-content :deep(.review-detail .rv-extract a:hover),.analysis-content :deep(.review-detail .rv-sheets a:hover){text-decoration:underline}
.analysis-content :deep(.review-detail .rv-sheets img){border:1px solid var(--wb-border);border-radius:6px}
.feedback-panel,.question-panel{margin:0;padding:16px 18px;border-bottom:1px solid var(--wb-divider);background:#fff}
.feedback-panel h3{font-size:13px;margin:0}
.feedback-panel>p,.question-panel>p{font-size:12px;line-height:1.6;color:var(--wb-muted);margin:2px 0 12px}
.feedback-options{display:inline-flex;gap:2px;padding:2px;margin-bottom:10px;background:#eceef2;border-radius:7px}
.feedback-options label{position:relative;padding:4px 12px;border-radius:5px;font-size:12.5px;color:var(--wb-text);cursor:pointer;white-space:nowrap}
.feedback-options input{position:absolute;inset:0;width:100%;height:100%;opacity:0;cursor:pointer}
.feedback-options label:has(input:checked){background:#fff;color:var(--wb-ink);font-weight:500;box-shadow:0 1px 2px rgba(16,24,40,.1)}
.feedback-options label:has(input:focus-visible){outline:2px solid var(--wb-accent);outline-offset:1px}
.feedback-panel textarea,.question-panel textarea{display:block;width:100%;resize:vertical;min-height:80px;margin-bottom:10px;line-height:1.65}
.question-panel>summary{cursor:pointer;font-weight:600;font-size:13px;color:var(--wb-ink)}
.question-panel[open]>summary{margin-bottom:2px}
.question-panel .field{margin-bottom:10px}
.question-panel .field>label{font-size:12px}
.question-panel .notice{margin-top:10px;font-size:12.5px}
.question-answer{border-top:1px solid var(--wb-divider);margin-top:14px;padding-top:12px;font-size:13px;line-height:1.75;color:var(--wb-text)}
.question-record-meta{display:flex;flex-wrap:wrap;gap:4px 12px;color:var(--wb-muted);font-size:11.5px;margin-bottom:6px}
.question-answer h4{font-size:13px;line-height:1.6;margin:0 0 6px;color:var(--wb-ink)}
.question-answer p{white-space:pre-wrap;margin:0}
.question-answer ul{padding-left:18px;font-size:12px;color:var(--wb-muted)}
.save-notice{font-size:12px;color:var(--wb-success)}
.report-mobile-tabs{display:none}
@media(max-width:1300px){.review-columns{grid-template-columns:240px minmax(0,1fr) minmax(330px,390px)}.report-object-count{display:none}}
@media(max-width:1100px){.report-workspace{height:auto;min-height:0;padding:12px 16px 24px}.report-mobile-tabs{display:flex;flex-shrink:0}.report-actions{margin-left:0}.review-columns{display:block;flex:none;overflow:visible}.review-columns>*{border-radius:8px}.review-columns[data-mobile-tab='issues'] .drawing-pane,.review-columns[data-mobile-tab='issues'] .analysis-pane,.review-columns[data-mobile-tab='drawing'] .issues-pane,.review-columns[data-mobile-tab='drawing'] .analysis-pane,.review-columns[data-mobile-tab='detail'] .issues-pane,.review-columns[data-mobile-tab='detail'] .drawing-pane{display:none}.drawing-pane,.analysis-pane{border-left:0}.issue-list{max-height:none;min-height:240px}.drawing-pane iframe{flex:none;height:72vh;min-height:440px}.analysis-content{overflow:visible}}
@media(max-width:600px){.report-bar{gap:10px}.report-selector{width:100%}.report-selector select{flex:1;max-width:none}.report-actions{width:100%}.report-actions>*{flex:1}.history-panel .actions{align-items:stretch;flex-direction:column}.history-panel select{max-width:none}.history-spacer{display:none}.feedback-panel textarea,.question-panel textarea{font-size:16px}.feedback-options{display:flex}.feedback-options label{flex:1;text-align:center}}
</style>
