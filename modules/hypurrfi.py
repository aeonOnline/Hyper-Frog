import json
from decimal import Decimal
from web3 import Web3
import time


RPC_URL = "https://rpc.hyperliquid.xyz/evm"
POOL_ADDRESSES_PROVIDER = "0xA73ff12D177D8F1Ec938c3ba0e87D33524dD5594"
UI_POOL_DATA_PROVIDER_V3_ADDRESS = "0x7b883191011AEAe40581d3Fa1B112413808C9c00"
PROTOCOL_DATA_PROVIDER_ADDRESS = "0x895C799a5bbdCb63B80bEE5BD94E7b9138D977d6"
POOL_ADDRESS = "0xceCcE0EB9DD2Ef7996e01e25DD70e461F918A14b"
MAX_UINT256 = Web3.to_int(hexstr="0xffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff")


with open("modules/abi/hypurrfi_pool_abi.json") as f:
    POOL_ABI = json.load(f)
with open("modules/abi/UiPoolDataProviderV3.json") as f:
    UI_POOL_DATA_PROVIDER_ABI = json.load(f)
with open("modules/abi/HyFiProtocolDataProvider.json") as f:
    PROTOCOL_DATA_PROVIDER_ABI = json.load(f)
with open("modules/abi/erc20_abi.json") as f:
    ERC20_ABI = json.load(f)


# supported_asset_addrs = ["0x5555555555555555555555555555555555555555", "0x94e8396e0869c9F2200760aF0621aFd240E1CF38", "0xca79db4B49f608eF54a5CB813FbEd3a6387bC645", "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463", "0xBe6727B535545C67d5cAa73dEa54865B92CF7907","0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34", "0x02c6a2fA58cC01A18B8D9E00eA48d65E4dF26c70", "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb", "0xb50A96253aBDF803D85efcDce07Ad8becBc52BD5", "0x068f321Fa8Fb9f0D135f290Ef6a3e2813e1c8A29", "0xfD739d4e423301CE9385c1fb8850539D657C296D", "0xf4D9235269a96aaDaFc9aDAe454a0618eBE37949", "0xfDD22Ce6D1F66bc0Ec89b20BF16CcB6670F55A5a", "0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2"]
w3 = Web3(Web3.HTTPProvider(RPC_URL))
if not w3.is_connected():
    w3 = Web3(Web3.HTTPProvider("https://hyperliquid.drpc.org"))
    if not w3.is_connected():
        raise ConnectionError("Failed to connect to Web3 provider")

pool = w3.eth.contract(address=POOL_ADDRESS, abi=POOL_ABI)
ui_pool = w3.eth.contract(address=UI_POOL_DATA_PROVIDER_V3_ADDRESS, abi=UI_POOL_DATA_PROVIDER_ABI)
protocol_data = w3.eth.contract(address=PROTOCOL_DATA_PROVIDER_ADDRESS, abi=PROTOCOL_DATA_PROVIDER_ABI)

def fetch_reserves():
    reserves = ui_pool.functions.getReservesList(POOL_ADDRESSES_PROVIDER).call()
    results = []

    for asset in reserves:
        try:
            data = protocol_data.functions.getReserveData(asset).call()
            liquidity_rate = data[5] / 1e25
            variable_borrow_rate = data[6] / 1e25
        except Exception as e:
            print(f"[ERR] {asset}: {e}")
            continue

        token = w3.eth.contract(address=asset, abi=ERC20_ABI)
        try:
            symbol = token.functions.symbol().call()
            decimals = token.functions.decimals().call()
        except Exception:
            symbol, decimals = "UNKNOWN", 18

        results.append({
            "asset": asset,
            "symbol": symbol,
            "decimals": decimals,
            "liquidity_rate_%": liquidity_rate,
            "variable_borrow_rate_%": variable_borrow_rate
        })

    return results

def build_tx(function, sender):
    nonce = w3.eth.get_transaction_count(sender, "pending")
    return function.build_transaction({
        "from": sender,
        "nonce": nonce,
        "gas": 500000,
        "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
        "maxFeePerGas": w3.eth.get_block("latest")["baseFeePerGas"] + w3.to_wei(1, "gwei"),
        "chainId": w3.eth.chain_id
    })


def sign_and_send(tx, private_key):
    try:
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        return tx_hash.hex()
    except Exception as e:
        return e

