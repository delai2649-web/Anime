import os
import sys
import json
import time
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.errors import SessionExpired, AuthKeyInvalid

# ==================== KONFIGURASI ====================
RUNNER_TOKEN = os.getenv("RUNNER_TOKEN", "runner_secret_token_123")
API_ID = int(os.getenv("API_ID", "2040"))  # Default Pyrogram API ID
API_HASH = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")  # Default Pyrogram API HASH

# Flask untuk komunikasi dengan Bot Admin
app = Flask(__name__)

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ==================== DATABASE SEMENTARA ====================
# Nanti pakai MongoDB/Redis
active_userbots = {}  # {user_id: {'client': Client, 'expired': datetime, 'plan': str}}
user_sessions = {}    # {user_id: session_string}

# ==================== PYROGRAM USERBOT CLASS ====================

class UserbotInstance:
    """Kelas untuk mengelola satu instance userbot"""
    
    def __init__(self, user_id, session_string, plan):
        self.user_id = user_id
        self.session_string = session_string
        self.plan = plan
        self.client = None
        self.plugins = self.get_plugins_by_plan()
        self.running = False
        
    def get_plugins_by_plan(self):
        """Dapatkan plugin berdasarkan plan"""
        plugins = {
            'lite': 25,
            'basic': 56,
            'pro': 99
        }
        return plugins.get(self.plan, 25)
    
    async def start(self):
        """Mulai userbot"""
        try:
            self.client = Client(
                name=f"userbot_{self.user_id}",
                session_string=self.session_string,
                api_id=API_ID,
                api_hash=API_HASH,
                no_updates=False
            )
            
            # Register handlers berdasarkan plan
            await self.register_handlers()
            
            await self.client.start()
            self.running = True
            
            logger.info(f"✅ Userbot {self.user_id} started successfully!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start userbot {self.user_id}: {e}")
            return False
    
    async def stop(self):
        """Hentikan userbot"""
        if self.client:
            await self.client.stop()
            self.running = False
            logger.info(f"🛑 Userbot {self.user_id} stopped")
    
    async def restart(self):
        """Restart userbot"""
        await self.stop()
        await asyncio.sleep(2)
        return await self.start()
    
    async def register_handlers(self):
        """Daftarkan command handlers"""
        client = self.client
        
        # ===== HANDLER UNTUK SEMUA PLAN =====
        
        @client.on_message(filters.command("start") & filters.me)
        async def start_handler(client: Client, message: Message):
            await message.edit_text(
                f"🤖 **Userbot Aktif!**\n\n"
                f"Plan: {self.plan.upper()}\n"
                f"Plugins: {self.plugins}\n"
                f"Ketik `.help` untuk bantuan"
            )
        
        @client.on_message(filters.command("help") & filters.me)
        async def help_handler(client: Client, message: Message):
            help_text = self.get_help_text()
            await message.edit_text(help_text)
        
        @client.on_message(filters.command("ping") & filters.me)
        async def ping_handler(client: Client, message: Message):
            start = time.time()
            await message.edit_text("🏓 Pong!")
            end = time.time()
            await message.edit_text(f"🏓 **Pong!**\n`{(end-start)*1000:.2f}ms`")
        
        # ===== LITE PLAN (25 PLUGIN) =====
        if self.plugins >= 25:
            @client.on_message(filters.command("afk") & filters.me)
            async def afk_handler(client: Client, message: Message):
                reason = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else "Sedang AFK"
                await message.edit_text(f"😴 **AFK Mode:** {reason}")
            
            @client.on_message(filters.command("alive") & filters.me)
            async def alive_handler(client: Client, message: Message):
                await message.edit_text(
                    f"🤖 **Userbot Alive!**\n"
                    f"Plan: {self.plan}\n"
                    f"Uptime: Running..."
                )
            
            @client.on_message(filters.command("spam") & filters.me)
            async def spam_handler(client: Client, message: Message):
                try:
                    args = message.text.split()
                    if len(args) < 3:
                        await message.edit_text("Usage: `.spam <jumlah> <teks>`")
                        return
                    
                    count = int(args[1])
                    text = " ".join(args[2:])
                    
                    await message.delete()
                    
                    for i in range(min(count, 10)):  # Max 10 untuk lite
                        await client.send_message(message.chat.id, text)
                        await asyncio.sleep(0.5)
                        
                except Exception as e:
                    await message.edit_text(f"Error: {e}")
        
        # ===== BASIC PLAN (56 PLUGIN) =====
        if self.plugins >= 56:
            @client.on_message(filters.command("clone") & filters.me)
            async def clone_handler(client: Client, message: Message):
                if message.reply_to_message:
                    target = message.reply_to_message.from_user
                    try:
                        photo = await client.download_media(target.photo.big_file_id)
                        await client.set_profile_photo(photo=photo)
                        await client.update_profile(
                            first_name=target.first_name,
                            last_name=target.last_name or ""
                        )
                        await message.edit_text("✅ **Profile cloned!**")
                    except Exception as e:
                        await message.edit_text(f"❌ Error: {e}")
                else:
                    await message.edit_text("Reply to user to clone!")
            
            @client.on_message(filters.command("broadcast") & filters.me)
            async def broadcast_handler(client: Client, message: Message):
                text = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
                if not text:
                    await message.edit_text("Usage: `.broadcast <pesan>`")
                    return
                
                await message.edit_text("📢 **Broadcasting...**")
                count = 0
                
                async for dialog in client.get_dialogs():
                    try:
                        if dialog.chat.type in ["group", "supergroup"]:
                            await client.send_message(dialog.chat.id, text)
                            count += 1
                            await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"Broadcast error: {e}")
                
                await message.edit_text(f"✅ **Broadcast done!**\nSent to {count} groups")
            
            @client.on_message(filters.command("tagall") & filters.me)
            async def tagall_handler(client: Client, message: Message):
                if not message.chat.type in ["group", "supergroup"]:
                    await message.edit_text("❌ This command only works in groups!")
                    return
                
                await message.edit_text("🏷️ **Tagging all members...**")
                
                tags = []
                async for member in client.get_chat_members(message.chat.id):
                    if not member.user.is_bot:
                        tags.append(f"[{member.user.first_name}](tg://user?id={member.user.id})")
                    
                    if len(tags) == 5:  # Basic: 5 per batch
                        await client.send_message(
                            message.chat.id,
                            " ".join(tags),
                            disable_web_page_preview=True
                        )
                        tags = []
                        await asyncio.sleep(1)
                
                if tags:
                    await client.send_message(
                        message.chat.id,
                        " ".join(tags),
                        disable_web_page_preview=True
                    )
                
                await message.delete()
        
        # ===== PRO PLAN (99 PLUGIN) =====
        if self.plugins >= 99:
            @client.on_message(filters.command("download") & filters.me)
            async def download_handler(client: Client, message: Message):
                if not message.reply_to_message or not message.reply_to_message.media:
                    await message.edit_text("❌ Reply to media!")
                    return
                
                await message.edit_text("⬇️ **Downloading...**")
                
                try:
                    start_time = time.time()
                    file_path = await client.download_media(
                        message.reply_to_message,
                        progress=lambda current, total: asyncio.create_task(
                            self.update_progress(message, current, total, start_time)
                        )
                    )
                    
                    await message.edit_text(f"✅ **Downloaded!**\n`{file_path}`")
                except Exception as e:
                    await message.edit_text(f"❌ Error: {e}")
            
            @client.on_message(filters.command("chatbot") & filters.me)
            async def chatbot_handler(client: Client, message: Message):
                # Integrasi dengan AI (OpenAI/Gemini)
                await message.edit_text("🤖 **Chatbot AI aktif!** (Pro feature)")
            
            @client.on_message(filters.command("deepseek") & filters.me)
            async def deepseek_handler(client: Client, message: Message):
                query = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
                if not query:
                    await message.edit_text("Usage: `.deepseek <pertanyaan>`")
                    return
                
                await message.edit_text("🧠 **Thinking...**")
                # Integrasi DeepSeek AI di sini
                await asyncio.sleep(2)
                await message.edit_text(f"🧠 **DeepSeek:**\nIni adalah jawaban simulasi untuk: {query}")
            
            @client.on_message(filters.command("gemini") & filters.me)
            async def gemini_handler(client: Client, message: Message):
                query = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
                if not query:
                    await message.edit_text("Usage: `.gemini <pertanyaan>`")
                    return
                
                await message.edit_text("♊ **Gemini thinking...**")
                # Integrasi Google Gemini di sini
                await asyncio.sleep(2)
                await message.edit_text(f"♊ **Gemini:**\nIni adalah jawaban simulasi untuk: {query}")
            
            @client.on_message(filters.command("yt") & filters.me)
            async def youtube_handler(client: Client, message: Message):
                url = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
                if not url:
                    await message.edit_text("Usage: `.yt <url youtube>`")
                    return
                
                await message.edit_text("⬇️ **Downloading from YouTube...**")
                # Integrasi yt-dlp di sini
                await asyncio.sleep(3)
                await message.edit_text("✅ **Video downloaded!** (Simulasi)")
            
            @client.on_message(filters.command("spam") & filters.me)
            async def spam_pro_handler(client: Client, message: Message):
                # Override: Pro bisa spam lebih banyak
                try:
                    args = message.text.split()
                    if len(args) < 3:
                        await message.edit_text("Usage: `.spam <jumlah> <teks>`")
                        return
                    
                    count = int(args[1])
                    text = " ".join(args[2:])
                    
                    await message.delete()
                    
                    for i in range(min(count, 50)):  # Max 50 untuk pro
                        await client.send_message(message.chat.id, text)
                        await asyncio.sleep(0.3)
                        
                except Exception as e:
                    await message.edit_text(f"Error: {e}")
    
    async def update_progress(self, message, current, total, start_time):
        """Update progress download"""
        now = time.time()
        diff = now - start_time
        percentage = current * 100 / total
        speed = current / diff if diff > 0 else 0
        elapsed_time = round(diff)
        
        try:
            await message.edit_text(
                f"⬇️ **Downloading...**\n"
                f"Progress: {percentage:.1f}%\n"
                f"Speed: {self.humanbytes(speed)}/s\n"
                f"Size: {self.humanbytes(current)} / {self.humanbytes(total)}"
            )
        except:
            pass
    
    def humanbytes(self, size):
        """Convert bytes to human readable"""
        if not size:
            return "0 B"
        power = 2**10
        n = 0
        units = ['B', 'KB', 'MB', 'GB']
        while size > power and n < len(units) - 1:
            size /= power
            n += 1
        return f"{round(size, 2)} {units[n]}"
    
    def get_help_text(self):
        """Generate help text berdasarkan plan"""
        base_help = """
🤖 **USERBOT COMMANDS**

**Basic:**
`.start` - Start userbot
`.help` - Show this help
`.ping` - Check latency
`.alive` - Check status

"""
        
        lite_help = """
**Lite Plan (25):**
`.afk [reason]` - Set AFK status
`.spam <count> <text>` - Spam message (max 10)
`.sticker` - Sticker tools
`.voice` - Voice tools

"""
        
        basic_help = """
**Basic Plan (56):**
`.clone` - Clone user profile
`.broadcast <text>` - Broadcast to groups
`.tagall` - Tag all members
`.download` - Download media
`.qoute` - Make quote

"""
        
        pro_help = """
**Pro Plan (99):**
`.deepseek <query>` - AI DeepSeek
`.gemini <query>` - AI Gemini
`.chatbot` - Enable AI chatbot
`.yt <url>` - Download YouTube
`.spam <count> <text>` - Spam (max 50)
`.translate` - Translate text
`.ocr` - Text from image
`.rembg` - Remove background
And 40+ more plugins!

"""
        
        help_text = base_help
        if self.plugins >= 25:
            help_text += lite_help
        if self.plugins >= 56:
            help_text += basic_help
        if self.plugins >= 99:
            help_text += pro_help
        
        return help_text

