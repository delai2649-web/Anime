import os
import re
import sys
import json
import time
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    CallbackQueryHandler, ContextTypes, filters, ConversationHandler
)
from pyrogram import Client
from pyrogram.errors import (
    PhoneNumberInvalid, PhoneCodeInvalid, 
    PhoneCodeExpired, SessionPasswordNeeded,
    AuthKeyUnregistered, FloodWait
)

# ==================== KONFIGURASI ====================
TOKEN = os.getenv("TOKEN", "YOUR_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "123456789"))
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://user:pass@cluster.mongodb.net/userbot_db")

# Pyrogram API (dari my.telegram.org)
API_ID = int(os.getenv("API_ID", "2040"))
API_HASH = os.getenv("API_HASH", "b18441a1ff607e10a989891a5462e627")

# Flask keep alive
app = Flask(__name__)

@app.route('/')
def home():
    active_count = len([u for u in users_collection.find({"userbot_active": True})]) if 'users_collection' in globals() else 0
    return f"<h1>🤖 Userbot SaaS Running</h1><p>Active Userbots: {active_count}</p>"

def run_flask():
    app.run(host='0.0.0.0', port=8080)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== DATABASE (MongoDB) ====================
try:
    from pymongo import MongoClient
    mongo_client = MongoClient(MONGODB_URI)
    db = mongo_client.userbot_db
    users_collection = db.users
    sessions_collection = db.sessions
    payments_collection = db.payments
    logger.info("✅ Connected to MongoDB")
except Exception as e:
    logger.error(f"❌ MongoDB Error: {e}")
    # Fallback ke JSON file
    users_collection = None
    sessions_collection = None
    payments_collection = None

def get_user(user_id):
    """Ambil data user dari database"""
    if users_collection:
        return users_collection.find_one({"user_id": user_id})
    else:
        try:
            with open('users.json', 'r') as f:
                users = json.load(f)
                return users.get(str(user_id))
        except:
            return None

def save_user(user_id, data):
    """Simpan data user"""
    if users_collection:
        users_collection.update_one(
            {"user_id": user_id},
            {"$set": data},
            upsert=True
        )
    else:
        try:
            with open('users.json', 'r') as f:
                users = json.load(f)
        except:
            users = {}
        users[str(user_id)] = data
        with open('users.json', 'w') as f:
            json.dump(users, f, indent=2)

def get_session(user_id):
    """Ambil session string user"""
    if sessions_collection:
        session_data = sessions_collection.find_one({"user_id": user_id})
        return session_data.get('session_string') if session_data else None
    return None

def save_session(user_id, session_string):
    """Simpan session string"""
    if sessions_collection:
        sessions_collection.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "session_string": session_string,
                "created_at": datetime.now().isoformat()
            }},
            upsert=True
        )

# ==================== KONFIGURASI PLAN ====================
PLANS = {
    'lite': {
        'name': '⚡ Plan Lite',
        'price': 10000,
        'plugins': 25,
        'features': 'Fitur dasar, 25 plugin'
    },
    'basic': {
        'name': '🧩 Plan Basic', 
        'price': 15000,
        'plugins': 56,
        'features': 'Fitur standar, 56 plugin'
    },
    'pro': {
        'name': '💎 Plan Pro',
        'price': 22000,
        'plugins': 99,
        'features': 'Semua fitur, 99 plugin'
    }
}

# States untuk ConversationHandler
(
    SELECTING_PLAN, SELECTING_DURATION, WAITING_PAYMENT_PROOF,
    WAITING_PHONE, WAITING_OTP, WAITING_2FA_PASSWORD,
    USERBOT_ACTIVE
) = range(7)

