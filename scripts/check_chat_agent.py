"""Pure controller tests: no model process, authentication or real QQ writes."""
import io
from pathlib import Path
import sys
import unittest

sys.path[:0]=[str(Path(__file__).resolve().parents[1]),str(Path(__file__).resolve().parent)]
from chat_agent import Budget,AppServer,overrides


def usage(i,c,o=0,last_i=None,last_c=None,last_o=0):
    def row(a,b,d):return dict(inputTokens=a,cachedInputTokens=b,outputTokens=d,reasoningOutputTokens=0,totalTokens=a+d)
    return dict(total=row(i,c,o),last=row(i if last_i is None else last_i,c if last_c is None else last_c,last_o))


class Tests(unittest.TestCase):
    def test_initial_and_steady_budgets_distinguish_cached_tokens(self):
        b=Budget();self.assertIsNone(b.record(usage(17000,0,5),now=1))
        b.initializing=False
        self.assertIsNone(b.record(usage(37000,19000,105,last_i=20000,last_c=19000,last_o=100),now=2))
        count=len(b.samples);self.assertIsNone(b.record(b.usage,now=3));self.assertEqual(len(b.samples),count)
        self.assertEqual(b.record(usage(70000,20000,110,last_i=33000,last_c=1000),now=4),'single_call_token_limit')
        b=Budget();self.assertEqual(b.record(usage(50000,0),now=1),'startup_token_limit')

    def test_rolling_limits_and_usage_regression(self):
        b=Budget();b.initializing=False
        for n in (1,2):self.assertIsNone(b.record(usage(n*7000,0,last_i=7000),now=n))
        self.assertEqual(b.record(usage(21000,0,last_i=7000),now=3),'five_minute_token_limit')
        b=Budget();self.assertIsNone(b.record(usage(17000,0),now=1));b.initializing=False
        self.assertEqual(b.record(usage(1200000,1170000,last_i=20000,last_c=19000),now=2),'raw_fifteen_minute_token_limit')
        self.assertEqual(b.record(usage(100,0),now=3),'usage_counter_regressed')

    def test_fence_precedes_model_interrupt_and_is_idempotent(self):
        a=object.__new__(AppServer);calls=[];a.reason=None;a.thread_id='thread';a.turn_id='turn';a.on_update=lambda:calls.append('status')
        a.fence=lambda *x:calls.append('fence');a.send=lambda *x:calls.append(x[0])
        a.pause('limit');a.pause('limit')
        self.assertEqual(calls,['fence','turn/interrupt','status'])

    def test_overrides_are_local_and_keep_provider_auth_shell_and_tier(self):
        cfg=dict(model_provider='openai',service_tier='default',mcp_servers={'tulpa':{},'other':{}})
        result=overrides(cfg)
        self.assertFalse(result['mcp_servers.other.enabled']);self.assertNotIn('model_provider',result)
        for k in ('service_tier','approval_policy','sandbox_mode','features.shell_tool'):self.assertNotIn(k,result)
        self.assertEqual(cfg['service_tier'],'default')
        with self.assertRaises(ValueError):overrides(dict(model_provider='third_party'))


if __name__=='__main__':unittest.main()
