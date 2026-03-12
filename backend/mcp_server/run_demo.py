import requests

prompt = input("Ask the system: ")

response = requests.post(
    "http://localhost:8000/generate",
    json={"prompt": prompt}
)

print(response.json())