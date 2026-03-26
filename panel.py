"""
panel.py — Streamlit multi-page dashboard.
Pages:
  🔐 Login
  📊 Market Overview (coin table + heatmap)
  💼 Portfolio (open positions, PnL, equity curve)
  🤖 Bot Control (start/stop/emergency, settings)
  📈 Backtest
  🕑 Alert History
  👥 Admin Panel (admin only)
"""

import asyncio
import datetime
import time
from typing import Optional

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import requests
import streamlit as st

# ─── Config ───────────────────────────────────────────────────────────────────
BACKEND = "http://localhost:8000"

st.set_page_config(
    page_title="CryptoTrader Pro",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ─── Helpers ──────────────────────────────────────────────────────────────────
def _api(method: str, path: str, json=None, params=None) -> Optional[dict]:
    token = st.session_state.get("token", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        r = getattr(requests, method)(
            f"{BACKEND}{path}", json=json, params=params,
            headers=headers, timeout=15
        )
        if r.status_code == 200:
            return r.json()
        st.error(f"API error {r.status_code}: {r.text[:200]}")
    except Exception as exc:
        st.error(f"Connection error: {exc}")
    return None


def _login_page() -> None:
    st.title("🔐 CryptoTrader Pro — Login")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            totp     = st.text_input("2FA Code (if enabled)", placeholder="Optional")
            submit   = st.form_submit_button("Login", use_container_width=True)

        if submit:
            payload = {"username": username, "password": password}
            if totp:
                payload["totp_code"] = totp
            try:
                r = requests.post(f"{BACKEND}/login", json=payload, timeout=10)
                if r.status_code == 200:
                    data = r.json()
                    st.session_state["token"]    = data["access_token"]
                    st.session_state["logged_in"] = True
                    # Fetch user info
                    me = _api("get", "/me")
                    if me:
                        st.session_state["username"] = me.get("username")
                        st.session_state["role"]     = me.get("role", "user")
                    st.success("Login successful!")
                    st.rerun()
                else:
                    st.error(f"Login failed: {r.json().get('detail', 'Unknown error')}")
            except Exception as exc:
                st.error(f"Cannot connect to backend: {exc}")


def _sidebar() -> str:
    with st.sidebar:
        st.markdown(f"### 🤖 CryptoTrader Pro")
        st.markdown(f"👤 **{st.session_state.get('username', '')}**")

        # Bot quick status
        status = _api("get", "/bot/status") or {}
        running = status.get("running", False)
        equity  = status.get("equity", 0)
        st.metric("Equity", f"${equity:,.2f}")
        if running:
            st.success("🟢 Bot Running")
        else:
            st.warning("🔴 Bot Stopped")

        st.divider()

        role  = st.session_state.get("role", "user")
        pages = ["📊 Market Overview", "💼 Portfolio",
                 "🤖 Bot Control", "📈 Backtest", "🕑 Alerts"]
        if role == "admin":
            pages.append("👥 Admin")

        selected = st.radio("Navigation", pages, label_visibility="collapsed")

        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    return selected


# ─── Pages ────────────────────────────────────────────────────────────────────
def _page_market() -> None:
    st.title("📊 Market Overview")

    # Refresh
    col1, col2 = st.columns([8, 1])
    with col2:
        if st.button("🔄 Refresh"):
            st.rerun()

    with st.spinner("Loading market data…"):
        profiles = _api("get", "/scan/latest") or []

    if not profiles:
        st.info("No scan data yet. Trigger a scan from Bot Control page.")
        return

    df = pd.DataFrame(profiles)
    if df.empty:
        return

    # Rename for display
    rename_map = {
        "symbol": "Symbol", "price": "Price", "change_24h": "24h%",
        "volume_24h": "Volume 24h", "market_cap": "Mkt Cap",
        "liquidity": "Liquidity", "whale_score": "Whale",
        "sentiment": "Sentiment", "volatility": "Volatility%",
        "signal": "Signal", "ai_score": "AI Score",
    }
    display_cols = [c for c in rename_map.keys() if c in df.columns]
    df_view = df[display_cols].rename(columns=rename_map)

    # Color-code signals
    def _signal_color(val):
        if val == "LONG":   return "background-color: #1a472a; color: #70e090"
        if val == "SHORT":  return "background-color: #5c1a1a; color: #e08080"
        return ""

    st.dataframe(
        df_view.style.applymap(_signal_color, subset=["Signal"])
                     .format({"Price": "{:.6f}", "24h%": "{:+.2f}%",
                               "Volume 24h": "{:,.0f}", "AI Score": "{:.1f}"}),
        use_container_width=True, height=460
    )

    # Heatmap — performance by symbol
    if "Symbol" in df_view.columns and "24h%" in df_view.columns:
        st.subheader("🌡 Performance Heatmap")
        heat_df = df_view[["Symbol", "24h%", "AI Score"]].dropna()
        fig = px.treemap(
            heat_df, path=["Symbol"], values="AI Score",
            color="24h%", color_continuous_scale=["#c0392b", "#ecf0f1", "#27ae60"],
            color_continuous_midpoint=0,
            title="Coin Heatmap (size=AI Score, color=24h%)"
        )
        fig.update_layout(template="plotly_dark", height=400)
        st.plotly_chart(fig, use_container_width=True)


def _page_portfolio() -> None:
    st.title("💼 Portfolio & PnL")

    port = _api("get", "/portfolio") or {}
    history = _api("get", "/trade/history", params={"limit": 50}) or []

    # Metrics row
    cols = st.columns(4)
    cols[0].metric("Equity",          f"${port.get('equity', 0):,.2f}")
    cols[1].metric("Unrealized PnL",  f"${port.get('unrealized_pnl', 0):+,.2f}")
    cols[2].metric("Realized PnL",    f"${port.get('realized_pnl', 0):+,.2f}")
    cols[3].metric("Win Rate",        f"{port.get('win_rate', 0):.1f}%")

    st.divider()

    # Open positions
    open_pos = port.get("open_positions", [])
    if open_pos:
        st.subheader("📂 Open Positions")
        pos_df = pd.DataFrame(open_pos)
        st.dataframe(pos_df.style.format(
            {"entry_price": "{:.6f}", "current_price": "{:.6f}",
             "unrealized_pnl": "{:+.4f}", "unrealized_pct": "{:+.2f}%",
             "ai_score": "{:.1f}"}),
            use_container_width=True)
    else:
        st.info("No open positions.")

    # Equity curve
    eq_curve = port.get("equity_curve", [])
    if eq_curve:
        st.subheader("📈 Equity Curve")
        eq_df = pd.DataFrame(eq_curve)
        fig = go.Figure(go.Scatter(
            x=eq_df["ts"], y=eq_df["equity"],
            fill="tozeroy", line=dict(color="#00c8ff"),
        ))
        fig.update_layout(template="plotly_dark", height=300,
                          xaxis_title="Time", yaxis_title="Equity (USDT)")
        st.plotly_chart(fig, use_container_width=True)

    # Trade history
    st.subheader("📋 Trade History")
    if history:
        h_df = pd.DataFrame(history)
        def pnl_style(val):
            if isinstance(val, float) and val > 0: return "color: #70e090"
            if isinstance(val, float) and val < 0: return "color: #e08080"
            return ""
        cols_to_show = [c for c in ["id","symbol","signal","status","pnl",
                                     "pnl_pct","ai_score","entry_time","notes"]
                        if c in h_df.columns]
        st.dataframe(
            h_df[cols_to_show].style.applymap(pnl_style, subset=["pnl"] if "pnl" in cols_to_show else []),
            use_container_width=True, height=350
        )
    else:
        st.info("No trades yet.")


def _page_bot_control() -> None:
    st.title("🤖 Bot Control")

    status = _api("get", "/bot/status") or {}
    me     = _api("get", "/me") or {}
    settings = me.get("settings", {})

    # Status
    running = status.get("running", False)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Status",      "🟢 Running" if running else "🔴 Stopped")
    col2.metric("Scans",       status.get("scan_count", 0))
    col3.metric("Open Trades", status.get("open_trades", 0))
    col4.metric("Uptime",      f"{status.get('uptime_secs', 0)//60}m")

    # Controls
    st.divider()
    cstart, cstop, cemergency = st.columns(3)
    with cstart:
        if st.button("▶ Start Bot", use_container_width=True, type="primary",
                     disabled=running):
            _api("post", "/bot/start")
            st.success("Bot started!")
            time.sleep(1); st.rerun()
    with cstop:
        if st.button("⏹ Stop Bot", use_container_width=True, disabled=not running):
            _api("post", "/bot/stop")
            st.info("Bot stopped.")
            time.sleep(1); st.rerun()
    with cemergency:
        if st.button("🚨 EMERGENCY STOP", use_container_width=True, type="secondary"):
            _api("post", "/bot/emergency")
            st.error("Emergency stop triggered!")
            time.sleep(1); st.rerun()

    # Trigger manual scan
    st.divider()
    if st.button("🔍 Trigger Manual Scan"):
        _api("post", "/scan/run")
        st.success("Scan triggered!")

    # Settings
    st.divider()
    st.subheader("⚙ Settings")
    with st.form("settings_form"):
        col_a, col_b = st.columns(2)
        with col_a:
            sim_mode    = st.toggle("Simulation Mode",  value=settings.get("simulation_mode", True))
            trd_enabled = st.toggle("Trading Enabled",  value=settings.get("trading_enabled", False))
            risk_pct    = st.slider("Risk %",           0.1, 5.0, float(settings.get("risk_percent", 1.0)), 0.1)
            sl          = st.slider("Stop Loss %",      0.5, 10.0, float(settings.get("stop_loss_pct", 2.0)), 0.25)
        with col_b:
            tp          = st.slider("Take Profit %",    0.5, 20.0, float(settings.get("take_profit_pct", 4.0)), 0.25)
            lev         = st.slider("Leverage",         1,   20,   int(settings.get("leverage", 5)))
            max_trades  = st.slider("Max Open Trades",  1,   20,   int(settings.get("max_trades", 5)))
            interval    = st.slider("Scan Interval (s)", 30, 3600, int(settings.get("scan_interval", 60)), 30)

        st.markdown("**API Keys**")
        bk = st.text_input("Binance API Key",    type="password", placeholder="Leave blank to keep existing")
        bs = st.text_input("Binance API Secret", type="password", placeholder="Leave blank to keep existing")

        st.markdown("**Notifications**")
        n_tg, n_dc, n_em = st.columns(3)
        with n_tg: ntg = st.checkbox("Telegram")
        with n_dc: ndc = st.checkbox("Discord")
        with n_em: nem = st.checkbox("Email")

        if st.form_submit_button("💾 Save Settings", use_container_width=True, type="primary"):
            payload = {
                "simulation_mode": sim_mode, "trading_enabled": trd_enabled,
                "risk_percent": risk_pct, "stop_loss_pct": sl,
                "take_profit_pct": tp, "leverage": lev,
                "max_trades": max_trades, "scan_interval": interval,
                "notify_telegram": ntg, "notify_discord": ndc, "notify_email": nem,
            }
            if bk: payload["binance_api_key"] = bk
            if bs: payload["binance_api_secret"] = bs
            _api("put", "/settings", json=payload)
            st.success("Settings saved!")


def _page_backtest() -> None:
    st.title("📈 Backtest")

    col1, col2, col3 = st.columns(3)
    with col1: symbol   = st.text_input("Symbol",   value="BTCUSDT")
    with col2: interval = st.selectbox("Interval",  ["1m","5m","15m","1h","4h"])
    with col3: limit    = st.slider("Bars",          100, 1000, 500, 50)

    if st.button("▶ Run Backtest", type="primary"):
        with st.spinner("Running backtest…"):
            result = _api("post", "/backtest",
                          json={"symbol": symbol, "interval": interval, "limit": limit})
        if result:
            # Metrics
            cols = st.columns(4)
            cols[0].metric("Net Profit",    f"${result['net_profit']:+,.2f}",
                           f"{result['net_profit_pct']:+.2f}%")
            cols[1].metric("Win Rate",      f"{result['win_rate']:.1f}%")
            cols[2].metric("Max Drawdown",  f"{result['max_drawdown_pct']:.2f}%")
            cols[3].metric("Sharpe",        f"{result['sharpe']:.3f}")

            cols2 = st.columns(4)
            cols2[0].metric("Total Trades",   result['total_trades'])
            cols2[1].metric("Profit Factor",  f"{result['profit_factor']:.3f}")
            cols2[2].metric("Avg Win",        f"${result['avg_win']:+.4f}")
            cols2[3].metric("Avg Loss",       f"${result['avg_loss']:+.4f}")

            # Equity curve
            eq = result.get("equity_curve", [])
            if eq:
                st.subheader("Equity Curve")
                fig = go.Figure(go.Scatter(y=eq, fill="tozeroy",
                                           line=dict(color="#00c8ff")))
                fig.update_layout(template="plotly_dark", height=300,
                                  xaxis_title="Bar", yaxis_title="Equity")
                st.plotly_chart(fig, use_container_width=True)


def _page_alerts() -> None:
    st.title("🕑 Alert History")
    st.info("Alert history is recorded in the database. "
            "In a full deployment, this page pulls from /admin/alerts or a dedicated endpoint.")
    # Placeholder — extend with real endpoint
    st.json({"message": "Connect /admin/alerts endpoint here."})


def _page_admin() -> None:
    st.title("👥 Admin Panel")
    tab1, tab2, tab3 = st.tabs(["Users", "System Logs", "Active Bots"])

    with tab1:
        users = _api("get", "/admin/users") or []
        if users:
            df = pd.DataFrame(users)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No users found.")

    with tab2:
        logs = _api("get", "/admin/logs", params={"limit": 200}) or []
        if logs:
            df = pd.DataFrame(logs)
            level_filter = st.multiselect("Level", df["level"].unique().tolist(),
                                          default=df["level"].unique().tolist())
            st.dataframe(df[df["level"].isin(level_filter)], use_container_width=True)
        else:
            st.info("No logs.")

    with tab3:
        bots = _api("get", "/admin/bots") or []
        if bots:
            st.dataframe(pd.DataFrame(bots), use_container_width=True)
        else:
            st.info("No active bots.")


# ─── Main ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if not st.session_state.get("logged_in"):
        _login_page()
        return

    page = _sidebar()

    if   "Market"    in page: _page_market()
    elif "Portfolio" in page: _page_portfolio()
    elif "Bot"       in page: _page_bot_control()
    elif "Backtest"  in page: _page_backtest()
    elif "Alert"     in page: _page_alerts()
    elif "Admin"     in page: _page_admin()

    # Auto-refresh every 30s
    if st.session_state.get("auto_refresh", False):
        time.sleep(30)
        st.rerun()


if __name__ == "__main__":
    main()
