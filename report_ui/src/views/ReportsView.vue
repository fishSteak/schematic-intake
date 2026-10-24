<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { api, assetUrl, withBasePath, dateLabel, verdictLabel, verdictClass } from '../api';
import { workspace, currentBoard } from '../workspace';
import EmptyState from '../components/EmptyState.vue';
import StatusBadge from '../components/StatusBadge.vue';
import ReviewContractDetail from '../components/ReviewContractDetail.vue';
import PathViewer from '../components/PathViewer.vue';
import AppIcon from '../components/AppIcon.vue';
const route=useRoute(),router=useRouter();
const reports=ref([]), report=ref(null), loading=ref(false), reportLoading=ref(false), error=ref(''), feedback=ref({}), profiles=ref([]), profileId=ref('');
const runId=ref(''), selectedKey=ref(''), search=ref(''), ruleFilter=ref(String(route.query.rule||'')), verdictFilter=ref(''), reviewFilter=ref(''), mobileTab=ref(route.query.object?'detail':'issues');
const mark=ref('unreviewed'), note=ref(''), feedbackBusy=ref(false), feedbackNotice=ref(''), question=ref(''), questionHistory=ref([]), historyLoading=ref(false), historyError=ref(''), asking=ref(false), questionError=ref('');
const viewer=ref(null), pageIndex=ref(0), compareId=ref(''), comparison=ref(null), comparing=ref(false), compareError=ref('');
const historyOpen=ref(false), issuesExpanded=ref(false), analysisContent=ref(null), smallScreen=ref(false);
const mobileViews=[{id:'issues',label:'问题清单'},{id:'drawing',label:'原理图'},{id:'detail',label:'分析与复核'}];
const detailLinks=[{id:'summary',label:'判定'},{id:'premises',label:'分析'},{id:'primary',label:'图与计算'},{id:'audit',label:'证据'},{id:'feedback',label:'复核'},{id:'questions',label:'追问'}];
let screenQuery;
onMounted(()=>{screenQuery=window.matchMedia('(max-width: 760px)');smallScreen.value=screenQuery.matches;screenQuery.addEventListener('change',syncScreen);});
function syncScreen(event){smallScreen.value=event.matches;}
async function switchMobileView(id,focusTab=false){mobileTab.value=id;await nextTick();if(focusTab)document.getElementById('review-tab-'+id)?.focus();}
function onMobileTabKey(event){const ids=mobileViews.map(v=>v.id),index=ids.indexOf(mobileTab.value);let next;if(event.key==='ArrowRight')next=(index+1)%ids.length;else if(event.key==='ArrowLeft')next=(index-1+ids.length)%ids.length;else if(event.key==='Home')next=0;else if(event.key==='End')next=ids.length-1;else return;event.preventDefault();switchMobileView(ids[next],true);}
async function jumpTo(id){
 mobileTab.value='detail';await nextTick();const container=analysisContent.value;if(!container)return;
 const selector=id==='feedback'?'.feedback-panel':id==='questions'?'.question-panel':`[data-review-module="${id}"]`;
 const target=container.querySelector(selector);if(!target)return;
 if(target.tagName==='DETAILS')target.open=true;
 target.setAttribute('tabindex','-1');target.focus({preventScroll:true});
 if(smallScreen.value)target.scrollIntoView({block:'start',behavior:'instant'});
 else container.scrollTo({top:target.getBoundingClientRect().top-container.getBoundingClientRect().top+container.scrollTop-12,behavior:'instant'});
}
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
watch(()=>[route.query.rule,route.query.object],()=>{const key=`${route.query.rule}::${route.query.object}`;if(!route.query.object||key!==selectedKey.value)ruleFilter.value=String(route.query.rule||'');if(allCards.value.some(c=>c.key===key)){selectedKey.value=key;if(route.query.object)mobileTab.value='detail';}});
watch(selectedKey,()=>{pageIndex.value=0;questionGen++;asking.value=false;question.value='';questionHistory.value=[];questionError.value='';feedbackNotice.value='';const value=feedback.value[selectedKey.value];mark.value=value?.mark||'unreviewed';note.value=value?.note||'';});
watch(selectedKey,async()=>{await nextTick();analysisContent.value?.scrollTo({top:0,behavior:'instant'});});
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

