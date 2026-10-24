<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue';
import { api, dateLabel } from '../api';
import { loadWorkspace } from '../workspace';
import AppIcon from '../components/AppIcon.vue';
import StatusBadge from '../components/StatusBadge.vue';

const profiles = ref([]), loading = ref(true), busy = ref(''), error = ref(''), success = ref('');
const result = ref(null), selected = ref(''), defaultId = ref(''), keyConfigured = ref(false);
const formElement = ref(null), advancedElement = ref(null), errorSummary = ref(null), fieldErrors = ref({}), savedSignature = ref('');
let mounted = true;
const defaults = () => ({id:'',name:'',provider:'openai-compatible',wire_api:'chat',base_url:'',model:'',api_key_env:'',api_key:'',timeout:120,max_tokens:8192,max_turns:12,max_requests:40,max_retries:2,max_context_tokens:null,stream:false,structured_output:'json_schema',strict_schema:false,supports_tools:true,supports_images:true,token_parameter:'max_tokens',default:false});
const form = reactive(defaults());
const wireLabel = value => ({chat:'Chat Completions API',responses:'Responses API'}[value || 'chat'] || value);
const signature = () => JSON.stringify(Object.fromEntries(Object.keys(defaults()).filter(key => key !== 'api_key').map(key => [key,form[key]])));
const dirty = computed(() => Boolean(form.api_key) || signature() !== savedSignature.value);
const savedProfile = computed(() => profiles.value.find(profile => profile.id === selected.value));
const numericFields = [
  {key:'timeout',id:'model-timeout',label:'请求超时（秒）',min:1,max:1200,help:'等待单次模型响应的最长时间。'},
  {key:'max_tokens',id:'model-tokens',label:'单次最大输出 Token',min:128,max:131072,help:'为分析、工具调用或结构化结果预留输出。'},
  {key:'max_turns',id:'model-turns',label:'最大工具循环轮次',min:1,max:100,help:'限制一个模型子任务中的工具交互轮次。'},
  {key:'max_requests',id:'model-requests',label:'单个模型子任务最大请求数',min:1,max:500,help:'每个子任务分别计数；不是整板评审的总额度。'},
  {key:'max_retries',id:'model-retries',label:'失败重试次数',min:0,max:5,help:'适用于可重试的请求错误。'},
  {key:'max_context_tokens',id:'model-context',label:'上下文窗口预算（Token，可选）',min:1,optional:true,help:'按服务商文档填写；留空不启用本机预算检查。输入与预留输出采用保守估算，测试不会探测服务最大窗口。'},
];
const fieldIds = {name:'model-name',provider:'model-provider',wire_api:'model-wire-api',base_url:'model-base',model:'model-id',api_key:'model-key',api_key_env:'model-key-env',structured_output:'model-output',token_parameter:'model-token-parameter',...Object.fromEntries(numericFields.map(field => [field.key,field.id]))};
const capabilityDefinitions = [{key:'tools',label:'工具调用'},{key:'structured_output',label:'结构化输出'},{key:'vision',label:'图像理解'},{key:'streaming',label:'流式响应'},{key:'context_capacity',label:'上下文预算'}];
const capabilityRows = computed(() => {
  const data = result.value || {}, tested = data.capabilities || data.tested || {};
  return capabilityDefinitions.map(({key,label}) => {
    const value = tested[key] ?? (key === 'context_capacity' ? data.context?.status : key === 'vision' ? data.vision : undefined);
    const states = {disabled:['已关闭','neutral'],configured_not_tested:['已配置，未测试','warning'],not_tested:['未测试','neutral'],tested:['测试通过','success'],configured:['已配置容量','info'],not_configured:['未配置容量','neutral'],failed:['测试未通过','danger']};
    const [status,tone] = value === true ? ['测试通过','success'] : value === false ? ['测试未通过','danger'] : states[value] || ['未测试','neutral'];
    let detail = '';
    if (key === 'context_capacity') {
      const context = data.context || {}, parts = [];
      if (context.max_context_tokens != null) parts.push('配置容量 ' + Number(context.max_context_tokens).toLocaleString('zh-CN') + ' Token');
      if (context.estimated_input_tokens != null) parts.push('本次估算输入 ' + Number(context.estimated_input_tokens).toLocaleString('zh-CN') + ' Token');
      if (context.reserved_output_tokens != null) parts.push('本次预留输出 ' + Number(context.reserved_output_tokens).toLocaleString('zh-CN') + ' Token');
      detail = (parts.length ? parts.join('；') + '。' : value === 'not_configured' ? '未启用本机容量限制。' : '') + '容量采用保守估算，未探测服务允许的最大窗口。';
    } else if (key === 'vision' && value === true) detail = '已发送测试图片并核对识别结果；这不代表工程图纸评审质量。';
    else if (value === undefined || value === 'not_tested') detail = '尚无此项测试结果。';
    return {key,label,status,tone,detail};
  });
});
const describedBy = (key, help) => [help, fieldErrors.value[key] ? fieldIds[key] + '-error' : ''].filter(Boolean).join(' ') || undefined;
function clearMessages() { error.value = ''; success.value = ''; fieldErrors.value = {}; }
function edit(profile) {
  selected.value = profile.id;
  Object.assign(form,defaults(),Object.fromEntries(Object.keys(defaults()).filter(key => key in profile).map(key => [key,profile[key]])),{api_key:'',wire_api:profile.wire_api || 'chat',default:profile.id === defaultId.value});
  keyConfigured.value = Boolean(profile.has_api_key || profile.api_key_configured || profile.has_key);
  result.value = profile.probe || null; clearMessages(); savedSignature.value = signature();
}
function create() { selected.value = ''; Object.assign(form,defaults(),{default:!profiles.value.length}); keyConfigured.value = false; result.value = null; clearMessages(); savedSignature.value = signature(); }
function resetChanges() { if (savedProfile.value) edit(savedProfile.value); else create(); }
async function refreshProfiles(preferred = selected.value) {
  const data = await api.profiles();
  if (!mounted) return;
  profiles.value = data.profiles || []; defaultId.value = data.default_profile_id || '';
  if (profiles.value.length) edit(profiles.value.find(profile => profile.id === preferred) || profiles.value.find(profile => profile.id === defaultId.value) || profiles.value[0]);
  else create();
}
async function load() {
  loading.value = true; clearMessages();
  try { await refreshProfiles(); } catch (e) { if (mounted) error.value = e.message; }
  finally { if (mounted) loading.value = false; }
}
async function focusField(key) {
  if (numericFields.some(field => field.key === key) || ['structured_output','token_parameter'].includes(key)) advancedElement.value.open = true;
  await nextTick(); document.getElementById(fieldIds[key])?.focus();
}
function revealInvalid(event) {
  const input = event.target;
  // Native submit validation runs before @submit. Open hidden controls during
  // the capture phase so the browser can focus and announce the first error.
  for (let section = input.closest?.('details'); section; section = section.parentElement?.closest('details')) section.open = true;
  const key = Object.keys(fieldIds).find(name => fieldIds[name] === input.id);
  if (key && input.validationMessage) fieldErrors.value = {...fieldErrors.value,[key]:input.validationMessage};
}
async function save(test = false) {
  if (busy.value || !formElement.value?.reportValidity()) return;
  busy.value = 'save'; clearMessages();
  try {
    const payload = Object.fromEntries(Object.keys(defaults()).map(key => [key,form[key]]));
    for (const key of ['timeout','max_tokens','max_turns','max_requests','max_retries']) payload[key] = Number(payload[key]);
    payload.max_context_tokens = form.max_context_tokens == null || form.max_context_tokens === '' ? null : Number(form.max_context_tokens);
    if (!payload.id) delete payload.id;
    if (!payload.api_key) delete payload.api_key;
    const response = await api.saveProfile(payload);
    if (!mounted) return;
    const profile = response.profile || response;
    form.api_key = ''; selected.value = profile.id;
    await refreshProfiles(profile.id);
    if (!mounted) return;
    success.value = '模型配置已保存。';
    await loadWorkspace();
    if (test && mounted) {
      busy.value = 'test'; result.value = null;
      result.value = await api.testProfile(selected.value);
      if (!result.value.ok) success.value = '';
    }
  } catch (e) {
    if (!mounted) return;
    if (busy.value === 'test') { result.value = {...(e.response && typeof e.response === 'object' ? e.response : {}),ok:false,error:e.message}; success.value = ''; }
    else {
      error.value = e.message;
      if (Array.isArray(e.detail)) fieldErrors.value = Object.fromEntries(e.detail.filter(item => fieldIds[item.loc?.at(-1)]).map(item => [item.loc.at(-1),item.msg]));
      await nextTick(); errorSummary.value?.focus();
    }
  } finally { if (mounted) busy.value = ''; }
}
onMounted(load);
onBeforeUnmount(() => { mounted = false; });
</script>
<template>
  <section class="models-page">
    <div class="page-heading"><div><div class="eyebrow">模型服务</div><h1>模型连接与能力</h1><p>配置评审使用的 LLM API，核对接口能力和调用预算。</p></div><button class="button" :disabled="!!busy || loading" @click="create"><AppIcon name="plus" />添加配置</button></div>
    <div v-if="error" ref="errorSummary" class="notice error model-error" role="alert" tabindex="-1"><strong>配置未能完成</strong><p>{{ error }}</p><ul v-if="Object.keys(fieldErrors).length"><li v-for="(message,key) in fieldErrors" :key="key"><button type="button" @click="focusField(key)">{{ message }} · 定位字段</button></li></ul></div>
    <div v-if="success" class="notice success" role="status">{{ success }}</div>
    <div v-if="loading" class="loading-state" role="status">正在加载模型配置</div>
    <div v-else class="model-layout">
      <aside class="model-sidebar"><section class="section-card"><div class="section-heading"><h2>已保存配置</h2><span>{{ profiles.length }} 个</span></div><p v-if="!profiles.length" class="small-copy muted">添加服务地址和模型名称，保存后验证连接。</p><nav aria-label="模型配置"><button v-for="profile in profiles" :key="profile.id" class="profile-choice" :class="{selected:selected === profile.id}" :aria-pressed="selected === profile.id" :disabled="!!busy" @click="edit(profile)"><AppIcon name="server" /><span class="profile-text"><strong>{{ profile.name || profile.model }}</strong><small>{{ profile.model }}</small><small>{{ wireLabel(profile.wire_api) }}</small></span><StatusBadge v-if="profile.id === defaultId" tone="info">默认</StatusBadge></button></nav><p v-if="dirty" class="switch-note">当前有未保存修改。切换配置将放弃这些修改。</p></section><section class="section-card service-note"><h3>材料与凭据</h3><p>配置与密钥保存在工作台服务所在主机，已保存密钥不会回显。</p><p>远端模型会接收参与分析的材料内容；本机模型可填写本地 API 地址。实际能力以测试结果为准。</p></section></aside>
      <div class="model-main">
        <form ref="formElement" class="section-card model-form" @submit.prevent="save(false)" @invalid.capture="revealInvalid">
          <div class="section-heading"><h2>{{ selected ? '编辑模型配置' : '新增模型配置' }}</h2><StatusBadge :tone="dirty ? 'warning' : 'neutral'">{{ selected ? (dirty ? '未保存修改' : '已保存') : '尚未保存' }}</StatusBadge></div>
          <div v-if="dirty && selected" class="draft-notice" role="status"><AppIcon name="alert-circle" /><span>修改尚未用于评审。保存后重新测试，确认当前接口能力。</span></div>
          <fieldset class="model-fields" :disabled="!!busy"><legend class="sr-only">模型连接配置</legend>
            <section class="model-form-section"><div class="form-section-title"><span>01</span><div><h3>服务连接</h3><p>填写服务支持的地址、接口协议和模型 ID。</p></div></div>
              <div class="form-grid"><div class="field"><label for="model-name">配置名称</label><input id="model-name" v-model="form.name" placeholder="例如：评审主模型" maxlength="100" required :aria-invalid="!!fieldErrors.name" :aria-describedby="describedBy('name')" /><p v-if="fieldErrors.name" id="model-name-error" class="field-error">{{ fieldErrors.name }}</p></div><div class="field"><label for="model-provider">服务类型</label><select id="model-provider" v-model="form.provider"><option value="openai-compatible">OpenAI 兼容接口</option></select></div><div class="field span-2"><label for="model-base">服务地址（Base URL）</label><input id="model-base" v-model="form.base_url" type="url" placeholder="https://your-provider.example/v1" required :aria-invalid="!!fieldErrors.base_url" :aria-describedby="describedBy('base_url','model-base-help')" /><span id="model-base-help" class="help">API 根地址；运行在工作台主机上的模型服务可使用 localhost。</span><p v-if="fieldErrors.base_url" id="model-base-error" class="field-error">{{ fieldErrors.base_url }}</p></div><div class="field"><label for="model-wire-api">API 协议</label><select id="model-wire-api" v-model="form.wire_api" required :aria-describedby="describedBy('wire_api','model-wire-help')"><option value="chat">Chat Completions API</option><option value="responses">Responses API</option></select><span id="model-wire-help" class="help">按服务商支持的协议选择，更改后需重新测试。</span><p v-if="fieldErrors.wire_api" id="model-wire-api-error" class="field-error">{{ fieldErrors.wire_api }}</p></div><div class="field"><label for="model-id">模型名称</label><input id="model-id" v-model="form.model" placeholder="服务支持的模型 ID" required :aria-invalid="!!fieldErrors.model" :aria-describedby="describedBy('model')" /><p v-if="fieldErrors.model" id="model-id-error" class="field-error">{{ fieldErrors.model }}</p></div></div>
            </section>
            <section class="model-form-section"><div class="form-section-title"><span>02</span><div><h3>访问凭据</h3><p>本机无鉴权服务可留空。已保存的密钥留空即可保留。</p></div></div><div class="form-grid"><div class="field span-2"><label for="model-key">API Key <span v-if="keyConfigured" class="saved-key">已保存</span></label><input id="model-key" v-model="form.api_key" type="password" autocomplete="new-password" :placeholder="keyConfigured ? '留空保留已保存密钥' : '输入 API Key'" :aria-describedby="describedBy('api_key','model-key-help')" /><span id="model-key-help" class="help">页面不会读取或回显已保存密钥。填写新值将替换保存的密钥。</span><p v-if="fieldErrors.api_key" id="model-key-error" class="field-error">{{ fieldErrors.api_key }}</p></div><div class="field span-2"><label for="model-key-env">密钥环境变量（可选）</label><input id="model-key-env" v-model="form.api_key_env" placeholder="例如 SCHEMATIC_LLM_API_KEY" :aria-describedby="describedBy('api_key_env','model-key-env-help')" /><span id="model-key-env-help" class="help">填写工作台服务启动环境中的变量名；变量有有效值时优先使用。</span><p v-if="fieldErrors.api_key_env" id="model-key-env-error" class="field-error">{{ fieldErrors.api_key_env }}</p></div></div></section>
            <details ref="advancedElement" class="profile-advanced"><summary><span class="advanced-title">03 <strong>调用、能力与预算</strong></span><span>高级设置<AppIcon name="chevron-down" /></span></summary><p class="advanced-intro">限制每个模型子任务的请求与输出规模。能力开关须与实际服务支持保持一致。</p><div class="form-grid"><div v-for="field in numericFields" :key="field.key" class="field" :class="{'span-2':field.optional}"><label :for="field.id">{{ field.label }}</label><input :id="field.id" v-model.number="form[field.key]" type="number" :min="field.min" :max="field.max" step="1" :required="!field.optional" :placeholder="field.optional ? '留空不限制' : undefined" :aria-invalid="!!fieldErrors[field.key]" :aria-describedby="describedBy(field.key,field.id + '-help')" /><span :id="field.id + '-help'" class="help">{{ field.help }}</span><p v-if="fieldErrors[field.key]" :id="field.id + '-error'" class="field-error">{{ fieldErrors[field.key] }}</p></div><div class="field"><label for="model-output">结构化输出方式</label><select id="model-output" v-model="form.structured_output"><option value="json_schema">JSON Schema</option><option value="json_object">JSON Object</option><option value="none">模型文本返回</option></select></div><div v-if="form.wire_api === 'chat'" class="field"><label for="model-token-parameter">输出预算参数</label><select id="model-token-parameter" v-model="form.token_parameter"><option value="max_tokens">max_tokens</option><option value="max_completion_tokens">max_completion_tokens</option></select></div></div><div class="capability-switches"><label class="check-label"><input v-model="form.supports_tools" type="checkbox" />支持工具调用</label><label class="check-label"><input v-model="form.supports_images" type="checkbox" />支持图像输入</label><label class="check-label"><input v-model="form.stream" type="checkbox" />启用流式响应</label><label class="check-label"><input v-model="form.strict_schema" type="checkbox" />严格 JSON Schema</label></div></details>
            <div class="model-save"><label class="check-label default-model"><input v-model="form.default" type="checkbox" />设为新运行的默认配置</label><div class="actions"><button class="button primary" :disabled="!!busy"><AppIcon name="check" />{{ busy === 'save' ? '正在保存…' : '保存配置' }}</button><button class="button" type="button" :disabled="!!busy || !form.base_url || !form.model || !form.name" @click="save(true)"><AppIcon name="server" />{{ busy === 'test' ? '正在测试连接…' : '保存并测试连接' }}</button><button v-if="dirty" class="button ghost" type="button" :disabled="!!busy" @click="resetChanges">放弃修改</button></div><p class="inline-note">连接测试会发起真实模型请求。所有请求关闭服务端响应保存（store: false）。</p></div>
          </fieldset>
        </form>
        <section class="section-card capability-panel" aria-label="已保存配置的能力测试"><div class="section-heading"><div><h2>{{ result ? '已保存配置的最近测试' : '模型能力检查' }}</h2><p v-if="savedProfile" class="test-profile">{{ savedProfile.name || savedProfile.model }} · {{ savedProfile.model }}</p></div><StatusBadge :tone="busy === 'test' ? 'info' : result ? (result.ok ? 'success' : 'danger') : 'neutral'">{{ busy === 'test' ? '正在测试' : result ? (result.ok ? '测试通过' : '测试未通过') : '未测试' }}</StatusBadge></div><div v-if="dirty" class="draft-notice" role="status"><AppIcon name="alert-circle" /><span>{{ result ? '以下结果仅对应上次保存的配置。当前修改尚未保存或测试。' : '当前修改尚未测试。请保存配置并测试连接。' }}</span></div><p class="small-copy test-message" role="status">{{ busy === 'test' ? '正在核对服务响应与实际接口能力，请稍候。' : result ? (result.message || result.detail || result.error || (result.ok ? '模型服务已返回有效响应。' : '请核对接口地址、凭据和模型名称。')) : '尚未测试。保存并测试连接后，将显示各项能力的实际检查结果。' }}</p><dl class="test-metadata"><div v-if="result?.wire_api"><dt>测试接口</dt><dd>{{ wireLabel(result.wire_api) }}</dd></div><div v-if="result?.model"><dt>响应模型</dt><dd>{{ result.model }}</dd></div><div v-if="result?.latency_ms != null"><dt>耗时</dt><dd>{{ result.latency_ms }} ms</dd></div><div v-if="result?.tested_at"><dt>测试时间</dt><dd>{{ dateLabel(result.tested_at) }}</dd></div></dl><div class="capability-list"><div v-for="capability in capabilityRows" :key="capability.key" class="capability-row" :data-capability="capability.key"><div><strong>{{ capability.label }}</strong><StatusBadge :tone="capability.tone">{{ capability.status }}</StatusBadge></div><p v-if="capability.detail">{{ capability.detail }}</p></div></div></section>
      </div>
    </div>
  </section>