# ==================== FLASK API ENDPOINTS ====================

@app.route('/')
def home():
    return {
        "status": "running",
        "active_userbots": len(active_userbots),
        "timestamp": datetime.now().isoformat()
    }

@app.route('/api/start_userbot', methods=['POST'])
def api_start_userbot():
    """API untuk memulai userbot dari Bot Admin"""
    data = request.json
    
    # Verifikasi token
    if data.get('runner_token') != RUNNER_TOKEN:
        return jsonify({"error": "Invalid token"}), 401
    
    user_id = data.get('user_id')
    session_string = data.get('session_string')
    plan = data.get('plan', 'lite')
    expired_str = data.get('expired')
    
    if not all([user_id, session_string, plan]):
        return jsonify({"error": "Missing parameters"}), 400
    
    # Cek kalau sudah ada, stop dulu
    if user_id in active_userbots:
        old_bot = active_userbots[user_id]
        asyncio.run(old_bot.stop())
        del active_userbots[user_id]
    
    # Buat instance baru
    userbot = UserbotInstance(user_id, session_string, plan)
    
    # Simpan expired
    if expired_str:
        userbot.expired = datetime.fromisoformat(expired_str)
    
    # Jalankan di thread terpisah
    def run_bot():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(userbot.start())
        loop.run_forever()
    
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()
    
    active_userbots[user_id] = userbot
    
    return jsonify({
        "success": True,
        "message": f"Userbot {user_id} started",
        "plan": plan,
        "plugins": userbot.plugins
    })

