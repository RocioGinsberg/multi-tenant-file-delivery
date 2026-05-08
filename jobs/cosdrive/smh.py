from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote, urlencode

import httpx
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from fastapi import HTTPException

logger = logging.getLogger("jobs.cosdrive.smh")

DEFAULT_SMH_API_BASE_PUBLIC = "https://api.tencentsmh.cn"
DEFAULT_SMH_API_BASE_APP = "https://api.tencentsmh.cn/api/v1"

_user_token_cache: dict = {"token": "", "expires_at": 0.0}
_HAN_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
_upload_semaphore: asyncio.Semaphore | None = None
_rate_limit_lock: asyncio.Lock | None = None
_next_upload_ts = 0.0


def _cfg(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _cfg_url(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip().rstrip("/")


def _cfg_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _cfg_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


class SmhApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        phase: str,
        error_code: str,
        retryable: bool,
        status_code: int = 0,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.error_code = error_code
        self.retryable = retryable
        self.status_code = status_code


class SmhConfigError(RuntimeError):
    def __init__(self, message: str, *, error_code: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = False


def _upload_semaphore_instance() -> asyncio.Semaphore:
    global _upload_semaphore
    if _upload_semaphore is None:
        _upload_semaphore = asyncio.Semaphore(max(1, _cfg_int("SMH_MAX_CONCURRENT_UPLOADS", 2)))
    return _upload_semaphore


def _rate_limit_lock_instance() -> asyncio.Lock:
    global _rate_limit_lock
    if _rate_limit_lock is None:
        _rate_limit_lock = asyncio.Lock()
    return _rate_limit_lock


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


def _raise_http_error(*, phase: str, action: str, status_code: int, text: str, error_code: str) -> None:
    raise SmhApiError(
        f"{action}: {status_code} {text[:200]}",
        phase=phase,
        error_code=error_code,
        retryable=_is_retryable_status(status_code),
        status_code=status_code,
    )


def _is_retryable_exception(exc: BaseException) -> bool:
    explicit = getattr(exc, "retryable", None)
    if isinstance(explicit, bool):
        return explicit
    if isinstance(exc, HTTPException):
        status_code = int(getattr(exc, "status_code", 0) or 0)
        return status_code == 429 or status_code >= 500
    return isinstance(exc, (httpx.TimeoutException, httpx.TransportError, TimeoutError, ConnectionError, OSError))


async def _run_with_async_retry(
    operation,
    *,
    max_attempts: int,
    backoff_ms: int,
    action_label: str,
):
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")

    attempt_no = 0
    while True:
        attempt_no += 1
        try:
            return await operation()
        except Exception as exc:
            if attempt_no >= max_attempts or not _is_retryable_exception(exc):
                raise
            logger.warning(
                "SMH %s retry scheduled: next_attempt=%d/%d error=%s",
                action_label,
                attempt_no + 1,
                max_attempts,
                exc,
            )
            if backoff_ms > 0:
                await asyncio.sleep((backoff_ms * attempt_no) / 1000.0)


async def _throttle_upload() -> None:
    global _next_upload_ts
    min_interval_ms = max(0, _cfg_int("SMH_UPLOAD_MIN_INTERVAL_MS", 0))
    if min_interval_ms <= 0:
        return
    async with _rate_limit_lock_instance():
        now = time.monotonic()
        if now < _next_upload_ts:
            await asyncio.sleep(_next_upload_ts - now)
            now = time.monotonic()
        _next_upload_ts = now + (min_interval_ms / 1000.0)


def norm_name(name: str) -> str:
    if not name:
        return ""
    return "".join(_HAN_RE.findall(str(name)))


def _b64url(data: bytes) -> bytes:
    return base64.urlsafe_b64encode(data).rstrip(b"=")


def _load_private_key() -> RSAPrivateKey:
    key_file = (_cfg("SMH_PRIVATE_KEY_FILE", "") or "").strip()
    if not key_file:
        raise SmhConfigError(
            "SMH_PRIVATE_KEY_FILE 未配置",
            error_code="smh_private_key_path_missing",
        )

    key_path = Path(key_file)
    if not key_path.exists():
        raise SmhConfigError(
            f"SMH 私钥文件不存在: {key_file}",
            error_code="smh_private_key_missing",
        )
    if key_path.is_dir():
        raise SmhConfigError(
            f"SMH 私钥路径当前是目录，不是文件: {key_file}；请检查私钥挂载，确保该路径映射到真实 PEM 文件",
            error_code="smh_private_key_is_directory",
        )
    if not key_path.is_file():
        raise SmhConfigError(
            f"SMH 私钥路径不是常规文件: {key_file}",
            error_code="smh_private_key_not_regular_file",
        )

    try:
        key_bytes = key_path.read_bytes()
    except OSError as exc:
        raise SmhConfigError(
            f"SMH 私钥文件不可读: {key_file} ({exc})",
            error_code="smh_private_key_unreadable",
        ) from exc
    if not key_bytes:
        raise SmhConfigError(
            f"SMH 私钥文件为空: {key_file}；请替换掉 bootstrap 生成的占位文件",
            error_code="smh_private_key_empty",
        )

    try:
        pk = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())
    except (ValueError, TypeError, UnsupportedAlgorithm) as exc:
        raise SmhConfigError(
            f"SMH 私钥格式非法，必须是 PEM 编码的 RSA 私钥: {key_file}",
            error_code="smh_private_key_invalid_pem",
        ) from exc

    if not isinstance(pk, RSAPrivateKey):
        raise SmhConfigError(
            f"SMH 私钥必须是 RSA 格式: {key_file}",
            error_code="smh_private_key_not_rsa",
        )
    return pk


def _build_jws() -> str:
    ttl = _cfg_int("SMH_TOKEN_TTL_SECONDS", 300)
    header = {"alg": "RS256"}
    payload = {
        "exp": int(time.time()) + ttl,
        "countryCode": _cfg("SMH_COUNTRY_CODE", "+86"),
        "phoneNumber": _cfg("SMH_PHONE_NUMBER", ""),
        "type": "user",
    }
    h = _b64url(json.dumps(header, separators=(",", ":")).encode())
    p = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = h + b"." + p
    pk = _load_private_key()
    sig = pk.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return (signing_input + b"." + _b64url(sig)).decode()


async def _ensure_user_token_once(*, force: bool = False) -> str:
    if not force and _user_token_cache["token"] and time.time() < _user_token_cache["expires_at"] - 5:
        return _user_token_cache["token"]

    ttl = _cfg_int("SMH_TOKEN_TTL_SECONDS", 300)
    url = f"{_cfg_url('SMH_API_BASE_PUBLIC', DEFAULT_SMH_API_BASE_PUBLIC)}/user/v1/token"
    params = {
        "auth_type": "jws",
        "app_id": _cfg("SMH_APP_ID", ""),
        "jws_token": _build_jws(),
    }
    body = {
        "countryCode": _cfg("SMH_COUNTRY_CODE", "+86"),
        "phoneNumber": _cfg("SMH_PHONE_NUMBER", ""),
        "type": "user",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, params=params, json=body)
            if r.status_code != 200:
                _raise_http_error(
                    phase="user_token",
                    action="SMH user_token 获取失败",
                    status_code=r.status_code,
                    text=r.text,
                    error_code="smh_user_token_failed",
                )
            data = r.json()
    except httpx.TimeoutException as exc:
        raise SmhApiError(
            "SMH user_token 获取超时",
            phase="user_token",
            error_code="smh_user_token_timeout",
            retryable=True,
        ) from exc
    token = data.get("userToken")
    if not token:
        raise SmhApiError(
            "SMH 响应中缺少 userToken",
            phase="user_token",
            error_code="smh_user_token_missing",
            retryable=False,
        )
    expires_in = data.get("expiresIn", ttl)
    _user_token_cache["token"] = token
    _user_token_cache["expires_at"] = time.time() + max(60, expires_in - 10)
    return token


async def ensure_user_token(*, force: bool = False) -> str:
    return await _run_with_async_retry(
        lambda: _ensure_user_token_once(force=force),
        max_attempts=max(1, _cfg_int("SMH_USER_TOKEN_MAX_ATTEMPTS", 2)),
        backoff_ms=max(0, _cfg_int("SMH_USER_TOKEN_RETRY_BACKOFF_MS", 400)),
        action_label="user_token",
    )


async def _get_access_token_once(space_id: str) -> str:
    user_token = await ensure_user_token()
    org_id = _cfg("SMH_ORG_ID", "")
    library_id = _cfg("SMH_LIBRARY_ID", "")
    url = f"{_cfg_url('SMH_API_BASE_PUBLIC', DEFAULT_SMH_API_BASE_PUBLIC)}/user/v1/space/{org_id}/token/{space_id}"
    params = {"user_token": user_token, "library_id": library_id, "libraryId": library_id}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(url, params=params, json={})
            if r.status_code != 200:
                _raise_http_error(
                    phase="access_token",
                    action=f"SMH access_token 获取失败 (space={space_id})",
                    status_code=r.status_code,
                    text=r.text,
                    error_code="smh_access_token_failed",
                )
            at = r.json().get("accessToken")
            if not at:
                raise SmhApiError(
                    f"SMH 响应中缺少 accessToken (space={space_id})",
                    phase="access_token",
                    error_code="smh_access_token_missing",
                    retryable=False,
                )
            return at
    except httpx.TimeoutException as exc:
        raise SmhApiError(
            f"SMH access_token 获取超时 (space={space_id})",
            phase="access_token",
            error_code="smh_access_token_timeout",
            retryable=True,
        ) from exc


async def get_access_token(space_id: str) -> str:
    return await _run_with_async_retry(
        lambda: _get_access_token_once(space_id),
        max_attempts=max(1, _cfg_int("SMH_ACCESS_TOKEN_MAX_ATTEMPTS", 2)),
        backoff_ms=max(0, _cfg_int("SMH_ACCESS_TOKEN_RETRY_BACKOFF_MS", 400)),
        action_label=f"access_token space={space_id}",
    )


async def _fetch_team_tree_once() -> List[dict]:
    user_token = await ensure_user_token()
    org_id = _cfg("SMH_ORG_ID", "")
    url = f"{_cfg_url('SMH_API_BASE_PUBLIC', DEFAULT_SMH_API_BASE_PUBLIC)}/user/v1/team/{org_id}/"
    params = {
        "user_token": user_token,
        "with_path": "true",
        "check_permission": "1",
        "check_management_permission": "0",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"SMH 团队列表获取失败: {r.status_code} {r.text[:200]}")
        teams = r.json().get("children", [])
    if not isinstance(teams, list):
        raise HTTPException(status_code=502, detail="SMH 团队列表格式异常")
    return teams


async def fetch_team_tree() -> List[dict]:
    return await _run_with_async_retry(
        _fetch_team_tree_once,
        max_attempts=max(1, _cfg_int("SMH_TEAM_LIST_MAX_ATTEMPTS", 2)),
        backoff_ms=max(0, _cfg_int("SMH_TEAM_LIST_RETRY_BACKOFF_MS", 400)),
        action_label="team_list",
    )


def flatten_team_tree(raw_teams: List[dict]) -> List[Dict[str, Any]]:
    flat: List[Dict[str, Any]] = []

    def _walk(node: dict):
        team_id = node.get("teamId") or node.get("id")
        name = node.get("name") or node.get("teamName")
        original_name = node.get("original_name") or name
        space_id = node.get("spaceId") or node.get("space_id")
        org_id = node.get("orgId") or node.get("org_id") or _cfg("SMH_ORG_ID", "")
        if space_id and name:
            flat.append({
                "id": team_id,
                "name": str(name),
                "original_name": str(original_name),
                "spaceId": str(space_id),
                "orgId": str(org_id),
            })
        for child in (node.get("children") or []):
            if isinstance(child, dict):
                _walk(child)

    for n in raw_teams:
        if isinstance(n, dict):
            _walk(n)
    if not flat:
        raise HTTPException(status_code=502, detail="SMH 在线团队列表为空")
    return flat


async def _create_directory_once(
    space_id: str,
    path: str,
    access_token: str,
    conflict_strategy: str = "ask",
) -> bool:
    library_id = _cfg("SMH_LIBRARY_ID", "")
    url = f"{_cfg_url('SMH_API_BASE_APP', DEFAULT_SMH_API_BASE_APP)}/directory/{library_id}/{space_id}/{quote(path)}"
    params = {"access_token": access_token, "conflict_resolution_strategy": conflict_strategy}
    try:
        async with httpx.AsyncClient(timeout=_cfg_float("SMH_MKDIR_TIMEOUT_SECONDS", 15.0)) as client:
            r = await client.put(f"{url}?{urlencode(params)}", json={})
            if r.status_code in (201, 409):
                return True
            if _is_retryable_status(r.status_code):
                _raise_http_error(
                    phase="mkdir",
                    action=f"SMH 创建目录失败 ({path})",
                    status_code=r.status_code,
                    text=r.text,
                    error_code="smh_mkdir_http_failed",
                )
            logger.warning("SMH 创建目录失败: %s → %s %s", path, r.status_code, r.text[:200])
            return False
    except httpx.TimeoutException as exc:
        raise SmhApiError(
            f"SMH 创建目录超时: {path}",
            phase="mkdir",
            error_code="smh_mkdir_timeout",
            retryable=True,
        ) from exc


async def create_directory(space_id: str, path: str, access_token: str, conflict_strategy: str = "ask") -> bool:
    try:
        return await _run_with_async_retry(
            lambda: _create_directory_once(
                space_id,
                path,
                access_token,
                conflict_strategy,
            ),
            max_attempts=max(1, _cfg_int("SMH_MKDIR_MAX_ATTEMPTS", 3)),
            backoff_ms=max(0, _cfg_int("SMH_MKDIR_RETRY_BACKOFF_MS", 400)),
            action_label=f"mkdir path={path}",
        )
    except Exception as exc:
        logger.warning("SMH 创建目录最终失败: %s (%s)", path, exc)
        return False


async def ensure_directory(space_id: str, dir_path: str, access_token: str) -> bool:
    if not dir_path:
        return True
    parts = [p for p in dir_path.split("/") if p]
    current = ""
    for part in parts:
        current = f"{current}/{part}" if current else part
        ok = await create_directory(space_id, current, access_token)
        if not ok:
            return False
    return True


def _compute_sha256(data: bytes, *, first_64k: bool = False) -> str:
    if first_64k:
        return hashlib.sha256(data[:65536]).hexdigest()
    return hashlib.sha256(data).hexdigest()


async def upload_single_file(
    space_id: str,
    remote_path: str,
    file_bytes: bytes,
    filename: str,
    access_token: str,
    request_id: str = "",
    conflict_strategy: str = "overwrite",
) -> Dict[str, Any]:
    async with _upload_semaphore_instance():
        await _throttle_upload()
        file_size = len(file_bytes)
        dir_path = "/".join(remote_path.split("/")[:-1])
        if dir_path:
            ok = await ensure_directory(space_id, dir_path, access_token)
            if not ok:
                return {
                    "ok": False,
                    "filename": filename,
                    "error": f"创建目录失败: {dir_path}",
                    "phase": "mkdir",
                    "error_code": "smh_mkdir_failed",
                    "retryable": True,
                }

        auth_data = await _get_upload_auth(space_id, remote_path, file_size, file_bytes, access_token, conflict_strategy)
        if auth_data.get("isInstantUpload"):
            return {"ok": True, "filename": filename, "instant": True}

        domain = auth_data.get("domain")
        cos_path = auth_data.get("path")
        headers = auth_data.get("headers", {})
        if not (domain and cos_path):
            return {
                "ok": False,
                "filename": filename,
                "error": "上传授权缺少 domain/path",
                "phase": "auth",
                "error_code": "smh_auth_payload_invalid",
                "retryable": False,
            }

        upload_url = f"https://{domain}{cos_path}"
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                r = await client.put(upload_url, content=file_bytes, headers=headers)
                if r.status_code not in (200, 201, 204):
                    return {
                        "ok": False,
                        "filename": filename,
                        "error": f"COS PUT 失败: HTTP {r.status_code}",
                        "phase": "upload",
                        "error_code": "smh_upload_http_failed",
                        "retryable": _is_retryable_status(r.status_code),
                        "status_code": r.status_code,
                    }
        except httpx.TimeoutException as exc:
            raise SmhApiError(
                "COS PUT 超时",
                phase="upload",
                error_code="smh_upload_timeout",
                retryable=True,
            ) from exc

        confirm_key = auth_data.get("confirmKey", "")
        if confirm_key:
            confirm_result = await _confirm_upload(space_id, confirm_key, access_token, conflict_strategy)
            if not confirm_result:
                return {
                    "ok": False,
                    "filename": filename,
                    "error": "confirm 失败",
                    "phase": "confirm",
                    "error_code": "smh_confirm_failed",
                    "retryable": True,
                }

        return {"ok": True, "filename": filename, "instant": False}


async def _get_upload_auth_once(
    space_id: str,
    remote_path: str,
    file_size: int,
    file_bytes: bytes,
    access_token: str,
    conflict_strategy: str,
) -> Dict[str, Any]:
    library_id = _cfg("SMH_LIBRARY_ID", "")
    url = f"{_cfg_url('SMH_API_BASE_APP', DEFAULT_SMH_API_BASE_APP)}/file/{library_id}/{space_id}/{quote(remote_path)}"
    params = {
        "access_token": access_token,
        "conflict_resolution_strategy": conflict_strategy,
        "filesize": str(file_size),
        "domainTag": "0",
    }
    full_url = f"{url}?{urlencode(params)}"

    try:
        async with httpx.AsyncClient(timeout=_cfg_float("SMH_UPLOAD_AUTH_TIMEOUT_SECONDS", 30.0)) as client:
            r = await client.put(full_url, json={})
            if r.status_code in (200, 201):
                return _tag_instant(r.json())
            if r.status_code != 202:
                _raise_http_error(
                    phase="auth",
                    action="SMH 上传授权失败(阶段1)",
                    status_code=r.status_code,
                    text=r.text,
                    error_code="smh_auth_stage1_failed",
                )

            beginning_hash = _compute_sha256(file_bytes, first_64k=True)
            r = await client.put(full_url, json={"beginningHash": beginning_hash, "size": str(file_size)})
            if r.status_code in (200, 201):
                return _tag_instant(r.json())
            if r.status_code != 202:
                _raise_http_error(
                    phase="auth",
                    action="SMH 上传授权失败(阶段2)",
                    status_code=r.status_code,
                    text=r.text,
                    error_code="smh_auth_stage2_failed",
                )

            full_hash = _compute_sha256(file_bytes, first_64k=False)
            r = await client.put(full_url, json={"fullHash": full_hash, "beginningHash": beginning_hash, "size": str(file_size)})
            if r.status_code in (200, 201):
                return _tag_instant(r.json())
            _raise_http_error(
                phase="auth",
                action="SMH 上传授权失败(阶段3)",
                status_code=r.status_code,
                text=r.text,
                error_code="smh_auth_stage3_failed",
            )
    except httpx.TimeoutException as exc:
        raise SmhApiError(
            "SMH 上传授权超时",
            phase="auth",
            error_code="smh_auth_timeout",
            retryable=True,
        ) from exc


async def _get_upload_auth(
    space_id: str,
    remote_path: str,
    file_size: int,
    file_bytes: bytes,
    access_token: str,
    conflict_strategy: str,
) -> Dict[str, Any]:
    return await _run_with_async_retry(
        lambda: _get_upload_auth_once(
            space_id,
            remote_path,
            file_size,
            file_bytes,
            access_token,
            conflict_strategy,
        ),
        max_attempts=max(1, _cfg_int("SMH_UPLOAD_AUTH_MAX_ATTEMPTS", 2)),
        backoff_ms=max(0, _cfg_int("SMH_UPLOAD_AUTH_RETRY_BACKOFF_MS", 500)),
        action_label=f"upload_auth path={remote_path}",
    )


def _tag_instant(result: dict) -> dict:
    if "domain" not in result:
        result["isInstantUpload"] = True
    return result


async def _confirm_upload(space_id: str, confirm_key: str, access_token: str, conflict_strategy: str) -> bool:
    library_id = _cfg("SMH_LIBRARY_ID", "")
    url = f"{_cfg_url('SMH_API_BASE_APP', DEFAULT_SMH_API_BASE_APP)}/file/{library_id}/{space_id}/{quote(confirm_key)}"
    params = {"access_token": access_token, "confirm": "", "conflict_resolution_strategy": conflict_strategy}
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{url}?{urlencode(params)}")
            if r.status_code in (200, 201):
                return True
            logger.error("SMH confirm 失败: %s %s", r.status_code, r.text[:200])
            return False
    except httpx.TimeoutException:
        logger.error("SMH confirm 超时: %s", confirm_key)
        return False
