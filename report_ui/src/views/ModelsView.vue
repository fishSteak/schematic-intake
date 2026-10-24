<script setup>
import { computed, onMounted, reactive, ref } from 'vue';
import { api, dateLabel } from '../api';
import { loadWorkspace } from '../workspace';
import StatusBadge from '../components/StatusBadge.vue';
const profiles=ref([]), loading=ref(true), busy=ref(''), error=ref(''), success=ref(''), result=ref(null), selected=ref(''), defaultId=ref(''), keyConfigured=ref(false), formElement=ref(null);
const defaults=()=>({id:'',name:'',provider:'openai-compatible',wire_api:'chat',base_url:'',model:'',api_key_env:'',api_key:'',timeout:120,max_tokens:8192,max_turns:12,max_requests:40,max_retries:2,max_context_tokens:null,stream:false,structured_output:'json_schema',strict_schema:false,supports_tools:true,supports_images:true,token_parameter:'max_tokens',default:false});
const form=reactive(defaults());
const wireLabel=value=>({chat:'Chat Completions API',responses:'Responses API'}[value||'chat']||value);
const capabilityDefinitions=[
 {key:'tools',label:'工具调用'},
 {key:'structured_output',label:'结构化输出'},
 {key:'vision',label:'图像理解'},
 {key:'streaming',label:'流式响应'},
 {key:'context_capacity',label:'上下文预算'},
];
const capabilityRows=computed(()=>{
 const data=result.value||{}, tested=data.capabilities||data.tested||{};
 return capabilityDefinitions.map(({key,label})=>{
  const value=tested[key]??(key==='context_capacity'?data.context?.status:key==='vision'?data.vision:undefined);
  const states={disabled:['已关闭','neutral'],configured_not_tested:['已配置，未测试','warning'],not_tested:['未测试','neutral'],tested:['测试通过','success'],configured:['已配置容量','info'],not_configured:['未配置容量','neutral'],failed:['测试未通过','danger']};
  const [status,tone]=value===true?['测试通过','success']:value===false?['测试未通过','danger']:states[value]||['未测试','neutral'];
  let detail='';
  if(key==='context_capacity'){
   const context=data.context||{},parts=[];
   if(context.max_context_tokens!=null)parts.push(`配置容量 ${Number(context.max_context_tokens).toLocaleString('zh-CN')} Token`);
   if(context.estimated_input_tokens!=null)parts.push(`本次测试估算输入 ${Number(context.estimated_input_tokens).toLocaleString('zh-CN')} Token`);
   if(context.reserved_output_tokens!=null)parts.push(`预留输出 ${Number(context.reserved_output_tokens).toLocaleString('zh-CN')} Token`);
   detail=(parts.length?parts.join('；')+'。':value==='not_configured'?'未启用本机容量限制。':'')+'容量检查采用保守估算，未探测服务允许的最大窗口。';
  }else if(key==='vision'&&value===true)detail='已向模型发送测试图片并核对识别结果；不代表工程图纸评审质量。';
  else if(value===undefined||value==='not_tested')detail='这次测试未提供此项结果。';
  return {key,label,status,tone,detail};
 });
});
function edit(profile){selected.value=profile.id;Object.assign(form,defaults(),Object.fromEntries(Object.keys(defaults()).filter(key=>key in profile).map(key=>[key,profile[key]])),{api_key:'',wire_api:profile.wire_api||'chat',default:profile.id===defaultId.value});keyConfigured.value=Boolean(profile.has_api_key||profile.api_key_configured||profile.has_key);result.value=profile.probe||null;error.value='';success.value='';}
function create(){selected.value='';Object.assign(form,defaults(),{default:!profiles.value.length});keyConfigured.value=false;result.value=null;error.value='';success.value='';}
async function load(){loading.value=true;error.value='';try{const data=await api.profiles();profiles.value=data.profiles||[];defaultId.value=data.default_profile_id||'';if(profiles.value.length)edit(profiles.value.find(p=>p.id===selected.value)||profiles.value.find(p=>p.id===defaultId.value)||profiles.value[0]);else create();}catch(e){error.value=e.message;}finally{loading.value=false;}}
async function save(test=false){if(!formElement.value?.reportValidity())return;busy.value='save';error.value='';success.value='';result.value=null;try{const payload=Object.fromEntries(Object.keys(defaults()).map(key=>[key,form[key]]));for(const key of ['timeout','max_tokens','max_turns','max_requests','max_retries'])payload[key]=Number(payload[key]);payload.max_context_tokens=form.max_context_tokens==null||form.max_context_tokens===''?null:Number(form.max_context_tokens);if(!payload.id)delete payload.id;if(!payload.api_key)delete payload.api_key;const response=await api.saveProfile(payload);const profile=response.profile||response;form.api_key='';selected.value=profile.id;await load();success.value='模型配置已保存。';await loadWorkspace();if(test){busy.value='test';result.value=null;result.value=await api.testProfile(selected.value);if(!result.value.ok)success.value='';}}catch(e){if(busy.value==='test'){result.value={...(e.response&&typeof e.response==='object'?e.response:{}),ok:false,error:e.message};success.value='';}else error.value=e.message;}finally{busy.value='';}}
onMounted(load);
</script>
<template>
  <section><div class="page-heading"><div><h1>模型</h1><p>评审直接调用 LLM API。配置服务地址与凭据，保存后测试连接与能力。</p></div><button class="button" :disabled="!!busy" @click="create"><span aria-hidden="true">＋</span>添加配置</button></div>
    <div v-if="error" class="notice error" role="alert">{{ error }}</div><div v-if="success" class="notice success" role="status">{{ success }}</div>
    <div v-if="loading" class="loading-state">正在加载模型配置</div>
    <div v-else class="model-layout"><aside class="model-sidebar"><div class="section-card flush"><div class="section-heading"><h2>已保存配置</h2><span>{{ profiles.length }}</span></div><div class="profile-list"><p v-if="!profiles.length" class="small-copy muted">还没有模型服务。填写右侧连接信息后保存。</p><button v-for="profile in profiles" :key="profile.id" class="profile-choice" :class="{selected:selected===profile.id}" :disabled="!!busy" @click="edit(profile)"><span><strong>{{ profile.name||profile.model }}</strong><small>{{ profile.model }}</small><small>{{ wireLabel(profile.wire_api) }}</small></span><StatusBadge v-if="profile.id===defaultId" tone="info">默认</StatusBadge></button></div></div><div class="model-note"><h3>数据去向</h3><p>使用远端模型时，参与分析的材料内容会通过所配置的接口发送。本机模型可填写本地 API 地址，图像、工具调用等能力以实测为准。</p></div></aside>
      <div><form ref="formElement" class="section-card" @submit.prevent="save(false)"><div class="section-heading"><h2>{{ selected?'编辑模型配置':'新增模型配置' }}</h2><StatusBadge :tone="selected?'neutral':'info'">{{ selected?'已保存':'新配置' }}</StatusBadge></div><div class="form-grid"><div class="field"><label for="model-name">配置名称</label><input id="model-name" v-model="form.name" placeholder="例如：评审主模型" maxlength="100" :disabled="!!busy" required /></div><div class="field"><label for="model-provider">服务类型</label><select id="model-provider" v-model="form.provider" :disabled="!!busy"><option value="openai-compatible">OpenAI 兼容接口</option></select></div><div class="field span-2"><label for="model-wire-api">API 协议</label><select id="model-wire-api" v-model="form.wire_api" :disabled="!!busy" required><option value="chat">Chat Completions API</option><option value="responses">Responses API</option></select><span class="help">按模型服务支持的接口选择。新配置默认 Chat Completions；更换协议后请保存并重新测试连接。请求均关闭服务端响应保存（store: false）。</span></div><div class="field span-2"><label for="model-base">服务地址（Base URL）</label><input id="model-base" v-model="form.base_url" type="url" placeholder="https://your-provider.example/v1" :disabled="!!busy" required /><span class="help">填写服务商的 API 根地址；本机模型也可使用 localhost 地址。</span></div><div class="field span-2"><label for="model-id">模型名称</label><input id="model-id" v-model="form.model" placeholder="填写服务支持的模型 ID" :disabled="!!busy" required /></div><div class="field span-2"><label for="model-key">API Key <span class="muted">（本机无鉴权服务可留空）</span></label><input id="model-key" v-model="form.api_key" type="password" autocomplete="new-password" :placeholder="keyConfigured?'已保存密钥，留空保留':'输入 API Key'" :disabled="!!busy" /><span class="help">密钥由本机后端保存；页面重新打开时不会回显。</span></div><div class="field span-2"><label for="model-key-env">或使用环境变量中的密钥</label><input id="model-key-env" v-model="form.api_key_env" placeholder="例如 SCHEMATIC_LLM_API_KEY" :disabled="!!busy" /><span class="help">填写环境变量名，后端从启动环境读取对应值。</span></div></div><details class="profile-advanced"><summary>调用与预算设置</summary><div class="form-grid"><div class="field"><label for="model-timeout">请求超时（秒）</label><input id="model-timeout" v-model="form.timeout" type="number" min="1" max="1200" required /></div><div class="field"><label for="model-tokens">单次最大输出 Token</label><input id="model-tokens" v-model="form.max_tokens" type="number" min="128" max="131072" required /></div><div class="field"><label for="model-turns">最大工具循环轮次</label><input id="model-turns" v-model="form.max_turns" type="number" min="1" max="100" required /></div><div class="field"><label for="model-requests">单个模型子任务最大请求数</label><input id="model-requests" v-model="form.max_requests" type="number" min="1" max="500" required /><span class="help">整次运行包含多个对象与模型子任务；此值限制每个子任务，不是整板总额度。</span></div><div class="field"><label for="model-retries">失败重试次数</label><input id="model-retries" v-model="form.max_retries" type="number" min="0" max="5" required /></div><div class="field"><label for="model-output">结构化输出方式</label><select id="model-output" v-model="form.structured_output"><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option><option value="none">模型文本返回</option></select></div><div class="field span-2"><label for="model-context">上下文窗口预算（Token，可选）</label><input id="model-context" v-model="form.max_context_tokens" type="number" min="1" step="1" placeholder="按服务商文档填写；留空不启用本机预算检查" /><span class="help">按保守估算检查输入与预留输出，超出预算时停止请求。此值由你配置，连接测试不会探测供应商允许的最大窗口。</span></div><div v-if="form.wire_api==='chat'" class="field span-2"><label for="model-token-parameter">输出预算参数</label><select id="model-token-parameter" v-model="form.token_parameter"><option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option></select><span class="help">按服务商文档选择兼容参数；能力开关应与实际模型支持保持一致。</span></div><label class="check-label"><input type="checkbox" v-model="form.supports_tools" />支持工具调用</label><label class="check-label"><input type="checkbox" v-model="form.supports_images" />支持图像输入</label><label class="check-label"><input type="checkbox" v-model="form.stream" />启用流式响应</label><label class="check-label"><input type="checkbox" v-model="form.strict_schema" />严格 JSON Schema</label></div></details><label class="check-label default-model"><input type="checkbox" v-model="form.default" :disabled="!!busy" />设为新运行的默认配置</label><div class="actions"><button class="button primary" :disabled="!!busy">{{ busy==='save'?'正在保存…':'保存配置' }}</button><button class="button" type="button" :disabled="!!busy||!form.base_url||!form.model||!form.name" @click="save(true)">{{ busy==='test'?'正在测试连接…':'保存并测试连接' }}</button></div><p class="inline-note">连接测试会向所选模型发起真实请求，用于验证接口和返回结果。更改配置后，请保存并重新测试。</p></form>
      <div class="section-card" role="status"><div class="section-heading"><h2>{{ result?'已保存配置的最近测试':'模型能力检查' }}</h2><StatusBadge :tone="result?(result.ok?'success':'danger'):'neutral'">{{ result?(result.ok?'测试通过':'测试未通过'):'未测试' }}</StatusBadge></div><p class="small-copy">{{ result?(result.message||result.detail||result.error||(result.ok?'模型服务已返回有效响应。':'请检查接口地址、凭据和模型名称。')):'尚未测试。保存配置并测试后，这里会显示各项能力的检查结果。' }}</p><dl class="metadata-list"><div v-if="result?.wire_api"><dt>测试接口</dt><dd>{{ wireLabel(result.wire_api) }}</dd></div><div v-if="result?.model"><dt>响应模型</dt><dd>{{ result.model }}</dd></div><div v-if="result?.latency_ms!=null"><dt>耗时</dt><dd>{{ result.latency_ms }} ms</dd></div><div v-if="result?.tested_at"><dt>测试时间</dt><dd>{{ dateLabel(result.tested_at) }}</dd></div></dl><div class="capability-list"><div v-for="capability in capabilityRows" :key="capability.key" class="capability-row" :data-capability="capability.key"><div><strong>{{ capability.label }}</strong><StatusBadge :tone="capability.tone">{{ capability.status }}</StatusBadge></div><p v-if="capability.detail">{{ capability.detail }}</p></div></div></div></div>
    </div>
  </section>
