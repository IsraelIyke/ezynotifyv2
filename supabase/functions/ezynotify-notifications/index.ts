import { deliverAdminNotifications } from '../_shared/admin-notifications.mjs';
import { createDatabase,createTelegram,envRequired,secureEquals } from '../_shared/services.mjs';
const env=(name:string)=>Deno.env.get(name);
Deno.serve(async(req:Request)=>{
  if(req.method!=='POST')return new Response('Method not allowed',{status:405});
  if(!await secureEquals(req.headers.get('x-dispatch-secret'),env('DISPATCH_SECRET')))return new Response('Unauthorized',{status:401});
  try {
    const db=createDatabase(envRequired(env,'SUPABASE_URL'),envRequired(env,'SUPABASE_SERVICE_ROLE_KEY'));
    return Response.json(await deliverAdminNotifications({db,telegram:createTelegram(envRequired(env,'ADMIN_BOT_TOKEN'))}));
  } catch {
    console.error('Admin notification delivery could not complete.');
    return Response.json({error:'Delivery unavailable'},{status:502});
  }
});
