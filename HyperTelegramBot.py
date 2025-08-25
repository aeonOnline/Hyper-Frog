import telebot
from telebot import types
import telegram.error
from modules.wallet_manager import WalletDatabase, WalletManager
from modules.balance_manager import fetch_hyperevm_balances, fetch_solana_balance, get_token_decimals, get_token_symbol, get_token_balance_evm
from modules.gluex import get_swap_quote, execute_swap
from modules.token_map import TOKEN_MAP
from modules.hyper_debridge import get_debridge_quote, send_debridge_tx
from dotenv import load_dotenv
import os
import json
from modules.hyper_lifi_bridge import fetch_lifi_balance, get_lifi_quote, format_lifi_quote, send_lifi_tx
import modules.hyperlend as hyperlend
import modules.hypurrfi as hypurrfi
from modules.loopedhype import convert_to_loop_hype, get_lhype_balance

load_dotenv()
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not TOKEN:
    raise RuntimeError("TELEGRAM_BOT_TOKEN not set in .env")

bot = telebot.TeleBot(TOKEN)

db = WalletDatabase()
wallet_manager = WalletManager(db)

with open('lifi_list.json', 'r') as f:
    lifi_data = json.load(f)
chains = lifi_data['chains']

text_header = f"```_\n_             [ HYPERFROG ]              _\n```\n\n"

# In-memory user state for swap and input handling
state = {}  # user_id: dict for states

def create_wallets(user_id):
    evm_addr = wallet_manager.create_and_store_evm(user_id)
    sol_addr = wallet_manager.create_and_store_solana(user_id)
    if not evm_addr or not sol_addr:
        raise RuntimeError("Failed to create wallets")

def get_home_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('Balance', callback_data='balance'),
        types.InlineKeyboardButton('Swap', callback_data='swap'),
        types.InlineKeyboardButton('Yield', callback_data='yield'),
        types.InlineKeyboardButton('Settings', callback_data='settings')
    )
    return markup

def get_balance_markup():
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('Bridge sol', callback_data='bridge'),
        types.InlineKeyboardButton('Bridge EVM', callback_data='bridge_evm')
    )
    markup.add(
        types.InlineKeyboardButton('Swap', callback_data='swap'),
        types.InlineKeyboardButton('Yield', callback_data='yield')
    )
    markup.add(
        types.InlineKeyboardButton('Home', callback_data='back_home'),
        types.InlineKeyboardButton('Settings', callback_data='settings')
    )
    markup.add(
        types.InlineKeyboardButton('REFRESH', callback_data='balance')
    )
    return markup