@app.route('/api/stop_userbot', methods=['POST'])
def api_stop_userbot():
    """API untuk menghentikan userbot"""
    data = request.json
    
    if data.get('runner_token') != RUNNER_TOKEN:
        return jsonify({"error": "Invalid token"}), 401
    
    user_id = data.get('user_id')
    
    if user_id not in active_userbots:
        return jsonify({"error": "Userbot not found"}), 404
    
    userbot = active_userbots[user_id]
    asyncio.run(userbot.stop())
    del active_userbots[user_id]
    
    return jsonify({
        "success": True,
        "message": f"Userbot {user_id} stopped"
    })

@app.route('/api/restart_userbot', methods=['POST'])
def api_restart_userbot():
    """API untuk restart userbot"""
    data = request.json
    
    if data.get('runner_token') != RUNNER_TOKEN:
        return jsonify({"error": "Invalid token"}), 401
    
    user_id = data.get('user_id')
    
    if user_id not in active_userbots:
        return jsonify({"error": "Userbot not found"}), 404
    
    userbot = active_userbots[user_id]
    success = asyncio.run(userbot.restart())
    
    return jsonify({
        "success": success,
        "message": f"Userbot {user_id} restarted"
    })

@app.route('/api/status', methods=['GET'])
def api_status():
    """Cek status semua userbot"""
    status = {}
    for user_id, userbot in active_userbots.items():
        status[user_id] = {
            "running": userbot.running,
            "plan": userbot.plan,
            "plugins": userbot.plugins,
            "expired": userbot.expired.isoformat() if hasattr(userbot, 'expired') else None
        }
    
    return jsonify({
        "total_active": len(active_userbots),
        "userbots": status
    })

