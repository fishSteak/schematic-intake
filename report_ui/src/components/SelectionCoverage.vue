<script setup>
import {computed,onMounted,ref} from 'vue';
const props=defineProps({ruleId:{type:String,default:''}});
const coverage=ref(null);
onMounted(async()=>{try{const r=await fetch('/delivery_coverage.json');if(r.ok)coverage.value=await r.json();}catch{}});
const row=computed(()=>coverage.value?.rules.find(r=>r.rule_id===props.ruleId));
const distribution=r=>r.no_object?'已核查无适用对象':[['是','通过'],['否','不通过'],['待确认','待确认'],['不适用','不适用']].map(([key,label])=>`${label} ${r.counts?.[key]||0}`).join(' / ');
const remaining=r=>(r.objects||[]).filter(o=>o.status!=='validated'||['待确认','INSUFFICIENT'].includes(o.verdict)).map(o=>`${o.object}：${o.status==='validated'?'待确认':'执行未完成'}`).join('；')||'—';
</script>
<template>
  <section v-if="coverage" class="batch-scope">
    <template v-if="ruleId">
      <p v-if="row">本轮代表性评审：候选 {{ row.candidate_count }}，选中 {{ row.selected_count }}，已校验 {{ row.counts?.validated || 0 }}。{{ row.no_object ? '已核查器件分类，无适用对象。' : '未选对象不计入本轮已审范围。' }}</p>
      <p v-else>既有批次结果 · 本项未在本轮选型评审中重审。</p>
    </template>
    <template v-else>
      <h3>器件选型 · 本轮代表性完整评审</h3>
      <p>覆盖 {{ coverage.rules.length }} 项；选中 {{ coverage.selected_pairs }} 组，已校验 {{ coverage.counts?.validated || 0 }} 组，执行未完成 {{ coverage.counts?.execution_pending || 0 }} 组。</p>
      <p>通过 {{ coverage.counts?.['是'] || 0 }}，不通过 {{ coverage.counts?.['否'] || 0 }}，电子待确认 {{ coverage.counts?.['待确认'] || 0 }}，对象不适用 {{ coverage.counts?.['不适用'] || 0 }}。</p>
      <p>本轮为代表性抽样，不代表全板全器件已审完；其他检查项保留既有批次结果。执行未完成与电子待确认分别统计。</p>
      <details><summary>查看覆盖、结论与对象清单</summary><table><thead><tr><th>检查项</th><th>候选</th><th>选中</th><th>已校验</th><th>结论分布</th><th>对象 / 状态</th><th>剩余对象（原因见详情）</th></tr></thead><tbody><tr v-for="r in coverage.rules" :key="r.rule_id"><td><router-link :to="{name: 'rule', params: {ruleId: r.rule_id}}">{{ r.rule_id }}</router-link><div>{{ r.title }}</div></td><td>{{ r.candidate_count }}</td><td>{{ r.selected_count }}</td><td>{{ r.counts?.validated || 0 }}</td><td>{{ distribution(r) }}</td><td>{{ r.no_object ? '无适用对象' : r.selected_objects.join('、') }}</td><td>{{ remaining(r) }}</td></tr></tbody></table></details>
    </template>
  </section>
</template>
<style scoped>
.batch-scope{padding:14px 18px;margin:12px 0;border:1px solid #dce5e9;border-radius:10px;background:#f7fbfc;color:#496574;font-size:13px;line-height:1.7;overflow-x:auto}.batch-scope h3{margin:0 0 6px;font-size:15px}.batch-scope p{margin:4px 0}.batch-scope summary{cursor:pointer;margin-top:8px}.batch-scope table{border-collapse:collapse;width:100%;margin-top:8px;text-align:left}.batch-scope td,.batch-scope th{padding:6px 10px;border-bottom:1px solid #e2eaee}
</style>