# ==================== PYROGRAM CLIENT MANAGER ====================
class UserbotManager:
    """Manage multiple Pyrogram clients"""
    
    def __init__(self):
        self.clients = {}  # {user_id: Client}
        self.active = {}   # {user_id: bool}
    
    async def create_client(self, user_id, phone_number):
        """Buat client baru untuk user"""
        client = Client(
            name=f"userbot_{user_id}",
            api_id=API_ID,
            api_hash=API_HASH,
            phone_number=phone_number,
            workdir=f"sessions/{user_id}"
        )
        return client
    
    async def send_code(self, client):
        """Kirim kode OTP"""
        try:
            sent_code = await client.send_code(client.phone_number)
            return True, sent_code.phone_code_hash
        except PhoneNumberInvalid:
            return False, "Nomor telepon tidak valid!"
        except FloodWait as e:
            return False, f"Terlalu banyak request! Tunggu {e.value} detik."
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    async def sign_in(self, client, phone_code_hash, otp):
        """Verifikasi OTP"""
        try:
            await client.sign_in(
                client.phone_number,
                phone_code_hash,
                otp.replace(" ", "")  # Hapus spasi
            )
            return True, None
        except PhoneCodeInvalid:
            return False, "Kode OTP salah!"
        except PhoneCodeExpired:
            return False, "Kode OTP sudah expired!"
        except SessionPasswordNeeded:
            return None, "2FA"  # Butuh password 2FA
        except Exception as e:
            return False, f"Error: {str(e)}"
    
    async def check_2fa(self, client, password):
        """Verifikasi 2FA password"""
        try:
            await client.check_password(password)
            return True, None
        except Exception as e:
            return False, f"Password 2FA salah: {str(e)}"
    
    async def export_session(self, client):
        """Export session string"""
        try:
            session_string = await client.export_session_string()
            return session_string
        except Exception as e:
            logger.error(f"Export session error: {e}")
            return None
    
    async def start_userbot(self, user_id, plan):
        """Mulai userbot untuk user"""
        if user_id in self.clients and self.active.get(user_id):
            return True  # Sudah aktif
        
        session_string = get_session(user_id)
        if not session_string:
            return False
        
        try:
            client = Client(
                name=f"userbot_active_{user_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                session_string=session_string,
                in_memory=True
            )
            
            # Register handlers berdasarkan plan
            await self.register_handlers(client, user_id, plan)
            
            await client.start()
            self.clients[user_id] = client
            self.active[user_id] = True
            
            # Update status di database
            user_data = get_user(user_id) or {}
            user_data['userbot_active'] = True
            user_data['last_started'] = datetime.now().isoformat()
            save_user(user_id, user_data)
            
            logger.info(f"✅ Userbot {user_id} started!")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to start userbot {user_id}: {e}")
            return False
    
    async def stop_userbot(self, user_id):
        """Hentikan userbot"""
        if user_id in self.clients:
            try:
                await self.clients[user_id].stop()
                self.active[user_id] = False
                logger.info(f"🛑 Userbot {user_id} stopped")
                return True
            except Exception as e:
                logger.error(f"Error stopping {user_id}: {e}")
                return False
        return False
    
    async def register_handlers(self, client, user_id, plan):
        """Daftarkan command handlers"""
        from pyrogram import filters as pyro_filters
        
        plugins = PLANS[plan]['plugins'] if plan in PLANS else 25
        
        # Handler: Ping
        @client.on_message(pyro_filters.command("ping") & pyro_filters.me)
        async def ping_handler(client, message):
            start = time.time()
            await message.edit("🏓 Pong!")
            end = time.time()
            await message.edit(f"🏓 **Pong!**\n`{(end-start)*1000:.1f}ms`")
        
        # Handler: Alive
        @client.on_message(pyro_filters.command("alive") & pyro_filters.me)
        async def alive_handler(client, message):
            expired = get_user(user_id).get('expired', 'Unknown')
            await message.edit(
                f"🤖 **Userbot Active!**\n"
                f"Plan: {plan.upper()}\n"
                f"Plugins: {plugins}\n"
                f"Expired: {expired[:10] if expired != 'Unknown' else 'Unknown'}"
            )
        
        # Handler: Help
        @client.on_message(pyro_filters.command("help") & pyro_filters.me)
        async def help_handler(client, message):
            help_text = self.get_help_text(plan, plugins)
            await message.edit(help_text)
        
        # LITE PLAN (25 plugin)
        if plugins >= 25:
            @client.on_message(pyro_filters.command("afk") & pyro_filters.me)
            async def afk_handler(client, message):
                reason = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else "AFK"
                await message.edit(f"😴 **AFK:** {reason}")
            
            @client.on_message(pyro_filters.command("spam") & pyro_filters.me)
            async def spam_handler(client, message):
                try:
                    args = message.text.split()
                    if len(args) < 3:
                        await message.edit("Usage: `.spam <jumlah> <teks>`")
                        return
                    count = min(int(args[1]), 10)  # Max 10 untuk lite
                    text = " ".join(args[2:])
                    await message.delete()
                    for i in range(count):
                        await client.send_message(message.chat.id, text)
                        await asyncio.sleep(0.5)
                except Exception as e:
                    await message.edit(f"Error: {e}")
        
        # BASIC PLAN (56 plugin)
        if plugins >= 56:
            @client.on_message(pyro_filters.command("broadcast") & pyro_filters.me)
            async def broadcast_handler(client, message):
                text = message.text.split(maxsplit=1)[1] if len(message.text.split()) > 1 else None
                if not text:
                    await message.edit("Usage: `.broadcast <pesan>`")
                    return
                await message.edit("📢 Broadcasting...")
                count = 0
                async for dialog in client.get_dialogs():
                    try:
                        if dialog.chat.type in ["group", "supergroup"]:
                            await client.send_message(dialog.chat.id, text)
                            count += 1
                            await asyncio.sleep(1)
                    except:
                        pass
                await message.edit(f"✅ Broadcast ke {count} grup")
            
            @client.on_message(pyro_filters.command("tagall") & pyro_filters.me)
            async def tagall_handler(client, message):
                if message.chat.type not in ["group", "supergroup"]:
                    await message.edit("Hanya untuk grup!")
                    return
                await message.edit("🏷️ Tagging...")
                tags = []
                async for member in client.get_chat_members(message.chat.id):
                    if not member.user.is_bot:
                        tags.append(f"[{member.user.first_name}](tg://user?id={member.user.id})")
                    if len(tags) == 5:
                        await client.send_message(message.chat.id, " ".join(tags))
                        tags = []
                        await asyncio.sleep(1)
                if tags:
                    await client.send_message(message.chat.id, " ".join(tags))
                await message.delete()
        
        # PRO PLAN (99 plugin)
        if plugins >= 99:
            @client.on_message(pyro_filters.command("yt") & pyro_filters.me)
            async def yt_handler(client, message):
                await message.edit("⬇️ Download feature (Pro)")
            
            @client.on_message(pyro_filters.command("ai") & pyro_filters.me)
            async def ai_handler(client, message):
                await message.edit("🤖 AI feature (Pro)")
    
    def get_help_text(self, plan, plugins):
        """Generate help text"""
        text = f"🤖 **USERBOT COMMANDS ({plan.upper()})**\n\n"
        text += "**Basic:**\n`.ping` `.alive` `.help`\n\n"
        
        if plugins >= 25:
            text += "**Lite:**\n`.afk` `.spam`\n\n"
        if plugins >= 56:
            text += "**Basic:**\n`.broadcast` `.tagall`\n\n"
        if plugins >= 99:
            text += "**Pro:**\n`.yt` `.ai` + 40+ more\n\n"
        
        return text

