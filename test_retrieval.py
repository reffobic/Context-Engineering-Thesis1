# test_retrieval.py
# Run this to test all 4 queries against the mock DB

from retrieval_tool import retrieve_context, vector_retrieve, hybrid_retrieve

DIVIDER = "─" * 55

def print_results(results: list[dict]):
    for i, r in enumerate(results):
        print(f"\n  Result #{i+1}")
        print(f"  File:         {r['metadata']['file']}")
        print(f"  Function:     {r['metadata']['function']}()")
        print(f"  Similarity:   {r.get('similarity', '?')}")
        print(f"  Hybrid Score: {r.get('hybrid_score', '?')}")
        print(f"  Final Score:  {r.get('final_score', '?')}  ← ranked")
        print(f"  Code preview: {r['code'][:60].strip()}...")


# ─────────────────────────────────────────────────────────────
# TEST 1: password hashing
# Expected top result: hash_password() in auth/security.py
# ─────────────────────────────────────────────────────────────
print(f"\n{DIVIDER}")
print("TEST 1: 'function that hashes a password'")
print(DIVIDER)
results = retrieve_context("function that hashes a password", top_k=3)
print_results(results)


# ─────────────────────────────────────────────────────────────
# TEST 2: logging
# Expected top result: add_logging() or log_error() in utils/logger.py
# ─────────────────────────────────────────────────────────────
print(f"\n{DIVIDER}")
print("TEST 2: 'add logging to a function'")
print(DIVIDER)
results = retrieve_context("add logging to a function", top_k=3)
print_results(results)


# ─────────────────────────────────────────────────────────────
# TEST 3: database connection
# Expected top result: connect_database() in db/connection.py
# ─────────────────────────────────────────────────────────────
print(f"\n{DIVIDER}")
print("TEST 3: 'connect to a database'")
print(DIVIDER)
results = retrieve_context("connect to a database", top_k=3)
print_results(results)


# ─────────────────────────────────────────────────────────────
# TEST 4: email validation
# Expected top result: validate_email() in notifications/email.py
# ─────────────────────────────────────────────────────────────
print(f"\n{DIVIDER}")
print("TEST 4: 'validate user email address'")
print(DIVIDER)
results = retrieve_context("validate user email address", top_k=3)
print_results(results)


print(f"\n{DIVIDER}")
print("✅ All tests done!")
print(DIVIDER)