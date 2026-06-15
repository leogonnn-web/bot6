"""Замер реальной ликвидности/минималок по торгуемым парам на Bybit spot.

Публичные эндпоинты, ключи НЕ нужны, сделок НЕ делает. Для оценки осуществимости
реального мини-пилота: проходит ли $3-6 ордер по минимуму биржи и сколько съест
спред/слиппедж на неликвидных парах.
"""
import ccxt

# Топ реально торгуемых ботом пар (из server_stats) + майоры для сравнения
SYMBOLS = [
    'H/USDT', 'HOME/USDT', 'BSB/USDT', 'OPG/USDT', 'EDGE/USDT', 'XPL/USDT',
    'MEGA/USDT', 'LIT/USDT', 'WLD/USDT', 'VVV/USDT', 'HNT/USDT', 'SAHARA/USDT',
    'NEAR/USDT', 'IO/USDT', 'SOL/USDT', 'BTC/USDT',
]
SLOT = 3.0          # текущий слот, USDT
TP_PCT = 0.8        # take_profit_pct
GRID_PCT = 0.6      # grid_distance_pct

ex = ccxt.bybit({'enableRateLimit': True, 'options': {'defaultType': 'spot'}})
markets = ex.load_markets()

print('%-13s %-7s %8s %8s %9s %9s %s' % (
    'SYMBOL', 'exists', 'minCost', 'spread%', 'ask$<0.4%', 'ask$<0.8%', 'verdict'))
print('-' * 78)

for s in SYMBOLS:
    if s not in markets:
        print('%-13s %-7s' % (s, 'NO'))
        continue
    m = markets[s]
    min_cost = (m.get('limits', {}).get('cost', {}) or {}).get('min')
    min_amt = (m.get('limits', {}).get('amount', {}) or {}).get('min')
    try:
        ob = ex.fetch_order_book(s, limit=50)
        bid = ob['bids'][0][0]
        ask = ob['asks'][0][0]
        mid = (bid + ask) / 2.0
        spread = (ask - bid) / mid * 100.0
        # сколько USDT доступно на продажу (для нашей покупки) в пределах 0.4% и 0.8% от mid
        d04 = sum(p * a for p, a in ob['asks'] if p <= mid * 1.004)
        d08 = sum(p * a for p, a in ob['asks'] if p <= mid * 1.008)
    except Exception as e:
        print('%-13s ERR %s' % (s, e))
        continue

    # вердикт
    issues = []
    mc = min_cost if min_cost is not None else 0
    if mc > SLOT:
        issues.append('min>$%g' % SLOT)
    if spread > TP_PCT:
        issues.append('spread>TP')
    elif spread > 0.25:
        issues.append('spread>filter')
    if d08 < SLOT * 3:  # хотя бы 3 ордера влезают без сдвига за TP
        issues.append('thin')
    verdict = 'OK' if not issues else ','.join(issues)

    print('%-13s %-7s %8s %8.3f %9.0f %9.0f %s' % (
        s, 'yes',
        ('%.2f' % min_cost) if min_cost is not None else '?',
        spread, d04, d08, verdict))

print('\nПримечание: ask$<0.4% / <0.8% = объём USDT на продажу в пределах шага сетки / TP.')
print('Если он не сильно больше слота ($3) — наш ордер сам двигает цену (слиппедж).')
print('fee 0.1%%/сторону = 0.2%% round-trip против TP 0.8%% (четверть цели).')
