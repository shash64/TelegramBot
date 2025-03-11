# TelegramBot

## Description
This is a Telegram bot designed to invest in memecoins on the Solana blockchain. It uses the Telegram, Solana, and Raydium libraries, as well as the Solscan, Dexscreener and Raydium APIs, to provide real-time data and investment functionalities.

## Features
- **Real-time Memecoin Data**: Fetches live data on memecoins using Solscan and dexscreener APIs.
- **Investment Tools**: Allows users to invest in memecoins directly through the bot.
- **Transaction Management**: Utilizes Solana and Raydium libraries for seamless transactions.
- **User-Friendly Interface**: Provides an intuitive and interactive experience via Telegram.

## Technologies Used
- **Telegram Bot API**: For bot interaction and user interface.
- **Solana Libraries**: For blockchain interactions and transactions.
- **Raydium**: For decentralized exchange functionalities.
- **Solscan API**: For blockchain data and analytics.
- **Dexscreener API**: For real-time market data and insights.

## Prerequisites
- Python 3.7 or higher
- Telegram account
- Solana wallet

## Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/shash64/TelegramBot.git
   cd TelegramBot

2. Install the dependencies:
   ```bash
   pip install -r requirements.txt


Please, before running the `MoonMapper.py` file, create a bot with the **BotFather** on Telegram and copy the token to paste it into the main file. 

You also need an RPC to make buy and sell requests; you can use, for example, **Helius** (free). To do this, create a Helius account, copy your key and enter the RPC link in the following files: `pool_utils.py`, `common_utils.py`, and `amm_v4.py`. 

Please note that some features have not yet been developed, including the sniper, copy trading, and slippage automation. 
(⚠️) The bot only works with **Raydium AMM pairs**, meaning you cannot trade pairs created with other DEXs or Raydium CPMM/CLMM pools.
