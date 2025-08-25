
"""
HyperLend helper module.
- Fetch markets (API + onchain)
- Get detailed user positions (per reserve)
- Basic interactions (approve, supply, borrow, repay, withdraw)
- Simple (non-atomic) hyper-loop function that supplies, borrows and re-supplies
"""

import time
import requests
from web3 import Web3
from eth_account import Account
from eth_utils import to_checksum_address


RPC_URL = "https://rpc.hyperliquid.xyz/evm"
CHAIN_ID = 999
API_BASE = "https://api.hyperlend.finance"


POOL_ADDRESS = "0x00A89d7a5A02160f20150EbEA7a2b5E4879A1A8b"
PROTOCOL_DATA_PROVIDER = "0x5481bf8d3946E6A3168640c1D7523eB59F055a29"
UI_POOL_DATA_PROVIDER = "0x3Bb92CF81E38484183cc96a4Fb8fBd2d73535807"
MAX_UINT256 = 2**256 - 1

w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    raise RuntimeError(f"Cannot connect to RPC {RPC_URL}")

POOL_ABI = [
    {"inputs":[{"internalType":"address","name":"asset","type":"address"},
               {"internalType":"uint256","name":"amount","type":"uint256"},
               {"internalType":"address","name":"onBehalfOf","type":"address"},
               {"internalType":"uint16","name":"referralCode","type":"uint16"}],
     "name":"supply","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"asset","type":"address"},
               {"internalType":"uint256","name":"amount","type":"uint256"},
               {"internalType":"address","name":"to","type":"address"}],
     "name":"withdraw","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
     "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"asset","type":"address"},
               {"internalType":"uint256","name":"amount","type":"uint256"},
               {"internalType":"uint256","name":"interestRateMode","type":"uint256"},
               {"internalType":"uint16","name":"referralCode","type":"uint16"},
               {"internalType":"address","name":"onBehalfOf","type":"address"}],
     "name":"borrow","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"asset","type":"address"},
               {"internalType":"uint256","name":"amount","type":"uint256"},
               {"internalType":"uint256","name":"interestRateMode","type":"uint256"},
               {"internalType":"address","name":"onBehalfOf","type":"address"}],
     "name":"repay","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],
     "stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"internalType":"address","name":"user","type":"address"}],
     "name":"getUserAccountData",
     "outputs":[
         {"internalType":"uint256","name":"totalCollateralBase","type":"uint256"},
         {"internalType":"uint256","name":"totalDebtBase","type":"uint256"},
         {"internalType":"uint256","name":"availableBorrowsBase","type":"uint256"},
         {"internalType":"uint256","name":"currentLiquidationThreshold","type":"uint256"},
         {"internalType":"uint256","name":"ltv","type":"uint256"},
         {"internalType":"uint256","name":"healthFactor","type":"uint256"}],
     "stateMutability":"view","type":"function"}
]

PROTOCOL_DATA_PROVIDER_ABI = [
    {
        "inputs":[],
        "name":"getAllReservesTokens",
        "outputs":[
            {
                "components":[
                    {"internalType":"string","name":"symbol","type":"string"},
                    {"internalType":"address","name":"tokenAddress","type":"address"}
                ],
                "internalType":"struct IProtocolDataProvider.TokenData[]",
                "name":"",
                "type":"tuple[]"
            }
        ],
        "stateMutability":"view",
        "type":"function"
    },
    {
        "inputs":[
            {"internalType":"address","name":"asset","type":"address"},
            {"internalType":"address","name":"user","type":"address"}
        ],
        "name":"getUserReserveData",
        "outputs":[
            {"internalType":"uint256","name":"currentATokenBalance","type":"uint256"},
            {"internalType":"uint256","name":"currentStableDebt","type":"uint256"},
            {"internalType":"uint256","name":"currentVariableDebt","type":"uint256"},
            {"internalType":"uint256","name":"principalStableDebt","type":"uint256"},
            {"internalType":"uint256","name":"scaledVariableDebt","type":"uint256"},
            {"internalType":"uint256","name":"stableBorrowRate","type":"uint256"},
            {"internalType":"uint256","name":"liquidityRate","type":"uint256"},
            {"internalType":"uint40","name":"stableRateLastUpdated","type":"uint40"},
            {"internalType":"bool","name":"usageAsCollateralEnabled","type":"bool"}
        ],
        "stateMutability":"view",
        "type":"function"
    },
]

