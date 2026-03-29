from pathlib import Path
from .prompt_builder import build_coding_prompt, ContextChunk

def inject_context(user_prompt: str, chunks: list[dict]) -> str:
    context_chunks = [
        ContextChunk(
            text=c["code"],
            path=c["metadata"]["file"],
            symbol=c["metadata"]["function"],
            language=c["metadata"]["language"],
            start_line=c["metadata"]["start_line"],
        )
        for c in chunks
    ]
    system_prompt, user_message = build_coding_prompt(user_prompt, context_chunks)
    return f"<SYSTEM>\n{system_prompt}\n</SYSTEM>\n\n<USER>\n{user_message}\n</USER>"