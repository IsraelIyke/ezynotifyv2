export function envRequired(env, name) {
  const value = env(name);
  if (!value) throw new Error(`Missing setting: ${name}`);
  return value;
}

export function createDatabase(url, key, fetcher = fetch) {
  return {
    async request(path, { method = 'GET', body, prefer } = {}) {
      const response = await fetcher(`${url}/rest/v1/${path}`, {
        method,
        headers: { apikey: key, Authorization: `Bearer ${key}`, 'Content-Type': 'application/json',
          ...(prefer ? { Prefer: prefer } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: AbortSignal.timeout(12000),
      });
      if (!response.ok) throw new Error(`Database request failed (${response.status})`);
      const text = await response.text();
      return text ? JSON.parse(text) : null;
    },
    rpc(name, body) { return this.request(`rpc/${name}`, { method: 'POST', body }); },
  };
}

export function createTelegram(token, fetcher = fetch) {
  return async (method, body) => {
    const response = await fetcher(`https://api.telegram.org/bot${token}/${method}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      signal: AbortSignal.timeout(12000),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      // Never surface token-bearing request URLs or raw third-party error bodies.
      if (method === 'editMessageText' && data.description?.includes('message is not modified')) return null;
      throw new Error(`Telegram ${method} failed (${data.error_code || response.status})`);
    }
    return data.result;
  };
}

export async function secureEquals(a, b) {
  if (!a || !b) return false;
  const digest = async (s) => new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)));
  const [x,y] = await Promise.all([digest(a),digest(b)]);
  let difference = 0;
  for (let i=0;i<x.length;i++) difference |= x[i]^y[i];
  return difference === 0;
}

export async function dispatchChecks(db, env, fetcher = fetch) {
  const now = new Date().toISOString();
  const [due, pending] = await Promise.all([
    db.request(`ezynotify?select=id&paused=eq.false&completed=eq.false&next_check_at=lte.${encodeURIComponent(now)}&limit=1`),
    db.request(`notification_outbox?select=id&state=eq.pending&next_attempt_at=lte.${encodeURIComponent(now)}&limit=1`),
  ]);
  if (!due?.length && !pending?.length) return { status: 'idle' };
  const active=await db.request(`runtime_locks?name=eq.worker&expires_at=gt.${encodeURIComponent(now)}&select=name&limit=1`);
  if(active?.length)return {status:'queued'};
  const owner = crypto.randomUUID();
  if (!await db.rpc('acquire_runtime_lock', { p_name: 'dispatch', p_owner: owner, p_seconds: 60 })) return { status: 'queued' };
  try {
    const repository = envRequired(env, 'GITHUB_REPOSITORY');
    if (!/^[\w.-]+\/[\w.-]+$/.test(repository)) throw new Error('Invalid repository setting');
    const workflow = env('GITHUB_WORKFLOW') || 'ezynotify.yml';
    const response = await fetcher(`https://api.github.com/repos/${repository}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${envRequired(env, 'GITHUB_DISPATCH_TOKEN')}`, Accept: 'application/vnd.github+json', 'Content-Type': 'application/json', 'X-GitHub-Api-Version': '2022-11-28' },
      body: JSON.stringify({ ref: env('GITHUB_REF') || 'main' }), signal: AbortSignal.timeout(12000),
    });
    if (!response.ok) throw new Error(`Workflow dispatch failed (${response.status})`);
    return { status: 'dispatched' };
  } catch (error) {
    await db.request(`runtime_locks?name=eq.dispatch&owner=eq.${owner}`, { method: 'DELETE' });
    throw error;
  }
}
