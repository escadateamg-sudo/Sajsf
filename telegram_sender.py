#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Mass Sender Pro
Массовая рассылка сообщений в Telegram с поддержкой тысяч готовых аккаунтов и кнопок
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import threading
from concurrent.futures import ThreadPoolExecutor

try:
    import msvcrt  # Только для Windows
except ImportError:
    msvcrt = None

try:
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession
    from telethon.tl.types import KeyboardButtonUrl
    from telethon.tl.custom import Button
    import colorama
    from colorama import Fore, Back, Style, init
    import aiofiles
except ImportError as e:
    print(f"Ошибка импорта: {e}")
    print("Установите необходимые библиотеки:")
    print("pip install telethon colorama aiofiles")
    sys.exit(1)

# Инициализация colorama для Windows
init(autoreset=True)


class Config:
    """Класс для управления конфигурацией программы"""
    
    def __init__(self):
        self.config_file = "config.json"
        self.default_config = {
            "paths": {
                "users_file": "users.txt",
                "messages_file": "messages.txt", 
                "apidata_file": "apidata.txt",
                "proxies_file": "proxies.txt",
                "sessions_dir": "sessions",
                "sent_users_file": "sent_users/sent_users.txt",
                "logs_file": "telegram_sender.log",
                "stats_file": "account_stats.json"
            },
            "limits": {
                "daily_limit": 25,
                "min_delay": 5,
                "max_delay": 15,
                "accounts_per_proxy": 1,
                "accounts_per_api": 30,
                "max_concurrent_accounts": 3000
            },
            "settings": {
                "emoji_probability": 0.3,
                "retry_attempts": 3,
                "flood_wait_threshold": 600,
                "button": {
                    "text": "Перейти",
                    "url": "https://example.com"
                }
            }
        }
        self.config = self.load_config()

    def load_config(self) -> Dict:
        """Загружает конфигурацию из файла"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded_config = json.load(f)
                    # Глубокое слияние конфигураций
                    merged_config = self._deep_merge(self.default_config.copy(), loaded_config)
                    return merged_config
            except Exception as e:
                print(f"Ошибка загрузки конфигурации: {e}")
        return self.default_config.copy()

    def _deep_merge(self, default: dict, loaded: dict) -> dict:
        """Глубоко сливает две конфигурации"""
        for key, value in loaded.items():
            if key in default and isinstance(default[key], dict) and isinstance(value, dict):
                default[key] = self._deep_merge(default[key], value)
            else:
                default[key] = value
        return default

    def save_config(self):
        """Сохраняет конфигурацию в файл"""
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"Ошибка сохранения конфигурации: {e}")

    def get(self, key_path: str, default=None):
        """Получает значение по пути ключа (например, 'limits.daily_limit')"""
        keys = key_path.split('.')
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, key_path: str, value):
        """Устанавливает значение по пути ключа"""
        keys = key_path.split('.')
        config = self.config
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        config[keys[-1]] = value
        self.save_config()


class Logger:
    """Класс для логирования с цветным выводом"""
    
    def __init__(self, log_file: str):
        self.log_file = log_file
        self.logs = []
        self.max_logs = 1000
        
        # Настройка логгера
        logging.basicConfig(
            filename=log_file,
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            encoding='utf-8'
        )
        self.logger = logging.getLogger(__name__)

    def log(self, message: str, level: str = "INFO", color: str = Fore.WHITE):
        """Добавляет сообщение в лог"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {level}: {message}"
        
        # Добавление в список логов для интерфейса
        self.logs.append({
            "timestamp": timestamp,
            "level": level,
            "message": message,
            "color": color
        })
        
        # Ограничение количества логов в памяти
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]
        
        # Запись в файл
        if level == "ERROR":
            self.logger.error(message)
        elif level == "WARNING":
            self.logger.warning(message)
        else:
            self.logger.info(message)

    def success(self, message: str):
        self.log(message, "SUCCESS", Fore.GREEN)

    def error(self, message: str):
        self.log(message, "ERROR", Fore.RED)

    def warning(self, message: str):
        self.log(message, "WARNING", Fore.YELLOW)

    def info(self, message: str):
        self.log(message, "INFO", Fore.CYAN)

    def button_info(self, text: str, url: str):
        """Специальный метод для логирования добавления кнопки"""
        self.log(f"Добавлена кнопка: [{text}] -> {url}", "BUTTON", Fore.MAGENTA)


