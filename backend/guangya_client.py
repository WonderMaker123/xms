"""
光鸭云盘客户端 - xms
基于 guangyaclient 改写，支持扫码登录 + Token 管理
"""
import base64
import hashlib
import hmac
import time
import secrets
import re
import json
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field


def generate_traceparent() -> str:
    """生成 traceparent header"""
    trace_id = secrets.token_hex(16)
    parent_id = secrets.token_hex(8)
    return f"00-{trace_id}-{parent_id}-01"


def generate_did() -> str:
    """生成设备 ID"""
    return secrets.token_hex(16)


def calculate_gcid(file_path: str) -> str:
    """计算文件 GCID（用于秒传）"""
    import os
    md5_hash = hashlib.md5()
    with open(file_path, 'rb') as f:
        while chunk := f.read(8192):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


@dataclass
class GuangyaClient:
    """光鸭云盘客户端"""
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    device_id: str = field(default_factory=generate_did)
    token_expires_at: Optional[float] = None
    _headers: Dict = field(default_factory=dict)

    def __post_init__(self):
        self._build_headers()

    def _build_headers(self):
        self._headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": f"Bearer {self.access_token}" if self.access_token else "",
            "content-type": "application/json",
            "did": self.device_id,
            "dt": "4",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "traceparent": generate_traceparent(),
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        }

    def _account_headers(self) -> Dict:
        return {
            "accept": "*/*",
            "content-type": "application/json",
            "origin": "https://www.guangyapan.com",
            "referer": "https://www.guangyapan.com/",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
            "x-client-id": "aMe-8VSlkrbQXpUR",
            "x-client-version": "0.0.1",
            "x-device-id": self.device_id,
            "x-device-model": "chrome%2F147.0.0.0",
            "x-device-name": "PC-Chrome",
            "x-device-sign": f"wdi10.{self.device_id}{secrets.token_hex(16)}",
            "x-net-work-type": "NONE",
            "x-os-version": "MacIntel",
            "x-platform-version": "1",
            "x-protocol-version": "301",
            "x-provider-name": "NONE",
            "x-sdk-version": "9.0.2",
        }

    def _refresh_if_needed(self):
        """检查并刷新 token"""
        if self.refresh_token and self.token_expires_at and time.time() >= self.token_expires_at:
            self.refresh_token_call()

    def _request(self, url: str, method: str = "POST", **kwargs) -> Dict:
        import httpx
        self._refresh_if_needed()
        headers = kwargs.pop("headers", {})
        headers["traceparent"] = generate_traceparent()
        headers.update(self._headers)
        
        resp = httpx.request(method, url, headers=headers, **kwargs, timeout=30.0)
        if resp.status_code == 401 and self.refresh_token:
            self.refresh_token_call()
            headers["traceparent"] = generate_traceparent()
            resp = httpx.request(method, url, headers=headers, **kwargs, timeout=30.0)
        resp.raise_for_status()
        return resp.json()

    # ===== 登录流程 =====

    def login_sms_init(self, phone_number: str, captcha_token: Optional[str] = None) -> Dict:
        """短信登录 - 初始化"""
        import httpx
        body = {
            "client_id": "aMe-8VSlkrbQXpUR",
            "action": "POST:/v1/auth/verification",
            "device_id": self.device_id,
            "meta": {"phone_number": phone_number},
        }
        if captcha_token:
            body["captcha_token"] = captcha_token
        return httpx.post(
            "https://account.guangyapan.com/v1/shield/captcha/init",
            headers=self._account_headers(), json=body, timeout=15.0
        ).json()

    def login_sms_send(self, phone_number: str, captcha_token: str, target: str = "ANY") -> Dict:
        """短信登录 - 发送验证码"""
        import httpx
        headers = self._account_headers()
        headers["x-captcha-token"] = captcha_token
        return httpx.post(
            "https://account.guangyapan.com/v1/auth/verification",
            headers=headers,
            json={"phone_number": phone_number, "target": target, "client_id": "aMe-8VSlkrbQXpUR"},
            timeout=15.0
        ).json()

    def login_sms_verify(self, verification_id: str, verification_code: str) -> Dict:
        """短信登录 - 验证"""
        import httpx
        return httpx.post(
            "https://account.guangyapan.com/v1/auth/verification/verify",
            headers=self._account_headers(),
            json={"verification_id": verification_id, "verification_code": verification_code, "client_id": "aMe-8VSlkrbQXpUR"},
            timeout=15.0
        ).json()

    def login_sms_signin(self, verification_code: str, verification_token: str, username: str, captcha_token: str) -> Dict:
        """短信登录 - 完成"""
        import httpx
        headers = self._account_headers()
        headers["x-captcha-token"] = captcha_token
        result = httpx.post(
            "https://account.guangyapan.com/v1/auth/signin",
            headers=headers,
            json={
                "verification_code": verification_code,
                "verification_token": verification_token,
                "username": username,
                "client_id": "aMe-8VSlkrbQXpUR",
            },
            timeout=15.0
        ).json()
        self._apply_auth_result(result)
        return result

    def refresh_token_call(self) -> Dict:
        """刷新 Token"""
        import httpx
        if not self.refresh_token:
            return {}
        headers = self._account_headers()
        headers["x-action"] = "401"
        result = httpx.post(
            "https://account.guangyapan.com/v1/auth/token",
            headers=headers,
            json={
                "client_id": "aMe-8VSlkrbQXpUR",
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            },
            timeout=15.0
        ).json()
        self._apply_auth_result(result)
        return result

    def _apply_auth_result(self, result: Dict):
        """应用登录/刷新结果"""
        if result.get("access_token"):
            self.access_token = result["access_token"]
            self._headers["authorization"] = f"Bearer {result['access_token']}"
            expires_in = result.get("expires_in", 3600)
            self.token_expires_at = time.time() + expires_in
            if result.get("refresh_token"):
                self.refresh_token = result["refresh_token"]

    # ===== 用户信息 =====
    def user_info(self) -> Dict:
        """获取用户信息"""
        import httpx
        headers = self._account_headers()
        headers["authorization"] = f"Bearer {self.access_token}"
        return httpx.post(
            "https://account.guangyapan.com/v1/user/me",
            headers=headers, timeout=15.0
        ).json()

    # ===== 文件操作 =====
    def fs_files(self, parent_id: Optional[str] = None, page: int = 0, page_size: int = 50,
                 file_types: Optional[List] = None, res_type: int = 1) -> Dict:
        """获取文件列表"""
        data = {
            "parentId": "" if parent_id is None else parent_id,
            "page": page,
            "pageSize": page_size,
            "orderBy": 3,
            "sortType": 1,
        }
        if file_types:
            data["fileTypes"] = file_types
        data["resType"] = res_type
        return self._request("https://api.guangyapan.com/nd.bizuserres.s/v1/file/fs_files", json=data)

    def fs_video_list(self, parent_id: Optional[str] = None, page: int = 0, page_size: int = 50) -> Dict:
        """获取视频列表"""
        return self.fs_files(parent_id=parent_id, page=page, page_size=page_size,
                             file_types=[2], res_type=1)

    def download_url(self, file_id: str) -> Dict:
        """获取文件下载链接"""
        return self._request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/get_res_download_url",
            json={"fileId": file_id}
        )

    def fs_detail(self, file_id: str) -> Dict:
        """获取文件详情"""
        return self._request(
            "https://api.guangyapan.com/nd.bizuserres.s/v1/file/get_file_detail",
            json={"fileId": file_id}
        )

    # ===== 扫码登录 =====
    def qrcode_generate(self) -> Dict:
        """生成设备码（扫码登录第一步）"""
        import httpx
        return httpx.post(
            "https://account.guangyapan.com/v1/auth/device/code",
            headers=self._account_headers(),
            json={"client_id": "aMe-8VSlkrbQXpUR", "scope": "all"},
            timeout=15.0
        ).json()

    def qrcode_check(self, device_code: str) -> Dict:
        """检查扫码状态"""
        import httpx
        return httpx.post(
            "https://account.guangyapan.com/v1/auth/device/token",
            headers=self._account_headers(),
            json={"device_code": device_code, "client_id": "aMe-8VSlkrbQXpUR", "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
            timeout=15.0
        ).json()

    def get_stream_url(self, file_id: str) -> str:
        """获取流播放 URL（302 重定向）"""
        resp = self.download_url(file_id)
        url = resp.get("data", {}).get("url", "")
        return url
