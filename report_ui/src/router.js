import { createRouter, createWebHistory } from "vue-router";
import RuleList from "./views/RuleList.vue";
import RuleDetail from "./views/RuleDetail.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "rules", component: RuleList },
    { path: "/rules/:ruleId", name: "rule", component: RuleDetail, props: true },
  ],
});