class DataLoader:
    """Класс для загрузки данных из файлов"""
    
    def __init__(self, config: Config, logger: Logger):
        self.config = config
        self.logger = logger
        self.emojis = ["😊", "🎉", "🔥", "💫", "⭐", "🌟", "💎", "🚀", "✨", "🎯"]

    def process_spintax(self, text: str) -> Tuple[str, Optional[Dict[str, str]]]:
        """Обрабатывает спинтакс в тексте и извлекает данные кнопки"""
        # Сначала ищем кнопку в формате {button:текст|url}
        button_data = None
        button_pattern = r'\{button:([^|]+)\|([^}]+)\}'
        button_match = re.search(button_pattern, text)
        
        if button_match:
            button_text = button_match.group(1).strip()
            button_url = button_match.group(2).strip()
            button_data = {"text": button_text, "url": button_url}
            # Удаляем кнопку из текста
            text = re.sub(button_pattern, '', text)
        
        # Обрабатываем обычный спинтакс
        pattern = r'\{([^}]+)\}'
        
        def replace_spin(match):
            options = match.group(1).split('|')
            return random.choice(options).strip()
        
        processed_text = re.sub(pattern, replace_spin, text)
        return processed_text, button_data

    def add_random_emoji(self, text: str) -> str:
        """Добавляет случайный эмодзи с заданной вероятностью"""
        if random.random() < self.config.get("settings.emoji_probability", 0.3):
            emoji = random.choice(self.emojis)
            # Добавляем эмодзи в случайное место
            if random.choice([True, False]):
                return f"{emoji} {text}"
            else:
                return f"{text} {emoji}"
        return text

    def load_users(self) -> List[str]:
        """Загружает список пользователей"""
        users_file = self.config.get("paths.users_file")
        if not os.path.exists(users_file):
            self.logger.error(f"Файл пользователей не найден: {users_file}")
            return []

        users = []
        try:
            with open(users_file, 'r', encoding='utf-8') as f:
                for line in f:
                    user = line.strip()
                    if user:
                        # Убираем @ если он есть
                        if user.startswith('@'):
                            user = user[1:]
                        users.append(user)
            
            self.logger.success(f"Загружено пользователей: {len(users)}")
            return users
        except Exception as e:
            self.logger.error(f"Ошибка загрузки пользователей: {e}")
            return []

    def load_messages(self) -> List[str]:
        """Загружает список сообщений"""
        messages_file = self.config.get("paths.messages_file")
        if not os.path.exists(messages_file):
            self.logger.error(f"Файл сообщений не найден: {messages_file}")
            return []

        messages = []
        try:
            with open(messages_file, 'r', encoding='utf-8') as f:
                content = f.read().strip()
                # Разделяем сообщения по номерам (1., 2., 3., и т.д.)
                raw_messages = re.split(r'\n(?=\d+\.)', content)
                
                for msg in raw_messages:
                    msg = msg.strip()
                    if msg:
                        # Убираем номер в начале сообщения
                        msg = re.sub(r'^\d+\.', '', msg).strip()
                        if msg:
                            messages.append(msg)
            
            self.logger.success(f"Загружено сообщений: {len(messages)}")
            return messages
        except Exception as e:
            self.logger.error(f"Ошибка загрузки сообщений: {e}")
            return []

    def load_api_data(self) -> List[Tuple[int, str]]:
        """Загружает API ключи"""
        api_file = self.config.get("paths.apidata_file")
        if not os.path.exists(api_file):
            self.logger.error(f"Файл API данных не найден: {api_file}")
            return []

        api_data = []
        try:
            with open(api_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ':' in line:
                        api_id, api_hash = line.split(':', 1)
                        api_data.append((int(api_id), api_hash))
            
            self.logger.success(f"Загружено API ключей: {len(api_data)}")
            return api_data
        except Exception as e:
            self.logger.error(f"Ошибка загрузки API данных: {e}")
            return []

    def load_proxies(self) -> List[Dict[str, str]]:
        """Загружает прокси"""
        proxies_file = self.config.get("paths.proxies_file")
        if not os.path.exists(proxies_file):
            self.logger.warning(f"Файл прокси не найден: {proxies_file}")
            return []

        proxies = []
        try:
            with open(proxies_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if ':' in line:
                        parts = line.split(':')
                        if len(parts) >= 4:
                            proxy = {
                                'proxy_type': 'socks5',
                                'addr': parts[0],
                                'port': int(parts[1]),
                                'username': parts[2],
                                'password': parts[3]
                            }
                            proxies.append(proxy)
            
            self.logger.success(f"Загружено прокси: {len(proxies)}")
            return proxies
        except Exception as e:
            self.logger.error(f"Ошибка загрузки прокси: {e}")
            return []

    def load_sessions(self) -> List[str]:
        """Загружает файлы сессий"""
        sessions_dir = self.config.get("paths.sessions_dir")
        if not os.path.exists(sessions_dir):
            os.makedirs(sessions_dir)
            self.logger.warning(f"Создана папка для сессий: {sessions_dir}")
            self.logger.info("Поместите ваши .session файлы в эту папку")
            return []

        sessions = []
        try:
            for file in os.listdir(sessions_dir):
                if file.endswith('.session'):
                    session_path = os.path.join(sessions_dir, file)  # Оставляем расширение
                    sessions.append(session_path)
            
            self.logger.success(f"Найдено сессий: {len(sessions)}")
            return sessions
        except Exception as e:
            self.logger.error(f"Ошибка загрузки сессий: {e}")
            return []

    def load_sent_users(self) -> set:
        """Загружает список уже отправленных пользователей"""
        sent_file = self.config.get("paths.sent_users_file")
        sent_users = set()
        
        if os.path.exists(sent_file):
            try:
                with open(sent_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        user = line.strip()
                        if user:
                            sent_users.add(user)
                self.logger.info(f"Загружено отправленных пользователей: {len(sent_users)}")
            except Exception as e:
                self.logger.error(f"Ошибка загрузки отправленных пользователей: {e}")
        else:
            # Создаем директорию если не существует
            os.makedirs(os.path.dirname(sent_file), exist_ok=True)
        
        return sent_users

    def save_sent_user(self, username: str):
        """Сохраняет пользователя в список отправленных"""
        sent_file = self.config.get("paths.sent_users_file")
        try:
            with open(sent_file, 'a', encoding='utf-8') as f:
                f.write(f"{username}\n")
        except Exception as e:
            self.logger.error(f"Ошибка сохранения отправленного пользователя: {e}")


class Statistics:
    """Класс для сбора и отображения статистики"""
    
    def __init__(self, config: Config, logger: Logger):
        self.config = config
        self.logger = logger
        self.sent_count = 0
        self.failed_count = 0
        self.active_accounts = 0
        self.total_accounts = 0
        self.start_time = None
        self.account_stats = {}  # username -> {"sent": 0, "failed": 0, "status": "active", "total_sent": 0, "total_failed": 0}
        self.lock = threading.Lock()
        
        # Загружаем сохраненную статистику
        self.load_account_stats()

    def load_account_stats(self):
        """Загружает статистику аккаунтов из файла"""
        stats_file = self.config.get("paths.stats_file")
        if os.path.exists(stats_file):
            try:
                with open(stats_file, 'r', encoding='utf-8') as f:
                    saved_stats = json.load(f)
                    
                    # Восстанавливаем статистику
                    for account, stats in saved_stats.items():
                        self.account_stats[account] = {
                            "sent": 0,  # Счетчик текущей сессии
                            "failed": 0,  # Счетчик текущей сессии
                            "status": "inactive",  # Статус текущей сессии
                            "total_sent": stats.get("total_sent", 0),  # Общий счетчик
                            "total_failed": stats.get("total_failed", 0),  # Общий счетчик
                            "last_activity": stats.get("last_activity", "")
                        }
                
                total_sent = sum(stats["total_sent"] for stats in self.account_stats.values())
                total_failed = sum(stats["total_failed"] for stats in self.account_stats.values())
                
                self.logger.info(f"Загружена статистика аккаунтов: {len(self.account_stats)} аккаунтов, "
                               f"всего отправлено: {total_sent}, ошибок: {total_failed}")
                
            except Exception as e:
                self.logger.error(f"Ошибка загрузки статистики аккаунтов: {e}")
                self.account_stats = {}

    def save_account_stats(self):
        """Сохраняет статистику аккаунтов в файл"""
        stats_file = self.config.get("paths.stats_file")
        try:
            # Подготавливаем данные для сохранения
            save_data = {}
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            for account, stats in self.account_stats.items():
                save_data[account] = {
                    "total_sent": stats["total_sent"],
                    "total_failed": stats["total_failed"],
                    "last_activity": current_time if stats["status"] == "active" else stats.get("last_activity", "")
                }
            
            # Создаем директорию если не существует
            os.makedirs(os.path.dirname(stats_file), exist_ok=True)
            
            # Сохраняем в файл
            with open(stats_file, 'w', encoding='utf-8') as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
                
        except Exception as e:
            self.logger.error(f"Ошибка сохранения статистики аккаунтов: {e}")

    def start_tracking(self):
        """Начинает отслеживание времени"""
        self.start_time = datetime.now()

    def add_sent(self, account: str = None):
        """Увеличивает счетчик отправленных"""
        with self.lock:
            self.sent_count += 1
            if account:
                if account not in self.account_stats:
                    self.account_stats[account] = {
                        "sent": 0, "failed": 0, "status": "active",
                        "total_sent": 0, "total_failed": 0, "last_activity": ""
                    }
                
                self.account_stats[account]["sent"] += 1
                self.account_stats[account]["total_sent"] += 1
                
                # Периодически сохраняем статистику (каждые 10 отправленных сообщений)
                if self.account_stats[account]["total_sent"] % 10 == 0:
                    self.save_account_stats()

    def add_failed(self, account: str = None):
        """Увеличивает счетчик неудачных отправок"""
        with self.lock:
            self.failed_count += 1
            if account:
                if account not in self.account_stats:
                    self.account_stats[account] = {
                        "sent": 0, "failed": 0, "status": "active",
                        "total_sent": 0, "total_failed": 0, "last_activity": ""
                    }
                
                self.account_stats[account]["failed"] += 1
                self.account_stats[account]["total_failed"] += 1
                
                # Сохраняем статистику при ошибках
                self.save_account_stats()

    def set_account_status(self, account: str, status: str):
        """Устанавливает статус аккаунта"""
        with self.lock:
            if account not in self.account_stats:
                self.account_stats[account] = {
                    "sent": 0, "failed": 0, "status": status,
                    "total_sent": 0, "total_failed": 0, "last_activity": ""
                }
            else:
                self.account_stats[account]["status"] = status
            
            # Сохраняем при изменении статуса
            if status in ["banned", "flood_wait", "inactive"]:
                self.save_account_stats()

    def update_active_accounts(self, active: int, total: int):
        """Обновляет количество активных аккаунтов"""
        with self.lock:
            self.active_accounts = active
            self.total_accounts = total

    def get_success_rate(self) -> float:
        """Возвращает процент успешных отправок"""
        total = self.sent_count + self.failed_count
        return (self.sent_count / total * 100) if total > 0 else 0.0

    def get_total_success_rate(self) -> float:
        """Возвращает общий процент успешных отправок за все время"""
        total_sent = sum(stats["total_sent"] for stats in self.account_stats.values())
        total_failed = sum(stats["total_failed"] for stats in self.account_stats.values())
        total = total_sent + total_failed
        return (total_sent / total * 100) if total > 0 else 0.0

    def get_total_sent(self) -> int:
        """Возвращает общее количество отправленных сообщений за все время"""
        return sum(stats["total_sent"] for stats in self.account_stats.values())

    def get_total_failed(self) -> int:
        """Возвращает общее количество неудачных отправок за все время"""
        return sum(stats["total_failed"] for stats in self.account_stats.values())

    def get_runtime(self) -> str:
        """Возвращает время работы"""
        if not self.start_time:
            return "00:00:00"
        
        elapsed = datetime.now() - self.start_time
        hours = elapsed.seconds // 3600
        minutes = (elapsed.seconds % 3600) // 60
        seconds = elapsed.seconds % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def cleanup_and_save(self):
        """Очищает статистику текущей сессии и сохраняет общую статистику"""
        with self.lock:
            # Сбрасываем счетчики текущей сессии, но сохраняем общие
            for account in self.account_stats:
                self.account_stats[account]["sent"] = 0
                self.account_stats[account]["failed"] = 0
                self.account_stats[account]["status"] = "inactive"
            
            # Сохраняем финальную статистику
            self.save_account_stats()
            
            self.logger.success("Статистика аккаунтов сохранена")


class TelegramSender:
    """Основной класс для отправки сообщений"""
    
    def __init__(self, config: Config, logger: Logger, data_loader: DataLoader, statistics: Statistics):
        self.config = config
        self.logger = logger
        self.data_loader = data_loader
        self.stats = statistics
        self.active_clients = {}
        self.daily_counts = {}  # account -> {date: count}
        self.is_running = False

    async def create_client(self, session_file: str, api_id: int, api_hash: str, proxy: Dict = None) -> Optional[TelegramClient]:
        """Создает и подключает клиент Telegram"""
        try:
            client_kwargs = {}
            if proxy:
                client_kwargs['proxy'] = proxy

            client = TelegramClient(session_file, api_id, api_hash, **client_kwargs)
            await client.connect()
            
            if await client.is_user_authorized():
                me = await client.get_me()
                username = me.username or f"id{me.id}"
                self.logger.success(f"Подключен аккаунт: @{username}")
                return client
            else:
                self.logger.warning(f"Аккаунт не авторизован: {os.path.basename(session_file)}")
                await client.disconnect()
                return None
                
        except Exception as e:
            self.logger.error(f"Ошибка подключения {os.path.basename(session_file)}: {e}")
            return None

    def can_send_today(self, account: str) -> bool:
        """Проверяет, может ли аккаунт отправлять сообщения сегодня"""
        today = datetime.now().date()
        daily_limit = self.config.get("limits.daily_limit", 25)
        
        if account not in self.daily_counts:
            self.daily_counts[account] = {}
        
        if today not in self.daily_counts[account]:
            self.daily_counts[account][today] = 0
        
        return self.daily_counts[account][today] < daily_limit

    def increment_daily_count(self, account: str):
        """Увеличивает дневной счетчик для аккаунта"""
        today = datetime.now().date()
        if account not in self.daily_counts:
            self.daily_counts[account] = {}
        if today not in self.daily_counts[account]:
            self.daily_counts[account][today] = 0
        self.daily_counts[account][today] += 1

    def create_button_markup(self, button_text: str, button_url: str):
        """Создает разметку кнопки для сообщения"""
        try:
            if not button_text or not button_url:
                self.logger.warning(f"Пустой текст или URL кнопки: text='{button_text}', url='{button_url}'")
                return None
            # Проверяем и добавляем префикс https://, если отсутствует
            if not button_url.startswith(('http://', 'https://')):
                button_url = f"https://{button_url}"
                self.logger.info(f"Добавлен префикс https:// к URL кнопки: {button_url}")
            # Создаем кнопку с URL
            button = Button.url(button_text, button_url)
            self.logger.info(f"Создана кнопка: text='{button_text}', url='{button_url}'")
            return [[button]]
        except Exception as e:
            self.logger.error(f"Ошибка создания кнопки: text='{button_text}', url='{button_url}', error={e}")
            return None

    async def send_message_to_user(self, client: TelegramClient, username: str, message: str, account: str) -> bool:
        """Отправляет сообщение пользователю"""
        try:
            # Проверяем дневной лимит
            if not self.can_send_today(account):
                self.logger.warning(f"Достигнут дневной лимит для {account}")
                return False

            # Обрабатываем спинтакс и извлекаем данные кнопки
            processed_message, message_button = self.data_loader.process_spintax(message)
            processed_message = self.data_loader.add_random_emoji(processed_message)
            
            # Определяем кнопку для отправки
            button_markup = None
            button_text = None
            button_url = None
            
            if message_button:
                # Используем кнопку из сообщения
                button_text = message_button["text"]
                button_url = message_button["url"]
                self.logger.info(f"Используется кнопка из сообщения: text='{button_text}', url='{button_url}'")
            else:
                # Используем кнопку из конфигурации
                config_button_text = self.config.get("settings.button.text")
                config_button_url = self.config.get("settings.button.url")
                
                if config_button_text and config_button_url:
                    button_text = config_button_text
                    button_url = config_button_url
                    self.logger.info(f"Используется кнопка из конфигурации: text='{button_text}', url='{button_url}'")
                else:
                    self.logger.warning(f"Кнопка не задана: конфигурация пуста (text='{config_button_text}', url='{config_button_url}')")
            
            # Создаем разметку кнопки если есть данные
            if button_text and button_url:
                button_markup = self.create_button_markup(button_text, button_url)
                if button_markup:
                    self.logger.button_info(button_text, button_url)
                else:
                    self.logger.warning(f"Кнопка не добавлена для сообщения пользователю @{username} из-за ошибки создания разметки")
            else:
                self.logger.warning(f"Кнопка не добавлена для сообщения пользователю @{username}: отсутствуют данные кнопки")
            
            # Отправляем сообщение с кнопкой или без
            if button_markup:
                await client.send_message(username, processed_message, buttons=button_markup)
            else:
                await client.send_message(username, processed_message)
            
            # Обновляем статистику
            self.increment_daily_count(account)
            self.stats.add_sent(account)
            self.data_loader.save_sent_user(username)
            
            button_info = f" (с кнопкой '{button_text}')" if button_text else " (без кнопки)"
            self.logger.success(f"✓ Отправлено @{username} через {account}{button_info}")
            return True
            
        except errors.FloodWaitError as e:
            wait_time = e.seconds
            if wait_time > self.config.get("settings.flood_wait_threshold", 600):
                self.logger.error(f"FloodWait слишком долгий ({wait_time}s) для {account}, отключаем")
                self.stats.set_account_status(account, "flood_wait")
                return False
            else:
                self.logger.warning(f"FloodWait {wait_time}s для {account}")
                await asyncio.sleep(wait_time + 1)
                return await self.send_message_to_user(client, username, message, account)
                
        except errors.UserPrivacyRestrictedError:
            self.logger.warning(f"Пользователь @{username} ограничил сообщения")
            self.stats.add_failed(account)
            return False
            
        except errors.UserDeactivatedError:
            self.logger.warning(f"Пользователь @{username} деактивирован")
            self.stats.add_failed(account)
            return False
            
        except errors.InputUserDeactivatedError:
            self.logger.warning(f"Пользователь @{username} деактивирован")
            self.stats.add_failed(account)
            return False
            
        except errors.UserBannedInChannelError:
            self.logger.error(f"Аккаунт {account} забанен")
            self.stats.set_account_status(account, "banned")
            return False
            
        except Exception as e:
            self.logger.error(f"Ошибка отправки @{username} через {account}: {e}")
            self.stats.add_failed(account)
            return False

    async def worker(self, client: TelegramClient, account: str, users: List[str], messages: List[str]):
        """Воркер для отправки сообщений"""
        try:
            for username in users:
                if not self.is_running:
                    break
                    
                if not self.can_send_today(account):
                    self.logger.info(f"Дневной лимит достигнут для {account}")
                    break
                
                # Выбираем случайное сообщение
                message = random.choice(messages)
                
                # Отправляем сообщение
                success = await self.send_message_to_user(client, username, message, account)
                
                if not success:
                    # Если аккаунт заблокирован или есть проблемы, выходим
                    account_status = self.stats.account_stats.get(account, {}).get("status", "active")
                    if account_status in ["banned", "flood_wait"]:
                        break
                
                # Случайная задержка
                delay = random.uniform(
                    self.config.get("limits.min_delay", 5),
                    self.config.get("limits.max_delay", 15)
                )
                await asyncio.sleep(delay)
                
        except Exception as e:
            self.logger.error(f"Ошибка в воркере {account}: {e}")
        finally:
            await client.disconnect()
            if account in self.active_clients:
                del self.active_clients[account]

    async def start_sending(self, users: List[str], messages: List[str], sessions: List[str], 
                          api_data: List[Tuple[int, str]], proxies: List[Dict]):
        """Запускает процесс отправки сообщений"""
        if not users or not messages or not sessions:
            self.logger.error("Недостаточно данных для начала рассылки")
            return

        self.is_running = True
        self.stats.start_tracking()
        
        # Исключаем уже отправленных пользователей
        sent_users = self.data_loader.load_sent_users()
        users = [u for u in users if u not in sent_users]
        
        if not users:
            self.logger.warning("Все пользователи уже получили сообщения")
            return

        self.logger.info(f"Начинаем рассылку для {len(users)} пользователей")
        
        # Распределяем ресурсы
        max_accounts = min(len(sessions), self.config.get("limits.max_concurrent_accounts", 3000))
        accounts_per_api = self.config.get("limits.accounts_per_api", 30)
        accounts_per_proxy = self.config.get("limits.accounts_per_proxy", 1)
        
        # Разбиваем пользователей на части
        users_per_account = len(users) // max_accounts + 1
        user_chunks = [users[i:i + users_per_account] for i in range(0, len(users), users_per_account)]
        
        # Создаем задачи
        tasks = []
        for i in range(min(max_accounts, len(user_chunks))):
            if i >= len(sessions):
                break
                
            session_file = sessions[i]
            api_id, api_hash = api_data[i // accounts_per_api % len(api_data)]
            proxy = proxies[i // accounts_per_proxy % len(proxies)] if proxies else None
            user_chunk = user_chunks[i]
            
            if not user_chunk:
                continue
            
            # Создаем клиент
            client = await self.create_client(session_file, api_id, api_hash, proxy)
            if client:
                account = os.path.basename(session_file)
                self.active_clients[account] = client
                self.stats.set_account_status(account, "active")
                
                # Создаем задачу
                task = asyncio.create_task(
                    self.worker(client, account, user_chunk, messages)
                )
                tasks.append(task)
        
        self.stats.update_active_accounts(len(tasks), len(sessions))
        self.logger.info(f"Запущено {len(tasks)} воркеров")
        
        # Ждем завершения всех задач
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        self.is_running = False
        self.logger.success("Рассылка завершена")

    def stop_sending(self):
        """Останавливает процесс отправки"""
        self.is_running = False
        self.logger.info("Получен сигнал остановки рассылки")


class Interface:
    """Класс для командного интерфейса"""
    
    def __init__(self, config: Config, logger: Logger, data_loader: DataLoader, 
                 sender: TelegramSender, statistics: Statistics):
        self.config = config
        self.logger = logger
        self.data_loader = data_loader
        self.sender = sender
        self.stats = statistics
        self.current_menu = "main"
        self.log_scroll = 0
        self.is_running = True
        
        # Данные
        self.users = []
        self.messages = []
        self.sessions = []
        self.api_data = []
        self.proxies = []

    def clear_screen(self):
        """Очищает экран"""
        os.system('cls' if os.name == 'nt' else 'clear')

    def print_header(self):
        """Выводит заголовок программы"""
        print(f"{Fore.CYAN}{'='*80}")
        print(f"{Fore.CYAN}               TELEGRAM MASS SENDER PRO v1.0")
        print(f"{Fore.CYAN}         Массовая рассылка сообщений в Telegram")
        print(f"{Fore.CYAN}{'='*80}")
        print()

    def print_statistics(self):
        """Выводит статистику"""
        total_sent = self.stats.get_total_sent()
        total_failed = self.stats.get_total_failed()
        total_success_rate = self.stats.get_total_success_rate()
        
        print(f"{Fore.YELLOW}╔════════════════════ СТАТИСТИКА ════════════════════╗")
        print(f"{Fore.YELLOW}║ Текущая сессия:                                   ║")
        print(f"{Fore.YELLOW}║   Отправлено: {Fore.GREEN}{self.stats.sent_count:<8} {Fore.YELLOW}║ Ошибок: {Fore.RED}{self.stats.failed_count:<12} {Fore.YELLOW}║")
        print(f"{Fore.YELLOW}║   Успешность: {Fore.CYAN}{self.stats.get_success_rate():.1f}%{Fore.YELLOW}     ║ Время работы: {Fore.CYAN}{self.stats.get_runtime():<8} {Fore.YELLOW}║")
        print(f"{Fore.YELLOW}║                                                   ║")
        print(f"{Fore.YELLOW}║ За все время:                                     ║")
        print(f"{Fore.YELLOW}║   Отправлено: {Fore.GREEN}{total_sent:<8} {Fore.YELLOW}║ Ошибок: {Fore.RED}{total_failed:<12} {Fore.YELLOW}║")
        print(f"{Fore.YELLOW}║   Успешность: {Fore.CYAN}{total_success_rate:.1f}%{Fore.YELLOW}                              ║")
        print(f"{Fore.YELLOW}║                                                   ║")
        print(f"{Fore.YELLOW}║ Активных аккаунтов: {Fore.GREEN}{self.stats.active_accounts:<3} {Fore.YELLOW}║ Всего аккаунтов: {Fore.CYAN}{self.stats.total_accounts:<7} {Fore.YELLOW}║")
        print(f"{Fore.YELLOW}╚═══════════════════════════════════════════════════╝")
        print()

    def print_logs(self, max_lines: int = 10):
        """Выводит логи"""
        print(f"{Fore.MAGENTA}╔════════════════════ ЛОГИ ═══════════════════════════╗")
        
        if not self.logger.logs:
            print(f"{Fore.MAGENTA}║{' ' * 52}║")
            print(f"{Fore.MAGENTA}║{' Нет логов для отображения':^52}║")
            print(f"{Fore.MAGENTA}║{' ' * 52}║")
        else:
            visible_logs = self.logger.logs[self.log_scroll:self.log_scroll + max_lines]
            for log_entry in visible_logs:
                timestamp = log_entry["timestamp"]
                level = log_entry["level"]
                message = log_entry["message"]
                color = log_entry["color"]
                
                # Обрезаем сообщение если слишком длинное
                if len(message) > 40:
                    message = message[:37] + "..."
                
                log_line = f"[{timestamp}] {message}"
                if len(log_line) > 50:
                    log_line = log_line[:47] + "..."
                
                print(f"{Fore.MAGENTA}║ {color}{log_line:<50}{Fore.MAGENTA} ║")
        
        print(f"{Fore.MAGENTA}╚════════════════════════════════════════════════════╝")
        print()

    def print_main_menu(self):
        """Выводит главное меню"""
        print(f"{Fore.WHITE}╔═══════════════════ ГЛАВНОЕ МЕНЮ ═══════════════════╗")
        print(f"{Fore.WHITE}║  1. Загрузить данные                               ║")
        print(f"{Fore.WHITE}║  2. Начать рассылку                                ║")
        print(f"{Fore.WHITE}║  3. Настройки                                      ║")
        print(f"{Fore.WHITE}║  4. Статистика аккаунтов                           ║")
        print(f"{Fore.WHITE}║  5. Остановить рассылку                            ║")
        print(f"{Fore.WHITE}║  6. Очистить статистику                            ║")
        print(f"{Fore.WHITE}║  7. Выход                                          ║")
        print(f"{Fore.WHITE}╚════════════════════════════════════════════════════╝")
        print()
        
        # Показываем статус данных
        print(f"{Fore.CYAN}Статус загруженных данных:")
        print(f"├─ Пользователи: {Fore.GREEN if self.users else Fore.RED}{len(self.users)}")
        print(f"├─ Сообщения: {Fore.GREEN if self.messages else Fore.RED}{len(self.messages)}")
        print(f"├─ Сессии: {Fore.GREEN if self.sessions else Fore.RED}{len(self.sessions)}")
        print(f"├─ API ключи: {Fore.GREEN if self.api_data else Fore.RED}{len(self.api_data)}")
        print(f"└─ Прокси: {Fore.GREEN if self.proxies else Fore.YELLOW}{len(self.proxies)}")
        
        # Показываем статус кнопки
        button_text = self.config.get("settings.button.text")
        button_url = self.config.get("settings.button.url")
        if button_text and button_url:
            print(f"{Fore.CYAN}└─ Кнопка: {Fore.GREEN}'{button_text}' -> {button_url}")
        else:
            print(f"{Fore.CYAN}└─ Кнопка: {Fore.YELLOW}не настроена")
        print()

    def print_settings_menu(self):
        """Выводит меню настроек"""
        print(f"{Fore.WHITE}╔═══════════════════ НАСТРОЙКИ ══════════════════════╗")
        print(f"{Fore.WHITE}║  1. Дневной лимит на аккаунт: {self.config.get('limits.daily_limit'):<17} ║")
        print(f"{Fore.WHITE}║  2. Мин. задержка (сек): {self.config.get('limits.min_delay'):<21} ║")
        print(f"{Fore.WHITE}║  3. Макс. задержка (сек): {self.config.get('limits.max_delay'):<20} ║")
        print(f"{Fore.WHITE}║  4. Аккаунтов на API: {self.config.get('limits.accounts_per_api'):<24} ║")
        print(f"{Fore.WHITE}║  5. Аккаунтов на прокси: {self.config.get('limits.accounts_per_proxy'):<21} ║")
        print(f"{Fore.WHITE}║  6. Макс. одновременных аккаунтов: {self.config.get('limits.max_concurrent_accounts'):<11} ║")
        print(f"{Fore.WHITE}║  7. Вероятность эмодзи: {self.config.get('settings.emoji_probability'):<20} ║")
        print(f"{Fore.WHITE}║  8. Настройка кнопки                               ║")
        print(f"{Fore.WHITE}║  9. Пути к файлам                                  ║")
        print(f"{Fore.WHITE}║ 10. Назад в главное меню                           ║")
        print(f"{Fore.WHITE}╚════════════════════════════════════════════════════╝")
        print()

    def print_button_settings(self):
        """Выводит настройки кнопки"""
        button_text = self.config.get("settings.button.text", "")
        button_url = self.config.get("settings.button.url", "")
        
        print(f"{Fore.WHITE}╔════════════════ НАСТРОЙКИ КНОПКИ ══════════════════╗")
        print(f"{Fore.WHITE}║ Текущие настройки:                                ║")
        print(f"{Fore.WHITE}║   Текст кнопки: {button_text:<32} ║")
        print(f"{Fore.WHITE}║   URL: {button_url:<41} ║")
        print(f"{Fore.WHITE}║                                                   ║")
        print(f"{Fore.WHITE}║  1. Изменить текст кнопки                         ║")
        print(f"{Fore.WHITE}║  2. Изменить URL кнопки                           ║")
        print(f"{Fore.WHITE}║  3. Отключить кнопку                              ║")
        print(f"{Fore.WHITE}║  4. Назад                                         ║")
        print(f"{Fore.WHITE}╚═══════════════════════════════════════════════════╝")
        print()
        print(f"{Fore.CYAN}Примечание: Кнопка в сообщении имеет приоритет над настройками!")
        print(f"{Fore.CYAN}Формат в сообщении: {{button:Текст кнопки|https://example.com}}")
        print()

    def print_account_stats(self):
        """Выводит статистику по аккаунтам"""
        print(f"{Fore.WHITE}╔════════════════ СТАТИСТИКА АККАУНТОВ ══════════════╗")
        
        if not self.stats.account_stats:
            print(f"{Fore.WHITE}║{' ' * 52}║")
            print(f"{Fore.WHITE}║{' Нет данных о аккаунтах':^52}║")
            print(f"{Fore.WHITE}║{' ' * 52}║")
        else:
            print(f"{Fore.WHITE}║ {'Аккаунт':<15} {'Сессия':<8} {'Всего':<8} {'Статус':<10} ║")
            print(f"{Fore.WHITE}║ {'':<15} {'S/F':<8} {'S/F':<8} {'':<10} ║")
            print(f"{Fore.WHITE}║{'-' * 52}║")
            
            # Сортируем аккаунты по общему количеству отправленных сообщений
            sorted_accounts = sorted(
                self.stats.account_stats.items(),
                key=lambda x: x[1]["total_sent"],
                reverse=True
            )
            
            for account, stats in sorted_accounts[:20]:  # Показываем топ-20
                status_color = Fore.GREEN if stats["status"] == "active" else Fore.RED
                session_stats = f"{stats['sent']}/{stats['failed']}"
                total_stats = f"{stats['total_sent']}/{stats['total_failed']}"
                
                print(f"{Fore.WHITE}║ {account:<15} {session_stats:<8} {total_stats:<8} "
                      f"{status_color}{stats['status']:<10}{Fore.WHITE} ║")
        
        print(f"{Fore.WHITE}╚════════════════════════════════════════════════════╝")
        print(f"{Fore.CYAN}S - отправлено, F - ошибок. Показаны топ-20 аккаунтов.")
        print(f"{Fore.CYAN}Нажмите любую клавишу для возврата в главное меню...")

    def clear_statistics(self):
        """Очищает статистику аккаунтов"""
        print(f"{Fore.YELLOW}Очистка статистики аккаунтов...")
        print(f"{Fore.RED}ВНИМАНИЕ: Это действие удалит ВСЮ статистику аккаунтов!")
        print(f"{Fore.CYAN}Текущая статистика:")
        print(f"  - Всего аккаунтов: {len(self.stats.account_stats)}")
        print(f"  - Общее количество отправленных: {self.stats.get_total_sent()}")
        print(f"  - Общее количество ошибок: {self.stats.get_total_failed()}")
        print()
        
        response = self.get_user_input("Вы уверены, что хотите очистить статистику? (yes/no): ")
        if response.lower() == 'yes':
            try:
                # Очищаем статистику в памяти
                self.stats.account_stats = {}
                
                # Удаляем файл статистики
                stats_file = self.config.get("paths.stats_file")
                if os.path.exists(stats_file):
                    os.remove(stats_file)
                
                print(f"{Fore.GREEN}✓ Статистика аккаунтов успешно очищена!")
                self.logger.success("Статистика аккаунтов очищена пользователем")
                
            except Exception as e:
                print(f"{Fore.RED}✗ Ошибка при очистке статистики: {e}")
                self.logger.error(f"Ошибка очистки статистики: {e}")
        else:
            print(f"{Fore.YELLOW}Очистка статистики отменена.")
        
        input(f"{Fore.CYAN}Нажмите Enter для продолжения...")

    def get_user_input(self, prompt: str) -> str:
        """Получает ввод от пользователя"""
        print(f"{Fore.CYAN}{prompt}", end="")
        return input().strip()

    def load_all_data(self):
        """Загружает все данные"""
        print(f"{Fore.YELLOW}Загрузка данных...")
        
        # Проверяем наличие всех файлов
        files_to_check = [
            ("users_file", "Файл пользователей"),
            ("messages_file", "Файл сообщений"), 
            ("apidata_file", "Файл API данных")
        ]
        
        missing_files = []
        for file_key, file_desc in files_to_check:
            file_path = self.config.get(f"paths.{file_key}")
            if not os.path.exists(file_path):
                missing_files.append((file_key, file_desc, file_path))
        
        if missing_files:
            print(f"{Fore.RED}Не найдены следующие файлы:")
            for file_key, file_desc, file_path in missing_files:
                print(f"  - {file_desc}: {file_path}")
            
            response = self.get_user_input("Хотите указать пути вручную? (y/n): ")
            if response.lower() == 'y':
                for file_key, file_desc, file_path in missing_files:
                    new_path = self.get_user_input(f"Введите путь к {file_desc}: ")
                    if os.path.exists(new_path):
                        self.config.set(f"paths.{file_key}", new_path)
                        print(f"{Fore.GREEN}✓ Файл найден: {new_path}")
                    else:
                        print(f"{Fore.RED}✗ Файл не найден: {new_path}")
            else:
                print(f"{Fore.YELLOW}Создайте необходимые файлы и повторите загрузку.")
                return
        
        # Загружаем данные
        self.users = self.data_loader.load_users()
        self.messages = self.data_loader.load_messages()
        self.api_data = self.data_loader.load_api_data()
        self.sessions = self.data_loader.load_sessions()
        self.proxies = self.data_loader.load_proxies()
        
        # Проверяем прокси
        if not self.proxies:
            response = self.get_user_input(f"{Fore.YELLOW}Прокси не загружены. Продолжить без прокси? (y/n): ")
            if response.lower() != 'y':
                print(f"{Fore.RED}Загрузка отменена. Добавьте прокси и повторите.")
                return
        
        print(f"{Fore.GREEN}✓ Данные успешно загружены!")
        input(f"{Fore.CYAN}Нажмите Enter для продолжения...")

    async def start_sending_process(self):
        """Запускает процесс рассылки"""
        if not all([self.users, self.messages, self.sessions, self.api_data]):
            print(f"{Fore.RED}✗ Не все данные загружены! Сначала выполните загрузку данных.")
            input(f"{Fore.CYAN}Нажмите Enter для продолжения...")
            return
        
        print(f"{Fore.YELLOW}Подготовка к рассылке...")
        print(f"Пользователей: {len(self.users)}")
        print(f"Сообщений: {len(self.messages)}")
        print(f"Сессий: {len(self.sessions)}")
        print(f"API ключей: {len(self.api_data)}")
        print(f"Прокси: {len(self.proxies)}")
        
        # Показываем информацию о кнопке
        button_text = self.config.get("settings.button.text")
        button_url = self.config.get("settings.button.url")
        if button_text and button_url:
            print(f"Кнопка по умолчанию: '{button_text}' -> {button_url}")
        else:
            print(f"Кнопка по умолчанию: не настроена")
        print()
        
        response = self.get_user_input("Начать рассылку? (y/n): ")
        if response.lower() == 'y':
            print(f"{Fore.GREEN}🚀 Запуск рассылки...")
            print(f"{Fore.CYAN}Нажмите Ctrl+C для остановки")
            
            try:
                await self.sender.start_sending(
                    self.users, self.messages, self.sessions, 
                    self.api_data, self.proxies
                )
            except KeyboardInterrupt:
                print(f"{Fore.YELLOW}Остановка рассылки...")
                self.sender.stop_sending()
            except Exception as e:
                print(f"{Fore.RED}Ошибка во время рассылки: {e}")
            finally:
                # Сохраняем финальную статистику
                self.stats.cleanup_and_save()
            
            input(f"{Fore.CYAN}Нажмите Enter для продолжения...")

    def change_setting(self, setting_num: int):
        """Изменяет настройку"""
        settings_map = {
            1: ("limits.daily_limit", "Дневной лимит на аккаунт", int),
            2: ("limits.min_delay", "Минимальная задержка (сек)", float),
            3: ("limits.max_delay", "Максимальная задержка (сек)", float),
            4: ("limits.accounts_per_api", "Аккаунтов на API", int),
            5: ("limits.accounts_per_proxy", "Аккаунтов на прокси", int),
            6: ("limits.max_concurrent_accounts", "Максимум одновременных аккаунтов", int),
            7: ("settings.emoji_probability", "Вероятность эмодзи (0.0-1.0)", float)
        }
        
        if setting_num not in settings_map:
            return
        
        key, description, value_type = settings_map[setting_num]
        current_value = self.config.get(key)
        
        print(f"{Fore.CYAN}Текущее значение '{description}': {current_value}")
        new_value = self.get_user_input(f"Введите новое значение: ")
        
        try:
            new_value = value_type(new_value)
            self.config.set(key, new_value)
            print(f"{Fore.GREEN}✓ Настройка обновлена: {description} = {new_value}")
        except ValueError:
            print(f"{Fore.RED}✗ Неверный формат значения!")
        
        input(f"{Fore.CYAN}Нажмите Enter для продолжения...")

    def change_button_settings(self):
        """Изменяет настройки кнопки"""
        while True:
            self.clear_screen()
            self.print_header()
            self.print_button_settings()
            
            choice = self.get_user_input("Выберите пункт (1-4): ")
            
            if choice == "1":
                new_text = self.get_user_input("Введите новый текст кнопки: ")
                if new_text:
                    self.config.set("settings.button.text", new_text)
                    print(f"{Fore.GREEN}✓ Текст кнопки обновлен: {new_text}")
                else:
                    print(f"{Fore.YELLOW}Текст не может быть пустым")
                input(f"{Fore.CYAN}Нажмите Enter для продолжения...")
                
            elif choice == "2":
                new_url = self.get_user_input("Введите новый URL кнопки: ")
                if new_url:
                    # Проверяем, что URL начинается с http:// или https://
                    if not new_url.startswith(('http://', 'https://')):
                        new_url = 'https://' + new_url
                    self.config.set("settings.button.url", new_url)
                    print(f"{Fore.GREEN}✓ URL кнопки обновлен: {new_url}")
                else:
                    print(f"{Fore.YELLOW}URL не может быть пустым")
                input(f"{Fore.CYAN}Нажмите Enter для продолжения...")
                
            elif choice == "3":
                self.config.set("settings.button.text", "")
                self.config.set("settings.button.url", "")
                print(f"{Fore.YELLOW}✓ Кнопка отключена")
                input(f"{Fore.CYAN}Нажмите Enter для продолжения...")
                
            elif choice == "4":
                break

    def change_file_paths(self):
        """Изменяет пути к файлам"""
        files_map = {
            "users_file": "Файл пользователей",
            "messages_file": "Файл сообщений",
            "apidata_file": "Файл API данных", 
            "proxies_file": "Файл прокси",
            "sessions_dir": "Папка сессий"
        }
        
        print(f"{Fore.CYAN}Текущие пути к файлам:")
        for key, description in files_map.items():
            current_path = self.config.get(f"paths.{key}")
            print(f"  {description}: {current_path}")
        print()
        
        for key, description in files_map.items():
            current_path = self.config.get(f"paths.{key}")
            new_path = self.get_user_input(f"Новый путь к '{description}' (Enter - оставить {current_path}): ")
            
            if new_path:
                self.config.set(f"paths.{key}", new_path)
                print(f"{Fore.GREEN}✓ Обновлен путь: {description}")
        
        input(f"{Fore.CYAN}Нажмите Enter для продолжения...")

    async def run_interface(self):
        """Запускает интерфейс"""
        while self.is_running:
            self.clear_screen()
            self.print_header()
            self.print_statistics()
            self.print_logs()
            
            if self.current_menu == "main":
                self.print_main_menu()
                
                choice = self.get_user_input("Выберите пункт меню (1-7): ")
                
                if choice == "1":
                    self.load_all_data()
                elif choice == "2":
                    await self.start_sending_process()
                elif choice == "3":
                    self.current_menu = "settings"
                elif choice == "4":
                    self.clear_screen()
                    self.print_account_stats()
                    input()
                elif choice == "5":
                    self.sender.stop_sending()
                    print(f"{Fore.YELLOW}Сигнал остановки отправлен.")
                    input(f"{Fore.CYAN}Нажмите Enter для продолжения...")
                elif choice == "6":
                    self.clear_statistics()
                elif choice == "7":
                    # Сохраняем статистику перед выходом
                    self.stats.cleanup_and_save()
                    self.is_running = False
                    print(f"{Fore.CYAN}До свидания!")
                
            elif self.current_menu == "settings":
                self.print_settings_menu()
                
                choice = self.get_user_input("Выберите настройку (1-10): ")
                
                if choice in ["1", "2", "3", "4", "5", "6", "7"]:
                    self.change_setting(int(choice))
                elif choice == "8":
                    self.change_button_settings()
                elif choice == "9":
                    self.change_file_paths()
                elif choice == "10":
                    self.current_menu = "main"


def create_default_files():
    """Создает файлы с примерами по умолчанию"""
    base_dir = Path(".")
    
    # Создаем папки
    (base_dir / "sessions").mkdir(exist_ok=True)
    (base_dir / "sent_users").mkdir(exist_ok=True)
    
    # Создаем файлы с примерами если они не существуют
    examples = {
        "users.txt": """@user1
user2
user3
user4""",
        
        "messages.txt": """1.Привет, {друг|товарищ}! Хочешь узнать о {новом проекте|крутой возможности}?
2.Смотри, {это|вот} {круто|потрясающе}! {button:Подробнее|https://mysite.com/details}
3.Привет, ты оставлял заявку на связь, цена: 250 грн, скинуть телеграм-магазин? {button:Магазин|https://t.me/myshop}""",
        
        "apidata.txt": """123456:abcdef1234567890abcdef1234567890
789012:ghijk4567890abcdef1234567890abcd""",
        
        "proxies.txt": """192.168.1.1:1080:user:pass
10.0.0.1:1080:user2:pass2"""
    }
    
    created_files = []
    for filename, content in examples.items():
        file_path = base_dir / filename
        if not file_path.exists():
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                created_files.append(filename)
            except Exception as e:
                print(f"Ошибка создания файла {filename}: {e}")
    
    if created_files:
        print(f"{Fore.GREEN}Созданы файлы с примерами: {', '.join(created_files)}")
        print(f"{Fore.YELLOW}Отредактируйте их перед использованием!")
        print(f"{Fore.CYAN}Примеры кнопок добавлены в messages.txt!")
        print()


async def main():
    """Главная функция программы"""
    print(f"{Fore.CYAN}Инициализация Telegram Mass Sender Pro...")
    
    # Создаем файлы с примерами если нужно
    create_default_files()
    
    # Инициализируем компоненты
    config = Config()
    logger = Logger(config.get("paths.logs_file", "telegram_sender.log"))
    data_loader = DataLoader(config, logger)
    statistics = Statistics(config, logger)
    sender = TelegramSender(config, logger, data_loader, statistics)
    interface = Interface(config, logger, data_loader, sender, statistics)
    
    logger.info("Программа запущена")
    
    try:
        # Запускаем интерфейс
        await interface.run_interface()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Программа остановлена пользователем")
    except Exception as e:
        print(f"\n{Fore.RED}Критическая ошибка: {e}")
        logger.error(f"Критическая ошибка: {e}")
    finally:
        # Останавливаем все активные клиенты
        if sender.active_clients:
            print(f"{Fore.YELLOW}Отключение активных клиентов...")
            for client in sender.active_clients.values():
                try:
                    await client.disconnect()
                except:
                    pass
        
        # Финальное сохранение статистики
        statistics.cleanup_and_save()
        
        logger.info("Программа завершена")
        print(f"{Fore.GREEN}Программа завершена. Логи сохранены в {config.get('paths.logs_file')}")


if __name__ == "__main__":
    try:
        # Проверяем версию Python
        if sys.version_info < (3, 7):
            print("Требуется Python 3.7 или выше")
            sys.exit(1)
        
        # Запускаем программу
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nПрограмма прервана пользователем")
    except Exception as e:
        print(f"Ошибка запуска: {e}")
        sys.exit(1)