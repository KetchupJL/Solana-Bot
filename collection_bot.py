import requests
import csv
import psycopg2
from datetime import datetime, timezone, timedelta
import time
import threading
from cachetools import TTLCache
import pandas as pd
from typing import Optional

# Constants
PERIOD = 60
WSOL_ADDRESS = 'So11111111111111111111111111111111111111112'
DATA_FILE = "dex_paid_tracked_data.csv"
PRICE_TRACK_FILE = "price_tracking_data.csv"
DEXSCREENER_API_BASE_URL = "https://api.dexscreener.com"
TARGET_CHAIN_ID = "solana"
MAX_MARKET_CAP = 100000  # Filter for tokens under 100K MC

# PostgreSQL Database Credentials
DB_NAME = "solana_bot"
DB_USER = "bot_user"
DB_PASSWORD = "Topdog"
DB_HOST = "localhost"
DB_PORT = "5432"

# Globals
already_paid_dex_tokens = TTLCache(maxsize=500000, ttl=3600)
tokens_scanned = 0
dex_paid_sniped = 0
latest_dex_paid_time = None

# Initialize PostgreSQL Database
def init_db():
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    cursor = conn.cursor()
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id SERIAL PRIMARY KEY,
            token_name TEXT,
            symbol TEXT,
            market_cap INTEGER,
            pair_created_at TIMESTAMP,
            dex_paid_at TIMESTAMP,
            logged_at TIMESTAMP DEFAULT NOW()
        )
    """)
    
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS prices (
            id SERIAL PRIMARY KEY,
            token_name TEXT,
            token_address TEXT,
            timestamp TIMESTAMP,
            price_usd REAL
        )
    """)
    
    conn.commit()
    conn.close()

init_db()

# Retry logic for API requests
def retry_request(url, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching data ({attempt+1}/{retries}): {e}")
            time.sleep(delay)
    return None

# Function to get latest token profiles
def get_latest_token_profiles():
    url = f"{DEXSCREENER_API_BASE_URL}/token-profiles/latest/v1"
    return retry_request(url)

# Function to check if a token has paid for Dex Screener
def is_dex_paid(chain_id, token_address):
    url = f"{DEXSCREENER_API_BASE_URL}/orders/v1/{chain_id}/{token_address}"
    orders = retry_request(url)
    if not orders:
        return False, None
    for order in orders:
        if order.get("type") == "tokenProfile" and order.get("status") == "approved":
            return True, order
    return False, None

# Function to get token pairs
def get_token_pairs(chain_id, token_address):
    url = f"{DEXSCREENER_API_BASE_URL}/token-pairs/v1/{chain_id}/{token_address}"
    data = retry_request(url)
    
    if not data:
        print(f"No data received for {token_address}")
        return []
    
    if isinstance(data, list):
        data = data[0]
    
    if not isinstance(data, dict):
        print(f"Unexpected API response format for {token_address}: {data}")
        return []
    
    pairs = data.get("pairs", [])
    if isinstance(pairs, list) and pairs:
        for pair in pairs:
            pair["volume24h"] = pair.get("volume", {}).get("h24", 0)
            pair["liquidity"] = pair.get("liquidity", {}).get("usd", "Unknown")
            pair["buyers"] = pair.get("txns", {}).get("h24", {}).get("buys", 0)
            pair["sellers"] = pair.get("txns", {}).get("h24", {}).get("sells", 0)
        return pairs
    
    print(f"No pairs found for {token_address}")
    return []

# Function to save token data to PostgreSQL
def save_token_data(token_data):
    """Inserts token data into the PostgreSQL database while avoiding duplicates."""
    if token_data.get("marketCap", 0) > MAX_MARKET_CAP:
        print(f"Skipping {token_data.get('tokenName')} - Market Cap exceeds 100K")
        return

    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    cursor = conn.cursor()

    # Check if token is already in the database
    cursor.execute("SELECT token_name FROM tokens WHERE token_name = %s", (token_data.get("tokenName"),))
    if cursor.fetchone():
        print(f"Skipping {token_data.get('tokenName')} - already logged.")
        conn.close()
        return

    # Insert new token data
    cursor.execute("""
        INSERT INTO tokens (token_name, symbol, market_cap, pair_created_at, dex_paid_at)
        VALUES (%s, %s, %s, %s, %s)
    """, (
        token_data.get("tokenName"),
        token_data.get("tokenSymbol"),
        token_data.get("marketCap", 0),
        token_data.get("pairCreatedAt"),
        token_data.get("dexPaidAt")
    ))

    conn.commit()
    conn.close()
    print(f"Saved token: {token_data.get('tokenName')}")


# Function to track price changes every 1 minute for 6 hours
def track_price_changes(token_address, token_name, duration=6, interval=1):
    """Tracks price changes for a given token and logs them into the PostgreSQL database."""
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    cursor = conn.cursor()

    total_checks = (duration * 60) // interval  # Convert hours to minutes
    for _ in range(total_checks):
        pairs = get_token_pairs(TARGET_CHAIN_ID, token_address)
        price_usd = pairs[0].get("priceUsd") if pairs else None
        timestamp = datetime.utcnow()

        cursor.execute("""
            INSERT INTO prices (token_name, token_address, timestamp, price_usd)
            VALUES (%s, %s, %s, %s)
        """, (token_name, token_address, timestamp, price_usd))

        conn.commit()
        time.sleep(interval * 60)  # Wait before next check

    conn.close()

# Function to inspect token profiles and check if they are Dex Paid
def inspect_token_profiles(token_profiles):
    global tokens_scanned, dex_paid_sniped, latest_dex_paid_time
    for profile in token_profiles:
        tokens_scanned += 1
        token_address = profile.get("tokenAddress")
        chain_id = profile.get("chainId")
        if not token_address or not chain_id or chain_id != TARGET_CHAIN_ID:
            continue
        elif token_address not in already_paid_dex_tokens:
            paid, dex_paid_details = is_dex_paid(chain_id, token_address)
            if paid:
                already_paid_dex_tokens[token_address] = datetime.now()
                pairs = get_token_pairs(chain_id, token_address)
                if not pairs:
                    continue
                pair_data = pairs[0]
                token_data = {
                    "tokenName": pair_data.get("baseToken", {}).get("name"),
                    "tokenSymbol": pair_data.get("baseToken", {}).get("symbol"),
                    "marketCap": pair_data.get("marketCap"),
                    "pairCreatedAt": datetime.utcfromtimestamp(int(pair_data.get("pairCreatedAt", 0)) / 1000).strftime('%Y-%m-%d %H:%M:%S UTC') if pair_data.get("pairCreatedAt") else None,
                    "dexPaidAt": datetime.utcfromtimestamp(int(dex_paid_details.get("paymentTimestamp", 0)) / 1000).strftime('%Y-%m-%d %H:%M:%S UTC') if dex_paid_details and dex_paid_details.get("paymentTimestamp") else None
                }
                save_token_data(token_data)
                threading.Thread(target=track_price_changes, args=(token_address, token_data["tokenName"]), daemon=True).start()
                dex_paid_sniped += 1
                latest_dex_paid_time = datetime.now()

# Main execution loop
def main():
    print("Dex Paid Token Bot Started...")
    while True:
        token_profiles = get_latest_token_profiles()
        if token_profiles:
            inspect_token_profiles(token_profiles)
        time.sleep(1)

if __name__ == "__main__":
    main()
