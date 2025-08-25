from typing import Dict, Any, List, Optional
from web3 import Web3, HTTPProvider
import requests
import os
from modules.token_map import TOKEN_MAP

ERC20_MINI_ABI = [
    {"constant": True, "inputs": [{"name": "_owner", "type": "address"}], "name": "balanceOf",
     "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
]

DEFAULT_HYPEREVM_RPC = "https://rpc.hyperliquid.xyz/evm"

DEFAULT_HYPEREVM_TOKENS = []
for i in TOKEN_MAP:
    DEFAULT_HYPEREVM_TOKENS.append(TOKEN_MAP[i])


DEFAULT_SOLANA_RPC = os.getenv("SOLANA_RPC", "https://api.mainnet-beta.solana.com")

def fetch_hyperevm_balances(wallet_address: str, tokens: Optional[List[str]] = None) -> Dict[str, Any]:
    if tokens is None:
        tokens = DEFAULT_HYPEREVM_TOKENS

    results: Dict[str, Any] = {"native": 0, "tokens": {}, "errors": []}
    try:
        w3 = Web3(HTTPProvider(DEFAULT_HYPEREVM_RPC))
        addr = Web3.to_checksum_address(wallet_address)
    except Exception as e:
        results["errors"].append(f"RPC or address init failed: {e}")
        return results

    try:
        raw_hype = w3.eth.get_balance(addr)
        hype_balance = raw_hype / 1e18
        if hype_balance > 0:
            results["native"] = hype_balance
    except Exception as e:
        results["errors"].append(f"Native HYPE fetch failed: {e}")

    for t in tokens:
        if t.lower() == "0x2222222222222222222222222222222222222222" or t.lower() == "0x0000000000000000000000000000000000000000":
            continue
        try:
            contract = w3.eth.contract(address=Web3.to_checksum_address(t), abi=ERC20_MINI_ABI)
            raw = contract.functions.balanceOf(addr).call()
            if raw == 0:
                continue
            try:
                decimals = contract.functions.decimals().call()
            except Exception:
                decimals = 18
            try:
                symbol = contract.functions.symbol().call()
            except Exception:
                symbol = t
            bal = raw / (10 ** decimals)
            if bal > 0:
                results["tokens"][symbol] = bal
        except Exception as e:
            results["errors"].append(f"{t} fetch failed: {e}")
    return results

def fetch_solana_balance(sol_addr: str) -> Dict[str, Any]:
    results: Dict[str, Any] = {"native": 0, "errors": []}
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getBalance",
        "params": [sol_addr, {"commitment": "confirmed"}]
    }
    try:
        r = requests.post(DEFAULT_SOLANA_RPC, json=payload, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        results["errors"].append(f"Solana RPC request error: {e}")
        return results

    lamports = data.get("result", {}).get("value")
    if lamports is None:
        results["errors"].append(f"Solana unexpected response: {data}")
        return results

    bal = lamports / 1e9
    if bal > 0:
        results["native"] = bal
    return results

def get_token_decimals(token_address: str) -> int:
    if token_address.lower() == "0x0000000000000000000000000000000000000000" or token_address.lower() == "0x2222222222222222222222222222222222222222":
        return 18
    try:
        w3 = Web3(HTTPProvider(DEFAULT_HYPEREVM_RPC))
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_MINI_ABI)
        decimals = contract.functions.decimals().call()
        return decimals
    except Exception:
        return 18 
    
def get_token_symbol(token_address: str) -> str:
    if token_address.lower() == "0x0000000000000000000000000000000000000000" or token_address.lower() == "0x2222222222222222222222222222222222222222":
        return "HYPE"
    try:
        w3 = Web3(HTTPProvider(DEFAULT_HYPEREVM_RPC))
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_MINI_ABI)
        symbol = contract.functions.symbol().call()
        return str(symbol)
    except Exception:
        return str(token_address)

def get_token_balance_evm(wallet_address, token_address: str) -> str:
    try:
        w3 = Web3(HTTPProvider(DEFAULT_HYPEREVM_RPC))

        if token_address.lower() == "0x0000000000000000000000000000000000000000" or token_address.lower() == "0x2222222222222222222222222222222222222222":
            try:
                raw_hype = w3.eth.get_balance(wallet_address)
                hype_balance = raw_hype / 1e18
                if hype_balance > 0:
                    return hype_balance
                else:
                    return 0
            except Exception as e:
                return 0
        contract = w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=ERC20_MINI_ABI)


        raw = contract.functions.balanceOf(wallet_address).call()
        if raw == 0:
            return 0
        try:
            decimals = get_token_decimals(token_address)
        except Exception:
            decimals = 18

        return raw / (10 ** decimals)
    except Exception:
        return 0

if __name__ == "__main__":
    hype_wallet = ""
    sol_wallet = ""

    # print(fetch_hyperevm_balances(hype_wallet))
    # print(fetch_solana_balance(sol_wallet))
    # print(get_token_decimals('0xb50A96253aBDF803D85efcDce07Ad8becBc52BD5'))
    # print(get_token_symbol('0xb50A96253aBDF803D85efcDce07Ad8becBc52BD5'))
    