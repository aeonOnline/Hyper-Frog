import os
import sqlite3
import logging
from typing import Optional, Tuple
from cryptography.fernet import Fernet
from solders.keypair import Keypair as SolanaKeypair
from eth_account import Account as EvmAccount
from dotenv import load_dotenv

load_dotenv()
MASTER_KEY = os.getenv("MASTER_KEY")
if not MASTER_KEY:
    raise RuntimeError("MASTER_KEY environment variable is not set")

fernet = Fernet(MASTER_KEY.encode())

DB_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "wallets.db")
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)



class WalletDatabase:

    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.init_db()

    def init_db(self) -> None:
        """Initialize the wallets table with all required fields."""
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS wallets (
                    user_id TEXT PRIMARY KEY,
                    evm_pubkey TEXT,
                    evm_cipher BLOB,
                    sol_pubkey TEXT,
                    sol_cipher BLOB,
                    slippage REAL DEFAULT 1.0,
                    yield_hype BOOLEAN DEFAULT 0,
                    yield_stables BOOLEAN DEFAULT 0
                )
            """)
            self.conn.commit()
            logger.info("Database initialized successfully")
        except sqlite3.Error as e:
            logger.error(f"Failed to initialize database: {e}")
            raise

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
        logger.info("Database connection closed")



class WalletManager:
    
    def __init__(self, db: WalletDatabase):
        self.db = db
        self.fernet = Fernet(MASTER_KEY.encode())

    def create_and_store_evm(self, user_id: int) -> Optional[str]:
        try:
            acct = EvmAccount.create()
            priv_bytes = acct.key
            cipher = self.fernet.encrypt(priv_bytes)

            self.db.cursor.execute("""
                SELECT sol_pubkey, sol_cipher, slippage, yield_hype, yield_stables 
                FROM wallets WHERE user_id = ?
            """, (str(user_id),))
            row = self.db.cursor.fetchone()
            sol_pub, sol_cipher, slippage, yield_hype, yield_stables = row if row else (None, None, 1.0, 0, 0)

            self.db.cursor.execute("""
                INSERT OR REPLACE INTO wallets 
                (user_id, evm_pubkey, evm_cipher, sol_pubkey, sol_cipher, slippage, yield_hype, yield_stables)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(user_id), acct.address, cipher, sol_pub, sol_cipher, slippage, yield_hype, yield_stables))
            self.db.conn.commit()
            logger.info(f"Stored EVM wallet for user {user_id}: {acct.address}")
            return acct.address
        except Exception as e:
            logger.error(f"Failed to create EVM wallet for user {user_id}: {e}")
            return None

    def get_evm_wallet(self, user_id: int) -> Optional[Tuple[str, str]]:
        try:
            self.db.cursor.execute(
                "SELECT evm_pubkey, evm_cipher FROM wallets WHERE user_id = ?",
                (str(user_id),)
            )
            row = self.db.cursor.fetchone()
            if not row or not row[1]:
                logger.warning(f"No EVM wallet found for user {user_id}")
                return None
            pub, cipher = row
            priv_bytes = self.fernet.decrypt(cipher)
            return priv_bytes.hex(), pub
        except Exception as e:
            logger.error(f"Failed to retrieve EVM wallet for user {user_id}: {e}")
            return None

    def create_and_store_solana(self, user_id: int) -> Optional[str]:
        try:
            kp = SolanaKeypair()
            priv_bytes = bytes(kp)
            cipher = self.fernet.encrypt(priv_bytes)
            pub = str(kp.pubkey())

            # Preserve existing EVM data and settings
            self.db.cursor.execute("""
                SELECT evm_pubkey, evm_cipher, slippage, yield_hype, yield_stables 
                FROM wallets WHERE user_id = ?
            """, (str(user_id),))
            row = self.db.cursor.fetchone()
            evm_pub, evm_cipher, slippage, yield_hype, yield_stables = row if row else (None, None, 1.0, 0, 0)

            self.db.cursor.execute("""
                INSERT OR REPLACE INTO wallets 
                (user_id, evm_pubkey, evm_cipher, sol_pubkey, sol_cipher, slippage, yield_hype, yield_stables)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (str(user_id), evm_pub, evm_cipher, pub, cipher, slippage, yield_hype, yield_stables))
            self.db.conn.commit()
            logger.info(f"Stored Solana wallet for user {user_id}: {pub}")
            return pub
        except Exception as e:
            logger.error(f"Failed to create Solana wallet for user {user_id}: {e}")
            return None

    def get_solana_wallet(self, user_id: int) -> Optional[Tuple[str, str]]:
        try:
            self.db.cursor.execute(
                "SELECT sol_pubkey, sol_cipher FROM wallets WHERE user_id = ?",
                (str(user_id),)
            )
            row = self.db.cursor.fetchone()
            if not row or not row[1]:
                logger.warning(f"No Solana wallet found for user {user_id}")
                return None
            pub, cipher = row
            priv_bytes = self.fernet.decrypt(cipher)
            return priv_bytes.hex(), pub
        except Exception as e:
            logger.error(f"Failed to retrieve Solana wallet for user {user_id}: {e}")
            return None

    def get_user_settings(self, user_id: int) -> Tuple[float, bool, bool]:
        try:
            self.db.cursor.execute(
                "SELECT slippage, yield_hype, yield_stables FROM wallets WHERE user_id = ?",
                (str(user_id),)
            )
            row = self.db.cursor.fetchone()
            if not row:
                return 1.0, False, False
            return row[0], bool(row[1]), bool(row[2])
        except Exception as e:
            logger.error(f"Failed to retrieve settings for user {user_id}: {e}")
            return 1.0, False, False

    def update_slippage(self, user_id: int, slippage: float) -> bool:
        try:
            if slippage < 0:
                slippage = 0
            elif slippage > 30:
                slippage = 30
            slippage = round(slippage, 2)

            self.db.cursor.execute(
                "UPDATE wallets SET slippage = ? WHERE user_id = ?",
                (slippage, str(user_id))
            )
            self.db.conn.commit()
            logger.info(f"Updated slippage to {slippage}% for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to update slippage for user {user_id}: {e}")
            return False

    def toggle_yield_hype(self, user_id: int) -> bool:
        try:
            current = self.get_user_settings(user_id)[1]
            self.db.cursor.execute(
                "UPDATE wallets SET yield_hype = ? WHERE user_id = ?",
                (not current, str(user_id))
            )
            self.db.conn.commit()
            logger.info(f"Toggled hype yield to {not current} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to toggle hype yield for user {user_id}: {e}")
            return False

    def toggle_yield_stables(self, user_id: int) -> bool:
        try:
            current = self.get_user_settings(user_id)[2]
            self.db.cursor.execute(
                "UPDATE wallets SET yield_stables = ? WHERE user_id = ?",
                (not current, str(user_id))
            )
            self.db.conn.commit()
            logger.info(f"Toggled stables yield to {not current} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to toggle stables yield for user {user_id}: {e}")
            return False

# Initialize database and wallet manager
db = WalletDatabase()
wallet_manager = WalletManager(db)

if __name__ == "__main__":
    # Example usage for testing
    user_id = 12345
    # evm_address = wallet_manager.create_and_store_evm(user_id)
    # sol_address = wallet_manager.create_and_store_solana(user_id)
    # print(f"EVM Address: {evm_address}")
    # print(f"Solana Address: {sol_address}")
    # settings = wallet_manager.get_user_settings(user_id)
    # print(f"Settings: Slippage={settings[0]}%, Hype Yield={settings[1]}, Stables Yield={settings[2]}")
    print(wallet_manager.get_evm_wallet())
    db.close()
