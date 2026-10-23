<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

const props = defineProps({
  pages: { type: Array, required: true },
  lines: { type: Array, default: () => [] },
  who: { type: String, default: "" },
});
const emit = defineEmits(["close"]);

const index = ref(0);
const split = ref(false);
const sideW = ref(380);
const zoomLabel = ref("100%");
const viewEl = ref(null);
const mainEl = ref(null);
const fullOpen = ref(false);
const fullTitle = ref("");
const fullZoom = ref("100%");
const fullStage = ref(null);
const panes = [];
let full = { z: 1, x: 0, y: 0 };

const pageCount = computed(() => props.pages.length);

function pairStart() {
  if (pageCount.value < 2) return 0;
  return Math.min(index.value, pageCount.value - 2);
}
function visibleIndexes() {
  if (!split.value || pageCount.value < 2) return [index.value];
  return [pairStart(), pairStart() + 1];
}
const shown = computed(() => new Set(visibleIndexes()));
const activePage = computed(() => props.pages[index.value] || {});
const handoff = computed(() => {
  const n = pageCount.value;
  const next = props.pages[index.value + 1];
  if (split.value && n > 1) {
    const a = pairStart();
    return `对照的是第 ${a + 1}、${a + 2} 幅；页间关系以图中网名和标注为准。`;
  }
  if (next) return `继续查看关联图幅：${next.label || ""}`;
  return "";
});

function paintZoom() {
  zoomLabel.value = `${Math.round((panes[0]?.st.z || 1) * 100)}%`;
}
function bindSharp(host, src) {
  const board = document.createElement("canvas");
  board.className = "sharp";
  host.appendChild(board);
  const ctx = board.getContext("2d");
  const img = new Image();
  const st = { z: 1, x: 0, y: 0, nw: 0, nh: 0, drawnZ: -1 };
  function fit() {
    const r = host.getBoundingClientRect();
    if (!st.nw || r.width < 2 || r.height < 2) return 0;
    return Math.min(r.width / st.nw, r.height / st.nh);
  }
  function place() {
    const r = host.getBoundingClientRect();
    const scale = fit() * st.z;
    if (!scale) return;
    const cssW = st.nw * scale;
    const cssH = st.nh * scale;
    board.style.width = `${Math.round(cssW)}px`;
    board.style.height = `${Math.round(cssH)}px`;
    board.style.left = `${Math.round(r.width / 2 - cssW / 2 + st.x)}px`;
    board.style.top = `${Math.round(r.height / 2 - cssH / 2 + st.y)}px`;
  }
  function redraw() {
    const dpr = window.devicePixelRatio || 1;
    const scale = fit() * st.z;
    if (!scale) return;
    const cssW = st.nw * scale;
    const cssH = st.nh * scale;
    const bw = Math.max(1, Math.round(cssW * dpr));
    const bh = Math.max(1, Math.round(cssH * dpr));
    if (board.width !== bw || board.height !== bh) {
      board.width = bw;
      board.height = bh;
    }
    const up = bw >= st.nw;
    ctx.imageSmoothingEnabled = !up;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, 0, 0, bw, bh);
    st.drawnZ = st.z;
    place();
  }
  function apply() {
    if (st.drawnZ !== st.z) redraw();
    else place();
  }
  img.onload = () => {
    st.nw = img.naturalWidth;
    st.nh = img.naturalHeight;
    redraw();
  };
  img.src = src;
  return { board, st, apply, redraw };
}
function makePane(page, i, host) {
  const el = document.createElement("div");
  el.className = "pane";
  el.innerHTML = `<button type="button" class="full-btn">全图</button><div class="canvas"></div>`;
  el.querySelector(".full-btn").addEventListener("click", () => openFull(i));
  const canvas = el.querySelector(".canvas");
  const sharp = bindSharp(canvas, page.src);
  const st = sharp.st;
  function zoomAt(clientX, clientY, next) {
    const prev = st.z;
    const z = Math.min(6, Math.max(0.35, next));
    const rect = canvas.getBoundingClientRect();
    const mx = clientX - rect.left - rect.width / 2;
    const my = clientY - rect.top - rect.height / 2;
    st.x = mx - ((mx - st.x) * z) / prev;
    st.y = my - ((my - st.y) * z) / prev;
    st.z = z;
    sharp.apply();
    paintZoom();
  }
  canvas.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    zoomAt(ev.clientX, ev.clientY, st.z * (ev.deltaY < 0 ? 1.12 : 1 / 1.12));
  }, { passive: false });
  let drag = null;
  canvas.addEventListener("pointerdown", (ev) => {
    if (ev.button !== 0 || ev.target.closest(".full-btn")) return;
    drag = { x: ev.clientX, y: ev.clientY, ox: st.x, oy: st.y };
    canvas.setPointerCapture(ev.pointerId);
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    st.x = drag.ox + (ev.clientX - drag.x);
    st.y = drag.oy + (ev.clientY - drag.y);
    sharp.apply();
  });
  canvas.addEventListener("pointerup", () => { drag = null; });
  host.appendChild(el);
  return { el, st, zoomAt, reset() { st.z = 1; st.x = 0; st.y = 0; sharp.apply(); paintZoom(); } };
}
function renderView() {
  const host = viewEl.value;
  if (!host) return;
  const idxs = visibleIndexes();
  host.className = "view" + (idxs.length > 1 ? " split" : "");
  host.innerHTML = "";
  panes.length = 0;
  idxs.forEach((i) => panes.push(makePane(props.pages[i], i, host)));
  paintZoom();
}
function selectPage(i) {
  index.value = i;
  renderView();
}
function setSplit(on) {
  split.value = on && pageCount.value > 1;
  renderView();
}
function zoomAll(factor) {
  panes.forEach((p) => {
    const r = p.el.getBoundingClientRect();
    p.zoomAt(r.left + r.width / 2, r.top + r.height / 2, p.st.z * factor);
  });
}
function fitAll() {
  panes.forEach((p) => p.reset());
}

