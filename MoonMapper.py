import os
import re
import json
import base58
import bip_utils
import html
import asyncio
import aiohttp
import random
from datetime import datetime
from bip_utils import Bip39SeedGenerator, Bip44Coins, Bip44, Bip44Changes
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from solders.keypair import Keypair  # type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solana.rpc.api import Client
from raydiumFolder.raydium_py.raydium.amm_v4 import buy, sell

"""---------------------------------"""
"""         Global Variable         """
"""---------------------------------"""

WALLETS_FILE = "users.json"
TOKEN = "" # Paste your TG token
coinType = Bip44Coins.SOLANA


"""-------------------------------"""
"""       Honeypot Detector       """
"""-------------------------------"""

def calculate_risk_percentage(risk_factors):
    total_weight = sum(risk_factors.values())
    max_weight = 100 
    risk_percentage = (total_weight / max_weight) * 100
    return min(risk_percentage, 100)  

def is_honeypot(best_pool):
    txns_24h = best_pool.get('txns', {}).get('h24', {})
    txns_6h = best_pool.get('txns', {}).get('h6', {})
    txns_1h = best_pool.get('txns', {}).get('h1', {})
    buys_24h = txns_24h.get('buys', 0)
    sells_24h = txns_24h.get('sells', 0)
    buys_6h = txns_6h.get('buys', 0)
    sells_6h = txns_6h.get('sells', 0)
    buys_1h = txns_1h.get('buys', 0)
    sells_1h = txns_1h.get('sells', 0)
    price_change_24h = best_pool.get('priceChange', {}).get('h24', 0)
    liquidity_usd = best_pool.get('liquidity', {}).get('usd', 0)
    pair_created_at = best_pool.get('pairCreatedAt', 0) / 1000 
    market_cap = best_pool.get('fdv', 0) 
    info = best_pool.get('info', {})  

    buy_sell_ratio_threshold_24h = 2.2
    buy_sell_ratio_threshold_6h = 2.3
    buy_sell_ratio_threshold_1h = 2.5
    price_change_threshold = 100000
    min_liquidity_threshold = 10000
    very_min_liquidity = 1000
    recent_pair_threshold = 2 * 3600  
    very_recent_pair_threshold = 1 * 3600  
    market_cap_threshold_high = 250000000
    market_cap_threshold_low = 100000000
    price_change_market_cap_threshold = 100000

    risk_factors = {}

    if datetime.now().timestamp() - pair_created_at < very_recent_pair_threshold and market_cap > market_cap_threshold_low:
        risk_factors["The pair was created less than an hour ago and the Market Cap is over 100 million."] = 30

    if datetime.now().timestamp() - pair_created_at < recent_pair_threshold:
        risk_factors["The pair was created recently."] = 5

    if sells_24h == 0 and buys_24h > 0 or (sells_24h > 0 and buys_24h / sells_24h > buy_sell_ratio_threshold_24h):
        risk_factors["The buy/sell ratio in the last 24 hours is abnormally high or there are no sells."] = 20

    if sells_6h == 0 and buys_6h > 0 or (sells_6h > 0 and buys_6h / sells_6h > buy_sell_ratio_threshold_6h):
        risk_factors["The buy/sell ratio in the last 6 hours is abnormally high or there are no sells."] = 15

    if sells_1h == 0 and buys_1h > 0 or (sells_1h > 0 and buys_1h / sells_1h > buy_sell_ratio_threshold_1h):
        risk_factors["The buy/sell ratio in the last hour is abnormally high or there are no sells."] = 10

    if price_change_24h > price_change_threshold:
        risk_factors["The price change in the last 24 hours is abnormally high."] = 15

    if liquidity_usd < min_liquidity_threshold and liquidity_usd > very_min_liquidity:
        risk_factors["Liquidity is low."] = 5

    if liquidity_usd < very_min_liquidity:
        risk_factors["Liquidity is very low."] = 10

    if market_cap > market_cap_threshold_high and price_change_24h > price_change_market_cap_threshold:
        risk_factors["The Market Cap is over 250 million and the price change in the last 24 hours is over 100,000."] = 20

    if market_cap > market_cap_threshold_low and not info:
        risk_factors["The Market Cap is over 100 million and the crypto has no information."] = 25

    if not info:
        risk_factors["The crypto has no information."] = 5

    risk_percentage = calculate_risk_percentage(risk_factors)

    if risk_factors:
        alerts = "\n".join(risk_factors.keys())
        if risk_percentage > 35 and risk_percentage < 70:
            return "⚠️ Warning: This token may be a honeypot, please check out before investing", risk_percentage
        elif risk_percentage >= 70:
            return "⚠️ Alert: This token is a honeypot, please do not invest you might lose your funds", risk_percentage
        elif risk_percentage > 10 and risk_percentage <= 35:
            return "🔎 Warning: This token may involve some risks (volatility, low liquidity,...), invest wisely and stay cautious", risk_percentage
        elif risk_percentage <= 10:
            return "✅ This token is safe", 0
    else:
        return "✅ This token is safe", 0




"""-------------------------------"""
"""           BUY/SELL            """
"""-------------------------------"""
# Format number fonction (e.g. 100 000 = 100K)
def format_number(value): 
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    elif value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    elif value >= 1_000:
        return f"{value / 1_000:.1f}K"
    else:
        return str(value)

# We don't want to have so much "0" if the price is low
def format_price(price): 
    superscript_digits = "⁰¹²³⁴⁵⁶⁷⁸⁹"
    price_str = f"{price:.20f}".rstrip('0') 
    
    match = re.match(r"0\.(0+)([1-9]\d*)", price_str)
    if match:
        zeros_count = len(match.group(1))
        exponent = ''.join(superscript_digits[int(d)] for d in str(zeros_count))  
        return f"0.0{exponent}{match.group(2)[:4]}"
    
    return price_str  


