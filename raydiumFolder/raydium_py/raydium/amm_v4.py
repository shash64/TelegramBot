import base64
import os
from typing import Optional
from solders.keypair import Keypair #type: ignore
from solana.rpc.commitment import Processed
from solana.rpc.types import TokenAccountOpts, TxOpts
from solders.compute_budget import set_compute_unit_limit, set_compute_unit_price  # type: ignore
from solders.message import MessageV0  # type: ignore
from solders.pubkey import Pubkey  # type: ignore
from solders.system_program import (
    CreateAccountWithSeedParams,
    create_account_with_seed,
)
from solders.transaction import VersionedTransaction  # type: ignore
from spl.token.client import Token
from spl.token.instructions import (
    CloseAccountParams,
    InitializeAccountParams,
    close_account,
    create_associated_token_account,
    get_associated_token_address,
    initialize_account,
)
from raydiumFolder.raydium_py.utils.common_utils import confirm_txn, get_token_balance
from raydiumFolder.raydium_py.utils.pool_utils import (
    AmmV4PoolKeys,
    fetch_amm_v4_pool_keys,
    get_amm_v4_reserves,
    make_amm_v4_swap_instruction
)
from solana.rpc.api import Client
from raydiumFolder.raydium_py.raydium.constants import ACCOUNT_LAYOUT_LEN, SOL_DECIMAL, TOKEN_PROGRAM_ID, WSOL

UNIT_BUDGET =  150_000
UNIT_PRICE =  1_000_000
RPC = "https://mainnet.helius-rpc.com/?api-key=..." # Your RPC link
client = Client(RPC)

