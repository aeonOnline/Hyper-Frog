import requests
from web3 import Web3
from dotenv import load_dotenv
import os
import json
# Load environment variables
load_dotenv()
GLUEX_API_KEY = os.getenv('GLUEX_API_KEY')
RPC_URL = 'https://rpc.hyperliquid.xyz/evm'

if not GLUEX_API_KEY:
    raise RuntimeError("GLUEX_API_KEY not set in .env")
if not RPC_URL:
    raise RuntimeError("RPC_URL not set in .env")

HEADERS = {
    'content-type': 'application/json',
    'x-api-key': GLUEX_API_KEY
}

with open("modules/abi/erc20_abi.json") as f:
    erc20_abi = json.load(f)


w3 = Web3(Web3.HTTPProvider(RPC_URL))

def get_swap_quote(input_token: str, output_token: str, input_amount: str, user_address: str) -> dict:

    url = 'https://router.gluex.xyz/v1/quote'
    payload = {
        'inputToken': input_token,
        'outputToken': output_token,
        'inputAmount': input_amount,
        'userAddress': user_address,
        'outputReceiver':user_address,
        'chainID': 'hyperevm',
        'uniquePID': '083b7cd68478935999b08b1bf9d7ea3a77e5d5de6072e209ec872574b372ed6b',
        "isPermit2":    False
    }
    try:
        resp = requests.post(url, headers=HEADERS, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if 'result' not in data:
            return {"statusCode": 400, "error": f"Failed to fetch quote: simulatipon failed"}

        return data
    except requests.RequestException as e:
        return {"statusCode": 400, "error": f"Failed to fetch quote: {str(e)}"}


def execute_swap(quote_result: dict, user_address: str, private_key: str) -> dict:
    try:
        if not w3.is_connected():
            return {"statusCode": 400, "error": "Cannot connect to RPC endpoint"}


        if not quote_result.get('isNativeTokenInput', False):
            token_address = Web3.to_checksum_address(quote_result['inputToken'])
            router = Web3.to_checksum_address(quote_result['router'])
            amount_in = int(quote_result['inputAmount'])

            token_contract = w3.eth.contract(address=token_address, abi=erc20_abi)
            allowance = token_contract.functions.allowance(user_address, router).call()

            if allowance < amount_in:
                approve_tx = token_contract.functions.approve(router, amount_in).build_transaction({
                    'chainId': 999,
                    'from': user_address,
                    'nonce': w3.eth.get_transaction_count(user_address),
                    'gasPrice': w3.eth.gas_price
                })
                approve_tx['gas'] = w3.eth.estimate_gas(approve_tx)
                signed_approve = w3.eth.account.sign_transaction(approve_tx, private_key)
                approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
                print("Approve tx sent, hash:", approve_hash.hex())
                approve_receipt = w3.eth.wait_for_transaction_receipt(approve_hash)
                if approve_receipt.status == 0:
                    return {"statusCode": 400, "error": "Approval transaction failed"}
                print(f"Approval tx confirmed: {approve_hash.hex()}")


        tx = {
            'from': user_address,
            'to': quote_result['router'],
            'data': quote_result['calldata'],
            'value': int(quote_result.get('value', 0)) if quote_result.get('isNativeTokenInput') else 0,
            'nonce': w3.eth.get_transaction_count(user_address),
            'gasPrice': w3.eth.gas_price
        }
        tx['gas'] = w3.eth.estimate_gas(tx)
        signed = w3.eth.account.sign_transaction(tx, private_key)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
        # print(receipt)
        # print(tx_hash.hex())

        if receipt.status == 0:
            return {"statusCode": 400, "error": "Transaction reverted"}
        return {"statusCode": 200, "txHash": tx_hash.hex(), "error": None}

    except Exception as e:
        return {"statusCode": 400, "error": f"Transaction failed: {str(e)}"}

def gluex_get_exchange_rates(token_address):
    pairs = [
        {
            "domestic_blockchain": "hyperevm",
            "domestic_token": token_address,  
            "foreign_blockchain": "hyperevm",
            "foreign_token": "0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34"    # USDe
        }
    ]
    url = "https://exchange-rates.gluex.xyz/"
    resp = requests.post(url, json=pairs)
    resp.raise_for_status()
    rates =  resp.json()
    try:
        # rates[0]['price'] = ("0xb50A96253aBDF803D85efcDce07Ad8becBc52BD5")*10**(18-6)
        web3 = Web3(Web3.HTTPProvider('https://hyperliquid.drpc.org'))
        contract = web3.eth.contract(address=Web3.to_checksum_address(token_address), abi=erc20_abi)
        dec = contract.functions.decimals().call()
        if dec != 18:
            return (round(float(rates[0]['price']) / 10**(18-dec), 2))
        return round(float(rates[0]['price']),2)
    except Exception as e:
        print("ERROR : ",e)
        print("RETURNED DEFAULT PRICE 1 for TOKEN: ", token_address)
        return {"RETURNED DEFAULT PRICE 1 for TOKEN: "}