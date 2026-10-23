<template>
  <el-container class="shell">
    <el-header class="top">
      <router-link class="brand" to="/">原理图评审</router-link>
      <span class="sub">报告</span>
    </el-header>
    <el-main class="main">
      <router-view />
    </el-main>
  </el-container>
</template>

<style>
html, body, #app { margin: 0; height: 100%; }
body { background: #f5f7fa; color: #1f2329; font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
.shell { min-height: 100%; }
.top { display: flex; align-items: baseline; gap: 12px; background: #fff; border-bottom: 1px solid #e5e6eb; }
.brand { color: #1f2329; font-size: 16px; font-weight: 650; text-decoration: none; }
.sub { color: #86909c; font-size: 13px; }
.main { max-width: 1120px; margin: 0 auto; width: 100%; }
</style>
