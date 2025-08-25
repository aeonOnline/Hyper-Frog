import json
import time
from datetime import datetime
import sqlite3
from modules.token_map import TOKEN_MAP
import modules.hyperlend as hyperlend
import modules.hypurrfi as hypurrfi
from web3 import Web3
import os
from eth_account import Account
from web3.exceptions import ContractLogicError, Web3RPCError
from modules.gluex import get_swap_quote, execute_swap, gluex_get_exchange_rates
from modules.loopedhype import convert_to_loop_hype
from modules.wallet_manager import WalletDatabase, WalletManager
from modules.balance_manager import get_token_symbol

db = WalletDatabase()
wallet_manager = WalletManager(db)

web3 = Web3(Web3.HTTPProvider('https://hyperliquid.drpc.org'))
NATIVE_ADDRESS = '0x2222222222222222222222222222222222222222'
LOOPED_APY = 0.1  # Assumed % for looped HYPE
SAFETY_FACTOR = 0.8
MIN_HEALTH = 1.6
SWITCH_THRESHOLD = 1.0
GAS_MIN = 0.05
GAS_TARGET = 0.1
LOG_FILE = 'actions_log.json'
MAX_UINT256 = 2**256 - 1
ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}], "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}], "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

def ERC20(addr):
    return web3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)

def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r') as f:
            return json.load(f)
    return []

def append_log(action_dict):
    logs = load_log()
    logs.append(action_dict)
    with open(LOG_FILE, 'w') as f:
        json.dump(logs, f, indent=4)

def get_address(private_key):
    return Account.from_key(private_key).address

def check_network():
    try:
        web3.eth.get_block_number()
        return True
    except Web3RPCError as e:
        print(f"Network error: {e}")
        return False

def is_valid_contract(addr):
    try:
        code = web3.eth.get_code(addr)
        return len(code) > 0
    except:
        return False

def safe_call(func, max_retries=3):
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed after {max_retries} attempts: {e}")
                return None
            time.sleep(2 ** attempt)  # Exponential backoff
    return None

def fetch_all_data(private_key):
    if not check_network():
        raise Exception("Network connection failed")
    address = get_address(private_key)
    lend_markets = hyperlend.fetch_all_markets_combined()
    fi_reserves = hypurrfi.fetch_reserves()
    lend_positions = hyperlend.get_user_positions(private_key, include_wallet_balances=True, include_allowances=False)
    fi_portfolio = hypurrfi.get_full_user_portfolio(address)
    lend_account = lend_positions['account']
    fi_account = hypurrfi.get_user_account_data(address)
    # Filter tokens to only those in TOKEN_MAP
    token_addresses = set(TOKEN_MAP.values())
    filtered_lend_markets = {addr: data for addr, data in lend_markets.items() if addr in token_addresses}
    filtered_fi_reserves = [res for res in fi_reserves if res['asset'] in token_addresses]
    
    asset_data = {}
    for addr, data in filtered_lend_markets.items():
        asset_data[addr] = {
            'symbol': data['symbol'],
            'decimals': data['decimals'],
            'lend_supply_apy': data['liquidityRatePct'],
            'lend_borrow_apy': data['variableBorrowRatePct'],
            'ltv': data['baseLTVasCollateral'] / 10000,
            'collateral_enabled': data['usageAsCollateralEnabled'],
            'borrow_enabled': data['borrowingEnabled'],
            'liq_threshold': data.get('liquidationThreshold', data['baseLTVasCollateral'] / 10000 + 0.1)
        }
    for res in filtered_fi_reserves:
        addr = res['asset']
        if addr not in asset_data:
            asset_data[addr] = {'symbol': res['symbol'], 'decimals': res['decimals']}
        asset_data[addr]['fi_supply_apy'] = res['liquidity_rate_%']
        asset_data[addr]['fi_borrow_apy'] = res['variable_borrow_rate_%']
        asset_data[addr]['fi_ltv'] = 0.6
        asset_data[addr]['fi_liq_threshold'] = 0.8
        asset_data[addr]['fi_collateral_enabled'] = True
        asset_data[addr]['fi_borrow_enabled'] = True

    prices = {}
    for addr in asset_data:
        try:
            prices[addr] = gluex_get_exchange_rates(addr)
        except Exception:
            prices[addr] = 1.0  # Fallback for stables or errors
    prices[NATIVE_ADDRESS] = prices.get(TOKEN_MAP.get('WHYPE', next(iter(prices), None)), 1.0)

    balances = {}
    for symbol, addr in TOKEN_MAP.items():
        try:
            if addr == NATIVE_ADDRESS:
                balances[symbol] = safe_call(lambda: web3.eth.get_balance(address) / 10**18) or 0
            else:
                try:
                    contract = web3.eth.contract(address=Web3.to_checksum_address(addr), abi=ERC20_ABI)
                    dec = safe_call(contract.functions.decimals().call) or 18
                    balances[symbol] = safe_call(lambda: contract.functions.balanceOf(address).call() / 10**dec) or 0
                except ContractLogicError as e:
                    print(f"Error fetching balance for {symbol}: {e}")
                    balances[symbol] = 0
                except Exception as e:
                    print(f"Unexpected error for {symbol}: {e}")
                    balances[symbol] = 0
        except Exception as e:
            print(f"Error processing {symbol}: {e}")
            balances[symbol] = 0
    return {
        'address': address,
        'asset_data': asset_data,
        'prices': prices,
        'balances': balances,
        'lend_positions': lend_positions,
        'fi_portfolio': fi_portfolio,
        'lend_health': lend_account['healthFactorRaw'] / 10**18 if 'healthFactorRaw' in lend_account else fi_account['health_factor'],
        'fi_health': fi_account['health_factor']
    }

