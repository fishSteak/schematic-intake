<script setup>
import modules from "../review-modules.json";
import { assetUrl } from "../api";
const titles=Object.fromEntries(modules.map(m=>[m.id,m.title]));
const props=defineProps({ view: { type: Object, required: true }, assetBase: { type: String, default: '' } });
const asset=path=>assetUrl(props.assetBase,path);
const pdfLink=p=>asset('pdfjs/web/viewer.html')+'?file='+encodeURIComponent(asset(p.pdf))+'#page='+p.page;
const moduleOrder=id=>(props.view.module_order||modules.map(m=>m.id)).indexOf(id);
const diagramLabel=()=>['cross_case_bound','incomplete_proof'].includes(props.view.primary?.kind)?'分析关系图':'工况分析图';
const pendingAssumption=step=>(step.assumption_refs||[]).some(id=>(props.view.key_assumptions||[]).some(p=>p.id===id&&p.status==='unresolved'));
const assumptionStatus={supported:'已支持',model_assumption:'模型假设',unresolved:'待证',recorded:'原记录'};
const emit = defineEmits(["open-path"]);
</script>
<template>
  <div class="review-detail review-body">
  <section class="rv-summary" data-review-module="summary" :style="{order:moduleOrder('summary')}"><h3 class="rv-section-title">{{ titles.summary }}</h3><p>{{ view.headline }}</p></section>
  <section class="contract-card rv-premise" data-review-module="premises" :style="{order:moduleOrder('premises')}">
    <h3 class="rv-section-title">{{ titles.premises }}</h3>
    <div class="inputs rv-materials-only">
      <div><h4>材料参数</h4><p v-if="!view.materials.length">原记录未单列参数</p><p v-for="m in view.materials" :key="m.label">{{ m.label }}：{{ m.text }}{{ m.kind === 'model_parameter' ? '（模型参数）' : '' }}</p></div>
    </div>
    <div class="premise-reason"><div class="premise-label">分析思路</div><p v-if="!view.analysis_steps.length">当前记录缺少可追溯的分析内容，见判定证据中的资料状态。</p><ol v-else class="rv-reason-steps"><li v-for="(item,i) in view.analysis_steps" :key="i"><span>{{ item.text }} <b v-if="pendingAssumption(item)" class="rv-assumption-pending">待证条件</b></span></li></ol></div>
  </section>
  <div class="rv1">
    <section class="rv-panel rv-decision" data-review-module="assessment" :style="{order:moduleOrder('assessment')}">
      <h3 class="rv-section-title">{{ view.assessment_title || '判定要点' }}</h3>
      <div v-if="view.assessment_rows?.length" class="rv-metrics"><div v-for="r in view.assessment_rows" :key="r.label"><small>{{ r.label }}</small><strong>{{ r.text }}</strong></div></div>
      <p v-if="view.assessment_text">{{ view.assessment_text }}</p>
      <p v-if="view.criterion_text && view.criterion_text !== view.assessment_text">{{ view.criterion_text }}</p>
      <div v-if="view.assessment_checks?.length" class="rv-audit-list"><article v-for="c in view.assessment_checks" :key="c.name"><b>{{ c.name }}</b><p>{{ c.detail }}</p></article></div>
    </section>
    <section v-if="view.primary" class="rv-panel rv-primary" data-review-module="primary" :style="{order:moduleOrder('primary')}">
      <h3 class="rv-kicker rv-section-title">{{ view.primary_module_title || titles.primary }}</h3>
      <div class="rv-heading"><h4 v-if="!view.primary.route_summary" class="rv-case-title">{{ view.primary.title }}</h4><span class="rv-tag">{{ view.primary_label }}</span><span v-if="view.primary.comparison" class="rv-tag rv-tag-neutral">{{ view.primary.comparison }}</span></div>
      <p class="rv-route">{{ view.primary.route_summary }}</p>
      <div v-if="view.main_analysis" class="rv-basis-scope"><b>分析范围</b><p>{{ view.main_analysis.scope.included }}</p><small>范围外：{{ view.main_analysis.scope.excluded }}</small></div>
      <div v-if="view.primary.conditions.length" class="rv-condition"><b>成立条件</b><span>{{ view.primary.conditions.join('；') }}</span></div>
      <button v-if="view.primary.figure" class="figure" @click="emit('open-path', view.primary)"><img :src="asset(view.primary.figure)" :alt="diagramLabel()" /><span>打开{{ diagramLabel() }}</span></button>
      <p v-else-if="view.primary.route_expected">通路图尚未通过连线校验</p>
      <h4 v-if="view.primary.calculations.length" class="rv-subtitle rv-calc-title">计算过程</h4>
      <ol v-if="view.primary.calculations.length" class="rv-calcs"><li v-for="c in view.primary.calculations" :key="c.id">{{ c.expression }}</li></ol>
    </section>
    <section v-if="view.comparisons.length" class="rv-panel" data-review-module="comparisons" :style="{order:moduleOrder('comparisons')}">
      <h3 class="rv-kicker rv-section-title">{{ titles.comparisons }}</h3>
      <div class="rv-comparisons"><article v-for="s in view.comparisons" :key="s.id"><div class="rv-heading"><b>{{ s.title }}</b><span class="rv-tag rv-tag-neutral">{{ s.kind === 'model_boundary' ? '判定边界' : s.comparison }}</span><strong class="rv-value">{{ s.value_text }}</strong></div><p>{{ s.reason }}</p></article></div>
    </section>
    <details class="rv-panel rv-audit" data-review-module="audit" :style="{order:moduleOrder('audit')}">
      <summary>判定证据与未解边界<span>适用依据 · 覆盖范围 · 待确认事项</span></summary>
      <div class="rv-audit-body">
        <section><h4>01 · 判定依据</h4><p>{{ view.explanation }}</p><div class="rv-note"><b>适用条件</b><p>{{ view.basis.applicability || '原记录未单列适用条件' }}</p></div></section>
        <section><h4>02 · 覆盖范围与边界</h4><p>{{ view.basis.coverage_argument || '原记录未单列覆盖范围，详见证据索引' }}</p></section>
        <section v-if="view.main_analysis"><h4>正式判定依据的证明核查</h4><div class="rv-audit-list"><article v-for="(c,i) in view.main_analysis.proof_checks" :key="i"><b>{{ {supported:"已核查",unresolved:"待完成",refuted:"不成立"}[c.status] }} · {{ c.claim }}</b><p>{{ c.reason }}</p><small>{{ c.evidence_refs.join("；") }}</small></article></div></section>
        <section v-if="view.gaps.length"><h4>03 · 未闭合事项</h4><div class="rv-audit-list"><article v-for="g in view.gaps" :key="g.id"><b>{{ g.issue }}</b><p>{{ g.reason }}</p></article></div></section>
        <section v-if="view.audit_scenarios.length"><h4>补充工况说明</h4><div class="rv-audit-list"><article v-for="s in view.audit_scenarios" :key="s.id"><div class="rv-heading"><b>{{ s.title }}</b><span class="rv-tag rv-tag-neutral">{{ s.role === 'excluded' ? '不纳入稳态排名' : '排名未闭合' }}</span></div><p>{{ s.reason }}</p></article></div></section>
        <section v-if="view.key_assumptions?.length"><h4>关键假设与来源（追溯）</h4><div class="rv-audit-list"><article v-for="a in view.key_assumptions" :key="a.id"><b>{{ assumptionStatus[a.status] || a.status }}</b><p>{{ a.text }}</p><small>{{ a.id }} · {{ a.source.field }}<template v-if="a.evidence_refs?.length"> · 证据：{{ a.evidence_refs.join('、') }}</template></small></article></div></section>
        <section v-if="view.notes?.length"><h4>展示资料状态</h4><p v-for="n in view.notes" :key="n">{{ n }}</p></section>
        <section v-if="view.evidence?.length"><h4>证据索引</h4><article v-for="(e,i) in view.evidence" :key="i"><b>{{ e.display_label }}</b><p>{{ e.display_locator }}</p><p>{{ e.display_text }}</p></article></section>
      </div>
    </details>
  </div>
  <section class="rv-extract" data-review-module="datasheets" :style="{order:moduleOrder('datasheets')}"><h3 class="rv-section-title">{{ titles.datasheets }}</h3><p>资料摘录仅作来源参考，评审采用值见材料与分析思路。</p><p v-for="r in view.resources?.extract_rows || []" :key="r.key">{{ r.label || r.key }}：{{ r.value }} {{ r.unit }}</p><a v-if="view.resources?.datasheet_pdf" :href="asset(view.resources.datasheet_pdf)" target="_blank" rel="noopener">打开规格书 PDF</a><p v-else>未关联规格书 PDF</p></section>
  <section class="rv-sheets" data-review-module="schematics" :style="{order:moduleOrder('schematics')}"><h3 class="rv-section-title">{{ titles.schematics }}</h3><p v-if="!view.resources?.pages?.length">未关联具体图幅</p><div v-for="p in view.resources?.pages || []" :key="p.page"><a :href="pdfLink(p)" target="_blank" rel="noopener">第 {{ p.page }} 页 · PDF 缩放 / 搜索 / 划选</a><img v-if="p.img" :src="asset(p.img)" :alt="`原理图第 ${p.page} 页`" style="width:100%" /></div></section>
  </div>
</template>
<style src="../review-detail.css"></style>
