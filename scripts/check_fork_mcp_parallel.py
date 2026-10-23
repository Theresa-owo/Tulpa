"""Portable transport/read-budget regressions using only isolated fixtures."""
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import anyio
import httpx
from starlette.responses import JSONResponse
from chatlocal import mcp_server, mcp_tools
from chatlocal.mcp_access import MCPAccess, AccessDenied, RateLimited, CHAT_WAIT_TOOLS, CHAT_MEDIA_TOOLS
from chatlocal.retrieval import Plan


def grant(**flags):
    scope = dict(platforms=['qq'], conversations=[], start='', end='',
                 prepare=False, voice=False, media=False, onebot=True,
                 send=True, manage=True, chat=False)
    scope.update(flags)
    return dict(id='fixture', accounts='{}', scope=scope, revision=0)


class Store:
    def __init__(self, root):
        self.path = root / 'history.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE messages(id INTEGER PRIMARY KEY,platform TEXT)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class Protocol:
    def __init__(self):
        self.request_context = SimpleNamespace(request=SimpleNamespace(
            headers={'authorization': 'Bearer fixture'},
            scope={'state': {'mcp_cancel': threading.Event()}}))

    def list_tools(self):
        def register(callback):
            self.list_callback = callback
            return callback
        return register

    def call_tool(self, **kwargs):
        def register(callback):
            self.call_callback = callback
            return callback
        return register


class Manager:
    def __init__(self):
        self.requests = 0

    async def handle_request(self, scope, receive, send):
        self.requests += 1
        await JSONResponse({'ok': True})(scope, receive, send)


def application(access, tools):
    service = object.__new__(mcp_server.MCPService)
    service.access, service.tools = access, tools
    service.source_base = ''
    service.pending_lock = threading.Lock()
    service.pending = {}
    service.wait_limiter = service.media_limiter = None
    protocol, manager = Protocol(), Manager()
    with patch.object(mcp_server, 'Server', return_value=protocol), \
            patch.object(mcp_server, 'StreamableHTTPSessionManager', return_value=manager):
        app = service.application(18777)
    return service, protocol, manager, app


