import requests
import csv
import psycopg2
from datetime import datetime, timezone, timedelta
import time
import threading
from cachetools import TTLCache
import pandas as pd
from typing import Optional
import sys
sys.stdout.reconfigure(encoding='utf-8')


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

def get_token_pairs(chain_id, token_address):
    url = f"{DEXSCREENER_API_BASE_URL}/token-pairs/v1/{chain_id}/{token_address}"
    data = retry_request(url)

    if not data:
        print(f"No data received for {token_address}")
        return []

    # ✅ Ensure data is properly formatted (Handles case where response is a list)
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "pairs" in item:
                pairs = item.get("pairs", [])
                if pairs:
                    print(f" Found pairs for {token_address}: {pairs}")  # Debugging log
                    return pairs  #  FIXED: Return pairs if found
        print(f"No pairs found for {token_address}")
        return []

    elif isinstance(data, dict) and "pairs" in data:
        pairs = data.get("pairs", [])
        if pairs:
            print(f" Found pairs for {token_address}: {pairs}")
            return pairs

    print(f"No pairs found for {token_address}")
    return []



def save_token_data(token_data):
    """Inserts token data into the PostgreSQL database while avoiding duplicates."""
    print(f"DEBUG: Attempting to save token: {token_data}")  # ADD DEBUG

    if token_data.get("marketCap", 0) > MAX_MARKET_CAP:
        print(f"Skipping {token_data.get('tokenName')} - Market Cap exceeds 100K")
        return  # Exit function early

    conn = None  # Declare connection outside try block
    cursor = None

    try:
        # Establish DB connection
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
        )
        cursor = conn.cursor()

        # Check if token is already in the database
        cursor.execute("SELECT token_name FROM tokens WHERE token_name = %s", (token_data.get("tokenName"),))
        if cursor.fetchone():
            print(f"Skipping {token_data.get('tokenName')} - already logged.")
            return  # Exit early

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
        print(f"DEBUG: Successfully saved {token_data.get('tokenName')}")  # Confirm success

    except Exception as e:
        print(f"ERROR: Failed to save token - {e}")  # Print SQL errors

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()  # Ensures DB connection is always closed


def track_price_changes(token_address, token_name, duration=6, interval=1):
    headers = ["Token Name", "Token Address", "Timestamp", "Price USD"]
    
    total_checks = (duration * 60) // interval  # Convert hours to minute intervals

    for _ in range(total_checks):
        pairs = get_token_pairs(TARGET_CHAIN_ID, token_address)
        price_usd = pairs[0].get("priceUsd") if pairs else "Unknown"
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

        conn = None
        cursor = None

        try:
            conn = psycopg2.connect(dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT)
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO prices (token_name, token_address, timestamp, price_usd)
                VALUES (%s, %s, %s, %s)
            """, (token_name, token_address, timestamp, price_usd))

            conn.commit()

        except Exception as e:
            print(f"ERROR: Failed to track price for {token_name} - {e}")

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()  # Ensure connection is closed

        time.sleep(interval * 60)  # Wait before next check



def inspect_token_profiles(token_profiles):
    global tokens_scanned, dex_paid_sniped, latest_dex_paid_time
    
    print(f"DEBUG: Inspecting {len(token_profiles)} token profiles...")  # ADDED

    for profile in token_profiles:
        tokens_scanned += 1
        token_address = profile.get("tokenAddress")
        chain_id = profile.get("chainId")

        if not token_address or not chain_id or chain_id != TARGET_CHAIN_ID:
            print(f"DEBUG: Skipping {token_address} (Invalid Chain or Missing Address)")
            continue

        print(f"DEBUG: Checking {token_address} on {chain_id}")  # ADDED

        if token_address not in already_paid_dex_tokens:
            paid, dex_paid_details = is_dex_paid(chain_id, token_address)

            if paid:
                print(f" DEBUG: {token_address} is DEX PAID!")  # ADDED
                already_paid_dex_tokens[token_address] = datetime.now()

                pairs = get_token_pairs(chain_id, token_address)
                if not pairs:
                    print(f" DEBUG: No pairs found for {token_address}")
                    continue
                
                pair_data = pairs[0]

                # Convert timestamps
                pair_created_at = datetime.utcfromtimestamp(int(pair_data.get("pairCreatedAt", 0)) / 1000).strftime('%Y-%m-%d %H:%M:%S UTC') if pair_data.get("pairCreatedAt") else None
                dex_paid_at = datetime.utcfromtimestamp(int(dex_paid_details.get("paymentTimestamp", 0)) / 1000).strftime('%Y-%m-%d %H:%M:%S UTC') if dex_paid_details and dex_paid_details.get("paymentTimestamp") else None
                
                token_name = pair_data.get("baseToken", {}).get("name")
                market_cap = pair_data.get("marketCap", 0)

                print(f"DEBUG: Token Name: {token_name}, Market Cap: {market_cap}, Pair Created: {pair_created_at}, Dex Paid: {dex_paid_at}")

                save_token_data({
                    "tokenName": token_name,
                    "tokenSymbol": pair_data.get("baseToken", {}).get("symbol"),
                    "marketCap": market_cap,
                    "pairCreatedAt": pair_created_at,
                    "dexPaidAt": dex_paid_at
                })

                threading.Thread(target=track_price_changes, args=(token_address, token_name), daemon=True).start()
                
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
