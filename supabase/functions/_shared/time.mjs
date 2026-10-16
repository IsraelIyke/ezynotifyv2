export function validTimezone(value) {
  if(typeof value!=='string'||!value.trim()||value.length>80)return null;
  try {return new Intl.DateTimeFormat('en',{timeZone:value.trim()}).resolvedOptions().timeZone;}
  catch {return null;}
}

export function formatTimestamp(value,timezone='UTC') {
  if(!value)return 'Not yet';
  const date=new Date(value);
  if(Number.isNaN(date.getTime()))return 'Not available';
  const zone=validTimezone(timezone)||'UTC';
  return new Intl.DateTimeFormat('en-GB',{timeZone:zone,year:'numeric',month:'short',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23',timeZoneName:'short'}).format(date)+` (${zone})`;
}

export function formatInterval(value) {
  let minutes=Math.max(1,Math.floor(Number(value)||10));
  const units=[];
  for(const [size,label] of [[1440,'day'],[60,'hour'],[1,'minute']]) {
    const count=Math.floor(minutes/size);minutes%=size;
    if(count)units.push(`${count} ${label}${count===1?'':'s'}`);
  }
  return units.join(' ');
}
