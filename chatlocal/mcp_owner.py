"""Owner-origin checks for Tulpa tools only; no Codex or host-tool policy."""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import json
import re
import time

from .mcp_access import AccessDenied, GrantConflict

PROTECTED = frozenset({
    'prepare_file','download_file','prepare_local_image','send_chat_file',
    'download_chat_file','download_chat_image','moderate_chat_member','manage_qq_group',
    'qq_group_action','qq_file_action','qq_friend_action','qq_profile_action',
    'qq_qzone_action','qq_album_action','qq_message_action','import_qq_media',
})
PROOF_FIELDS = ('owner_session_id','owner_event_id')
_active = ContextVar('tulpa_owner_guard', default=None)


class OwnerDenied(AccessDenied):
    pass


def protected(name, args):
    if name in PROTECTED:
        return True
    if name == 'send_chat_message':
        return bool(args.get('mention_all'))
    if name == 'send_chat_reply':
        bubbles=args.get('bubbles',[])
        return isinstance(bubbles,list) and any(isinstance(b,dict) and (b.get('mention_all') or b.get('kind') in ('file','qq_action')) for b in bubbles)
    return False


def add_proof_schema(rows):
    for row in rows:
        if row['name'] in PROTECTED or row['name'] in ('send_chat_message','send_chat_reply'):
            row['parameters']['properties'].update(
                owner_session_id=dict(type='string',minLength=32,maxLength=32,description='启用主人校验时，主人原始 /tulpa 指令所在的本连接会话。'),
                owner_event_id=dict(type='integer',minimum=1,description='主人原始指令的已读取事件 ID；不是昵称、QQ 消息编号或引用事件。'))
            row['description'] += ' 启用主人校验时须提供原始 /tulpa 授权消息，工具名与参数必须和原文完全一致。'
    return rows


def check_bound(access, gid):
    guard = _active.get()
    if guard is not None and guard.gate.access is access and guard.gid == gid:
        guard.check()


class OwnerGate:
    def __init__(self, access):
        self.access = access

    def policy(self, gid):
        with self.access.connect() as db:
            row=db.execute('SELECT enabled,owner_qq,revision,updated,after_event_id FROM owner_policies WHERE grant_id=?',(gid,)).fetchone()
        return dict(row) if row else dict(enabled=0,owner_qq='',revision=0,updated=0,after_event_id=0)

    def view(self, gid):
        self.access.by_id(gid,source=False)
        p=self.policy(gid)
        return dict(p,enabled=bool(p['enabled']),protected_tools=sorted(PROTECTED),max_age_seconds=600,
                    command_format='/tulpa {"tool":"工具名","arguments":{完整工具参数}}',scope='Tulpa MCP only')

    def configure(self, gid, body):
        if not isinstance(body,dict) or set(body)!={'enabled','owner_qq','expected_revision'}:
            raise ValueError('主人设置字段无效。')
        if type(body['enabled']) is not bool or type(body['expected_revision']) is not int:
            raise ValueError('主人设置类型无效。')
        owner=body['owner_qq']
        if not isinstance(owner,str) or (owner and not re.fullmatch(r'[1-9][0-9]{4,15}',owner)):
            raise ValueError('主人 QQ 应为 5 至 16 位数字。')
        if body['enabled'] and not owner:raise ValueError('启用前请填写主人 QQ。')
        grant=self.access.by_id(gid)
        if body['enabled'] and not (grant['scope'].get('chat') and grant['scope'].get('send') and grant['scope'].get('chat_account')):
            raise ValueError('主人指令需绑定 QQ 账号并开启持续聊天及发送权限。')
        with self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revoked,revision FROM grants WHERE id=?',(gid,)).fetchone()
            p=db.execute('SELECT * FROM owner_policies WHERE grant_id=?',(gid,)).fetchone()
            revision=p['revision'] if p else 0
            if not row or row['revoked']:raise AccessDenied('连接已撤销。')
            if row['revision']!=grant['revision'] or revision!=body['expected_revision']:
                raise GrantConflict('设置已改变，请重新打开后保存。')
            if p and bool(p['enabled'])==body['enabled'] and p['owner_qq']==owner:return self.view(gid)
            after=db.execute('SELECT coalesce(max(id),0) FROM chat_inbox').fetchone()[0]
            db.execute('INSERT INTO owner_policies(grant_id,enabled,owner_qq,revision,updated,after_event_id) VALUES(?,?,?,?,?,?) '
                       'ON CONFLICT(grant_id) DO UPDATE SET enabled=excluded.enabled,owner_qq=excluded.owner_qq,revision=excluded.revision,updated=excluded.updated,after_event_id=excluded.after_event_id',
                       (gid,int(body['enabled']),owner,revision+1,time.time(),after))
            db.execute('UPDATE grants SET revision=revision+1 WHERE id=?',(gid,))
        return self.view(gid)

    def guard(self, grant, name, args, proof):
        return Guard(self,grant,name,args,proof)


