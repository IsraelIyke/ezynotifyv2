import { runAI } from '../_shared/ai.mjs';
import { secureEquals } from '../_shared/services.mjs';
const env=(name:string)=>Deno.env.get(name);
Deno.serve(async(req:Request)=>{
  if(req.method!=='POST')return new Response('Method not allowed',{status:405});
  if(!await secureEquals(req.headers.get('x-ai-secret'),env('MONITOR_AI_SECRET')))return new Response('Unauthorized',{status:401});
  try {
    const raw=await req.text();if(raw.length>2700000)return new Response('Payload too large',{status:413});
    return Response.json(await runAI(JSON.parse(raw),env));
  } catch {
    console.error('Monitor AI request failed; check Groq settings or limits.');
    return Response.json({error:'AI unavailable'},{status:502});
  }
});
