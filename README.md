# 🤖 CryptoTrader Pro

Production-grade, modüler, çok kullanıcılı kripto trading bot platformu.

---

## 📁 Proje Yapısı

```
cryptotrader_pro/
├── config.py           # Tüm ayarlar, .env okuma
├── models.py           # SQLAlchemy ORM modelleri + DB init
├── cache.py            # Redis / in-memory iki katmanlı cache
├── logger.py           # Structured JSON logging (Grafana uyumlu)
├── api.py              # Binance + Bybit + CoinGecko + WebSocket
├── analysis.py         # RSI, EMA, MACD, BB, ATR, multi-TF sinyal
├── ai.py               # Ensemble ML modeli, online learning
├── risk.py             # Position sizing, SL/TP, trailing stop, correlation
├── scanner.py          # Market tarama + CoinProfile oluşturma
├── trade.py            # Trade motoru (sim + gerçek), PnL, equity
├── notifications.py    # Telegram + Discord + Email bildirimleri
├── backtest.py         # Geçmiş veri backtesting motoru
├── bot.py              # Ana bot döngüsü + BotRegistry (multi-user)
├── backend.py          # FastAPI REST backend + JWT + 2FA
├── panel.py            # Streamlit dashboard
├── tests/              # Pytest unit testleri
├── migrations/         # Alembic DB migration dosyaları
├── .github/workflows/  # GitHub Actions CI
├── .streamlit/         # Streamlit tema ve cloud ayarları
├── requirements.txt
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── export_zip.py
```

---

## 🚀 Hızlı Başlangıç

### 1. Ortam Kurulumu

```bash
# Repoyu klonla
git clone https://github.com/youruser/cryptotrader-pro.git
cd cryptotrader-pro

# Python venv oluştur
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Bağımlılıkları yükle
pip install -r requirements.txt

# .env dosyasını oluştur
cp .env.example .env
# → .env dosyasını düzenle, API key'leri ekle
```

### 2. Veritabanı Başlatma

```bash
# SQLite (geliştirme için):
python models.py

# Alembic ile (production için):
alembic upgrade head
```

### 3. Backend'i Başlat

```bash
uvicorn backend:app --host 0.0.0.0 --port 8000 --reload
# API docs: http://localhost:8000/docs
```

### 4. Dashboard'u Başlat

```bash
streamlit run panel.py
# Arayüz: http://localhost:8501
```

**Varsayılan admin giriş bilgileri:**
- Kullanıcı: `admin`
- Şifre: `admin1234`
- ⚠️ İlk girişten sonra değiştir!

---

## 🐳 Docker ile Kurulum (Önerilen)

```bash
cp .env.example .env
# .env dosyasını düzenle

docker compose up -d

# Servisler:
# Backend  → http://localhost:8000
# API Docs → http://localhost:8000/docs
# Panel    → http://localhost:8501
```

---

## 🔑 .env Temel Ayarlar

| Değişken | Açıklama | Varsayılan |
|---|---|---|
| `DATABASE_URL` | Veritabanı bağlantısı | SQLite |
| `JWT_SECRET` | JWT imzalama anahtarı | *Değiştir!* |
| `ENCRYPTION_KEY` | API key şifreleme (Fernet) | *Oluştur!* |
| `BINANCE_API_KEY` | Binance API anahtarı | — |
| `BINANCE_TESTNET` | Testnet kullan | `true` |
| `SIMULATION_MODE` | Gerçek trade yapma | `true` |
| `TELEGRAM_BOT_TOKEN` | Bildirim botu | — |

**Fernet key oluşturma:**
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

---

## 📊 API Endpoint'leri

| Method | Path | Açıklama |
|---|---|---|
| `GET` | `/health` | Sağlık kontrolü |
| `POST` | `/register` | Yeni kullanıcı kaydı |
| `POST` | `/login` | JWT token al |
| `GET` | `/me` | Kullanıcı bilgileri |
| `PUT` | `/settings` | Ayarları güncelle |
| `POST` | `/bot/start` | Botu başlat |
| `POST` | `/bot/stop` | Botu durdur |
| `GET` | `/bot/status` | Bot durumu |
| `POST` | `/bot/emergency` | Acil dur (tüm pozisyonları kapat) |
| `GET` | `/portfolio` | Portföy anlık görünümü |
| `GET` | `/trade/history` | Geçmiş işlemler |
| `GET` | `/scan/latest` | Son tarama sonuçları |
| `POST` | `/backtest` | Backtest çalıştır |
| `POST` | `/2fa/setup` | TOTP 2FA kur (admin) |
| `GET` | `/admin/users` | Kullanıcı yönetimi |
| `GET` | `/admin/logs` | Sistem logları |