def classify_groups(asset_data):
    hype_symbols = ['HYPE', 'WHYPE', 'wstHYPE', 'kHYPE', 'LHYPE']
    stable_symbols = ['USDe', 'USD₮0', 'sUSDe', 'USDHL', 'USR', 'feUSD', 'USDXL']
    volatile_symbols = []
    groups = {
        'hype': [addr for addr, d in asset_data.items() if d['symbol'] in hype_symbols],
        'stable': [addr for addr, d in asset_data.items() if d['symbol'] in stable_symbols],
        'volatile': [addr for addr, d in asset_data.items() if d['symbol'] in volatile_symbols]
    }
    gas_priority_hype = ['WHYPE', 'wstHYPE', 'kHYPE', 'LHYPE']
    return groups, [TOKEN_MAP[s] for s in gas_priority_hype if s in TOKEN_MAP]

def calculate_current_apy(group, data):
    prices = data['prices']
    balances = data['balances']
    lend_pos = data['lend_positions']['positions']
    fi_pos = {t['symbol']: t for t in data['fi_portfolio']['tokens']}
    equity = 0
    net_yield = 0
    for addr in group:
        symbol = data['asset_data'][addr]['symbol']
        price = prices.get(addr, 1.0)
        wallet_val = balances.get(symbol, 0) * price
        equity += wallet_val
        if addr in lend_pos:
            pos = lend_pos[addr]
            sup_val = pos['supplied'] * price
            debt_val = pos['variableDebt'] * price
            equity += sup_val - debt_val
            net_yield += (pos['market_liquidityRatePct'] / 100 * sup_val) - (pos['market_variableBorrowRatePct'] / 100 * debt_val)
        if symbol in fi_pos:
            pos = fi_pos[symbol]
            sup_val = float(pos['supplied']) * price
            debt_val = float(pos['borrowed']) * price
            equity += sup_val - debt_val
            s_apy = data['asset_data'][addr].get('fi_supply_apy', 0) / 100
            b_apy = data['asset_data'][addr].get('fi_borrow_apy', 0) / 100
            net_yield += s_apy * sup_val - b_apy * debt_val
    apy = (net_yield / equity * 100) if equity > 0 else 0
    return apy, equity

def calculate_potential_strategies(group, data, is_hype=False):
    strategies = []
    for protocol in ['lend', 'fi']:
        for addr in group:
            if protocol == 'lend':
                s_apy = data['asset_data'][addr].get('lend_supply_apy', 0)
            else:
                s_apy = data['asset_data'][addr].get('fi_supply_apy', 0)
            strategies.append({'type': 'unleveraged', 'protocol': protocol, 'supply_asset': addr, 'borrow_asset': None, 'apy': s_apy, 'health': float('inf')})
    for protocol in ['lend', 'fi']:
        for s_addr in group:
            for b_addr in group:
                if data['asset_data'][s_addr].get(protocol + '_collateral_enabled', False) and data['asset_data'][b_addr].get(protocol + '_borrow_enabled', False):
                    ltv = data['asset_data'][s_addr].get(protocol + '_ltv' if protocol=='fi' else 'ltv', 0)
                    eff_ltv = SAFETY_FACTOR * ltv
                    liq_th = data['asset_data'][s_addr].get(protocol + '_liq_threshold' if protocol=='fi' else 'liq_threshold', 0)
                    s_apy = data['asset_data'][s_addr].get(protocol + '_supply_apy', 0)
                    b_apy = data['asset_data'][b_addr].get(protocol + '_borrow_apy', 0)
                    if eff_ltv > 0:
                        lev_apy = max(0, (s_apy - b_apy * eff_ltv) / (1 - eff_ltv))
                        est_health = liq_th / eff_ltv if eff_ltv > 0 else float('inf')
                        if est_health >= MIN_HEALTH:
                            strategies.append({'type': 'leveraged', 'protocol': protocol, 'supply_asset': s_addr, 'borrow_asset': b_addr, 'apy': lev_apy, 'health': est_health})
    strategies.append({'type': 'hold', 'protocol': None, 'supply_asset': None, 'borrow_asset': None, 'apy': 0, 'health': float('inf')})
    if is_hype:
        strategies.append({'type': 'looped', 'protocol': None, 'supply_asset': NATIVE_ADDRESS, 'borrow_asset': None, 'apy': LOOPED_APY, 'health': float('inf')})
    best = max(strategies, key=lambda x: x['apy'])
    return best, strategies