# Global manager
userbot_manager = UserbotManager()

# ==================== BOT ADMIN HANDLERS ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    user = update.effective_user
    
    # Cek apakah user sudah terdaftar
    user_data = get_user(user.id)
    
    if not user_data:
        # User baru
        save_user(user.id, {
            'user_id': user.id,
            'username': user.username,
            'name': user.first_name,
            'registered': datetime.now().isoformat(),
            'plan': None,
            'expired': None,
            'userbot_active': False,
            'phone': None
        })
        
        welcome_text = f"""
✨ **Selamat datang, {user.first_name}!**

🤖 Saya adalah *Userbot Assistant* yang akan membantu Anda membuat userbot dengan mudah dan cepat.

📋 **Pilih menu di bawah untuk memulai:**
"""
    else:
        # User lama
        welcome_text = f"""
✨ **Selamat datang kembali, {user.first_name}!**

🤖 Userbot Assistant siap membantu.

📋 **Pilih menu:**
"""
    
    await update.message.reply_text(
        welcome_text,
        parse_mode='Markdown',
        reply_markup=get_main_menu()
    )

def get_main_menu():
    """Menu utama"""
    keyboard = [
        [InlineKeyboardButton("✨ Mulai Buat Userbot", callback_data='create_userbot')],
        [InlineKeyboardButton("❓ Status Akun", callback_data='status')],
        [InlineKeyboardButton("⚡ Plan Lite", callback_data='plan_lite'),
         InlineKeyboardButton("🧩 Plan Basic", callback_data='plan_basic'),
         InlineKeyboardButton("💎 Plan Pro", callback_data='plan_pro')],
        [InlineKeyboardButton("🔑 Token", callback_data='token'),
         InlineKeyboardButton("🔄 Restart Userbot", callback_data='restart')],
        [InlineKeyboardButton("⚙️ Settings", callback_data='settings')],
        [InlineKeyboardButton("💬 Hubungi Admin", callback_data='contact_admin')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle semua tombol"""
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = update.effective_user.id
    
    if data == 'create_userbot':
        await show_plan_selection(query)
    
    elif data.startswith('plan_'):
        plan_key = data.split('_')[1]
        await show_duration_selection(query, plan_key)
    
    elif data.startswith('duration_'):
        parts = data.split('_')
        plan_key = parts[1]
        months = int(parts[2])
        await show_payment_info(query, plan_key, months)
    
    elif data == 'confirm_payment':
        await start_payment_process(query, context)
    
    elif data == 'lanjutkan_buat':  # Setelah bayar
        await start_userbot_creation(query, context)
    
    elif data == 'status':
        await show_user_status(query)
    
    elif data == 'restart':
        await restart_userbot(query)
    
    elif data == 'back_menu':
        await query.edit_message_text(
            "🏠 **Menu Utama**",
            parse_mode='Markdown',
            reply_markup=get_main_menu()
        )

async def show_plan_selection(query):
    """Tampilkan pilihan plan"""
    text = """
📦 **PILIH PAKET USERBOT**

⚡ **Plan Lite** - Rp10.000/bulan
├ 25 Plugin
└ Fitur dasar

🧩 **Plan Basic** - Rp15.000/bulan  
├ 56 Plugin
└ Fitur standar

💎 **Plan Pro** - Rp22.000/bulan
├ 99 Plugin
└ Semua fitur

**Pilih plan:**
"""
    keyboard = [
        [InlineKeyboardButton("⚡ Lite", callback_data='select_lite'),
         InlineKeyboardButton("🧩 Basic", callback_data='select_basic'),
         InlineKeyboardButton("💎 Pro", callback_data='select_pro')]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_duration_selection(query, plan_key):
    """Pilih durasi"""
    plan = PLANS[plan_key]
    text = f"""
{plan['name']}

💰 Rp{plan['price']:,}/bulan

🎁 **Diskon:**
• 2 bulan: Rp10.000 off
• 5 bulan: Rp25.000 off  
• 12 bulan: 33% off

**Pilih durasi:**
"""
    keyboard = [
        [InlineKeyboardButton("1 Bulan", callback_data=f'duration_{plan_key}_1'),
         InlineKeyboardButton("2 Bulan", callback_data=f'duration_{plan_key}_2')],
        [InlineKeyboardButton("3 Bulan", callback_data=f'duration_{plan_key}_3'),
         InlineKeyboardButton("6 Bulan", callback_data=f'duration_{plan_key}_6')],
        [InlineKeyboardButton("12 Bulan", callback_data=f'duration_{plan_key}_12')],
        [InlineKeyboardButton("🔙 Kembali", callback_data='create_userbot')]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def show_payment_info(query, plan_key, months):
    """Info pembayaran"""
    plan = PLANS[plan_key]
    base = plan['price'] * months
    discount = 0
    if months >= 12:
        discount = int(base * 0.33)
    elif months >= 5:
        discount = 25000
    elif months >= 2:
        discount = 10000
    final = base - discount
    
    order_id = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=8))
    
    # Simpan ke context
    query.message.chat.id  # Untuk akses nanti
    
    user_id = query.from_user.id
    context_data = {
        'order_id': order_id,
        'plan': plan_key,
        'months': months,
        'amount': final,
        'status': 'pending'
    }
    # Simpan sementara (nanti di database)
    pending_payments[user_id] = context_data
    
    text = f"""
🛒 **KONFIRMASI PEMBAYARAN**

📋 Order ID: `{order_id}`
📦 Plan: {plan['name']}
⏱️ Durasi: {months} bulan

💵 **Rincian:**
├ Harga: Rp{base:,}
├ Diskon: Rp{discount:,}
└ **Total: Rp{final:,}**

🏦 **Cara Bayar:**
• BCA: 1234567890 (a.n Userbot)
• DANA: 081234567890
• QRIS: Minta ke @admin

✅ Klik "Konfirmasi" setelah transfer
"""
    keyboard = [
        [InlineKeyboardButton("✅ Konfirmasi Pembayaran", callback_data='confirm_payment')],
        [InlineKeyboardButton("❌ Batal", callback_data='back_menu')]
    ]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def start_payment_process(query, context):
    """Proses konfirmasi pembayaran"""
    user_id = query.from_user.id
    
    # Kirim ke admin untuk verifikasi
    payment = pending_payments.get(user_id, {})
    
    admin_text = f"""
🚨 **PEMBAYARAN BARU**

👤 User: @{query.from_user.username or 'N/A'}
🆔 ID: `{user_id}`
📋 Order: {payment.get('order_id')}
📦 Plan: {payment.get('plan')}
⏱️ Durasi: {payment.get('months')} bulan
💰 Total: Rp{payment.get('amount', 0):,}

Kirim:
/verify {user_id} {payment.get('order_id')}
atau
/reject {user_id} {payment.get('order_id')}
"""
    
    await context.bot.send_message(ADMIN_ID, admin_text, parse_mode='Markdown')
    
    await query.edit_message_text(
        "📸 **Silakan upload bukti pembayaran**\n\n"
        "Kirim screenshot bukti transfer ke chat ini.",
        parse_mode='Markdown'
    )
    
    # Set state
    context.user_data['waiting_payment'] = True

async def handle_payment_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle bukti pembayaran"""
    if not context.user_data.get('waiting_payment'):
        return
    
    user_id = update.effective_user.id
    
    # Forward ke admin
    await update.message.forward(ADMIN_ID)
    await context.bot.send_message(
        ADMIN_ID,
        f"📸 Bukti dari user {user_id}\nVerifikasi dengan: /verify {user_id} [order_id]",
        parse_mode='Markdown'
    )
    
    await update.message.reply_text(
        "✅ **Bukti diterima!**\n\n"
        "Admin akan verifikasi 1x24 jam.\n"
        "Anda akan mendapat notifikasi.",
        parse_mode='Markdown'
    )
    
    context.user_data['waiting_payment'] = False

async def verify_payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin verifikasi pembayaran"""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /verify <user_id> <order_id>")
        return
    
    user_id = int(context.args[0])
    order_id = context.args[1]
    
    # Update user
    payment = pending_payments.get(user_id, {})
    months = payment.get('months', 1)
    plan = payment.get('plan', 'lite')
    
    expired = (datetime.now() + timedelta(days=30 * months)).isoformat()
    
    save_user(user_id, {
        'plan': plan,
        'expired': expired,
        'payment_verified': True,
        'order_id': order_id
    })
    
    # Notifikasi user
    await context.bot.send_message(
        user_id,
        f"""
✅ **PEMBAYARAN DIVERIFIKASI!**

Order: `{order_id}`
Plan: {PLANS[plan]['name']}
Expired: {expired[:10]}

🚀 **Klik tombol di bawah untuk mulai buat userbot!**
""",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✨ Lanjutkan Buat Userbot", callback_data='lanjutkan_buat')
        ]])
    )
    
    await update.message.reply_text(f"✅ User {user_id} verified!")

async def start_userbot_creation(query, context):
    """Mulai proses buat userbot (setelah bayar)"""
    user_id = query.from_user.id
    user_data = get_user(user_id)
    
    if not user_data or not user_data.get('plan'):
        await query.edit_message_text("❌ Anda belum memiliki plan aktif!")
        return
    
    await query.edit_message_text(
        """
📱 **SETUP USERBOT**

**Langkah 1/3:** Kirimkan nomor telepon Telegram Anda

Format: `+6281234567890`

⚠️ Pastikan:
• Nomor aktif di Telegram
• Bisa menerima SMS/kode
• Jangan pakai nomor yang sudah jadi userbot di tempat lain
""",
        parse_mode='Markdown'
    )
    
    context.user_data['setup_step'] = 'waiting_phone'

async def handle_setup_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle pesan setup (nomor, OTP, password)"""
    user_id = update.effective_user.id
    step = context.user_data.get('setup_step')
    text = update.message.text
    
    if step == 'waiting_phone':
        # Validasi nomor
        if not text.startswith('+') or not text[1:].isdigit():
            await update.message.reply_text("❌ Format salah! Gunakan: `+6281234567890`")
            return
        
        # Simpan nomor
        save_user(user_id, {'phone': text})
        context.user_data['phone'] = text
        
        # Buat client dan kirim OTP
        await update.message.reply_text("⏳ Mengirim kode OTP...")
        
        try:
            client = Client(
                name=f"temp_{user_id}",
                api_id=API_ID,
                api_hash=API_HASH,
                phone_number=text
            )
            
            await client.connect()
            sent_code = await client.send_code(text)
            context.user_data['phone_code_hash'] = sent_code.phone_code_hash
            context.user_data['client'] = client
            context.user_data['setup_step'] = 'waiting_otp'
            
            await update.message.reply_text(
                """
📲 **Kode OTP telah dikirim!**

**Langkah 2/3:** Masukkan kode OTP

⚠️ **PENTING:** Tambahkan spasi antar angka!
Contoh: Jika kode `50169`, kirim: `5 0 1 6 9`

⏱️ Kode berlaku 2 menit
"""
            )
            
        except Exception as e:
            logger.error(f"Send code error: {e}")
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    elif step == 'waiting_otp':
        # Verifikasi OTP
        otp = text.replace(" ", "")  # Hapus spasi
        client = context.user_data.get('client')
        phone = context.user_data.get('phone')
        phone_code_hash = context.user_data.get('phone_code_hash')
        
        try:
            await client.sign_in(phone, phone_code_hash, otp)
            
            # Berhasil login!
            session_string = await client.export_session_string()
            save_session(user_id, session_string)
            
            await client.disconnect()
            
            # Cek apakah butuh 2FA
            context.user_data['setup_step'] = 'completed'
            
            # Langsung start userbot
            user_data = get_user(user_id)
            plan = user_data.get('plan', 'lite')
            
            success = await userbot_manager.start_userbot(user_id, plan)
            
            if success:
                await update.message.reply_text(
                    f"""
🔥 **LIFEBOT BERHASIL DI AKTIFKAN!**

✅ Akun: {phone}
✅ Plan: {plan.upper()}
✅ Status: Aktif

🎉 Userbot Anda sekarang berjalan 24/7!

**Cara pakai:**
Ketik `.help` di chat mana saja untuk melihat command.

⚠️ **Jangan logout dari Telegram di HP Anda!**
Ini akan mematikan userbot.
"""
                )
            else:
                await update.message.reply_text("❌ Gagal start userbot. Hubungi admin.")
                
        except SessionPasswordNeeded:
            # Butuh 2FA
            context.user_data['setup_step'] = 'waiting_2fa'
            await update.message.reply_text(
                """
🔐 **Verifikasi Dua Langkah**

Akun Anda memiliki password 2FA.

**Langkah 3/3:** Masukkan password 2FA Anda:
"""
            )
            
        except Exception as e:
            await update.message.reply_text(f"❌ Error: {str(e)}")
    
    elif step == 'waiting_2fa':
        # Verifikasi 2FA
        password = text
        client = context.user_data.get('client')
        
        try:
            await client.check_password(password)
            
            # Berhasil!
            session_string = await client.export_session_string()
            save_session(user_id, session_string)
            
            await client.disconnect()
            
            # Start userbot
            user_data = get_user(user_id)
            plan = user_data.get('plan', 'lite')
            
            success = await userbot_manager.start_userbot(user_id, plan)
            
            if success:
                await update.message.reply_text(
                    f"""
🔥 **LIFEBOT BERHASIL DI AKTIFKAN!**

✅ Akun terhubung
✅ 2FA diverifikasi
✅ Plan: {plan.upper()}
✅ Status: Aktif 24/7

🎉 Userbot siap digunakan!

Ketik `.help` di chat mana saja untuk command.
"""
                )
            else:
                await update.message.reply_text("❌ Gagal start. Hubungi admin.")
                
            context.user_data['setup_step'] = None
            
        except Exception as e:
            await update.message.reply_text(f"❌ Password salah: {str(e)}")

async def show_user_status(query):
    """Tampilkan status user"""
    user_id = query.from_user.id
    user_data = get_user(user_id) or {}
    
    if not user_data.get('plan'):
        text = "❌ Anda belum memiliki plan aktif."
    else:
        plan = PLANS.get(user_data['plan'], {})
        expired = user_data.get('expired', 'N/A')
        if expired != 'N/A':
            expired = expired[:10]
        
        text = f"""
📊 **STATUS AKUN**

👤 Nama: {user_data.get('name', 'N/A')}
📦 Plan: {plan.get('name', 'Unknown')}
🎯 Plugin: {plan.get('plugins', 0)} plugin
⏱️ Expired: {expired}
🤖 Status: {'✅ Aktif' if user_data.get('userbot_active') else '❌ Nonaktif'}
📱 Nomor: {user_data.get('phone', 'Belum setup')}
"""
    
    keyboard = [[InlineKeyboardButton("🔙 Kembali", callback_data='back_menu')]]
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup(keyboard))

async def restart_userbot(query):
    """Restart userbot"""
    user_id = query.from_user.id
    user_data = get_user(user_id) or {}
    
    if not user_data.get('plan'):
        await query.edit_message_text("❌ Anda belum punya plan!")
        return
    
    await query.edit_message_text("🔄 Merestart userbot...")
    
    # Stop dulu
    await userbot_manager.stop_userbot(user_id)
    await asyncio.sleep(2)
    
    # Start lagi
    success = await userbot_manager.start_userbot(user_id, user_data.get('plan'))
    
    if success:
        await query.edit_message_text(
            "✅ **Userbot berhasil direstart!**\n\nUserbot aktif kembali.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data='back_menu')]])
        )
    else:
        await query.edit_message_text(
            "❌ Gagal restart. Coba lagi atau hubungi admin.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Kembali", callback_data='back_menu')]])
        )

