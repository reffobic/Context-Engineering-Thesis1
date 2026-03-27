# create_mock_db.py
# Run this ONCE to create a fake codebase database for testing
# It simulates exactly what Person 2 would build

import chromadb
from sentence_transformers import SentenceTransformer

print("Loading embedding model (downloads ~80MB first time)...")
model = SentenceTransformer("all-MiniLM-L6-v2")

print("Creating ChromaDB at ./data/chroma_db ...")
client = chromadb.PersistentClient(path="./data/chroma_db")

# Delete collection if it already exists (clean start)
try:
    client.delete_collection("codebase")
except:
    pass

collection = client.create_collection("codebase")

# ─────────────────────────────────────────────────────────────
# FAKE CODEBASE — 10 simple code chunks
# These simulate what Person 2 would extract from real files
# ─────────────────────────────────────────────────────────────

chunks = [
    {
        "id":       "chunk_1",
        "code":     """def hash_password(password: str) -> str:
    import hashlib
    return hashlib.sha256(password.encode()).hexdigest()""",
        "file":     "auth/security.py",
        "function": "hash_password",
        "language": "python"
    },
    {
        "id":       "chunk_2",
        "code":     """def verify_password(plain: str, hashed: str) -> bool:
    return hash_password(plain) == hashed""",
        "file":     "auth/security.py",
        "function": "verify_password",
        "language": "python"
    },
    {
        "id":       "chunk_3",
        "code":     """def get_user(user_id: int):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise ValueError(f"User {user_id} not found")
    return user""",
        "file":     "api/routes.py",
        "function": "get_user",
        "language": "python"
    },
    {
        "id":       "chunk_4",
        "code":     """def create_user(name: str, email: str):
    user = User(name=name, email=email)
    db.add(user)
    db.commit()
    return user""",
        "file":     "api/routes.py",
        "function": "create_user",
        "language": "python"
    },
    {
        "id":       "chunk_5",
        "code":     """def add_logging(func):
    import logging
    def wrapper(*args, **kwargs):
        logging.info(f"Calling {func.__name__}")
        result = func(*args, **kwargs)
        logging.info(f"Done {func.__name__}")
        return result
    return wrapper""",
        "file":     "utils/logger.py",
        "function": "add_logging",
        "language": "python"
    },
    {
        "id":       "chunk_6",
        "code":     """def log_error(message: str, error: Exception):
    import logging
    logging.error(f"{message}: {str(error)}")""",
        "file":     "utils/logger.py",
        "function": "log_error",
        "language": "python"
    },
    {
        "id":       "chunk_7",
        "code":     """def connect_database(url: str):
    from sqlalchemy import create_engine
    engine = create_engine(url)
    return engine""",
        "file":     "db/connection.py",
        "function": "connect_database",
        "language": "python"
    },
    {
        "id":       "chunk_8",
        "code":     """def run_query(sql: str, params: dict = {}):
    with engine.connect() as conn:
        result = conn.execute(sql, params)
        return result.fetchall()""",
        "file":     "db/connection.py",
        "function": "run_query",
        "language": "python"
    },
    {
        "id":       "chunk_9",
        "code":     """def send_email(to: str, subject: str, body: str):
    import smtplib
    server = smtplib.SMTP('smtp.gmail.com', 587)
    server.sendmail('app@example.com', to, body)
    server.quit()""",
        "file":     "notifications/email.py",
        "function": "send_email",
        "language": "python"
    },
    {
        "id":       "chunk_10",
        "code":     """def validate_email(email: str) -> bool:
    import re
    pattern = r'^[a-zA-Z0-9+_.-]+@[a-zA-Z0-9.-]+$'
    return bool(re.match(pattern, email))""",
        "file":     "notifications/email.py",
        "function": "validate_email",
        "language": "python"
    },
]

# ─────────────────────────────────────────────────────────────
# Convert each code chunk to a vector and store in ChromaDB
# This is exactly what Person 2's indexer does
# ─────────────────────────────────────────────────────────────

print(f"\nIndexing {len(chunks)} code chunks...")

for chunk in chunks:
    # Convert code text → 384 numbers
    embedding = model.encode([chunk["code"]])[0].tolist()

    # Store in ChromaDB
    collection.add(
        ids        =[chunk["id"]],
        documents  =[chunk["code"]],
        embeddings =[embedding],
        metadatas  =[{
            "file":     chunk["file"],
            "function": chunk["function"],
            "language": chunk["language"]
        }]
    )
    print(f"  ✓ Indexed {chunk['function']}()  ←  {chunk['file']}")

print(f"\n✅ Mock DB ready! Total chunks stored: {collection.count()}")
print("   Location: ./data/chroma_db")
print("   Collection: codebase")
print("\nYou can now run: python test_retrieval.py")