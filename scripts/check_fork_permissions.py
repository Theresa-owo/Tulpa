"""Fork flags, file/image budgets and client timeout, with isolated state only."""
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from chatlocal.mcp_access import MCPAccess, RateLimited
from chatlocal.mcp_connect import codex_config
from chatlocal.store import Store


class PermissionChecks(unittest.TestCase):
    def test_terminal_permission_dependencies(self):
        from chatlocal.tui import model
        enabled=model.normalize_flags(dict(send=True,chat=True,chat_file_send=True,chat_moderate=True))
        self.assertTrue(enabled['chat_file_send'] and enabled['chat_moderate'])
        disabled=model.normalize_flags(dict(chat_file_send=True,chat_moderate=True))
        self.assertFalse(disabled['chat_file_send'] or disabled['chat_moderate'])

    def test_native_controls_require_chat_and_send(self):
        for flag in ('chat_file_send','chat_moderate'):
            with self.assertRaises(ValueError):
                MCPAccess.validate_permissions(dict(platforms=['qq'],**{flag:True}))
            with self.assertRaises(ValueError):
                MCPAccess.validate_permissions(dict(platforms=['qq'],chat=True,**{flag:True}))
            MCPAccess.validate_permissions(dict(platforms=['qq'],chat=True,send=True,**{flag:True}))

    def test_download_shared_budget_and_image_read_isolation(self):
        with tempfile.TemporaryDirectory() as td:
            access=MCPAccess(Store(Path(td)/'chats.sqlite3'))
            for _ in range(6):
                access.begin_call('fixture','download_chat_file')
                access.begin_call('fixture','download_chat_image')
            for tool in ('download_chat_file','download_chat_image'):
                with self.assertRaises(RateLimited):access.begin_call('fixture',tool)
            access.begin_call('fixture','read_chat_image')
            for _ in range(60):access.begin_call('other','prepare_local_image')
            with self.assertRaises(RateLimited):access.begin_call('other','read_chat_image')
            access.begin_call('other','get_data_status')

    def test_native_write_limits(self):
        with tempfile.TemporaryDirectory() as td:
            access=MCPAccess(Store(Path(td)/'chats.sqlite3'))
            for tool in ('send_chat_file','moderate_chat_member'):
                for _ in range(12):access.begin_call(tool,tool)
                with self.assertRaises(RateLimited):access.begin_call(tool,tool)
                access.begin_call(tool,'wait_chat_messages_many')

    def test_client_timeout_and_explicit_tool_flags(self):
        def entry(scope):return tomllib.loads(codex_config('http://localhost:18777/mcp','fixture',scope))['mcp_servers']['tulpa']
        self.assertEqual(entry({})['tool_timeout_sec'],240)
        full=entry(dict(chat=True,send=True,chat_files=True,chat_file_send=True,chat_moderate=True))
        self.assertEqual(full['tool_timeout_sec'],1260)
        for tool in ('wait_chat_messages_many','download_chat_file','send_chat_file','moderate_chat_member'):
            self.assertIn(tool,full['tools'])
        limited=entry(dict(chat=True,send=True,chat_images=True))
        self.assertNotIn('send_chat_file',limited['tools'])
        self.assertNotIn('moderate_chat_member',limited['tools'])


if __name__=='__main__':unittest.main()