def generate_actions(group, best_strategy, data, equity):
    actions = []
    withdrawn_amounts = {}
    for protocol in ['lend', 'fi']:
        if protocol == 'lend':
            pos = data['lend_positions']['positions']
            for addr in group:
                if addr in pos:
                    p = pos[addr]
                    if p['variableDebt_raw'] > 0:
                        actions.append({'type': 'repay', 'protocol': 'lend', 'asset': addr, 'amount': None})
                    if p['supplied_raw'] > 0:
                        amount_wei = int(p['supplied_raw'])
                        actions.append({'type': 'withdraw', 'protocol': 'lend', 'asset': addr, 'amount': None})
                        withdrawn_amounts[addr] = amount_wei
        else:
            fi_pos = {t['symbol']: t for t in data['fi_portfolio']['tokens']}
            for addr in group:
                symbol = data['asset_data'][addr]['symbol']
                if symbol in fi_pos:
                    p = fi_pos[symbol]
                    if float(p['raw_borrowed']) > 0:
                        actions.append({'type': 'repay', 'protocol': 'fi', 'asset': addr, 'amount': None})
                    if float(p['raw_supplied']) > 0:
                        amount_wei = int(float(p['raw_supplied']))
                        actions.append({'type': 'withdraw', 'protocol': 'fi', 'asset': addr, 'amount': None})
                        withdrawn_amounts[addr] = amount_wei
    target_asset = best_strategy['supply_asset'] if best_strategy['type'] != 'hold' else None
    if best_strategy['type'] == 'looped':
        target_asset = NATIVE_ADDRESS
    if target_asset:
        for symbol, bal in data['balances'].items():
            addr = TOKEN_MAP[symbol]
            if addr in group and bal > 0 and addr != target_asset:
                amount_wei = int(bal * 10**data['asset_data'][addr]['decimals'])
                actions.append({'type': 'swap', 'from': addr, 'to': target_asset, 'amount': amount_wei})
        for addr, amount_wei in withdrawn_amounts.items():
            if addr != target_asset and amount_wei > 0:
                actions.append({'type': 'swap', 'from': addr, 'to': target_asset, 'amount': amount_wei})
    if best_strategy['type'] in ['unleveraged', 'leveraged']:
        total_wei = int(equity / data['prices'].get(target_asset, 1.0) * 10**data['asset_data'][target_asset]['decimals'])
        if total_wei > 0:
            actions.append({'type': 'supply', 'protocol': best_strategy['protocol'], 'asset': target_asset, 'amount': total_wei})
        if best_strategy['type'] == 'leveraged':
            borrow_asset = best_strategy['borrow_asset']
            eff_ltv = SAFETY_FACTOR * data['asset_data'][target_asset].get(best_strategy['protocol'] + '_ltv' if best_strategy['protocol']=='fi' else 'ltv', 0)
            for _ in range(2):
                supply_value_usd = (total_wei / 10**data['asset_data'][target_asset]['decimals']) * data['prices'].get(target_asset, 1.0)
                borrow_value_usd = supply_value_usd * eff_ltv / 2  # Adjusted to 2 for safer leverage
                borrow_amount = int((borrow_value_usd / data['prices'].get(borrow_asset, 1.0)) * 10**data['asset_data'][borrow_asset]['decimals'])
                if borrow_amount > 0:
                    actions.append({'type': 'borrow', 'protocol': best_strategy['protocol'], 'asset': borrow_asset, 'amount': borrow_amount})
                    if borrow_asset != target_asset:
                        actions.append({'type': 'swap', 'from': borrow_asset, 'to': target_asset, 'amount': borrow_amount})
                    actions.append({'type': 'supply', 'protocol': best_strategy['protocol'], 'asset': target_asset, 'amount': borrow_amount})
    elif best_strategy['type'] == 'looped':
        amount_wei = int(data['balances']['HYPE'] * 10**18)
        if amount_wei > 0:
            actions.append({'type': 'convert_looped', 'amount': amount_wei})
    return actions