def show_home(chat_id, user_id, message_id=None):
    # Fetch wallet addresses directly from wallet_manager
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    sol_wallet = wallet_manager.get_solana_wallet(user_id)
    if not evm_wallet or not sol_wallet:
        text = "```_\n_             [ HYPERFROG ]              _\n```\n\nError: No wallet data found. Please create wallets."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Create Wallet', callback_data='create_wallet'))
        if message_id:
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        else:
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
        return
    _, evm_addr = evm_wallet
    _, sol_addr = sol_wallet

    text = (
        f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
        f"*YOUR WALLET ADDRESSES:* 🔐(click to copy)\n\n"
        f"*HYPEVM :* `{evm_addr}`\n\n"
        f"*SOLANA :* `{sol_addr}`\n\n"
        " "
    )
    markup = get_home_markup()
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def show_balance(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    sol_wallet = wallet_manager.get_solana_wallet(user_id)
    
    if not evm_wallet or not sol_wallet:
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"Error: Could not retrieve wallet data.\n"
            f"EVM Wallet: {'Available' if evm_wallet else 'Not found'}\n"
            f"Solana Wallet: {'Available' if sol_wallet else 'Not found'}"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Home', callback_data='back_home')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    _, evm_addr = evm_wallet
    _, sol_addr = sol_wallet

    # Show loading template
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
    bot.edit_message_text(
        "```_\n_             [ HYPERFROG ]              _\n```\n\n⏳ Fetching balances...\n\nPlease wait a moment.",
        chat_id, message_id, reply_markup=markup, parse_mode='Markdown'
    )
    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'))
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    # Fetch balances on demand
    evm_balances = fetch_hyperevm_balances(evm_addr)
    sol_balances = fetch_solana_balance(sol_addr)
    
    # Format EVM balances
    evm_text = f"🌐 *HyperEVM* (`{evm_addr}`)\n\n"
    if evm_balances['errors']:
        evm_text += f"❌ *Errors:* {'; '.join(evm_balances['errors'])}\n"
    else:
        evm_text += "```\n"
        evm_text += f"{'Token':<8}{'Balance':>12}\n"
        evm_text += f"{'HYPE':<8}{evm_balances['native']:>12.2f}\n"
        for token, balance in evm_balances['tokens'].items():
            evm_text += f"{token:<8}{balance:>12.2f}\n"
        evm_text += "```\n"

    # Format Solana balances
    sol_text = f"🍃 *Solana* (`{sol_addr}`)\n\n"
    if sol_balances['errors']:
        sol_text += f"❌ *Errors:* {'; '.join(sol_balances['errors'])}\n"
    else:
        sol_text += "```\n"
        sol_text += f"{'Token':<8}{'Balance':>12}\n"
        sol_text += f"{'SOL':<8}{sol_balances['native']:>12.2f}\n"
        sol_text += "```\n"

    # Full message
    text = (
        f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
        "*📊 Balances:*\n\n"
        f"{evm_text}"
        f"{sol_text}"
        "\n🔁 _Deposit at least 0.1 SOL + gas on Solana to bridge directly to HyperEVM._"
    )
    markup = get_balance_markup()
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')

    # Delete loading message if it exists
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_private_key(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    sol_wallet = wallet_manager.get_solana_wallet(user_id)
    
    if not evm_wallet or not sol_wallet:
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"Error: Could not retrieve private keys.\n"
            f"EVM Wallet: {'Available' if evm_wallet else 'Not found'}\n"
            f"Solana Wallet: {'Available' if sol_wallet else 'Not found'}"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Back', callback_data='back_home')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    evm_priv, evm_pub = evm_wallet
    sol_priv, sol_pub = sol_wallet
    text = (
        f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
        f"Private keys (click to copy):\n\n"
        f"HYPEVM: `{evm_priv}`\n\n"
        f"SOLANA: `{sol_priv}`"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('Delete Message', callback_data='delete_priv'),
    )
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')

def format_hyperlend_data(data):
    address = data.get('address', 'N/A')
    positions = data.get('positions', {})
    acc = data.get('account', {})

    text = "✨ *HyperLend Overview* ✨\n\n"
    text += f"*Address:* `{address}`\n\n"

    text += "📊 *Open Positions:*\n"
    if not positions:
        text += "_No active positions found._\n\n"
    else:
        text += "```\n"
        for addr, pos in positions.items():
            if pos['supplied'] > 0 or pos['stableDebt'] > 0 or pos['variableDebt'] > 0:
                text += f"{pos['symbol']} ({pos['name']})\n"
                text += f"  Supplied:           {pos['supplied']:.4f}\n"
                text += f"  Stable Debt:        {pos['stableDebt']:.4f}\n"
                text += f"  Variable Debt:      {pos['variableDebt']:.4f}\n"
                text += f"  Collateral:         {'Yes' if pos['usageAsCollateralEnabled'] else 'No'}\n"
                text += f"  Liquidity Rate:     {pos['market_liquidityRatePct']:.2f}%\n"
                text += f"  Variable Borrow:    {pos['market_variableBorrowRatePct']:.2f}%\n"
                text += f"  Wallet Balance:     {pos['walletBalance']:.4f}\n\n"
        text += "```\n"

    text += "📌 *Account Summary:*\n"
    text += "```\n"

    if acc.get('totalCollateralBase', 0) != 0:
        t_c = acc.get('totalCollateralBase', 0) / (10**8)
    else:
        t_c = acc.get('totalCollateralBase', 0)

    text += f"Total Collateral:     {t_c}\n"

    if acc.get('totalDebtBase', 0) != 0:
        t_d = acc.get('totalDebtBase', 0) / (10**8)
    else:
        t_d = acc.get('totalDebtBase', 0)

    text += f"Total Debt:           {t_d}\n"

    if acc.get('availableBorrowsBase', 0) != 0:
        t_b = acc.get('availableBorrowsBase', 0) / (10**8)
    else:
        t_b = acc.get('availableBorrowsBase', 0)

    text += f"Available Borrows:    {t_b}\n"
    text += f"Liquidation Threshold:{acc.get('currentLiquidationThreshold', 0) / 100:.2f}%\n"
    text += f"LTV:                  {acc.get('ltv', 0) / 100:.2f}%\n"
    try:
        if acc.get('healthFactorRaw', 'N/A') > 11579208923731619542357098500868790785326998466564056403945758400791312963993:
            text += f"`Health Factor:` {'infinite ∞'}\n\n"
        else:
            try:
                text += f"`Health Factor:` {(acc.get('healthFactorRaw', 'N/A') / (10**18))}\n\n"
            except:
                text += f"`Health Factor:` {acc.get('healthFactorRaw', 'N/A')}\n\n"
    except:
        text += f"`Health Factor:` {acc.get('healthFactorRaw', 'N/A')}\n\n"
    text += "```\n"

    return text

def format_hyperfi_data(account, reserves):
    text = "💠 *HypurrFi Dashboard* 💠\n\n"

    # Account Summary (in USD)
    text += "📊 *Account Summary (USD)*\n"
    text += f"`Collateral:`    {account.get('total_collateral', 0) * 1e10:.2f} USD\n"
    text += f"`Debt:`          {account.get('total_debt', 0) * 1e10:.2f} USD\n"
    text += f"`Can Borrow:`    {account.get('available_borrow', 0) * 1e10:.2f} USD\n\n"

    text += "🧮 *Risk Metrics*\n"
    text += f"`LTV:`           {account.get('ltv', 0):.2f}%\n"
    text += f"`Threshold:`     {account.get('liquidation_threshold', 0):.2f}%\n"
    text += f"`Health Factor:` {account.get('health_factor', 'N/A')}\n\n"

    # Active Reserves
    text += "🏦 *Your Active Positions*\n"
    if not reserves:
        text += "_No active reserves found._\n"
        return text

    for addr, res in reserves.items():
        symbol = get_token_symbol(addr) or addr[:10] + "..."
        supplied = res["supplied_balance"]
        stable = res["stable_debt"]
        variable = res["variable_debt"]
        used = "Yes" if res["usage_as_collateral"] else "No"

        if supplied > 0 or stable > 0 or variable > 0:
            text += f"\n• *{symbol}*\n"
            text += f"   ├ Supplied:       `{supplied:.4f}`\n"
            text += f"   ├ Stable Debt:    `{stable:.4f}`\n"
            text += f"   ├ Variable Debt:  `{variable:.4f}`\n"
            text += f"   └ Collateral Use: `{used}`\n"

    return text

def show_yield(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    text = (
        f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
        f"Select a market to Connect to :\n\n"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('HyperLend', callback_data='yield_hyperlend'),
        types.InlineKeyboardButton('HypurrFi', callback_data='yield_hypurrfi'),
        types.InlineKeyboardButton('Get Looped Hype', callback_data='yield_loopedhype'),
    )
    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')

def show_hyperlend_positions(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Explore HyperLend', callback_data='explore_hyperlend'),
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    private_key, address = evm_wallet

    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'), caption="Fetching HyperLend positions, please wait...")
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    try:
        hyperlend_data = hyperlend.get_user_positions(private_key)
        formatted_hyperlend = format_hyperlend_data(hyperlend_data)
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"{formatted_hyperlend}"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Explore HyperLend', callback_data='explore_hyperlend')
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError fetching HyperLend data: {str(e)}"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Explore HyperLend', callback_data='explore_hyperlend')
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_hypurrfi_positions(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Explore HypurrFi', callback_data='explore_hypurrfi')
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    private_key, address = evm_wallet

    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'), caption="Fetching HypurrFi positions, please wait...")
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    try:
        hyperfi_account = hypurrfi.get_user_account_data(address)
        hyperfi_reserves = hypurrfi.get_user_reserve_data_full(address)
        formatted_hyperfi = format_hyperfi_data(hyperfi_account, hyperfi_reserves)
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"{formatted_hyperfi}"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Explore HypurrFi', callback_data='explore_hypurrfi')
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError fetching HypurrFi data: {str(e)}"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Explore HypurrFi', callback_data='explore_hypurrfi')
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_loopedhype_positions(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    private_key, address = evm_wallet

    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'), caption="Fetching Looped Hype positions, please wait...")
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    try:
        hype_token_address = "0x2222222222222222222222222222222222222222"
        hype_balance = get_token_balance_evm(address, hype_token_address)
        lhype_balance = get_lhype_balance(address)
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"✨ *Looped Hype Overview* ✨\n\n"
            f"*Hype Balance:* {hype_balance:.4f}\n\n"
            f"*Looped Hype Balance:* {lhype_balance:.4f}\n\n"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Stake Hype', callback_data='stake_hype')
        )
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError fetching Looped Hype data: {str(e)}"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_hyperlend_markets(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='yield_hyperlend'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    private_key, address = evm_wallet

    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'), caption="Fetching HyperLend markets, please wait...")
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    try:
        markets = hyperlend.fetch_all_markets_combined()
        # Filter markets to only include assets in TOKEN_MAP
        filtered_markets = {addr: m for addr, m in markets.items() if addr in TOKEN_MAP.values()}
        state[user_id] = state.get(user_id, {})
        state[user_id]['hyperlend_markets'] = filtered_markets
        state[user_id]['protocol'] = 'hyperlend'
        text = f"```_\n_             [ HYPERLEND MARKETS ]              _\n```\n\nAvailable Markets:\n\n"
        text += "```\n"
        # text += f"{'Symbol':<8}{'Liq Rate':>10}% {'Borrow Rate':>12}% {'Collateral':>12} {'Borrow':>8}\n\n"
        text += f"{'Symbol':<8}{'Liq Rate':>10}% {'Borrow Rate':>12}%\n\n"
        for addr, m in filtered_markets.items():
            # text += f"{m['symbol']:<8}{m['liquidityRatePct']:>10.2f} {m['variableBorrowRatePct']:>12.2f} {'Yes' if m['usageAsCollateralEnabled'] else 'No':>12} {'Yes' if m['borrowingEnabled'] else 'No':>8}\n"
            text += f"{m['symbol']:<8}{m['liquidityRatePct']:>10.2f} {m['variableBorrowRatePct']:>12.2f}\n"
        text += "```\nSelect an asset to interact:"
        markup = types.InlineKeyboardMarkup(row_width=3)
        asset_buttons = [types.InlineKeyboardButton(m['symbol'], callback_data=f'select_asset_{addr}') for addr, m in filtered_markets.items()]
        markup.add(*asset_buttons)
        markup.add(
            types.InlineKeyboardButton('HOME', callback_data='back_home'),
            types.InlineKeyboardButton('Back', callback_data='yield_hyperlend')
            )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError fetching markets: {str(e)}"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='yield_hyperlend'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_hypurrfi_markets(chat_id, user_id, message_id):
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='yield_hypurrfi'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    private_key, address = evm_wallet

    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'), caption="Fetching HypurrFi reserves, please wait...")
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    try:
        reserves = hypurrfi.fetch_reserves()
        # Filter reserves to only include assets in TOKEN_MAP
        filtered_reserves = [r for r in reserves if r['asset'] in TOKEN_MAP.values()]
        state[user_id] = state.get(user_id, {})
        state[user_id]['hypurrfi_reserves'] = filtered_reserves
        state[user_id]['protocol'] = 'hypurrfi'
        text = f"```_\n_             [ HYPURRFI RESERVES ]              _\n```\n\nAvailable Reserves:\n\n"
        text += "```\n"
        text += f"{'Symbol':<8}{'Liq Rate':>10}% {'Borrow Rate':>12}%\n\n"
        for r in filtered_reserves:
            text += f"{r['symbol']:<8}{r['liquidity_rate_%']:>10.2f} {r['variable_borrow_rate_%']:>12.2f}\n"
        text += "```\nSelect an asset to interact:"
        markup = types.InlineKeyboardMarkup(row_width=3)
        asset_buttons = [types.InlineKeyboardButton(r['symbol'], callback_data=f'select_asset_{r["asset"]}') for r in filtered_reserves]
        markup.add(*asset_buttons)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='yield_hypurrfi'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError fetching reserves: {str(e)}"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='yield_hypurrfi'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_asset_actions(chat_id, user_id, message_id):
    protocol = state.get(user_id, {}).get('protocol')
    asset_addr = state.get(user_id, {}).get('asset_addr')
    if not protocol or not asset_addr:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No protocol or asset selected."
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='yield'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        return

    private_key, address = wallet_manager.get_evm_wallet(user_id)

    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'))
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    text = f"```_\n_             [ {protocol.upper()} ASSET ]              _\n```\n\n"
    try:
        if protocol == 'hyperlend':
            markets = state[user_id].get('hyperlend_markets', {})
            m = markets.get(asset_addr, {})
            symbol = m.get('symbol', 'Unknown')
            positions = hyperlend.get_user_positions(private_key)
            pos = positions['positions'].get(asset_addr, {})
            text += "```"  # Start grey monospace block
            text += f"\n💠 Asset:              {symbol}\n"
            text += f"💰 Supplied:           {pos.get('supplied', 0):.4f}\n"
            text += f"📉 Stable Debt:        {pos.get('stableDebt', 0):.4f}\n"
            text += f"📈 Variable Debt:      {pos.get('variableDebt', 0):.4f}\n"
            text += f"🛡️ Collateral Enabled: {'Yes' if pos.get('usageAsCollateralEnabled', False) else 'No'}\n"
            text += f"💧 Liquidity Rate:     {pos.get('market_liquidityRatePct', 0):.2f}%\n"
            text += f"⚡ Borrow Rate:        {pos.get('market_variableBorrowRatePct', 0):.2f}%\n"
            text += f"👛 Wallet Balance:     {pos.get('walletBalance', 0):.4f}\n"
            text += f"✨ Borrowing Enabled:  {'Yes' if m.get('borrowingEnabled', False) else 'No'}\n"
            text += "```"  # End grey monospace block
            text += "\n\n👇 *Select an action:*"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton('Supply', callback_data='action_supply'),
                types.InlineKeyboardButton('Withdraw', callback_data='action_withdraw')
            )
            markup.add(
                types.InlineKeyboardButton('Borrow', callback_data='action_borrow'),
                types.InlineKeyboardButton('Repay', callback_data='action_repay')
            )
            markup.add(types.InlineKeyboardButton('Hyperloop', callback_data='action_hyperloop'))
            markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='explore_hyperlend'))
        elif protocol == 'hypurrfi':
            reserves_list = state[user_id].get('hypurrfi_reserves', [])
            r = next((rr for rr in reserves_list if rr['asset'] == asset_addr), {})
            symbol = r.get('symbol', 'Unknown')
            account = hypurrfi.get_user_account_data(address)
            res = hypurrfi.get_user_reserve_data_full(address).get(asset_addr, {})
            text += "```"  
            text += f"\n💠 Asset:              {symbol}\n"
            text += f"💰 Supplied:           {res.get('supplied_balance', 0):.4f}\n"
            text += f"📉 Stable Debt:        {res.get('stable_debt', 0):.4f}\n"
            text += f"📈 Variable Debt:      {res.get('variable_debt', 0):.4f}\n"
            text += f"🛡️ Collateral Enabled: {'Yes' if res.get('usage_as_collateral', False) else 'No'}\n"
            text += f"💧 Liquidity Rate:     {r.get('liquidity_rate_%', 0):.2f}%\n"
            text += f"⚡ Borrow Rate:        {r.get('variable_borrow_rate_%', 0):.2f}%\n"
            text += f"🔓 Wallet Balance:     {get_token_balance_evm(address, asset_addr):.4f} {(symbol)}\n"
            text += "```"
            text += "\n\n👇 *Select an action:*"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton('Supply', callback_data='action_supply'),
                types.InlineKeyboardButton('Withdraw', callback_data='action_withdraw')
            )
            markup.add(
                types.InlineKeyboardButton('Borrow', callback_data='action_borrow'),
                types.InlineKeyboardButton('Repay', callback_data='action_repay')
            )
            markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data='explore_hypurrfi'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    except Exception as e:
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError fetching {protocol} asset details: {str(e)}"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data=f'explore_{protocol}'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')

    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def prompt_for_amount(chat_id, user_id, message_id, action):
    protocol = state[user_id]['protocol']
    text = f"Enter amount to {action} "
    if protocol == 'hyperlend' and action in ['supply', 'withdraw', 'repay']:
        text += "(or 'max' for maximum):"
    else:
        text += ":"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('Cancel', callback_data='cancel_action'))
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    state[user_id]['waiting'] = f'amount_{action}'
    state[user_id]['message_id'] = message_id