function select(card){issuesExpanded.value=false;selectedKey.value=card.key;router.replace({path:'/reports',query:{board:workspace.boardId,run:runId.value,rule:card.rule_id,object:card.object}});mobileTab.value='detail';}
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
onBeforeUnmount(()=>{boardGen++;reportGen++;questionGen++;historyGen++;screenQuery?.removeEventListener('change',syncScreen);});
</script>
<template>
  <EmptyState v-if="!workspace.boardId&&!workspace.loading" title="先选择一个项目" description="在项目中完成一次评审后，可在这里核对问题与证据。"><router-link class="button primary" to="/">前往项目</router-link></EmptyState>
  <section v-else class="report-workspace">
    <header class="report-heading">
      <div class="report-title"><h1>评审工作台</h1><span>{{ currentBoard?.name }}</span></div>
      <div class="actions">
        <router-link class="button small" :to="{path:'/runs',query:{board:workspace.boardId}}"><AppIcon name="plus" />新建评审</router-link>
        <a v-if="report" class="button small" :href="assetUrl(assetBase,'expert_review.html')" target="_blank" rel="noopener">完整报告<AppIcon name="external" /></a>
        <button v-if="report" class="button small report-mobile-history" type="button" aria-label="历史比较与导出" :aria-expanded="historyOpen" aria-controls="report-history" @click="historyOpen=!historyOpen"><AppIcon name="layers" /></button>
      </div>
    </header>
    <div v-if="error" class="notice error" role="alert">{{ error }}<button class="button small" @click="loadBoardsReports">重新加载</button></div>
    <div v-if="loading||reportLoading" class="loading-state" role="status">正在加载完整评审报告</div>
    <EmptyState v-else-if="!report&&!error" title="还没有已发布的报告" description="从运行页面选择检查范围并启动任务。完成校验的报告会出现在这里，未完成结果不会覆盖已有报告。"><router-link class="button primary" :to="{path:'/runs',query:{board:workspace.boardId}}">开始一次评审</router-link></EmptyState>
    <template v-else-if="report">
      <div class="report-toolbar">
        <div class="report-selector"><label for="report-version">批次</label><select id="report-version" :value="runId" @change="changeReport"><option v-for="r in reports" :key="reportKey(r)" :value="reportKey(r)">{{ r.title || dateLabel(r.finished_at||r.created_at) }} · {{ reportKey(r).slice(0,8) }}</option><option v-if="!selectedReport" :value="runId">{{ runId.slice(0,12) }}</option></select></div>
        <div class="report-counts" aria-label="本批次判定统计"><StatusBadge v-for="label in ['通过','不通过','待确认','不适用']" :key="label" :tone="verdictClass(label)">{{ label }} {{ countByVerdict[label]||0 }}</StatusBadge></div>
        <span class="report-object-count">{{ allCards.length }} 个对象</span>
        <div class="report-tools">
          <button class="button small issue-toggle" type="button" :aria-expanded="issuesExpanded" aria-controls="review-panel-issues" @click="issuesExpanded=!issuesExpanded"><AppIcon name="panel-left" />{{ issuesExpanded?'收起清单':'问题清单' }}</button>
          <button class="button small history-toggle" type="button" :aria-expanded="historyOpen" aria-controls="report-history" @click="historyOpen=!historyOpen"><AppIcon name="layers" />历史与导出<AppIcon name="chevron-down" :class="{rotated:historyOpen}" /></button>
        </div>
      </div>
      <section v-if="historyOpen" id="report-history" class="report-history" aria-label="历史比较与导出">
        <div class="history-content">
          <div class="actions"><label for="compare-report">比较历史批次</label><select id="compare-report" v-model="compareId" @change="compare"><option value="">选择已发布报告</option><option v-for="r in reports.filter(r=>reportKey(r)!==runId)" :key="reportKey(r)" :value="reportKey(r)">{{ dateLabel(r.finished_at||r.created_at) }} · {{ reportKey(r).slice(0,8) }}</option></select><a class="button small primary" :href="withBasePath(`/api/reports/${encodeURIComponent(runId)}/download`)" download><AppIcon name="download" />下载完整报告包</a><a class="button small" :href="assetUrl(assetBase,'report_data.json')" download>报告数据</a><a class="button small" :href="assetUrl(assetBase,'expert_review.html')" download>HTML</a></div>
          <p class="inline-note">比较同一规则与对象的工程结论；“未纳入”表示该次报告未覆盖此对象。HTML 的图纸和附件可在本机工作台中查看。</p>
          <p v-if="comparing" class="small-copy muted" role="status">正在读取历史报告…</p><div v-if="compareError" class="notice error" role="alert">{{ compareError }}</div>
          <div v-if="comparison" class="data-table-wrap"><table v-if="changes.length" class="data-table"><thead><tr><th>检查项</th><th>对象</th><th>历史结论</th><th>本次结论</th></tr></thead><tbody><tr v-for="change in changes" :key="change.key"><td>{{ change.rule_id }}</td><td>{{ change.object }}</td><td>{{ change.before }}</td><td>{{ change.after }}</td></tr></tbody></table><p v-else class="small-copy">两次报告中，同一规则与对象的工程结论没有变化。</p></div>
        </div>
      </section>
      <nav class="inline-tabs report-mobile-tabs" role="tablist" aria-label="工作区视图" @keydown="onMobileTabKey">
        <button v-for="item in mobileViews" :id="'review-tab-'+item.id" :key="item.id" role="tab" type="button" :aria-selected="mobileTab===item.id" :aria-controls="'review-panel-'+item.id" :tabindex="mobileTab===item.id?0:-1" :class="{active:mobileTab===item.id}" @click="switchMobileView(item.id)">{{ item.label }}</button>
      </nav>
      <div class="review-columns" :data-mobile-tab="mobileTab" :data-issues-expanded="issuesExpanded">
        <aside id="review-panel-issues" class="issues-pane" :role="smallScreen?'tabpanel':undefined" :aria-labelledby="smallScreen?'review-tab-issues':'issues-title'">
          <div class="pane-heading"><h2 id="issues-title">问题清单</h2><span class="pane-meta">{{ filtered.length }} / {{ allCards.length }}</span></div>
          <div class="issue-filters">
            <label class="sr-only" for="object-search">搜索对象或问题</label><div class="search-field"><AppIcon name="search" /><input id="object-search" v-model="search" class="search-input" placeholder="位号、规则或问题" /></div>
            <label class="sr-only" for="rule-filter">筛选检查项</label><select id="rule-filter" v-model="ruleFilter"><option value="">全部检查项</option><option v-for="rule in ruleOptions" :key="rule.rule_id" :value="rule.rule_id">{{ rule.rule_id }} · {{ rule.title }}</option></select>
            <div class="filter-pair"><label class="sr-only" for="verdict-filter">筛选结论</label><select id="verdict-filter" v-model="verdictFilter"><option value="">全部结论</option><option v-for="label in ['通过','不通过','待确认','不适用']" :key="label">{{ label }}</option></select><label class="sr-only" for="review-filter">筛选复核状态</label><select id="review-filter" v-model="reviewFilter"><option value="">全部复核状态</option><option v-for="(label,key) in feedbackLabels" :key="key" :value="key">{{ label }}</option></select></div>
          </div>
          <div class="issue-list">
            <button v-for="card in filtered" :key="card.key" class="issue-row" :class="{selected:card.key===selectedKey}" :aria-pressed="card.key===selectedKey" @click="select(card)"><div class="issue-row-top"><strong>{{ card.object }}</strong><StatusBadge :tone="verdictClass(card.verdict_zh)">{{ verdictLabel(card.verdict_zh) }}</StatusBadge></div><span class="issue-rule">{{ card.rule_id }} · {{ card.rule_title }}</span><p>{{ card.headline_zh||card.conclusion||card.summary||'打开查看评审依据' }}</p><small>{{ feedbackLabels[feedback[card.key]?.mark]||'未复核' }}</small></button>
            <p v-if="!filtered.length" class="no-filter-results" role="status">没有匹配的对象，请调整筛选条件。</p>
          </div>
        </aside>
        <section id="review-panel-drawing" class="drawing-pane" :role="smallScreen?'tabpanel':undefined" :aria-labelledby="smallScreen?'review-tab-drawing':'drawing-title'">
          <div class="pane-heading"><h2 id="drawing-title"><AppIcon name="circuit" />原理图</h2><a v-if="pdfUrl" :href="pdfUrl" target="_blank" rel="noopener">独立窗口<AppIcon name="external" /></a></div>
          <div v-if="pages.length" class="drawing-controls"><label for="schematic-page">关联图幅</label><select id="schematic-page" v-model="pageIndex"><option v-for="(page,index) in pages" :key="index" :value="index">第 {{ page.page }} 页{{ page.title?' · '+page.title:'' }}</option></select><span>{{ selected?.object }}</span></div>
          <iframe v-if="pdfUrl" :key="pdfUrl" :src="pdfUrl" :title="`${selected?.object} 的关联原理图第 ${selectedPage?.page} 页`"></iframe>
          <EmptyState v-else title="当前对象尚未关联图幅" description="这里展示报告中已有的可靠定位。可以先在分析中核对材料与证据。" />
        </section>
        <section id="review-panel-detail" class="analysis-pane" :role="smallScreen?'tabpanel':undefined" :aria-labelledby="smallScreen?'review-tab-detail':'analysis-title'">
          <div class="pane-heading"><h2 id="analysis-title">{{ selected?.object||'对象详情' }} <span v-if="selected">{{ selected.rule_id }}</span></h2><StatusBadge v-if="selected" :tone="verdictClass(selected.verdict_zh)">{{ verdictLabel(selected.verdict_zh) }}</StatusBadge></div>
          <nav v-if="selected" class="detail-nav" aria-label="当前对象详情定位"><button v-for="item in detailLinks" :key="item.id" type="button" :disabled="item.id==='primary'&&!selected.view?.primary" @click="jumpTo(item.id)">{{ item.label }}</button></nav>
          <div v-if="selected" ref="analysisContent" class="analysis-content">
            <ReviewContractDetail v-if="selected.view" :key="selected.key" :view="selected.view" :asset-base="assetBase" :source-assets="report.source_assets || {}" @open-path="openPath" />
            <div v-else class="notice warning">这份报告尚未生成统一分析详情。请在运行页重新生成完整报告。</div>
            <form class="feedback-panel" aria-label="人工复核" @submit.prevent="saveFeedback"><h3>人工复核</h3><p>复核意见独立记录，保留原始工程判定。</p><fieldset class="feedback-options"><legend class="sr-only">复核状态</legend><label v-for="(label,key) in feedbackLabels" :key="key"><input type="radio" name="review-mark" v-model="mark" :value="key" />{{ label }}</label></fieldset><label for="feedback-note" class="field-caption">复核意见</label><textarea id="feedback-note" v-model="note" rows="3" placeholder="核查依据、疑问或需补充的材料" maxlength="10000"></textarea><div class="actions"><button class="button small primary" :disabled="feedbackBusy">{{ feedbackBusy?'保存中…':'保存复核意见' }}</button><span v-if="feedbackNotice" class="save-notice" role="status">{{ feedbackNotice }}</span></div></form>
            <details class="question-panel"><summary>围绕此对象追问</summary><p>回答基于本次报告与证据，追问不会修改原始判定。</p><div v-if="!profiles.length" class="notice warning">请先在<router-link to="/models">模型设置</router-link>中配置服务。</div><form @submit.prevent="ask"><div class="field"><label for="question-profile">使用模型</label><select id="question-profile" v-model="profileId"><option v-for="p in profiles" :key="p.id" :value="p.id">{{ p.name||p.model }}</option></select></div><label for="object-question" class="field-caption">对象问题</label><textarea id="object-question" v-model="question" rows="3" placeholder="这个结论依赖哪些材料？还有哪些条件需要确认？" required></textarea><div class="actions"><button class="button small" :disabled="asking||historyLoading||!profileId||!question.trim()">{{ asking?'正在分析…':'发送问题' }}</button><button class="button small ghost" type="button" @click="rerun">设置此对象复核<AppIcon name="arrow-right" /></button></div></form><div v-if="questionError" class="notice error" role="alert">{{ questionError }}</div><p v-if="historyLoading" class="small-copy muted" role="status">正在加载此对象的已保存问答…</p><div v-if="historyError" class="notice error" role="alert">问答记录加载失败：{{ historyError }}<button class="button small" type="button" :disabled="asking||historyLoading" @click="loadQuestionHistory">重试加载</button></div><p v-if="!historyLoading&&!historyError&&!questionHistory.length" class="small-copy muted">此对象还没有已保存的问答。</p><article v-for="entry in questionHistory.slice().reverse()" :key="entry.id" class="question-answer" :data-question-id="entry.id"><div class="question-record-meta"><span>{{ dateLabel(entry.created_at) }}</span><span v-if="entry.metadata?.model">{{ entry.metadata.model }}</span></div><h4>{{ entry.question }}</h4><p>{{ entry.answer||entry.text }}</p><ul v-if="entry.citations?.length"><li v-for="(citation,index) in entry.citations" :key="index">{{ typeof citation==='string'?citation:[citation.source_id||citation.label||citation.source,citation.quote].filter(Boolean).join(' · ')||JSON.stringify(citation) }}</li></ul></article></details>
          </div>
          <EmptyState v-else title="选择一个对象" description="从问题清单中选择对象，查看它的分析与复核记录。" />
        </section>
      </div>
    </template>
    <PathViewer v-if="viewer" :pages="viewer.pages" :lines="viewer.lines" :who="viewer.who" @close="viewer=null" />
  </section>
