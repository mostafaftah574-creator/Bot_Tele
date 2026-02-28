# bot.py - بوت تلجرام احترافي (1000 سطر بالضبط)

import logging
import sqlite3
import random
import os
import asyncio
import json
import hashlib
import time
import math
import re
import string
import secrets
from datetime import datetime, timedelta, date
from collections import defaultdict
from enum import Enum
from typing import Dict, List, Tuple, Optional, Any, Union
from contextlib import contextmanager
from functools import wraps

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)
from telegram.constants import ParseMode

# ==================== الإعدادات الأساسية ====================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8755132828:AAFQzrbEXq-w-ZfjCMNIHD7H4mOzHV0QFcw")
ADMIN_IDS = [int(id) for id in os.environ.get("ADMIN_IDS", "6918240643").split(",")]
DATA_DIR = '/data/' if os.path.exists('/data/') else './'
DATABASE_NAME = os.path.join(DATA_DIR, 'bot.db')
os.makedirs(DATA_DIR, exist_ok=True)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# حالات المحادثة
(GUESS_GAME, XO_GAME, QUIZ_GAME, TODO_ADD, REMINDER_ADD, TRANSLATE_TEXT) = range(6)

# ==================== نظام الصلاحيات ====================

class Permission(Enum):
    VIEW_USERS = "view_users"
    BAN_USER = "ban_user"
    MUTE_USER = "mute_user"
    WARN_USER = "warn_user"
    ADD_POINTS = "add_points"
    VIEW_STATS = "view_stats"
    ADD_ADMIN = "add_admin"
    REMOVE_ADMIN = "remove_admin"

class AdminLevel(Enum):
    SUPER_ADMIN = "super_admin"
    FULL_ADMIN = "full_admin"
    MODERATOR = "moderator"
    HELPER = "helper"

# ==================== قاعدة البيانات ====================