def execute_action(chat_id, user_id, mid, action, amount_str):
    protocol = state.get(user_id, {}).get('protocol')
    asset_addr = state.get(user_id, {}).get('asset_addr')
    private_key, address = wallet_manager.get_evm_wallet(user_id)
    try:
        if protocol == 'hyperlend':
            markets = state[user_id].get('hyperlend_markets', {})
            m = markets.get(asset_addr, {})
            decimals = m.get('decimals', 18)
            if amount_str.lower() == 'max':
                if action == 'supply':
                    positions = hyperlend.get_user_positions(private_key)
                    pos = positions['positions'].get(asset_addr, {})
                    amount_wei = pos.get('walletBalance_raw', 0)
                elif action == 'withdraw':
                    amount_wei = None
                elif action == 'repay':
                    amount_wei = None
                else:
                    raise ValueError(" 'max' not supported for this action.")
            else:
                amount = float(amount_str)
                amount_wei = int(amount * 10 ** decimals)
            if action == 'supply':
                tx_hash = hyperlend.supply_with_approve(private_key, asset_addr, amount_wei, approve_infinite=True)
            elif action == 'withdraw':
                tx_hash = hyperlend.withdraw(private_key, asset_addr, amount_wei)
            elif action == 'borrow':
                tx_hash = hyperlend.borrow(private_key, asset_addr, amount_wei)
            elif action == 'repay':
                tx_hash = hyperlend.repay_with_approve(private_key, asset_addr, amount_wei, approve_infinite=True)
        elif protocol == 'hypurrfi':
            reserves_list = state[user_id].get('hypurrfi_reserves', [])
            r = next((rr for rr in reserves_list if rr['asset'] == asset_addr), {})
            decimals = r.get('decimals', 18)
            if amount_str.lower() == 'max':
                raise ValueError(" 'max' not supported for HypurrFi supply, enter numeric amount.")
            amount = float(amount_str)
            amount_wei = int(amount * 10 ** decimals)
            if action == 'supply':
                tx_hash = hypurrfi.supply(asset_addr, amount_wei, address, private_key)
            elif action == 'withdraw':
                tx_hash = hypurrfi.withdraw(asset_addr, amount_wei, address, private_key)
            elif action == 'borrow':
                tx_hash = hypurrfi.borrow(asset_addr, amount_wei, address, private_key)
            elif action == 'repay':
                tx_hash = hypurrfi.repay(asset_addr, amount_wei, address, private_key)
        # Delete user input message if it exists
        if 'user_input_message_id' in state[user_id]:
            try:
                bot.delete_message(chat_id, state[user_id]['user_input_message_id'])
            except telegram.error.TelegramError:
                pass
            del state[user_id]['user_input_message_id']
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nTransaction successful! \nTx hash: `{tx_hash}`\n\n[click to view on explorer](https://purrsec.com/tx/{tx_hash})"
        markup = types.InlineKeyboardMarkup(row_width=1)
        # markup.add(types.InlineKeyboardButton('Refresh Positions', callback_data=f'yield_{protocol}'))
        markup.add(types.InlineKeyboardButton('home', callback_data='back_home'))
        bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode='Markdown')
        z = bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\n")
        if protocol == 'hyperlend':
            show_hyperlend_positions(chat_id, user_id, z.message_id)
        else:
            show_hypurrfi_positions(chat_id, user_id, z.message_id)
    except Exception as e:
        # Delete user input message if it exists
        if 'user_input_message_id' in state[user_id]:
            try:
                bot.delete_message(chat_id, state[user_id]['user_input_message_id'])
            except telegram.error.TelegramError:
                pass
            del state[user_id]['user_input_message_id']
        text = f"Error performing {action}: {str(e)}"
        bot.edit_message_text(text, chat_id, mid, parse_mode='Markdown')
        if protocol == 'hyperlend':
            show_hyperlend_positions(chat_id, user_id, mid)
        else:
            show_hypurrfi_positions(chat_id, user_id, mid)
    if 'waiting' in state[user_id]:
        del state[user_id]['waiting']

