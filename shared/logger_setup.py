"""
Logger setup for HYDRA Bot
Configures logging with file and console output
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from paths import LOGS_DIR

# Create logs directory if not exists
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# Configure logger
logger = logging.getLogger('HYDRA')
logger.setLevel(logging.DEBUG)

# File handler (logs/bot.log)
file_handler = RotatingFileHandler(
    os.path.join(LOGS_DIR, 'bot.log'),
    maxBytes=10*1024*1024,  # 10MB
    backupCount=5,
    encoding='utf-8'
)
file_handler.setLevel(logging.DEBUG)
file_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(file_formatter)

# Console handler (with emoji support)
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
console_handler.setFormatter(console_formatter)

# Add handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# Export logger
__all__ = ['logger']