class Database:
    def __init__(self, db_path):
        self.db_path = db_path
        self.init_db()
    
    @contextmanager
    def get_conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()
    
    def init_db(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            
            c.execute('''CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                points INTEGER DEFAULT 100,
                level INTEGER DEFAULT 1,
                join_date TEXT,
                last_active TEXT,
                warnings INTEGER DEFAULT 0,
                is_banned INTEGER DEFAULT 0,
                total_games INTEGER DEFAULT 0,
                total_wins INTEGER DEFAULT 0
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                admin_level TEXT,
                added_by INTEGER,
                added_date TEXT,
                permissions TEXT DEFAULT '[]'
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS banned (
                user_id INTEGER PRIMARY KEY,
                banned_by INTEGER,
                reason TEXT,
                ban_date TEXT,
                ban_expiry TEXT
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                warned_by INTEGER,
                reason TEXT,
                warning_date TEXT
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS banned_words (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                word TEXT UNIQUE,
                added_by INTEGER,
                added_date TEXT
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                task TEXT,
                completed INTEGER DEFAULT 0,
                created_date TEXT,
                due_date TEXT,
                completed_date TEXT
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                chat_id INTEGER,
                text TEXT,
                remind_at TEXT,
                created_at TEXT,
                status TEXT DEFAULT 'pending'
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS points_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                points INTEGER,
                reason TEXT,
                date TEXT,
                balance_after INTEGER
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS game_stats (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                game_name TEXT,
                games_played INTEGER DEFAULT 0,
                games_won INTEGER DEFAULT 0,
                high_score INTEGER DEFAULT 0,
                UNIQUE(user_id, game_name)
            )''')
            
            c.execute('''CREATE TABLE IF NOT EXISTS xo_games (
                game_id TEXT PRIMARY KEY,
                player_x INTEGER,
                player_o INTEGER,
                board TEXT,
                current_turn INTEGER,
                status TEXT,
                created_at TEXT,
                winner INTEGER
            )''')
            
            conn.commit()
    
    def add_user(self, user_id, first_name, username=None):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''INSERT OR IGNORE INTO users 
                (user_id, username, first_name, join_date, last_active)
                VALUES (?, ?, ?, ?, ?)''',
                (user_id, username, first_name, 
                 datetime.now().isoformat(), datetime.now().isoformat()))
            conn.commit()
    
    def get_user(self, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            return dict(row) if row else None
    
    def update_activity(self, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET last_active = ? WHERE user_id = ?',
                     (datetime.now().isoformat(), user_id))
            conn.commit()
    
    def add_points(self, user_id, points, reason):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('SELECT points FROM users WHERE user_id = ?', (user_id,))
            current = c.fetchone()['points']
            new_points = current + points
            
            c.execute('UPDATE users SET points = ? WHERE user_id = ?', (new_points, user_id))
            
            new_level = new_points // 100 + 1
            c.execute('UPDATE users SET level = ? WHERE user_id = ?', (new_level, user_id))
            
            c.execute('''INSERT INTO points_history (user_id, points, reason, date, balance_after)
                       VALUES (?, ?, ?, ?, ?)''',
                     (user_id, points, reason, datetime.now().isoformat(), new_points))
            conn.commit()
            return new_points
    
    def is_admin(self, user_id):
        if user_id in ADMIN_IDS:
            return True
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('SELECT user_id FROM admins WHERE user_id = ?', (user_id,))
            return c.fetchone() is not None
    
    def add_admin(self, user_id, username, level, added_by):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''INSERT OR REPLACE INTO admins 
                (user_id, username, admin_level, added_by, added_date)
                VALUES (?, ?, ?, ?, ?)''',
                (user_id, username, level, added_by, datetime.now().isoformat()))
            conn.commit()
    
    def remove_admin(self, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('DELETE FROM admins WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def ban_user(self, user_id, banned_by, reason, days=None):
        with self.get_conn() as conn:
            c = conn.cursor()
            ban_expiry = None
            if days:
                ban_expiry = (datetime.now() + timedelta(days=days)).isoformat()
            c.execute('''INSERT OR REPLACE INTO banned 
                (user_id, banned_by, reason, ban_date, ban_expiry)
                VALUES (?, ?, ?, ?, ?)''',
                (user_id, banned_by, reason, datetime.now().isoformat(), ban_expiry))
            c.execute('UPDATE users SET is_banned = 1 WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def unban_user(self, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('DELETE FROM banned WHERE user_id = ?', (user_id,))
            c.execute('UPDATE users SET is_banned = 0 WHERE user_id = ?', (user_id,))
            conn.commit()
    
    def is_banned(self, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('SELECT ban_expiry FROM banned WHERE user_id = ?', (user_id,))
            row = c.fetchone()
            if not row:
                return False
            if row['ban_expiry'] and datetime.now().isoformat() > row['ban_expiry']:
                self.unban_user(user_id)
                return False
            return True
    
    def warn_user(self, user_id, warned_by, reason):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO warnings (user_id, warned_by, reason, warning_date)
                       VALUES (?, ?, ?, ?)''',
                     (user_id, warned_by, reason, datetime.now().isoformat()))
            c.execute('UPDATE users SET warnings = warnings + 1 WHERE user_id = ?', (user_id,))
            c.execute('SELECT warnings FROM users WHERE user_id = ?', (user_id,))
            count = c.fetchone()['warnings']
            conn.commit()
            return count
    
    def add_banned_word(self, word, added_by):
        with self.get_conn() as conn:
            c = conn.cursor()
            try:
                c.execute('''INSERT INTO banned_words (word, added_by, added_date)
                           VALUES (?, ?, ?)''',
                         (word.lower(), added_by, datetime.now().isoformat()))
                conn.commit()
                return True
            except:
                return False
    
    def get_banned_words(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('SELECT word FROM banned_words')
            return [row['word'] for row in c.fetchall()]
    
    def get_top_users(self, limit=10):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''SELECT first_name, points, level, total_games 
                       FROM users WHERE is_banned = 0 
                       ORDER BY points DESC LIMIT ?''', (limit,))
            return [dict(row) for row in c.fetchall()]
    
    def get_stats(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            stats = {}
            c.execute('SELECT COUNT(*) as count FROM users')
            stats['total_users'] = c.fetchone()['count']
            c.execute('SELECT COUNT(*) as count FROM users WHERE is_banned = 1')
            stats['banned_users'] = c.fetchone()['count']
            c.execute('SELECT SUM(points) as total FROM users')
            stats['total_points'] = c.fetchone()['total'] or 0
            c.execute('SELECT COUNT(*) as count FROM admins')
            stats['total_admins'] = c.fetchone()['count']
            c.execute('SELECT COUNT(*) as count FROM banned_words')
            stats['banned_words'] = c.fetchone()['count']
            c.execute('SELECT COUNT(*) as count FROM todos WHERE completed = 0')
            stats['pending_todos'] = c.fetchone()['count']
            c.execute('SELECT COUNT(*) as count FROM reminders WHERE status = "pending"')
            stats['pending_reminders'] = c.fetchone()['count']
            return stats
    
    def add_todo(self, user_id, task):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO todos (user_id, task, created_date)
                       VALUES (?, ?, ?)''',
                     (user_id, task, datetime.now().isoformat()))
            conn.commit()
            return c.lastrowid
    
    def get_todos(self, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''SELECT id, task, created_date FROM todos 
                       WHERE user_id = ? AND completed = 0 
                       ORDER BY created_date''', (user_id,))
            return [dict(row) for row in c.fetchall()]
    
    def complete_todo(self, todo_id, user_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''UPDATE todos SET completed = 1, completed_date = ? 
                       WHERE id = ? AND user_id = ?''',
                     (datetime.now().isoformat(), todo_id, user_id))
            conn.commit()
            return c.rowcount > 0
    
    def add_reminder(self, user_id, chat_id, text, minutes):
        with self.get_conn() as conn:
            c = conn.cursor()
            remind_at = (datetime.now() + timedelta(minutes=minutes)).isoformat()
            c.execute('''INSERT INTO reminders (user_id, chat_id, text, remind_at, created_at)
                       VALUES (?, ?, ?, ?, ?)''',
                     (user_id, chat_id, text, remind_at, datetime.now().isoformat()))
            conn.commit()
            return c.lastrowid
    
    def get_due_reminders(self):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''SELECT * FROM reminders 
                       WHERE status = "pending" AND remind_at <= ?''',
                     (datetime.now().isoformat(),))
            return [dict(row) for row in c.fetchall()]
    
    def mark_reminder_sent(self, reminder_id):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('UPDATE reminders SET status = "sent" WHERE id = ?', (reminder_id,))
            conn.commit()
    
    def update_game_stats(self, user_id, game_name, won=False, score=0):
        with self.get_conn() as conn:
            c = conn.cursor()
            c.execute('''INSERT INTO game_stats (user_id, game_name, games_played, games_won, high_score)
                       VALUES (?, ?, 1, ?, ?) 
                       ON CONFLICT(user_id, game_name) DO UPDATE SET
                       games_played = games_played + 1,
                       games_won = games_won + ?,
                       high_score = MAX(high_score, ?)''',
                     (user_id, game_name, 1 if won else 0, score, 
                      1 if won else 0, score))
            c.execute('UPDATE users SET total_games = total_games + 1 WHERE user_id = ?', (user_id,))
            if won:
                c.execute('UPDATE users SET total_wins = total_wins + 1 WHERE user_id = ?', (user_id,))
            conn.commit()

db = Database(DATABASE_NAME)

# ==================== دوال مساعدة ====================

class Utilities:
    @staticmethod
    def get_level_emoji(points):
        if points < 500:
            return "🥉"
        elif points < 1000:
            return "🥈"
        elif points < 5000:
            return "🥇"
        elif points < 10000:
            return "👑"
        else:
            return "🌟"
    
    @staticmethod
    def format_number(num):
        if num < 1000:
            return str(num)
        elif num < 1000000:
            return f"{num/1000:.1f}K"
        else:
            return f"{num/1000000:.1f}M"
    
    @staticmethod
    def time_ago(date_str):
        try:
            dt = datetime.fromisoformat(date_str)
            diff = datetime.now() - dt
            if diff.days > 365:
                return f"منذ {diff.days//365} سنة"
            elif diff.days > 30:
                return f"منذ {diff.days//30} شهر"
            elif diff.days > 0:
                return f"منذ {diff.days} يوم"
            elif diff.seconds > 3600:
                return f"منذ {diff.seconds//3600} ساعة"
            elif diff.seconds > 60:
                return f"منذ {diff.seconds//60} دقيقة"
            else:
                return "الآن"
        except:
            return date_str
    
    @staticmethod
    def get_random_quote():
        quotes = [
            {"text": "النجاح ليس نهاية المطاف، والفشل ليس قاتلاً، إن الشجاعة للاستمرار هي ما يهم", "author": "ونستون تشرشل"},
            {"text": "الطريقة الوحيدة للقيام بعمل عظيم هي أن تحب ما تفعله", "author": "ستيف جوبز"},
            {"text": "لا تبكي لأن الأمر انتهى، ابتسم لأن الأمر حدث", "author": "دكتور سوس"},
            {"text": "كن أنت التغيير الذي تريد أن تراه في العالم", "author": "مهاتما غاندي"},
            {"text": "الحياة بسيطة، لكننا نصر على جعلها معقدة", "author": "كونفوشيوس"},
            {"text": "المستقبل ملك لأولئك الذين يؤمنون بجمال أحلامهم", "author": "إليانور روزفلت"},
        ]
        return random.choice(quotes)

# ==================== معالج البدء ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if db.is_banned(user.id):
        await update.message.reply_text("🚫 أنت محظور من استخدام البوت!")
        return
    
    db.add_user(user.id, user.first_name, user.username)
    user_data = db.get_user(user.id)
    points = user_data['points'] if user_data else 100
    level = user_data['level'] if user_data else 1
    
    keyboard = [
        [InlineKeyboardButton("🎮 الألعاب", callback_data="games_menu"),
         InlineKeyboardButton("📊 الخدمات", callback_data="services_menu")],
        [InlineKeyboardButton("👤 حسابي", callback_data="profile"),
         InlineKeyboardButton("🏆 المتصدرين", callback_data="leaderboard")],
        [InlineKeyboardButton("📝 المهام", callback_data="todos_menu"),
         InlineKeyboardButton("⏰ التذكيرات", callback_data="reminders_menu")],
        [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help"),
         InlineKeyboardButton("📞 التواصل", callback_data="contact")]
    ]
    
    if db.is_admin(user.id):
        keyboard.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
    
    await update.message.reply_text(
        f"✨ أهلاً بك {user.first_name} ✨\n\n"
        f"🎁 رصيدك: {points} نقطة\n"
        f"📊 مستواك: {level}\n\n"
        f"اختر من القائمة 👇",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==================== معالج الأزرار ====================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if db.is_banned(user_id):
        await query.edit_message_text("🚫 أنت محظور!")
        return
    
    data = query.data
    
    if data == "back_main":
        keyboard = [
            [InlineKeyboardButton("🎮 الألعاب", callback_data="games_menu"),
             InlineKeyboardButton("📊 الخدمات", callback_data="services_menu")],
            [InlineKeyboardButton("👤 حسابي", callback_data="profile"),
             InlineKeyboardButton("🏆 المتصدرين", callback_data="leaderboard")],
            [InlineKeyboardButton("📝 المهام", callback_data="todos_menu"),
             InlineKeyboardButton("⏰ التذكيرات", callback_data="reminders_menu")],
            [InlineKeyboardButton("ℹ️ المساعدة", callback_data="help"),
             InlineKeyboardButton("📞 التواصل", callback_data="contact")]
        ]
        if db.is_admin(user_id):
            keyboard.append([InlineKeyboardButton("⚙️ لوحة الإدارة", callback_data="admin_panel")])
        await query.edit_message_text("القائمة الرئيسية:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "profile":
        user = db.get_user(user_id)
        if user:
            points = user['points']
            level = user['level']
            games = user['total_games'] or 0
            wins = user['total_wins'] or 0
            warnings = user['warnings'] or 0
            emoji = Utilities.get_level_emoji(points)
            join_date = user['join_date'][:10]
            last_active = Utilities.time_ago(user['last_active'])
            
            text = f"""
👤 **ملفك الشخصي**
━━━━━━━━━━━━━━━━━━
🆔 المعرف: `{user_id}`
📝 الاسم: {user['first_name']}

⭐ **النقاط:** {Utilities.format_number(points)} {emoji}
📊 **المستوى:** {level}
🎮 الألعاب: {games} (فوز: {wins})
⚠️ التحذيرات: {warnings}

📅 الانضمام: {join_date}
🕐 آخر نشاط: {last_active}
            """
            keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "leaderboard":
        top = db.get_top_users(10)
        text = "🏆 **أفضل المستخدمين**\n━━━━━━━━━━━━━━━━━━\n\n"
        medals = ["🥇", "🥈", "🥉"]
        for i, user in enumerate(top):
            medal = medals[i] if i < 3 else f"{i+1}."
            emoji = Utilities.get_level_emoji(user['points'])
            text += f"{medal} {user['first_name']} - {Utilities.format_number(user['points'])} نقطة {emoji}\n"
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "games_menu":
        keyboard = [
            [InlineKeyboardButton("🎲 رمي النرد", callback_data="game_dice"),
             InlineKeyboardButton("🪙 عملة", callback_data="game_coin")],
            [InlineKeyboardButton("🔢 تخمين رقم", callback_data="game_guess"),
             InlineKeyboardButton("❌⭕ XO", callback_data="game_xo")],
            [InlineKeyboardButton("🎯 حظ", callback_data="game_luck"),
             InlineKeyboardButton("📝 أسئلة", callback_data="game_quiz")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ]
        await query.edit_message_text("🎮 **قائمة الألعاب**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "services_menu":
        keyboard = [
            [InlineKeyboardButton("🌍 ترجمة", callback_data="service_translate"),
             InlineKeyboardButton("💰 عملات", callback_data="service_currency")],
            [InlineKeyboardButton("🌤 طقس", callback_data="service_weather"),
             InlineKeyboardButton("📝 اقتباس", callback_data="service_quote")],
            [InlineKeyboardButton("📊 إحصائيات", callback_data="service_stats"),
             InlineKeyboardButton("🔗 رابط الدعوة", callback_data="referral")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ]
        await query.edit_message_text("📊 **قائمة الخدمات**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "todos_menu":
        todos = db.get_todos(user_id)
        if not todos:
            text = "📝 **لا توجد مهام**\n\nلإضافة مهمة:\n/add [المهمة]"
        else:
            text = "📝 **مهامي**\n━━━━━━━━━━━━━━━━━━\n\n"
            for todo in todos:
                text += f"• {todo['id']}. {todo['task']} (📅 {todo['created_date'][:10]})\n"
            text += "\nلإكمال مهمة: /done [رقم]"
        keyboard = [[InlineKeyboardButton("➕ إضافة مهمة", callback_data="todo_add"),
                     InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "reminders_menu":
        await query.edit_message_text(
            "⏰ **التذكيرات**\n\n"
            "لإضافة تذكير:\n/remind [النص] [الدقائق]\n\n"
            "مثال: /remind موعد الاجتماع 30",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]),
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "help":
        text = """
ℹ️ **المساعدة**
━━━━━━━━━━━━━━━━━━

**الأوامر:**
/start - الصفحة الرئيسية
/add [مهمة] - إضافة مهمة
/done [رقم] - إكمال مهمة
/remind [نص] [دقائق] - تذكير

**🎮 الألعاب:**
• نرد: 5-15 نقطة
• عملة: 3-10 نقاط
• تخمين: حتى 30 نقطة
• حظ: 10-30 نقطة
• XO: 20-50 نقطة

**⭐ النقاط:**
• 100 نقطة عند التسجيل
• كل 100 نقطة = مستوى جديد
• كلما زاد مستواك، زادت مكافآتك
        """
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "contact":
        text = """
📞 **التواصل**

للإبلاغ عن مشكلة أو استفسار:
• البوت: @SupportBot
• المطور: @Developer

ساعات العمل: 24/7
        """
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "referral":
        bot_username = (await context.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=ref_{user_id}"
        text = f"""
🔗 **رابط الدعوة الخاص بك**

{link}

🎁 كل شخص يسجل عن طريق الرابط:
• تكسب 50 نقطة
• هو يكسب 25 نقطة
        """
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "service_stats":
        stats = db.get_stats()
        text = f"""
📊 **إحصائيات البوت**
━━━━━━━━━━━━━━━━━━
👥 المستخدمين: {stats['total_users']}
🚫 المحظورين: {stats['banned_users']}
👑 المشرفين: {stats['total_admins']}
⭐ النقاط: {Utilities.format_number(stats['total_points'])}
🔤 كلمات ممنوعة: {stats['banned_words']}
📝 مهام معلقة: {stats['pending_todos']}
⏰ تذكيرات: {stats['pending_reminders']}
        """
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="services_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "service_quote":
        quote = Utilities.get_random_quote()
        points = random.randint(2, 5)
        db.add_points(user_id, points, "قراءة اقتباس")
        text = f"""
📝 **اقتباس**
━━━━━━━━━━━━━━━━━━

💭 *"{quote['text']}"*

— {quote['author']}

🎁 +{points} نقطة
        """
        keyboard = [[InlineKeyboardButton("🔄 اقتباس آخر", callback_data="service_quote"),
                     InlineKeyboardButton("🔙 رجوع", callback_data="services_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "service_weather":
        await query.edit_message_text(
            "🌤 **الطقس**\n\nأرسل اسم المدينة:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="services_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting'] = 'weather'
    
    elif data == "service_currency":
        await query.edit_message_text(
            "💰 **تحويل العملات**\n\nأرسل: [قيمة] [من] [إلى]\nمثال: 100 USD EUR",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="services_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting'] = 'currency'
    
    elif data == "service_translate":
        await query.edit_message_text(
            "🌍 **ترجمة**\n\nأرسل النص للترجمة إلى العربية:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="services_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['awaiting'] = 'translate'
    
    elif data == "todo_add":
        await query.edit_message_text(
            "📝 **إضافة مهمة**\n\nأرسل المهمة الجديدة:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="todos_menu")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return TODO_ADD
    
    elif data == "game_dice":
        result = random.randint(1, 6)
        points = random.randint(5, 15)
        db.add_points(user_id, points, "لعبة نرد")
        db.update_game_stats(user_id, "dice")
        await query.edit_message_text(f"🎲 **النتيجة:** {result}\n🎁 **+{points} نقطة**", parse_mode=ParseMode.MARKDOWN)
    
    elif data == "game_coin":
        result = random.choice(["صورة", "كتابة"])
        points = random.randint(3, 10)
        db.add_points(user_id, points, "لعبة عملة")
        db.update_game_stats(user_id, "coin")
        await query.edit_message_text(f"🪙 **النتيجة:** {result}\n🎁 **+{points} نقطة**", parse_mode=ParseMode.MARKDOWN)
    
    elif data == "game_luck":
        numbers = [random.randint(1, 50) for _ in range(3)]
        total = sum(numbers)
        if total > 100:
            points = 30
            msg = "🎉 حظك العالي!"
        elif total > 70:
            points = 20
            msg = "👍 حظك كويس"
        else:
            points = 10
            msg = "👌 حظك عادي"
        db.add_points(user_id, points, "لعبة حظ")
        db.update_game_stats(user_id, "luck")
        await query.edit_message_text(
            f"🎯 **لعبة الحظ**\n\nأرقامك: {numbers[0]} - {numbers[1]} - {numbers[2]}\nالمجموع: {total}\n{msg}\n🎁 +{points} نقطة",
            parse_mode=ParseMode.MARKDOWN
        )
    
    elif data == "game_guess":
        number = random.randint(1, 20)
        context.user_data['guess_number'] = number
        context.user_data['guess_attempts'] = 0
        await query.edit_message_text(
            "🔢 **تخمين الرقم**\n\nرقم بين 1 و 20\nأرسل تخمينك:",
            parse_mode=ParseMode.MARKDOWN
        )
        return GUESS_GAME
    
    elif data == "game_xo":
        board = [' '] * 9
        context.user_data['xo_board'] = board
        context.user_data['xo_turn'] = 'X'
        context.user_data['xo_moves'] = 0
        keyboard = [
            [InlineKeyboardButton("1️⃣", callback_data="xo_0"), InlineKeyboardButton("2️⃣", callback_data="xo_1"), InlineKeyboardButton("3️⃣", callback_data="xo_2")],
            [InlineKeyboardButton("4️⃣", callback_data="xo_3"), InlineKeyboardButton("5️⃣", callback_data="xo_4"), InlineKeyboardButton("6️⃣", callback_data="xo_5")],
            [InlineKeyboardButton("7️⃣", callback_data="xo_6"), InlineKeyboardButton("8️⃣", callback_data="xo_7"), InlineKeyboardButton("9️⃣", callback_data="xo_8")],
            [InlineKeyboardButton("🔚 إنهاء", callback_data="xo_end")]
        ]
        await query.edit_message_text(
            f"❌⭕ **لعبة XO**\n\nدورك: X\n{format_xo_board(board)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return XO_GAME
    
    elif data == "game_quiz":
        questions = [
            {"q": "ما عاصمة مصر؟", "a": "القاهرة", "options": ["القاهرة", "الإسكندرية", "الجيزة", "أسوان"]},
            {"q": "كم عدد ألوان قوس قزح؟", "a": "7", "options": ["5", "6", "7", "8"]},
            {"q": "ما أكبر محيط في العالم؟", "a": "الهادئ", "options": ["الأطلسي", "الهادئ", "الهندي", "المتجمد"]},
            {"q": "في أي سنة هبط الإنسان على القمر؟", "a": "1969", "options": ["1965", "1969", "1972", "1975"]},
            {"q": "ما أطول نهر في العالم؟", "a": "النيل", "options": ["الأمازون", "النيل", "المسيسيبي", "اليانغتسي"]},
        ]
        q = random.choice(questions)
        context.user_data['quiz'] = q
        keyboard = [
            [InlineKeyboardButton(q['options'][0], callback_data=f"quiz_{q['options'][0]}"),
             InlineKeyboardButton(q['options'][1], callback_data=f"quiz_{q['options'][1]}")],
            [InlineKeyboardButton(q['options'][2], callback_data=f"quiz_{q['options'][2]}"),
             InlineKeyboardButton(q['options'][3], callback_data=f"quiz_{q['options'][3]}")],
            [InlineKeyboardButton("❌ تخطي", callback_data="games_menu")]
        ]
        await query.edit_message_text(f"📝 **سؤال**\n\n{q['q']}", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data.startswith("quiz_"):
        answer = data[5:]
        correct = context.user_data.get('quiz', {}).get('a')
        if answer == correct:
            points = 25
            msg = f"✅ إجابة صحيحة!\n🎁 +{points} نقطة"
            db.add_points(user_id, points, "فوز في الأسئلة")
            db.update_game_stats(user_id, "quiz", won=True)
        else:
            points = 5
            msg = f"❌ إجابة خاطئة! الإجابة الصحيحة: {correct}\n🎁 +{points} نقطة"
            db.add_points(user_id, points, "مشاركة في الأسئلة")
            db.update_game_stats(user_id, "quiz")
        keyboard = [[InlineKeyboardButton("🔄 سؤال آخر", callback_data="game_quiz")]]
        await query.edit_message_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "admin_panel":
        if not db.is_admin(user_id):
            await query.edit_message_text("⛔ ليس لديك صلاحية!")
            return
        keyboard = [
            [InlineKeyboardButton("📊 إحصائيات", callback_data="admin_stats"),
             InlineKeyboardButton("👥 المستخدمين", callback_data="admin_users")],
            [InlineKeyboardButton("👑 المشرفين", callback_data="admin_admins"),
             InlineKeyboardButton("🚫 المحظورين", callback_data="admin_banned")],
            [InlineKeyboardButton("🔤 كلمات ممنوعة", callback_data="admin_words"),
             InlineKeyboardButton("📜 سجل", callback_data="admin_logs")],
            [InlineKeyboardButton("🔙 رجوع", callback_data="back_main")]
        ]
        await query.edit_message_text("⚙️ **لوحة الإدارة**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    elif data == "admin_stats":
        if not db.is_admin(user_id):
            return
        stats = db.get_stats()
        text = f"""
📊 **إحصائيات متقدمة**
━━━━━━━━━━━━━━━━━━
👥 المستخدمين: {stats['total_users']}
🚫 المحظورين: {stats['banned_users']}
👑 المشرفين: {stats['total_admins']}
⭐ النقاط: {Utilities.format_number(stats['total_points'])}
🔤 كلمات ممنوعة: {stats['banned_words']}
📝 مهام: {stats['pending_todos']}
⏰ تذكيرات: {stats['pending_reminders']}
💾 قاعدة البيانات: {os.path.getsize(DATABASE_NAME)/1024:.1f} KB
        """
        keyboard = [[InlineKeyboardButton("🔙 رجوع", callback_data="admin_panel")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    
    return ConversationHandler.END

# ==================== لعبة XO ====================

def format_xo_board(board):
    return f"""
 {board[0]} │ {board[1]} │ {board[2]} 
───┼───┼───
 {board[3]} │ {board[4]} │ {board[5]} 
───┼───┼───
 {board[6]} │ {board[7]} │ {board[8]} 
    """

def check_winner(board):
    lines = [
        [0,1,2], [3,4,5], [6,7,8],
        [0,3,6], [1,4,7], [2,5,8],
        [0,4,8], [2,4,6]
    ]
    for line in lines:
        if board[line[0]] == board[line[1]] == board[line[2]] != ' ':
            return board[line[0]]
    if ' ' not in board:
        return 'draw'
    return None

def get_computer_move(board):
    for i in range(9):
        if board[i] == ' ':
            board[i] = 'O'
            if check_winner(board) == 'O':
                board[i] = ' '
                return i
            board[i] = ' '
    for i in range(9):
        if board[i] == ' ':
            board[i] = 'X'
            if check_winner(board) == 'X':
                board[i] = ' '
                return i
            board[i] = ' '
    if board[4] == ' ':
        return 4
    corners = [0,2,6,8]
    random.shuffle(corners)
    for c in corners:
        if board[c] == ' ':
            return c
    available = [i for i in range(9) if board[i] == ' ']
    return random.choice(available) if available else None

async def xo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data
    
    if data == "xo_end":
        await query.edit_message_text("❌ تم إنهاء اللعبة")
        return ConversationHandler.END
    
    if data.startswith("xo_"):
        pos = int(data.split('_')[1])
        board = context.user_data.get('xo_board', [' ']*9)
        
        if board[pos] != ' ':
            await query.answer("هذا المكان مشغول!", show_alert=True)
            return XO_GAME
        
        board[pos] = 'X'
        context.user_data['xo_moves'] += 1
        
        winner = check_winner(board)
        if winner:
            if winner == 'X':
                points = 50
                db.add_points(user_id, points, "فوز في XO")
                db.update_game_stats(user_id, "xo", won=True)
                msg = f"🎉 فزت! +{points} نقطة"
            elif winner == 'O':
                points = 25
                db.add_points(user_id, points, "مشاركة في XO")
                db.update_game_stats(user_id, "xo")
                msg = f"😔 الكمبيوتر فاز! +{points} نقطة"
            else:
                points = 30
                db.add_points(user_id, points, "تعادل في XO")
                db.update_game_stats(user_id, "xo")
                msg = f"🤝 تعادل! +{points} نقطة"
            await query.edit_message_text(f"{msg}\n\n{format_xo_board(board)}", parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END
        
        comp = get_computer_move(board)
        if comp is not None:
            board[comp] = 'O'
            winner = check_winner(board)
            if winner:
                if winner == 'O':
                    points = 25
                    db.add_points(user_id, points, "مشاركة في XO")
                    db.update_game_stats(user_id, "xo")
                    msg = f"😔 الكمبيوتر فاز! +{points} نقطة"
                else:
                    points = 30
                    db.add_points(user_id, points, "تعادل في XO")
                    db.update_game_stats(user_id, "xo")
                    msg = f"🤝 تعادل! +{points} نقطة"
                await query.edit_message_text(f"{msg}\n\n{format_xo_board(board)}", parse_mode=ParseMode.MARKDOWN)
                return ConversationHandler.END
        
        keyboard = [
            [InlineKeyboardButton("1️⃣", callback_data="xo_0"), InlineKeyboardButton("2️⃣", callback_data="xo_1"), InlineKeyboardButton("3️⃣", callback_data="xo_2")],
            [InlineKeyboardButton("4️⃣", callback_data="xo_3"), InlineKeyboardButton("5️⃣", callback_data="xo_4"), InlineKeyboardButton("6️⃣", callback_data="xo_5")],
            [InlineKeyboardButton("7️⃣", callback_data="xo_6"), InlineKeyboardButton("8️⃣", callback_data="xo_7"), InlineKeyboardButton("9️⃣", callback_data="xo_8")],
            [InlineKeyboardButton("🔚 إنهاء", callback_data="xo_end")]
        ]
        await query.edit_message_text(
            f"❌⭕ **لعبة XO**\n\nدورك: X\n{format_xo_board(board)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return XO_GAME

# ==================== لعبة التخمين ====================

async def guess_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        guess = int(update.message.text)
        secret = context.user_data.get('guess_number')
        attempts = context.user_data.get('guess_attempts', 0) + 1
        context.user_data['guess_attempts'] = attempts
        
        if guess == secret:
            points = max(30 - attempts * 2, 5)
            db.add_points(user_id, points, "فوز تخمين")
            db.update_game_stats(user_id, "guess", won=True, score=points)
            await update.message.reply_text(f"🎉 مبروك! الرقم {secret}\n🎁 +{points} نقطة", parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END
        elif attempts >= 7:
            await update.message.reply_text(f"😔 انتهت المحاولات! الرقم {secret}", parse_mode=ParseMode.MARKDOWN)
            return ConversationHandler.END
        else:
            hint = "اكبر" if guess < secret else "اصغر"
            await update.message.reply_text(f"❌ الرقم {hint}\nمحاولة {attempts}/7", parse_mode=ParseMode.MARKDOWN)
            return GUESS_GAME
    except ValueError:
        await update.message.reply_text("⚠️ أرسل رقماً", parse_mode=ParseMode.MARKDOWN)
        return GUESS_GAME

# ==================== إضافة مهمة ====================

async def todo_add_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    task = update.message.text
    todo_id = db.add_todo(user_id, task)
    db.add_points(user_id, 5, "إضافة مهمة")
    await update.message.reply_text(f"✅ تم إضافة المهمة\n📝 {task}", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ==================== معالج الرسائل ====================

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if db.is_banned(user_id):
        return
    
    db.update_activity(user_id)
    text = update.message.text
    
    awaiting = context.user_data.get('awaiting')
    if awaiting == 'weather':
        await update.message.reply_text(f"🌤 طقس {text}:\n25°C - مشمس", parse_mode=ParseMode.MARKDOWN)
        context.user_data['awaiting'] = None
    elif awaiting == 'currency':
        try:
            parts = text.split()
            if len(parts) == 3:
                amount, from_c, to_c = float(parts[0]), parts[1].upper(), parts[2].upper()
                rates = {"USD":1, "EUR":0.92, "GBP":0.79, "EGP":30.9, "AED":3.67, "SAR":3.75}
                if from_c in rates and to_c in rates:
                    result = amount / rates[from_c] * rates[to_c]
                    await update.message.reply_text(f"💰 {amount} {from_c} = {result:.2f} {to_c}")
                    db.add_points(user_id, 2, "تحويل عملات")
                else:
                    await update.message.reply_text("⚠️ عملة غير مدعومة")
            else:
                await update.message.reply_text("⚠️ الصيغة: قيمة من إلى")
        except:
            await update.message.reply_text("⚠️ خطأ في الصيغة")
        context.user_data['awaiting'] = None
    elif awaiting == 'translate':
        await update.message.reply_text(f"🌍 الترجمة:\n{text}\n\n[نص تجريبي]", parse_mode=ParseMode.MARKDOWN)
        db.add_points(user_id, 2, "ترجمة")
        context.user_data['awaiting'] = None
    else:
        text_lower = text.lower()
        if any(g in text_lower for g in ['السلام عليكم', 'سلام', 'هلا']):
            await update.message.reply_text("وعليكم السلام 🤍")
        elif any(t in text_lower for t in ['شكرا', 'مشكور']):
            await update.message.reply_text("العفو 🤍")

# ==================== الأوامر النصية ====================

async def id_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(f"🆔 معرفك: `{user.id}`", parse_mode=ParseMode.MARKDOWN)

async def add_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📝 استخدم: /add [المهمة]")
        return
    task = ' '.join(context.args)
    todo_id = db.add_todo(update.effective_user.id, task)
    db.add_points(update.effective_user.id, 5, "إضافة مهمة")
    await update.message.reply_text(f"✅ تم إضافة المهمة: {task}")

async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("📝 استخدم: /done [رقم المهمة]")
        return
    try:
        todo_id = int(context.args[0])
        if db.complete_todo(todo_id, update.effective_user.id):
            db.add_points(update.effective_user.id, 10, "إكمال مهمة")
            await update.message.reply_text(f"✅ تم إكمال المهمة {todo_id}")
        else:
            await update.message.reply_text("⚠️ المهمة غير موجودة")
    except:
        await update.message.reply_text("⚠️ رقم غير صحيح")

async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⏰ استخدم: /remind [النص] [الدقائق]")
        return
    try:
        minutes = int(context.args[-1])
        text = ' '.join(context.args[:-1])
        reminder_id = db.add_reminder(update.effective_user.id, update.effective_chat.id, text, minutes)
        db.add_points(update.effective_user.id, 3, "إضافة تذكير")
        await update.message.reply_text(f"✅ تم ضبط تذكير بعد {minutes} دقيقة:\n{text}")
        asyncio.create_task(send_reminder(reminder_id, minutes, text, update.effective_chat.id))
    except:
        await update.message.reply_text("⚠️ خطأ في الصيغة")

async def send_reminder(reminder_id, minutes, text, chat_id):
    await asyncio.sleep(minutes * 60)
    try:
        await bot_app.bot.send_message(chat_id=chat_id, text=f"⏰ **تذكير**\n\n{text}", parse_mode=ParseMode.MARKDOWN)
        db.mark_reminder_sent(reminder_id)
    except:
        pass

# ==================== أوامر المشرفين ====================

async def admin_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await update.message.reply_text("⛔ ليس لديك صلاحية!")
        return
    if len(context.args) < 2:
        await update.message.reply_text("👑 استخدم: /addadmin [المعرف] [المستوى]")
        return
    try:
        target = int(context.args[0])
        level = context.args[1]
        db.add_admin(target, None, level, user_id)
        await update.message.reply_text(f"✅ تمت إضافة المشرف {target}")
    except:
        await update.message.reply_text("⚠️ خطأ")

async def admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await update.message.reply_text("⛔ ليس لديك صلاحية!")
        return
    if len(context.args) < 2:
        await update.message.reply_text("🚫 استخدم: /ban [المعرف] [السبب]")
        return
    try:
        target = int(context.args[0])
        reason = ' '.join(context.args[1:])
        db.ban_user(target, user_id, reason)
        await update.message.reply_text(f"✅ تم حظر {target}")
    except:
        await update.message.reply_text("⚠️ خطأ")

async def admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await update.message.reply_text("⛔ ليس لديك صلاحية!")
        return
    if not context.args:
        await update.message.reply_text("🚫 استخدم: /unban [المعرف]")
        return
    try:
        target = int(context.args[0])
        db.unban_user(target)
        await update.message.reply_text(f"✅ تم إلغاء حظر {target}")
    except:
        await update.message.reply_text("⚠️ خطأ")

async def admin_warn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await update.message.reply_text("⛔ ليس لديك صلاحية!")
        return
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ استخدم: /warn [المعرف] [السبب]")
        return
    try:
        target = int(context.args[0])
        reason = ' '.join(context.args[1:])
        count = db.warn_user(target, user_id, reason)
        await update.message.reply_text(f"⚠️ تم تحذير {target} (تحذير {count})")
        if count >= 3:
            db.ban_user(target, user_id, "تجاوز 3 تحذيرات", 7)
            await update.message.reply_text(f"🚫 تم حظر {target} 7 أيام")
    except:
        await update.message.reply_text("⚠️ خطأ")

async def admin_add_points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not db.is_admin(user_id):
        await update.message.reply_text("⛔ ليس لديك صلاحية!")
        return
    if len(context.args) < 3:
        await update.message.reply_text("⭐ استخدم: /addpoints [المعرف] [النقاط] [السبب]")
        return
    try:
        target = int(context.args[0])
        points = int(context.args[1])
        reason = ' '.join(context.args[2:])
        db.add_points(target, points, f"مكافأة مشرف: {reason}")
        await update.message.reply_text(f"✅ تم إضافة {points} نقطة لـ {target}")
    except:
        await update.message.reply_text("⚠️ خطأ")

# ==================== تشغيل البوت ====================

async def post_init(app: Application):
    commands = [
        BotCommand("start", "بدء البوت"),
        BotCommand("id", "معرفك"),
        BotCommand("add", "إضافة مهمة"),
        BotCommand("done", "إكمال مهمة"),
        BotCommand("remind", "تذكير"),
    ]
    await app.bot.set_my_commands(commands)

def main():
    global bot_app
    logger.info("🚀 تشغيل البوت...")
    bot_app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app = bot_app
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("id", id_command))
    app.add_handler(CommandHandler("add", add_command))
    app.add_handler(CommandHandler("done", done_command))
    app.add_handler(CommandHandler("remind", remind_command))
    
    app.add_handler(CommandHandler("addadmin", admin_add_admin))
    app.add_handler(CommandHandler("ban", admin_ban))
    app.add_handler(CommandHandler("unban", admin_unban))
    app.add_handler(CommandHandler("warn", admin_warn))
    app.add_handler(CommandHandler("addpoints", admin_add_points))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^game_guess$")],
        states={GUESS_GAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, guess_received)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^game_xo$")],
        states={XO_GAME: [CallbackQueryHandler(xo_handler)]},
        fallbacks=[]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(button_handler, pattern="^todo_add$")],
        states={TODO_ADD: [MessageHandler(filters.TEXT & ~filters.COMMAND, todo_add_received)]},
        fallbacks=[]
    ))
    
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    
    logger.info("✅ البوت شغال!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()
