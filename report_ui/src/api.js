import { loginRedirectUrl, withBasePath } from './urls';
export { assetUrl, withBasePath } from './urls';
let redirectingToLogin = false;

export class ApiError extends Error {
  constructor(message, status = 0, detail = null) { super(message); this.status = status; this.detail = detail; }
}

export async function request(path, options = {}) {
  const { timeout = 120000, ...init } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const headers = new Headers(init.headers || {});
    if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json');
    const response = await fetch(withBasePath(path), { ...init, headers, signal: init.signal || controller.signal });
    const contentType = response.headers.get('content-type') || '';
    const data = contentType.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) {
      if (response.status === 401 && !redirectingToLogin && typeof window !== 'undefined') {
        const login = loginRedirectUrl(data?.login_url, window.location.href);
        if (login) {
          redirectingToLogin = true;
          window.location.assign(login);
        }
      }
      const detail = data?.detail ?? data?.error ?? data;
      const message = typeof detail === 'string' ? detail : Array.isArray(detail)
        ? detail.map(row => `${(row.loc || []).slice(1).join(' / ')} ${row.msg || ''}`).join('；')
        : detail?.message || JSON.stringify(detail);
      const error = new ApiError(message || `请求失败（${response.status}）`, response.status, detail);
      error.response = data;
      throw error;
    }
    if (!contentType.includes('application/json')) throw new ApiError('服务未返回预期数据，请确认工作台服务已启动。', response.status);
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new ApiError('请求超时，请刷新状态后重试。后台任务不会因页面超时而停止。');
    if (error instanceof ApiError) throw error;
    throw new ApiError('无法连接本机工作台服务，请检查服务是否正在运行。');
  } finally { clearTimeout(timer); }
}

const segment = value => encodeURIComponent(String(value));
const json = (method, body) => ({ method, body: body === undefined ? undefined : JSON.stringify(body) });
export const api = {
  runtime: () => request('/api/runtime'),
  boards: () => request('/api/boards'),
  createBoard: name => request('/api/boards', json('POST', { name })),
  materials: id => request(`/api/boards/${segment(id)}/materials`),
  uploadMaterial: (id, role, file) => { const body = new FormData(); body.append('role', role); body.append('file', file); return request(`/api/boards/${segment(id)}/materials`, { method: 'POST', body }); },
  preflight: id => request(`/api/boards/${segment(id)}/preflight`, json('POST', {})),
  rules: () => request('/api/rules'),
  runs: board => request(`/api/runs${board ? `?board_id=${segment(board)}` : ''}`),
  run: id => request(`/api/runs/${segment(id)}`),
  createRun: (board, body) => request(`/api/boards/${segment(board)}/runs`, json('POST', body)),
  stopRun: id => request(`/api/runs/${segment(id)}/stop`, json('POST', {})),
  resumeRun: id => request(`/api/runs/${segment(id)}/resume`, json('POST', {})),
  reports: board => request(`/api/boards/${segment(board)}/reports`),
  report: id => request(`/api/reports/${segment(id)}/data`),
  feedback: id => request(`/api/reviews/${segment(id)}/feedback`),
  saveFeedback: (run, rule, object, body) => request(`/api/reviews/${segment(run)}/${segment(rule)}/${segment(object)}/feedback`, json('PUT', body)),
  questions: (run, rule, object) => request(`/api/reviews/${segment(run)}/${segment(rule)}/${segment(object)}/questions`),
  ask: (run, rule, object, body) => request(`/api/reviews/${segment(run)}/${segment(rule)}/${segment(object)}/questions`, { ...json('POST', body), timeout: 300000 }),
  profiles: () => request('/api/model-profiles'),
  saveProfile: body => request('/api/model-profiles', json('PUT', body)),
  testProfile: id => request(`/api/model-profiles/${segment(id)}/test`, { ...json('POST', {}), timeout: 180000 }),
};

export function dateLabel(value) {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { hour12: false });
}
export const runLabels = { queued: '等待执行', running: '执行中', stopping: '正在停止', stopped: '已停止', interrupted: '已中断', failed: '执行失败', completed: '执行完成' };
export const modeLabels = { materials: '材料检查', program: '程序检查', review: 'LLM 深度评审' };
export const stageLabels = { prep: '准备材料', enumerate: '识别评审范围', program: '程序规则检查', cases: '建立工况', calc: '执行计算', context_build: '构建评审上下文', judge: '对象评审', stress_figures: '生成分析图', aggregate: '汇总报告', gate: '校验交付', publish: '发布报告', materials: '检查材料', starting: '准备启动', completed: '完成' };
export const activeStatuses = new Set(['queued', 'running', 'stopping']);
export const verdictLabel = value => ({ '是': '通过', '否': '不通过', 'INSUFFICIENT': '待确认', 'PASS': '通过', 'FAIL': '不通过', 'INSUFFICIENT_EVIDENCE': '待确认', 'NOT_APPLICABLE': '不适用' }[value] || value || '待确认');
export const verdictClass = value => ({ '通过': 'success', '不通过': 'danger', '待确认': 'warning', '不适用': 'neutral' }[verdictLabel(value)] || 'neutral');