let fullSharp = null;
function syncFull() {
  if (!fullSharp) return;
  fullSharp.st.z = full.z;
  fullSharp.st.x = full.x;
  fullSharp.st.y = full.y;
}
function paintFull() {
  syncFull();
  if (fullSharp) fullSharp.apply();
  fullZoom.value = `${Math.round(full.z * 100)}%`;
}
function zoomFull(clientX, clientY, next) {
  const stage = fullStage.value;
  if (!stage || !fullSharp) return;
  const prev = full.z;
  const z = Math.min(8, Math.max(0.35, next));
  const rect = stage.getBoundingClientRect();
  const mx = clientX - rect.left - rect.width / 2;
  const my = clientY - rect.top - rect.height / 2;
  full.x = mx - ((mx - full.x) * z) / prev;
  full.y = my - ((my - full.y) * z) / prev;
  full.z = z;
  paintFull();
}
function openFull(i) {
  const page = props.pages[i];
  if (!page) return;
  full = { z: 1, x: 0, y: 0 };
  fullTitle.value = page.label || "全图";
  fullOpen.value = true;
  requestAnimationFrame(() => {
    const stage = fullStage.value;
    if (!stage) return;
    stage.innerHTML = "";
    fullSharp = bindSharp(stage, page.src);
    paintFull();
  });
}
function closeFull() {
  fullOpen.value = false;
}
function onFullWheel(ev) {
  zoomFull(ev.clientX, ev.clientY, full.z * (ev.deltaY < 0 ? 1.12 : 1 / 1.12));
}
let fullDrag = null;
let fullDown = null;
function onFullDown(ev) {
  if (ev.button !== 0) return;
  fullDown = { x: ev.clientX, y: ev.clientY, onImg: ev.target.classList && ev.target.classList.contains("sharp") };
  fullDrag = { x: ev.clientX, y: ev.clientY, ox: full.x, oy: full.y };
  ev.currentTarget.setPointerCapture(ev.pointerId);
}
function onFullMove(ev) {
  if (!fullDrag) return;
  full.x = fullDrag.ox + (ev.clientX - fullDrag.x);
  full.y = fullDrag.oy + (ev.clientY - fullDrag.y);
  paintFull();
}
function onFullUp(ev) {
  if (fullDown) {
    const moved = Math.hypot(ev.clientX - fullDown.x, ev.clientY - fullDown.y);
    if (!fullDown.onImg && moved < 6) closeFull();
  }
  fullDrag = null;
  fullDown = null;
}
function fullBy(factor) {
  const stage = fullStage.value;
  if (!stage) return;
  const r = stage.getBoundingClientRect();
  zoomFull(r.left + r.width / 2, r.top + r.height / 2, full.z * factor);
}
function fitFull() {
  full = { z: 1, x: 0, y: 0 };
  paintFull();
}

