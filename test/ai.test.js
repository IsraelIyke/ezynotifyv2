import {test} from 'node:test';
import assert from 'node:assert/strict';
import {runAI} from '../supabase/functions/_shared/ai.mjs';

test('summarizer reads the current model secret on every call and treats excerpts as data',async()=>{
  const settings={GROQ_API_KEY:'test-key',GROQ_MODEL:'model-one'};const models=[];
  const fetcher=async(url,options)=>{
    assert.equal(url,'https://api.groq.com/openai/v1/chat/completions');
    const body=JSON.parse(options.body);models.push(body.model);
    assert.match(body.messages[0].content,/untrusted data/);
    return {ok:true,json:async()=>({choices:[{message:{content:'Price changed from 10 to 12.'}}]})};
  };
  const input={action:'summarize',changes:[{removed:'10',added:'12'}]};
  assert.equal((await runAI(input,k=>settings[k],fetcher)).source,'groq');
  settings.GROQ_MODEL='model-two';await runAI(input,k=>settings[k],fetcher);
  assert.deepEqual(models,['model-one','model-two']);
});

test('vision classification requires confidence and never treats AI as proof of success',async()=>{
  const env=k=>({GROQ_API_KEY:'test-key',GROQ_VISION_MODEL:'vision-model'}[k]);
  const respond=(status,confidence)=>async()=>({ok:true,json:async()=>({choices:[{message:{content:JSON.stringify({status,confidence})}}]})});
  const input={action:'classify',text:'Page'};
  assert.equal((await runAI(input,env,respond('challenge',0.95))).status,'challenge');
  assert.equal((await runAI(input,env,respond('challenge',0.4))).status,null);
  assert.equal((await runAI(input,env,respond('content',1))).status,null);
  assert.deepEqual(await runAI(input,()=>null),{status:null});
});