class ParallelTests(unittest.TestCase):
    def test_native_file_transfer_deadline_matches_streaming_budget(self):
        async def scenario():
            tools = SimpleNamespace(call=lambda *args: {'ok': True}, result=lambda data: data)
            service, protocol, manager, app = application(None, tools)
            deadline, seen = anyio.fail_after, []
            @contextmanager
            def record(seconds):
                seen.append(seconds)
                with deadline(seconds):
                    yield
            with patch.object(mcp_server.anyio, 'fail_after', record):
                for name in ('download_chat_file','send_chat_file','wait_chat_messages_many','get_data_status'):
                    self.assertEqual(await protocol.call_callback(name, {}), {'ok': True})
            self.assertEqual(seen, [1260,1260,240,240])
        anyio.run(scenario)

    def test_slow_authentication_and_revision_checks_leave_event_loop_available(self):
        async def scenario(kind):
            started, release = threading.Event(), threading.Event()
            thread_ids, timed_out = [], []
            loop_thread = threading.get_ident()

            def slow_check():
                thread_ids.append(threading.get_ident())
                started.set()
                if not release.wait(.5):
                    timed_out.append(True)
                return grant()

            def authorize(token, *, source):
                self.assertEqual(token, 'fixture')
                self.assertFalse(source)
                return slow_check()

            access = SimpleNamespace(authorize=authorize, ensure_revision=lambda row: slow_check())
            tools = SimpleNamespace(schemas=lambda row: [])
            service, protocol, manager, app = application(access, tools)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url='http://127.0.0.1:18777', trust_env=False) as client:
                async def request():
                    if kind == 'endpoint':
                        response = await client.get('/mcp', headers={'authorization': 'Bearer fixture'})
                        self.assertEqual(response.status_code, 200)
                    else:
                        self.assertEqual(await protocol.list_callback(), [])

                async with anyio.create_task_group() as group:
                    group.start_soon(request)
                    with anyio.fail_after(2):
                        while not started.is_set():
                            await anyio.sleep(.001)
                    release.set()
            self.assertFalse(timed_out, 'authorization blocked the event loop')
            self.assertTrue(all(tid != loop_thread for tid in thread_ids))
            self.assertEqual(len(thread_ids), 1 if kind == 'endpoint' else 2)

        for kind in ('endpoint', 'listing'):
            with self.subTest(kind=kind):
                anyio.run(scenario, kind)

    def test_denied_transport_and_changed_revision_do_not_disclose_tools(self):
        def deny(*args, **kwargs):
            raise AccessDenied('fixture rejection')

        async def scenario():
            access = SimpleNamespace(authorize=deny, ensure_revision=deny)
            tools = SimpleNamespace(schemas=lambda row: [])
            service, protocol, manager, app = application(access, tools)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                        base_url='http://127.0.0.1:18777', trust_env=False) as client:
                response = await client.get('/mcp', headers={'authorization': 'Bearer fixture'})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(manager.requests, 0)
            with self.assertRaises(AccessDenied):
                await protocol.list_callback()
            access.authorize = lambda *args, **kwargs: grant()
            with self.assertRaises(AccessDenied):
                await protocol.list_callback()
        anyio.run(scenario)

    def test_single_and_many_waits_share_dedicated_worker_limiter(self):
        async def scenario():
            tools = SimpleNamespace(call=lambda *args: {'ok': True}, result=lambda data: data)
            service, protocol, manager, app = application(None, tools)
            worker, seen = anyio.to_thread.run_sync, []
            async def record(*args, **kwargs):
                seen.append(kwargs.get('limiter'))
                return await worker(*args, **kwargs)
            with patch.object(anyio.to_thread, 'run_sync', side_effect=record):
                for name in (*CHAT_WAIT_TOOLS, 'get_chat_session'):
                    await protocol.call_callback(name, {})
            self.assertIsNotNone(seen[0])
            self.assertIs(seen[0], seen[1])
            self.assertEqual(seen[0].total_tokens, 16)
            self.assertIsNone(seen[2])
        anyio.run(scenario)

    def test_discovery_and_history_skip_onebot_probe_but_remote_reads_keep_it(self):
        remote_client = object()
        class ScopedTools:
            def __init__(self, *args, **kwargs):
                self.messages = {}
            def execute(inner, name, args):
                self.assertIs(inner.qq_client, remote_client if name == 'read_qq_group' else None)
                return {'items': []}

        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            store = Store(root)
            access = MCPAccess(store, path=root / 'access.sqlite3')
            allowed, checks = grant(), []
            access.authorize = lambda *args, **kwargs: checks.append('authorize') or allowed
            access.ensure_revision = lambda row: checks.append('revision') or row
            access.plan = lambda row: Plan(platforms=['qq'])
            tools = object.__new__(mcp_tools.MCPTools)
            tools.access, tools.store = access, store
            tools.slots = threading.BoundedSemaphore(4)
            with patch.object(tools, 'onebot', side_effect=AssertionError('unrelated OneBot probe')):
                names = {row['name'] for row in tools.schemas(allowed)}
                self.assertTrue({'read_qq_group', 'send_qq_message'} <= names)
                limited = tools.schemas(grant(onebot=False, send=False, manage=False))
                self.assertNotIn('read_qq_group', {row['name'] for row in limited})
                with patch.object(mcp_tools, 'MCPChatTools', ScopedTools):
                    result = tools.call('fixture', 'search_messages', {'query': 'fixture'}, threading.Event())
                self.assertFalse(result.isError)
                self.assertGreaterEqual(checks.count('authorize'), 2)
                self.assertGreaterEqual(checks.count('revision'), 2)
            with patch.object(tools, 'onebot', return_value=remote_client) as probe, \
                    patch.object(mcp_tools, 'MCPChatTools', ScopedTools):
                result = tools.call('fixture', 'read_qq_group',
                                    {'conversation_id': '111:group:222', 'view': 'info'}, threading.Event())
                self.assertFalse(result.isError)
                probe.assert_called_once_with(allowed)

    def test_many_wait_shares_single_wait_capacity_and_stop_is_available(self):
        tools = object.__new__(mcp_tools.MCPTools)
        tools.chat_slots = threading.BoundedSemaphore(1)
        tools.access = SimpleNamespace(begin_call=lambda *args: 1, finish_call=lambda *args: None,
                                       ensure_revision=lambda row: row)
        tools.chat = SimpleNamespace(call=lambda *args: {'event': 'stopped', 'messages': []})
        self.assertTrue(tools.chat_slots.acquire(blocking=False))
        try:
            for name in CHAT_WAIT_TOOLS:
                result = tools.chat_call(grant(), 'fixture', name, {}, threading.Event())
                self.assertEqual(result.structuredContent['error_code'], 'chat_busy')
            result = tools.chat_call(grant(), 'fixture', 'stop_chat_session', {}, threading.Event())
            self.assertFalse(result.isError)
        finally:
            tools.chat_slots.release()

    def test_many_wait_preserves_per_session_compaction_and_pixel_references(self):
        tools = object.__new__(mcp_tools.MCPTools)
        tools.chat_slots = threading.BoundedSemaphore(1)
        counts = []
        tools.access = SimpleNamespace(begin_call=lambda *args: 1,
                                       finish_call=lambda *args: counts.append(args[-1]),
                                       ensure_revision=lambda row: row)
        continuation = {'tool': 'wait_chat_messages_many', 'arguments': {'session_ids': ['first', 'second']}}
        tools.chat = SimpleNamespace(call=lambda *args: dict(event='messages', next_call=continuation,
            results=[dict(session_id=sid,messages=[dict(id=index)],next_call={'tool':'plan_chat_reply'})
                     for index,sid in enumerate(('first','second'),1)]))
        versions = []
        def compact(item, version):
            versions.append((item['session_id'], version))
            return dict(item, compact=True)
        def prepare(chat, allowed, item, cancel):
            packet = dict(item, materials=dict(images=[dict(content_indexes=[1])]))
            packet['messages'][0]['segments'] = [dict(pixels=dict(content_indexes=[1]))]
            image = mcp_tools.types.ImageContent(type='image', data='Zg==', mimeType='image/png')
            return packet, [image]
        with patch('chatlocal.mcp_chat_output.compact_wait', side_effect=compact), \
                patch('chatlocal.mcp_chat_materials.prepare', side_effect=prepare):
            result = tools.chat_call(grant(), 'fixture', 'wait_chat_messages_many',
                dict(known_prompt_versions={'first':'v1','second':'v2'}), threading.Event())
        self.assertFalse(result.isError)
        self.assertEqual(versions, [('first','v1'),('second','v2')])
        self.assertEqual(len(result.content), 3)
        self.assertEqual(result.structuredContent['next_call'], continuation)
        for index, item in enumerate(result.structuredContent['results'], 1):
            self.assertTrue(item['compact'])
            self.assertEqual(item['next_call']['tool'], 'plan_chat_reply')
            self.assertEqual(item['materials']['images'][0]['content_indexes'], [index])
            self.assertEqual(item['messages'][0]['segments'][0]['pixels']['content_indexes'], [index])
        self.assertEqual(counts, [2])

    def test_recent_index_and_existing_minute_and_hourly_budgets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            access = MCPAccess(SimpleNamespace(path=root / 'unused.sqlite3'), path=root / 'access.sqlite3')
            now = time.time()
            with access.connect() as db:
                db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',
                               [('fixture', 'search_messages', now - 86400, 'ok')] * 20000)
                placeholders = ','.join('?' for _ in CHAT_MEDIA_TOOLS)
                sql = ("SELECT count(*) FROM calls WHERE grant_id=? AND at>? AND tool!='stop_chat_session' "
                       f'AND tool NOT IN ({placeholders})')
                plan = [row['detail'] for row in db.execute('EXPLAIN QUERY PLAN ' + sql,
                                                          ('fixture', now - 60, *CHAT_MEDIA_TOOLS))]
                self.assertTrue(any('calls_recent' in row and 'at>?' in row for row in plan), plan)
            for index in range(120):
                access.begin_call('fixture', CHAT_WAIT_TOOLS[index % 2])
            for name in (*CHAT_WAIT_TOOLS, 'search_messages', 'get_chat_session'):
                with self.subTest(name=name), self.assertRaises(RateLimited):
                    access.begin_call('fixture', name)
            access.begin_call('fixture', 'stop_chat_session')
            access.begin_call('fixture', 'list_chat_stickers')
            access.begin_call('another-grant', 'search_messages')
            with access.connect() as db:
                db.executemany('INSERT INTO calls(grant_id,tool,at,status) VALUES(?,?,?,?)',
                               [('hourly', 'prepare_file', now, 'ok')] * 6 +
                               [('hourly', 'download_file', now, 'ok')] * 6)
            for name in ('prepare_file', 'download_file'):
                with self.subTest(hourly=name), self.assertRaises(RateLimited):
                    access.begin_call('hourly', name)
            print('isolated recent-budget plan:', '; '.join(plan))


if __name__ == '__main__':
    unittest.main(verbosity=2)