async def wallet_exists_and_has_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    wallets = await load_wallets()
    if not wallets:
        await update.callback_query.message.reply_text("❌ No wallet found for this user.")
        return False

    if str(user_id) not in wallets or "wallets" not in wallets[str(user_id)]:
        await update.callback_query.message.reply_text("❌ No wallet found for this user.")
        return False

    user_wallets = wallets[str(user_id)]["wallets"]
    
    if not user_wallets:
        await update.callback_query.message.reply_text("❌ The wallet is incomplete.")
        return False

    wallet_data = next(iter(user_wallets.values()))
    public_address = wallet_data['public_address']

    balance = await get_solana_balance(public_address)

    if balance > 0:      
        await update.callback_query.message.reply_text("🔑 Please enter the token address you want to trade:")
        context.user_data['awaiting_token_address'] = True 
        return True
    else:
        await update.callback_query.message.reply_text("❌ Wallet has no sufficient balance.")
        return False


async def get_token_data(token_address):
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status != 200:
                print(f"[ERROR] API request failed with status {response.status}")
                return None

            data = await response.json()

            if "pairs" not in data or not isinstance(data["pairs"], list):
                print(f"[ERROR] No 'pairs' data found for token {token_address}")
                return None

            # We take the Raydium pools for now
            raydium_pools = [pair for pair in data["pairs"] if pair.get("dexId") == "raydium"]
            if not raydium_pools:
                print(f"[ERROR] No Raydium pools found for token {token_address}")
                return None

            best_pool = max(raydium_pools, key=lambda p: p.get("liquidity", {}).get("usd", 0))

            return {
                "token_name": best_pool.get("baseToken", {}).get("name", "Unknown"),
                "token_symbol": best_pool.get("baseToken", {}).get("symbol", "Unknown"),
                "price_sol": format_price(float(best_pool.get("priceNative", "N/A"))),
                "price_usd": format_price(float(best_pool.get("priceUsd", "N/A"))),
                "liquidity": format_number(float(best_pool["liquidity"].get("usd", "N/A"))) if "liquidity" in best_pool else "N/A",
                "market_cap": format_number(float(best_pool.get("fdv", "N/A"))),
                "price_change": best_pool.get("priceChange", {}),
                "pair_address": best_pool.get("pairAddress", "N/A"), 
                "best_pool": best_pool  
            }


