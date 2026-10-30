"""Live-priority learning must not turn deferred history into chat work."""
from http.server import ThreadingHTTPServer
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
from check_mcp_chat import Events,OneBot,eventually
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.onebot import invalidate_availability
from chatlocal.store import Store
from fastapi import FastAPI
from fastapi.testclient import TestClient


def run(enabled):
    events=Events();OneBot.sent=[];upstream=ThreadingHTTPServer(('127.0.0.1',0),OneBot)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix='tulpa-live-learning-') as tmp,patch.dict('os.environ',isolated_env(),clear=True):
            root=Path(tmp);os.environ['TEMP']=str(root);os.environ['TMP']=str(root)
            (root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            (root/'mcp-efficiency.json').write_text(json.dumps(dict(enabled=enabled,learning_interval_seconds=0,learning_hourly_calls=100)),'utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,Store(root/'chats.sqlite3'))
                tools=service.tools;access=service.access;chat=tools.chat
                with TestClient(app):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    access.configure(True,port);service.start();invalidate_availability()
                    timer=[time.time()];chat.learning.store.clock=lambda:timer[0]
                    groups=[222,223,224]
                    conn=access.create(dict(name='live-learning-fixture',platforms=['qq'],send=True,chat=True,
                                            conversations=[['qq',f'111:group:{g}'] for g in groups]))
                    def call(name,**args):
                        result=tools.call(conn['token'],name,args,threading.Event())
                        assert not result.isError,result
                        return result.structuredContent or json.loads(next(x.text for x in result.content if x.type=='text'))
                    sessions={group:call('start_chat_session',conversation_id=f'111:group:{group}',persona='fixture',
                                         idempotency_key=f'start-{group}')['session']['id'] for group in groups}
                    eventually(lambda:chat.receiver.status()['state']=='connected')
                    counter=[0]
                    def receive(group):
                        counter[0]+=1;mid=counter[0]
                        chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=group,
                            message_id=mid,user_id=333,time=time.time(),sender={'nickname':'Fixture'},
                            message=[dict(type='text',data={'text':f'synthetic peer message {mid}'})]))
                        with access.connect() as db:
                            return db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',
                                              (sessions[group],str(mid))).fetchone()[0]
                    for group in groups:
                        for _ in range(10):receive(group)
                        with access.connect() as db:
                            db.execute("UPDATE chat_inbox SET at=?,payload=json_set(payload,'$.received_at',?) WHERE session_id=?",
                                       (time.time()-3600,time.time()-3600,sessions[group]))
                    timer[0]+=31;chat.learning.tick()
                    def wake(group):
                        packet=call('wait_chat_messages_many',session_ids=[sessions[group]],timeout_seconds=0,prefer_live=True)
                        assert packet['event']=='learning_ready',packet['event']
                        return packet['pending_action']
                    def old_state(group):
                        with access.connect() as db:
                            return db.execute('SELECT count(*) FROM chat_inbox WHERE session_id=? AND completed=0 AND at<?',
                                              (sessions[group],time.time()-120)).fetchone()[0]
                    sid=sessions[222];wake_a=wake(222);before=chat.row(sid)['cursor']
                    claimed=call(wake_a['tool'],**wake_a['arguments'])
                    assert claimed.get('state')=='claimed',('deferred backlog blocked learning',claimed.get('state'),claimed.get('reason'))
                    task=claimed['task'];assert task['stage'] in ('extract','extract_jargon')
                    duplicate=call(wake_a['tool'],**wake_a['arguments'])
                    assert duplicate['state']=='no_task' and 'updated_chat' not in duplicate
                    assert old_state(222)==10 and chat.row(sid)['cursor']==before
                    fresh=receive(222)
                    result={'expressions':[]} if task['stage']=='extract' else {'jargon':[]}
                    submitted=call('submit_chat_learning',**task['submit_call']['arguments'],invocation_id='synthetic-live-stage',result=result)
                    page=submitted['updated_chat'];assert [x['id'] for x in page['messages']]==[fresh]
                    assert page['attention']['priority'] and chat.row(sid)['cursor']==before and old_state(222)==10
                    decision=call('plan_chat_reply',session_id=sid,batch_id=page['input_batch']['id'],action='silence',intent='fixture')
                    assert decision['state']=='SILENT'
                    call('wait_chat_messages_many',session_ids=[sid],timeout_seconds=0,prefer_live=True,
                         acknowledge_through_ids={sid:page['read_through_id']})
                    assert old_state(222)==10 and chat.row(sid)['cursor']==before
                    # A new live message arriving after a wake still prevents
                    # the claim and is delivered ahead of the old backlog.
                    wake_b=wake(223);fresh=receive(223)
                    race=call(wake_b['tool'],**wake_b['arguments'])
                    assert race['state']=='no_task' and race['reason']=='chat_pending'
                    assert [x['id'] for x in race['updated_chat']['messages']]==[fresh]
                    assert old_state(223)==10 and chat.row(sessions[223])['cursor']==0
                    # Legacy/manual claim without a live wake keeps its former
                    # explicit full-backlog behavior.
                    legacy=call('claim_chat_learning',session_id=sessions[224],wake_id=wake_a['arguments']['wake_id'])
                    assert legacy['state']=='no_task' and legacy['reason']=='chat_pending'
                    assert len(legacy['updated_chat']['messages'])==10
                    manual=call('claim_chat_learning',session_id=sessions[224])
                    assert manual['state']=='no_task' and manual['reason']=='chat_pending'
                    assert not OneBot.sent
        print(f'PASS live-priority learning/claim/submit, fresh-message races, retained backlog, legacy behavior; efficiency={enabled}; no real QQ writes')
    finally:events.close();upstream.shutdown();upstream.server_close()


if __name__=='__main__':
    run(True);run(False)
