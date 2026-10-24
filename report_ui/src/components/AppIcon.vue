<script setup>
defineProps({ name: { type: String, default: 'file' }, size: { type: [Number, String], default: 18 } });
const paths = {
  folder: ['M3 7V5a2 2 0 0 1 2-2h5l2 3h7a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z'],
  file: ['M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z', 'M14 2v6h6M8 13h8M8 17h5'],
  play: ['m8 5 11 7-11 7V5Z'],
  layers: ['m12 3 10 5-10 5L2 8l10-5Z', 'm2 12 10 5 10-5M2 16l10 5 10-5'],
  settings: ['m9 3-1 3-3 1-2 4 2 2v3l3 2 1 3h6l1-3 3-2v-3l2-2-2-4-3-1-1-3H9Z', 'M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z'],
  'chevron-right': ['m9 5 7 7-7 7'], 'chevron-down': ['m6 9 6 6 6-6'],
  'arrow-right': ['M4 12h16m-6-6 6 6-6 6'], 'arrow-left': ['M20 12H4m6-6-6 6 6 6'],
  external: ['M14 3h7v7m0-7L10 14', 'M10 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-5'],
  check: ['m5 12 4 4L19 6'], 'check-circle': ['M22 11v1a10 10 0 1 1-5.9-9.1', 'm22 4-10 10-3-3'],
  'alert-circle': ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z', 'M12 8v5m0 3h.01'],
  clock: ['M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z', 'M12 6v6l4 2'],
  search: ['M21 21l-4.4-4.4M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z'],
  filter: ['M4 6h16M7 12h10m-7 6h4'], refresh: ['M20 7v5h-5', 'M4 17v-5h5', 'M6 7a7 7 0 0 1 12-2l2 2M4 17l2 2a7 7 0 0 0 12-2'],
  plus: ['M12 5v14M5 12h14'], menu: ['M4 6h16M4 12h16M4 18h16'], close: ['m6 6 12 12M18 6 6 18'],
  circuit: ['M3 6h6v6h6v6h6M3 18h6M15 6h6', 'M3 4v4m0 8v4M21 4v4m0 8v4M7 18h4M13 6h4'],
  'panel-left': ['M9 3v18', 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z'],
  'panel-right': ['M15 3v18', 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z'],
  download: ['M12 3v12m-5-5 5 5 5-5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4'],
  upload: ['M12 16V4m-5 5 5-5 5 5M4 16v4a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-4'],
  server: ['M4 3h16v7H4ZM4 14h16v7H4Z', 'M7 6.5h.01M7 17.5h.01M11 6.5h6M11 17.5h6'],
};
</script>
<template><svg class="app-icon" :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path v-for="(path,index) in (paths[name] || paths.file)" :key="index" :d="path" /></svg></template>
