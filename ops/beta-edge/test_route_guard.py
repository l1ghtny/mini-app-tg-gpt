import runpy
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from pathlib import Path

mod = runpy.run_path(str(Path(__file__).with_name('route_guard.py')), run_name='route_guard_test')
CONFIG = 'upstream lightny_beta_frontend {\n    server 10.78.0.5:30445;\n}\nupstream lightny_beta_backend {\n    server 10.78.0.5:30445;\n}\nserver {\n    server_name beta.app.lightnyai.ru;\n    proxy_next_upstream off;\n}\n'

class RoutingTest(unittest.TestCase):
    def setUp(self):
        self.current = mod['parse_upstreams'](CONFIG)
        self.streak = {route: {'10.78.0.1': 0, '10.78.0.5': 3, '10.78.0.9': 0}
                       for route in self.current}

    def outcomes(self, node2=False, new=True, main=False):
        return {route: {'10.78.0.1': node2, '10.78.0.5': new, '10.78.0.9': main}
                for route in self.current}

    def test_new_peer_needs_three_good_checks(self):
        for route in self.current:
            self.streak[route]['10.78.0.9'] = 2
        desired = mod['selected_members'](self.current, self.outcomes(main=True), self.streak)
        self.assertEqual(mod['render'](CONFIG, desired), CONFIG)
        for route in self.current:
            self.streak[route]['10.78.0.9'] = 3
        desired = mod['selected_members'](self.current, self.outcomes(main=True), self.streak)
        self.assertEqual(desired['backend'], ['10.78.0.5', '10.78.0.9'])
        rendered = mod['render'](CONFIG, desired)
        self.assertIn('server 10.78.0.9:30445 backup;', rendered)
        self.assertIn('proxy_next_upstream off;', rendered)
        self.assertEqual(mod['parse_upstreams'](rendered), desired)

    def test_active_peer_failure_promotes_verified_main(self):
        for route in self.current:
            self.streak[route]['10.78.0.9'] = 3
        two = mod['selected_members'](self.current, self.outcomes(main=True), self.streak)
        desired = mod['selected_members'](two, self.outcomes(new=False, main=True), self.streak)
        self.assertEqual(desired['backend'], ['10.78.0.9'])
        self.assertEqual(mod['parse_upstreams'](mod['render'](CONFIG, desired)), desired)

    def test_node2_can_join_as_third_after_recovery(self):
        for route in self.current:
            self.streak[route]['10.78.0.1'] = 3
            self.streak[route]['10.78.0.9'] = 3
        desired = mod['selected_members'](self.current, self.outcomes(True, True, True), self.streak)
        self.assertEqual(desired['backend'], ['10.78.0.5', '10.78.0.1', '10.78.0.9'])
        self.assertEqual(mod['parse_upstreams'](mod['render'](CONFIG, desired)), desired)

    def test_all_failed_retains_last_config(self):
        desired = mod['selected_members'](self.current, self.outcomes(False, False, False), self.streak)
        self.assertEqual(mod['render'](CONFIG, desired), CONFIG)

    def test_prepared_config_with_nginx_server_options_is_accepted(self):
        prepared = CONFIG.replace('10.78.0.5:30445;', '10.78.0.5:30445 max_fails=2 fail_timeout=10s;').replace(
            '10.78.0.5:8443;', '10.78.0.5:8443 max_fails=2 fail_timeout=10s;')
        self.assertEqual(mod['parse_upstreams'](prepared), self.current)

    def test_production_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            mod['parse_upstreams'](CONFIG.replace('beta.app.lightnyai.ru', 'app.lightnyai.ru'))

    def test_frontend_probe_rejects_spa_fallback(self):
        result = SimpleNamespace(returncode=0, stdout='<html>app</html>\n200')
        with patch.object(mod['subprocess'], 'run', return_value=result):
            self.assertFalse(mod['probe']('frontend', 'new'))

    def test_frontend_probe_requires_health_json(self):
        result = SimpleNamespace(returncode=0, stdout='{"status":"ok"}\n200')
        with patch.object(mod['subprocess'], 'run', return_value=result):
            self.assertTrue(mod['probe']('frontend', 'new'))

if __name__ == '__main__':
    unittest.main()