</template>
<style scoped>
.report-workspace{display:flex;flex-direction:column;gap:12px;height:calc(100dvh - 92px);min-height:0;color:var(--wb-ink,#172b43)}
.report-heading,.report-toolbar,.report-history,.report-mobile-tabs{flex-shrink:0}
.report-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:36px}
.report-title{display:flex;align-items:baseline;gap:12px;min-width:0}.report-title h1{font-size:20px;line-height:1.4;letter-spacing:-.3px;margin:0;white-space:nowrap}.report-title>span{font-size:13px;color:var(--wb-muted,#526278);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.report-heading .actions{flex-shrink:0}
.report-toolbar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 12px;border:1px solid var(--wb-border,#dce4ed);border-radius:8px;background:var(--wb-surface,#fff)}
.report-selector{display:flex;align-items:center;gap:8px;min-width:0}.report-selector label,.history-content label{font-size:12px;font-weight:600;color:var(--wb-muted,#526278);white-space:nowrap}
.report-selector select,.history-content select{height:34px;min-width:0;max-width:280px;border:1px solid var(--wb-border,#dce4ed);border-radius:6px;padding:5px 8px;background:#fff;color:var(--wb-ink,#172b43);font-size:12px}
.report-counts{display:flex;gap:6px;flex-wrap:wrap}.report-object-count{font-size:12px;color:var(--wb-muted,#526278);white-space:nowrap}.report-tools{display:flex;gap:6px;margin-left:auto}.history-toggle .rotated{transform:rotate(180deg)}.issue-toggle,.report-mobile-history{display:none}
.report-history{max-height:32vh;overflow:auto;border:1px solid var(--wb-border,#dce4ed);border-radius:8px;background:#fff}.history-content{padding:12px}.history-content .inline-note{margin:10px 0 0}.history-content .data-table-wrap{margin-top:10px}
.review-columns{display:grid;grid-template-columns:228px minmax(300px,1fr) minmax(390px,450px);gap:12px;flex:1;min-height:0}
.issues-pane,.drawing-pane,.analysis-pane{display:flex;flex-direction:column;min-width:0;min-height:0;overflow:hidden;border:1px solid var(--wb-border,#dce4ed);border-radius:8px;background:#fff}
.pane-heading{display:flex;align-items:center;justify-content:space-between;gap:8px;min-height:45px;padding:10px 12px;border-bottom:1px solid var(--wb-border,#dce4ed);flex-shrink:0}.pane-heading h2{display:flex;align-items:center;gap:8px;margin:0;font-size:14px;line-height:1.5}.pane-heading h2>span{font-size:12px;font-family:ui-monospace,monospace;color:var(--wb-muted,#526278);font-weight:400}.pane-meta{font-size:12px;color:var(--wb-muted,#526278);font-variant-numeric:tabular-nums}.pane-heading>a{display:inline-flex;align-items:center;gap:4px;min-height:24px;font-size:12px;color:var(--wb-teal,#0f766e);text-decoration:none}
.issue-filters{position:relative;display:grid;gap:8px;padding:10px;border-bottom:1px solid var(--wb-border,#dce4ed);flex-shrink:0}.search-field{position:relative}.search-field>svg{position:absolute;left:9px;top:10px;color:var(--wb-muted,#526278);pointer-events:none}.issue-filters input,.issue-filters select{width:100%;min-width:0;height:36px;border:1px solid var(--wb-border,#dce4ed);border-radius:6px;background:#fff;color:var(--wb-ink,#172b43);padding:6px 8px;font-size:12px}.issue-filters .search-input{padding-left:32px}.filter-pair{display:grid;grid-template-columns:1fr 1fr;gap:6px}
.issue-list{position:relative;overflow:auto;flex:1;min-height:0;scroll-padding-block:8px}.issue-row{display:block;width:100%;border:0;border-bottom:1px solid var(--wb-border,#dce4ed);background:#fff;padding:12px;text-align:left;position:relative}.issue-row:hover{background:var(--wb-soft,#edf2f7)}.issue-row.selected{background:#edf7f5;box-shadow:inset 3px 0 var(--wb-teal,#0f766e)}.issue-row-top{display:flex;align-items:center;justify-content:space-between;gap:8px}.issue-row-top strong{font-family:ui-monospace,monospace;font-size:15px;color:var(--wb-ink,#172b43)}.issue-rule{display:block;margin-top:8px;color:var(--wb-muted,#526278);font-size:12px;line-height:1.5;overflow-wrap:anywhere}.issue-row p{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;margin:7px 0;font-size:12px;line-height:1.65;color:var(--wb-ink,#172b43)}.issue-row small{display:block;font-size:12px;color:var(--wb-muted,#526278)}.no-filter-results{padding:16px;font-size:13px;color:var(--wb-muted,#526278)}
.drawing-controls{display:flex;align-items:center;gap:8px;padding:8px 10px;background:#f8fafc;border-bottom:1px solid var(--wb-border,#dce4ed);font-size:12px;color:var(--wb-muted,#526278);flex-shrink:0}.drawing-controls select{min-width:0;max-width:65%;height:30px;padding:4px 6px;border:1px solid var(--wb-border,#dce4ed);border-radius:5px;background:#fff;font-size:12px;color:var(--wb-ink,#172b43)}.drawing-controls>span{margin-left:auto;font-family:ui-monospace,monospace}.drawing-pane iframe{display:block;flex:1;width:100%;height:100%;min-height:0;border:0;background:var(--wb-soft,#edf2f7)}.drawing-pane .empty-state{padding:24px 14px}.drawing-pane :deep(.empty-state h2){font-size:16px}.drawing-pane :deep(.empty-state p){font-size:13px}
.detail-nav{display:flex;flex-shrink:0;gap:2px;padding:5px 8px;background:#f8fafc;border-bottom:1px solid var(--wb-border,#dce4ed);overflow:auto;scrollbar-width:thin}.detail-nav button{flex:1 0 auto;min-height:32px;padding:5px 7px;border:0;border-radius:5px;background:transparent;color:var(--wb-muted,#526278);font-size:12px;white-space:nowrap}.detail-nav button:hover{color:var(--wb-teal,#0f766e);background:#e3f1ee}.detail-nav button:disabled{opacity:.5}
.analysis-content{position:relative;flex:1;min-height:0;padding:12px;overflow:auto;overscroll-behavior:contain;scroll-padding-block:12px;background:#f8fafc}.analysis-content :deep([data-review-module]),.feedback-panel,.question-panel{scroll-margin-top:12px}.analysis-content :deep([tabindex='-1']:focus-visible){outline:2px solid var(--wb-teal,#0f766e);outline-offset:2px}.analysis-content :deep(.review-detail .rv-premise .premise-reason){grid-template-columns:1fr}
.feedback-panel,.question-panel{position:relative;padding:14px;border:1px solid var(--wb-border,#dce4ed);border-radius:8px;background:#fff;margin:0 0 10px}.feedback-panel h3{font-size:14px;margin:0}.feedback-panel>p,.question-panel>p{font-size:13px;line-height:1.65;color:var(--wb-muted,#526278);margin:8px 0 12px}.feedback-options{position:relative;display:flex;flex-wrap:wrap;gap:6px 12px;border:0;padding:0;margin:0 0 10px}.feedback-options label{display:flex;align-items:center;gap:5px;min-height:32px;font-size:13px;color:var(--wb-ink,#172b43)}.feedback-options input{accent-color:var(--wb-teal,#0f766e)}.field-caption{display:block;font-size:12px;font-weight:600;color:var(--wb-muted,#526278);margin-bottom:6px}.feedback-panel textarea,.question-panel textarea{width:100%;min-height:90px;border:1px solid #b9c7d7;border-radius:6px;padding:10px;font-size:14px;line-height:1.65;resize:vertical;margin-bottom:10px;color:var(--wb-ink,#172b43);background:white}.question-panel>summary{cursor:pointer;font-weight:650;font-size:14px;color:var(--wb-ink,#172b43)}.question-panel .field>label{font-size:12px}.question-panel .field select{font-size:13px;min-height:36px;padding:7px 10px}.question-panel .notice{margin-top:12px;font-size:13px}.question-answer{border-top:1px solid var(--wb-border,#dce4ed);margin-top:14px;padding-top:12px;font-size:14px;line-height:1.7;color:var(--wb-ink,#172b43)}.question-record-meta{display:flex;flex-wrap:wrap;gap:6px 12px;color:var(--wb-muted,#526278);font-size:12px;margin-bottom:8px}.question-answer h4{font-size:14px;line-height:1.65;margin:0 0 8px}.question-answer p{white-space:pre-wrap}.question-answer ul{padding-left:18px;font-size:13px}.save-notice{font-size:12px;color:var(--wb-success,#14734f)}.report-mobile-tabs{display:none}
@media(min-width:761px) and (max-width:1180px){.review-columns{grid-template-columns:minmax(0,1fr) minmax(360px,1fr)}.issues-pane{display:none}.issue-toggle{display:inline-flex}.review-columns[data-issues-expanded='true']{grid-template-columns:240px minmax(0,1fr)}.review-columns[data-issues-expanded='true'] .issues-pane{display:flex}.review-columns[data-issues-expanded='true'] .drawing-pane{display:none}.report-object-count{display:none}.report-toolbar{gap:8px}.report-selector select{max-width:250px}}
@media(max-width:760px){.report-workspace{height:auto;min-height:calc(100dvh - 150px);gap:10px}.report-heading{align-items:flex-start;flex-wrap:wrap}.report-title{gap:8px}.report-title h1{font-size:18px}.report-title>span{display:none}.report-heading .actions{gap:6px}.report-heading .button{min-height:40px;padding:7px 10px;font-size:12px}.report-toolbar{padding:10px;gap:10px}.report-selector{width:100%}.report-selector select{flex:1;max-width:none;height:38px}.report-counts{gap:5px}.report-object-count{display:none}.report-tools{display:none}.report-mobile-history{display:inline-flex;min-width:40px;justify-content:center}.history-toggle{min-height:38px}.report-history{max-height:none}.history-content .actions{align-items:stretch;flex-direction:column}.history-content select{max-width:none}.report-mobile-tabs{position:sticky;top:60px;z-index:5;display:flex;gap:4px;margin:0;padding:5px;background:var(--wb-bg,#f3f6fa);border:1px solid var(--wb-border,#dce4ed);border-radius:8px}.report-mobile-tabs button{flex:1;min-height:44px;font-size:13px}.review-columns{display:block;flex:none;min-height:0}.review-columns[data-mobile-tab='issues'] .drawing-pane,.review-columns[data-mobile-tab='issues'] .analysis-pane,.review-columns[data-mobile-tab='drawing'] .issues-pane,.review-columns[data-mobile-tab='drawing'] .analysis-pane,.review-columns[data-mobile-tab='detail'] .issues-pane,.review-columns[data-mobile-tab='detail'] .drawing-pane{display:none}.issues-pane,.drawing-pane,.analysis-pane{min-height:0}.analysis-pane{overflow:visible}.issue-list{max-height:none;min-height:160px;overflow:visible}.issue-filters input,.issue-filters select{height:40px;font-size:14px}.drawing-pane iframe{height:65dvh;min-height:380px;flex:none}.analysis-content{max-height:none;overflow:visible;padding:10px}.analysis-content :deep([data-review-module]),.feedback-panel,.question-panel{scroll-margin-top:184px}.detail-nav{position:sticky;top:116px;z-index:4;flex-wrap:wrap;padding:6px;background:#f8fafc}.detail-nav button{min-height:44px}.feedback-panel textarea,.question-panel textarea{font-size:16px}.feedback-options label{min-height:40px}.pane-heading{min-height:46px}.pane-heading>a{min-height:32px}}
</style>
