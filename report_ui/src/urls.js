export function normalizeBasePath(value = '/') {
  const path = String(value || '/').trim();
  if (/[?#\\]/.test(path) || path.includes('://') || path.split('/').some(part => part === '.' || part === '..')) {
    throw new Error('VITE_BASE_PATH must be a URL path, for example /schematic/.');
  }
  const parts = path.split('/').filter(Boolean);
  return parts.length ? `/${parts.join('/')}/` : '/';
}

export const APP_BASE = normalizeBasePath(import.meta.env?.BASE_URL || '/');
const externalUrl = value => /^(?:https?:)?\/\//i.test(value) || /^(?:data:|blob:)/i.test(value);

// Both old API responses (/reports/...) and prefixed responses are supported.
export function withBasePath(path, base = APP_BASE) {
  const value = String(path || '');
  if (externalUrl(value) || value.startsWith('#')) return value;
  const prefix = normalizeBasePath(base).replace(/\/$/, '');
  const absolute = `/${value.replace(/^\/+/, '')}`;
  if (!prefix || absolute === prefix || absolute.startsWith(`${prefix}/`) || absolute.startsWith(`${prefix}?`) || absolute.startsWith(`${prefix}#`)) return absolute;
  return `${prefix}${absolute}`;
}

export function assetUrl(reportBase, path, base = APP_BASE) {
  if (!path) return '';
  const value = String(path);
  if (externalUrl(value)) return value;
  const prefixedReports = withBasePath('/reports/', base);
  if (value.startsWith('/reports/') || value.startsWith(prefixedReports)) return withBasePath(value, base);
  const relative = value.replace(/^\/?review_out\//, '').replace(/^\/+/, '');
  return withBasePath(`${String(reportBase || '').replace(/\/$/, '')}/${relative}`, base);
}

export function loginRedirectUrl(loginUrl, currentUrl, base = APP_BASE) {
  if (typeof loginUrl !== 'string' || !loginUrl.trim()) return '';
  try {
    const current = new URL(currentUrl);
    const login = new URL(loginUrl, current.origin);
    if (!['http:', 'https:'].includes(current.protocol) || login.origin !== current.origin || login.username || login.password || login.pathname !== '/login') return '';
    const appBase = normalizeBasePath(base);
    const withinApp = appBase === '/' || current.pathname === appBase.slice(0, -1) || current.pathname.startsWith(appBase);
    const decodedPath = decodeURIComponent(current.pathname);
    const safePath = !decodedPath.startsWith('//') && !/[\\\r\n]/.test(decodedPath);
    const next = withinApp && safePath ? current.pathname + current.search + current.hash : appBase;
    // The host login contract accepts only this locally reconstructed return path.
    const target = new URL('/login', current.origin);
    target.searchParams.set('next', next);
    return target.pathname + target.search;
  } catch {
    return '';
  }
}