ERC20_ABI = [
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

# Instances
pool = w3.eth.contract(address=to_checksum_address(POOL_ADDRESS), abi=POOL_ABI)
pdata = w3.eth.contract(address=to_checksum_address(PROTOCOL_DATA_PROVIDER), abi=PROTOCOL_DATA_PROVIDER_ABI)

RAY = 10**27
WAD = 10**18
MAX_UINT = 2**256 - 1

def ray_to_percent(ray_val):
    if isinstance(ray_val, str):
        ray_val = int(ray_val)
    return (ray_val / RAY) * 100.0

def wei_to_amount(amount_wei, decimals):
    if decimals is None:
        decimals = 18
    return amount_wei / (10 ** decimals)

def to_wei(amount, decimals):
    return int(amount * (10 ** decimals))

def erc20(token_addr):
    return w3.eth.contract(address=to_checksum_address(token_addr), abi=ERC20_ABI)

def token_decimals(token_addr, default=18):
    try:
        return erc20(token_addr).functions.decimals().call()
    except Exception:
        return default

def token_symbol(token_addr):
    try:
        return erc20(token_addr).functions.symbol().call()
    except Exception:
        return "TOKEN"

def wallet_balance(token_addr, owner):
    try:
        return erc20(token_addr).functions.balanceOf(to_checksum_address(owner)).call()
    except Exception:
        return 0

def allowance(token_addr, owner, spender):
    try:
        return erc20(token_addr).functions.allowance(to_checksum_address(owner), to_checksum_address(spender)).call()
    except Exception:
        return 0

def approve_erc20(private_key, token_addr, spender, amount_wei):
    acct = Account.from_key(private_key)
    token = erc20(token_addr)
    tx = token.functions.approve(to_checksum_address(spender), int(amount_wei)).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, 'pending'),
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID
    })
    # let node set gas (or estimate)
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()

def ensure_allowance(private_key, token_addr, spender, min_needed_wei, approve_infinite=True):

    acct = Account.from_key(private_key)
    current = allowance(token_addr, acct.address, spender)
    if current >= int(min_needed_wei):
        return (False, None)
    amt = MAX_UINT if approve_infinite else int(min_needed_wei)
    h = approve_erc20(private_key, token_addr, spender, amt)
    return (True, h)

# ----------------------------
# API: markets & rates
# ----------------------------
def fetch_markets_api():

    url = f"{API_BASE}/data/markets"
    r = requests.get(url, params={"chain":"hyperEvm"}, timeout=2)
    r.raise_for_status()
    obj = r.json()
    reserves = obj.get("reserves", [])
    out = []
    for rsv in reserves:
        lr = int(rsv.get("liquidityRate") or 0)
        vbr = int(rsv.get("variableBorrowRate") or 0)
        entry = {
            "underlyingAsset": to_checksum_address(rsv.get("underlyingAsset")),
            "name": rsv.get("name"),
            "symbol": rsv.get("symbol"),
            "decimals": int(rsv.get("decimals") or 18),
            "baseLTVasCollateral": int(rsv.get("baseLTVasCollateral") or 0),
            "liquidityRate": lr,
            "liquidityRatePct": ray_to_percent(lr),
            "variableBorrowRate": vbr,
            "variableBorrowRatePct": ray_to_percent(vbr),
            "availableLiquidity": int(rsv.get("availableLiquidity") or 0),
            "borrowingEnabled": bool(rsv.get("borrowingEnabled")),
            "usageAsCollateralEnabled": bool(rsv.get("usageAsCollateralEnabled")),
            "aTokenAddress": rsv.get("aTokenAddress"),
            "variableDebtTokenAddress": rsv.get("variableDebtTokenAddress"),
            "stableDebtTokenAddress": rsv.get("stableDebtTokenAddress")
        }
        out.append(entry)
    return out

def fetch_reserves_onchain():
    try:
        raw = pdata.functions.getAllReservesTokens().call()
    except Exception:
        return []
    out = []
    for t in raw:
        sym = t[0]
        addr = to_checksum_address(t[1])
        out.append({"symbol": sym, "underlyingAsset": addr})
    return out

