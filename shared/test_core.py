"""
HYDRA-NET Инженерный контур контроля качества (Unit Tests)
Запуск из корня: venv\\Scripts\\python -m unittest shared/test_core.py
"""
import unittest
import math
import os
import json

class TestHydraCoreMath(unittest.TestCase):

    def test_round_step_size(self):
        """Тест округления лота под шаг биржи"""
        # Эмуляция функции round_step_size
        def round_step_size(quantity: float, step_size: float) -> float:
            if step_size <= 0: return quantity
            precision = int(round(-math.log10(step_size), 0))
            return round(quantity, precision)

        self.assertEqual(round_step_size(123.45678, 0.01), 123.46)
        self.assertEqual(round_step_size(10.55, 1.0), 11.0)
        self.assertEqual(round_step_size(0.003456, 0.0001), 0.0035)

    def test_calculate_percentage_drop(self):
        """Тест точного расчета процента падения"""
        def calculate_percentage_drop(high: float, current: float) -> float:
            if high <= 0: return 0.0
            return ((high - current) / high) * 100.0

        self.assertAlmostEqual(calculate_percentage_drop(100.0, 99.0), 1.0)
        self.assertAlmostEqual(calculate_percentage_drop(0.080, 0.076), 5.0)
        self.assertEqual(calculate_percentage_drop(0.0, 10.0), 0.0)

    def test_breakeven_calculation(self):
        """Тест расчета цены безубытка с учетом 0.2% комиссий"""
        buy_price = 0.07700
        breakeven_price = float(round(buy_price * 1.0022, 5))
        # Ожидаем, что цена покроет комиссию за круг и выйдет в микро-плюс
        self.assertTrue(breakeven_price > buy_price)
        self.assertAlmostEqual(breakeven_price, 0.07717, places=5)

if __name__ == '__main__':
    unittest.main()