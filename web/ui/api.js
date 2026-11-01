const BASE = '/asset-library/api';
const pending = new Set();
let token = '', bootstrapPromise = null;

async function bootstrap(signal) {
  if (token) return token;
  if (!bootstrapPromise) bootstrapPromise = fetch(BASE + '/bootstrap', {signal, cache:'no-store'})
    .then(async response => {
      const data = await response.json();
      if (!response.ok || !data.token) throw new Error(data.error || '资产库连接失败');
      token = data.token; return token;
    }).finally(() => { bootstrapPromise = null; });
  return bootstrapPromise;
}

export async function request(path, options = {}) {
  const controller = new AbortController();
  pending.add(controller);
  const relay = () => controller.abort();
  options.signal?.addEventListener('abort', relay, {once:true});
  if (options.signal?.aborted) controller.abort();
  try {
    const method = options.method || 'GET';
    const mutation = !['GET','HEAD'].includes(method);
    for (let attempt = 0; attempt < 2; attempt++) {
      const headers = new Headers(options.headers || {});
      if (mutation) headers.set('X-Asset-Token', await bootstrap(controller.signal));
      let body = options.body;
      if (body && !(body instanceof FormData)) {
        headers.set('Content-Type', 'application/json'); body = JSON.stringify(body);
      }
      const response = await fetch(BASE + path, {...options, method, headers, body, signal:controller.signal, cache:'no-store'});
      let data;
      try { data = await response.json(); } catch { data = {}; }
      if (response.status === 401 && mutation && attempt === 0) { token = ''; continue; }
      if (!response.ok) {
        const error = new Error(data.error?.message || data.error || data.message || `请求失败（${response.status}）`);
        error.status = response.status; error.data = data; throw error;
      }
      return data;
    }
  } finally {
    options.signal?.removeEventListener('abort', relay); pending.delete(controller);
  }
}

export function abortRequests() { for (const controller of pending) controller.abort(); pending.clear(); }
export function isAbort(error) { return error?.name === 'AbortError'; }
export const apiBase = BASE;
