#!/usr/bin/env python3
"""
ClimaRC v2.3 - Script para registrar Webhook y Menú de Comandos en Telegram Bot API
Configura el webhook seguro con secret_token y drop_pending_updates, registra los 6 comandos
en el menú interactivo de Telegram y verifica el estado con getWebhookInfo.

Uso:
    python scripts/register_telegram_webhook.py [--token BOT_TOKEN] [--secret SECRET_TOKEN] [--url WEBHOOK_URL]
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error

DEFAULT_WEBHOOK_URL = "https://climarc-api.onrender.com/api/telegram/webhook"

COMMANDS = [
    {"command": "ahora", "description": "Clima actual en tiempo real en Río Cuarto"},
    {"command": "noche", "description": "Pronóstico de la noche y madrugada"},
    {"command": "granizo", "description": "Reporte completo del Módulo Antigranizo"},
    {"command": "satelite", "description": "Foto satelital GOES-19 IR anotada en vivo"},
    {"command": "ruta", "description": "Estado del tiempo en rutas y corredores"},
    {"command": "ayuda", "description": "Lista de comandos disponibles y ayuda"},
]


def post_telegram_api(token: str, method: str, payload: dict) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "ClimaRC-WebhookRegistrar/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8")
        try:
            return json.loads(err_body)
        except Exception:
            return {"ok": False, "error_code": err.code, "description": err_body}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_telegram_api(token: str, method: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "ClimaRC-WebhookRegistrar/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        err_body = err.read().decode("utf-8")
        try:
            return json.loads(err_body)
        except Exception:
            return {"ok": False, "error_code": err.code, "description": err_body}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description="Registrar webhook y comandos de ClimaRC en Telegram Bot API")
    parser.add_argument("--token", default=os.getenv("TELEGRAM_BOT_TOKEN", ""), help="Telegram Bot Token")
    parser.add_argument("--secret", default=os.getenv("TELEGRAM_WEBHOOK_SECRET", ""), help="Telegram Webhook Secret Token (32 chars)")
    parser.add_argument("--url", default=os.getenv("TELEGRAM_WEBHOOK_URL", DEFAULT_WEBHOOK_URL), help="URL pública del webhook")
    args = parser.parse_args()

    token = args.token.strip()
    secret = args.secret.strip()
    webhook_url = args.url.strip()

    if not token:
        print("❌ ERROR: TELEGRAM_BOT_TOKEN no proporcionado ni encontrado en entorno.")
        sys.exit(1)

    if not secret:
        print("❌ ERROR: TELEGRAM_WEBHOOK_SECRET no proporcionado ni encontrado en entorno.")
        sys.exit(1)

    print("=================================================================")
    print("📡 REGISTRO DE WEBHOOK Y MENÚ DE COMANDOS — CLIMARC BOT")
    print("=================================================================")
    print(f"🔗 Webhook Target URL : {webhook_url}")
    print(f"🔒 Secret Token Length: {len(secret)} caracteres")
    print("-----------------------------------------------------------------")

    # 1. Configurar setWebhook
    print("1️⃣ Configurando webhook vía setWebhook...")
    webhook_payload = {
        "url": webhook_url,
        "secret_token": secret,
        "allowed_updates": ["message"],
        "drop_pending_updates": True,
    }
    wh_res = post_telegram_api(token, "setWebhook", webhook_payload)
    if wh_res.get("ok"):
        print(f"   ✅ setWebhook exitoso: {wh_res.get('description', 'OK')}")
    else:
        print(f"   ❌ Fallo setWebhook: {wh_res}")
        sys.exit(1)

    # 2. Configurar setMyCommands
    print("2️⃣ Registrando menú interactivo de comandos vía setMyCommands...")
    cmd_payload = {
        "commands": COMMANDS,
    }
    cmd_res = post_telegram_api(token, "setMyCommands", cmd_payload)
    if cmd_res.get("ok"):
        print(f"   ✅ setMyCommands exitoso ({len(COMMANDS)} comandos registrados):")
        for c in COMMANDS:
            print(f"      • /{c['command']} — {c['description']}")
    else:
        print(f"   ⚠️ Fallo setMyCommands: {cmd_res}")

    # 3. Verificar estado del webhook con getWebhookInfo
    print("3️⃣ Verificando estado con getWebhookInfo...")
    info_res = get_telegram_api(token, "getWebhookInfo")
    if info_res.get("ok"):
        res = info_res.get("result", {})
        print("   ✅ Webhook Info:")
        print(f"      • URL: {res.get('url')}")
        print(f"      • Custom Certificate: {res.get('has_custom_certificate')}")
        print(f"      • Pending Update Count: {res.get('pending_update_count')}")
        print(f"      • Last Error Date: {res.get('last_error_date')}")
        print(f"      • Last Error Message: {res.get('last_error_message') or 'Ninguno (OK)'}")
    else:
        print(f"   ⚠️ Error consultando getWebhookInfo: {info_res}")

    print("-----------------------------------------------------------------")
    print("🎉 Registro completado exitosamente.")


if __name__ == "__main__":
    main()
