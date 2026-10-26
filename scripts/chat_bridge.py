"""Bounded MCP transport for a chat agent; never invokes an inference API.

Run with one JSON request argument or feed JSON lines on stdin. __bootstrap
loads exact persona/behavior once and no chat history. Ordinary tools preserve
their complete JSON; oversized results are deferred to a local file, never ACKed.
"""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import sys
import tomllib

ROOT=Path(__file__).resolve().parents[1]


def payload(result):
    value=result.structuredContent
    if value is None:value=json.loads(next(c.text for c in result.content if c.type=='text'))
    paths=[]
    for block in result.content:
        if block.type!='image':continue
        raw=base64.b64decode(block.data,validate=True);digest=hashlib.sha256(raw).hexdigest()
        ext={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp','image/gif':'.gif'}.get(block.mimeType)
        if not ext:raise ValueError('Unsupported image representation')
        dest=ROOT/'.tmp/chat-bridge/images'/(digest+ext);dest.parent.mkdir(parents=True,exist_ok=True)
        if not dest.exists():dest.write_bytes(raw)
        paths.append(dict(path=str(dest),sha256=digest,mime_type=block.mimeType))
    if paths:value=dict(value,local_images=paths,image_instruction='用宿主view_image读取这些真实像素；路径不是已看图证明。')
    return value


def bounded(value,maximum=16000):
    text=json.dumps(value,ensure_ascii=False,separators=(',',':'))
    if len(text)<=maximum:return text
    raw=text.encode();digest=hashlib.sha256(raw).hexdigest()
    dest=ROOT/'.tmp/chat-bridge/results'/(digest+'.json');dest.parent.mkdir(parents=True,exist_ok=True)
    if not dest.exists():dest.write_bytes(raw)
    # Do not forward a continuation that might acknowledge unseen material.
    return json.dumps(dict(delivery='deferred_unread',characters=len(text),sha256=digest,path=str(dest),
        recovery='分段读取本机JSON原文；未读完不能确认消息、声称已缓存人格或根据未读内容执行操作。',ack_allowed=False),ensure_ascii=False)


def share_static(contexts):
    """Exact append-only sharing, never semantic summarization of a persona."""
    out={}
    for version,value in sorted(contexts.items(),key=lambda pair:len(pair[1]['persona'])):
        assert hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()==version
        base=next(((v,c) for v,c in out.items() if 'inherits_static_version' not in c and c['behavior']==value['behavior'] and value['persona'].startswith(c['persona'])),None)
        out[version]=(dict(inherits_static_version=base[0],persona_append=value['persona'][len(base[1]['persona']):]) if base else value)
    return out


async def bootstrap(client,request):
    listing=payload(await client.call_tool('list_chat_sessions',{}))
    if listing.get('has_more'):raise ValueError('Select a bounded session scope before bootstrapping')
    selected=set(request.get('session_ids',[]));rows=[s for s in listing['sessions'] if not selected or s['id'] in selected]
    if selected-set(s['id'] for s in rows) or not 1<=len(rows)<=8:raise ValueError('Invalid active chat scope')
    contexts={};sessions=[];policies=[];recovery=[]
    for row in rows:
        setup=payload(await client.call_tool('get_chat_session',dict(session_id=row['id'],view='setup')))
        if 'session' not in setup:raise ValueError('Session setup failed')
        version=setup['static_version']
        if version not in contexts:
            persona=payload(await client.call_tool('get_chat_session',dict(session_id=row['id'],view='persona')))
            if persona.get('static_version')!=version or not persona.get('static_context'):raise ValueError('Persona changed during initialization; retry setup')
            contexts[version]=persona['static_context']
        sessions.append(dict(setup['session'],static_version=version,pending_messages=setup.get('pending_messages',0)))
        policies.append(setup['request_policy'])
        for operation in setup.get('unsettled_operations',[]):recovery.append(dict(session_id=row['id'],**operation))
        plan=setup['session'].get('turn_process',{}).get('last_plan')
        if plan and plan['state'] in ('READY','WAITING','SENDING','UNKNOWN'):recovery.append(dict(session_id=row['id'],plan=plan,read_call=dict(tool='get_chat_session',arguments=dict(session_id=row['id'],view='all',response_format='full'))))
    result=dict(sessions=sessions,static_contexts=share_static(contexts),receiver=listing['receiver'],
        request_policy=policies[0] if all(p==policies[0] for p in policies) else {s['id']:p for s,p in zip(sessions,policies)},recovery_required=recovery)
    if any('inherits_static_version' in c for c in result['static_contexts'].values()):result['static_context_rule']='继承版本的完整人格及行为，再逐字追加persona_append；此为无损共享，不是摘要。'
    if not recovery:result['next_call']=dict(tool='wait_chat_messages_many',arguments=dict(session_ids=[s['id'] for s in sessions],known_prompt_versions={s['id']:s['prompt_version'] for s in sessions},timeout_seconds=150,limit=8))
    return result


async def main():
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    cfg=tomllib.loads((Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'config.toml').read_text('utf-8-sig'))['mcp_servers']['tulpa']
    headers=dict(cfg.get('http_headers',{}))
    if cfg.get('bearer_token_env_var'):headers['Authorization']='Bearer '+os.environ[cfg['bearer_token_env_var']]
    async with httpx.AsyncClient(headers=headers,trust_env=False,timeout=240) as http:
        async with streamable_http_client(cfg['url'],http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                requests=[sys.argv[1]] if len(sys.argv)>1 else sys.stdin
                for line in requests:
                    request=json.loads(line)
                    if request['tool']=='__bootstrap':value=await bootstrap(client,request)
                    elif request['tool']=='__wait':
                        args=dict(request.get('arguments',request.get('args',{})))
                        if 'wait_state_id' not in args and 'session_ids' not in args:
                            listing=payload(await client.call_tool('list_chat_sessions',{}))
                            args['session_ids']=[s['id'] for s in listing['sessions']]
                        value=payload(await client.call_tool('wait_chat_messages_many',dict(dict(timeout_seconds=150,limit=8),**args)))
                    elif request['tool']=='__schemas':
                        names=set(request['names']);value=[dict(name=t.name,inputSchema=t.inputSchema) for t in (await client.list_tools()).tools if t.name in names]
                    else:value=payload(await client.call_tool(request['tool'],request.get('arguments',request.get('args',{}))))
                    print(bounded(value),flush=True)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    asyncio.run(main())
