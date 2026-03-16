"""
File map for the AI chatbot + RAG project from Dr. Ibrahim's Claude chat.

Used to test file-level context selection: e.g. "Which files are needed to
add image upload to custom chat?" — the selector should return a minimal set.

Source: shared chat (file list + purposes), reviewed for both RAG and custom chats.
"""

from src.file_context.file_map import FileEntry, FileMap

# Full file map (reviewed version with chatbot_files vs rag_files split)
AI_PROJECT_ENTRIES = [
    # Entry point & config
    FileEntry("main.py", "FastAPI app initialization, middleware, router mounting"),
    FileEntry("config.py", "All env vars and settings (DB URL, API keys, model names, storage paths)"),
    FileEntry("dependencies.py", "Shared FastAPI dependency injections (DB session, current user, auth guards)"),
    # API
    FileEntry("api/auth.py", "Register, login, logout, token refresh"),
    FileEntry("api/users.py", "User profile CRUD"),
    FileEntry("api/chatbots.py", "Create, read, update, delete chatbot configurations"),
    FileEntry("api/chatbot_files.py", "Upload, list, delete files attached to a specific chatbot"),
    FileEntry("api/chatbot_sessions.py", "Start and continue chat sessions with a chatbot"),
    FileEntry("api/rag_systems.py", "Create, read, update, delete RAG system configurations"),
    FileEntry("api/rag_files.py", "Upload, list, delete files belonging to a RAG system"),
    FileEntry("api/rag_sessions.py", "Start and continue chat sessions against a RAG system"),
    # Services — shared
    FileEntry("services/auth_service.py", "JWT creation/validation, password hashing, token blacklisting"),
    FileEntry("services/user_service.py", "User creation, update, quota management"),
    FileEntry("services/file_service.py", "File validation, storage write/delete, metadata persistence (shared)"),
    # Services — chatbot
    FileEntry("services/chatbot_service.py", "Chatbot CRUD, prompt management, settings"),
    FileEntry("services/chatbot_file_service.py", "On upload: extract and store raw text from chatbot-attached files"),
    FileEntry("services/chatbot_context_builder.py", "Assembles full LLM context: system prompt + file content + history"),
    FileEntry("services/chatbot_chat_service.py", "Drives chatbot session: context builder → LLM → persist messages"),
    # Services — RAG
    FileEntry("services/rag_system_service.py", "RAG system CRUD, vector store namespace per system"),
    FileEntry("services/rag_indexing_service.py", "On upload: load → chunk → embed → store, track indexing status"),
    FileEntry("services/rag_chat_service.py", "RAG session: retrieve chunks → augmented prompt → LLM → persist"),
    # RAG pipeline
    FileEntry("rag/document_loader.py", "Parse uploaded files into raw text (PDF, DOCX, TXT, etc.)"),
    FileEntry("rag/chunker.py", "Split documents into chunks (size, overlap, semantic)"),
    FileEntry("rag/embedder.py", "Generate embeddings via configured model"),
    FileEntry("rag/vector_store.py", "Vector DB abstraction: upsert, query, delete by namespace"),
    FileEntry("rag/retriever.py", "Similarity search, re-ranking, top-k selection"),
    FileEntry("rag/pipeline.py", "Orchestrates indexing: loader → chunker → embedder → vector store"),
    # Models
    FileEntry("models/user.py", "User table"),
    FileEntry("models/chatbot.py", "Chatbot config table: owner, name, system prompt, model settings"),
    FileEntry("models/chatbot_file.py", "Files attached to chatbots: storage path, extracted text, status"),
    FileEntry("models/rag_system.py", "RAG system table: owner, name, vector store namespace, config"),
    FileEntry("models/rag_file.py", "RAG files: storage path, indexing status (pending/indexed/failed)"),
    FileEntry("models/chat_session.py", "Session table: linked to chatbot or RAG system"),
    FileEntry("models/chat_message.py", "Individual messages: session id, role, content, timestamp"),
    # Schemas
    FileEntry("schemas/auth.py", "Login, register, token schemas"),
    FileEntry("schemas/user.py", "UserOut, UserUpdate"),
    FileEntry("schemas/chatbot.py", "ChatbotCreate, ChatbotUpdate, ChatbotOut"),
    FileEntry("schemas/chatbot_file.py", "ChatbotFileOut, upload response"),
    FileEntry("schemas/rag_system.py", "RAGSystemCreate, RAGSystemUpdate, RAGSystemOut"),
    FileEntry("schemas/rag_file.py", "RAGFileOut with indexing status"),
    FileEntry("schemas/chat.py", "ChatMessageIn, ChatMessageOut, ChatHistoryOut (shared)"),
    # DB
    FileEntry("db/database.py", "SQLAlchemy engine, session factory, Base"),
    FileEntry("db/init_db.py", "Table creation on first run"),
    # Utils
    FileEntry("utils/llm_client.py", "Unified LLM abstraction (OpenAI, Anthropic, Ollama)"),
    FileEntry("utils/storage.py", "File storage abstraction (local or S3)"),
    FileEntry("utils/token_counter.py", "Token counting for context window limits"),
    FileEntry("utils/text_extractor.py", "Extract plain text from binary files"),
    FileEntry("utils/exceptions.py", "Custom exceptions (NotFoundError, QuotaExceeded, etc.)"),
    FileEntry("utils/logger.py", "Structured logging config"),
    FileEntry("utils/validators.py", "File type/size validation, prompt sanitization"),
]

# Gold set for: "Enable uploading an image to the custom chat" (from Claude's answer in the chat)
IMAGE_UPLOAD_GOLD_MUST_HAVE = [
    "services/chatbot_context_builder.py",
    "utils/llm_client.py",
    "schemas/chat.py",
    "api/chatbot_sessions.py",
    "services/chatbot_chat_service.py",
    "models/chat_message.py",
    "utils/validators.py",
]

IMAGE_UPLOAD_GOLD_SHOULD_HAVE = [
    "services/chatbot_file_service.py",
    "utils/storage.py",
    "models/chatbot_file.py",
    "config.py",
]


def get_ai_project_file_map() -> FileMap:
    return FileMap(name="AI Chatbot + RAG Backend", entries=AI_PROJECT_ENTRIES)
