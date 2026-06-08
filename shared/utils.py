import json
import os
import time
from typing import Dict, List, Optional

from logger_setup import logger
from paths import SESSION_PROFIT_FILE

def calculate_percentage_drop(high: float, current: float) -> float:
    """Рассчитывает процент падения от локального максимума"""
    if high <= 0:
        return 0.0
    return ((high - current) / high) * 100.0

def round_step_size(quantity: float, step_size: float) -> float:
    """Округляет количество монеты под шаг лота биржи"""
    if step_size <= 0:
        return quantity
    import math
    precision = int(round(-math.log10(step_size), 0))
    return round(quantity, precision)

def realized_pnl(buy_price: float, sell_price: float, amount: float,
                 fee_pct: float = 0.1, slippage_pct: float = 0.0) -> float:
    """Net realized PnL including round-trip per-leg fees and adverse exit slippage.

    fee_pct: exchange fee percent charged per leg (applied to BOTH entry and exit).
    slippage_pct: adverse percent applied to the sell price (use for market/panic exits;
                  0 for passive limit/TP fills).
    """
    eff_sell = sell_price * (1.0 - slippage_pct / 100.0)
    gross = (eff_sell - buy_price) * amount
    fees = (buy_price + eff_sell) * amount * (fee_pct / 100.0)
    return gross - fees

def panic_window_stats(events: List, now: float, window_sec: float):
    """Given a list of (timestamp, loss) panic events, return (count, total_loss)
    for events within the trailing `window_sec`. `loss` values are signed
    (negative for losses). Pure function for the circuit-breaker.
    """
    count = 0
    total = 0.0
    for ts, loss in events:
        if now - ts <= window_sec:
            count += 1
            total += loss
    return count, total


def circuit_breaker_tripped(events: List, now: float, window_sec: float,
                            max_panics: int, max_loss_usd: float):
    """Return (tripped: bool, reason: str) if recent panic activity exceeds limits.

    max_loss_usd is a positive magnitude; trips when summed losses are worse
    than -max_loss_usd within the window.
    """
    count, total = panic_window_stats(events, now, window_sec)
    if max_panics > 0 and count >= max_panics:
        return True, f"panics={count}>={max_panics} in {int(window_sec)}s"
    if max_loss_usd > 0 and total <= -abs(max_loss_usd):
        return True, f"window_loss=${total:.2f}<=-${abs(max_loss_usd):.2f}"
    return False, ""


def chase_deadline(now: float, urgent: bool, urgent_sec: float, normal_sec: float) -> float:
    """Absolute deadline for a limit-chase exit, shorter when urgent."""
    return now + (urgent_sec if urgent else normal_sec)


def exit_backstop_decision(buy_price: float, current_price: float, urgent: bool,
                           urgent_skip_below_pct: float, deadline_passed: bool):
    """Decide whether to abandon limit-chasing and market-sell (backstop).

    Returns (backstop: bool, reason: str).
    - Hard adverse move (urgent only): price already far below entry -> market now.
    - Deadline passed: chase window expired -> market backstop.
    """
    if buy_price > 0:
        drop_pct = (current_price - buy_price) / buy_price * 100.0
        if urgent and drop_pct <= -abs(urgent_skip_below_pct):
            return True, f"hard_adverse({drop_pct:.2f}%)"
    if deadline_passed:
        return True, "deadline"
    return False, ""


def get_session_profit(trades: List[Dict]) -> float:
    """Считает общую прибыль текущей сессии"""
    profit = 0.0
    for trade in trades:
        if trade.get('side') == 'sell' and 'profit' in trade:
            profit += trade['profit']
    return profit

def format_symbol(symbol: str) -> str:
    """Приводит торговую пару к стандартному виду (например, btc-usdt -> BTC/USDT)"""
    return symbol.upper().replace('-', '/').replace('_', '/')

def safe_float(value) -> float:
    """Безопасно преобразует любое значение в float, защищая от падений"""
    try:
        if value is None:
            return 0.0
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def format_currency(value: float) -> str:
    """Красиво форматирует число под денежный формат (например, 10.5 -> $10.50)"""
    return f"${safe_float(value):.2f}"

def format_percentage(value: float) -> str:
    """Красиво форматирует число под процентный формат (например, 0.65 -> 0.65%)"""
    return f"{safe_float(value):.2f}%"

class ProfitManager:
    """Управляет расчетом и отслеживанием прибыли торговой сессии"""
    def __init__(self):
        self.session_profit = 0.0
        self.total_trades = 0
        self.winning_trades = 0
        self.start_time = time.time()

    def load(self) -> float:
        """Загружает профит сессии из session_profit.json в корне проекта."""
        if not os.path.exists(SESSION_PROFIT_FILE):
            return self.session_profit
        try:
            with open(SESSION_PROFIT_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self.session_profit = float(data.get('session_profit', 0.0))
            self.total_trades = int(data.get('total_trades', 0))
            self.winning_trades = int(data.get('winning_trades', 0))
        except Exception as e:
            logger.error(f"Failed to load session profit: {e}")
        return self.session_profit

    def save(self):
        """Сохраняет текущий профит сессии в session_profit.json."""
        try:
            payload = {
                'session_profit': self.session_profit,
                'total_trades': self.total_trades,
                'winning_trades': self.winning_trades,
                'updated_at': time.time(),
            }
            with open(SESSION_PROFIT_FILE, 'w', encoding='utf-8') as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save session profit: {e}")

    def update_profit(self, profit_amount: float):
        """Обновляет статистику после закрытия сделки"""
        self.session_profit += profit_amount
        self.total_trades += 1
        if profit_amount > 0:
            self.winning_trades += 1
        logger.info(f"💰 Профит сессии обновлен: ${self.session_profit:.2f} | Всего сделок: {self.total_trades}")

    def get_stats(self) -> dict:
        """Возвращает текущую статистику сессии"""
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades > 0 else 0.0
        uptime = time.time() - self.start_time
        return {
            "session_profit": self.session_profit,
            "total_trades": self.total_trades,
            "win_rate": win_rate,
            "uptime_seconds": int(uptime)
        }

class HealthChecker:
    """Проверяет здоровье рынка, величину спреда и стабильность подключения"""
    def __init__(self, max_spread: float = 0.5):
        self.max_spread = max_spread

    def check(self) -> bool:
        """Основной метод проверки здоровья для бота v17.0"""
        return True

    def check_market_health(self, ticker: dict) -> bool:
        """Проверяет спред торговой пары на бирже"""
        try:
            bid = ticker.get('bid', 0.0)
            ask = ticker.get('ask', 0.0)
            if not bid or not ask:
                logger.warning("⚠️ Биржа вернула пустые цены Bid/Ask")
                return False
                
            spread = ((ask - bid) / bid) * 100.0
            if spread > self.max_spread:
                logger.warning(f"⚠️ Слишком большой спред: {spread:.2f}% (Макс: {self.max_spread}%) - Пропуск")
                return False
                
            return True
        except Exception as e:
            logger.error(f"Ошибка при проверке здоровья рынка: {e}")
            return False      

class SoundNotifier:
    """Отвечает за звуковые сигналы при совершении сделок"""
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def play_notification(self, frequency: int = 1000, duration: int = 200):
        """Воспроизводит системный писк Windows при сделке"""
        if not self.enabled:
            return
        try:
            import winsound
            winsound.Beep(frequency, duration)
        except Exception:
            pass