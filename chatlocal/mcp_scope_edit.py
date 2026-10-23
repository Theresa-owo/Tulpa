"""Add bounded QQ groups from the local settings UI, never from MCP tools."""
import hashlib
import json
import re

from .mcp_access import GrantConflict
from .onebot import Client


class GroupScopeEditor:
    def __init__(self, access, client_factory=None):
        self.access = access
        self.client_factory = client_factory or (lambda: Client(timeout=4))

    @staticmethod
    def revision(grant):
        value = [grant['id'], grant['scope'], grant['epoch'], grant['accounts'], grant['revision']]
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def grant(self, gid):
        grant = self.access.by_id(gid)
        scope = grant['scope']
        account = scope.get('chat_account')
        conversations = scope.get('conversations')
        if ('qq' not in scope.get('platforms', []) or not isinstance(account, str)
                or not re.fullmatch(r'[1-9][0-9]{0,19}', account)
                or not isinstance(conversations, list) or not 1 <= len(conversations) <= 500):
            raise ValueError('仅支持已绑定 QQ 账号、指定会话范围的有效连接。')
        return grant

    def directory(self, grant):
        account = grant['scope']['chat_account']
        client = self.client_factory()
        if client.login() != account:
            raise ValueError('OneBot 当前账号与连接授权账号不同。')
        rows = client.call('get_group_list', {'no_cache': True})
        if not isinstance(rows, list):
            raise ValueError('OneBot 群目录无效。')
        items = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            group_id = str(row.get('group_id', ''))
            if re.fullmatch(r'[1-9][0-9]{0,19}', group_id):
                cid = f'{account}:group:{group_id}'
                items[group_id] = dict(group_id=group_id, name=str(row.get('group_name') or group_id),
                                       authorized=json.dumps(['qq', cid], ensure_ascii=False) in grant['scope']['conversations'])
        if client.login() != account:
            raise ValueError('OneBot 查询期间账号已改变，请重新加载。')
        return items

    def listed(self, gid):
        grant = self.grant(gid)
        items = self.directory(grant)
        if self.revision(self.grant(gid)) != self.revision(grant):
            raise GrantConflict('连接授权已改变，请重新加载。')
        return dict(items=list(items.values()), scope=grant['scope'], revision=self.revision(grant))

    def add(self, gid, body):
        if not isinstance(body, dict) or set(body) != {'group_ids', 'revision'}:
            raise ValueError('添加授权群字段无效。')
        ids = body['group_ids']
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 500
                or any(not isinstance(value, str) or not re.fullmatch(r'[1-9][0-9]{0,19}', value) for value in ids)
                or not isinstance(body['revision'], str)):
            raise ValueError('请输入最多 500 个有效 QQ 群号。')
        grant = self.grant(gid)
        if body['revision'] != self.revision(grant):
            raise GrantConflict('连接授权已改变，请重新加载后确认。')
        directory = self.directory(grant)
        if any(value not in directory for value in ids):
            raise ValueError('所选群不在当前 QQ 账号的群目录中。')
        conversations = list(grant['scope']['conversations'])
        added = []
        for value in dict.fromkeys(ids):
            selection = json.dumps(['qq', f'{grant["scope"]["chat_account"]}:group:{value}'], ensure_ascii=False)
            if selection not in conversations:
                conversations.append(selection)
                added.append(dict(group_id=value, name=directory[value]['name']))
        if len(conversations) > 500:
            raise ValueError('添加后超过 500 个指定会话。')
        if not added:
            raise ValueError('所选群均已授权，无需添加。')
        with self.access.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM grants WHERE id=? AND revoked=0', (gid,)).fetchone()
            if row is None:
                raise ValueError('连接凭据无效或已撤销。')
            latest = self.access.validate_grant(row)
            if not self.access.settings()['enabled'] or self.revision(latest) != body['revision']:
                raise GrantConflict('连接授权已改变，请重新加载后确认。')
            scope = dict(latest['scope'], conversations=conversations)
            # Participate in native permission-edit CAS and in-flight validation.
            # Additions preserve existing sessions; no stop_all/cancel is needed.
            updated = db.execute('UPDATE grants SET scope=?,revision=revision+1 WHERE id=? AND revoked=0 AND revision=?',
                                 (json.dumps(scope), gid, latest['revision'])).rowcount
            if not updated:
                raise GrantConflict('连接授权已改变，请重新加载后确认。')
        return dict(added=added, conversations=len(conversations), revision=latest['revision'] + 1)