def approve_erc20(token_address, spender, amount, user_address, private_key):
    token = w3.eth.contract(address=token_address, abi=ERC20_ABI)
    tx = build_tx(token.functions.approve(spender, amount), user_address)
    return sign_and_send(tx, private_key)

def supply(asset, amount, user_address, private_key):
    approve_erc20(asset, POOL_ADDRESS, amount, user_address, private_key)
    time.sleep(2)
    tx = build_tx(pool.functions.deposit(asset, amount, user_address, 0), user_address)
    return sign_and_send(tx, private_key)

def withdraw(asset, amount, user_address, private_key, to=None):
    if amount is None:
        amount = MAX_UINT256
    if to is None:
        to = user_address
    tx = build_tx(pool.functions.withdraw(asset, amount, to), user_address)
    return sign_and_send(tx, private_key)

def borrow(asset, amount, user_address, private_key, interest_rate_mode=2):
    tx = build_tx(pool.functions.borrow(asset, amount, interest_rate_mode, 0, user_address), user_address)
    return sign_and_send(tx, private_key)

def repay(asset, amount, user_address, private_key, interest_rate_mode=2):
    repay_amount = amount if amount is not None else MAX_UINT256
    approve_erc20(asset, POOL_ADDRESS, repay_amount, user_address, private_key)
    time.sleep(2)
    tx = build_tx(pool.functions.repay(asset, repay_amount, interest_rate_mode, user_address), user_address)
    return sign_and_send(tx, private_key)

def get_user_account_data(user_address):
    data = pool.functions.getUserAccountData(user_address).call()
    return decimal_to_float({
        "total_collateral": w3.from_wei(data[0], "ether"),
        "total_debt": w3.from_wei(data[1], "ether"),
        "available_borrow": w3.from_wei(data[2], "ether"),
        "liquidation_threshold": data[3] / 100,
        "ltv": data[4] / 100,
        "health_factor": data[5] / 1e18
    })

def get_user_reserve_data(user_address, asset_address):

    data = protocol_data.functions.getUserReserveData(asset_address, user_address).call()
    m =  {
        "supplied_balance": Decimal(data[0]) / Decimal(1e18),
        "stable_debt": Decimal(data[1]) / Decimal(1e18),
        "variable_debt": Decimal(data[2]) / Decimal(1e18),
        "usage_as_collateral": data[8]
    }
    return decimal_to_float(m)

def get_full_user_portfolio(user_address):
    reserves_list = protocol_data.functions.getAllReservesTokens().call()
    token_map = {addr.lower(): symbol for (symbol, addr) in reserves_list}
    portfolio_tokens = []
    total_supplied = Decimal("0")
    total_borrowed = Decimal("0")

    for _, token_addr in reserves_list:
        try:
            data = protocol_data.functions.getUserReserveData(token_addr, user_address).call()
        except Exception as e:
            print(f"Error fetching reserve data for {token_addr}: {e}")
            continue

        currentATokenBalance = Decimal(data[0]) / Decimal(1e18)
        currentStableDebt = Decimal(data[1]) / Decimal(1e18)
        currentVariableDebt = Decimal(data[2]) / Decimal(1e18)
        usageAsCollateral = bool(data[8]) if len(data) > 8 else False

        if currentATokenBalance > 0 or currentStableDebt > 0 or currentVariableDebt > 0:
            portfolio_tokens.append({
                "symbol": token_map.get(token_addr.lower(), token_addr),
                "supplied": str(currentATokenBalance.quantize(Decimal("1.000000"), rounding="ROUND_DOWN")),
                "borrowed": str((currentStableDebt + currentVariableDebt).quantize(Decimal("1.000000"), rounding="ROUND_DOWN")),
                "collateral": usageAsCollateral,
                "raw_supplied": currentATokenBalance,
                "raw_borrowed": currentStableDebt + currentVariableDebt
            })

        total_supplied += currentATokenBalance
        total_borrowed += currentStableDebt + currentVariableDebt

    try:
        account_data = pool.functions.getUserAccountData(user_address).call()
    except Exception as e:
        raise RuntimeError(f"Failed to fetch global account data: {e}")

    total_collateral = Decimal(account_data[0]) / Decimal(1e18)
    total_debt = Decimal(account_data[1]) / Decimal(1e18)
    available_borrow = Decimal(account_data[2]) / Decimal(1e18)
    liquidation_threshold = Decimal(account_data[3]) / Decimal(100)
    ltv = Decimal(account_data[4]) / Decimal(100)
    health_factor = Decimal(account_data[5]) / Decimal(1e18) if account_data[5] else Decimal("0")

    return {
        "tokens": portfolio_tokens,
        "total_collateral": str(total_collateral),
        "total_debt": str(total_debt),
        "available_borrow": str(available_borrow),
        "ltv": float(ltv),
        "liq_threshold": float(liquidation_threshold),
        "health_factor": str(health_factor),
        "raw": {
            "total_collateral": total_collateral,
            "total_debt": total_debt,
            "available_borrow": available_borrow,
            "ltv": ltv,
            "liq_threshold": liquidation_threshold,
            "health_factor": health_factor
        }
    }

