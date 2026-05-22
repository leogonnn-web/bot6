"""Global safety check (JSON bridge with external scanner)."""
import json
import os
import sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'shared')))
from logger_setup import logger


class SafetyMixin:
    def _check_global_safety(self) -> bool:
        """
        JSON-мост безопасности: проверяет shared/market_status.json на наличие global_freeze.
        Возвращает False, если внешний сканер активировал глобальную заморозку.
        """
        try:
            root_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
            status_file = os.path.join(root_path, 'shared', 'market_status.json')
            if not os.path.isfile(status_file):
                return True
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
            if status.get('global_freeze', False) is True:
                logger.warning("@GLOBAL_LOCK@ Сканер активировал глобальную заморозку!")
                return False
            return True
        except Exception as e:
            logger.error(f"@GLOBAL_LOCK_ERROR@ Failed to check global safety: {e}")
            return True