# ==================== BACKGROUND TASKS ====================

async def check_expired_loop():
    """Cek userbot expired setiap jam"""
    while True:
        await asyncio.sleep(3600)  # 1 jam
        
        now = datetime.now()
        
        # Cek semua user
        if users_collection:
            expired_users = users_collection.find({
                "expired": {"$lt": now.isoformat()},
                "userbot_active": True
            })
            
            for user in expired_users:
                user_id = user['user_id']
                await userbot_manager.stop_userbot(user_id)
                users_collection.update_one(
                    {"user_id": user_id},
                    {"$set": {"userbot_active": False}}
                )
                
                # Notifikasi user
                try:
                    await application.bot.send_message(
                        user_id,
                        "⏰ **Masa aktif userbot Anda telah habis!**\n\n"
                        "Silakan perpanjang untuk terus menggunakan.",
                        parse_mode='Markdown'
                    )
                except:
                    pass

def start_background_tasks():
    """Mulai background tasks"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(check_expired_loop())

# ==================== MAIN ====================

def main():
    global application
    
    # Start Flask
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Start background task
    bg_thread = threading.Thread(target=start_background_tasks, daemon=True)
    bg_thread.start()
    
    # Setup bot
    application = Application.builder().token(TOKEN).build()
    
    # Handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("verify", verify_payment_command))
    
    # Callback handler
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Message handlers
    application.add_handler(MessageHandler(filters.PHOTO, handle_payment_proof))
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND, 
        handle_setup_message
    ))
    
    # Restore active userbots on startup
    async def restore_userbots():
        await asyncio.sleep(5)  # Tunggu bot siap
        if users_collection:
            active_users = users_collection.find({"userbot_active": True})
            for user in active_users:
                try:
                    await userbot_manager.start_userbot(
                        user['user_id'], 
                        user.get('plan', 'lite')
                    )
                    await asyncio.sleep(2)  # Jeda antar userbot
                except Exception as e:
                    logger.error(f"Failed to restore {user['user_id']}: {e}")
    
    # Jalankan restore di background
    asyncio.create_task(restore_userbots())
    
    logger.info("🤖 Bot Userbot SaaS berjalan...")
    application.run_polling()

if __name__ == '__main__':
    main()
