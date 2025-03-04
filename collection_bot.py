import requests
import csv
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

# Globals
already_paid_dex_tokens = TTLCache(maxsize=500000, ttl=3600)
tokens_scanned = 0
dex_paid_sniped = 0
latest_dex_paid_time = None

# Retry logic to handle API failures
def retry_request(url, retries=3, delay=5):
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"Error fetching data ({attempt+1}/{retries}): {e}")
            time.sleep(delay)
    return None  # Return None if all retries fail

# Function to fetch the latest token profiles
def get_latest_token_profiles():
    url = f"{DEXSCREENER_API_BASE_URL}/token-profiles/latest/v1"
    return retry_request(url)

# Function to check if a token has paid for promotions
def is_dex_paid(chain_id, token_address):
    url = f"{DEXSCREENER_API_BASE_URL}/orders/v1/{chain_id}/{token_address}"
    orders = retry_request(url)
    if not orders:
        return False, None
    for order in orders:
        if order.get("type") == "tokenProfile" and order.get("status") == "approved":
            return True, order
    return False, None

# Function to get token pairs data
def get_token_pairs(chain_id, token_address):
    url = f"{DEXSCREENER_API_BASE_URL}/token-pairs/v1/{chain_id}/{token_address}"
    data = retry_request(url)
    if not data:
        return []
    pairs = data.get("pairs", [])
    for pair in pairs:
        pair["volume24h"] = pair.get("volume", {}).get("h24")  # 24-hour volume
        pair["liquidity"] = pair.get("liquidity", "Unknown")  # Liquidity pool size
        pair["buyers"] = pair.get("txns", {}).get("h24", {}).get("buys", 0)
        pair["sellers"] = pair.get("txns", {}).get("h24", {}).get("sells", 0)
    return pairs

# Function to prevent duplicate token entries and save token data
def save_token_data(token_data):
    try:
        existing_data = pd.read_csv(DATA_FILE)
        if token_data.get("tokenName") in existing_data["Token Name"].values:
            print(f"Skipping {token_data.get('tokenName')} - already logged.")
            return
    except FileNotFoundError:
        pass  # If file doesn’t exist, continue

    with open(DATA_FILE, "a", newline="") as file:
        writer = csv.writer(file)
        writer.writerow([
            token_data.get("tokenName"), token_data.get("tokenSymbol"),
            token_data.get("marketCap", 0),
            token_data.get("pairCreatedAt"), token_data.get("dexPaidAt"),
            datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')
        ])

# Function to track price changes every 1 minute for 6 hours
def track_price_changes(token_address, token_name, duration=6, interval=1):
    headers = ["Token Name", "Token Address", "Timestamp", "Price USD"]
    file_exists = False
    try:
        with open(PRICE_TRACK_FILE, "r") as f:
            file_exists = True
    except FileNotFoundError:
        pass

    total_checks = (duration * 60) // interval  # Convert hours to minute intervals
    for _ in range(total_checks):
        pairs = get_token_pairs(TARGET_CHAIN_ID, token_address)
        price_usd = pairs[0].get("priceUsd") if pairs else "Unknown"
        timestamp = datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')

        with open(PRICE_TRACK_FILE, "a", newline="") as file:
            writer = csv.writer(file)
            if not file_exists:
                writer.writerow(headers)
                file_exists = True
            writer.writerow([token_name, token_address, timestamp, price_usd])

        time.sleep(interval * 60)  # Wait before next check

# Function to inspect token profiles and process them
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
                pair_data = pairs[0]  # Assuming the first pair is the primary one
                token_data = {
                    "tokenName": pair_data.get("baseToken", {}).get("name"),
                    "tokenSymbol": pair_data.get("baseToken", {}).get("symbol"),
                    "marketCap": pair_data.get("marketCap"),
                    "pairCreatedAt": pair_data.get("pairCreatedAt"),
                    "dexPaidAt": dex_paid_details.get("paymentTimestamp")
                }
                save_token_data(token_data)
                threading.Thread(target=track_price_changes, args=(token_address, token_data["tokenName"]), daemon=True).start()
                dex_paid_sniped += 1
                latest_dex_paid_time = datetime.now()