</template>
<style scoped>
.model-layout{display:grid;grid-template-columns:260px minmax(0,1fr);gap:22px;align-items:start}.model-sidebar .section-card{padding:18px}.profile-choice{display:flex;align-items:flex-start;gap:9px;width:100%;padding:13px 11px;margin:0 0 8px;border:1px solid var(--wb-border);border-radius:8px;background:var(--wb-surface);text-align:left;color:var(--wb-ink)}.profile-choice.selected{border-color:var(--wb-primary);background:var(--wb-soft);box-shadow:inset 3px 0 var(--wb-primary)}.profile-choice>svg{width:18px;height:18px;flex-shrink:0;color:var(--wb-muted);margin-top:1px}.profile-text{flex:1;min-width:0}.profile-choice strong{font-size:13px;display:block;font-weight:600;overflow-wrap:anywhere;line-height:1.5}.profile-choice small{display:block;color:var(--wb-muted);font-size:12px;line-height:1.6;overflow-wrap:anywhere;margin-top:4px}.profile-choice .status-badge{padding:3px 6px;font-size:12px}.switch-note{font-size:12px;line-height:1.7;color:var(--wb-warning);margin:14px 0 0}.service-note h3{font-size:14px}.service-note p{font-size:12px;color:var(--wb-muted);line-height:1.85;margin:10px 0 0}.model-main{min-width:0}.model-form{padding:24px}.model-fields{padding:0;margin:0;border:0;min-width:0}.model-form-section{border-top:1px solid var(--wb-border);padding-top:22px;margin-top:22px}.form-section-title{display:flex;align-items:flex-start;gap:12px;margin-bottom:20px}.form-section-title>span,.advanced-title{font-variant-numeric:tabular-nums;font-size:12px;color:var(--wb-muted)}.form-section-title>span{padding-top:2px}.form-section-title h3{font-size:15px;margin:0 0 5px}.form-section-title p{font-size:12px;color:var(--wb-muted);margin:0;line-height:1.7}.draft-notice{display:flex;align-items:flex-start;gap:9px;padding:12px 14px;border:1px solid var(--wb-border);border-left:3px solid var(--wb-warning);border-radius:8px;font-size:12px;line-height:1.8;background:var(--wb-bg);color:var(--wb-ink);margin-bottom:20px}.draft-notice svg{width:17px;height:17px;color:var(--wb-warning);flex-shrink:0;margin-top:2px}.saved-key{font-size:12px;color:var(--wb-success);font-weight:500;margin-left:8px}.profile-advanced{border-top:1px solid var(--wb-border);border-bottom:1px solid var(--wb-border);margin:5px 0 22px}.profile-advanced summary{display:flex;align-items:center;justify-content:space-between;gap:12px;cursor:pointer;padding:19px 0;list-style:none;min-height:44px}.profile-advanced summary::-webkit-details-marker{display:none}.advanced-title{display:flex;align-items:center;gap:12px}.advanced-title strong{font-size:15px;color:var(--wb-ink)}.profile-advanced summary>span:last-child{display:flex;align-items:center;gap:6px;font-size:12px;color:var(--wb-muted)}.profile-advanced summary svg{width:16px;height:16px}.profile-advanced[open] summary svg{transform:rotate(180deg)}.advanced-intro{font-size:12px;color:var(--wb-muted);margin:0 0 19px;line-height:1.75}.capability-switches{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin:3px 0 23px}.default-model{margin-bottom:20px}.model-save .inline-note{margin-top:13px}.test-profile{font-size:12px;color:var(--wb-muted);margin:8px 0 0;overflow-wrap:anywhere}.test-message{color:var(--wb-muted)}.test-metadata{display:flex;flex-wrap:wrap;gap:14px 28px;padding:0;margin:18px 0;font-size:12px}.test-metadata dt{color:var(--wb-muted);margin-bottom:5px}.test-metadata dd{margin:0;overflow-wrap:anywhere;line-height:1.6}.capability-list{display:grid;grid-template-columns:1fr 1fr;gap:0 24px;margin-top:18px}.capability-row{padding:16px 0;border-top:1px solid var(--wb-border);min-width:0}.capability-row:last-child{grid-column:1/-1}.capability-row>div{display:flex;align-items:center;justify-content:space-between;gap:12px}.capability-row strong{font-size:13px;font-weight:600}.capability-row p{font-size:12px;color:var(--wb-muted);line-height:1.75;margin:8px 0 0}.field-error{font-size:12px;color:var(--wb-danger);margin:0;line-height:1.6}.model-error p{margin:6px 0}.model-error ul{margin:8px 0 0;padding-left:20px}.model-error button{padding:5px 0;background:none;border:0;color:inherit;text-decoration:underline;font-size:13px;text-align:left;min-height:32px}.models-page .button>svg{width:17px;height:17px}.model-fields:disabled input,.model-fields:disabled select{background:var(--wb-soft);cursor:not-allowed}.model-fields:disabled .check-label{cursor:not-allowed}@media(max-width:1040px){.model-layout{grid-template-columns:230px minmax(0,1fr);gap:16px}.model-form{padding:20px}.capability-list{grid-template-columns:1fr}}@media(max-width:800px){.model-layout{grid-template-columns:1fr}.model-sidebar nav{display:flex;gap:8px;overflow-x:auto;padding:2px 2px 5px}.profile-choice{flex:0 0 240px;margin:0}.service-note{display:none}.model-sidebar .section-card{margin-bottom:0}.model-form{padding:18px}}@media(max-width:560px){.capability-switches{grid-template-columns:1fr;gap:8px}.capability-switches .check-label{min-height:44px}.model-save .actions{align-items:stretch}.model-save .actions .button{min-height:44px}.model-error button{min-height:44px}.profile-advanced summary{gap:8px}.model-form .form-grid{grid-template-columns:1fr}.model-form .form-grid .span-2{grid-column:auto}.default-model{min-height:44px}.capability-panel{padding:18px}}
</style>