def prompt_for_hyperloop(chat_id, user_id, message_id):
    if state[user_id]:
        text = "Enter initial supply amount for hyperloop:"
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Cancel', callback_data='cancel_action'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        state[user_id]['waiting'] = 'hyperloop_initial_amount'
        state[user_id]['message_id'] = message_id
    else:
        show_hyperlend_markets(chat_id,user_id,message_id)

def prompt_for_stake_amount(chat_id, user_id, message_id):
    text = "Enter amount to stake:"
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('Cancel', callback_data='cancel_stake'))
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    state[user_id]['waiting'] = 'stake_amount'
    state[user_id]['message_id'] = message_id

def execute_stake(chat_id, user_id, mid, amount_str):
    private_key, address = wallet_manager.get_evm_wallet(user_id)
    try:
        amount = int(float(amount_str) * (10**18))
        tx_hash = convert_to_loop_hype(private_key, amount)
        # Delete user input message if it exists
        # if 'user_input_message_id' in state[user_id]:
        #     try:
        #         bot.delete_message(chat_id, state[user_id]['user_input_message_id'])
        #     except telegram.error.TelegramError:
        #         pass
        #     del state[user_id]['user_input_message_id']
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nTransaction successful! \nTx hash: `{tx_hash}`\n\n[click to view on explorer](https://purrsec.com/tx/{tx_hash})"
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton('home', callback_data='back_home'))
        bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode='Markdown')
        z = bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nplease wait, frog is frogging...", parse_mode='Markdown')
        show_loopedhype_positions(chat_id, user_id, z.message_id)
    except Exception as e:
        # Delete user input message if it exists
        # if 'user_input_message_id' in state[user_id]:
        #     try:
        #         bot.delete_message(chat_id, state[user_id]['user_input_message_id'])
        #     except telegram.error.TelegramError:
        #         pass
        #     del state[user_id]['user_input_message_id']
        text = f"Error performing stake: {str(e)}"
        bot.edit_message_text(text, chat_id, mid, parse_mode='Markdown')
        show_loopedhype_positions(chat_id, user_id, mid)
    if 'waiting' in state[user_id]:
        del state[user_id]['waiting']

def show_settings(chat_id, user_id, message_id=None):
    slippage, yield_hype, yield_stables = wallet_manager.get_user_settings(user_id)
    text = (
        f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
        f"*FROG SETTINGS* : \n\n"
        f"*SLIPPAGE*: `{slippage}%`\n\n"
        f"*HYPE YIELD*: {'ON' if yield_hype else 'OFF'}\n\n"
        f"*STABLES YIELD*: {'ON' if yield_stables else 'OFF'}\n\n"
        f"_turning hype or stables yield on will allow the background yieldinator frog to access and execute transactions with them and the lsts, it has risks and you should understand them before turning it on_"
    )
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton('Private Key', callback_data='private_key'),
        types.InlineKeyboardButton('Set Slippage', callback_data='set_slippage')
    )
    markup.add(
        types.InlineKeyboardButton('Toggle Hype', callback_data='toggle_hype'),
        types.InlineKeyboardButton('Toggle Stables', callback_data='toggle_stables')
    )
    markup.add(
        types.InlineKeyboardButton('Home', callback_data='back_home')
    )
    if message_id:
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    else:
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')

def convert_wei_to_units(amount: str, token: str, token_address: str) -> float:
    decimals = get_token_decimals(token_address)
    return float(amount) / (10 ** decimals)

def format_bridge_message(resp: dict) -> str:
    estimation = resp.get("estimation", {})

    src = estimation.get("srcChainTokenIn", {})
    dst = estimation.get("dstChainTokenOut", {})

    src_symbol = src.get("symbol")
    src_amount = int(src.get("amount", "0")) / (10 ** src.get("decimals", 0))
    src_usd = src.get("approximateUsdValue")

    dst_symbol = dst.get("symbol")
    dst_amount = int(dst.get("amount", "0")) / (10 ** dst.get("decimals", 0))
    dst_usd = dst.get("approximateUsdValue")

    return (
        f"Swapping *{src_amount:.4f}* {src_symbol} (~${src_usd:.2f}) "
        f"to *{dst_amount:.4f}* {dst_symbol} (~${dst_usd:.2f}) _via deBridge_"
    )

