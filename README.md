# 🤖 Userbot SaaS Telegram

Bot Telegram untuk jualan userbot dengan sistem berlangganan.

## 📋 Fitur

- ✅ 3 Plan: Lite (25 plugin), Basic (56 plugin), Pro (99 plugin)
- ✅ Payment manual (transfer bank/e-wallet)
- ✅ Auto-generate session & activate userbot
- ✅ Multi-session dalam 1 bot
- ✅ Auto-restart kalau Replit sleep

## 🚀 Deploy di Replit

1. Fork repo ini ke Replit
2. Install requirements: `pip install -r requirements.txt`
3. Set environment variables (Secrets)
4. Run: `python main.py`

## 🔧 Environment Variables

| Variable | Keterangan |
|----------|-----------|
| `TOKEN` | Token bot dari @BotFather |
| `ADMIN_ID` | ID Telegram admin |
| `API_ID` | Dari my.telegram.org |
| `API_HASH` | Dari my.telegram.org |
| `MONGODB_URI` | Dari MongoDB Atlas |

## 📱 Cara Pakai

1. User kirim `/start`
2. Pilih plan & bayar
3. Admin verifikasi: `/verify &lt;user_id&gt; &lt;order_id&gt;`
4. User klik "Lanjutkan Buat Userbot"
5. User kirim nomor telepon
6. User kirim OTP (dengan spasi)
7. Kalau ada 2FA, kirim password
8. ✅ Userbot aktif 24/7!

## ⚠️ Limit Replit Gratis

- Max ~5-10 userbot aktif
- Sleep setelah 5 menit (pakai UptimeRobot)
- Gunakan MongoDB Atlas untuk database

## 📝 Harga

| Plan | Harga/Bulan | Plugin |
|------|-------------|--------|
| Lite | Rp10.000 | 25 |
| Basic | Rp15.000 | 56 |
| Pro | Rp22.000 | 99 |

Diskon: 2 bln (Rp10k), 5 bln (Rp25k), 12 bln (33%)