---

## 🧠 Trading Stratejisi

### Sinyal Üretimi
1. **RSI (14)** — aşırı alım/satım tespiti
2. **EMA (9, 21)** — trend yönü
3. **MACD histogram** — momentum
4. **Bollinger Bands** — fiyat konumu
5. **ATR** — volatilite ölçümü
6. **Volume ratio** — hacim onayı

### Multi-Timeframe Ağırlıkları
| Zaman Dilimi | Ağırlık |
|---|---|
| 1m | 1 |
| 5m | 2 |
| 15m | 3 |
| 1h | 4 |

Sinyal üretmek için ağırlıklı oy sayısının **≥%60'ı** aynı yönde olmalı.

### AI Skoru (0-100)
- **0-40** → Zayıf sinyal → pozisyon %50 küçültülür
- **40-70** → Normal → standart pozisyon
- **70-100** → Güçlü sinyal → pozisyon x2'ye kadar büyütülür

---

## 🛡 Risk Yönetimi

- **Position Sizing**: Equity'nin `RISK_PERCENT`'i kadar risk
- **Stop Loss / Take Profit**: Konfigüre edilebilir %, varsayılan SL:2% TP:4% (RR=2)
- **Trailing Stop**: En iyi fiyata göre dinamik SL
- **Correlation Filter**: Aynı anda benzer coinlerde pozisyon engellenir
- **Daily Loss Limit**: Günlük zarar limitine ulaşınca bot durur
- **Emergency Stop**: Tüm açık pozisyonları tek tuşla kapatır

---

## 🤖 AI / ML Sistemi

- **Ensemble Model**: GradientBoosting + RandomForest (soft voting)
- **Feature Vector**: 31 özellik (teknik + likidite + sentiment + whale)
- **Online Learning**: Her kapalı trade'den öğrenir, `MIN_TRAIN_SAMPLES=50` sonra eğitim başlar
- **Model Persistence**: `models/signal_model.joblib` — restart'ta yüklenir
- **Feature Importance**: Her eğitimde loglanır

---

## 📈 Backtest

```bash
# HTTP üzerinden:
curl -X POST http://localhost:8000/backtest \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"BTCUSDT","interval":"1h","limit":1000}'
```

**Çıktı metrikleri:**
- Net Profit / %
- Win Rate
- Profit Factor
- Max Drawdown
- Sharpe Ratio
- Equity Curve

---

## 🔔 Bildirim Kurulumu

### Telegram
1. `@BotFather`'dan bot oluştur → `TELEGRAM_BOT_TOKEN` al
2. Bota mesaj at, `@userinfobot`'tan chat ID'ni öğren → `TELEGRAM_CHAT_ID`

### Discord
1. Sunucu Ayarları → Entegrasyonlar → Webhook oluştur
2. URL'yi `DISCORD_WEBHOOK`'a yapıştır

### Gmail
```
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USER=senin@gmail.com
EMAIL_PASS=uygulama-şifresi  # Gmail App Password
EMAIL_TO=hedef@email.com
```

---

## 🔐 Güvenlik

- API key'leri veritabanında **Fernet şifrelemesi** ile saklanır
- JWT token'ları `JWT_EXPIRE_MINS` dakika sonra geçersiz olur
- Admin endpoint'leri `ADMIN_IP_WHITELIST` ile kısıtlanabilir
- Admin hesapları için **TOTP 2FA** (Google Authenticator)
- Rate limiting: varsayılan 100 istek/dakika
- Trade sanity check: absürd pozisyonlar reddedilir

---

## 🧪 Test Çalıştırma

```bash
pytest tests/ -v --cov=. --cov-report=term-missing
```

---

## 📦 ZIP Export

```bash
python export_zip.py
# → cryptotrader_pro_YYYYMMDD_HHMMSS.zip
```

---

## 🚀 Streamlit Cloud Deploy

1. GitHub'a push et
2. [streamlit.io/cloud](https://streamlit.io/cloud) → New App
3. Main file: `panel.py`
4. Secrets bölümüne `.streamlit/secrets.toml.example` içeriğini ekle

---

## ⚠️ Önemli Notlar

- **SIMULATION_MODE=true** ile başla, stratejiyi test et
- Gerçek trade için `BINANCE_TESTNET=false` ve `SIMULATION_MODE=false` yap
- İlk çalıştırmada ML modeli eğitilmemiş olur, kural tabanlı heuristic kullanılır
- Binance Testnet'te gerçek para yoktur, güvenle test edilebilir

---

## 📄 Lisans

MIT License — Ticari kullanım serbesttir.
