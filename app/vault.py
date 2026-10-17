import os

from cryptography.fernet import Fernet

# The master key should be injected via environment variable in production
# Example: generate VAULT_MASTER_KEY with:
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
MASTER_KEY = os.getenv("VAULT_MASTER_KEY", "uE2-1z7H7bXm1oYVq_T0G4e7b8G-4K8P1X_J5eX8_gI=")

cipher_suite = Fernet(MASTER_KEY.encode("utf-8"))


def encrypt_credential(plaintext: str) -> str:
    return cipher_suite.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_credential(encrypted: str) -> str:
    return cipher_suite.decrypt(encrypted.encode("utf-8")).decode("utf-8")