let barDrag = false;
function onBarDown(ev) {
  if (ev.button !== 0) return;
  barDrag = true;
  ev.currentTarget.setPointerCapture(ev.pointerId);
}
function onBarMove(ev) {
  if (!barDrag || !mainEl.value) return;
  const rect = mainEl.value.getBoundingClientRect();
  sideW.value = Math.min(rect.width - 280, Math.max(240, rect.right - ev.clientX));
}
function onBarUp() {
  barDrag = false;
}
function onKey(ev) {
  if (!fullOpen.value) return;
  if (ev.key === "Escape") closeFull();
}
onMounted(() => {
  if (pageCount.value < 2) split.value = false;
  renderView();
  window.addEventListener("keydown", onKey);
});
onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
</script>

<template>
  <div class="viewer">
    <div class="top">
      <button type="button" class="icon-btn" @click="emit('close')">← 回到明细</button>
      <div class="who">{{ who }}</div>
    </div>
    <div ref="mainEl" class="mainv" :style="{ '--side-w': sideW + 'px' }">
      <div class="stage">
        <div class="rail">
          <template v-for="(page, i) in pages" :key="page.src + i">
            <span v-if="i > 0" class="join">关联</span>
            <button type="button" class="step" :class="{ on: shown.has(i) }" @click="selectPage(i)">
              <i>{{ i + 1 }}</i>
              <span><strong>{{ page.label || "本页" }}</strong></span>
            </button>
          </template>
          <div v-if="pageCount > 1" class="tools">
            <button type="button" :class="{ on: !split }" @click="setSplit(false)">一页跟着看</button>
            <button type="button" :class="{ on: split }" @click="setSplit(true)">相邻两页对照</button>
          </div>
        </div>
        <div ref="viewEl" class="view"></div>
        <div class="zoom">
          <button type="button" @click="zoomAll(1 / 1.25)">−</button>
          <span>{{ zoomLabel }}</span>
          <button type="button" @click="zoomAll(1.25)">+</button>
          <button type="button" @click="fitAll">适应</button>
          <span class="hint">滚轮缩放 · 拖动平移</span>
        </div>
      </div>
      <div class="split-bar" title="左右拖动" @pointerdown="onBarDown" @pointermove="onBarMove" @pointerup="onBarUp" @pointercancel="onBarUp"></div>
      <aside class="side">
        <h2>这一段</h2>
        <p>{{ activePage.label || "本页" }}</p>
        <template v-if="lines.length">
          <h2 class="chain-h">计算链</h2>
          <div class="chain">
            <article v-for="(row, i) in lines" :key="i">
              <div class="expr"><span class="n">{{ i + 1 }}</span>{{ row.expr }}</div>
              <div v-if="row.src" class="src">{{ row.src }}</div>
            </article>
          </div>
        </template>
        <div v-if="handoff" class="handoff">{{ handoff }}</div>
        <div class="hint foot">图上只留结论和端压。算式和出处在这一侧，不画在铜线上。</div>
      </aside>
    </div>
    <div v-if="fullOpen" class="fullprev">
      <div class="bar">
        <b>{{ fullTitle }}</b>
        <span>{{ fullZoom }}</span>
        <span class="spacer"></span>
        <button type="button" @click="fullBy(1 / 1.25)">−</button>
        <button type="button" @click="fullBy(1.25)">+</button>
        <button type="button" @click="fitFull">适应全图</button>
        <button type="button" @click="closeFull">关闭</button>
      </div>
      <div
        ref="fullStage"
        class="full-stage"
        @wheel.prevent="onFullWheel"
        @pointerdown="onFullDown"
        @pointermove="onFullMove"
        @pointerup="onFullUp"
      ></div>
    </div>
  </div>
</template>

