from cryptography.fernet import Fernet
from passlib.context import CryptContext

from app.config import settings

# 1. Configuração para Hashing de Senhas/Segredos
# Usamos o `bcrypt` que é um padrão forte para hashing.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_secret(plain_secret: str, hashed_secret: str) -> bool:
    """Verifica uma chave secreta contra seu hash."""
    return pwd_context.verify(plain_secret, hashed_secret)

def get_secret_hash(secret: str) -> str:
    """Cria o hash de uma chave secreta."""
    return pwd_context.hash(secret)


# 2. Configuração para Criptografia de Dados
# Usamos Fernet para criptografia simétrica. A chave deve ser mantida em segredo!
# A chave é carregada das configurações, que a lêem do arquivo .env.
try:
    fernet = Fernet(settings.encryption_key.encode())
except Exception as e:
    # Lança um erro claro se a chave não for válida para que o app não inicie.
    raise ValueError(f"A ENCRYPTION_KEY não é uma chave Fernet válida. Erro: {e}")

def encrypt_data(data: str) -> bytes:
    """Criptografa uma string e retorna bytes."""
    if not isinstance(data, str):
        raise TypeError("O dado a ser criptografado deve ser uma string.")
    return fernet.encrypt(data.encode())

def decrypt_data(encrypted_data) -> str:
    """Descriptografa bytes e retorna uma string."""
    if isinstance(encrypted_data, str):
        if encrypted_data.startswith("\\x"):
            # O Supabase/PostgREST serializa BYTEA como uma string hexadecimal começando com \x
            encrypted_data = bytes.fromhex(encrypted_data[2:])
        else:
            encrypted_data = encrypted_data.encode('latin-1')
    elif not isinstance(encrypted_data, bytes):
        # O psycopg2 pode retornar `memoryview`, então convertemos para bytes.
        encrypted_data = bytes(encrypted_data)
    return fernet.decrypt(encrypted_data).decode()
