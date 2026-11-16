"""B-23: the Grafana alerting provisioning file must survive env interpolation.

Deploy 2026-11-08: Grafana crash-looped on "cannot unmarshal number into Go
struct field Config.chatid of type string"; no alerts, no dashboards.

Grafana 10.4 parses the YAML first and only then expands env vars in every
string under `settings`; a string that changed during expansion is re-typed
with ParseInt / ParseFloat / ParseBool (provisioning/values/values.go,
interpolateIfaceValue; grafana/grafana#69950). YAML quoting therefore does not
help. `_grafana_value` below mirrors that algorithm.
"""
import os
import re

import pytest

yaml = pytest.importorskip("yaml")

PATH = os.path.join(os.path.dirname(__file__), '..', 'monitoring', 'grafana',
                    'provisioning', 'alerting', 'hydra_alerts.yml')
GO_BOOLS = {'1', 't', 'T', 'TRUE', 'true', 'True', '0', 'f', 'F', 'FALSE', 'false', 'False'}


def _grafana_value(val, env):
    """interpolateIfaceValue from Grafana 10.4.2 for one string under `settings`."""
    expand = lambda s: re.sub(r'\$\{(\w+)\}|\$(\w+)', lambda m: env.get(m.group(1) or m.group(2), ''), s)
    if '$$' in val:
        return '$'.join(expand(p) for p in val.split('$$'))
    expanded = expand(val)
    if expanded == val:
        return val
    if re.fullmatch(r'[+-]?[0-9]+', expanded):
        return int(expanded)
    if expanded == expanded.strip() and '_' not in expanded:
        try:
            return float(expanded)
        except ValueError:
            pass
    return expanded in {'1', 't', 'T', 'TRUE', 'true', 'True'} if expanded in GO_BOOLS else expanded


def _settings(env):
    doc = yaml.safe_load(open(PATH, encoding='utf-8'))
    raw = doc['contactPoints'][0]['receivers'][0]['settings']
    return {k: _grafana_value(v, env) if isinstance(v, str) else v for k, v in raw.items()}


def test_emulation_reproduces_the_grafana_bug():
    env = {'TELEGRAM_CHAT_ID': '1234567890'}
    assert _grafana_value('${TELEGRAM_CHAT_ID}', env) == 1234567890
    assert _grafana_value('no env here', env) == 'no env here'


@pytest.mark.parametrize('chat_id', ['1234567890', '-1001234567890'])
def test_chat_id_stays_a_string_after_grafana_interpolation(chat_id):
    settings = _settings({'TELEGRAM_BOT_TOKEN': '123456:abc-DEF', 'TELEGRAM_CHAT_ID': chat_id})
    assert isinstance(settings['chatid'], str), 'Grafana would re-type chatid to a number and refuse to start'
    assert settings['chatid'].strip() == chat_id


def test_bot_token_is_passed_through_verbatim():
    settings = _settings({'TELEGRAM_BOT_TOKEN': '123456:abc-DEF', 'TELEGRAM_CHAT_ID': '1'})
    assert settings['bottoken'] == '123456:abc-DEF'


def test_quantile_rules_drop_nan():
    """histogram_quantile over a window with no observations is NaN, and Grafana
    fires on NaN: OrderLatencyHigh alerted for a day (2026-11-16) on sides that
    simply had no orders in 5 minutes. A trailing comparison filters NaN out."""
    doc = yaml.safe_load(open(PATH, encoding='utf-8'))
    exprs = [d['model']['expr'] for g in doc['groups'] for r in g['rules'] for d in r['data'] if 'expr' in d['model']]
    quantiles = [e for e in exprs if 'histogram_quantile' in e]
    assert quantiles
    for e in quantiles:
        assert re.search(r'\)\s*>=\s*0\s*$', e), e


def test_every_rule_has_condition_and_data():
    doc = yaml.safe_load(open(PATH, encoding='utf-8'))
    assert doc['apiVersion'] == 1
    rules = [r for g in doc['groups'] for r in g['rules']]
    assert len(rules) >= 9
    for r in rules:
        refs = {d['refId'] for d in r['data']}
        assert r['condition'] in refs, r['title']
        assert r['uid'] and r['title']
