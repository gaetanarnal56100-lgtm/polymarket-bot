"""
setup_auth.py — Génère les clés API Polymarket depuis ta clé privée
===================================================================

Lance une seule fois AVANT de démarrer le bot :
  python setup_auth.py

Ce script :
  1. Lit POLY_PRIVATE_KEY depuis .env
  2. Appelle create_or_derive_api_key() → obtient apiKey, secret, passphrase
  3. Les écrit automatiquement dans .env

Prérequis :
  - pip install py-clob-client-v2
  - .env avec POLY_PRIVATE_KEY=0x... et DEPOSIT_WALLET_ADDRESS=0x...
"""
from __future__ import annotations
import os
import sys

def main():
    from dotenv import load_dotenv, set_key
    load_dotenv()

    private_key = os.getenv("POLY_PRIVATE_KEY", "")
    funder      = os.getenv("DEPOSIT_WALLET_ADDRESS", "")

    if not private_key or private_key == "0x...":
        print("❌ POLY_PRIVATE_KEY non configuré dans .env")
        sys.exit(1)
    if not funder or funder == "0x...":
        print("❌ DEPOSIT_WALLET_ADDRESS non configuré dans .env")
        print("   → Va sur polymarket.com/settings pour trouver ton adresse")
        sys.exit(1)

    try:
        from py_clob_client.client import ClobClient
    except ImportError:
        print("❌ py-clob-client non installé")
        print("   Lance : pip install py-clob-client")
        sys.exit(1)

    print("🔑 Connexion à Polymarket CLOB…")
    try:
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=137,
            key=private_key,
            signature_type=1,  # L1 pour dériver les clés
        )
        creds = client.create_or_derive_api_creds()
    except Exception as e:
        print(f"❌ Erreur : {e}")
        print("\nVérifie que :")
        print("  - POLY_PRIVATE_KEY commence par 0x")
        print("  - Tu as du POL (MATIC) sur Polygon pour le gas")
        print("  - Tu as déjà créé un compte sur polymarket.com")
        sys.exit(1)

    # create_or_derive_api_creds retourne un objet ApiCreds
    if hasattr(creds, 'api_key'):
        api_key    = creds.api_key
        secret     = creds.api_secret
        passphrase = creds.api_passphrase
    else:
        # fallback dict (anciennes versions)
        api_key    = creds.get("apiKey") or creds.get("api_key") or creds.get("key", "")
        secret     = creds.get("secret", "")
        passphrase = creds.get("passphrase", "")

    if not api_key:
        print(f"❌ Réponse inattendue : {creds}")
        sys.exit(1)

    # Écrire dans .env
    env_file = ".env"
    if not os.path.exists(env_file):
        # Créer depuis .env.example si absent
        import shutil
        shutil.copy(".env.example", env_file)

    set_key(env_file, "POLY_API_KEY",    api_key)
    set_key(env_file, "POLY_SECRET",     secret)
    set_key(env_file, "POLY_PASSPHRASE", passphrase)

    print("\n✅ Clés API générées et sauvegardées dans .env :")
    print(f"   POLY_API_KEY    = {api_key}")
    print(f"   POLY_SECRET     = {secret[:8]}…")
    print(f"   POLY_PASSPHRASE = {passphrase[:8]}…")
    print("\n👉 Lance maintenant : python main.py")


if __name__ == "__main__":
    main()