def fetch_all_markets_combined():
    api = fetch_markets_api()
    onchain = fetch_reserves_onchain()
    store = {}
    for m in api:
        store[m["underlyingAsset"]] = m
    for o in onchain:
        addr = o["underlyingAsset"]
        if addr not in store:
            store[addr] = {
                "underlyingAsset": addr,
                "symbol": o.get("symbol"),
                "decimals": 18,
                "liquidityRate": 0,
                "liquidityRatePct": 0.0,
                "variableBorrowRate": 0,
                "variableBorrowRatePct": 0.0,
                "availableLiquidity": 0,
                "borrowingEnabled": False,
                "usageAsCollateralEnabled": False,
                "name": o.get("symbol") or addr[:8]
            }
    return store
def normalize_txhash(txh):

    if txh is None:
        return None

    if isinstance(txh, bytes):
        return "0x" + txh.hex()
    if isinstance(txh, str):
        return txh if txh.startswith("0x") else "0x" + txh
    return txh

def wait_for_tx(txh, timeout=2):

    txh_n = normalize_txhash(txh)
    if not txh_n:
        raise RuntimeError("No tx hash provided to wait_for_tx()")
    try:

        receipt = w3.eth.wait_for_transaction_receipt(txh_n, timeout=timeout)
        return receipt
    except Exception as e:
        raise RuntimeError(f"Waiting for tx {txh_n} failed: {e}")
    


def supply_with_approve(private_key, asset, amount_wei, on_behalf=None, approve_infinite=True):
    acct = Account.from_key(private_key)

    m = fetch_all_markets_combined().get(to_checksum_address(asset))
    if m and not m.get("usageAsCollateralEnabled", True):
        raise RuntimeError("This asset is not enabled as collateral (usageAsCollateralEnabled=false).")


    bal = wallet_balance(asset, acct.address)
    if bal < int(amount_wei):
        raise RuntimeError(f"Insufficient wallet balance for supply. balance={bal}, need={int(amount_wei)}")


    approved, txh = ensure_allowance(private_key, asset, POOL_ADDRESS, int(amount_wei), approve_infinite=approve_infinite)
    if txh:

        wait_for_tx(txh, timeout=2)

    h = supply(private_key, asset, amount_wei, on_behalf=on_behalf)
    wait_for_tx(h, timeout=2)
    return h



def list_markets():
    mk = fetch_markets_api()
    mk.sort(key=lambda x: x["liquidityRatePct"], reverse=True)
    return mk

def best_supply_markets(top=5):
    mk = list_markets()
    return mk[:top]

def best_borrow_markets(top=5):
    mk = fetch_markets_api()
    mk.sort(key=lambda x: x["variableBorrowRatePct"])  # cheapest first
    return mk[:top]


def get_user_positions(private_key, include_wallet_balances=True, include_allowances=True):
    acct = Account.from_key(private_key)
    address = acct.address
    markets = fetch_all_markets_combined()
    positions = {}
    for asset_addr, m in markets.items():
        try:
            data = pdata.functions.getUserReserveData(asset_addr, address).call()
            # (aBal, stableDebt, varDebt, principalStableDebt, scaledVarDebt, stableBorrowRate, liquidityRate, stableRateLastUpdated, usageAsCollateralEnabled)
            a_bal = int(data[0])
            st_debt = int(data[1])
            var_debt = int(data[2])
            usage_flag = bool(data[8])
            decimals = m.get("decimals", 18)

            entry = {
                "symbol": m.get("symbol"),
                "name": m.get("name"),
                "decimals": decimals,
                "supplied_raw": a_bal,
                "supplied": wei_to_amount(a_bal, decimals),
                "stableDebt_raw": st_debt,
                "stableDebt": wei_to_amount(st_debt, decimals),
                "variableDebt_raw": var_debt,
                "variableDebt": wei_to_amount(var_debt, decimals),
                "usageAsCollateralEnabled": usage_flag,
                "market_liquidityRatePct": m.get("liquidityRatePct"),
                "market_variableBorrowRatePct": m.get("variableBorrowRatePct"),
                "market_availableLiquidity": m.get("availableLiquidity"),
            }

            if include_wallet_balances:
                entry["walletBalance_raw"] = wallet_balance(asset_addr, address)
                entry["walletBalance"] = wei_to_amount(entry["walletBalance_raw"], decimals)

            if include_allowances:
                entry["allowanceToPool_raw"] = allowance(asset_addr, address, POOL_ADDRESS)

            positions[asset_addr] = entry

        except Exception:
            # skip reserves that revert
            continue

    try:
        ac = pool.functions.getUserAccountData(address).call()
        acct_summary = {
            "totalCollateralBase": ac[0],
            "totalDebtBase": ac[1],
            "availableBorrowsBase": ac[2],
            "currentLiquidationThreshold": ac[3],
            "ltv": ac[4],
            "healthFactorRaw": ac[5],
        }
    except Exception as e:
        acct_summary = {"error": str(e)}

    return {"address": address, "positions": positions, "account": acct_summary}


