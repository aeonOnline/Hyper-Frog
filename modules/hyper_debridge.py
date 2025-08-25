import requests
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from solders.message import MessageV0
from solders.hash import Hash
from solana.rpc.api import Client

CREATE_TX_URL = "https://dln.debridge.finance/v1.0/dln/order/create-tx"
SOLANA_RPC_URL = "https://api.mainnet-beta.solana.com"

def get_debridge_quote(amount: int, sender_address: str, recipient_address: str) -> dict:
    params = {
        "srcChainId": 7565164,
        "srcChainTokenIn": "11111111111111111111111111111111",
        "srcChainTokenInAmount": amount,
        "dstChainId": 100000022,
        "dstChainTokenOut": "0x0000000000000000000000000000000000000000",
        "dstChainTokenOutAmount": "auto",
        "dstChainTokenOutRecipient": recipient_address,
        "senderAddress": sender_address,
        "srcChainOrderAuthorityAddress": sender_address,
        "dstChainOrderAuthorityAddress": recipient_address,
        "enableEstimate": "true",
        "referralCode": "32261",
        "affiliateFeePercent": 0,
        "prependOperatingExpenses": "false",
        "skipSolanaRecipientValidation": "false",
        "srcChainPriorityLevel": "normal"
    }

    try:
        response = requests.get(CREATE_TX_URL, params=params, headers={"accept": "application/json"})
        data = response.json()
        if "errorCode" in data:
            return data  # Return deBridge error response directly
        if "tx" not in data or "data" not in data["tx"]:
            return {
                "errorCode": 11,
                "errorId": "INVALID_RESPONSE",
                "errorMessage": "Invalid deBridge API response: missing 'tx' or 'data' field"
            }
        return data
    except requests.RequestException as e:
        return {
            "errorCode": 11,
            "errorId": "API_REQUEST_FAILED",
            "errorMessage": f"Failed to fetch deBridge quote: {str(e)}"
        }

def send_debridge_tx(solana_private_key: str, tx_data: dict) -> dict:

    try:
        client = Client(SOLANA_RPC_URL)
        
        try:

            keypair = Keypair.from_base58_string(solana_private_key)
            print(f"Successfully decoded private key as Base58")
        except ValueError:

            print(f"Failed Base58 decode, trying hex: {solana_private_key}")
            try:
                key_bytes = bytes.fromhex(solana_private_key)
                if len(key_bytes) != 64:
                    return {
                        "errorCode": 15,
                        "errorId": "INVALID_KEY_LENGTH",
                        "errorMessage": "Hex private key must be 64 bytes (128 hex chars)"
                    }
                keypair = Keypair.from_seed(key_bytes[:32])  
                print(f"Successfully decoded private key as hex")
            except ValueError as e:
                return {
                    "errorCode": 16,
                    "errorId": "INVALID_KEY_FORMAT",
                    "errorMessage": f"Failed to decode private key (Base58 and hex failed): {str(e)}"
                }


        latest_blockhash = str(client.get_latest_blockhash().value.blockhash)
        tx_bytes = tx_data["tx"]["data"]
        if tx_bytes.startswith("0x"):
            tx_bytes = tx_bytes[2:]
        tx_bytes = bytes.fromhex(tx_bytes)
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        msg = vtx.message
        new_msg = MessageV0(
            header=msg.header,
            account_keys=msg.account_keys,
            recent_blockhash=Hash.from_string(latest_blockhash),
            instructions=msg.instructions,
            address_table_lookups=msg.address_table_lookups
        )
        signed_vtx = VersionedTransaction(new_msg, [keypair])
        txid = client.send_raw_transaction(bytes(signed_vtx))
        tx_hash = str(txid.value)
        print(f"Transaction hash: {tx_hash}")
        return {"tx_hash": tx_hash}
    except ValueError as e:
        return {
            "errorCode": 11,
            "errorId": "INVALID_TX_DATA",
            "errorMessage": f"Failed to process transaction data: {str(e)}"
        }
    except Exception as e:
        return {
            "errorCode": 11,
            "errorId": "TX_SEND_FAILED",
            "errorMessage": f"Failed to send deBridge transaction: {str(e)}"
        }
    