def show_swap_selection(chat_id, user_id, message_id):
    # Show loading template
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
    bot.edit_message_text(
        "```_\n_             [ HYPERFROG ]              _\n```\n\n⏳ Loading balances and tokens...\n\nPlease wait a moment.",
        chat_id, message_id, reply_markup=markup, parse_mode='Markdown'
    )
    loading_gif_path = "ani.gif"
    try:
        loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'))
        loading_message_id = loading_message.message_id
    except telegram.error.TelegramError:
        loading_message_id = None

    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    if not evm_wallet:
        text = (
            "```_\n_             [ HYPERFROG ]              _\n```\n\n"
            "❌ Error: No EVM wallet found."
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        # Delete loading GIF if it exists
        if loading_message_id:
            try:
                bot.delete_message(chat_id, loading_message_id)
            except telegram.error.TelegramError:
                pass
        return
    _, evm_addr = evm_wallet

    # Fetch available tokens and balances
    

    # Check current swap step
    swap_state = state.get(user_id, {})
    if 'swap_from' not in swap_state or 'swap_amount' not in swap_state:
        evm_balances = fetch_hyperevm_balances(evm_addr)
        available_tokens = (['HYPE'] if evm_balances.get('native', 0) > -1 else []) + list(evm_balances.get('tokens', {}).keys())
        # Step 1: Select From token and amount
        text = (
            "```_\n_             [ HYPERFROG ]              _\n```\n\n"
            "Select token to swap from:\n\n"
        )
        text += "```\n\n"  # start monospace block
        text += f"{'Token':<8}{'Balance':>12}\n\n"
        for token in available_tokens:
            balance = evm_balances['native'] if token == 'HYPE' else evm_balances['tokens'].get(token, 0)
            text += f"{token:<8}{balance:>12.2f}\n"
        text += "```"  # end monospace block

        if not available_tokens:
            text = (
                "```_\n_             [ HYPERFROG ]              _\n```\n\n"
                "❌ No tokens available to swap."
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
            # Delete loading GIF if it exists
            if loading_message_id:
                try:
                    bot.delete_message(chat_id, loading_message_id)
                except telegram.error.TelegramError:
                    pass
            return
        markup = types.InlineKeyboardMarkup(row_width=2)
        from_buttons = [types.InlineKeyboardButton(f"{token}", callback_data=f'swap_from_{token}') for token in available_tokens]
        markup.add(*from_buttons)
        markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    elif 'swap_to' not in swap_state:
        # Step 2: Select To token
        text = (
            "```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"From: `{swap_state['swap_from']} ({swap_state['swap_amount']:.2f})`\n\n"
            "Select token to swap to:\n\n"
        )
        # for token in TOKEN_MAP.keys():
            # balance = evm_balances['native'] if token == 'HYPE' else evm_balances['tokens'].get(token, 0)
        markup = types.InlineKeyboardMarkup(row_width=2)
        to_buttons = [types.InlineKeyboardButton(f"{token}", callback_data=f'swap_to_{token}') for token in TOKEN_MAP.keys()]
        markup.add(*to_buttons)
        markup.add(
            types.InlineKeyboardButton('Custom CA', callback_data='swap_to_custom'),
            types.InlineKeyboardButton('Home', callback_data='back_home')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    elif 'quote_result' not in swap_state:
        # Step 3: Fetch and show quote
        from_token = swap_state['swap_from']
        from_addr = swap_state['swap_from_addr'] or '0x2222222222222222222222222222222222222222'
        to_addr = swap_state['swap_to']
        to_token = next((k for k, v in TOKEN_MAP.items() if v == to_addr), get_token_symbol(to_addr))
        amount = swap_state['swap_amount']
        decimals = get_token_decimals(from_addr)
        amount_wei = str(int(amount * (10 ** decimals)))

        quote = get_swap_quote(from_addr, to_addr, amount_wei, evm_addr)

        if quote.get('statusCode') != 200:
            text = (
                "```_\n_             [ HYPERFROG ]              _\n```\n\n"
                f"❌ Simulation failed: Failed to fetch swap quote (Status {quote.get('statusCode')})."
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
            if user_id in state:
                state.pop(user_id)
            return
        if quote.get('revert', False):
            text = (
                "```_\n_             [ HYPERFROG ]              _\n```\n\n"
                "❌ Error: Swap will revert. Please try a different amount or token pair."
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
            if user_id in state:
                state.pop(user_id)
            return
        if quote.get('lowBalance', False):
            text = (
                "```_\n_             [ HYPERFROG ]              _\n```\n\n"
                "❌ Error: Insufficient balance for swap."
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
            if user_id in state:
                state.pop(user_id)
            return

        state[user_id]['quote_result'] = quote['result']

        input_amount = convert_wei_to_units(quote['result']['inputAmount'], from_token, from_addr)
        output_amount = convert_wei_to_units(quote['result']['outputAmount'], to_token, to_addr)
        min_output_amount = convert_wei_to_units(quote['result']['minOutputAmount'], to_token, to_addr)
        text = (
            "```_\n_             [ HYPERFROG ]              _\n```\n\n"
            "Swap Quote:\n\n"
            f"Swapping: `{input_amount:.2f}` {from_token} → `{output_amount:.2f}` {to_token}\n\n"
            f"*Minimum Received*: `{min_output_amount:.2f} {to_token}`\n\n"
            "Confirm this swap?"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Confirm', callback_data='swap_confirm'),
            types.InlineKeyboardButton('Cancel', callback_data='swap_cancel')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
    else:
        text = (
            "```_\n_             [ HYPERFROG ]              _\n```\n\n"
            "❌ Error: Quote already fetched. Please confirm or cancel."
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton('Confirm', callback_data='swap_confirm'),
            types.InlineKeyboardButton('Cancel', callback_data='swap_cancel')
        )
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')

    # Delete loading GIF if it exists
    if loading_message_id:
        try:
            bot.delete_message(chat_id, loading_message_id)
        except telegram.error.TelegramError:
            pass

def show_bridge_evm_chains(chat_id, user_id, message_id):
    text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nChoose chain to bridge from:"
    markup = types.InlineKeyboardMarkup(row_width=2)
    chain_buttons = [types.InlineKeyboardButton(chain['name'], callback_data=f'bridge_evm_from_{chain["key"]}') for chain in chains]
    markup.add(*chain_buttons)
    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
    markup.add(types.InlineKeyboardButton('Back', callback_data='balance'))
    bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['start'])
def start(message):
    """Handle /start command, checking if user has wallets in wallet_manager."""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    evm_wallet = wallet_manager.get_evm_wallet(user_id)
    sol_wallet = wallet_manager.get_solana_wallet(user_id)
    
    if not evm_wallet or not sol_wallet:
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Create Wallet', callback_data='create_wallet'))
        text = (
            f"\n\n"
            f"Welcome to HyperFrog 🐸 — Your DeFi Sidekick on HyperEVM!\n\n"
            f"Swap, bridge, and earn yields seamlessly in Telegram. Fast, secure, no hassle! 🚀\n\n"
            f"🔑 Features:\n\n"
            f"  • Swaps: Instant token trades via *Gluex Router* for best prices.\n\n"
            f"  • Bridging: Move assets from Solana/EVM to HyperEVM with LiFi & DeBridge.\n\n"
            f"  • Yields: Maximize APY with *HyperLend*, *HypurrFi*, and *LoopedHype* staking.\n\n"
            f"  • Wallets: Secure EVM/Solana wallet management in-chat.\n\n"
            f"Start now! Create your wallet and jump into DeFi!🐸"
            f" "
        )

        loading_gif_path = "ani.gif"
        try:
            loading_message = bot.send_animation(chat_id, animation=open(loading_gif_path, 'rb'), caption=text, reply_markup=markup)
            loading_message_id = loading_message.message_id
        except telegram.error.TelegramError:
            bot.send_message(
                chat_id,
                text,
                reply_markup=markup,
                parse_mode='Markdown'
            )
    else:
        show_home(chat_id, user_id)

@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    data = call.data

    if data == 'create_wallet':
        try:
            create_wallets(user_id)
            bot.delete_message(chat_id, message_id)
            show_home(chat_id, user_id)
        except RuntimeError as e:
            bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: {str(e)}", chat_id, message_id, parse_mode='Markdown')
    elif data == 'private_key':
        show_private_key(chat_id, user_id, message_id)
    elif data == 'delete_priv':
        bot.delete_message(chat_id, message_id)
        show_home(chat_id, user_id)
    elif data == 'balance':
        show_balance(chat_id, user_id, message_id)
    elif data == 'bridge':
        evm_wallet = wallet_manager.get_evm_wallet(user_id)
        sol_wallet = wallet_manager.get_solana_wallet(user_id)
        if not evm_wallet or not sol_wallet:
            bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No wallet data found.", parse_mode='Markdown')
            return
        _, evm_addr = evm_wallet
        _, sol_addr = sol_wallet
        sol_balances = fetch_solana_balance(sol_addr)
        sol_balance = sol_balances.get('native', 0)
        if sol_balance < 0.1:
            text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: Insufficient SOL balance. You need at least 0.1 SOL to bridge. Available: {sol_balance:.2f} SOL."
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton('home', callback_data='back_home'))
            markup.add(types.InlineKeyboardButton('delete message', callback_data='delete_priv'))
            bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
            return
        state[user_id] = state.get(user_id, {})
        state[user_id]['bridge_evm_addr'] = evm_addr
        state[user_id]['bridge_sol_addr'] = sol_addr
        state[user_id]['bridge_balance'] = sol_balance
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"Enter amount to bridge (SOL):\n\n"
            f"Available: `{sol_balance:.2f}` SOL\n\n"
            f"_minimum: 0.1 sol is required_"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Cancel', callback_data='bridge_cancel'))
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
        state[user_id]['waiting'] = 'bridge_amount'
        state[user_id]['message_id'] = message_id
    elif data == 'bridge_confirm':  # Lifi_sol_bridge
        if user_id not in state or 'bridge_full_resp' not in state[user_id]:
            bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No bridge data found.", chat_id, message_id, parse_mode='Markdown')
            show_home(chat_id, user_id)
            return
        full_resp = state[user_id]['bridge_full_resp']
        bridge_type = state[user_id]['bridge_type']
        sol_wallet = wallet_manager.get_solana_wallet(user_id)
        sol_priv, _ = sol_wallet
        if bridge_type == 'lifi':
            result = send_lifi_tx(sol_priv, full_resp)
        else:
            result = send_debridge_tx(sol_priv, full_resp)
        if 'bridge_full_resp' in state[user_id]:
            del state[user_id]['bridge_full_resp']
        if 'bridge_type' in state[user_id]:
            del state[user_id]['bridge_type']
        if 'errorCode' in result:
            text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: {result['errorMessage']}"
        else:
            tx_hash = result['tx_hash']
            text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nBridged successfully!\n\n Tx hash: `{tx_hash}`"
        bot.edit_message_text(text, chat_id, message_id, parse_mode='Markdown')
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
        tmp = bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nLoading balances !!", reply_markup=markup, parse_mode='Markdown')
        show_balance(chat_id, user_id, tmp.message_id)
    elif data == 'bridge_cancel':  # Lifi_sol_bridge
        if 'bridge_full_resp' in state[user_id]:
            del state[user_id]['bridge_full_resp']
        if 'bridge_type' in state[user_id]:
            del state[user_id]['bridge_type']
        if user_id in state and 'bridge_tx_data' in state[user_id]:
            del state[user_id]['bridge_tx_data']
        if user_id in state and 'bridge_amount' in state[user_id]:
            del state[user_id]['bridge_amount']
        if user_id in state and 'bridge_evm_addr' in state[user_id]:
            del state[user_id]['bridge_evm_addr']
        if user_id in state and 'bridge_sol_addr' in state[user_id]:
            del state[user_id]['bridge_sol_addr']
        if user_id in state and 'bridge_balance' in state[user_id]:
            del state[user_id]['bridge_balance']
        bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nBridge cancelled.", chat_id, message_id, parse_mode='Markdown')
        show_home(chat_id, user_id)
    elif data == 'bridge_evm':
        show_bridge_evm_chains(chat_id, user_id, message_id)
    elif data.startswith('bridge_evm_from_'):
        chain_key = data.split('_')[3]
        selected_chain = next((c for c in chains if c['key'] == chain_key), None)
        if not selected_chain:
            bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: Chain not found.", chat_id, message_id, parse_mode='Markdown')
            show_home(chat_id, user_id)
            return
        state[user_id] = state.get(user_id, {})
        state[user_id]['bridge_evm_chain'] = selected_chain
        evm_wallet = wallet_manager.get_evm_wallet(user_id)
        if not evm_wallet:
            bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found.", chat_id, message_id, parse_mode='Markdown')
            show_home(chat_id, user_id)
            return
        _, evm_addr = evm_wallet
        rpc = selected_chain['metamask']['rpcUrls'][0]
        balance = fetch_lifi_balance(evm_addr, rpc)
        symbol = selected_chain['coin']
        text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nBalance on {selected_chain['name']}: `{balance:.4f}` {symbol}\n\nEnter amount to bridge:"
        markup = types.InlineKeyboardMarkup(row_width=1)
        markup.add(types.InlineKeyboardButton('Cancel', callback_data='bridge_evm_cancel'))
        bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        state[user_id]['waiting'] = 'bridge_evm_amount'
        state[user_id]['message_id'] = message_id
        state[user_id]['bridge_evm_balance'] = balance
    elif data == 'bridge_evm_confirm':
        if user_id not in state or 'bridge_evm_quote' not in state[user_id]:
            bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No bridge data found.", chat_id, message_id, parse_mode='Markdown')
            return
        quote = state[user_id]['bridge_evm_quote']
        evm_wallet = wallet_manager.get_evm_wallet(user_id)
        private_key, _ = evm_wallet
        selected_chain = state[user_id]['bridge_evm_chain']
        rpc = selected_chain['metamask']['rpcUrls'][0]
        result = send_lifi_tx(private_key, quote, evm_rpc=rpc)
        if user_id in state and 'bridge_evm_quote' in state[user_id]:
            del state[user_id]['bridge_evm_quote']
        if 'errorCode' in result:
            text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: {result['errorMessage']}"
        else:
            tx_hash = result['tx_hash']
            ex_link = state[user_id]['bridge_evm_chain']['metamask']['blockExplorerUrls'][0] + "tx/" + str(tx_hash)
            text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nBridged successfully!\n\n Tx hash: `{tx_hash}`\n\n{ex_link}"
        bot.edit_message_text(text, chat_id, message_id, parse_mode='Markdown')
        if user_id in state:
            state.pop(user_id)
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
        tmp = bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nLoading balances !!", reply_markup=markup, parse_mode='Markdown')
        show_balance(chat_id, user_id, tmp.message_id)
    elif data == 'bridge_evm_cancel':
        if user_id in state:
            state.pop(user_id)
        bot.edit_message_text(f"```_\n_             [ HYPERFROG ]              _\n```\n\nBridge cancelled.", chat_id, message_id, parse_mode='Markdown')
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
        tmp = bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nLoading balances !!", reply_markup=markup, parse_mode='Markdown')
        show_bridge_evm_chains(chat_id, user_id, tmp.message_id)
    elif data == 'swap':
        state[user_id] = state.get(user_id, {})
        if 'swap_from' in state[user_id]:
            del state[user_id]['swap_from']
        if 'swap_amount' in state[user_id]:
            del state[user_id]['swap_amount']
        if 'swap_from_addr' in state[user_id]:
            del state[user_id]['swap_from_addr']
        if 'swap_to' in state[user_id]:
            del state[user_id]['swap_to']
        if 'quote_result' in state[user_id]:
            del state[user_id]['quote_result']
        show_swap_selection(chat_id, user_id, message_id)
    elif data == 'swap_to_custom':
        swap_state = state.get(user_id, {})
        if 'swap_from' not in swap_state or 'swap_amount' not in swap_state:
            bot.edit_message_text(
                f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: Please select a 'From' token and amount first.",
                chat_id, message_id, parse_mode='Markdown'
            )
            return
        bot.edit_message_text(
            f"```_\n_             [ HYPERFROG ]              _\n```\n\nFrom: `{swap_state['swap_from']} ({swap_state['swap_amount']:.2f})`\n\nEnter custom contract address:",
            chat_id, message_id, parse_mode='Markdown'
        )
        state[user_id]['waiting'] = 'swap_to_addr'
        state[user_id]['message_id'] = message_id
    elif data.startswith('swap_to_'):
        token = data.split('_')[2]
        state[user_id] = state.get(user_id, {})
        state[user_id]['swap_to'] = TOKEN_MAP[token]
        show_swap_selection(chat_id, user_id, message_id)
    elif data.startswith('swap_from_'):
        token = data.split('_')[2]
        state[user_id] = state.get(user_id, {})
        state[user_id]['swap_from'] = token
        state[user_id]['swap_from_addr'] = TOKEN_MAP.get(token, None)
        bot.edit_message_text(
            f"```_\n_             [ HYPERFROG ]              _\n```\n\nSelected: `{token}`\n\nEnter amount to swap:",
            chat_id, message_id, parse_mode='Markdown'
        )
        state[user_id]['waiting'] = 'swap_amount'
        state[user_id]['message_id'] = message_id
    elif data == 'swap_confirm':
        swap_state = state.get(user_id, {})
        if 'quote_result' not in swap_state:
            bot.edit_message_text(
                f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No quote available. Please try again.",
                chat_id, message_id, parse_mode='Markdown'
            )
            if user_id in state:
                state.pop(user_id)
            show_home(chat_id, user_id)
            return
        from_token = swap_state.get('swap_from', 'None')
        to_addr = swap_state.get('swap_to', 'None')
        to_token = next((k for k, v in TOKEN_MAP.items() if v == to_addr), to_addr)
        from_addr = swap_state.get('swap_from_addr', '0x2222222222222222222222222222222222222222')
        # Get user wallet for private key
        evm_wallet = wallet_manager.get_evm_wallet(user_id)
        if not evm_wallet:
            bot.edit_message_text(
                f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found.",
                chat_id, message_id, parse_mode='Markdown'
            )
            if user_id in state:
                state.pop(user_id)
            show_home(chat_id, user_id)
            return
        private_key, user_address = evm_wallet
        # Execute swap
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('GLUEx COOKING', callback_data='gluex'))
        temp = bot.send_message(
            chat_id,
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"EXECUTING SWAP...",
            reply_markup=markup,
            parse_mode='Markdown'
        )
        result = execute_swap(swap_state['quote_result'], user_address, private_key)
        if result.get('statusCode') != 200 or result.get('error'):
            text = (
                f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
                f"Error: Swap failed. {result.get('error', 'Unknown error (Status ' + str(result.get('statusCode')) + ').')}"
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
            if user_id in state:
                state.pop(user_id)
            return
        tx_hash = result.get('txHash', 'Unknown')
        input_amount = convert_wei_to_units(swap_state['quote_result']['inputAmount'], from_token, from_addr)
        output_amount = convert_wei_to_units(swap_state['quote_result']['outputAmount'], to_token, to_addr)
        text = (
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
            f"Swapped {input_amount:.2f} {from_token} to {output_amount:.2f} {to_token} successfully!\n\n"
            f"Tx Hash: `{tx_hash}`\n\n"
            f"[click to view on explorer](https://purrsec.com/tx/{tx_hash})"
        )
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(types.InlineKeyboardButton('GLUEx COOKED', callback_data='gluex'))
        bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
        if user_id in state:
            state.pop(user_id)
        temp = bot.send_message(
            chat_id,
            f"```_\n_             [ HYPERFROG ]              _\n```\n\n",
            reply_markup=markup,
            parse_mode='Markdown'
        )
        show_balance(chat_id, user_id, temp.message_id)
    elif data == 'swap_cancel':
        if user_id in state:
            state.pop(user_id)
        show_home(chat_id, user_id, message_id)
    elif data == 'yield':
        show_yield(chat_id, user_id, message_id)
    elif data == 'yield_hyperlend':
        show_hyperlend_positions(chat_id, user_id, message_id)
    elif data == 'yield_hypurrfi':
        show_hypurrfi_positions(chat_id, user_id, message_id)
    elif data == 'yield_loopedhype':
        show_loopedhype_positions(chat_id, user_id, message_id)
    elif data == 'explore_hyperlend':
        show_hyperlend_markets(chat_id, user_id, message_id)
    elif data == 'explore_hypurrfi':
        show_hypurrfi_markets(chat_id, user_id, message_id)
    elif data.startswith('select_asset_'):
        asset_addr = data[13:]
        state[user_id]['asset_addr'] = asset_addr
        show_asset_actions(chat_id, user_id, message_id)
    elif data == 'action_supply':
        prompt_for_amount(chat_id, user_id, message_id, 'supply')
    elif data == 'action_withdraw':
        prompt_for_amount(chat_id, user_id, message_id, 'withdraw')
    elif data == 'action_borrow':
        prompt_for_amount(chat_id, user_id, message_id, 'borrow')
    elif data == 'action_repay':
        prompt_for_amount(chat_id, user_id, message_id, 'repay')
    elif data == 'action_hyperloop':
        prompt_for_hyperloop(chat_id, user_id, message_id)
    elif data == 'cancel_action':
        if 'waiting' in state[user_id]:
            del state[user_id]['waiting']
        show_asset_actions(chat_id, user_id, message_id)
    elif data == 'stake_hype':
        hype_token_address = "0x2222222222222222222222222222222222222222"
        _, address = wallet_manager.get_evm_wallet(user_id)
        hype_balance = get_token_balance_evm(address, hype_token_address)
        if hype_balance <= 0:
            text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nNo hype available, please send on hyperevm to `{address}` or bridge from solana or any evm chain"
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton('Balance', callback_data='balance'),
                types.InlineKeyboardButton('HOME', callback_data='back_home'),
                types.InlineKeyboardButton('Back', callback_data='yield_loopedhype')
            )
            bot.edit_message_text(text, chat_id, message_id, reply_markup=markup, parse_mode='Markdown')
        else:
            state[user_id] = state.get(user_id, {})
            state[user_id]['hype_balance'] = hype_balance
            prompt_for_stake_amount(chat_id, user_id, message_id)
    elif data == 'cancel_stake':
        if 'waiting' in state[user_id]:
            del state[user_id]['waiting']
        show_loopedhype_positions(chat_id, user_id, message_id)
    elif data == 'toggle_hype':
        wallet_manager.toggle_yield_hype(user_id)
        show_settings(chat_id, user_id, message_id)
    elif data == 'toggle_stables':
        wallet_manager.toggle_yield_stables(user_id)
        show_settings(chat_id, user_id, message_id)
    elif data == 'settings':
        show_settings(chat_id, user_id, message_id)
    elif data == 'set_slippage':
        state[user_id] = state.get(user_id, {})
        bot.edit_message_text(
            f"```_\n_             [ HYPERFROG ]              _\n```\n\nEnter new slippage percentage:",
            chat_id,
            message_id,
            parse_mode='Markdown'
        )
        state[user_id]['waiting'] = 'slippage'
        state[user_id]['message_id'] = message_id
    elif data == 'back_home':
        if user_id in state:
            state.pop(user_id)
        show_home(chat_id, user_id, message_id)
    elif data == 'gluex':
        pass

@bot.message_handler(func=lambda message: True)
def handle_text(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    if user_id in state and 'waiting' in state[user_id]:
        waiting = state[user_id]['waiting']
        mid = state[user_id]['message_id']
        # Store the user input message ID for deletion
        state[user_id]['user_input_message_id'] = message.message_id
        if waiting == 'slippage':
            try:
                slippage = float(message.text)
                if wallet_manager.update_slippage(user_id, slippage):
                    try:
                        bot.send_message(chat_id,f"```_\n_             [ HYPERFROG ]              _\n```\n\nSlippage set to {slippage}%", parse_mode='Markdown')
                        z = bot.send_message(chat_id,f"```_\n_             [ HYPERFROG ]              _\n```\n\n", parse_mode='Markdown')
                        show_settings(chat_id, user_id, z.message_id)
                        if user_id in state:
                            state.pop(user_id)
                    except telegram.error.TelegramError:
                        pass  # Silently handle if message is already deleted
                    show_settings(chat_id, user_id, mid)
                else:
                    bot.reply_to(message, "Failed to update slippage.", parse_mode='Markdown')
            except ValueError:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nInvalid number. Enter again:", parse_mode='Markdown')
                show_settings(chat_id, user_id, mid)
        elif waiting.startswith('amount_'):
            action = waiting[7:]
            execute_action(chat_id, user_id, mid, action, message.text)
        elif waiting == 'swap_amount':
            try:
                amount = float(message.text)
                if amount <= 0:
                    raise ValueError("Amount must be positive")
                # Validate against token balance
                evm_wallet = wallet_manager.get_evm_wallet(user_id)
                if not evm_wallet:
                    bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: No EVM wallet found.", parse_mode='Markdown')
                    return
                k = bot.send_message(chat_id, "processing...")
                kid = k.message_id
                # evm_balances = fetch_hyperevm_balances(evm_wallet[1])
                token = state[user_id]['swap_from']
                max_balance = get_token_balance_evm(evm_wallet[1], TOKEN_MAP[token])
                # max_balance = evm_balances['native'] if token == 'HYPE' else evm_balances['tokens'].get(token, 0)
                if amount > max_balance:
                    bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nInsufficient balance. Available: {max_balance:.2f} {token}.", parse_mode='Markdown')
                    text = f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
                    sent = bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
                    message_id = sent.message_id
                    try:
                        bot.delete_message(chat_id, kid)
                    except:
                        pass
                    show_swap_selection(chat_id, user_id, message_id)
                    return
                # Check HYPE balance for gas fees
                hype_balance = get_token_balance_evm(evm_wallet[1], TOKEN_MAP['HYPE'])
                if token == 'HYPE' and hype_balance - amount < 0.02:
                    bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: You need at least 0.05 HYPE to cover gas fees.", parse_mode='Markdown')
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
                    try:
                        bot.delete_message(chat_id, kid)
                    except:
                        pass
                    bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\n", reply_markup=markup, parse_mode='Markdown')
                    if user_id in state:
                        state.pop(user_id)
                    return
                elif token != 'HYPE' and hype_balance < 0.02:
                    bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: You need at least 0.05 HYPE to cover gas fees.", parse_mode='Markdown')
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
                    try:
                        bot.delete_message(chat_id, kid)
                    except:
                        pass
                    bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\n", reply_markup=markup, parse_mode='Markdown')
                    if user_id in state:
                        state.pop(user_id)
                    return
                # Valid amount and sufficient HYPE balance, proceed
                state[user_id]['swap_amount'] = amount
                try:
                    bot.delete_message(chat_id, message.message_id)
                except telegram.error.TelegramError:
                    pass  # Silently handle if message is already deleted
                try:
                    bot.delete_message(chat_id, kid)
                except:
                    pass
                show_swap_selection(chat_id, user_id, mid)
            except ValueError as e:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: {str(e)}", parse_mode='Markdown')
                text = f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
                sent = bot.send_message(chat_id, text, reply_markup=markup, parse_mode='Markdown')
                message_id = sent.message_id
                show_swap_selection(chat_id, user_id, message_id)
        elif waiting == 'swap_to_addr':
            token_addr = message.text.strip()
            if not token_addr.startswith('0x') or len(token_addr) != 42:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: Invalid contract address. ", parse_mode='Markdown')
                show_swap_selection(chat_id, user_id, mid)
                return
            state[user_id]['swap_to'] = token_addr
            try:
                bot.delete_message(chat_id, message.message_id)
            except telegram.error.TelegramError:
                pass  # Silently handle if message is already deleted
            show_swap_selection(chat_id, user_id, mid)
        elif waiting == 'bridge_amount':  # Lifi_sol_bridge
            amount = float(message.text)
            sol_balance = state[user_id].get('bridge_balance', 0)
            if amount < 0.1 or amount > sol_balance:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: Amount must be between 0.1 and {sol_balance:.2f} SOL.", parse_mode='Markdown')
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton('Cancel', callback_data='bridge_cancel'))
                bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nTry again:", reply_markup=markup, parse_mode='Markdown')
                return
            state[user_id]['bridge_amount'] = amount
            amount_wei = str(int(amount * 10**9))  # SOL has 9 decimals
            sol_addr = state[user_id]['bridge_sol_addr']
            evm_addr = state[user_id]['bridge_evm_addr']
            bot.delete_message(chat_id, message.message_id)  # Delete user input message
            # Try LiFi first
            lifi_quote = get_lifi_quote("sol", "11111111111111111111111111111111", amount_wei, sol_addr, evm_addr)
            if 'errorCode' in lifi_quote:
                error_text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nLiFi Error: {lifi_quote['errorMessage']}"
                bot.send_message(chat_id, error_text, parse_mode='Markdown')
                trying_text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nTrying via deBridge..."
                bot.send_message(chat_id, trying_text, parse_mode='Markdown')
                # Fallback to DeBridge
                debridge_resp = get_debridge_quote(amount_wei, sol_addr, evm_addr)
                if 'errorCode' in debridge_resp:
                    error_text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nDeBridge Error: {debridge_resp['errorMessage']}"
                    bot.send_message(chat_id, error_text, parse_mode='Markdown')
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
                    bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nBridge failed.", reply_markup=markup, parse_mode='Markdown')
                    if user_id in state:
                        state.pop(user_id)
                    return
                else:
                    state[user_id]['bridge_type'] = 'debridge'
                    state[user_id]['bridge_full_resp'] = debridge_resp
                    formatted = format_bridge_message(debridge_resp)
                    text = f"```_\n_             [ HYPERFROG ]              _\n```\n\n{formatted}\n\nConfirm bridge?"
                    markup = types.InlineKeyboardMarkup(row_width=2)
                    markup.add(
                        types.InlineKeyboardButton('Confirm', callback_data='bridge_confirm'),
                        types.InlineKeyboardButton('Cancel', callback_data='bridge_cancel')
                    )
                    bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode='Markdown')
            else:
                state[user_id]['bridge_type'] = 'lifi'
                state[user_id]['bridge_full_resp'] = lifi_quote
                formatted = format_lifi_quote(lifi_quote)
                text = f"```_\n_             [ HYPERFROG ]              _\n```\n\n{formatted}\n\nConfirm bridge?"
                markup = types.InlineKeyboardMarkup(row_width=2)

                markup.add(
                    types.InlineKeyboardButton('Powered by LiFi', callback_data='gluex')
                )
                markup.add(
                    types.InlineKeyboardButton('Confirm', callback_data='bridge_confirm'),
                    types.InlineKeyboardButton('Cancel', callback_data='bridge_cancel')
                )
                bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode='Markdown')
        elif waiting == 'bridge_evm_amount':
            amount = float(message.text)
            balance = state[user_id].get('bridge_evm_balance', 0)
            selected_chain = state[user_id]['bridge_evm_chain']
            symbol = selected_chain['coin']
            if amount <= 0 or amount > balance:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: Amount must be between 0 and {balance:.4f} {symbol}.", parse_mode='Markdown')
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton('Cancel', callback_data='bridge_evm_cancel'))
                bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\nTry again:", reply_markup=markup, parse_mode='Markdown')
                return
            evm_wallet = wallet_manager.get_evm_wallet(user_id)
            _, evm_addr = evm_wallet
            from_chain = selected_chain['key']
            from_token = selected_chain['nativeToken']['address']
            decimals = selected_chain['nativeToken']['decimals']
            quote = get_lifi_quote(from_chain, from_token, int(amount * (10**decimals)), evm_addr)
            if quote.get('error'):
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nError: {quote['error']['message']}", parse_mode='Markdown')
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton('Home', callback_data='back_home'))
                bot.send_message(chat_id, f"```_\n_             [ HYPERFROG ]              _\n```\n\n", reply_markup=markup, parse_mode='Markdown')
                if user_id in state:
                    state.pop(user_id)
                return
            state[user_id]['bridge_evm_quote'] = quote
            text = (
                f"```_\n_             [ HYPERFROG ]              _\n```\n\n"
                f"{format_lifi_quote(quote)}\n\n"
                f"Confirm bridge?"
            )
            markup = types.InlineKeyboardMarkup(row_width=2)
            markup.add(
                types.InlineKeyboardButton('Powered by LiFi', callback_data='gluex')
            )
            markup.add(
                types.InlineKeyboardButton('Confirm', callback_data='bridge_evm_confirm'),
                types.InlineKeyboardButton('Cancel', callback_data='bridge_evm_cancel')
            )
            try:
                bot.delete_message(chat_id, message.message_id)
            except telegram.error.TelegramError:
                pass  # Silently handle if message is already deleted
            bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode='Markdown')
        elif waiting == 'hyperloop_initial_amount':
            try:
                amount = float(message.text)
                if amount <= 0:
                    raise ValueError("Amount must be positive")
                # Validate against token balance
                protocol = state[user_id].get('protocol')
                asset_addr = state[user_id].get('asset_addr')
                private_key, address = wallet_manager.get_evm_wallet(user_id)
                if protocol == 'hyperlend':
                    markets = state[user_id].get('hyperlend_markets', {})
                    m = markets.get(asset_addr, {})
                    decimals = m.get('decimals', 18)
                    positions = hyperlend.get_user_positions(private_key)
                    pos = positions['positions'].get(asset_addr, {})
                    max_balance = pos.get('walletBalance', 0)
                else:
                    reserves_list = state[user_id].get('hypurrfi_reserves', [])
                    r = next((rr for rr in reserves_list if rr['asset'] == asset_addr), {})
                    decimals = r.get('decimals', 18)
                    res = hypurrfi.get_user_reserve_data_full(address).get(asset_addr, {})
                    max_balance = res.get('supplied_balance', 0)
                if amount > max_balance:
                    symbol = m.get('symbol', 'Unknown') if protocol == 'hyperlend' else r.get('symbol', 'Unknown')
                    bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nInsufficient balance. Available: {max_balance:.2f} {symbol}.", parse_mode='Markdown')
                    show_asset_actions(chat_id, user_id, mid)
                    return
                amount_wei = int(amount * 10 ** decimals)
                # Placeholder for hyperloop logic
                text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nHyperloop is live on the back-end,\nbut cannot be initiated via telegram until risk adjusting params are added (very soon!)\n\nThankfrog🐸. "
                markup = types.InlineKeyboardMarkup(row_width=2)
                markup.add(types.InlineKeyboardButton('HOME', callback_data='back_home'),types.InlineKeyboardButton('Back', callback_data=f'explore_{protocol}'))
                try:
                    bot.delete_message(chat_id, message.message_id)
                except telegram.error.TelegramError:
                    pass  # Silently handle if message is already deleted
                bot.edit_message_text(text, chat_id, mid, reply_markup=markup, parse_mode='Markdown')
                if 'waiting' in state[user_id]:
                    del state[user_id]['waiting']
                if 'user_input_message_id' in state[user_id]:
                    del state[user_id]['user_input_message_id']
            except ValueError:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nInvalid number. Try again.", parse_mode='Markdown')
                show_asset_actions(chat_id, user_id, mid)
        elif waiting == 'stake_amount':
            try:
                amount = float(message.text)
                if amount <= 0:
                    raise ValueError("Amount must be positive")
                hype_balance = state[user_id].get('hype_balance', 0)
                if amount > hype_balance:
                    bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nInsufficient balance. Available: {hype_balance:.4f}.", parse_mode='Markdown')
                    prompt_for_stake_amount(chat_id, user_id, mid)
                    return
                bot.delete_message(chat_id, message.message_id)
                text = f"```_\n_             [ HYPERFROG ]              _\n```\n\nStaking..."
                bot.edit_message_text(text, chat_id, mid, parse_mode='Markdown')
                execute_stake(chat_id, user_id, mid, message.text)
            except ValueError:
                bot.reply_to(message, f"```_\n_             [ HYPERFROG ]              _\n```\n\nInvalid number. Try again.", parse_mode='Markdown')
                prompt_for_stake_amount(chat_id, user_id, mid)

if __name__ == '__main__':
    try:
        bot.infinity_polling()
    finally:
        db.close()  # Ensure database connection is closed on exit