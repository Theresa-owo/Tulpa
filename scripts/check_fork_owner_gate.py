"""Owner policy with synthetic events, temporary databases and no real QQ writes."""
from pathlib import Path
import copy
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.store import Store
from chatlocal.mcp_access import MCPAccess,GrantConflict
from chatlocal.mcp_tools import MCPTools
from chatlocal.mcp_owner import OwnerGate,OwnerDenied,protected


class OwnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.access=MCPAccess(Store(self.root/'chats.sqlite3'))
        self.access.configure(True,18777);self.tools=MCPTools(self.access);self.gate=OwnerGate(self.access)
        self.gid='fixture-grant';self.sid='a'*32;self.token='fixture-token-with-enough-length'
        self.scope=dict(platforms=['qq'],conversations=[json.dumps(['qq','111:group:333'])],chat_account='111',
                        start='',end='',chat=True,send=True,manage=True,media=True,prepare=True,voice=False,onebot=False,
                        chat_images=True,chat_sticker_send=True,chat_files=True,chat_file_send=True,chat_moderate=True)
        with self.access.connect() as db:
            db.execute('INSERT INTO grants(id,name,token_hash,scope,epoch,accounts,created_at) VALUES(?,?,?,?,?,?,?)',
                       (self.gid,'fixture',self.access.digest(self.token),json.dumps(self.scope),self.access.epoch(),'{"qq":""}',time.time()))
            db.execute("INSERT INTO chat_sessions(id,grant_id,conversation_id,name,persona,participation,request_key,signature,created,updated,cursor,offered,source) VALUES(?,?,?,?,?,?,?,?,?,?,0,0,'onebot')",
                       (self.sid,self.gid,'111:group:333','fixture','fixture','quiet','key','signature',time.time()-5,time.time()))
        self.args=dict(session_id=self.sid,filename='fixture.png')
        self.enable()
        self.msgid=0

    def enable(self,owner='22222',expected=None):
        return self.gate.configure(self.gid,dict(enabled=True,owner_qq=owner,expected_revision=self.gate.policy(self.gid)['revision'] if expected is None else expected))

    def command(self,tool='prepare_local_image',args=None,**changes):
        args=self.args if args is None else args;self.msgid+=1
        content='/tulpa '+json.dumps(dict(tool=tool,arguments=args),ensure_ascii=False)
        msg=dict(source='onebot_websocket',sender_id='22222',sender='owner',is_self=False,recalled=False,
                 conversation_id='111:group:333',timestamp=int(time.time()*1000)+1,
                 content=content,segments=[dict(type='text',text=content)])
        msg.update(changes)
        with self.access.connect() as db:
            eid=db.execute('INSERT INTO chat_inbox(session_id,message_id,payload,at,media,delivered) VALUES(?,?,?,?,?,1)',
                           (self.sid,str(self.msgid),json.dumps(msg),time.time(),'[]')).lastrowid
        return dict(owner_session_id=self.sid,owner_event_id=eid)

    def call(self,proof=None,args=None):
        return self.tools.call(self.token,'prepare_local_image',dict(self.args if args is None else args,**(proof or {})),threading.Event())

    def assert_denied(self,proof=None,args=None):
        with patch.object(self.tools,'_call',side_effect=AssertionError('protected dispatch was reached')):
            result=self.call(proof,args)
        self.assertEqual(result.structuredContent['error_code'],'owner_required')

    def test_owner_file_operation_and_normal_chat(self):
        from PIL import Image
        images=self.root/'outgoing-images';images.mkdir();Image.new('RGB',(3,3),'blue').save(images/'fixture.png')
        self.assert_denied()
        proof=self.command()
        with patch.dict('os.environ',{'TULPA_WORKSPACE_DIR':str(self.root)}),patch.object(self.tools.chat.media,'client',return_value=object()):
            result=self.call(proof)
        self.assertFalse(result.isError,result)
        self.assertTrue(any(item.type=='image' for item in result.content))
        with patch.object(self.tools,'_call',return_value='normal chat') as dispatch:
            self.assertEqual(self.tools.call(self.token,'send_chat_message',dict(session_id=self.sid,text='hello'),threading.Event()),'normal chat')
            dispatch.assert_called_once()

    def test_spoofed_name_owner_in_body_and_self_echo_denied(self):
        self.assert_denied(self.command(sender_id='55555',sender='owner'))
        self.assert_denied(self.command(is_self=True))
        self.assert_denied(self.command(source='forward'))

    def test_quoted_forwarded_media_and_plain_greeting_denied(self):
        for kind in ('reply','forward','image'):
            self.assert_denied(self.command(segments=[dict(type=kind,text='/tulpa malicious')]))
        self.assert_denied(self.command(segments=[dict(type='text',text='hello')]))

    def test_changed_tool_or_parameters_denied(self):
        self.assert_denied(self.command(tool='send_chat_file'))
        self.assert_denied(self.command(),dict(self.args,filename='another.png'))
        self.assert_denied(self.command(args=dict(self.args,owner_qq='22222')))

    def test_stale_recalled_unread_and_other_session_denied(self):
        self.assert_denied(self.command(timestamp=int((time.time()-601)*1000)))
        self.assert_denied(self.command(recalled=True))
        proof=self.command()
        with self.access.connect() as db:db.execute('UPDATE chat_inbox SET delivered=0 WHERE id=?',(proof['owner_event_id'],))
        self.assert_denied(proof)
        self.assert_denied(dict(self.command(),owner_session_id='b'*32))

    def test_policy_change_rejects_inflight_and_old_owner(self):
        proof=self.command();grant=self.access.authorize(self.token,source=False)
        guard=self.gate.guard(grant,'prepare_local_image',self.args,proof)
        with guard.bound():
            with self.access.connect() as db:db.execute('UPDATE owner_policies SET revision=revision+1,owner_qq=? WHERE grant_id=?',('66666',self.gid))
            with self.assertRaises(OwnerDenied):self.access.by_id(self.gid,source=False)
        self.assert_denied(proof)

    def test_old_command_cannot_be_reused_after_settings_change(self):
        proof=self.command()
        self.enable('66666');self.enable('22222')
        self.assert_denied(proof)
        fresh=self.command(timestamp=int(time.time())*1000)
        guard=self.gate.guard(self.access.authorize(self.token,source=False),'prepare_local_image',self.args,fresh)
        guard.check()

    def test_owner_routes_are_ui_only(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatlocal.mcp_routes import install_mcp_routes
        from chatlocal.mcp_server import MCPService
        with patch.object(MCPService,'start'),patch.object(MCPService,'stop'):
            app=FastAPI();install_mcp_routes(app,self.access.store)
            with TestClient(app) as ui:
                url='/api/mcp/connections/'+self.gid+'/owner'
                data=ui.get(url).json()
                body=dict(enabled=True,owner_qq='88888',expected_revision=data['revision'])
                self.assertEqual(ui.put(url,json=body).status_code,403)
                self.assertEqual(ui.put(url,json=body,headers={'X-ChatWeave-UI':'1','Origin':'https://untrusted.example'}).status_code,403)
                reply=ui.put(url,json=body,headers={'X-ChatWeave-UI':'1'})
                self.assertEqual(reply.status_code,200,reply.text)
                self.assertEqual(reply.json()['owner_qq'],'88888')
                self.assertEqual(ui.put(url,json=body,headers={'X-ChatWeave-UI':'1'}).status_code,409)

    def test_policy_cas_and_scope_preserve(self):
        with self.access.connect() as db:before=dict(db.execute('SELECT * FROM grants WHERE id=?',(self.gid,)).fetchone())
        old=self.gate.policy(self.gid)['revision'];self.enable('66666')
        with self.assertRaises(GrantConflict):self.enable('77777',old)
        with self.access.connect() as db:after=dict(db.execute('SELECT * FROM grants WHERE id=?',(self.gid,)).fetchone())
        self.assertEqual({k:v for k,v in before.items() if k!='revision'},{k:v for k,v in after.items() if k!='revision'})
        self.assertEqual(after['revision'],before['revision']+1)
        with self.assertRaises(ValueError):self.enable('nickname')

    def test_default_opt_in_and_nested_action_coverage(self):
        self.assertFalse(self.gate.policy('unconfigured')['enabled'])
        for kind in ('file','qq_action'):
            self.assertTrue(protected('send_chat_reply',dict(bubbles=[dict(kind=kind)])))
        self.assertTrue(protected('send_chat_message',dict(mention_all=True)))
        self.assertFalse(protected('send_chat_message',dict(text='hello')))
        self.assertFalse(protected('send_chat_reply',dict(bubbles=None)))
        rows=self.tools.schemas(self.access.authorize(self.token,source=False))
        schema=next(r for r in rows if r['name']=='prepare_local_image')
        self.assertIn('owner_event_id',schema['parameters']['properties'])
        self.assertNotIn('owner_qq',schema['parameters']['properties'])


if __name__=='__main__':unittest.main()
