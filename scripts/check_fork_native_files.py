"""Portable fork regressions with isolated OneBot/files; no real QQ or models."""
import hashlib
import http.client
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parent))
from check_mcp_chat import Events, OneBot, ROOT, eventually
from check_fixture_support import isolated_env
sys.path.insert(0,str(ROOT))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from chatlocal.mcp_routes import install_mcp_routes
from chatlocal.mcp_chat_files import MAX_FILE_BYTES, file_limit, stream_download
from chatlocal.mcp_chat_media import remote_target, local_image_bytes
from chatlocal.message_sender import QQSender, SendError
from chatlocal.native_file_transfer import source_file, workspace
from chatlocal.onebot import invalidate_availability
from chatlocal.store import Store


class NativeBot(OneBot):
    account=111
    role='admin'
    quota=True
    unknown=False
    wrong_message=False
    files=[]
    bans=[]
    uploads=[]
    stamp=int(time.time())
    # Above upstream's in-memory 32 MiB limit; generated once for this fixture.
    blob=b'\x00\xffnative-file\r\n'*(33*1024*1024//15+1)

    def do_GET(self):
        assert self.path=='/blob'
        self.send_response(200)
        self.send_header('Content-Length',str(len(self.blob)))
        self.end_headers()
        try:
            for offset in range(0,len(self.blob),256*1024):
                self.wfile.write(self.blob[offset:offset+256*1024])
        except (ConnectionError,OSError): pass

    def do_POST(self):
        length=int(self.headers.get('Content-Length','0'))
        # Base handles ordinary group discovery and sender receipts.
        if self.path in ('/get_group_list','/get_group_info'):
            return super().do_POST()
        body=json.loads(self.rfile.read(length))
        action=self.path[1:]
        if action=='get_login_info': data=dict(user_id=self.account)
        elif action=='get_group_member_info':
            data=dict(group_id=body['group_id'],user_id=body['user_id'],role=self.role if body['user_id']==111 else 'member')
        elif action=='get_group_at_all_remain':
            data=dict(can_at_all=self.quota,remain_at_all_count_for_group=1,remain_at_all_count_for_uin=1)
        elif action in ('get_group_root_files','get_group_files_by_folder'):
            data=dict(files=self.files,folders=[])
        elif action=='get_group_file_url':
            data=dict(url=f'http://fixture.qq.com:{self.server.server_port}/blob')
        elif action=='get_msg':
            data=dict(message_id=body['message_id'],message_type='group',group_id=999 if self.wrong_message else 222,
                      self_id=111,user_id=333,time=self.stamp,message=[dict(type='file',data=self.files[0])])
        elif action=='set_group_ban':
            self.bans.append(body)
            data={}
        elif action=='send_group_msg':
            self.sent.append(body)
            if any(seg['type']=='file' for seg in body['message']):
                seg=next(seg for seg in body['message'] if seg['type']=='file')['data']
                path=Path(unquote(urlsplit(seg['file']).path.lstrip('/')) if os.name=='nt' else unquote(urlsplit(seg['file']).path))
                self.uploads.append(dict(name=seg['name'],sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            if self.unknown:
                self.send_response(503)
                self.end_headers()
                return
            data=dict(message_id=9000+len(self.sent))
        else: raise AssertionError(action)
        raw=json.dumps(dict(status='ok',retcode=0,data=data)).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def rejected(action):
    try: action()
    except (ValueError,OSError): return
    raise AssertionError('Expected rejection')


def main():
    events=Events()
    server=ThreadingHTTPServer(('127.0.0.1',0),NativeBot)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    NativeBot.files=[dict(file_id='fixture-file',file_name='original.bin',file_size=len(NativeBot.blob),busid=102,upload_time=NativeBot.stamp)]
    try:
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='fork-native-') as tmp,patch.dict('os.environ',isolated_env(),clear=True):
            folder=Path(tmp)
            (folder/'.env').write_text(f'REPLY_ONEBOT_URL=http://127.0.0.1:{server.server_port}\nREPLY_ONEBOT_WS_URL=ws://127.0.0.1:{events.port}\nREPLY_ONEBOT_WS_TOKEN=event-fixture\n','utf-8')
            store=Store(folder/'chats.sqlite3')
            app=FastAPI()
            service=install_mcp_routes(app,store)
            tools,access=service.tools,service.access
            with patch('chatlocal.onebot.ROOT',folder),patch('chatlocal.message_sender.ROOT',folder),patch('chatlocal.config.ROOT',folder),TestClient(app) as ui:
                invalidate_availability()
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0))
                    port=sock.getsockname()[1]
                assert ui.put('/api/mcp',json=dict(enabled=True,port=port),headers={'X-ChatWeave-UI':'1'}).json()['running']
                config=dict(name='native-fixture',platforms=['qq'],conversations=[['qq','111:group:222']],send=True,chat=True,
                            chat_files=True,chat_file_send=True,chat_moderate=True,chat_images=True,chat_sticker_send=True)
                grant=access.create(config)
                limited=access.create(config|dict(name='limited',chat_file_send=False,chat_moderate=False))
                def call(name,connection=grant,**args):
                    return tools.call(connection['token'],name,args,threading.Event())
                def data(name,**args):
                    result=call(name,**args)
                    assert not result.isError,result
                    return result.structuredContent
                def start(key):
                    return data('start_chat_session',conversation_id='111:group:222',persona='Fixture',idempotency_key=key)['session']['id']
                sid=start('native-session')
                eventually(lambda:tools.chat.receiver.status()['state']=='connected')
                assert workspace(folder)==folder
                with patch.dict('os.environ',{'TULPA_WORKSPACE_DIR':str(folder/'custom')}): assert workspace(folder)==folder/'custom'
                assert file_limit()==MAX_FILE_BYTES
                with patch.dict('os.environ',{'TULPA_CHAT_FILE_MAX_BYTES':'0'}): rejected(file_limit)
                with patch.dict('os.environ',{'TULPA_CHAT_FILE_MAX_BYTES':str(MAX_FILE_BYTES+1)}): rejected(file_limit)
                limited_tools={s['name'] for s in tools.schemas(access.authorize(limited['token']))}
                assert {'list_chat_files','download_chat_file','download_chat_image'}<=limited_tools
                assert not {'send_chat_file','moderate_chat_member'}&limited_tools
                handle=data('list_chat_files',session_id=sid)['items'][0]['file_handle']
                # Pin fixture-only public DNS resolution to its local HTTP server.
                records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',server.server_port))]
                chunks=[]
                original_read=http.client.HTTPResponse.read1
                def bounded_read(response,amount):
                    chunks.append(amount)
                    return original_read(response,amount)
                with patch('chatlocal.mcp_chat_media.public_addresses',return_value=records),patch.object(http.client.HTTPResponse,'read1',bounded_read):
                    result=data('download_chat_file',session_id=sid,file_handle=handle)
                downloaded=Path(result['local_path'])
                expected=hashlib.sha256(NativeBot.blob).hexdigest()
                assert result['size']>32*1024*1024 and result['size']==len(NativeBot.blob)
                assert result['sha256']==expected==hashlib.sha256(downloaded.read_bytes()).hexdigest()
                assert result['streamed'] and not result['parsed'] and max(chunks)<=256*1024
                assert not list(downloaded.parent.glob('*.partial'))
                assert call('download_chat_file',connection=limited,session_id=sid,file_handle=handle).isError
                NativeBot.account=999
                assert call('list_chat_files',session_id=sid).isError
                NativeBot.account=111
                original_name=NativeBot.files[0]['file_name']
                NativeBot.files[0]['file_name']='changed.bin'
                assert call('download_chat_file',session_id=sid,file_handle=handle).isError
                NativeBot.files[0]['file_name']=original_name
                old_stamp=NativeBot.files[0]['upload_time']
                NativeBot.files[0]['upload_time']=1
                from chatlocal.mcp_chat_files import in_date
                with patch.object(access,'plan',return_value=type('Plan',(),dict(start=NativeBot.stamp*1000,end=None))()):
                    assert not data('list_chat_files',session_id=sid)['items']
                    assert not in_date(access,{},1)
                NativeBot.files[0]['upload_time']=old_stamp
                event=dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=1234,user_id=333,time=NativeBot.stamp,
                           message=[dict(type='file',data=dict(file_id='fixture-file',name='original.bin'))])
                events.send(event)
                def inbox():
                    with access.connect() as db: return db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,'1234')).fetchone()
                eventually(inbox)
                eid=inbox()[0]
                NativeBot.wrong_message=True
                assert call('download_chat_file',session_id=sid,event_id=eid).isError
                NativeBot.wrong_message=False
                with patch('chatlocal.mcp_chat_media.public_addresses',return_value=records):
                    from_event=data('download_chat_file',session_id=sid,event_id=eid)
                assert from_event['sha256']==expected and from_event['source']['event_id']==eid
                with access.connect() as db:
                    payload=json.loads(db.execute('SELECT payload FROM chat_inbox WHERE id=?',(eid,)).fetchone()[0])
                    payload['recalled']=True
                    db.execute('UPDATE chat_inbox SET payload=? WHERE id=?',(json.dumps(payload),eid))
                assert call('download_chat_file',session_id=sid,event_id=eid).isError
                def interrupted(url,target,*args):
                    target.write_bytes(b'incomplete')
                    raise ValueError('fixture interrupted transfer')
                with patch('chatlocal.mcp_chat_files.stream_download',side_effect=interrupted):
                    assert call('download_chat_file',session_id=sid,file_handle=handle).isError
                assert not list(downloaded.parent.glob('*.partial'))
                cancel=threading.Event()
                cancel.set()
                with patch('chatlocal.mcp_chat_media.public_addresses',return_value=records):
                    rejected(lambda:stream_download(f'http://fixture.qq.com:{server.server_port}/blob',folder/'cancel.partial',cancel,len(NativeBot.blob),len(NativeBot.blob),lambda:None))
                assert not (folder/'cancel.partial').exists()
                for url in ('http://127.0.0.1/file','file:///tmp/file','https://qpic.cn.evil.invalid/file'):
                    rejected(lambda:remote_target(url,trusted_file=True))
                out=folder/'outgoing-files'
                out.mkdir()
                (out/'sample.bin').write_bytes(b'original\0\xffbytes')
                rejected(lambda:source_file(out,'../sample.bin'))
                rejected(lambda:source_file(out,'sample.bin:stream'))
                sent=data('send_chat_file',session_id=sid,filename='sample.bin',idempotency_key='send-file-fixture')
                assert sent['state']=='SUCCEEDED' and NativeBot.uploads[-1]['sha256']==hashlib.sha256((out/'sample.bin').read_bytes()).hexdigest()
                count=len(NativeBot.sent)
                assert data('send_chat_file',session_id=sid,filename='sample.bin',idempotency_key='send-file-fixture')['id']==sent['id']
                assert len(NativeBot.sent)==count
                (out/'sample.bin').write_bytes(b'changed')
                assert call('send_chat_file',session_id=sid,filename='sample.bin',idempotency_key='send-file-fixture').isError
                NativeBot.unknown=True
                unknown=data('send_chat_file',session_id=sid,filename='sample.bin',idempotency_key='send-file-unknown')
                assert unknown['state']=='UNKNOWN'
                count=len(NativeBot.sent)
                assert data('send_chat_file',session_id=sid,filename='sample.bin',idempotency_key='send-file-unknown')['state']=='UNKNOWN'
                assert len(NativeBot.sent)==count and list((folder/'outgoing-file-cache').glob('*.bin'))
                NativeBot.unknown=False
                mute=data('moderate_chat_member',session_id=sid,action='mute',user_id='333',duration_seconds=60,idempotency_key='mute-fixture')
                assert mute['state']=='SUCCEEDED' and NativeBot.bans[-1]['duration']==60
                assert data('moderate_chat_member',session_id=sid,action='unmute',user_id='333',idempotency_key='unmute-fixture')['state']=='SUCCEEDED'
                assert NativeBot.bans[-1]['duration']==0
                assert call('moderate_chat_member',session_id=sid,action='kick',user_id='333',idempotency_key='kick-fixture').isError
                NativeBot.role='member'
                assert call('moderate_chat_member',session_id=sid,action='mute',user_id='333',duration_seconds=60,idempotency_key='no-role-mute').isError
                NativeBot.role='admin'
                images=folder/'outgoing-images'
                images.mkdir()
                raw=io.BytesIO()
                frames=[Image.new('RGB',(40,30),color) for color in ('red','blue')]
                frames[0].save(raw,'GIF',save_all=True,append_images=frames[1:],duration=100,loop=0)
                (images/'animation.gif').write_bytes(raw.getvalue())
                assert local_image_bytes(images,'animation.gif')==raw.getvalue()
                image=data('prepare_local_image',session_id=sid,filename='animation.gif')
                image_file=data('download_chat_image',session_id=sid,sticker_id=image['sticker_id'])
                assert Path(image_file['local_path']).read_bytes()==raw.getvalue()
                assert image_file['sha256']==hashlib.sha256(raw.getvalue()).hexdigest() and image_file['total_frames']==2
                # Exercise the public Planner -> legacy single-tool -> bubble
                # queue -> native sender path, with every runtime gate intact.
                policy=access.path.parent/'mcp-chat-policy.json'
                policy.write_text(json.dumps(dict(quiet_seconds=.1,reply_cooldown_seconds=0,
                                                 bubble_min_seconds=.1,bubble_max_seconds=.1)),'utf-8')
                paused=ui.put(f'/api/mcp/chats/{sid}/learning',json=dict(enabled=False),headers={'X-ChatWeave-UI':'1'})
                assert paused.status_code==200 and not paused.json()['enabled']
                reply_schema=next(s for s in tools.schemas(access.authorize(grant['token'])) if s['name']=='send_chat_reply')
                assert reply_schema['parameters']['properties']['bubbles']['items']['properties']['mention_all']==dict(type='boolean')
                serial=0
                def ready_all():
                    nonlocal serial
                    serial+=1
                    mid=200000+serial
                    tools.chat.receive(dict(post_type='message',message_type='group',self_id=111,group_id=222,message_id=mid,
                        user_id=333,time=int(time.time()),message=[dict(type='text',data=dict(text='Fixture public mention-all request '+str(serial)))]))
                    with access.connect() as db:
                        event_id=db.execute('SELECT id FROM chat_inbox WHERE session_id=? AND message_id=?',(sid,str(mid))).fetchone()[0]
                    time.sleep(.12)
                    read=data('get_chat_session',session_id=sid)
                    plan=data('plan_chat_reply',session_id=sid,batch_id=read['input_batch']['id'],action='reply',
                              target_event_id=event_id,expressions=['text'],intent='Fixture group announcement',
                              idempotency_key='public-all-plan-'+str(serial))
                    assert plan['state']=='READY',plan
                    return plan
                count=len(NativeBot.sent)
                no_plan=data('send_chat_message',session_id=sid,text='Unplanned @all',mention_all=True,idempotency_key='public-all-no-plan')
                assert no_plan['state']=='REJECTED' and no_plan['error_code']=='plan_required' and len(NativeBot.sent)==count
                plan=ready_all()
                sent_all=data('send_chat_message',session_id=sid,plan_id=plan['plan_id'],text='Public planned @all',mention_all=True,
                              expression_ids=[],idempotency_key='public-all-success')
                assert sent_all['state']=='SUCCEEDED' and sent_all['chat_turn']['state']=='SUCCEEDED',sent_all
                assert sent_all['result']['mention_all'] is True and NativeBot.sent[-1]['message'][0]==dict(type='at',data=dict(qq='all'))
                for denied_by in ('quota','role'):
                    plan=ready_all()
                    count=len(NativeBot.sent)
                    NativeBot.quota=denied_by!='quota'
                    NativeBot.role='member' if denied_by=='role' else 'admin'
                    denied=data('send_chat_message',session_id=sid,plan_id=plan['plan_id'],text='Blocked planned @all',mention_all=True,
                                expression_ids=[],idempotency_key='public-all-denied-'+denied_by)
                    assert denied['state']=='INTERRUPTED' and denied['bubbles'][0]['state']=='FAILED',denied
                    assert len(NativeBot.sent)==count
                    NativeBot.quota,NativeBot.role=True,'admin'
                plan=ready_all()
                count=len(NativeBot.sent)
                repeated=data('send_chat_reply',session_id=sid,plan_id=plan['plan_id'],expression_ids=[],
                    bubbles=[dict(kind='text',text='First'),dict(kind='text',text='Second',mention_all=True)])
                assert repeated['error_code']=='repeated_addressing' and len(NativeBot.sent)==count,repeated
                assert call('send_chat_reply',session_id=sid,plan_id=plan['plan_id'],expression_ids=[],
                            bubbles=[dict(kind='text',text='Invalid boolean',mention_all='yes')]).isError
                # A rejected submission never consumed READY; first-bubble @all
                # plus an ordinary continuation still sends exactly one @all.
                sent_pair=data('send_chat_reply',session_id=sid,plan_id=plan['plan_id'],expression_ids=[],
                    bubbles=[dict(kind='text',text='Announcement',mention_all=True),dict(kind='text',text='Continuation')])
                assert sent_pair['state']=='SUCCEEDED' and len(NativeBot.sent)==count+2,sent_pair
                assert sum(seg==dict(type='at',data=dict(qq='all')) for msg in NativeBot.sent[count:] for seg in msg['message'])==1
                target=dict(platform='qq',conversation_id='111:group:222',conversation='fixture')
                sender=QQSender()
                prepared=sender.prepare_chat(target,mention_all=True)
                receipt=sender.send(target,'native mention all',prepared)
                assert receipt['mention_all'] and NativeBot.sent[-1]['message'][0]==dict(type='at',data=dict(qq='all'))
                count=len(NativeBot.sent)
                NativeBot.quota=False
                rejected(lambda:sender.send(target,'no quota',prepared))
                NativeBot.quota=True
                NativeBot.role='member'
                rejected(lambda:sender.send(target,'role changed',prepared))
                NativeBot.role='admin'
                assert len(NativeBot.sent)==count
                rejected(lambda:sender.prepare_chat(target,mention_all='yes'))
                data('stop_chat_session',session_id=sid)
                assert call('list_chat_files',session_id=sid).isError
                assert call('download_chat_image',session_id=sid,sticker_id=image['sticker_id']).isError
    finally:
        events.close()
        server.shutdown()
        server.server_close()
        invalidate_availability()
    print('PASS: native >32 MiB HTTP streaming/exact SHA256 and size, directory/date/account/session guards, cancellation, local byte snapshots, durable send/UNKNOWN, mute-only roles, local GIF download, public Planner/READY -> @all including role/quota gates and single-bubble addressing, native dispatch recheck. Isolated fixtures only; no real QQ or model calls.')


if __name__=='__main__': main()
