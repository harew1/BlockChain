TR / EN:

━━━━━━━━━━━━━━━━━━━━━━━
TR (TÜRKÇE – TAM DETAYLI)
━━━━━━━━━━━━━━━━━━━━━━━

Python ile production seviyesinde, modüler, genişletilebilir ve çok kullanıcılı (multi-user) bir crypto trading bot platformu geliştir.

Sistem; trading bot, AI analiz, coin data engine, backend API, kullanıcı yönetimi, admin panel ve Streamlit dashboard içermelidir.

TÜM MODÜLLERİ EKSİKSİZ VE ÇALIŞIR ŞEKİLDE OLUŞTUR:

━━━━━━━━━━━
1. API ENTEGRASYONU
━━━━━━━━━━━
- Binance Spot + Futures API
- Opsiyonel Bybit desteği
- WebSocket ile gerçek zamanlı veri
- REST fallback sistemi
- Rate limit ve retry mekanizması
- Veri:
  - ticker
  - kline (OHLCV)
  - order book (depth)
- CoinGecko API:
  - market cap
  - supply
- On-chain:
  - whale transferleri
  - liquidity pool değişimleri

━━━━━━━━━━━
2. COIN DATA ENGINE
━━━━━━━━━━━
Her coin için:
- Price
- 24h change %
- Volume
- Market cap
- Liquidity score (order book depth bazlı)
- Holder count
- Whale activity score
- Social sentiment (Twitter/Reddit)
→ Hepsini tek bir "coin profile" objesinde topla

━━━━━━━━━━━
3. TRADING STRATEJİ MOTORU
━━━━━━━━━━━
- RSI (14), EMA (9,21), momentum
- Multi-timeframe (1m,5m,15m,1h)
- Trend doğrulama
- Signal:
  - LONG / SHORT / WAIT
- Filtreler:
  - düşük hacim
  - düşük likidite
- AI score (0-100)

━━━━━━━━━━━
4. AI / ADAPTIVE SYSTEM
━━━━━━━━━━━
- Basit ML modeli (classification/regression)
- Input:
  - teknik veriler
  - likidite
  - sosyal veri
- Online learning
- Dynamic risk:
  - güçlü sinyal → büyük pozisyon
  - zayıf → küçük

━━━━━━━━━━━
5. RİSK YÖNETİMİ
━━━━━━━━━━━
- Position sizing (% risk)
- Stop-loss
- Take-profit (RR)
- Trailing stop
- Max trade limiti
- Max günlük zarar
- Leverage kontrol

━━━━━━━━━━━
6. MARKET SCANNER
━━━━━━━━━━━
- USDT pariteleri
- Filtre:
  - volume
  - likidite
  - volatilite
- Top N coin
- Sinyal + skor üret

━━━━━━━━━━━
7. ARBITRAJ MODÜLÜ
━━━━━━━━━━━
- Binance vs Bybit fiyat farkı
- % threshold
- fırsat tespiti

━━━━━━━━━━━
8. BOT LOOP
━━━━━━━━━━━
- scan → analyze → trade
- interval ayarlanabilir
- hata yönetimi
- log sistemi

━━━━━━━━━━━
9. TRADE ENGINE
━━━━━━━━━━━
- SIMULATION (default)
- REAL (opsiyonel)
- Market order
- Trade sonrası log + bildirim

━━━━━━━━━━━
10. PORTFOLIO TRACKER
━━━━━━━━━━━
- Açık pozisyonlar
- PnL
- ROI
- Equity curve

━━━━━━━━━━━
11. STREAMLIT PANEL
━━━━━━━━━━━
- Login ekranı
- Coin tablosu:
  - price, signal, score, market cap, volume, liquidity, sentiment
- Açık pozisyonlar
- PnL
- Grafikler
- Bot kontrol (start/stop)
- Real trade toggle

━━━━━━━━━━━
12. BACKEND API (FASTAPI)
━━━━━━━━━━━
- Endpointler:
  - /register
  - /login
  - /logout
  - /settings
  - /trade/history
  - /bot/start
  - /bot/stop
- JWT auth

━━━━━━━━━━━
13. KULLANICI SİSTEMİ
━━━━━━━━━━━
- Register / login
- bcrypt hash
- Roller:
  - admin
  - user
- Kullanıcıya özel:
  - API key
  - ayarlar

━━━━━━━━━━━
14. AYARLAR SİSTEMİ
━━━━━━━━━━━
- Trade aktif/pasif
- Risk %
- Stop loss
- Take profit
- Leverage
- Scan interval
- Coin seçimi

━━━━━━━━━━━
15. DATABASE
━━━━━━━━━━━
- PostgreSQL / SQLite
- SQLAlchemy
- Tablolar:
  - users
  - settings
  - trades
  - logs

━━━━━━━━━━━
16. LOG & HISTORY
━━━━━━━━━━━
- Trade history
- Error log
- System log

━━━━━━━━━━━
17. ADMIN PANEL
━━━━━━━━━━━
- Kullanıcı yönetimi
- Sistem kontrolü

━━━━━━━━━━━
18. BİLDİRİMLER
━━━━━━━━━━━
- Telegram
- Discord
- Email

━━━━━━━━━━━
19. BACKTEST
━━━━━━━━━━━
- Geçmiş veri test
- Win rate
- Profit
- Drawdown

━━━━━━━━━━━
20. GÜVENLİK
━━━━━━━━━━━
- API key encryption
- JWT expiration
- Rate limit
- Trade sanity check

━━━━━━━━━━━
21. MODÜLER YAPI
━━━━━━━━━━━
Dosyalar:
- api.py
- analysis.py
- scanner.py
- ai.py
- trade.py
- risk.py
- bot.py
- panel.py
- backend.py
- models.py
- config.py

━━━━━━━━━━━
22. DEPLOY
━━━━━━━━━━━
- requirements.txt
- .env sistemi
- Docker desteği
- ZIP export script
- Streamlit Cloud deploy hazır

━━━━━━━━━━━
23. KOD KALİTESİ
━━━━━━━━━━━
- Clean code
- Yorum satırları
- Hata yönetimi
- Genişletilebilir yapı

━━━━━━━━━━━━━━━━━━━━━━━
EN (ENGLISH – FULL DETAILED)
━━━━━━━━━━━━━━━━━━━━━━━

Create a production-grade, modular, scalable, multi-user crypto trading platform in Python including:

- Binance + Bybit API integration (WebSocket + REST fallback)
- Coin data engine (price, volume, market cap, liquidity, sentiment, whale activity)
- Technical analysis (RSI, EMA, momentum, multi-timeframe)
- AI adaptive scoring system with online learning
- Risk management (position sizing, SL/TP, trailing stop, exposure limits)
- Market scanner + arbitrage detection
- Bot loop (continuous execution)
- Trade engine (simulation + real modes)
- Portfolio tracking (PnL, ROI, equity curve)
- Streamlit dashboard with login, charts, controls
- FastAPI backend with JWT authentication
- User system (register/login, roles, per-user settings)
- Database (PostgreSQL/SQLite, SQLAlchemy)
- Trade history + logging system
- Admin panel
- Notifications (Telegram, Discord, email)
- Backtesting engine
- Security (encryption, rate limits, validation)
- Modular architecture (separate files)
- Deployment (Docker, .env, Streamlit Cloud)
- ZIP export script
- Clean, readable, production-quality code