def supply(private_key, asset, amount_wei, on_behalf=None):
    acct = Account.from_key(private_key)
    if on_behalf is None:
        on_behalf = acct.address
    tx = pool.functions.supply(to_checksum_address(asset), int(amount_wei), to_checksum_address(on_behalf), 0).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, 'pending'),
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()

def supply_with_approve(private_key, asset, amount_wei, on_behalf=None, approve_infinite=True):
    acct = Account.from_key(private_key)
    # Preflight: market flags
    m = fetch_all_markets_combined().get(to_checksum_address(asset))
    if m and not m.get("usageAsCollateralEnabled", True):
        raise RuntimeError("This asset is not enabled as collateral (usageAsCollateralEnabled=false).")

    # Preflight: balance
    bal = wallet_balance(asset, acct.address)
    if bal < int(amount_wei):
        raise RuntimeError(f"Insufficient wallet balance for supply. balance={bal}, need={int(amount_wei)}")

    # Ensure allowance
    _, txh = ensure_allowance(private_key, asset, POOL_ADDRESS, int(amount_wei), approve_infinite=approve_infinite)
    if txh:
        # small delay for allowance to propagate (optional)
        time.sleep(1.0)
    return supply(private_key, asset, amount_wei, on_behalf=on_behalf)

def withdraw(private_key, asset, amount_wei, to_addr=None):
    acct = Account.from_key(private_key)
    if to_addr is None:
        to_addr = acct.address
    tx = pool.functions.withdraw(to_checksum_address(asset), int(amount_wei), to_checksum_address(to_addr)).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, 'pending'),
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()

def borrow(private_key, asset, amount_wei, interest_mode=2, on_behalf=None):
    acct = Account.from_key(private_key)
    if on_behalf is None:
        on_behalf = acct.address
    m = fetch_all_markets_combined().get(to_checksum_address(asset))
    if m and not m.get("borrowingEnabled", True):
        raise RuntimeError("Borrowing disabled for this asset (borrowingEnabled=false).")
    tx = pool.functions.borrow(to_checksum_address(asset), int(amount_wei), int(interest_mode), 0, to_checksum_address(on_behalf)).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, 'pending'),
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return tx_hash.hex()

def repay(private_key, asset, amount_wei, interest_mode=2, on_behalf=None):
    acct = Account.from_key(private_key)
    if on_behalf is None:
        on_behalf = acct.address
    if amount_wei is None:
        amount_wei = MAX_UINT256
    tx = pool.functions.repay(to_checksum_address(asset), int(amount_wei), int(interest_mode), to_checksum_address(on_behalf)).build_transaction({
        "from": acct.address,
        "nonce": w3.eth.get_transaction_count(acct.address, 'pending'),
        "gasPrice": w3.eth.gas_price,
        "chainId": CHAIN_ID
    })
    signed = acct.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return str(tx_hash.hex())

def repay_with_approve(private_key, asset, amount_wei, interest_mode=2, on_behalf=None, approve_infinite=True):
    acct = Account.from_key(private_key)
    bal = wallet_balance(asset, acct.address)
    if amount_wei is None:
        amount_wei = MAX_UINT256
    _, txh = ensure_allowance(private_key, asset, POOL_ADDRESS, int(amount_wei), approve_infinite=approve_infinite)
    if txh:
        time.sleep(1.0)
    return repay(private_key, asset, amount_wei, interest_mode=interest_mode, on_behalf=on_behalf)

