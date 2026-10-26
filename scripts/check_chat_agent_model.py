"""Opt-in official-model acceptance against eight isolated QQ groups.

This spends real Codex tokens using the existing official OpenAI login, but the
only message receiver/sender is a local fixture. Never run as a routine CI check.
"""
import argparse
import asyncio
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import tomllib
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'scripts')]
import chat_agent
from check_mcp_chat import OneBot,Events,eventually
from fastapi import FastAPI
from fastapi.testclient import TestClient
from chatlocal.store import Store
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_owner import OwnerGate
from chatlocal.mcp_runtime_control import configure,state as fence_state
from chatlocal.onebot import invalidate_availability


async def exercise(args,service,grant,sids,root,cfg,report):
    folder=root/'.tmp/chat-agent';status=folder/'state.json'
    def read():return json.loads(status.read_text('utf-8')) if status.exists() else {}
    class Control:
        def __init__(self,*a,**kw):pass
        def request(self,method,path,body=None):
            assert method=='PUT' and path=='/api/mcp/chat-runtime'
            return configure(service.access,body)
    settings=SimpleNamespace(command='run',mode='live',resume=False,tulpa_root=str(root),state_dir=str(folder),workspace=str(root),codex_exe=None,max_seconds=180,max_batches=10)
    if args.resume_report:
        previous=json.loads(Path(args.resume_report).read_text('utf-8'))['state']
        folder.mkdir(parents=True,exist_ok=True);status.write_text(json.dumps(previous),'utf-8');settings.resume=True
    with patch.object(chat_agent,'LocalClient',Control):
        runner=asyncio.create_task(chat_agent.run(settings,config=cfg))
        async def until(check,seconds=190):
            end=time.monotonic()+seconds
            while not check():
                if runner.done():raise AssertionError('Runner stopped: '+str(read().get('stop_reason')))
                if time.monotonic()>end:raise AssertionError('Acceptance timeout: '+str(read().get('diagnostic')))
                await asyncio.sleep(.2)
        try:
            await until(lambda:read().get('status')=='running')
            initial=deepcopy(read());await asyncio.sleep(4)
            assert read()['model_turns']==1 and read()['usage']==initial['usage'],'Idle started inference'
            report['idle_zero_inference']=True
            cases=[(222,333,'白龙娘，你喜欢热茶还是冰茶？就随口选一个。'),(223,333,'白龙娘，帮我画一张SVG的鹈鹕骑自行车，给我完整SVG代码。')]
            for index,(group,sender,text) in enumerate(cases,1):
                service.tools.chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=group,user_id=sender,message_id=500+index,time=time.time(),message=[dict(type='text',data={'text':text})]))
                await until(lambda:read().get('batches',0)>=index)
                await until(lambda:len(OneBot.sent)>=index,seconds=15)
            assert len(OneBot.sent)==2,'Duplicate or missing fixture reply'
            assert read().get('text_replies')==2,'Plain replies did not use the local Planner path'
            # Stop during an idle long poll. The send fence must close first and
            # cancellation must finish promptly, without another model turn.
            before=deepcopy(read());start=time.monotonic()
            stopper=SimpleNamespace(**vars(settings));stopper.command='stop'
            await chat_agent.run(stopper,config=cfg)
            await asyncio.wait_for(runner,15)
            report.update(stop_seconds=round(time.monotonic()-start,3),state=read(),fixture_replies=deepcopy(OneBot.sent))
            assert read()['stop_reason']=='user_pause' and fence_state(service.access)['paused']
            assert read()['usage']==before['usage'],'Pause invoked inference'
            assert {int(r['group_id']) for r in OneBot.sent}=={222,223}
        finally:
            if not runner.done():
                folder.mkdir(parents=True,exist_ok=True);(folder/'stop.request').write_text('acceptance_cleanup','utf-8')
                try:await asyncio.wait_for(runner,15)
                except asyncio.TimeoutError:runner.cancel();await asyncio.gather(runner,return_exceptions=True)
            report['last_state']=read()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--live-model',action='store_true');p.add_argument('--report',required=True);p.add_argument('--persona-file');p.add_argument('--resume-report')
    args=p.parse_args()
    if not args.live_model:p.error('Explicit --live-model is required; this spends real tokens')
    home=Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex');cfg=tomllib.loads((home/'config.toml').read_text('utf-8-sig'))
    persona=Path(args.persona_file).read_text('utf-8') if args.persona_file else '你是白龙娘，温和、简洁、自然地参与QQ群闲聊。不要刷屏。'
    events=Events();upstream=ThreadingHTTPServer(('127.0.0.1',0),OneBot);OneBot.sent=[]
    threading.Thread(target=upstream.serve_forever,daemon=True).start();report=dict(test='official_model_isolated_eight_groups',real_qq_traffic=False)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{upstream.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            with patch('chatlocal.onebot.ROOT',root),patch('chatlocal.message_sender.ROOT',root):
                app=FastAPI();service=install_mcp_routes(app,Store(root/'chats.sqlite3'))
                with TestClient(app):
                    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
                    service.access.configure(True,port);service.start();invalidate_availability()
                    grant=service.access.create(dict(name='production-acceptance',platforms=['qq'],conversations=[['qq',f'111:group:{n}'] for n in range(222,230)],chat=True,send=True,chat_images=True,chat_poke=True))
                    authorized=service.access.authorize(grant['token'],source=False);cancel=threading.Event()
                    OwnerGate(service.access).configure(authorized['id'],dict(enabled=True,owner_qq='44444',expected_revision=0))
                    authorized=service.access.authorize(grant['token'],source=False)
                    sids=[service.tools.chat.start(authorized,dict(conversation_id=f'111:group:{n}',persona=persona,idempotency_key=f'model-{n}'),cancel)['session']['id'] for n in range(222,230)]
                    for sid in sids:service.tools.chat.learning.configure(sid,dict(enabled=False))
                    (root/'mcp-efficiency.json').write_text(json.dumps(dict(enabled=True)),'utf-8')
                    cfg=deepcopy(cfg);cfg['mcp_servers']['tulpa']=dict(url=f'http://127.0.0.1:{port}/mcp',http_headers={'Authorization':'Bearer '+grant['token']})
                    eventually(lambda:service.tools.chat.receiver.status()['state']=='connected')
                    asyncio.run(exercise(args,service,authorized,sids,root,cfg,report))
    except Exception as exc:report['error']=str(exc)[:1800];raise
    finally:
        events.close();upstream.shutdown();upstream.server_close()
        Path(args.report).write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(dict(ok=True,report=args.report,stop_seconds=report.get('stop_seconds'),usage=report['state']['usage']),ensure_ascii=True))


if __name__=='__main__':main()