def buy(pair_address: str, payer_keypair: str, sol_in: float , slippage: int) -> bool:
    payer_keypairB58 = Keypair.from_base58_string(payer_keypair)
    try:
        pool_keys: Optional[AmmV4PoolKeys] = fetch_amm_v4_pool_keys(pair_address)
        if pool_keys is None:
            return False, 0, ""

        mint = (
            pool_keys.base_mint if pool_keys.base_mint != WSOL else pool_keys.quote_mint
        )

        amount_in = int(sol_in * SOL_DECIMAL)

        base_reserve, quote_reserve, token_decimal = get_amm_v4_reserves(pool_keys)
        amount_out = sol_for_tokens(sol_in, base_reserve, quote_reserve)

        slippage_adjustment = 1 - (slippage / 100)
        amount_out_with_slippage = amount_out * slippage_adjustment
        minimum_amount_out = int(amount_out_with_slippage * 10**token_decimal)

        token_account_check = client.get_token_accounts_by_owner(
            payer_keypairB58.pubkey(), TokenAccountOpts(mint), Processed
        )
        if token_account_check.value:
            token_account = token_account_check.value[0].pubkey
            create_token_account_instruction = None
        else:
            token_account = get_associated_token_address(payer_keypairB58.pubkey(), mint)
            create_token_account_instruction = create_associated_token_account(
                payer_keypairB58.pubkey(), payer_keypairB58.pubkey(), mint
            )

        seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
        wsol_token_account = Pubkey.create_with_seed(
            payer_keypairB58.pubkey(), seed, TOKEN_PROGRAM_ID
        )
        balance_needed = Token.get_min_balance_rent_for_exempt_for_account(client)

        create_wsol_account_instruction = create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=payer_keypairB58.pubkey(),
                to_pubkey=wsol_token_account,
                base=payer_keypairB58.pubkey(),
                seed=seed,
                lamports=int(balance_needed + amount_in),
                space=ACCOUNT_LAYOUT_LEN,
                owner=TOKEN_PROGRAM_ID,
            )
        )

        init_wsol_account_instruction = initialize_account(
            InitializeAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                mint=WSOL,
                owner=payer_keypairB58.pubkey(),
            )
        )

        swap_instruction = make_amm_v4_swap_instruction(
            amount_in=amount_in,
            minimum_amount_out=minimum_amount_out,
            token_account_in=wsol_token_account,
            token_account_out=token_account,
            accounts=pool_keys,
            owner=payer_keypairB58.pubkey(),
        )

        close_wsol_account_instruction = close_account(
            CloseAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                dest=payer_keypairB58.pubkey(),
                owner=payer_keypairB58.pubkey(),
            )
        )

        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(UNIT_PRICE),
            create_wsol_account_instruction,
            init_wsol_account_instruction,
        ]

        if create_token_account_instruction:
            instructions.append(create_token_account_instruction)

        instructions.append(swap_instruction)
        instructions.append(close_wsol_account_instruction)

        compiled_message = MessageV0.try_compile(
            payer_keypairB58.pubkey(),
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        txn_sig = client.send_transaction(
            txn=VersionedTransaction(compiled_message, [payer_keypairB58]),
            opts=TxOpts(skip_preflight=True),
        ).value

        confirmed = confirm_txn(txn_sig)

        return confirmed, amount_out, txn_sig

    except Exception as e:
        print("Error occurred during transaction:", e)
        return False, 0 ,""

def sell(pair_address: str, payer_keypair: str, percentage: int, slippage: int) -> bool:
    payer_keypairB58 = Keypair.from_base58_string(payer_keypair)
    payer_pubkey = payer_keypairB58.pubkey()  # Utilisation correcte de la clé publique

    try:
        if not (1 <= percentage <= 100):
            return False, 0, 0, ""

        pool_keys: Optional[AmmV4PoolKeys] = fetch_amm_v4_pool_keys(pair_address)
        if pool_keys is None:
            return False, 0, 0, ""

        mint = (
            pool_keys.base_mint if pool_keys.base_mint != WSOL else pool_keys.quote_mint
        )

        token_balance = get_token_balance(str(mint), payer_keypair)

        if token_balance == 0 or token_balance is None:
            return False, 0, 0, ""

        token_balance = token_balance * (percentage / 100)

        base_reserve, quote_reserve, token_decimal = get_amm_v4_reserves(pool_keys)
        amount_out = tokens_for_sol(token_balance, base_reserve, quote_reserve)

        slippage_adjustment = 1 - (slippage / 100)
        amount_out_with_slippage = amount_out * slippage_adjustment
        minimum_amount_out = int(amount_out_with_slippage * SOL_DECIMAL)

        amount_in = int(token_balance * 10**token_decimal)
        token_account = get_associated_token_address(payer_pubkey, mint)

        seed = base64.urlsafe_b64encode(os.urandom(24)).decode("utf-8")
        wsol_token_account = Pubkey.create_with_seed(
            payer_pubkey, seed, TOKEN_PROGRAM_ID
        )
        balance_needed = Token.get_min_balance_rent_for_exempt_for_account(client)

        create_wsol_account_instruction = create_account_with_seed(
            CreateAccountWithSeedParams(
                from_pubkey=payer_pubkey,
                to_pubkey=wsol_token_account,
                base=payer_pubkey,
                seed=seed,
                lamports=int(balance_needed),
                space=ACCOUNT_LAYOUT_LEN,
                owner=TOKEN_PROGRAM_ID,
            )
        )

        init_wsol_account_instruction = initialize_account(
            InitializeAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                mint=WSOL,
                owner=payer_pubkey,
            )
        )

        swap_instructions = make_amm_v4_swap_instruction(
            amount_in=amount_in,
            minimum_amount_out=minimum_amount_out,
            token_account_in=token_account,
            token_account_out=wsol_token_account,
            accounts=pool_keys,
            owner=payer_pubkey,
        )

        close_wsol_account_instruction = close_account(
            CloseAccountParams(
                program_id=TOKEN_PROGRAM_ID,
                account=wsol_token_account,
                dest=payer_pubkey,
                owner=payer_pubkey,
            )
        )

        instructions = [
            set_compute_unit_limit(UNIT_BUDGET),
            set_compute_unit_price(UNIT_PRICE),
            create_wsol_account_instruction,
            init_wsol_account_instruction,
            swap_instructions,
            close_wsol_account_instruction,
        ]

        if percentage == 100:
            close_token_account_instruction = close_account(
                CloseAccountParams(
                    program_id=TOKEN_PROGRAM_ID,
                    account=token_account,
                    dest=payer_pubkey,
                    owner=payer_pubkey,
                )
            )
            instructions.append(close_token_account_instruction)

        compiled_message = MessageV0.try_compile(
            payer_pubkey,
            instructions,
            [],
            client.get_latest_blockhash().value.blockhash,
        )

        txn_sig = client.send_transaction(
            txn=VersionedTransaction(compiled_message, [payer_keypairB58]),
            opts=TxOpts(skip_preflight=True),
        ).value

        confirmed = confirm_txn(txn_sig)
        return confirmed, token_balance, amount_out, txn_sig

    except Exception as e:
        print("Error occurred during transaction:", e)
        return False, 0, 0, ""
    


def sol_for_tokens(sol_amount, base_vault_balance, quote_vault_balance, swap_fee=0.25):
    effective_sol_used = sol_amount - (sol_amount * (swap_fee / 100))
    constant_product = base_vault_balance * quote_vault_balance
    updated_base_vault_balance = constant_product / (quote_vault_balance + effective_sol_used)
    tokens_received = base_vault_balance - updated_base_vault_balance
    return round(tokens_received, 9)

def tokens_for_sol(token_amount, base_vault_balance, quote_vault_balance, swap_fee=0.25):
    effective_tokens_sold = token_amount * (1 - (swap_fee / 100))
    constant_product = base_vault_balance * quote_vault_balance
    updated_quote_vault_balance = constant_product / (base_vault_balance + effective_tokens_sold)
    sol_received = quote_vault_balance - updated_quote_vault_balance
    return round(sol_received, 9)
