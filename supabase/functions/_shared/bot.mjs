export const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
const q = encodeURIComponent;
const button = (text, callback_data, style) => ({ text, callback_data, ...(style ? { style } : {}) });
const homeButtons = () => [[button('✨ New monitor', 'new', 'primary')], [button('📋 My monitors', 'list:0'), button('💡 Help', 'help')]];
const backHome = () => [[button('🏠 Dashboard', 'home')]];
const statusIcon = (m) => m.paused ? '⏸' : m.completed ? '🏁' : ['challenge','blocked','robots_denied'].includes(m.last_status) ? '🛡' : m.failure_count ? '⚠️' : '🟢';
const stamp = (s) => s ? new Date(s).toISOString().replace('T',' ').slice(0,16)+' UTC' : 'Not yet';
const siteName = (url) => { try { return new URL(url).hostname; } catch { return 'Website monitor'; } };

export function normalizeUrl(input) {
  const url = new URL(/^https?:\/\//i.test(input) ? input : `https://${input}`);
  if (!['http:','https:'].includes(url.protocol) || url.username || url.password || (url.port && !['80','443'].includes(url.port))) throw new Error('Use a public HTTP or HTTPS URL without credentials or custom ports.');
  const host = url.hostname.toLowerCase().replace(/\.$/, '');
  if (!host.includes('.') || host === 'localhost' || host.endsWith('.local') || host.endsWith('.internal') || /^\[/.test(host) || /^\d+(\.\d+){3}$/.test(host)) throw new Error('Use a public website hostname, not a local address or IP.');
  if (url.href.length > 1800) throw new Error('That URL is too long. Please use a shorter page URL.');
  url.hash = '';
  return url.href;
}

export function createBot({ db, telegram, dispatch, now = () => new Date() }) {
  async function session(chat) {
    const rows = await db.request(`bot_sessions?chat_id=eq.${q(chat)}&expires_at=gt.${q(now().toISOString())}&select=draft`);
    return rows?.[0]?.draft || null;
  }
  async function save(chat, draft) {
    await db.request('bot_sessions?on_conflict=chat_id', { method:'POST', prefer:'resolution=merge-duplicates', body: { chat_id:chat,draft,expires_at:new Date(now().getTime()+1800000).toISOString() } });
  }
  const clear = (chat) => db.request(`bot_sessions?chat_id=eq.${q(chat)}`, { method:'DELETE' });
  async function owned(chat,id) {
    if (!/^[\w-]{1,40}$/.test(id)) return null;
    const rows = await db.request(`ezynotify?id=eq.${q(id)}&telegramID=eq.${q(chat)}&limit=1`);
    return rows?.[0] || null;
  }
  async function screen(chat, text, rows = backHome(), messageId) {
    text=text.replace(/\u2014/g,'-');
    rows=rows.map(row=>row.map(b=>({...b,text:b.text.replace(/\u2014/g,'-')})));
    const payload = { chat_id:chat,text,parse_mode:'HTML',link_preview_options:{is_disabled:true},reply_markup:{inline_keyboard:rows} };
    if (messageId) {
      try { return await telegram('editMessageText', { ...payload,message_id:messageId }); }
      catch { /* Older/removed messages can be replaced with a fresh dashboard. */ }
    }
    return telegram('sendMessage', payload);
  }
  async function dashboard(chat, mid) {
    await clear(chat);
    return screen(chat, '🔔 <b>EzyNotify</b>\n<i>Your websites. Your updates. Less checking.</i>\n\nWatch a page for changes or keywords. Get clear AI summaries when something changes.\n\nChoose where to start 👇', homeButtons(), mid);
  }
  async function list(chat,page=0,mid) {
    page = Math.max(0, Math.min(Number(page)||0,1000));
    const rows = await db.request(`ezynotify?telegramID=eq.${q(chat)}&order=created_at.desc&offset=${page*6}&limit=7`);
    if (!rows?.length) return screen(chat, '📋 <b>Your monitors</b>\n\nNo monitors here yet. Add a public page and let EzyNotify keep an eye on it.', homeButtons(), mid);
    const items = rows.slice(0,6);
    const buttons = items.map(m => [button(`${statusIcon(m)} ${m.checkUpdates?'🔄':'🔎'} ${(m.title || siteName(m.url)).slice(0,40)}`, `view:${m.id}`)]);
    const nav=[];
    if (page) nav.push(button('← Previous',`list:${page-1}`));
    if (rows.length>6) nav.push(button('Next →',`list:${page+1}`));
    if (nav.length) buttons.push(nav);
    buttons.push([button('✨ Add monitor','new','primary'),button('🏠 Dashboard','home')]);
    return screen(chat, `📋 <b>Your monitors</b> · Page ${page+1}\n\n🟢 Watching · ⏸ Paused · 🛡 Access blocked · 🏁 Finished\n\nTap a monitor to see its status and controls.`, buttons, mid);
  }
  async function detail(chat,id,mid) {
    const m = await owned(chat,id);
    if (!m) return screen(chat,'That monitor is unavailable. Open your list to choose another.',[[button('📋 My monitors','list:0')]],mid);
    const keywords = m.keywords?.keywords || [];
    const actions = [[button('🔄 Check now',`check:${id}`,'primary'),button(m.paused||m.completed?'▶️ Resume':'⏸ Pause',`toggle:${id}`)],
      [button('✏️ Edit',`edit:${id}`),button('🗑 Delete',`delete:${id}`,'danger')],
      [button('📜 Recent activity',`history:${id}`),button('← My monitors','list:0')]];
    return screen(chat, `${statusIcon(m)} <b>${escapeHtml(m.title || siteName(m.url))}</b>\n${escapeHtml(m.url)}\n\n<b>Watching:</b> ${m.checkUpdates?'Content changes':'Keywords'}\n${keywords.length?`<b>Keywords:</b> ${escapeHtml(keywords.join(', '))}\n`:''}<b>Schedule:</b> Every ${m.interval_minutes || 10} minutes\n<b>Status:</b> ${escapeHtml(m.paused?'paused':m.completed?'finished':m.last_status)}\n<b>Last check:</b> ${stamp(m.last_checked_at)}\n<b>Last success:</b> ${stamp(m.last_success_at)}\n<b>Next check:</b> ${m.paused||m.completed?'Paused':stamp(m.next_check_at)}\n<b>Source:</b> ${escapeHtml(m.fetch_config?.mode || 'browser')}${m.fetch_config?.selector?` · <code>${escapeHtml(m.fetch_config.selector)}</code>`:''}\n\n${m.last_error?`⚠️ ${escapeHtml(m.last_error.slice(0,300))}\n\n`:''}<i>Check now requests a background job; startup can take a few minutes.</i>`,actions,mid);
  }
  function draftButtons(d, rows) {
    return rows.map(row=>row.map(([text,value,style])=>button(text,`draft:${d.nonce}:${value}`,style)));
  }
  async function review(chat,d,mid) {
    d.step='review'; await save(chat,d);
    const c=d.fetch_config;
    return screen(chat, `✅ <b>Review your monitor</b>\n\n<b>Name:</b> ${escapeHtml(d.title)}\n<b>Page:</b> ${escapeHtml(d.url)}\n<b>Watch:</b> ${d.checkUpdates?'Content changes':escapeHtml(d.keywords.keywords.join(', '))}\n<b>Frequency:</b> Every ${d.interval_minutes} minutes\n<b>After an alert:</b> ${d.shouldContinueCheck?'Keep watching':'Finish this monitor'}\n<b>Alerts:</b> ${d.shouldSendDetailedUpdates?'AI summary + change excerpts':'AI summary'}\n<b>Source:</b> ${escapeHtml(c.mode)}\n<b>Target:</b> ${escapeHtml(c.selector || 'Whole page')}\n<b>Scroll:</b> ${c.scroll?'Enabled':'Off'}\n\n<i>Only public pages are supported. Challenge pages are reported without replacing your saved content. Change alerts include an AI summary of the detected text.</i>`,
      draftButtons(d,[[['💚 Save monitor','confirm','success']],[['⚙️ Advanced settings','advanced']],[['✖️ Discard draft','cancel']]]),mid);
  }
  async function prompt(chat,d,mid) {
    const cancel = draftButtons(d,[[['✖️ Discard draft','cancel']]]);
    const choices = (rows) => draftButtons(d,rows).concat(cancel);
    switch(d.step) {
      case 'url': return screen(chat,'✨ <b>New monitor · 1 of 5</b>\n\nSend the public page URL you want to watch.\n\nExample: <code>https://example.com/news</code>',cancel,mid);
      case 'keywords': return screen(chat,'🔎 <b>Which keywords?</b>\n\nSend words or phrases separated by commas. Matches ignore capitalization.\n\nExample: <code>applications open, admission list</code>',cancel,mid);
      case 'title': return screen(chat,'🏷 <b>Give it a name · 2 of 5</b>\n\nSend a short name such as “University admissions”.', choices([[['Use website name','default-title']]]),mid);
      case 'interval': return screen(chat,'⏱ <b>How often? · 3 of 5</b>\n\nChoose a check interval. Jobs may start later when the runner is busy.', choices([[['10 minutes','interval-10'],['30 minutes','interval-30']],[['1 hour','interval-60'],['6 hours','interval-360']],[['Daily','interval-1440']]]),mid);
      case 'repeat': return screen(chat,'🔁 <b>After an alert? · 4 of 5</b>\n\nKeep watching for future changes, or finish after the first result? Keyword alerts repeat only after a keyword disappears and appears again.',choices([[['Keep watching','repeat-yes','primary']],[['Finish after first result','repeat-no']]]),mid);
      case 'detail': return screen(chat,'🧠 <b>Choose your alert · 5 of 5</b>\n\nEvery change alert includes an AI summary. Include short added/removed excerpts too?',choices([[['Summary + excerpts','detail-yes','primary']],[['Summary only','detail-no']]]),mid);
      case 'advanced': return screen(chat,'⚙️ <b>Advanced settings</b>\n\nBrowser rendering handles JavaScript. HTML is faster for static pages. Feed/API mode reads RSS, Atom, or JSON.\n\nA CSS target limits monitoring to a page section. An iframe selector selects a frame first. Ignored selectors exclude noisy elements. Scrolling is bounded to five steps.', choices([[['🌐 Browser','mode-browser'],['📄 HTML','mode-html']],[['📡 Feed / API','mode-feed']],[['🎯 CSS target','set-selector'],['🧩 Iframe','set-frame']],[['🙈 Ignore regions','set-ignore'],[`${d.fetch_config.scroll?'✅':'⬜'} Scroll`,'scroll']],[['↩️ Review','review','primary']]]),mid);
      case 'selector': case 'frame': case 'ignore': return screen(chat,`🎯 <b>${d.step==='selector'?'Target CSS selector':d.step==='frame'?'Iframe CSS selector':'Ignored CSS selectors'}</b>\n\n${d.step==='ignore'?'Send a comma-separated CSS selector list, e.g. <code>nav, footer, .timestamp</code>.':'Send one CSS selector, e.g. <code>main</code> or <code>iframe#news</code>.'}\n\nSend <code>/skip</code> to clear this setting.`,cancel,mid);
      default: return review(chat,d,mid);
    }
  }
  async function begin(chat,type,mid,existing) {
    if(!existing) {
      const count=await db.request(`ezynotify?telegramID=eq.${q(chat)}&select=id&limit=50`);
      if(count?.length>=50)return screen(chat,'📋 You have reached the limit of 50 monitors. Delete a monitor before adding another.',[[button('📋 My monitors','list:0')]],mid);
    }
    const d = { step:'url',nonce:crypto.randomUUID().slice(0,8),checkUpdates:type==='updates',title:'',interval_minutes:10,shouldContinueCheck:true,shouldSendDetailedUpdates:true,keywords:{keywords:[]},fetch_config:{mode:'browser',scroll:false}, ...(existing?{
      id:String(existing.id),url:existing.url,title:existing.title||siteName(existing.url),checkUpdates:existing.checkUpdates,
      keywords:existing.keywords||{keywords:[]},interval_minutes:existing.interval_minutes,shouldContinueCheck:existing.shouldContinueCheck,
      shouldSendDetailedUpdates:existing.shouldSendDetailedUpdates,fetch_config:existing.fetch_config||{mode:'browser'},step:'review',
    }:{}) };
    await save(chat,d);
    if(existing) return screen(chat,'✏️ <b>Edit monitor</b>\n\nChanges stay in a draft until you save. Choose a field:',draftButtons(d,[[['🔗 Page URL','edit-url'],['🏷 Name','edit-title']],[['⏱ Frequency','edit-interval'],['🔁 Repeat','edit-repeat']],[['🧠 Alert detail','edit-detail'],['⚙️ Advanced','advanced']],...(!d.checkUpdates?[[['🔎 Keywords','edit-keywords']]]:[]),[['✅ Review & save','review','success']],[['✖️ Discard draft','cancel']]]),mid);
    return prompt(chat,d,mid);
  }
  async function commit(chat,d,mid) {
    const original=d.id?await owned(chat,d.id):null;
    if(d.id&&!original) return screen(chat,'This monitor no longer exists.',backHome(),mid);
    if(!d.url||!d.title||(!d.checkUpdates&&!d.keywords.keywords.length)) throw new Error('Incomplete draft');
    const fields = {url:d.url,title:d.title,checkUpdates:d.checkUpdates,keywords:d.checkUpdates?null:d.keywords,interval_minutes:d.interval_minutes,
      shouldContinueCheck:d.shouldContinueCheck,shouldSendDetailedUpdates:d.shouldSendDetailedUpdates,fetch_config:d.fetch_config,
      completed:false,next_check_at:now().toISOString()};
    if(original && (original.url!==d.url||JSON.stringify(original.fetch_config)!==JSON.stringify(d.fetch_config))) fields.reference='';
    if(original && JSON.stringify(original.keywords)!==JSON.stringify(fields.keywords)) fields.keywords_seen=[];
    const id=await db.rpc('commit_bot_draft',{p_chat:chat,p_nonce:d.nonce,p_fields:fields,p_id:d.id||null});
    let dispatchFailed=false;
    try { await dispatch(); } catch { dispatchFailed=true; }
    return screen(chat, `💚 <b>${d.id?'Changes saved':'Your monitor is ready'}</b>\n\n${escapeHtml(d.title)}\n${dispatchFailed?'The monitor is saved. Automatic scheduling will retry the background check.':'The first check is queued. We’ll establish a baseline before reporting page changes.'}`,[[button('👀 View monitor',`view:${id}`,'primary')],...homeButtons()],mid);
  }
  async function draftAction(chat,nonce,action,mid) {
    const d=await session(chat);
    if(!d||d.nonce!==nonce) return screen(chat,'⌛ This draft expired or was replaced. Your saved monitors are unchanged.',homeButtons(),mid);
    if(action==='cancel') { await clear(chat); return screen(chat,'Draft discarded. Your saved monitors are unchanged.',homeButtons(),mid); }
    if(action==='confirm'&&d.step==='review') return commit(chat,d,mid);
    if(action==='review') return review(chat,d,mid);
    if(action==='advanced') d.step='advanced';
    else if(action.startsWith('edit-')) d.step=action.slice(5);
    else if(action==='default-title'&&d.step==='title') { d.title=new URL(d.url).hostname; d.step=d.id?'review':'interval'; }
    else if(action.startsWith('interval-')&&d.step==='interval') {
      const n=Number(action.slice(9)); if(![10,30,60,360,1440].includes(n)) return;
      d.interval_minutes=n;d.step=d.id?'review':'repeat';
    } else if(action.startsWith('repeat-')&&d.step==='repeat') { d.shouldContinueCheck=action==='repeat-yes';d.step=d.id?'review':'detail'; }
    else if(action.startsWith('detail-')&&d.step==='detail') { d.shouldSendDetailedUpdates=action==='detail-yes';d.step='review'; }
    else if(action.startsWith('mode-')&&d.step==='advanced') {
      const mode=action.slice(5);if(!['browser','html','feed'].includes(mode)) return;
      d.fetch_config.mode=mode;
      if(mode!=='browser') {d.fetch_config.scroll=false;delete d.fetch_config.frame;}
    } else if(action==='scroll'&&d.step==='advanced') { d.fetch_config.scroll=!d.fetch_config.scroll;d.fetch_config.mode='browser'; }
    else if(action.startsWith('set-')&&d.step==='advanced') d.step=action.slice(4);
    else if(!['advanced'].includes(action)&&!action.startsWith('edit-')) return screen(chat,'This step has already been completed. Continue with your current draft.',draftButtons(d,[[['Continue','review']]]),mid);
    await save(chat,d); return prompt(chat,d,mid);
  }
  async function textInput(chat,text) {
    const d=await session(chat);
    if(!d) return dashboard(chat);
    if(d.step==='url') {
      try { d.url=normalizeUrl(text); } catch(error) { return screen(chat,`🔗 ${escapeHtml(error.message)}`,draftButtons(d,[[['✖️ Discard draft','cancel']]])); }
      d.step=d.id?'review':d.checkUpdates?'title':'keywords';
    } else if(d.step==='keywords') {
      const keywords=[...new Set(text.split(',').map(s=>s.trim().toLowerCase()).filter(Boolean))];
      if(!keywords.length||keywords.length>20||keywords.some(s=>s.length>100)) return screen(chat,'Please send 1–20 keywords, each at most 100 characters, separated by commas.');
      d.keywords={keywords};d.step=d.id?'review':'title';
    } else if(d.step==='title') {
      if(!text||text.length>60) return screen(chat,'Please use a name with 1–60 characters.');
      d.title=text;d.step=d.id?'review':'interval';
    } else if(['selector','frame','ignore'].includes(d.step)) {
      if(text.length>400) return screen(chat,'Please keep selectors below 400 characters.');
      const field=d.step; d.fetch_config[field]=text==='/skip'?'':text;
      if(field==='frame'&&text!=='/skip')d.fetch_config.mode='browser';
      d.step='advanced';
    } else return prompt(chat,d);
    await save(chat,d);return prompt(chat,d);
  }
  return async function handle(update) {
    const callback=update.callback_query;
    const message=callback?.message || update.message;
    const chat=message?.chat;
    if(!chat||chat.type!=='private'||String(callback?.from?.id||message.from?.id)!==String(chat.id)) return;
    const chatId=String(chat.id), mid=callback?message.message_id:undefined;
    if(callback) await telegram('answerCallbackQuery',{callback_query_id:callback.id});
    const text=update.message?.text?.trim();
    const command=text?.split(/\s/)[0]?.split('@')[0];
    let action=callback?.data;
    if(!action) {
      if(['/start','/menu'].includes(command))action='home';
      else if(command==='/help')action='help';
      else if(command==='/cancel') {await clear(chatId);return screen(chatId,'Draft discarded. Your saved monitors are unchanged.',homeButtons());}
      else if(['/list_update_requests','/list_keyword_check_requests','/monitors'].includes(command))action='list:0';
      else if(command==='/new_update_monitor')return begin(chatId,'updates');
      else if(command==='/new_keyword_check')return begin(chatId,'keywords');
      else if(command==='/new')action='new';
      else if(text)return textInput(chatId,text);
      else return;
    }
    if(action==='home')return dashboard(chatId,mid);
    if(action==='help')return screen(chatId,'💡 <b>Your monitoring guide</b>\n\n<b>1.</b> Add a public page.\n<b>2.</b> Choose content changes or keywords.\n<b>3.</b> Pick a schedule and save.\n\n🧠 AI summarizes detected changes. If AI is unavailable, you still get a factual change excerpt.\n🛡 A CAPTCHA or access block gets a notice, not a false content alert. The last good snapshot stays safe.\n🔎 Keywords alert on first appearance, then re-arm after disappearing.\n⏱ Check now requests a job, subject to a two-minute cooldown.\n\nUse /cancel to discard an unfinished draft. Advanced settings support CSS targets, iframe targets, scrolling, HTML, feeds and JSON.\n\n<i>Only public pages are supported. Site restrictions and runner delays can prevent a check.</i>',homeButtons(),mid);
    if(action==='new')return screen(chatId,'✨ <b>What would you like to watch?</b>\n\n🔄 <b>Page changes</b> - announcements, prices, application pages.\n🔎 <b>Keywords</b> - get notified when a word or phrase appears.',[[button('🔄 Page changes','new:updates','primary')],[button('🔎 Keywords','new:keywords')],...backHome()],mid);
    if(action.startsWith('new:'))return begin(chatId,action.slice(4),mid);
    if(action.startsWith('draft:')) {const [,nonce,...parts]=action.split(':');return draftAction(chatId,nonce,parts.join(':'),mid);}
    if(action.startsWith('list:'))return list(chatId,action.slice(5),mid);
    const [verb,id]=action.split(':');
    const m=await owned(chatId,id||'');
    if(!m)return screen(chatId,'That monitor is unavailable.',[[button('📋 My monitors','list:0')]],mid);
    if(verb==='view')return detail(chatId,id,mid);
    if(verb==='edit')return begin(chatId,m.checkUpdates?'updates':'keywords',mid,m);
    if(verb==='toggle') {
      await db.request(`ezynotify?id=eq.${q(id)}&telegramID=eq.${q(chatId)}`,{method:'PATCH',body:{paused:!(m.paused||m.completed),completed:false,next_check_at:now().toISOString()}});
      return detail(chatId,id,mid);
    }
    if(verb==='delete')return screen(chatId,`🗑 <b>Delete ${escapeHtml(m.title||'this monitor')}?</b>\n\nThis stops future checks.`,[[button('Delete monitor',`remove:${id}`,'danger')],[button('Keep monitor',`view:${id}`)]],mid);
    if(verb==='remove') {
      await db.request(`ezynotify?id=eq.${q(id)}&telegramID=eq.${q(chatId)}`,{method:'DELETE'});
      await db.request(`notification_outbox?monitor_id=eq.${q(id)}&state=eq.pending`,{method:'PATCH',body:{state:'cancelled'}});
      return screen(chatId,'🗑 Monitor deleted.',[[button('📋 My monitors','list:0')],...homeButtons()],mid);
    }
    if(verb==='check') {
      const result=await db.rpc('request_monitor_check',{p_id:id,p_chat:chatId});
      const notices={inactive:'Resume this monitor before requesting a check.',cooldown:'⏳ A check was requested recently. Please wait two minutes.',missing:'That monitor is unavailable.'};
      let notice=notices[result]||'🔄 Check queued. We’ll notify you if there is a match or change.';
      if(result==='queued')try{await dispatch();}catch{notice='Your request is saved. The scheduler will retry starting the background job.';}
      return screen(chatId,notice,[[button('← Monitor',`view:${id}`)]],mid);
    }
    if(verb==='history') {
      const events=await db.request(`monitor_events?monitor_id=eq.${q(id)}&order=created_at.desc&limit=5`);
      return screen(chatId,`📜 <b>Recent activity</b>\n\n${events?.length?events.map(e=>`<b>${escapeHtml(e.status)}</b> · ${stamp(e.created_at)}\n${escapeHtml((e.summary||'').slice(0,350))}`).join('\n\n'):'No checks recorded yet.'}`,[[button('← Monitor',`view:${id}`)]],mid);
    }
  };
}