def hyperloop_simple(private_key, supply_asset, initial_supply_amount_wei, borrow_asset, borrow_amount_per_loop_wei, loops=1, approve_infinite=True):
    acct = Account.from_key(private_key)
    txs = []

    markets = fetch_all_markets_combined()
    s_m = markets.get(to_checksum_address(supply_asset))
    b_m = markets.get(to_checksum_address(borrow_asset))
    if s_m and not s_m.get("usageAsCollateralEnabled"):
        return ("Supply asset not allowed as collateral (usageAsCollateralEnabled=false).")
    if b_m and not b_m.get("borrowingEnabled"):
        return ("Borrow asset borrowingEnabled=false.")

    h_supply0 = supply_with_approve(private_key, supply_asset, int(initial_supply_amount_wei), approve_infinite=approve_infinite)
    txs.append(("supply_initial", h_supply0))

    if supply_asset.lower() != borrow_asset.lower():
        approved, h = ensure_allowance(private_key, borrow_asset, POOL_ADDRESS, int(borrow_amount_per_loop_wei), approve_infinite=approve_infinite)
        if approved and h:
            wait_for_tx(h, timeout=4)
            txs.append(("approve_borrow_asset", h))


    for i in range(loops):
        h_b = borrow(private_key, borrow_asset, int(borrow_amount_per_loop_wei), interest_mode=2)
        txs.append((f"borrow_loop_{i+1}", h_b))
        wait_for_tx(h_b, timeout=4)

        h_s = supply(private_key, borrow_asset, int(borrow_amount_per_loop_wei))
        txs.append((f"supply_back_loop_{i+1}", h_s))
        wait_for_tx(h_s, timeout=4)

    return txs


if __name__ == "__main__":
    PRIVATE_KEY = ""

    acct = Account.from_key(PRIVATE_KEY)
    print("Address:", acct.address)

    WHYPE = "0x5555555555555555555555555555555555555555"
    KHYPE = "0xfD739d4e423301CE9385c1fb8850539D657C296D"
    UBTC = "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463"
    MAX_UINT256 = 2**256 - 1
    dec = token_decimals(WHYPE)
    # amt = int(0.02 * (10**dec))

    # Safe supply (auto-approve if needed)
    # txh = supply_with_approve(PRIVATE_KEY, WHYPE, amt, approve_infinite=True)
    # txh = withdraw(PRIVATE_KEY, KHYPE, MAX_UINT256)
    # txh = repay_with_approve(PRIVATE_KEY,WHYPE,MAX_UINT256)
    # print("tx withdraw:", txh)

    # Fetch user positions (detailed)
    # user_info = get_user_positions(PRIVATE_KEY)
    # print("Account summary (raw):", user_info["account"])
    # for addr, p in list(user_info["positions"].items()):
    #     print(f"Pos: {p['symbol']} supplied:{p['supplied']} varDebt:{p['variableDebt']} stableDebt:{p['stableDebt']}")

    # # # Health factor quick view
    # hf_raw = user_info["account"].get("healthFactorRaw")
    # print("Health factor raw:", hf_raw if hf_raw else "n/a")
    # g = fetch_all_markets_combined()
    # for i in g.keys():
    #     print(g[i])
    # print(txh)
    # print()
    # Example hyperloop (COMMENT OUT until you understand risks!)
    # dec = token_decimals(UBTC)
    # print(dec)
    # print(get_user_positions(PRIVATE_KEY))
    # txs = hyperloop_simple(PRIVATE_KEY, WHYPE, amt, UBTC, int(0.00001 * (10**dec)), loops=2)
    # txs = borrow(PRIVATE_KEY, UBTC, int(0.00001 * (10**dec)), interest_mode=2)
    # print(txs)
    # user_info = get_user_positions(PRIVATE_KEY)
    # print("Account summary (raw):", user_info["account"])
    # for addr, p in list(user_info["positions"].items()):
    #     print(f"Pos: {p['symbol']} supplied:{p['supplied']} varDebt:{p['variableDebt']} stableDebt:{p['stableDebt']}")

    # # # Health factor quick view
    # hf_raw = user_info["account"].get("healthFactorRaw")
    # print("Health factor raw:", hf_raw if hf_raw else "n/a")