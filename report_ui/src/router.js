import { createRouter, createWebHistory } from "vue-router";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "projects", component: () => import('./views/ProjectsView.vue') },
    { path: "/materials", name: "materials", component: () => import('./views/MaterialsView.vue') },
    { path: "/runs", name: "runs", component: () => import('./views/RunsView.vue') },
    { path: "/reports", name: "reports", component: () => import('./views/ReportsView.vue') },
    { path: "/models", name: "models", component: () => import('./views/ModelsView.vue') },
    { path: "/legacy", name: "rules", redirect: to => ({ path: "/reports", query: to.query }) },
    { path: "/rules/:ruleId", name: "rule", redirect: to => ({ path: "/reports", query: { ...to.query, rule: to.params.ruleId } }) },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});
