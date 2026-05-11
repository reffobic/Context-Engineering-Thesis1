import chromadb
client = chromadb.PersistentClient(path="./chroma_db")
col = client.get_collection("code_embeddings")
result = col.get(limit=1, include=["embeddings"])
print(len(result["embeddings"][0]))
