-- Keep explicit operator consent while waiting for worker/delivery locks.
alter table public.privacy_requests
  add column erase_queued_at timestamptz,
  add column erase_queued_by text;

alter function public.privacy_action(bigint,text,text,text,text,text) rename to privacy_action_immediate;

create function public.privacy_action(p_id bigint,p_actor text,p_action text,p_note text default '',p_token text default '',p_event text default '') returns jsonb
language plpgsql security definer set search_path=public as $$
declare result jsonb; r privacy_requests;
begin
  if not privacy_admin_allowed(p_actor) then raise exception 'Not authorized'; end if;
  select * into r from privacy_requests where id=p_id for update;
  if not found then return jsonb_build_object('error','missing'); end if;
  if p_action='cancel_erasure' then
    update privacy_requests set erase_queued_at=null,erase_queued_by=null,erase_token=null,erase_expires_at=null where id=p_id;
    insert into privacy_audit(request_id,actor,action) values(p_id,p_actor,'erasure_cancelled');
    return jsonb_build_object('cancelled',true);
  end if;
  if p_action='erase' then
    -- Validate consent before allowing any busy response to become queued work.
    if r.kind<>'deletion' then return jsonb_build_object('error','not_deletion'); end if;
    if r.status='completed' then return jsonb_build_object('error','closed'); end if;
    if r.active_erased_at is not null and r.erase_token is null then
      return privacy_action_immediate(p_id,p_actor,p_action,p_note,p_token,p_event);
    end if;
    if r.erase_token is null or r.erase_token::text<>p_token or r.erase_expires_at<now() then
      return jsonb_build_object('error','expired_confirmation');
    end if;
    if exists(select 1 from privacy_requests where chat_id=r.chat_id and kind='access' and status<>'completed') then
      return jsonb_build_object('error','access_pending');
    end if;
  end if;
  result=privacy_action_immediate(p_id,p_actor,p_action,p_note,p_token,p_event);
  if p_action='erase' and result->>'error' in ('worker_busy','chat_busy','privacy_delivery_busy') then
    update privacy_requests set erase_queued_at=coalesce(erase_queued_at,now()),erase_queued_by=p_actor,
      erase_expires_at=coalesce(erase_queued_at,now())+interval '24 hours',status='in_review' where id=p_id;
    insert into privacy_audit(request_id,actor,action,details)
      values(p_id,p_actor,'erasure_queued',jsonb_build_object('reason',result->>'error'));
    return jsonb_build_object('queued',true);
  end if;
  if (p_action='preview' and not result?'error') or result->>'erased'='true' or p_action in ('complete','decline') and not result?'error' then
    update privacy_requests set erase_queued_at=null,erase_queued_by=null where id=p_id;
  end if;
  return result;
end $$;

create function public.process_privacy_erasures(p_limit integer default 5) returns jsonb
language plpgsql security definer set search_path=public as $$
declare r privacy_requests; result jsonb; erased integer:=0; waiting integer:=0;
begin
  for r in select * from privacy_requests where erase_queued_at is not null
    order by erase_queued_at limit greatest(1,least(p_limit,20)) for update skip locked
  loop
    if r.status='completed' or r.erase_expires_at is null or r.erase_expires_at<now() or not privacy_admin_allowed(r.erase_queued_by) then
      result=jsonb_build_object('error','expired_confirmation');
    else
      result=privacy_action_immediate(r.id,r.erase_queued_by,'erase','',r.erase_token::text,'queued-erasure');
    end if;
    if result->>'error' in ('worker_busy','chat_busy','privacy_delivery_busy') then
      waiting=waiting+1;
    else
      update privacy_requests set erase_queued_at=null,erase_queued_by=null where id=r.id;
      if result->>'erased'='true' then erased=erased+1;
      else insert into privacy_audit(request_id,actor,action,details)
        values(r.id,coalesce(r.erase_queued_by,'system'),'erasure_queue_stopped',result);
      end if;
      insert into privacy_outbox(request_id,destination,chat_id,payload)
        select r.id,'admin',s.chat_id,jsonb_build_object('text',
          case when result->>'erased'='true' then 'Active data erased for privacy request #'||r.id::text||'. Open it to finish the backup/provider checklist.'
          else 'Queued deletion stopped for privacy request #'||r.id::text||'. Open it to review and confirm again.' end,
          'reply_markup',jsonb_build_object('inline_keyboard',jsonb_build_array(jsonb_build_array(jsonb_build_object('text','Open request','callback_data','pv:view:'||r.id::text)))))
        from privacy_admin_settings s where s.singleton and s.enabled;
    end if;
  end loop;
  return jsonb_build_object('erased',erased,'waiting',waiting);
end $$;

revoke all on function public.privacy_action(bigint,text,text,text,text,text),public.process_privacy_erasures(integer) from public,anon,authenticated;
grant execute on function public.privacy_action(bigint,text,text,text,text,text),public.process_privacy_erasures(integer) to service_role;
