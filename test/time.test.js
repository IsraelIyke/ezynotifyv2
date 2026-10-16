import {test} from 'node:test';
import assert from 'node:assert/strict';
import {formatInterval,formatTimestamp,validTimezone} from '../supabase/functions/_shared/time.mjs';
test('duration formatting handles minutes, hours, days and mixed values',()=>{
  for(const [input,output] of [[10,'10 minutes'],[60,'1 hour'],[90,'1 hour 30 minutes'],[1440,'1 day'],[1500,'1 day 1 hour'],[2880,'2 days']])assert.equal(formatInterval(input),output);
});
test('timestamps respect local day changes and daylight saving',()=>{
  assert.match(formatTimestamp('2026-01-16T12:00:00Z','America/New_York'),/07:00/);
  assert.match(formatTimestamp('2026-07-16T12:00:00Z','America/New_York'),/08:00/);
  assert.match(formatTimestamp('2026-10-16T23:30:00Z','Africa/Lagos'),/17 Oct 2026.*00:30/);
  assert.equal(validTimezone('not-a-zone'),null);
  assert.equal(formatTimestamp(null,'UTC'),'Not yet');
});
