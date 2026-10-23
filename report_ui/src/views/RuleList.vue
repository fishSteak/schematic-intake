<script setup>
import { withBasePath } from "../api";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import SelectionCoverage from "../components/SelectionCoverage.vue";

const router = useRouter();
const loading = ref(true);
const error = ref("");
const rows = ref([]);
const summary = ref({});

const tagType = {
  是: "success",
  否: "danger",
  待确认: "warning",
  不适用: "info",
};

onMounted(async () => {
  try {
    const res = await fetch(withBasePath("/verdicts.json"));
    if (!res.ok) throw new Error("读不到 verdicts.json");
    const data = await res.json();
    summary.value = data.summary || {};
    rows.value = (data.results || []).map((r) => ({
      rule_id: r.rule_id,
      title: r.title || r.rule_id,
      verdict_zh: String(r.verdict_zh || "").replace(/^INSUFFICIENT.*/, "待确认"),
      coverage_n: r.coverage_n ?? (r.object_cards || []).length,
    }));
  } catch (err) {
    error.value = err.message || String(err);
  } finally {
    loading.value = false;
  }
});

function openRule(row) {
  router.push({ name: "rule", params: { ruleId: row.rule_id } });
}
</script>

<template>
  <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
  <div v-else v-loading="loading">
    <SelectionCoverage />
    <div class="stats">
      <el-tag type="success">是 {{ summary["是"] || 0 }}</el-tag>
      <el-tag type="danger">否 {{ summary["否"] || 0 }}</el-tag>
      <el-tag type="warning">待确认 {{ summary.INSUFFICIENT || summary["待确认"] || 0 }}</el-tag>
      <el-tag type="info">不适用 {{ summary["不适用"] || 0 }}</el-tag>
    </div>
    <el-table :data="rows" stripe @row-click="openRule">
      <el-table-column prop="rule_id" label="检查项" width="120" />
      <el-table-column prop="title" label="内容" min-width="280" />
      <el-table-column prop="coverage_n" label="对象" width="80" />
      <el-table-column label="结论" width="110">
        <template #default="{ row }">
          <el-tag :type="tagType[row.verdict_zh] || 'info'">{{ row.verdict_zh }}</el-tag>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.stats { display: flex; gap: 8px; margin-bottom: 12px; }
.el-table { cursor: pointer; }
</style>
