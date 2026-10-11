"""Initialize local configuration without overwriting an existing .env or printing secrets."""
import argparse
import os
import secrets
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="")
parser.add_argument("--model", default="")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
target = root / ".env"
if target.exists():
    raise SystemExit(".env already exists; edit it directly. Nothing was overwritten.")
for value in (args.base_url, args.model):
    if any(character in value for character in "\n\r'\""):
        raise SystemExit("Invalid configuration value")
content = (root / ".env.example").read_text()
content = content.replace("https://your-provider.example/v1", args.base_url)
content = content.replace("your-model-name", args.model)
content = content.replace("NEOBOTS_PASSWORD=", "NEOBOTS_PASSWORD=" + secrets.token_urlsafe(18))
content = content.replace("NEOBOTS_RUNTIME_TOKEN=", "NEOBOTS_RUNTIME_TOKEN=" + secrets.token_urlsafe(36))
descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(descriptor, "w") as file:
    file.write(content)
print("Created .env (mode 600). Set MODEL_API_KEY there. Your web login password is NEOBOTS_PASSWORD.")
