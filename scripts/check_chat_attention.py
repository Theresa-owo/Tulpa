"""Priority @/live delivery, exact ACK holes, ordinary and proactive replies.

Temporary DB + real service code + loopback OneBot only. No real QQ/model calls.
"""
from http.server import ThreadingHTTPServer
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
from check_fixture_support import isolated_env
from check_mcp_chat import OneBot,Events,eventually
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.onebot import invalidate_availability
from chatlocal import mcp_chat_attention as attention
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def fast_reply(port,token,args):
    from chat_bridge import reply_text
    async with httpx.AsyncClient(headers={'Authorization':'Bearer '+token},trust_env=False,timeout=20) as http:
        async with streamable_http_client(f'http://127.0.0.1:{port}/mcp',http_client=http) as (read,write,_):
            async with ClientSession(read,write) as client:
                await client.initialize()
                try:await reply_text(client,dict(args,expression_ids=['unseen']))
                except ValueError:pass
                else:raise AssertionError('Fast path pretended to choose unseen learned references')
                return await reply_text(client,args)


def main():
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),OneBot);OneBot.sent=[]
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory() as tmp,patch.dict('os.environ',isolated_env(),clear=True):
            root=Path(tmp)
            os.environ['TEMP']=str(root);os.environ['TMP']=str(root)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            (root/'mcp-chat-policy.json').write_text(json.dumps(dict(quiet_seconds=.1,reply_cooldown_seconds=0)),'utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,Store(root/'chats.sqlite3'));access=service.access;chat=service.tools.chat
                with TestClient(app):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    access.configure(True,port);service.start();invalidate_availability()
                    g=access.create(dict(name='attention-fixture',platforms=['qq'],conversations=[['qq',f'111:group:{n}'] for n in range(222,230)],chat=True,send=True))
                    grant=access.authorize(g['token'],source=False);cancel=threading.Event()
                    sids=[chat.start(grant,dict(conversation_id=f'111:group:{n}',persona='自然群友，可按语境主动聊。',idempotency_key=f'attention-{n}'),cancel)['session']['id'] for n in range(222,230)]
                    for sid in sids:chat.learning.configure(sid,dict(enabled=False))
                    eventually(lambda:chat.receiver.status()['state']=='connected')
                    counter=0
                    def add(index,text='hello',at=False):
                        nonlocal counter
                        counter+=1;segments=[dict(type='text',data={'text':text})]
                        if at:segments.insert(0,dict(type='at',data={'qq':'111' if at is True else str(at)}))
                        chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=222+index,user_id=333,message_id=counter,time=time.time(),message=segments))
                        with access.connect() as db:
                            eid=db.execute('SELECT max(id) FROM chat_inbox WHERE session_id=?',(sids[index],)).fetchone()[0]
                            db.execute("UPDATE chat_inbox SET payload=json_set(payload,'$.received_at',?) WHERE id=?",(time.time()-1,eid))
                        return eid
                    def one(index=0,**args):return chat.wait(grant,dict(session_id=sids[index],timeout_seconds=0,limit=3,**args),cancel,allow_learning=False)
                    def many(**args):return chat.wait_many(grant,dict(session_ids=sids,timeout_seconds=0,limit=3,**args),cancel,allow_learning=False)
                    old=[add(0,f'old-{n}') for n in range(1000)]
                    with access.connect() as db:db.execute("UPDATE chat_inbox SET at=?,payload=json_set(payload,'$.received_at',?) WHERE session_id=?",(time.time()-3600,time.time()-3600,sids[0]))
                    ordinary=add(1,'普通实时消息');target=add(0,'抱抱',at=True);follow=add(0,'就抱一下')
                    start=time.monotonic();packet=many()['results'][0]
                    assert time.monotonic()-start<.75 and packet['session_id']==sids[0]
                    assert packet['attention']['priority'] and packet['attention']['reason']=='direct_mention'
                    assert [m['id'] for m in packet['messages']]==[target,follow] and not packet['has_more']
                    assert packet['attention']['backlog_pending'] and chat.row(sids[0])['cursor']==0
                    before_gap=chat.row(sids[0])['gap_count'];add(0,'另一个话题',at='555')
                    assert chat.row(sids[0])['gap_count']>before_gap,'Fixture did not trim old cache'
                    plan=chat.turns.plan(grant,dict(session_id=sids[0],batch_id=packet['input_batch']['id'],action='reply',target_event_id=target,expressions=['text'],intent='自然回应点名'),cancel)
                    assert plan['state']=='READY',plan
                    sent=chat.turns.submit(grant,dict(session_id=sids[0],plan_id=plan['plan_id'],expression_ids=[],bubbles=[dict(kind='text',text='抱抱～')]),cancel)
                    assert sent['state']=='SUCCEEDED' and len(OneBot.sent)==1,sent
                    one(acknowledge_through_id=packet['read_through_id'])
                    assert chat.row(sids[0])['cursor']==0,'Priority ACK skipped unread old messages'
                    with access.connect() as db:
                        assert db.execute('SELECT sum(completed),sum(delivered) FROM chat_inbox WHERE session_id=? AND id<=?',(sids[0],old[-1])).fetchone()[0]==0
                        assert db.execute('SELECT completed FROM chat_inbox WHERE id=?',(target,)).fetchone()[0]==1
                    attention.install(access)  # Migration/reopen must retain settled priority IDs.
                    one(acknowledge_through_id=packet['read_through_id'])
                    assert chat.row(sids[0])['cursor']==0 and len(OneBot.sent)==1
                    assert target not in [m['id'] for m in one()['messages']]
                    live=many()['results'][0]
                    assert live['session_id']==sids[1] and live['messages'][0]['id']==ordinary,'Backlog starved another live group'
                    normal_sent=asyncio.run(fast_reply(port,g['token'],dict(session_id=sids[1],batch_id=live['input_batch']['id'],target_event_id=ordinary,expression_ids=[],text='收到，继续聊')))
                    assert normal_sent['stage']=='send' and normal_sent['result']['state']=='SUCCEEDED' and len(OneBot.sent)==2,normal_sent
                    one(1,acknowledge_through_id=live['read_through_id'])
                    # A pending/UNKNOWN priority plan cannot masquerade as a
                    # successful ACK. Only its exact completed IDs may settle.
                    unknown_id=add(0,'不能跳过UNKNOWN',at=True);u=one()
                    p=chat.turns.plan(grant,dict(session_id=sids[0],batch_id=u['input_batch']['id'],action='reply',target_event_id=unknown_id,expressions=['text'],intent='fixture'),cancel)
                    assert p['state']=='READY',p
                    healthy=chat.receiver.status()
                    with patch.object(chat.receiver,'status',return_value=dict(healthy,connected_at=healthy['connected_at']+1)):
                        try:chat.turns.check(chat.turns.load(p['plan_id'],sids[0]),cancel)
                        except ValueError as exc:assert getattr(exc,'code',None)=='needs_refresh'
                        else:raise AssertionError('Priority send accepted a replaced real-time connection')
                    chat.turns.save(chat.turns.load(p['plan_id'],sids[0]),'UNKNOWN')
                    try:one(acknowledge_through_id=u['read_through_id'])
                    except ValueError:pass
                    else:raise AssertionError('UNKNOWN priority ACK accepted')
                    assert chat.row(sids[0])['cursor']==0
                    # Resolve synthetic fixture uncertainty without a dispatch.
                    chat.turns.save(chat.turns.load(p['plan_id'],sids[0]),'SILENT')
                    one(acknowledge_through_id=u['read_through_id'])
                    # Drain old pages explicitly; contiguous progress now
                    # passes the already-settled priority without replaying it.
                    for _ in range(80):
                        page=chat.wait(grant,dict(session_id=sids[0],timeout_seconds=0,limit=50),cancel,allow_learning=False)
                        if not page['messages']:break
                        silence=chat.turns.plan(grant,dict(session_id=sids[0],batch_id=page['input_batch']['id'],action='silence',intent='已读本页，明确沉默'),cancel)
                        assert silence['state']=='SILENT',silence
                        chat.wait(grant,dict(session_id=sids[0],timeout_seconds=0,limit=50,acknowledge_through_id=page['read_through_id']),cancel,allow_learning=False)
                    assert chat.row(sids[0])['cursor']>=unknown_id and len(OneBot.sent)==2
                    idle=one();assert idle['event']=='idle' and not idle['decision_required']
                    assert any('主动' in hint for hint in idle['chat_guidance']['reminders'])
                    proactive=chat.turns.plan(grant,dict(session_id=sids[0],batch_id=idle['input_batch']['id'],action='reply',expressions=['text'],intent='顺着刚才话题主动接一句'),cancel)
                    assert proactive['state']=='READY',proactive
                    result=chat.turns.submit(grant,dict(session_id=sids[0],plan_id=proactive['plan_id'],expression_ids=[],bubbles=[dict(kind='text',text='今天大家都在忙什么呀')]),cancel)
                    assert result['state']=='SUCCEEDED' and len(OneBot.sent)==3,result
                    print('PASS: @ behind 1000 old messages delivered immediately; exact ACK retains holes; cache trim does not starve reply; stream replacement rejected; multi-group live priority; duplicate/reopen/UNKNOWN protection; proactive Planner and real loopback send')
    finally:events.close();upstream.shutdown();upstream.server_close()


if __name__=='__main__':main()
