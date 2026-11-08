"""B-23: the Grafana alerting provisioning file must survive env interpolation.

Deploy 2026-11-08: `chatid: ${TELEGRAM_CHAT_ID}` became a bare number after
Grafana substituted the env var, YAML read it as an int and Grafana refused to
start ("cannot unmarshal number into Go struct field Config.chatid of type
string"). Grafana crash-looped; no alerts, no dashboards.
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

PATH = os.path.join(os.path.dirname(__file__), '..', 'monitoring', 'grafana',
                    'provisioning', 'alerting', 'hydra_alerts.yml')


def _interpolated(env):
    text = open(PATH, encoding='utf-8').read()
    return re.sub(r'\$\{(\w+)\}', lambda m: env[m.group(1)], text)


def test_file_is_valid_yaml_before_interpolation():
    doc = yaml.safe_load(open(PATH, encoding='utf-8'))
    assert doc['apiVersion'] == 1 and doc['contactPoints'] and doc['groups']


def test_numeric_chat_id_stays_a_string_after_interpolation():
    doc = yaml.safe_load(_interpolated({'TELEGRAM_BOT_TOKEN': '123:abc', 'TELEGRAM_CHAT_ID': '1234567890'}))
    settings = doc['contactPoints'][0]['receivers'][0]['settings']
    assert isinstance(settings['chatid'], str) and settings['chatid'] == '1234567890'
    assert isinstance(settings['bottoken'], str)


def test_negative_group_chat_id_also_string():
    doc = yaml.safe_load(_interpolated({'TELEGRAM_BOT_TOKEN': '123:abc', 'TELEGRAM_CHAT_ID': '-1001234567890'}))
    assert doc['contactPoints'][0]['receivers'][0]['settings']['chatid'] == '-1001234567890'


def test_every_rule_has_condition_and_data():
    doc = yaml.safe_load(_interpolated({'TELEGRAM_BOT_TOKEN': 't', 'TELEGRAM_CHAT_ID': '1'}))
    rules = [r for g in doc['groups'] for r in g['rules']]
    assert len(rules) >= 9
    for r in rules:
        refs = {d['refId'] for d in r['data']}
        assert r['condition'] in refs, r['title']
        assert r['uid'] and r['title']