def manage_gas(private_key, data, gas_priority):
    address = data['address']
    native_bal = data['balances']['HYPE']
    if native_bal >= GAS_MIN:
        return []
    actions = []
    usd_needed = 1.0
    hype_price = data['prices'].get(NATIVE_ADDRESS, 1.0)
    hype_needed = (GAS_TARGET - native_bal) + (usd_needed / hype_price)
    for addr in gas_priority:
        symbol = data['asset_data'][addr]['symbol']
        bal = data['balances'][symbol]
        if bal > 0:
            price_from = data['prices'].get(addr, 1.0)
            amount_from = min(bal, hype_needed * hype_price / price_from)
            amount_wei = int(amount_from * 10**data['asset_data'][addr]['decimals'])
            actions.append({'type': 'swap', 'from': addr, 'to': NATIVE_ADDRESS, 'amount': amount_wei})
            break
    return actions

def make_decision(private_key, yield_hype, yield_stables):
    data = fetch_all_data(private_key)
    groups, gas_priority = classify_groups(data['asset_data'])
    decision = {'reasoning': {}, 'actions': []}
    gas_actions = manage_gas(private_key, data, gas_priority)
    decision['actions'].extend(gas_actions)
    group_flags = {'hype': yield_hype, 'stable': yield_stables, 'volatile': False}
    for g_name, group in groups.items():
        if not group or not group_flags.get(g_name, False):
            continue
        current_apy, equity = calculate_current_apy(group, data)
        is_hype = g_name == 'hype'
        best_strategy, all_strats = calculate_potential_strategies(group, data, is_hype)
        best_apy = best_strategy['apy']
        reasoning = {
            'group': g_name,
            'current_apy': current_apy,
            'best_apy': best_apy,
            'best_strategy': best_strategy,
            'all_strategies': all_strats,
            'health_lend': data['lend_health'],
            'health_fi': data['fi_health'],
            'worth_switch': best_apy > current_apy + SWITCH_THRESHOLD
        }
        decision['reasoning'][g_name] = reasoning
        if reasoning['worth_switch']:
            group_actions = generate_actions(group, best_strategy, data, equity)
            decision['actions'].extend(group_actions)
        elif g_name == 'hype' and best_strategy['type'] == 'unleveraged' and best_strategy['supply_asset']:
            symbol = data['asset_data'][best_strategy['supply_asset']]['symbol']
            bal = data['balances'].get(symbol, 0)
            if bal > 0:
                amount_wei = int(bal * 10**data['asset_data'][best_strategy['supply_asset']]['decimals'])
                decision['actions'].append({
                    'type': 'supply',
                    'protocol': best_strategy['protocol'],
                    'asset': best_strategy['supply_asset'],
                    'amount': amount_wei
                })
    return decision