class Guard:
    def __init__(self, gate, grant, name, args, proof):
        self.gate=gate;self.gid=grant['id'];self.grant_revision=grant.get('revision',0)
        self.policy=gate.policy(self.gid);self.name=name;self.args=copy.deepcopy(args);self.proof=dict(proof)

    @contextmanager
    def bound(self):
        self.check()
        token=_active.set(self)
        try:
            yield
        finally:
            _active.reset(token)

    def check(self):
        p=self.gate.policy(self.gid)
        if p['revision']!=self.policy['revision']:
            raise OwnerDenied('主人设置已改变，本次操作未获当前授权。')
        if not p['enabled']:return
        sid=self.proof.get('owner_session_id');eid=self.proof.get('owner_event_id')
        if not isinstance(sid,str) or type(eid) is not int or eid<=p['after_event_id']:
            raise OwnerDenied('此 Tulpa 操作需要主人原始指令，请提供 owner_session_id 与 owner_event_id。')
        with self.gate.access.connect() as db:
            grant=db.execute('SELECT * FROM grants WHERE id=? AND revoked=0',(self.gid,)).fetchone()
            row=db.execute('SELECT s.grant_id,s.conversation_id,s.active,s.created,i.payload,i.delivered FROM chat_sessions s '
                           'JOIN chat_inbox i ON i.session_id=s.id WHERE s.id=? AND i.id=?',(sid,eid)).fetchone()
        if not grant or grant['revision']!=self.grant_revision or not row or row['grant_id']!=self.gid or not row['active'] or not row['delivered']:
            raise OwnerDenied('主人指令不属于当前有效连接的已读取会话。')
        scope=json.loads(grant['scope']);msg=json.loads(row['payload']);cid=row['conversation_id'];now=time.time()*1000
        if scope['conversations'] and json.dumps(['qq',cid],ensure_ascii=False) not in scope['conversations']:
            raise OwnerDenied('主人指令所在会话不在授权范围。')
        if (not scope.get('chat') or not scope.get('send') or 'qq' not in scope['platforms']
                or cid.split(':')[0]!=scope.get('chat_account') or msg.get('conversation_id')!=cid
                or msg.get('sender_id')!=p['owner_qq'] or msg.get('is_self') is not False
                or msg.get('source')!='onebot_websocket' or msg.get('recalled')):
            raise OwnerDenied('只有已配置主人 QQ 的真实原始消息可授权此操作。')
        stamp=msg.get('timestamp');plan=self.gate.access.plan(dict(scope=scope))
        if (type(stamp) is not int or not max(now-600000,row['created']*1000-2000)<=stamp<=now+30000
                or (plan.start is not None and stamp<plan.start) or (plan.end is not None and (stamp>=plan.end or now>=plan.end))):
            raise OwnerDenied('主人指令已过期或不在授权日期范围。')
        segments=msg.get('segments')
        if not isinstance(segments,list) or not segments or any(not isinstance(s,dict) or s.get('type')!='text' or not isinstance(s.get('text'),str) for s in segments):
            raise OwnerDenied('授权指令只接受主人直接发送的纯文本，不接受引用、转发或媒体。')
        text=''.join(s.get('text','') for s in segments).strip()
        if not text.startswith('/tulpa '):raise OwnerDenied('普通聊天不能授权操作；主人需直接发送 /tulpa 指令。')
        try:
            command=json.loads(text[len('/tulpa '):])
            if (not isinstance(command,dict) or set(command)!={'tool','arguments'} or command['tool']!=self.name
                    or not isinstance(command['arguments'],dict) or any(k in command['arguments'] for k in PROOF_FIELDS)
                    or json.dumps(command['arguments'],sort_keys=True,ensure_ascii=False)!=json.dumps(self.args,sort_keys=True,ensure_ascii=False)):
                raise ValueError()
        except (ValueError,TypeError):
            raise OwnerDenied('工具名或参数与主人原始指令不一致，未执行。') from None