async def process_token_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text.strip()

    if context.user_data.get('awaiting_token_address'):
        token_address = user_message
        
        token_data = await get_token_data(token_address)
        if not token_data:
            await update.message.reply_text("❌ No valid pool found on Raydium for this token.")
            return

        best_pool = token_data.get("best_pool")  
        pair_address = best_pool.get("pairAddress") or best_pool.get("address") or best_pool.get("id", "N/A")
        context.user_data['pair_address'] = pair_address
        context.user_data['token_symbol'] = token_data['token_symbol']  
        context.user_data.pop('awaiting_token_address', None)
        wallets = await load_wallets()

        if not wallets:
            await update.message.reply_text("❌ No wallets file found or it is corrupted.")
            return

        if str(user_id) not in wallets or "wallets" not in wallets[str(user_id)]:
            await update.message.reply_text("❌ No wallet found for this user.")
            return

        user_wallets = wallets[str(user_id)]["wallets"]
        wallet_data = next(iter(user_wallets.values()))
        public_address = wallet_data['public_address']
        sol_balance = await get_solana_balance(public_address)

        token_mint = token_address 
        token_balance = await get_token_balance(public_address, token_mint)

        honeypot_alert, risk_percentage = is_honeypot(best_pool)

        message = (f"📊 <b>{token_data['token_name']} ({token_data['token_symbol']})</b>\n"
                   f"<code>{pair_address}</code>\n\n"
                   f"💰Price: <b>${token_data['price_usd']}</b> ({token_data['price_sol']} SOL)\n"
                   f"🏛️ Market Cap: <b>${token_data['market_cap']}</b>\n"
                   f"💧 Liquidity: <b>${token_data['liquidity']}</b>\n\n"
                   f"📈 Price Changes:\n"
                   f"• 5min: <b>{token_data['price_change'].get('m5', 'N/A')}%</b> • 1h: <b>{token_data['price_change'].get('h1', 'N/A')}%</b>\n"
                   f"• 6h: <b>{token_data['price_change'].get('h6', 'N/A')}%</b> • 24h: <b>{token_data['price_change'].get('h24', 'N/A')}%</b>\n\n"
                   f"💼 My Wallet:\n"
                   f"| Solana: <b>{sol_balance:.4f} SOL</b>\n"
                   f"| Token: <b>{token_balance:.4f} {token_data['token_symbol']}</b>\n"
                   f"| PnL <b>--</b> 🚀\n"
                   f"| Bought <b>-- SOL</b>\n"
                   f"| Sold <b>-- SOL</b>\n\n"
                   f"------------------------------\n"
                   f"📢 Risk percentage: <b>{risk_percentage:.2f}%</b>\n"
                   f"{honeypot_alert}\n")
                   

        buy_buttons = [
            InlineKeyboardButton("🟢 Buy 0.1 SOL", callback_data=f'buy_0.1_{pair_address}'),
            InlineKeyboardButton("🟢 Buy 0.5 SOL", callback_data=f'buy_0.5_{pair_address}')
        ]

        buy_x_buttons = [
            InlineKeyboardButton("🟢 Buy 1 SOL", callback_data=f'buy_1_{pair_address}'),
            InlineKeyboardButton("🟢 Buy x SOL", callback_data=f'buy_x_{pair_address}')
        ]

        separator = [
            InlineKeyboardButton("-", callback_data='separator')
        ]

        sell_buttons = [
            InlineKeyboardButton("🔴 Sell 25%", callback_data=f'sell_25_{pair_address}'),
            InlineKeyboardButton("🔴 Sell 50%", callback_data=f'sell_50_{pair_address}')
        ]

        sell_x_buttons = [
            InlineKeyboardButton("🔴 Sell 100%", callback_data=f'sell_100_{pair_address}'),
            InlineKeyboardButton("🔴 Sell x%", callback_data=f'sell_x_{pair_address}')
        ]

        back_button = [InlineKeyboardButton("← Back", callback_data='back_to_main')]

        keyboard = [
            buy_buttons,
            buy_x_buttons,
            separator,
            sell_buttons,
            sell_x_buttons,
            back_button
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(message, reply_markup=reply_markup, parse_mode="HTML")
    
    else:
        await update.message.reply_text("❓ I didn't ask for a token address. Use /start to begin.")


async def handle_buy_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("buy_"):
        parts = data.split('_')
        amount = parts[1]  # We take the amount of the button (0.1, 0.5, 1, or "x")
        pair_address = parts[2]  # We take the pair adress, has the sell/buy functions take the pool informations to execute 

        context.user_data['pair_address'] = pair_address
        context.user_data['action'] = 'buy'

        if amount == 'x':
            context.user_data['awaiting_amount'] = True
            await query.message.reply_text("How much SOL do you want to spend ? (Enter a number):")
        else:
            context.user_data['sol_amount'] = float(amount)
            await execute_buy(update, context)

    elif data.startswith("sell_"):
        parts = data.split('_')
        percentage = parts[1]  # We take the amount of the button (25, 50, 100, or "x")
        pair_address = parts[2] 

        context.user_data['pair_address'] = pair_address
        context.user_data['action'] = 'sell'

        if percentage == 'x':
            context.user_data['awaiting_amount'] = True
            await query.message.reply_text("What percentage of your tokens do you want to sell ? (Enter a number between 1 and 100):")
        else:
            context.user_data['sell_percentage'] = float(percentage)
            await execute_sell(update, context)


async def process_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_message = update.message.text.strip()

    # We need to have this condition because process_amount need to know if he wait for a slippage or an amount
    if context.user_data.get('awaiting_slippage'):
        try:
            slippage = float(user_message)
            if slippage < 0 or slippage > 100:
                await update.message.reply_text("❌ Slippage must be between 0 and 100.")
                return
            
            wallets = await load_wallets()
            if str(user_id) in wallets and 'settings' in wallets[str(user_id)]:
                wallets[str(user_id)]['settings']['slippage'] = slippage

                await save_wallets(wallets)

                await update.message.reply_text(f"✅ Slippage set to {slippage}%")
            else:
                await update.message.reply_text("❌ User settings not found.")

            context.user_data.pop('awaiting_slippage', None) 
        except ValueError:
            await update.message.reply_text("❌ Invalid input. Please enter a number.")
        return

    # As well has the referral code input
    if context.user_data.get('awaiting_referral_code'):
        await process_referral_code(update, context)
        return

    # If it's an amount to buy/sell
    if context.user_data.get('awaiting_amount'):
        try:
            amount = float(user_message)
            action = context.user_data.get('action')

            if action == 'buy':
                if amount <= 0:
                    await update.message.reply_text("❌ Please enter a positive amount of SOL.")
                    return

                context.user_data['sol_amount'] = amount
                context.user_data.pop('awaiting_amount', None)  
                await execute_buy(update, context)

            elif action == 'sell':
                if amount < 1 or amount > 100:
                    await update.message.reply_text("❌ Please enter a percentage between 1 and 100.")
                    return

                context.user_data['sell_percentage'] = amount
                context.user_data.pop('awaiting_amount', None)  
                await execute_sell(update, context)

        except ValueError:
            await update.message.reply_text("❌ Invalid input. Please enter a number.")
    else:
        await update.message.reply_text("❓ I didn't ask for an amount. Use /start to begin.")


async def execute_buy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pair_address = context.user_data.get('pair_address')
    sol_amount = context.user_data.get('sol_amount')
    token_symbol = context.user_data.get('token_symbol', 'Unknown')

    wallets = await load_wallets()
    if not wallets:
        if update.message:
            await update.message.reply_text("❌ No wallets file found or it is corrupted.")
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if str(user_id) not in wallets or "wallets" not in wallets[str(user_id)]:
        if update.message:
            await update.message.reply_text("❌ No wallet found for this user.")
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text("❌ No wallet found for this user.")
        return

    user_wallets = wallets[str(user_id)]["wallets"]
    wallet_data = next(iter(user_wallets.values()))
    private_key = wallet_data['private_key']
    slippage = wallets[str(user_id)].get('settings', {}).get('slippage', 2)
    
    # Buy 
    try:
        result, amount_out, txn_sig = buy(pair_address, private_key, sol_amount, slippage)  
        if result == True:
            message = (
                f"✅ Buy order executed successfully!\n\n"
                f"Amount In: <b>-{sol_amount} SOL</b>\n"
                f"Amount Out: <b>+{amount_out:.2f} {token_symbol}</b>\n\n" 
                f"<a href='https://solscan.io/tx/{txn_sig}'>View Transaction</a>"
            )
            if update.message:
                sent_message = await update.message.reply_text(message, parse_mode="HTML")
            elif update.callback_query and update.callback_query.message:
                sent_message = await update.callback_query.message.reply_text(message, parse_mode="HTML")
        
        else:
            error_message = "❌ Buy order failed. Please increase the slippage or check your wallet balance and try again."
            if update.message:
                sent_message = await update.message.reply_text(error_message)
            elif update.callback_query and update.callback_query.message:
                sent_message = await update.callback_query.message.reply_text(error_message)

        await asyncio.sleep(10)
        await sent_message.delete()

    except Exception as e:
        error_message = f"❌ Error executing buy order: {e}"
        if update.message:
            await update.message.reply_text(error_message)
        elif update.callback_query and update.callback_query.message:
            await update.callback_query.message.reply_text(error_message)


async def execute_sell(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    pair_address = context.user_data.get('pair_address')
    sell_percentage = context.user_data.get('sell_percentage')
    token_symbol = context.user_data.get('token_symbol', 'Unknown')

    wallets = await load_wallets()
    if not wallets:
        await update.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if str(user_id) not in wallets or "wallets" not in wallets[str(user_id)]:
        await update.message.reply_text("❌ No wallet found for this user.")
        return
    
    user_wallets = wallets[str(user_id)]["wallets"]
    wallet_data = next(iter(user_wallets.values()))
    private_key = wallet_data['private_key']
    slippage = wallets[str(user_id)].get('settings', {}).get('slippage', 2)

    # Sell
    try:
        result,token_balance, amount_out, txn_sig = sell(pair_address, private_key, sell_percentage, slippage)

        if result == True: 
            if 'settings' in wallets[str(user_id)]:
                wallets[str(user_id)]['settings']['trades'] = wallets[str(user_id)]['settings'].get('trades', 0) + 1
                
                # We give the user a random reward (cashback), the more he trade the more he earn
                base_reward = random.uniform(0.0001, 0.0005)  
                reward_increment = (sell_percentage / 100) * base_reward  
                reward_increment = round(reward_increment, 4) 
                wallets[str(user_id)]['settings']['reward'] = wallets[str(user_id)]['settings'].get('reward', 0) + reward_increment

            await save_wallets(wallets)

            if update.message:
                sent_message = await update.message.reply_text(
                    f"✅ Sell order executed successfully!\n\n"
                    f"Amount In: <b>-{token_balance:.2f} {token_symbol}</b>\n"
                    f"Amount Out: <b>+{amount_out:.4f} SOL</b>\n\n" 
                    f"🎁 You earned a reward of {reward_increment:.4f} SOL\n"
                    f"<a href='https://solscan.io/tx/{txn_sig}'>View Transaction</a>",
                    parse_mode="HTML"
                )
            else:
                sent_message = await update.callback_query.message.reply_text(
                    f"✅ Sell order executed successfully!\n\n"
                    f"Amount In: <b>-{token_balance:.2f} {token_symbol}</b>\n"
                    f"Amount Out: <b>+{amount_out:.4f} SOL</b>\n\n" 
                    f"🎁 You earned a reward of {reward_increment:.4f} SOL\n"
                    f"<a href='https://solscan.io/tx/{txn_sig}'>View Transaction</a>",
                    parse_mode="HTML"
                )
            
            await asyncio.sleep(10)
            await sent_message.delete()

        else: 
            if update.message:
                sent_message = await update.message.reply_text("❌ Sell order failed. Please increase the slippage or check your wallet balance and try again.")
            else:
                sent_message = await update.callback_query.message.reply_text("❌ Sell order failed. Please increase the slippage or check your wallet balance and try again.")
            await asyncio.sleep(5)
            await sent_message.delete()

    except Exception as e:
        print(f"[ERROR] Error executing sell order: {e}")
        if update.message:
            await update.message.reply_text(f"❌ Error executing sell order: {e}")
        else:
            await update.callback_query.message.reply_text(f"❌ Error executing sell order: {e}")


"""-------------------------------"""
"""      Get Wallet Balance       """
"""-------------------------------"""

async def get_solana_balance(public_address):
    try:
        decoded_bytes = base58.b58decode(public_address)
        pubkey = Pubkey(decoded_bytes)
    except Exception as e:
        print(f"Error decoding public address: {e}")
        return 0

    client = Client("https://api.mainnet-beta.solana.com")
    try:
        balance_response = client.get_balance(pubkey)
        balance_lamports = balance_response.value
    except Exception as e:
        print(f"Error fetching balance: {e}")
        return 0
    balance_sol = balance_lamports / 1e9
    return balance_sol


async def get_sol_price():
    url = "https://api.coingecko.com/api/v3/simple/price"
    params = {
        'ids': 'solana',
        'vs_currencies': 'usd'
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as response:
            if response.status == 200:
                data = await response.json()
                return data['solana']['usd']
            else:
                print(f"Error fetching SOL price: {response.status}")
                return 0


async def get_token_balance(public_address, token_mint):
    url = "https://api.mainnet-beta.solana.com"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            public_address,
            {
                "mint": token_mint 
            },
            {
                "encoding": "jsonParsed"
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status == 200:
                data = await response.json()

                for account in data.get("result", {}).get("value", []):
                    token_info = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    balance = int(token_info.get("tokenAmount", {}).get("amount", 0))
                    decimals = int(token_info.get("tokenAmount", {}).get("decimals", 0))
                    balance_normalized = balance / (10 ** decimals)
                    return balance_normalized

                return 0  
            else:
                print(f"Error fetching token balance: {response.status}")
                return 0


"""-------------------------------"""
"""        Wallet Action          """
"""-------------------------------"""

async def display_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()  
        message = query.message  
    else:
        message = update.message  

    user_id = update.effective_user.id  
    wallets = await load_wallets()

    if not wallets:
        message_text = "⚠️ <b>You don't have any wallets!</b>"
    else:
        user_wallets = wallets.get(str(user_id), {}).get("wallets", {})

        if not user_wallets:
            message_text = "⚠️ <b>You don't have any wallets!</b>"
        else:
            sol_price = await get_sol_price()
            wallets_list = "\n".join(
                [f"<code>{html.escape(data['public_address'])}</code> (<i>Tap to copy</i>)\n"
                 f"Balance: <b>{await get_solana_balance(data['public_address']):.4f} SOL (${await get_solana_balance(data['public_address']) * sol_price:.2f})</b>\n\n"
                 for wallet_type, data in user_wallets.items()]
            )
            message_text = f"<b>Your Wallets</b>\n\n{wallets_list}"

    keyboard_wallet = [
        [
            InlineKeyboardButton(f"🔑 {data['public_address']}", callback_data=f'getPrivate_{wallet_type}')
            for wallet_type, data in user_wallets.items()
        ],
        [
            InlineKeyboardButton("🆕 Create a Wallet", callback_data='create_wallet'),
            InlineKeyboardButton("➕ Import Wallet", callback_data='import_wallet')
        ],
        [InlineKeyboardButton("🗑️ Delete Wallet", callback_data='delete_wallet')],
        [InlineKeyboardButton("← Back", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard_wallet)

    if update.callback_query:
        await query.message.edit_text(message_text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await message.reply_text(message_text, reply_markup=reply_markup, parse_mode="HTML")


async def wallet_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("getPrivate_"):
        wallet_type = data.replace("getPrivate_", "")  
        await send_private_key(update, context, wallet_type)

    if query.data == 'create_wallet':
        await create_wallet(update, context)
    elif query.data == 'import_wallet':
        await import_wallet(update, context)
    elif query.data == 'delete_wallet':
        await delete_wallet(update, context)
    elif query.data == 'back_to_main':
        await start(update, context)


"""------------------------------"""
"""      Save Wallet.json        """
"""------------------------------"""

async def save_wallet_to_file(user_id: int, public_address: str, private_key: str, mnemonic: str = "", wallet_type: str = "created"):
    wallets = await load_wallets()
    if not wallets:
        return False

    if str(user_id) not in wallets:
        wallets[str(user_id)] = {"wallets": {}, "settings": {}}

    if wallet_type in wallets[str(user_id)]["wallets"] and wallets[str(user_id)]["wallets"][wallet_type]:
        return False 

    wallets[str(user_id)]["wallets"][wallet_type] = {
        "public_address": public_address,
        "private_key": private_key,
        "mnemonic": mnemonic
    }

    await save_wallets(wallets)
    return True


"""------------------------------"""
"""        Create Wallet         """
"""------------------------------"""

async def create_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    wallets = await load_wallets()
    if not wallets:
        sent_message = await query.message.reply_text("❌ No wallets file found or it is corrupted.")
        await asyncio.sleep(3)
        await sent_message.delete()
        return

    if str(user_id) in wallets and "created" in wallets[str(user_id)] and wallets[str(user_id)]["created"]:
        sent_message = await query.message.reply_text("❌ You already have a created wallet. You cannot create another one.")
        await asyncio.sleep(3)
        await sent_message.delete()
        return

    account = Keypair()
    public_key = str(account.pubkey())
    private_key = base58.b58encode(account.secret() + base58.b58decode(public_key)).decode('utf-8')

    if await save_wallet_to_file(user_id, public_key, private_key, "", "created"):
        sent_message = await query.message.reply_text(f"✅ Wallet created!\n\nAddress: {public_key}", parse_mode="Markdown")
        await asyncio.sleep(3)
        await sent_message.delete()
        await display_wallet(update, context)

"""------------------------------------"""
"""      Mnemonic Keypair Function     """
"""------------------------------------"""

async def generateKeysFromMnemonic(user_id: int, mnemonic: str):
    seed = Bip39SeedGenerator(mnemonic).Generate()

    if coinType == Bip44Coins.SOLANA:
        masterKey = Bip44.FromSeed(seed, coinType)
        accountKey = masterKey.Purpose().Coin().Account(0)
        changeKey = accountKey.Change(Bip44Changes.CHAIN_EXT)
        privKeyBytes = changeKey.PrivateKey().Raw().ToBytes()
        pubAddrBytes = changeKey.PublicKey().RawCompressed().ToBytes()[1:]
        key_pair = privKeyBytes + pubAddrBytes

        if await save_wallet_to_file(user_id, changeKey.PublicKey().ToAddress(), bip_utils.base58.Base58Encoder.Encode(key_pair), mnemonic, "imported"):
            return changeKey.PublicKey().ToAddress()
        else:
            return "EXISTS"
    
    return ""


"""------------------------------------"""
"""       Import Mnemonic Wallet       """
"""------------------------------------"""

async def import_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id

    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("<b>❌ No wallets file found or it is corrupted.</b>", parse_mode="HTML")
        return

    if str(user_id) in wallets and "imported" in wallets[str(user_id)] and wallets[str(user_id)]["imported"]:
        await query.message.reply_text("<b>❌ You already have an imported wallet. You cannot import another one.</b>", parse_mode="HTML")
        return

    await query.answer()
    context.user_data['awaiting_mnemonic'] = True
    await query.message.reply_text("<b>Please provide your 12 or 24 recovery words:</b>", parse_mode="HTML")

async def process_mnemonic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if context.user_data.get('awaiting_mnemonic'):
        mnemonic = update.message.text.strip()

        if len(mnemonic.split()) not in [12, 24]:
            await update.message.reply_text("<b>❌ Invalid mnemonic! It must contain 12 or 24 words. Please try again.</b>", parse_mode="HTML")
            return
            
        public_address = await generateKeysFromMnemonic(user_id, mnemonic)

        if public_address == "EXISTS":
            await update.message.reply_text("<b>❌ You already have an imported wallet. You cannot import another one.</b>", parse_mode="HTML")
        elif public_address:
            sent_message = await update.message.reply_text(f"<b>✅ Wallet imported!</b>\n<b>Address:</b> <code>{public_address}</code>", parse_mode="HTML")
            await asyncio.sleep(3)
            await sent_message.delete()
        else:
            await update.message.reply_text("<b>❌ Error importing wallet.</b>", parse_mode="HTML")

        context.user_data['awaiting_mnemonic'] = False
        await display_wallet(update, context)
    else:
        await update.message.reply_text("<b>❓ I didn't ask for any mnemonic. Use /start to begin.</b>", parse_mode="HTML")


"""-----------------------------------"""
"""       Delete Current Wallet       """
"""-----------------------------------"""

async def delete_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    await query.answer()
    
    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("⚠️ <b>No saved wallet found.</b>", parse_mode="HTML")
        return
    
    if str(user_id) in wallets:
        user_wallets = wallets[str(user_id)]["wallets"]
        keyboard = [[InlineKeyboardButton(f"💳 {html.escape(data['public_address'])}", callback_data=f'delete_{wallet_type}')]
                for wallet_type, data in user_wallets.items()]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.message.reply_text("<b>Select a wallet to delete:</b>\n\n⚠️ <b>Make sure to save your private key before</b>, as recovery is not possible.", reply_markup=reply_markup, parse_mode="HTML")
    else:
        await query.message.reply_text("⚠️ <b>No saved wallet found.</b>", parse_mode="HTML")


async def confirm_delete_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = update.effective_user.id
    wallet_type = query.data.replace('delete_', '')
    await query.answer()
    
    wallets = await load_wallets()
    if not wallets:
        sent_message = await query.message.reply_text("⚠️ <b>Wallet not found or already deleted</b>", parse_mode="HTML")
        await asyncio.sleep(3)
        await sent_message.delete()
        return

    if str(user_id) in wallets and wallet_type in wallets[str(user_id)]["wallets"]:
        del wallets[str(user_id)]["wallets"][wallet_type]
        await save_wallets(wallets)
            
        sent_message = await query.message.reply_text(
            f"✅ <b>{wallet_type.capitalize()} wallet deleted.</b>\n", parse_mode="HTML")
        await asyncio.sleep(3)
        await sent_message.delete()
        await display_wallet(update, context)
    else:
        sent_message = await query.message.reply_text("⚠️ <b>Wallet not found or already deleted</b>", parse_mode="HTML")
        await asyncio.sleep(3)
        await sent_message.delete()


"""------------------------------"""
"""       Get Private Key        """
"""------------------------------"""

async def send_private_key(update: Update, context: ContextTypes.DEFAULT_TYPE, wallet_type: str):
    query = update.callback_query
    user_id = str(update.effective_user.id)

    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ Wallet not found!", parse_mode="HTML")
        return

    user_wallets = wallets.get(user_id, {}).get("wallets", {})

    if wallet_type in user_wallets:
        private_key = user_wallets[wallet_type].get("private_key", "🔒 No private key found!")
        public_key = user_wallets[wallet_type].get("public_address", "🔒 No public address found!")
        message = (f"<b>Your Private Key</b> for <b>{public_key}</b>\n\n<code>{private_key}</code>\n(<i>Tap to copy</i>)\n\n"
                   f"⚠️ <b>Important:</b> Never share this key with anyone. "
                    f"Store it securely, as it grants full access to your wallet!\n\n"
                    f"🕒 <i>This message will be deleted in 10 seconds.</i>"
                    )
        sent_message = await query.message.reply_text(message, parse_mode="HTML")
        await asyncio.sleep(10)
        await sent_message.delete()
    else:
        await query.message.reply_text("❌ Wallet not found!", parse_mode="HTML")


"""-------------------------------"""
"""         Assets Menu           """
"""-------------------------------"""

async def assets_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)

    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if user_id not in wallets or "wallets" not in wallets[user_id]:
        await query.message.reply_text("❌ No wallet found for this user.")
        return

    user_wallets = wallets[user_id]["wallets"]
    message = "<b>Your Assets</b>\n\n"

    for wallet_type, wallet_data in user_wallets.items():
        public_address = wallet_data['public_address']
        sol_balance = await get_solana_balance(public_address)
        message += f"<b>Wallet Address:</b> <code>{public_address}</code> (<i>Tap to copy</i>)\n\n"
        message += f"💰 <b>SOL Balance:</b> {sol_balance:.4f} SOL\n\n"
        tokens = await get_all_token_balances(public_address)
        if tokens:
            message += "<b>Tokens:</b>\n"
            for token, balance in tokens.items():
                message += f" <code>{token}</code>\nAmount: <b>{balance:.2f}</b>\n"
        else:
            message += "🪙 <b>Tokens:</b> No tokens found.\n"

        message += "\n"

    keyboard = [
        [
            InlineKeyboardButton("← Back", callback_data='back_to_main'),
            InlineKeyboardButton("🔄 Refresh", callback_data='refresh_assets')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.message.edit_text(message, reply_markup=reply_markup, parse_mode="HTML")

async def button_assets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'refresh_assets':
        await assets_menu(update, context)  
    elif query.data == 'back_to_main':
        await start(update, context)  

async def get_all_token_balances(public_address):
    url = "https://api.mainnet-beta.solana.com"
    headers = {
        "Content-Type": "application/json"
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTokenAccountsByOwner",
        "params": [
            public_address,
            {
                "programId": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"  
            },
            {
                "encoding": "jsonParsed"
            }
        ]
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(url, headers=headers, json=payload) as response:
            if response.status == 200:
                data = await response.json()

                tokens = {}
                for account in data.get("result", {}).get("value", []):
                    token_info = account.get("account", {}).get("data", {}).get("parsed", {}).get("info", {})
                    mint = token_info.get("mint", "Unknown")
                    balance = int(token_info.get("tokenAmount", {}).get("amount", 0))
                    decimals = int(token_info.get("tokenAmount", {}).get("decimals", 0))
                    balance_normalized = balance / (10 ** decimals)
                    tokens[mint] = balance_normalized

                return tokens
            else:
                print(f"Error fetching token balances: {response.status}")
                return {}


"""------------------------------"""
"""        Setting Menu          """
"""------------------------------"""

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if user_id not in wallets or "wallets" not in wallets[user_id] or "settings" not in wallets[user_id]:
        await query.message.reply_text("❌ No wallet or settings found for this user.")
        return

    user_wallets = wallets[user_id]["wallets"]
    user_settings = wallets[user_id]["settings"]

    if not user_wallets:
        public_address = "No wallet found for this user"
        sol_balance = 0.0
    else:
        wallet_data = next(iter(user_wallets.values()))
        public_address = wallet_data['public_address']
        sol_balance = await get_solana_balance(public_address)

    sol_price = await get_sol_price()
    usd_balance = sol_balance * sol_price
    auto_slippage_status = user_settings.get('auto_slippage', 'disabled')
    slippage_display = "Auto Slippage Enabled" if auto_slippage_status == "enabled" else f"{user_settings.get('slippage', 2)}%"
    pro_version = user_settings.get('pro_version', False)
    user_pack = "Pro" if pro_version else "Free"
    reward_sol = user_settings.get('reward', 0)
    reward_usd = reward_sol * sol_price
    trades_count = user_settings.get('trades', 0)
    referral_count = user_settings.get('referral', 0)
    referral_code = user_settings.get('referral_code', user_id)

    message = (
        f"<b>Settings</b>\n\n"
        f"📦 Bot Version: <b>{user_pack}</b>\n\n"
        f"<b>Wallet Address:</b> <code>{public_address}</code>\n\n"
        f"Balance: <b>{sol_balance:.4f}</b> SOL (${usd_balance:.2f})\n"
        f"Slippage: <b>{slippage_display}</b>\n"
        f"Total trades: <b>{trades_count}</b>\n\n"
        f"🔗 You Referral Code: <code>{referral_code}</code>\n"
        f"👥 Affiliated Friends: <b>{referral_count}</b>\n\n"
        f"🎁 Total claimable: <b>{reward_sol:.4f}</b> SOL (${reward_usd:.2f})\n\n"
        f"Use the buttons below to configure your settings:"
    )

    keyboard = [
        [
            InlineKeyboardButton("----- Custom Slippage -----", callback_data='notbutton1')
        ],
        [
            InlineKeyboardButton("✏️ Set Slippage", callback_data='set_slippage'),
            InlineKeyboardButton("🔄 Auto Slippage", callback_data='auto_slippage')
        ],
        [
            InlineKeyboardButton("----- Referral-----", callback_data='notbutton2')
        ],
        [
            InlineKeyboardButton("👥 Referral code", callback_data='referral'),
            InlineKeyboardButton("🎁 Claim SOL", callback_data='claim_sol')
        ],
        [
            InlineKeyboardButton("💸 Gift code", callback_data='gift')
        ],
        [
            InlineKeyboardButton("----- Info -----", callback_data='notbutton3')
        ],
        [
            InlineKeyboardButton("🔜 Demo Mode", callback_data='demo_mode'),
            InlineKeyboardButton("🛟 Help", callback_data='help')

        ],
        [
            InlineKeyboardButton("← Back", callback_data='back_to_main')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(message, reply_markup=reply_markup, parse_mode="HTML")
    

async def settings_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'set_slippage':
        await set_slippage(update, context)
    elif query.data == 'auto_slippage':
        await auto_slippage(update, context) 
    elif query.data == 'referral':
        await referral(update, context)
    elif query.data == 'claim_sol':
        sent_message = await query.message.reply_text("You can withdraw your SOL starting from $25. If you want to boost your rewards, invite friends with your referral code 👥 or make trades 📈 using our Bot!")
        await asyncio.sleep(7)
        await sent_message.delete()
    elif query.data == 'gift':
        sent_message = await query.message.reply_text("There are no codes available at the moment. Please try again later.")
        await asyncio.sleep(5)
        await sent_message.delete()
    elif query.data == 'demo_mode':
        sent_message = await query.message.reply_text("Demo Mode is currently under development and will be available in the next update.")
        await asyncio.sleep(5)
        await sent_message.delete()
    elif query.data == 'help':
        sent_message = await query.message.reply_text("Here you can configure your bot settings to suit your trading preferences.\n\n✏️ <b>Set Slippage</b>: Adjust the slippage tolerance for your trades. Slippage is the difference between the expected price and the actual execution price.\n\n🔄 <b>Auto Slippage</b>: Enable or disable automatic slippage adjustment based on market conditions.\n\n👥 <b>Referral code</b>: Enter your friend referral code to earn a reward.\n\n🎁 <b>Claim SOL</b>: Earn Solana based on the number of trades you make with MoonBot and the people you refer.\n\n💸 <b>Gift code</b>: Enter the gift code given to you by the MoonBot team to claim your rewards.\n\n🔜 <b>Demo Mode</b>: Switch to demo mode to practice trading without risking real funds.", parse_mode="HTML")
        await asyncio.sleep(25)
        await sent_message.delete()
    elif query.data == 'back_to_main':
        await start(update, context)

async def set_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if user_id in wallets and 'settings' in wallets[user_id]:
        wallets[user_id]['settings']['auto_slippage'] = 'disabled'

    await save_wallets(wallets)
    context.user_data['awaiting_slippage'] = True
    await query.message.reply_text("Please enter the new slippage percentage (e.g., 2 for 2%):")
    
async def process_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    user_message = update.message.text.strip()

    if context.user_data.get('awaiting_slippage'):
        try:
            slippage = float(user_message)
            if slippage < 0 or slippage > 100:
                await update.message.reply_text("❌ Slippage must be between 0 and 100.")
                return

            wallets = await load_wallets()
            if user_id in wallets and 'settings' in wallets[user_id]:
                wallets[user_id]['settings']['slippage'] = slippage
                wallets[user_id]['settings']['auto_slippage'] = 'disabled'  

                await save_wallets(wallets)

                await update.message.reply_text(f"✅ Slippage set to {slippage}%")
                await settings_menu(update, context) 
            else:
                await update.message.reply_text("❌ User settings not found.")

            context.user_data.pop('awaiting_slippage', None)  
        except ValueError:
            await update.message.reply_text("❌ Invalid input. Please enter a number.")
    else:
        await update.message.reply_text("❓ I didn't ask for a slippage value. Use /start to begin.")

async def auto_slippage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return
    
    if user_id in wallets and 'settings' in wallets[user_id]:
        if wallets[user_id]['settings']['auto_slippage'] == "disabled":
            wallets[user_id]['settings']['auto_slippage'] = "enabled"
            wallets[user_id]['settings']['slippage'] = 3.0  # For the moment it's not auto, we put the default slippage to 3
            message = "✅ Auto Slippage enabled"
        else:
            wallets[user_id]['settings']['auto_slippage'] = "disabled"
            message = "✅ Auto Slippage disabled"

        await save_wallets(wallets)
        await query.message.reply_text(message)
        await settings_menu(update, context)
    else:
        await query.message.reply_text("❌ User settings not found.")

async def referral(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return
    
    if user_id in wallets and "settings" in wallets[user_id] and "friend_code" in wallets[user_id]["settings"]:
        sent_message = await query.message.reply_text("❌ You have already entered a referral code.")
        await asyncio.sleep(5)
        await sent_message.delete()
        return
    
    await query.message.reply_text("Please enter your friend's referral code:") # The referral code is based on the Telegram user code
    context.user_data['awaiting_referral_code'] = True  


async def process_referral_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    referral_code = update.message.text.strip()
    wallets = await load_wallets()
    if not wallets:
        await update.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if referral_code == user_id:
        sent_message = await update.message.reply_text("❌ You cannot use your own referral code.")
        await asyncio.sleep(5)
        await sent_message.delete()
        return
    
    if user_id in wallets and "settings" in wallets[user_id] and "friend_code" in wallets[user_id]["settings"]:
        sent_message = await update.message.reply_text("❌ You have already entered a referral code.")
        await asyncio.sleep(5)
        await sent_message.delete()
        return

    if referral_code not in wallets:
        sent_message = await update.message.reply_text("❌ Invalid referral code. Please check the code and try again.")
        await asyncio.sleep(5)
        await sent_message.delete()
        return
    
    wallets[user_id]["settings"]["friend_code"] = referral_code
    if "referral" in wallets[referral_code]["settings"]:
        wallets[referral_code]["settings"]["referral"] += 1
    else:
        wallets[referral_code]["settings"]["referral"] = 1

    await save_wallets(wallets)
    sent_message = await update.message.reply_text(f"✅ Referral code {referral_code} successfully added!")
    await asyncio.sleep(5)
    await sent_message.delete()
    context.user_data.pop('awaiting_referral_code', None) 

    await settings_menu(update, context)

"""------------------------------"""
"""      Starting the bot        """
"""------------------------------"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        message = query.message
    else:
        message = update.message

    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        wallets = {}

    if user_id not in wallets:
        wallets[user_id] = {"wallets": {}}
    
    # Default parameters for all users
    if 'settings' not in wallets[user_id]:
        wallets[user_id]['settings'] = {
            'slippage': 2,  
            'auto_slippage': 'disabled',
            'language': 'en',  
            'referral_code': user_id,  
            'pro_version': False,
            'reward': 0,
            'trades': 0,
            'referral':0
        }

        await save_wallets(wallets)

    image_path = os.path.join(os.getcwd(), "images", "image.jpeg") 
    intro_message = """
<b>Welcome to YourBotName. The best trading bot on Solana! 🌙</b>

To start making trades, fund your wallet: /wallet

Join our Telegram group @YourBotName and follow us on <a href="YourXlink">Twitter</a>
    """
    await message.reply_photo(photo=open(image_path, 'rb'), caption=intro_message, parse_mode="HTML")

    keyboard_start = [
        [
            InlineKeyboardButton("🚀 Buy/Sell", callback_data='buyorsell'),
            InlineKeyboardButton("💳 Wallet", callback_data='wallet')
        ],
        [
            InlineKeyboardButton("💰 Assets", callback_data='assets'),
            InlineKeyboardButton("📝 History", callback_data='history')
        ],
        [
            InlineKeyboardButton("🔫 Sniper", callback_data='sniper'),
            InlineKeyboardButton("🎯 Copy Trade", callback_data='copytrade')
        ],
        [
            InlineKeyboardButton("🤖 AI Trading", callback_data='aitrading')
        ],
        [
            InlineKeyboardButton("📡 MoonBot Pro", callback_data='moonbotpro')
        ],
        [
            InlineKeyboardButton("🌍 Languages", callback_data='languages'),
            InlineKeyboardButton("⚙️ Settings", callback_data='settings')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard_start)

    await message.reply_text("<b>---Choose an option below 👇---</b>", reply_markup=reply_markup, parse_mode="HTML")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        await query.message.reply_text("❌ No data file found or it is corrupted.")
        return

    if user_id not in wallets or "settings" not in wallets[user_id]:
        await query.message.reply_text("❌ No settings found for this user.")
        return

    user_settings = wallets[user_id]["settings"]
    pro_version = user_settings.get('pro_version', False)

    if query.data == 'wallet':
        await display_wallet(update, context)
    elif query.data == 'buyorsell':
        await wallet_exists_and_has_balance(update, context)
    elif query.data == 'moonbotpro':
        await upgrade(update, context)
    elif query.data == 'settings':
        await settings_menu(update, context)
    elif query.data == 'assets':
        await assets_menu(update, context) 
    elif query.data in ['sniper', 'copytrade', 'aitrading']:
        if not pro_version:
            pro_message = (
                "<b>MoonBot Pro</b> \n\n"
                "This feature is only available for <b>MoonBot Pro</b> members.\n\n"
                "<b>Advantages of MoonBot Pro:</b>\n"
                "- Access to <b>Sniper</b>, <b>Copy Trade</b>, and <b>AI Trading</b>.\n"
                "- Priority support.\n"
                "- Exclusive features and tools.\n\n"
                "💵 <b>Price:</b> 0.8 SOL\n\n"
                "To upgrade to MoonBot Pro, use the command /upgrade."
            )
            await query.message.reply_text(pro_message, parse_mode="HTML")
        else:
            await query.message.reply_text("🛠️ This feature is under development.", parse_mode="HTML")
    elif query.data == 'languages':
        await query.message.reply_text("Languages is currently under development and will be available in the next update.", parse_mode="HTML")


async def upgrade(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = str(update.effective_user.id)
    wallets = await load_wallets()
    if not wallets:
        if update.message:
            await update.message.reply_text("❌ No wallets file found or it is corrupted.")
        else:
            await update.callback_query.message.reply_text("❌ No wallets file found or it is corrupted.")
        return

    if user_id not in wallets or "settings" not in wallets[user_id]:
        if update.message:
            await update.message.reply_text("❌ No settings found for this user.")
        else:
            await update.callback_query.message.reply_text("❌ No settings found for this user.")
        return

    user_settings = wallets[user_id]["settings"]
    pro_version = user_settings.get('pro_version', False)

    if pro_version:
        if update.message:
            await update.message.reply_text("✅ You are already a MoonBot Pro member!")
        else:
            await update.callback_query.message.reply_text("✅ You are already a MoonBot Pro member!")
    else:
        pro_message = (
            "🚀 <b>Upgrade to MoonBot Pro</b> 🚀\n\n"
            "To upgrade to MoonBot Pro, please send <b>0.8 SOL</b> to the following address:\n\n"
            "<code>YourWalletAddress</code>\n\n"
            "Once the payment is confirmed, your account will be automatically upgraded to Pro."
        )
        if update.message:
            await update.message.reply_text(pro_message, parse_mode="HTML")
        else:
            await update.callback_query.message.reply_text(pro_message, parse_mode="HTML")


"""------------------------------"""
"""            Main              """
"""------------------------------"""

async def load_wallets():
    try:
        with open(WALLETS_FILE, "r") as file:
            return json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

async def save_wallets(wallets):
    with open(WALLETS_FILE, "w") as file:
        json.dump(wallets, file, indent=4)


def main():
    application = Application.builder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("upgrade", upgrade))
    application.add_handler(CommandHandler("wallet", display_wallet))
    application.add_handler(CallbackQueryHandler(button, pattern="^(buyorsell|wallet|assets|history|sniper|copytrade|aitrading|moonbotpro|languages|settings)$"))
    application.add_handler(CallbackQueryHandler(wallet_action, pattern="^(create_wallet|import_wallet|delete_wallet|back_to_main|getPrivate_.*)$"))
    application.add_handler(CallbackQueryHandler(confirm_delete_wallet, pattern="^delete_.*$"))
    application.add_handler(CallbackQueryHandler(handle_buy_sell, pattern="^(buy_.*|sell_.*)$")) 
    application.add_handler(CallbackQueryHandler(button_assets, pattern="^(back_to_main|refresh_assets)$"))
    application.add_handler(CallbackQueryHandler(settings_button, pattern="^(set_slippage|auto_slippage|referral|claim_sol|gift|demo_mode|help|back_to_main)$")) 


    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^[A-Za-z0-9]{32,}$'), process_token_address))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+(\.\d+)?$'), process_amount))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^(\w+\s){11,23}\w+$'), process_mnemonic))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+(\.\d+)?$'), process_slippage))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d+$'), process_referral_code))

    application.run_polling()

if __name__ == '__main__':
    main()