def execute(private_key, actions):
    data = fetch_all_data(private_key)
    groups, gas_priority = classify_groups(data['asset_data'])
    address = get_address(private_key)
    for act in actions:
        tx_hash = None
        now = datetime.now().isoformat()
        try:
            if act['type'] == 'supply':
                if act['protocol'] == 'lend':
                    tx_hash = hyperlend.supply_with_approve(private_key, act['asset'], act['amount'], approve_infinite=True)
                else:
                    tx_hash = hypurrfi.supply(act['asset'], act['amount'], address, private_key)
            elif act['type'] == 'withdraw':
                amount = act['amount'] if act['amount'] is not None else MAX_UINT256
                if act['protocol'] == 'lend':
                    tx_hash = hyperlend.withdraw(private_key, act['asset'], amount)
                else:
                    tx_hash = hypurrfi.withdraw(act['asset'], amount, address, private_key)
            elif act['type'] == 'borrow':
                if act['protocol'] == 'lend':
                    tx_hash = hyperlend.borrow(private_key, act['asset'], act['amount'])
                else:
                    tx_hash = hypurrfi.borrow(act['asset'], act['amount'], address, private_key)
            elif act['type'] == 'repay':
                if act['protocol'] == 'lend':
                    pos = hyperlend.get_user_positions(private_key)['positions'].get(act['asset'], {})
                    debt = pos.get('variableDebt_raw', 0)
                else:
                    pos = hypurrfi.get_user_reserve_data(address, act['asset'])
                    debt = int(float(pos.get('variable_debt', 0)) * 10**data['asset_data'][act['asset']]['decimals'])
                wallet_bal = safe_call(lambda: web3.eth.get_balance(address) if act['asset'] == NATIVE_ADDRESS else ERC20(act['asset']).functions.balanceOf(address).call()) or 0
                if wallet_bal < debt and debt > 0:
                    extra_needed = debt - wallet_bal
                    group = [g for g in groups.values() if act['asset'] in g][0]
                    from_addr = next((a for a in group if a != act['asset'] and data['balances'][data['asset_data'][a]['symbol']] > 0), group[0])
                    extra_wei = extra_needed
                    quote = get_swap_quote(from_addr, act['asset'], extra_wei + int(extra_wei * 0.01), address)
                    tx_hash = execute_swap(quote['result'], address, private_key)
                    append_log({'timestamp': now, 'type': 'swap_for_repay', 'tx_hash': tx_hash, 'details': {'from': from_addr, 'to': act['asset'], 'amount': extra_wei}})
                    time.sleep(10)
                if act['protocol'] == 'lend':
                    tx_hash = hyperlend.repay_with_approve(private_key, act['asset'], debt, approve_infinite=True)
                else:
                    tx_hash = hypurrfi.repay(act['asset'], debt, address, private_key)
            elif act['type'] == 'swap':
                quote = get_swap_quote(act['from'], act['to'], act['amount'], address)
                tx_hash = execute_swap(quote['result'], address, private_key)
            elif act['type'] == 'convert_looped':
                tx_hash = convert_to_loop_hype(private_key, act['amount'])
            if tx_hash:
                append_log({'timestamp': now, 'type': act['type'], 'tx_hash': tx_hash, 'details': act})
            data = fetch_all_data(private_key)
            gas_actions = manage_gas(private_key, data, gas_priority)
            for g_act in gas_actions:
                quote = get_swap_quote(g_act['from'], g_act['to'], g_act['amount'], address)
                g_tx = execute_swap(quote['result'], address, private_key)
                append_log({'timestamp': now, 'type': 'gas_swap', 'tx_hash': g_tx, 'details': g_act})
                time.sleep(10)
            time.sleep(10)
        except Exception as e:
            print(f"Error executing {act['type']}: {e}")
            append_log({'timestamp': now, 'type': 'error', 'details': act, 'error': str(e)})

def get_users():
    conn = sqlite3.connect('wallets.db')
    c = conn.cursor()
    c.execute("SELECT user_id, yield_hype, yield_stables FROM wallets")
    users = c.fetchall()
    conn.close()
    return users

def store_decision(user_id, decision):
    conn = sqlite3.connect('decisions.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS decisions
                 (user_id TEXT PRIMARY KEY, decisions TEXT)''')
    json_str = json.dumps(decision)
    c.execute("INSERT OR REPLACE INTO decisions (user_id, decisions) VALUES (?, ?)", (user_id, json_str))
    conn.commit()
    conn.close()

def process_all_users():
    users = get_users()
    for user_id, yield_hype, yield_stables in users:
        if not yield_hype and not yield_stables:
            continue
        private_key, pubkey = wallet_manager.get_evm_wallet(user_id)
        decision = make_decision(private_key, yield_hype, yield_stables)
        store_decision(user_id, decision)
        execute(private_key, decision['actions'])

if __name__ == "__main__":
    # For testing with one user_id
    test_user_id = ""  # Replace with actual user_id for testing
    execute_flag = False  # Set to True to execute actions during testing; False to only compute and print/store decisions
    conn = sqlite3.connect('wallets.db')
    c = conn.cursor()
    c.execute("SELECT yield_hype, yield_stables FROM wallets WHERE user_id = ?", (test_user_id,))
    result = c.fetchone()
    conn.close()
    if result:
        yield_hype, yield_stables = result
        private_key, pubkey = wallet_manager.get_evm_wallet(test_user_id)
        decision = make_decision(private_key, yield_hype, yield_stables)
        print(json.dumps(decision, indent=4))
        store_decision(test_user_id, decision)
        if execute_flag:
            execute(private_key, decision['actions'])
    else:
        print(f"No data found for user_id: {test_user_id}")

    # For production, uncomment the loop:
    # while True:
    #     process_all_users()
    #     time.sleep(3600)  # Run every hour