@app.route('/api/check_expired', methods=['POST'])
def api_check_expired():
    """Cek dan hentikan userbot yang expired"""
    data = request.json
    
    if data.get('runner_token') != RUNNER_TOKEN:
        return jsonify({"error": "Invalid token"}), 401
    
    now = datetime.now()
    expired_list = []
    
    for user_id, userbot in list(active_userbots.items()):
        if hasattr(userbot, 'expired') and userbot.expired < now:
            asyncio.run(userbot.stop())
            del active_userbots[user_id]
            expired_list.append(user_id)
    
    return jsonify({
        "success": True,
        "expired_count": len(expired_list),
        "expired_users": expired_list
    })

# ==================== BACKGROUND TASKS ====================

async def check_expired_loop():
    """Loop untuk cek userbot expired setiap jam"""
    while True:
        await asyncio.sleep(3600)  # Cek setiap 1 jam
        
        now = datetime.now()
        expired_list = []
        
        for user_id, userbot in list(active_userbots.items()):
            if hasattr(userbot, 'expired') and userbot.expired < now:
                logger.info(f"⏰ Userbot {user_id} expired, stopping...")
                await userbot.stop()
                del active_userbots[user_id]
                expired_list.append(user_id)
        
        if expired_list:
            logger.info(f"🧹 Cleaned up {len(expired_list)} expired userbots")

def start_background_tasks():
    """Mulai background tasks"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_expired_loop())

# ==================== MAIN ====================

if __name__ == '__main__':
    # Start background task
    bg_thread = threading.Thread(target=start_background_tasks, daemon=True)
    bg_thread.start()
    
    # Start Flask
    logger.info("🚀 Userbot Runner starting...")
    app.run(host='0.0.0.0', port=int(os.getenv("PORT", 5000)))
