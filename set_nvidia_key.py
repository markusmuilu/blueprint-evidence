"""Store the NVIDIA Build API key in .env (git-ignored). Run it yourself: python set_nvidia_key.py
The key is read hidden and never printed."""
import getpass
import pathlib

env = pathlib.Path(__file__).with_name(".env")
key = getpass.getpass("NVIDIA API key (hidden): ").strip()
if not key:
    raise SystemExit("No key entered, nothing changed.")
lines = [l for l in (env.read_text(encoding="utf-8").splitlines() if env.exists() else [])
         if not l.startswith("NVIDIA_API_KEY=")]
lines.append(f"NVIDIA_API_KEY={key}")
env.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("Saved NVIDIA_API_KEY to", env)
