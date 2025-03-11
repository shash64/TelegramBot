import json
import time
from solana.rpc.commitment import Confirmed, Processed
from solana.rpc.types import TokenAccountOpts
from solders.signature import Signature #type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solana.rpc.api import Client
from solders.keypair import Keypair #type: ignore

RPC = "https://mainnet.helius-rpc.com/?api-key=..." # Your RPC link
client = Client(RPC)

def get_token_balance(mint_str: str, payer_keypair: str) -> float | None:
    payer_keypairB58 = Keypair.from_base58_string(payer_keypair)
    mint = Pubkey.from_string(mint_str)
    response = client.get_token_accounts_by_owner_json_parsed(
        payer_keypairB58.pubkey(),
        TokenAccountOpts(mint=mint),
        commitment=Processed
    )

    if response.value:
        accounts = response.value
        if accounts:
            token_amount = accounts[0].account.data.parsed['info']['tokenAmount']['uiAmount']
            if token_amount:
                return float(token_amount)
    return None

def confirm_txn(txn_sig: Signature, max_retries: int = 20, retry_interval: int = 2) -> bool:
    retries = 1
    
    while retries < max_retries:
        try:
            txn_res = client.get_transaction(
                txn_sig, 
                encoding="json", 
                commitment=Confirmed, 
                max_supported_transaction_version=0)
            
            txn_json = json.loads(txn_res.value.transaction.meta.to_json())
            
            if txn_json['err'] is None:
                #print("Transaction confirmed... try count:", retries)
                return True
            
            #print("Error: Transaction not confirmed. Retrying...")
            if txn_json['err']:
                #print("Transaction failed.")
                return False
        except Exception as e:
            #print("Awaiting confirmation... try count:", retries)
            retries += 1
            time.sleep(retry_interval)
    
    #print("Max retries reached. Transaction confirmation failed.")
    return None
