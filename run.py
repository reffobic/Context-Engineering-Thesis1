"""
LongRAG Demo — run this to try the system.

FREE mode (no API key):
    python run.py

LLM mode (requires API key):
    python run.py --llm
"""

import argparse
from longrag import Document, LongRAG

# ── Sample corpus ────────────────────────────────────────────────────────────
# Replace these with your own documents to test on your own data.

CORPUS = [
    Document("d1", "Python (programming language)",
        ("Python is a high-level, general-purpose programming language. "
         "Its design philosophy emphasizes code readability. "
         "Python is dynamically typed and garbage-collected. ") * 6,
        links=["d2"]),

    Document("d2", "Guido van Rossum",
        ("Guido van Rossum is a Dutch programmer best known as the creator of Python. "
         "He was Python's principal author until July 2018. "
         "Van Rossum attended Vrije Universiteit in Amsterdam. ") * 4,
        links=["d1"]),

    Document("d3", "Java (programming language)",
        ("Java is a high-level, class-based, object-oriented programming language. "
         "Java applications are typically compiled to bytecode that can run on any JVM. "
         "Java was originally developed by James Gosling at Sun Microsystems. ") * 5,
        links=["d4"]),

    Document("d4", "James Gosling",
        ("James Gosling is a Canadian computer scientist, best known as the "
         "founder and lead designer behind the Java programming language. "
         "He was elected a member of the National Academy of Engineering. ") * 4,
        links=["d3"]),

    Document("d5", "Rust (programming language)",
        ("Rust is a multi-paradigm, general-purpose programming language. "
         "Rust emphasizes performance, type safety, and concurrency. "
         "It was designed by Graydon Hoare at Mozilla Research. ") * 7,
        links=[]),

    # Short linked docs — will be grouped into one long unit (the paper's key idea)
    Document("d6", "AFL Rising Star Award",
        "The NAB AFL Rising Star award is given annually to a standout young player. "
        "The 2015 award was won by Jesse Hogan of Melbourne who polled 49 votes. "
        "Patrick Cripps finished second with 41 votes.",
        links=["d7"]),

    Document("d7", "Jesse Hogan",
        "Jesse Hogan plays in the Australian Football League. "
        "He is a key forward and stands 1.95 m tall and weighs 100 kg. "
        "Hogan was selected by Melbourne Football Club.",
        links=["d6"]),
]

QUESTIONS = [
    "Who created the Python programming language?",
    "Who is the founder of Java?",
    "What is the height of the player who won the 2015 AFL Rising Star award?",
    "What language emphasizes performance and memory safety?",
]


def main():
    parser = argparse.ArgumentParser(description="LongRAG Demo")
    parser.add_argument("--llm", action="store_true",
                        help="Use Claude API as reader (requires ANTHROPIC_API_KEY)")
    parser.add_argument("--model", default="claude-haiku-4-5-20251001",
                        help="Claude model to use (default: haiku, cheapest)")
    parser.add_argument("--top-k", type=int, default=3,
                        help="Number of retrieval units to fetch (default: 3)")
    args = parser.parse_args()

    print("=" * 60)
    print("  LongRAG Demo")
    print("=" * 60)

    # Build the system
    system = LongRAG(
        CORPUS,
        max_tokens_per_unit=4000,
        short_doc_threshold=60,   # docs under 60 words get grouped
        use_llm=args.llm,
        claude_model=args.model,
    )

    print()

    # Run each question
    for question in QUESTIONS:
        print(f"Q: {question}")
        result = system.answer(question, top_k=args.top_k, verbose=True)
        print(f"→ Short answer : {result['short_answer']}")
        print(f"→ Long answer  : {result['long_answer'][:120]}...")
        print(f"→ Reader used  : {result['reader']}")
        print(f"→ Context size : {result['context_tokens']} tokens")
        print("-" * 60)


if __name__ == "__main__":
    main()