</template>
<style scoped>
.model-layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:16px;align-items:start}
.profile-list{padding:8px}
.profile-list>p{padding:8px;margin:0}
.profile-choice{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;width:100%;padding:10px 12px;margin:0 0 2px;border:1px solid transparent;border-radius:6px;background:transparent;text-align:left;color:var(--wb-ink)}
.profile-choice:hover{background:var(--wb-hover)}
.profile-choice.selected{background:var(--wb-accent-soft);border-color:var(--wb-accent-line)}
.profile-choice>span{min-width:0}
.profile-choice strong{font-size:13px;display:block;font-weight:600}
.profile-choice small{display:block;color:var(--wb-muted);font-size:12px;line-height:1.5;overflow-wrap:anywhere;margin-top:1px}
.profile-choice small:first-of-type{font-family:var(--wb-mono);font-size:11.5px}
.model-note{padding:0 4px}
.model-note h3{font-size:12px;font-weight:600;color:var(--wb-text);margin:0 0 4px}
.model-note p{font-size:12px;color:var(--wb-muted);line-height:1.65;margin:0}
.profile-advanced{margin:4px 0 18px;padding-top:14px;border-top:1px solid var(--wb-divider)}
.profile-advanced summary{cursor:pointer;color:var(--wb-text);font-size:13px;font-weight:500}
.profile-advanced[open] summary{margin-bottom:16px}
.profile-advanced .check-label{margin-bottom:10px}
.default-model{margin-bottom:18px}
.capability-list{display:grid;margin:14px -20px -20px;border-top:1px solid var(--wb-divider)}
.capability-row{padding:11px 20px;border-bottom:1px solid var(--wb-divider)}
.capability-row:last-child{border-bottom:0}
.capability-row>div{display:flex;align-items:center;justify-content:space-between;gap:14px}
.capability-row strong{font-size:13px;font-weight:500;color:var(--wb-ink)}
.capability-row p{font-size:12px;color:var(--wb-muted);line-height:1.6;margin:4px 0 0}
@media(max-width:860px){.model-layout{grid-template-columns:1fr}.model-note{display:none}.profile-list{display:flex;gap:6px;overflow-x:auto}.profile-choice{width:auto;min-width:200px;margin:0}.capability-list{margin:14px -16px -16px}.capability-row{padding:11px 16px}}
</style>
