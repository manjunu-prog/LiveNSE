from __future__ import annotations

import base64
import hashlib
import os
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pyotp
import requests
from fyers_apiv3 import fyersModel


def _load_dotenv_if_present() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ[key] = value


_load_dotenv_if_present()


def _b64(value: str) -> str:
    return base64.b64encode(str(value).encode()).decode()


def _app_hash(app_id: str, app_secret: str) -> str:
    raw = f"{app_id}-100:{app_secret}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _normalize_client_id(app_id: str) -> str:
    return app_id if app_id.endswith("-100") else f"{app_id}-100"


def _fallback_secrets_path() -> Path:
    return Path.home() / "Desktop" / "OptionTerminal" / ".streamlit" / "secrets.toml"


def _read_fallback_secrets() -> tuple[dict[str, str], str | None]:
    path = Path(os.getenv("FYERS_SECRETS_PATH", str(_fallback_secrets_path()))).expanduser()
    if not path.exists():
        return {}, None

    with path.open("rb") as handle:
        data = tomllib.load(handle)

    secrets = {
        "fy_id": str(data.get("FYERS_FY_ID", "")).strip(),
        "app_id": str(data.get("FYERS_APP_ID", "")).strip(),
        "app_secret": str(data.get("FYERS_APP_SECRET", "")).strip(),
        "redirect_uri": str(data.get("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/index.html")).strip(),
        "pin": str(data.get("FYERS_PIN", "")).strip(),
        "totp_key": str(data.get("FYERS_TOTP_KEY", "")).strip(),
    }
    return secrets, str(path)


def _read_streamlit_secrets() -> tuple[dict[str, str], str | None]:
    try:
        import streamlit as st

        flat = st.secrets
    except Exception:
        return {}, None

    def pick(key: str, nested_key: str | None = None, default: str = "") -> str:
        value = flat.get(key, "")
        if value:
            return str(value).strip()
        if nested_key is None:
            return default
        try:
            nested_value = flat.get("fyers", {}).get(nested_key, "")
        except Exception:
            nested_value = ""
        return str(nested_value).strip() if nested_value else default

    secrets = {
        "fy_id": pick("FYERS_FY_ID", "fy_id"),
        "app_id": pick("FYERS_APP_ID", "app_id"),
        "app_secret": pick("FYERS_APP_SECRET", "app_secret"),
        "redirect_uri": pick("FYERS_REDIRECT_URI", "redirect_uri", "https://trade.fyers.in/api-login/redirect-uri/index.html"),
        "pin": pick("FYERS_PIN", "pin"),
        "totp_key": pick("FYERS_TOTP_KEY", "totp_key"),
    }
    return secrets, "Streamlit secrets"


def resolve_fyers_credentials() -> dict[str, str]:
    creds = {
        "fy_id": os.getenv("FYERS_FY_ID", "").strip(),
        "app_id": os.getenv("FYERS_APP_ID", "").strip(),
        "app_secret": os.getenv("FYERS_APP_SECRET", "").strip(),
        "redirect_uri": os.getenv("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/index.html").strip(),
        "pin": os.getenv("FYERS_PIN", "").strip(),
        "totp_key": os.getenv("FYERS_TOTP_KEY", "").strip(),
    }
    required = ["fy_id", "app_id", "app_secret", "redirect_uri", "pin", "totp_key"]
    if all(creds.get(key) for key in required):
        return {**creds, "source": ".env / process env"}

    streamlit_secrets, source = _read_streamlit_secrets()
    if source and all(streamlit_secrets.get(key) for key in required):
        return {**streamlit_secrets, "source": source}

    fallback, path = _read_fallback_secrets()
    if path and all(fallback.get(key) for key in required):
        return {**fallback, "source": path}

    return {**creds, "source": ".env / process env"}


def fyers_credentials_source() -> str:
    return resolve_fyers_credentials()["source"]


@dataclass
class FyersDataClient:
    fyers: Any

    @classmethod
    @lru_cache(maxsize=1)
    def from_env(cls) -> "FyersDataClient":
        credentials = resolve_fyers_credentials()
        fy_id = credentials["fy_id"]
        app_id = credentials["app_id"]
        app_secret = credentials["app_secret"]
        redirect_uri = credentials["redirect_uri"]
        pin = credentials["pin"]
        totp_key = credentials["totp_key"]

        if not all([fy_id, app_id, app_secret, redirect_uri, pin, totp_key]):
            raise RuntimeError(
                "Missing FYERS env vars. Set FYERS_FY_ID, FYERS_APP_ID, FYERS_APP_SECRET, "
                "FYERS_REDIRECT_URI, FYERS_PIN, and FYERS_TOTP_KEY."
            )

        access_token = cls._login(fy_id, app_id, app_secret, redirect_uri, pin, totp_key)
        client_id = _normalize_client_id(app_id)
        fyers = fyersModel.FyersModel(client_id=client_id, token=access_token, is_async=False, log_path="")
        return cls(fyers=fyers)

    @staticmethod
    def _login(fy_id: str, app_id: str, app_secret: str, redirect_uri: str, pin: str, totp_key: str) -> str:
        session = requests.Session()

        response = session.post(
            "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2",
            json={"fy_id": _b64(fy_id), "app_id": "2"},
            timeout=20,
        ).json()
        if "request_key" not in response:
            raise RuntimeError(f"OTP Error: {response}")
        request_key = response["request_key"]

        response = session.post(
            "https://api-t2.fyers.in/vagator/v2/verify_otp",
            json={"request_key": request_key, "otp": pyotp.TOTP(totp_key).now()},
            timeout=20,
        ).json()
        if "request_key" not in response:
            raise RuntimeError(f"OTP Verification Failed: {response}")
        request_key = response["request_key"]

        response = session.post(
            "https://api-t2.fyers.in/vagator/v2/verify_pin_v2",
            json={"request_key": request_key, "identity_type": "pin", "identifier": _b64(pin)},
            timeout=20,
        ).json()
        login_token = response.get("data", {}).get("access_token")
        if not login_token:
            raise RuntimeError(f"PIN Verification Failed: {response}")

        response = session.post(
            "https://api-t1.fyers.in/api/v3/token",
            headers={"Authorization": f"Bearer {login_token}"},
            json={
                "fyers_id": fy_id,
                "app_id": app_id,
                "redirect_uri": redirect_uri,
                "appType": "100",
                "code_challenge": "",
                "state": "option_chain_desk",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True,
            },
            timeout=20,
        ).json()

        auth_url = response.get("Url")
        if not auth_url:
            raise RuntimeError(response)
        auth_code = parse_qs(urlparse(auth_url).query)["auth_code"][0]

        response = session.post(
            "https://api-t1.fyers.in/api/v3/validate-authcode",
            json={"grant_type": "authorization_code", "appIdHash": _app_hash(app_id, app_secret), "code": auth_code},
            timeout=20,
        ).json()
        access_token = response.get("access_token")
        if not access_token:
            raise RuntimeError(response)
        return access_token

    def fetch_history(self, symbol: str, resolution: str, start_date: str, end_date: str) -> pd.DataFrame:
        payload = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "1",
            "range_from": start_date,
            "range_to": end_date,
            "cont_flag": "1",
        }
        response = self.fyers.history(data=payload)
        if response.get("s") != "ok":
            raise RuntimeError(response.get("message", "FYERS history request failed."))

        candles = response.get("candles", [])
        if not candles:
            return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df.dropna(subset=["timestamp", "open", "high", "low", "close"]).sort_values("timestamp").reset_index(drop=True)
