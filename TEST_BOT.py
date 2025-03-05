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

def init_db():
    conn = psycopg2.connect(
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
    )
    cursor = conn.cursor()

    # Create the tokens table with ATH tracking
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tokens (
            id SERIAL PRIMARY KEY,
            token_name TEXT,
            symbol TEXT,
            market_cap_at_dex_paid INTEGER,
            highest_market_cap INTEGER,
            pair_created_at TIMESTAMP,
            dex_paid_at TIMESTAMP,
            ath_timestamp TIMESTAMP,
            logged_at TIMESTAMP DEFAULT NOW()
        )
    """)

    # Create the price tracking table
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


def retry_request(url, max_retries=3, delay=2):
    """
    Handles retries for API requests in case of temporary failures.
    """
    for attempt in range(max_retries):
        try:
            response = requests.get(url, timeout=5)
            if response.status_code == 200:
                return response.json()
            else:
                print(f"⚠️ API Error {response.status_code}: {response.text}")
        except requests.exceptions.RequestException as e:
            print(f"⚠️ Request failed (Attempt {attempt+1}/{max_retries}): {e}")
        time.sleep(delay)
    return None  # Return None if all retries fail


# Function to get latest token profiles
def get_latest_token_profiles():
    url = f"{DEXSCREENER_API_BASE_URL}/token-profiles/latest/v1"
    return retry_request(url)

def is_dex_paid(chain_id, token_address):
    url = f"{DEXSCREENER_API_BASE_URL}/orders/v1/{chain_id}/{token_address}"
    orders = retry_request(url)

    if not orders or not isinstance(orders, list):  # ✅ Ensures it's a list
        return False, None

    for order in orders:
        if isinstance(order, dict):  # ✅ Ensures each order is a dictionary
            if order.get("type") == "tokenProfile" and order.get("status") == "approved":
                return True, order

    return False, None

def get_token_pairs(chain_id, token_address):
    """
    Fetches available trading pairs for a given token on a specific blockchain.
    """
    url = f"{DEXSCREENER_API_BASE_URL}/token-pairs/v1/{chain_id}/{token_address}"
    data = retry_request(url)

    if not data:
        print(f"❌ No data received for {token_address}")
        return []

    # ✅ Fix: API returns a list, so we extract pairs from each item
    pairs = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and "marketCap" in item:  # Ensure it's a valid dictionary with marketCap
                pairs.append(item)

    if pairs:
        print(f"✅ Found {len(pairs)} pairs for {token_address}: {pairs}")
        return pairs

    print(f"⚠️ No pairs found for {token_address} (API returned empty or unexpected format)")
    return []




def save_token_data(token_data):
    print(f" DEBUG: Attempting to save token: {token_data}")

    try:
        conn = psycopg2.connect(
            dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD, host=DB_HOST, port=DB_PORT
        )
        cursor = conn.cursor()

        # Debugging: Check if DB connection works
        print(" Connected to the database")

        # Check if the token exists in DB
        cursor.execute("SELECT market_cap_at_dex_paid, highest_market_cap FROM tokens WHERE token_name = %s", 
                       (token_data.get("tokenName"),))
        existing_token = cursor.fetchone()

        market_cap = token_data.get("marketCap", 0)
        if not isinstance(market_cap, (int, float)):  # Ensure it's a number
            market_cap = 0

        if existing_token:
            # Extract existing data
            market_cap_at_dex_paid, highest_market_cap = existing_token

            # Update highest market cap if it's greater than the stored value
            new_highest_mc = max(highest_market_cap, market_cap)

            if new_highest_mc > highest_market_cap:
                new_ath_time = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
                print(f"📈 Updating {token_data.get('tokenName')} - New ATH: {new_highest_mc} at {new_ath_time}")

                cursor.execute("""
                    UPDATE tokens 
                    SET highest_market_cap = %s, ath_timestamp = %s 
                    WHERE token_name = %s
                """, (new_highest_mc, new_ath_time, token_data.get("tokenName")))

                conn.commit()

            else:
                print(f" Skipping {token_data.get('tokenName')} - No new highest market cap.")

            return  # Exit function if token already exists

        # If the token is new, insert it
        print(f" Inserting {token_data.get('tokenName')} into DB...")

        cursor.execute("""
            INSERT INTO tokens (token_name, symbol, market_cap_at_dex_paid, highest_market_cap, pair_created_at, dex_paid_at, ath_timestamp)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (
            token_data.get("tokenName"),
            token_data.get("tokenSymbol"),
            market_cap,  # Save Market Cap at DEX Paid time
            market_cap,  # Initial Highest Market Cap = Market Cap at DEX Paid
            token_data.get("pairCreatedAt"),
            token_data.get("dexPaidAt"),
            datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')  # Set ATH timestamp initially
        ))

        conn.commit()
        print(f" ✅ Successfully saved {token_data.get('tokenName')} to the database.")

    except Exception as e:
        print(f" ❌ ERROR: Failed to save token to DB - {e}")

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


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
        else:
            paid, dex_paid_details = False, None  # Ensure paid is always defined

        if paid:
            print(f"  {token_address} is DEX PAID!")

            # Add token to the cache
            already_paid_dex_tokens[token_address] = datetime.now()

            # ⏳ Small delay to allow pairs to appear
            time.sleep(3)  # Wait 3 seconds

            pairs = get_token_pairs(chain_id, token_address)
            if not pairs:
                print(f"  No pairs found for {token_address}, skipping database save.")
                continue  # ✅ Use continue instead of return

            pair_data = pairs[0]  # ✅ Correctly placed outside if block

            # Convert timestamps
            pair_created_at = datetime.fromtimestamp(int(pair_data.get("pairCreatedAt", 0)) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if pair_data.get("pairCreatedAt") else None
            dex_paid_at = datetime.fromtimestamp(int(dex_paid_details.get("paymentTimestamp", 0)) / 1000, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC') if dex_paid_details and dex_paid_details.get("paymentTimestamp") else None
            
            token_name = pair_data.get("baseToken", {}).get("name")
            market_cap = pair_data.get("marketCap", 0)

            print(f"DEBUG: Token Name: {token_name}, Market Cap: {market_cap}, Pair Created: {pair_created_at}, Dex Paid: {dex_paid_at}")

            print(f"DEBUG: Calling save_token_data() for {token_name}")
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
