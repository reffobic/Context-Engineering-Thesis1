import sys
sys.path.insert(0, ".")

from diamond.sufficiency_assessor import assess_sufficiency

# Test 1: obviously sufficient chunks
good_chunks = [
    {
        "text": "def can_access(self, permission, view_name):\n    return permission in self.get_user_permissions()",
        "path": "superset/security/manager.py",
        "symbol": "can_access",
        "language": "python",
        "final_score": 0.55,
    }
]

# Test 2: obviously insufficient chunks
bad_chunks = [
    {
        "text": "import logging\nlogger = logging.getLogger(__name__)",
        "path": "superset/utils/log.py",
        "symbol": "",
        "language": "python",
        "final_score": 0.32,
    }
]

query = "How does Superset check if a user has permission to download data as CSV?"

print("=== Good chunks ===")
print(assess_sufficiency(query, good_chunks))

print("\n=== Bad chunks ===")
print(assess_sufficiency(query, bad_chunks))

print("\n=== Empty chunks ===")
print(assess_sufficiency(query, []))
