<script setup>
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import PathViewer from "../components/PathViewer.vue";
import ReviewContractDetail from "../components/ReviewContractDetail.vue";
import SelectionCoverage from "../components/SelectionCoverage.vue";

const props = defineProps({ ruleId: { type: String, required: true } });
const router = useRouter();
const loading = ref(true);
const error = ref("");
const pack = ref(null);

const tagType = {
  是: "success",
  否: "danger",
  待确认: "warning",
  不适用: "info",
};

const cards = computed(() => pack.value?.object_cards || []);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const res = await fetch(`/evidence/${props.ruleId}.json`);
    if (!res.ok) throw new Error(`没有 ${props.ruleId} 的评审材料`);
    pack.value = await res.json();
  } catch (err) {
    pack.value = null;
    error.value = err.message || String(err);
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => props.ruleId, load);

function abs(src) {
  const s = String(src || "");
  if (!s || s.startsWith("/") || s.startsWith("http")) return s;
  return "/" + s;
}

const viewer = ref(null);

function pagesOf(st) {
  if (Array.isArray(st.figure_pages) && st.figure_pages.length) {
    return st.figure_pages.map((p) => ({ ...p, src: abs(p.src) }));
  }
  if (st.figure) return [{ src: abs(st.figure), label: "" }];
  return [];
}

function openPath(card, st) {
  const pages = pagesOf(st);
  if (!pages.length) return;
  viewer.value = {
    pages,
    lines: st.lines || [],
    who: `${props.ruleId} / ${card.object || ""}`,
  };
}
</script>

<template>
  <el-page-header @back="router.push('/')" :content="ruleId" />
  <SelectionCoverage :rule-id="ruleId" />
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="gap" />
  <div v-else v-loading="loading" class="gap">
    <template v-if="pack">
      <div class="head">
        <h2>{{ pack.name || ruleId }}</h2>
        <el-tag :type="tagType[pack.verdict_zh] || 'info'" size="large">{{ pack.verdict_zh }}</el-tag>
      </div>
      <p v-if="pack.rule_summary" class="lead">{{ pack.rule_summary }}</p>
      <el-collapse>
        <el-collapse-item v-for="card in cards" :key="card.object" :name="card.object">
          <template #title>
            <span class="obj">{{ card.object }}</span>
            <el-tag size="small" :type="tagType[card.verdict_zh] || 'info'">{{ card.verdict_zh }}</el-tag>
            <span class="one">{{ card.headline_zh || card.conclusion }}</span>
          </template>
          <ReviewContractDetail v-if="card.review_detail" :view="card.review_detail" @open-path="st => openPath(card, { figure: st.figure, figure_pages: st.figure_pages, lines: st.calculations.map(c => ({ expr: c.expression, src: '' })) })" />
          <p v-else>详情展示数据尚未生成，请重新生成报告。</p>
        </el-collapse-item>
      </el-collapse>
    </template>
  </div>
  <PathViewer
    v-if="viewer"
    :pages="viewer.pages"
    :lines="viewer.lines"
    :who="viewer.who"
    @close="viewer = null"
  />
</template>

<style scoped>
.gap { margin-top: 16px; }
.head { display: flex; align-items: center; gap: 12px; }
.head h2 { margin: 0; font-size: 20px; }
.lead { color: #4e5969; line-height: 1.6; }
.obj { font-weight: 650; margin-right: 8px; }
.one { margin-left: 8px; min-width: 0; color: #86909c; font-weight: 400; white-space: normal; overflow-wrap: anywhere; line-height: 1.6; }
:deep(.el-collapse-item__header) { height: auto; min-height: 48px; padding: 8px 0; }
.obj, :deep(.el-collapse-item__header .el-tag) { flex-shrink: 0; }
.state { margin-bottom: 12px; }
.state-hd { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.lines { padding-left: 18px; }
.lines em { display: block; color: #86909c; font-style: normal; font-size: 12px; }
.stress-cover { position: relative; display: block; width: 100%; margin: 0 0 10px; padding: 0; border: 1px solid #dde8e3; border-radius: 10px; overflow: hidden; background: #f4f7f8; cursor: zoom-in; text-align: left; }
.stress-cover img { display: block; width: 100%; max-height: 220px; object-fit: contain; background: #fff; }
.stress-mask { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; background: rgba(15,23,42,.42); color: #fff; font-size: 14px; font-weight: 650; opacity: 0; transition: opacity .15s; }
.stress-cover:hover .stress-mask { opacity: 1; }
</style>