<style scoped>
.viewer { position: fixed; inset: 0; z-index: 2000; background: #f3f7f5; display: flex; flex-direction: column; color: #24312e; font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
.top { background: #fff; border-bottom: 1px solid #dde8e3; padding: 10px 16px; display: flex; align-items: center; gap: 12px; }
.who { font-size: 12px; color: #5d6c67; }
.icon-btn, .tools button, .zoom button, .bar button, .full-btn { border: 1px solid #dde8e3; background: #fff; border-radius: 999px; font: inherit; font-size: 12px; cursor: pointer; }
.icon-btn { padding: 6px 10px; border-radius: 8px; }
.mainv { flex: 1; min-height: 0; display: grid; grid-template-columns: 1fr 8px var(--side-w, 380px); }
.stage { min-width: 0; min-height: 0; display: grid; grid-template-rows: auto 1fr auto; }
.rail { display: flex; align-items: center; gap: 8px; padding: 10px 16px; overflow: auto; }
.step { display: flex; gap: 8px; align-items: center; min-width: 180px; text-align: left; border: 1px solid #dde8e3; background: #fff; border-radius: 12px; padding: 8px 10px; cursor: pointer; font: inherit; color: inherit; }
.step.on { border-color: #1f6f62; box-shadow: 0 0 0 3px rgba(31,111,98,.12); }
.step i { width: 22px; height: 22px; border-radius: 999px; display: grid; place-items: center; background: #d7ebe6; color: #1f6f62; font-style: normal; font-size: 12px; font-weight: 700; flex: none; }
.step.on i { background: #1f6f62; color: #fff; }
.join { color: #5d6c67; font-size: 12px; }
.tools { margin-left: auto; display: flex; gap: 6px; }
.tools button { padding: 6px 10px; }
.tools button.on { background: #1f6f62; color: #fff; border-color: #1f6f62; }
.view { min-height: 0; display: grid; gap: 10px; padding: 0 16px; }
.view.split { grid-template-columns: 1fr 1fr; }
.view :deep(.pane) { position: relative; border: 1px solid #dde8e3; border-radius: 14px; background: #fff; overflow: hidden; min-height: 0; }
.view :deep(.full-btn) { position: absolute; z-index: 2; right: 10px; top: 10px; padding: 4px 10px; }
.view :deep(.canvas) { position: absolute; inset: 0; cursor: grab; touch-action: none; }
.view :deep(.canvas:active) { cursor: grabbing; }
.view :deep(.sharp), .full-stage :deep(.sharp) { position: absolute; user-select: none; }
.zoom { display: flex; align-items: center; gap: 8px; margin: 8px 16px 12px; width: max-content; padding: 6px 8px; border: 1px solid #dde8e3; border-radius: 999px; background: #fff; }
.zoom button { width: 32px; height: 28px; padding: 0; }
.hint { color: #5d6c67; font-size: 12px; }
.split-bar { cursor: col-resize; position: relative; }
.split-bar::before { content: ""; position: absolute; top: 0; bottom: 0; left: 3px; width: 2px; background: #dde8e3; }
.side { background: #fff; border-left: 1px solid #dde8e3; padding: 16px; overflow: auto; }
.side h2 { margin: 0 0 8px; font-size: 15px; }
.chain-h { margin-top: 18px; }
.side p { margin: 0 0 12px; font-size: 15px; line-height: 1.55; }
.chain { display: flex; flex-direction: column; gap: 10px; }
.chain article { background: #f7fbf9; border: 1px solid #dde8e3; border-radius: 12px; padding: 12px 14px; }
.chain .n { display: inline-grid; place-items: center; width: 22px; height: 22px; margin-right: 8px; border-radius: 999px; background: #1f6f62; color: #fff; font-size: 12px; font-weight: 700; }
.chain .expr { font-size: 15px; font-weight: 650; line-height: 1.45; }
.chain .src { margin-top: 6px; color: #5d6c67; font-size: 13px; line-height: 1.5; }
.handoff { margin-top: 16px; padding: 12px; border-radius: 12px; background: #e7f4f0; color: #134e48; font-size: 13px; line-height: 1.5; }
.foot { margin-top: 14px; line-height: 1.5; }
.fullprev { position: absolute; inset: 0; z-index: 5; background: rgba(20,28,26,.72); display: flex; flex-direction: column; }
.bar { display: flex; align-items: center; gap: 10px; padding: 10px 14px; color: #fff; }
.bar b { font-weight: 650; }
.spacer { flex: 1; }
.bar button { border-color: rgba(255,255,255,.35); background: transparent; color: #fff; padding: 6px 12px; font-size: 13px; }
.full-stage { flex: 1; min-height: 0; position: relative; cursor: grab; touch-action: none; }
</style>
