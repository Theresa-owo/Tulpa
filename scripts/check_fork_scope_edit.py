"""Isolated additive group UI checks; no real QQ, credentials, or model calls."""
import copy
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
from chatlocal.mcp_access import MCPAccess, AccessDenied, GrantConflict
from chatlocal.mcp_scope_edit import GroupScopeEditor

ACCOUNT = '111'
GROUPS = ['444', '555']


class FakeStore:
    def __init__(self, root):
        self.path = Path(root) / 'messages.sqlite'
        with self.connect() as db:
            db.executescript("CREATE TABLE local_change_meta(id INTEGER,epoch TEXT); INSERT INTO local_change_meta VALUES(1,'epoch-1');")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class FakeOneBot:
    def __init__(self):
        self.account = ACCOUNT
        self.rows = [dict(group_id=int(gid), group_name='test-' + gid) for gid in ['222', *GROUPS]]
        self.after_directory = None
        self.calls = []

    def login(self):
        self.calls.append('get_login_info')
        return self.account

    def call(self, action, payload):
        assert action == 'get_group_list' and payload == {'no_cache': True}
        self.calls.append(action)
        if self.after_directory:
            self.after_directory()
        return self.rows


class ScopeEditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FakeStore(self.temp.name)
        self.access = MCPAccess(self.store)
        self.access.configure(True, 18777)
        self.scope = dict(platforms=['qq', 'wechat'], conversations=[json.dumps(['qq', ACCOUNT + ':group:222'])],
                          start='2026-01-01', end='2026-12-31', chat_account=ACCOUNT,
                          send=True, chat=True, media=False, manage=False, onebot=True,
                          chat_files=True, custom_preserved=dict(value='unchanged'))
        with self.access.connect() as db:
            db.execute('INSERT INTO grants(id,name,token_hash,scope,epoch,accounts,created_at,revoked) VALUES(?,?,?,?,?,?,?,?)',
                       ('grant', 'existing connection', 'test-token-hash', json.dumps(self.scope),
                        self.access.epoch(), json.dumps(self.access.accounts(self.scope['platforms'])), 42.0, 0))
        self.bot = FakeOneBot()
        self.editor = GroupScopeEditor(self.access, lambda: self.bot)

    def row(self):
        with self.access.connect() as db:
            return dict(db.execute('SELECT * FROM grants WHERE id=?', ('grant',)).fetchone())

    def body(self, ids=None):
        return dict(group_ids=GROUPS if ids is None else ids, revision=self.editor.listed('grant')['revision'])

    def assert_unchanged_failure(self, body):
        before = self.row()
        with self.assertRaises(ValueError):
            self.editor.add('grant', body)
        self.assertEqual(before, self.row())

    def test_add_groups_and_preserve_every_other_field(self):
        before = self.row()
        result = self.editor.add('grant', self.body([GROUPS[0], GROUPS[1], GROUPS[0], '222']))
        after = self.row()
        self.assertEqual(result['added'], [dict(group_id=g, name='test-' + g) for g in GROUPS])
        expected = copy.deepcopy(self.scope)
        expected['conversations'] += [json.dumps(['qq', ACCOUNT + ':group:' + g]) for g in GROUPS]
        self.assertEqual(json.loads(after.pop('scope')), expected)
        before.pop('scope')
        self.assertEqual(after.pop('revision'), before.pop('revision') + 1)
        self.assertEqual(after, before)
        with self.access.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM grants').fetchone()[0], 1)
        self.assertEqual(set(self.bot.calls), {'get_login_info', 'get_group_list'})

    def test_invalid_input_private_crossaccount_unknown_extra_permissions(self):
        revision = self.body()['revision']
        invalid = [[], ['0'], ['-1'], ['1.5'], [True], [1], ['１２３'], ['111:direct:333'],
                   ['999:group:444'], ['nonexistent'], ['777'], GROUPS * 251, None, '123']
        for ids in invalid:
            with self.subTest(ids=str(ids)[:60]):
                self.assert_unchanged_failure(dict(group_ids=ids, revision=revision))
        self.assert_unchanged_failure(dict(group_ids=GROUPS, revision=revision, send=False))
        self.assert_unchanged_failure(dict(group_ids=GROUPS, revision=None))

    def test_wrong_onebot_account_and_account_switch_during_directory(self):
        body = self.body()
        self.bot.account = '999'
        self.assert_unchanged_failure(body)
        self.bot.account = ACCOUNT
        self.bot.after_directory = lambda: setattr(self.bot, 'account', '999')
        self.assert_unchanged_failure(body)

    def test_revoked_disabled_invalid_source_and_missing_connection(self):
        body = self.body()
        for sql in ["UPDATE grants SET revoked=1", "UPDATE grants SET epoch='other'", "UPDATE grants SET accounts='{}'"]:
            before = self.row()
            with self.access.connect() as db:
                db.execute(sql)
            self.assert_unchanged_failure(body)
            with self.access.connect() as db:
                db.execute('UPDATE grants SET revoked=?,epoch=?,accounts=?', (before['revoked'], before['epoch'], before['accounts']))
        self.access.configure(False, 18777)
        self.assert_unchanged_failure(body)
        self.access.configure(True, 18777)
        with self.assertRaises(ValueError):
            self.editor.listed('missing')

    def test_unbounded_and_unbound_grants_refused(self):
        for change in [dict(conversations=[]), dict(chat_account=None), dict(platforms=['wechat'])]:
            with self.subTest(change=change):
                with self.access.connect() as db:
                    db.execute('UPDATE grants SET scope=?', (json.dumps(dict(self.scope, **change)),))
                with self.assertRaises(ValueError):
                    self.editor.listed('grant')

    def test_500_boundary_and_already_authorized(self):
        scope = copy.deepcopy(self.scope)
        scope['conversations'] = [json.dumps(['qq', ACCOUNT + ':group:' + str(i)]) for i in range(1000, 1499)]
        with self.access.connect() as db:
            db.execute('UPDATE grants SET scope=?', (json.dumps(scope),))
        body = self.body()
        self.assert_unchanged_failure(body)
        result = self.editor.add('grant', dict(body, group_ids=GROUPS[:1]))
        self.assertEqual(result['conversations'], 500)
        self.assert_unchanged_failure(self.body(GROUPS[:1]))

    def test_stale_preview_and_changes_during_apply(self):
        body = self.body()
        with self.access.connect() as db:
            db.execute('UPDATE grants SET scope=?', (json.dumps(dict(self.scope, send=False)),))
        self.assert_unchanged_failure(body)
        for mutation in ['scope', 'revoked', 'epoch', 'revision']:
            with self.access.connect() as db:
                db.execute('UPDATE grants SET scope=?,revoked=0,epoch=?,revision=0', (json.dumps(self.scope), self.access.epoch()))
            body = self.body()
            def change():
                with self.access.connect() as db:
                    if mutation == 'scope':
                        db.execute('UPDATE grants SET scope=?', (json.dumps(dict(self.scope, send=False)),))
                    elif mutation == 'revoked':
                        db.execute('UPDATE grants SET revoked=1')
                    elif mutation == 'revision':
                        db.execute('UPDATE grants SET revision=revision+1')
                    else:
                        db.execute("UPDATE grants SET epoch='changed'")
            self.bot.after_directory = change
            with self.assertRaises(ValueError):
                self.editor.add('grant', body)
            self.assertEqual(json.loads(self.row()['scope'])['conversations'], self.scope['conversations'])
            self.bot.after_directory = None

    def test_source_replacement_during_preview_and_directory_errors(self):
        def change_source():
            with self.store.connect() as db:
                db.execute("UPDATE local_change_meta SET epoch='epoch-2'")
        self.bot.after_directory = change_source
        with self.assertRaises(ValueError):
            self.editor.listed('grant')
        with self.store.connect() as db:
            db.execute("UPDATE local_change_meta SET epoch='epoch-1'")
        self.bot.after_directory = None
        self.bot.rows = None
        with self.assertRaises(ValueError):
            self.editor.listed('grant')

    def test_native_revision_guards_both_edit_directions(self):
        old = self.access.by_id('grant')
        body = self.body()
        self.access.update_permissions('grant', dict(expected_revision=0, media=True))
        self.assert_unchanged_failure(body)
        self.editor.add('grant', self.body())
        with self.assertRaises(GrantConflict):
            self.access.update_permissions('grant', dict(expected_revision=1, media=False))
        with self.assertRaises(AccessDenied):
            self.access.ensure_revision(old)
        self.assertTrue(self.access.by_id('grant')['scope']['media'])
        self.assertEqual(self.row()['revision'], 2)

    def test_prepared_native_permission_edit_cannot_overwrite_added_groups(self):
        entered = threading.Event()
        release = threading.Event()
        class NativeClient:
            def login(self):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError('fixture release timed out')
                return ACCOUNT
        with patch('chatlocal.onebot.Client', return_value=NativeClient()), ThreadPoolExecutor(1) as pool:
            pending = pool.submit(self.access.update_permissions, 'grant', dict(expected_revision=0, qq_profile=True))
            try:
                self.assertTrue(entered.wait(5))
                self.editor.add('grant', self.body())
            finally:
                release.set()
            with self.assertRaises(GrantConflict):
                pending.result(5)
        saved = self.access.by_id('grant')
        self.assertEqual(saved['revision'], 1)
        self.assertFalse(saved['scope'].get('qq_profile'))
        self.assertEqual(len(saved['scope']['conversations']), 3)

    def test_ui_route_origin_headers_and_exact_add_payload(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from chatlocal import mcp_routes
        class Service:
            def __init__(self, access):
                self.source_base = ''
        with patch.object(mcp_routes, 'MCPService', Service), patch('chatlocal.mcp_scope_edit.Client', return_value=self.bot):
            app = FastAPI()
            mcp_routes.install_mcp_routes(app, self.store)
            client = TestClient(app)
            route = '/api/mcp/connections/grant/groups'
            choices = client.get(route)
            self.assertEqual(choices.status_code, 200)
            body = dict(group_ids=GROUPS, revision=choices.json()['revision'])
            before = self.row()
            for headers in [{}, {'X-ChatWeave-UI': '1', 'Origin': 'https://evil.example'},
                            {'X-ChatWeave-UI': '1', 'Sec-Fetch-Site': 'cross-site'},
                            {'X-ChatWeave-UI': '1', 'Host': 'evil.example'}]:
                response = client.post(route, json=body, headers=headers)
                self.assertEqual(response.status_code, 403, response.text)
                self.assertEqual(self.row(), before)
            response = client.post(route, json=body, headers={'X-ChatWeave-UI': '1'})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual([item['group_id'] for item in response.json()['added']], GROUPS)
            stale = client.post(route, json=body, headers={'X-ChatWeave-UI': '1'})
            self.assertEqual(stale.status_code, 409, stale.text)


class SessionPreservationTests(unittest.TestCase):
    def test_real_session_and_native_edit_lifecycle(self):
        import check_terminal_backend as backend
        fixture = backend.TerminalTests()
        fixture.setUp()
        try:
            fixture.configure()
            fixture.enable()
            grant = fixture.grant()
            service = fixture.app.state.tulpa_mcp
            access = service.access
            sid = fixture.api('POST', '/sessions/start', dict(connection_id=grant['id'], conversation_id='111:group:222',
                persona_preset='little_whale_v2', idempotency_key='scope-addition-fixture'))['session_id']
            with access.connect() as db:
                before = dict(db.execute('SELECT * FROM chat_sessions WHERE id=?', (sid,)).fetchone())
                token_hash = db.execute('SELECT token_hash FROM grants WHERE id=?', (grant['id'],)).fetchone()[0]
            old_grant = access.by_id(grant['id'], source=False)
            route = '/api/mcp/connections/' + grant['id'] + '/groups'
            with patch('chatlocal.mcp_scope_edit.Client', return_value=FakeOneBot()):
                choices = fixture.client.get(route)
                self.assertEqual(choices.status_code, 200, choices.text)
                response = fixture.client.post(route, json=dict(group_ids=GROUPS, revision=choices.json()['revision']))
            self.assertEqual(response.status_code, 200, response.text)
            with access.connect() as db:
                after = dict(db.execute('SELECT * FROM chat_sessions WHERE id=?', (sid,)).fetchone())
                self.assertEqual(db.execute('SELECT token_hash FROM grants WHERE id=?', (grant['id'],)).fetchone()[0], token_hash)
            self.assertEqual(after, before)
            self.assertTrue(after['active'])
            current = access.authorize(grant['token'], source=False)
            self.assertEqual(current['revision'], old_grant['revision'] + 1)
            with self.assertRaises(AccessDenied):
                access.ensure_revision(old_grant)
            stale = fixture.client.put('/api/mcp/connections/' + grant['id'], json=dict(media=True, expected_revision=0))
            self.assertEqual(stale.status_code, 409, stale.text)
            self.assertTrue(service.tools.chat.row(sid)['active'])
            # Ordinary permission edits retain their native stop/cancel semantics.
            changed = fixture.client.put('/api/mcp/connections/' + grant['id'], json=dict(media=True, expected_revision=1))
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertEqual(changed.json()['stopped_sessions'], 1)
            self.assertFalse(service.tools.chat.row(sid)['active'])
            self.assertEqual(backend.SL.sent, [])
        finally:
            fixture.tearDown()


if __name__ == '__main__':
    unittest.main(verbosity=2)
