from web3 import Web3
from decimal import Decimal
import time


RPC_URL = "https://rpc.hyperliquid.xyz/evm"
KEEP_RESERVE_HYPE = Decimal("0.05")  # reserve HYPE for gas
CCD_ADDRESS = Web3.to_checksum_address("0x6e358dd1204c3fb1D24e569DF0899f48faBE5337")
VAULT_ADDRESS = Web3.to_checksum_address("0x5748ae796AE46A4F1348a1693de4b50560485562")


CCD_ABI = [
    {
        "inputs": [
            {"internalType": "contract ERC20", "name": "depositAsset", "type": "address"},
            {"internalType": "uint256", "name": "depositAmount", "type": "uint256"},
            {"internalType": "uint256", "name": "minimumMint", "type": "uint256"},
            {"internalType": "address", "name": "to", "type": "address"},
            {"internalType": "bytes", "name": "communityCode", "type": "bytes"}
        ],
        "name": "deposit",
        "outputs": [{"internalType":"uint256","name":"shares","type":"uint256"}],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType":"uint256","name":"depositAmount","type":"uint256"},
            {"internalType":"uint256","name":"minimumMint","type":"uint256"},
            {"internalType":"address","name":"to","type":"address"},
            {"internalType":"bytes","name":"communityCode","type":"bytes"}
        ],
        "name": "depositNative",
        "outputs": [{"internalType":"uint256","name":"shares","type":"uint256"}],
        "stateMutability": "payable",
        "type": "function"
    },
    {"inputs":[],"name":"boringVault","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"depositNonce","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"view","type":"function"}
]

ERC20_MIN = [
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"}
]


def human_wei(x):
    return Decimal(x) / Decimal(10 ** 18)


def convert_to_loop_hype(private_key: str, amount: float):
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        return ("RPC unreachable: " + RPC_URL)

    acct = w3.eth.account.from_key(private_key)
    address = acct.address
    chain_id = w3.eth.chain_id

    ccd = w3.eth.contract(address=CCD_ADDRESS, abi=CCD_ABI)

    native_bal_wei = w3.eth.get_balance(address)
    amount_wei = amount
    reserve_wei = int(KEEP_RESERVE_HYPE * (10 ** 18))
    if native_bal_wei < amount_wei + reserve_wei:
        return (f"Not enough HYPE. Need {amount} + reserve {KEEP_RESERVE_HYPE}, have {human_wei(native_bal_wei)}")


    fn = ccd.functions.depositNative(amount_wei, 0, address, b"")
    nonce = w3.eth.get_transaction_count(address, "pending")
    tx_base = {
        "from": address,
        "value": amount_wei,
        "nonce": nonce,
        "chainId": chain_id,
        "gasPrice": w3.eth.gas_price,
    }

    try:
        unsigned = fn.build_transaction({**tx_base})
        estimated = w3.eth.estimate_gas(unsigned)
        gas_limit = int(estimated * 1.15)
    except Exception:
        gas_limit = 600_000

    unsigned = fn.build_transaction({**tx_base, "gas": gas_limit})
    signed = w3.eth.account.sign_transaction(unsigned, acct.key)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)

    return txh.hex()

def get_lhype_balance(address: str):
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        return ("RPC unreachable: " + RPC_URL)

    vault_token = w3.eth.contract(address=VAULT_ADDRESS, abi=ERC20_MIN)

    try:
        lhype_balance_raw = vault_token.functions.balanceOf(address).call()
    except Exception:
        lhype_balance_raw = 0

    return Decimal(lhype_balance_raw) / (Decimal(10) ** 18)