def analyze_portfolio_actions(portfolio):
    raw = portfolio["raw"]
    total_collateral = raw["total_collateral"]
    total_debt = raw["total_debt"]
    available_borrow = raw["available_borrow"]
    liq_threshold = raw["liq_threshold"]
    ltv = raw["ltv"]
    health_factor = raw["health_factor"]

    actions = {}
    max_withdrawable = (total_collateral * ltv) - total_debt
    actions["max_withdrawable"] = str(max_withdrawable if max_withdrawable > 0 else Decimal("0"))
    actions["max_borrowable"] = str(available_borrow if available_borrow > 0 else Decimal("0"))

    if total_debt > 0:
        target_hf = Decimal("2.0")
        if health_factor < target_hf and liq_threshold > 0:
            debt_target = (total_collateral * liq_threshold) / target_hf
            repay_amount = total_debt - debt_target if total_debt > debt_target else Decimal("0")
            actions["recommended_repay"] = str(repay_amount)
        else:
            actions["recommended_repay"] = "0"
    else:
        actions["recommended_repay"] = "0"

    dust_tokens = [t["symbol"] for t in portfolio["tokens"] if t["raw_borrowed"] > 0 and t["raw_borrowed"] < Decimal("0.000001")]
    actions["dust_tokens"] = dust_tokens

    return actions

def decimal_to_float(obj):
    if isinstance(obj, dict):
        return {k: decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_float(i) for i in obj]
    elif isinstance(obj, Decimal):
        return float(obj)
    else:
        return obj
    

def get_user_reserve_data_full(address):
    port = {}
    asset_addrs = ["0x5555555555555555555555555555555555555555", "0x94e8396e0869c9F2200760aF0621aFd240E1CF38", "0xca79db4B49f608eF54a5CB813FbEd3a6387bC645", "0x9FDBdA0A5e284c32744D2f17Ee5c74B284993463", "0xBe6727B535545C67d5cAa73dEa54865B92CF7907","0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34", "0x02c6a2fA58cC01A18B8D9E00eA48d65E4dF26c70", "0xB8CE59FC3717ada4C02eaDF9682A9e934F625ebb", "0xb50A96253aBDF803D85efcDce07Ad8becBc52BD5", "0x068f321Fa8Fb9f0D135f290Ef6a3e2813e1c8A29", "0xfD739d4e423301CE9385c1fb8850539D657C296D", "0xf4D9235269a96aaDaFc9aDAe454a0618eBE37949", "0xfDD22Ce6D1F66bc0Ec89b20BF16CcB6670F55A5a", "0x211Cc4DD073734dA055fbF44a2b4667d5E5fE5d2"]

    for i in asset_addrs:
        data = protocol_data.functions.getUserReserveData(i, address).call()
        m =  {
            "supplied_balance": Decimal(data[0]) / Decimal(1e18),
            "stable_debt": Decimal(data[1]) / Decimal(1e18),
            "variable_debt": Decimal(data[2]) / Decimal(1e18),
            "usage_as_collateral": data[8]
        }
        if m['supplied_balance'] > 0 or m['stable_debt'] or m['variable_debt'] > 0:
            port[i] = decimal_to_float(m)
    if port == {}:
        return {'0x2222222222222222222222222222222222222222': {'supplied_balance': 0.0, 'stable_debt': 0.0, 'variable_debt': 0.0, 'usage_as_collateral': True}}
    return port

if __name__ == "__main__":
    print((get_user_account_data("")))
    print()
    print()
    print((get_user_reserve_data_full("")))