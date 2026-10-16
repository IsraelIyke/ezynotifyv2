import { createDatabase, dispatchChecks, envRequired, secureEquals } from '../_shared/services.mjs';

const env = (name: string) => Deno.env.get(name);
Deno.serve(async (req: Request) => {
  if (req.method !== 'POST') return new Response('Method not allowed', { status: 405 });
  if (!await secureEquals(req.headers.get('x-dispatch-secret'), env('DISPATCH_SECRET'))) return new Response('Unauthorized', { status: 401 });
  try {
    const db = createDatabase(envRequired(env, 'SUPABASE_URL'), envRequired(env, 'SUPABASE_SERVICE_ROLE_KEY'));
    return Response.json(await dispatchChecks(db, env));
  } catch {
    console.error('Check dispatch failed; verify service credentials and GitHub Actions access.');
    return Response.json({ error: 'Unable to schedule checks' }, { status: 502 });
  }
});
