#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# name: oppo积分
# cron: 20 9 * * *
# desc: OPPO 商城 微信小程序 签到 + 浏览任务脚本 (YYB-Go 动态适配版)


# ==========================================================
# 功能说明：code 换 token（含缓存与自动刷新）
# 机制：本地 code 服务获取微信 code → 换取 token → 缓存到本地 JSON；
#       下次运行先读取缓存 token，并调用用户信息接口验证是否仍有效；
#       有效则直接复用（无需再获取 code）；失效 or 过期则重新获取 code 自动刷新。
# ==========================================================

"""
OPPO 商城 微信小程序 签到 + 浏览任务脚本 (YYB-Go 动态适配版)
版本: 2.1.1
"""

import hashlib
import json
import os
import random
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

import requests

# =============================================================================
# 常量配置（支持 YYB-Go 动态环境变量 YYB_GO 或 CODE_SERVER）
# =============================================================================

# ---- 自动读取 YYB-Go 多账号配置（支持 地址@OpenID 或 地址@序号） ----
SERVERS = []
env_servers = os.getenv("YYB_GO") or os.getenv("CODE_SERVER")
for line in env_servers.splitlines():
    line = line.strip()
    if line:
        SERVERS.append(line)

# ---- 小程序 app_id ----
WX_APP_ID = "wx9c825da1a7ba062e"

# ---- OPPO 登录相关接口 ----
PRE_AUTH_URL = "https://id.opposhop.cn/api/bind-login/pre-auth"
MEMBER_INFO_URL = "https://msec.opposhop.cn/users/web/member/info"

# ---- OPPO 业务接口基地址 ----
HD_BASE = "https://hd.opposhop.cn"
API_CREDIT = f"{HD_BASE}/api/cn/oapi/marketing/member/queryMemberCreditInfo"
API_SIGN_DETAIL = f"{HD_BASE}/api/cn/oapi/marketing/cumulativeSignIn/getSignInDetail"
API_SIGN_IN = f"{HD_BASE}/api/cn/oapi/marketing/cumulativeSignIn/signIn"
API_TASK_LIST = f"{HD_BASE}/api/cn/oapi/marketing/task/queryTaskList"
API_TASK_REPORT = f"{HD_BASE}/api/cn/oapi/marketing/taskReport/signInOrShareTask"
API_TASK_RECEIVE = f"{HD_BASE}/api/cn/oapi/marketing/task/receiveAward"

# ---- 落地页 ----
LANDING_PAGE = "https://hd.opposhop.cn/bp/b371ce270f7509f0"
LANDING_PARAMS = "?nightModelEnable=true&us=wode&um=qiandaobanner&colorScheme=light"

# ---- 默认活动 ID（解析失败时回退）----
FALLBACK_SIGN_ACTIVITY_ID = "2083099953777090560"
FALLBACK_TASK_ACTIVITY_ID = "1919591795180969984"
CREDITS_ADD_ACTION_ID = "1788913e6d9e4683b8b9ab0088733560"

# ---- 签名相关常量 ----
APP_KEY = "H7N4jMYgvNopNk7csDDhnM"
SIGN_KEY = "uyIVtwnGi3Qyf8dtGJ1d6g=="

# ---- 小程序通用 UA ----
UA = (
    "Mozilla/5.0 (Linux; Android 16; 2308CPXD0C Build/BP2A.250605.031.A3; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.178 "
    "Mobile Safari/537.36 XWEB/1460249 MMWEBSDK/20260502 MMWEBID/6435 "
    "MicroMessenger/8.0.76.3141(0x28004C3C) WeChat/arm64 Weixin NetType/WIFI "
    "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
)

# ---- 缓存文件路径 ----
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oppocookie.json")

# ---- 脚本行为开关 ----
SIMULATE_WAIT = True            # 是否真实等待浏览秒数
BROWSE_TIMEOUT = 20           # 请求超时(秒)

# ---- 品赞代理 ----
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

# ---- PushPlus 推送 ----
PLUSPLUS_TOKEN = os.getenv("PLUSPLUS_TOKEN", "")

# ---- 任务状态枚举 ----
TASK_STATUS_TODO = 1
TASK_STATUS_CLAIMABLE = 2
TASK_STATUS_DONE = 3
TASK_TYPE_BROWSE = 1

# =============================================================================
# 工具函数
# =============================================================================
_logs = []

def log(msg: str) -> None:
    line = str(msg)
    print(line, flush=True)
    _logs.append(line)
    if len(_logs) > 500:
        del _logs[: len(_logs) - 500]


def log_blank() -> None:
    log("")


def log_section(title: str) -> None:
    log(f"—— {title} ——")


def mask(s: str, keep: int = 6) -> str:
    s = str(s or "")
    if len(s) <= keep * 2:
        return "***"
    return s[:keep] + "***" + s[-keep:]


def points_text(amount) -> str:
    if amount is None or amount == "":
        return "未知"
    return str(amount)


def generate_sign(params: Dict[str, Any]) -> Tuple[str, int, str]:
    """生成 MD5 签名，返回 (sign, timestamp, nonce)"""
    data = params.copy()
    data["appKey"] = APP_KEY
    data["timestamp"] = int(time.time() * 1000)
    data["nonce"] = str(uuid.uuid4())
    data = {k: v for k, v in data.items() if v is not None and v != ""}
    sorted_keys = sorted(data.keys())
    raw = "&".join(f"{k}={data[k]}" for k in sorted_keys)
    sign_str = raw + f"&key={SIGN_KEY}"
    sign = hashlib.md5(sign_str.encode()).hexdigest()
    return sign, data["timestamp"], data["nonce"]


# =============================================================================
# 品赞代理工具
# =============================================================================
def json_preview(data: Any, limit: int = 800) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]


def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


def parse_proxy_response(text: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)

    text = text.strip()
    if not text:
        return None

    try:
        data = json.loads(text)
        proxy_obj = None

        if isinstance(data.get("data"), list) and data["data"]:
            proxy_obj = data["data"][0]
        elif isinstance(data.get("data"), dict):
            proxy_obj = data["data"]
        elif data.get("ip") and data.get("port"):
            proxy_obj = data
        elif isinstance(data.get("result"), dict):
            proxy_obj = data["result"]

        if proxy_obj:
            host = proxy_obj.get("ip") or proxy_obj.get("host")
            port = proxy_obj.get("port")
            if host and port:
                return {
                    "host": str(host),
                    "port": int(port),
                    "username": proxy_obj.get("user") or proxy_obj.get("username") or "",
                    "password": proxy_obj.get("pass") or proxy_obj.get("password") or "",
                }
    except Exception:
        pass

    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return {
                "host": parts[0],
                "port": int(parts[1]),
                "username": parts[2] if len(parts) > 2 else "",
                "password": parts[3] if len(parts) > 3 else "",
            }

    return None


def build_proxy_dict(proxy_info: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if not proxy_info:
        return None

    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")

    auth = ""
    if username and password:
        auth = f"{quote(username)}:{quote(password)}@"

    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"

    log(f"🛠️ [代理] 生成 {scheme.upper()} 代理 {host}:{port}")

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def validate_proxy(proxies: Optional[Dict[str, str]]) -> Tuple[bool, str]:
    if not proxies:
        return False, ""

    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            try:
                ip = response.json().get("origin", "未知")
            except Exception:
                ip = "未知"
            log(f"✅ [代理] 验证通过，出口 IP: {ip}")
            return True, ip
    except Exception as exc:
        log(f"⚠️ [代理] 验证失败: {exc}")

    return False, ""


def get_valid_proxy(account_name: str) -> Tuple[Optional[Dict[str, str]], str]:
    if not PROXY_API:
        log(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""

    log(f"🌐 [代理] {account_name} 正在获取品赞代理...")

    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)

            if not proxy_info:
                log(f"⚠️ [代理] 第 {index} 次代理解析失败")
                continue

            log(f"✅ [代理] 提取到 {proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)

            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip

            log(f"⚠️ [代理] 第 {index} 次代理不可用")
        except Exception as exc:
            log(f"⚠️ [代理] 第 {index} 次获取代理异常: {exc}")

        if index < PROXY_RETRY_TIMES:
            time.sleep(2)

    log("⚠️ [代理] 获取失败，使用直连")
    return None, ""


def request_with_proxy(
    method: str,
    url: str,
    *,
    proxies: Optional[Dict[str, str]] = None,
    server: str = "",
    **kwargs,
) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            log(f"⚠️ [代理] {server} 代理请求失败: {exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            log("🔁 [兜底] 切换直连重试")

    session = direct_session()
    return session.request(method, url, **kwargs)


# =============================================================================
# 本地 code 服务（完美适配 YYB-Go 动态寻址）
# =============================================================================
def get_code(server_entry: str) -> Optional[str]:
    """从 YYB-Go 或本地服务获取微信 code"""
    server_entry = str(server_entry or "").strip()
    if not server_entry:
        return None

    # 兼容处理：支持 地址@OpenID 或 地址@序号 格式
    if "@" in server_entry:
        parsed_server, ref = server_entry.split("@", 1)
        parsed_server = parsed_server.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
        ref = ref.strip()
        url = f"http://{parsed_server}/wxapp/getCode"
        log(f"🔐 [授权] 请求 YYB-Go 动态获取 code (标识: {mask(ref)}): {url}")
        try:
            response = direct_session().post(url, json={"ref": ref, "app_id": WX_APP_ID}, timeout=20)
            data = response.json()
            code = None
            if isinstance(data.get("data"), dict):
                code = (data["data"].get("result") or {}).get("code") or data["data"].get("code")
            elif data.get("code") == 0 and data.get("data"):
                code = data.get("data")

            if not code:
                log(f"❌ [授权] code 获取失败: {json_preview(data)}")
                return None
            log("✅ [授权] code 获取成功")
            return code
        except Exception as e:
            log(f"❌ [授权] code 获取异常: {e}")
            return None
    else:
        # 兼容旧的单一 /login 接口
        clean_server = server_entry.removeprefix("http://").removeprefix("https://").rstrip("/")
        url = f"http://{clean_server}/login"
        log(f"🔐 [授权] 请求本地 code 服务: {url}")
        try:
            response = direct_session().get(url, params={"appId": WX_APP_ID}, timeout=20)
            data = response.json()
            if data.get("err") != 0 or not data.get("code"):
                log(f"❌ [授权] code 获取失败: {json_preview(data)}")
                return None
            log("✅ [授权] code 获取成功")
            return data["code"]
        except Exception as e:
            log(f"❌ [授权] code 获取异常: {e}")
            return None


# =============================================================================
# OPPO 登录
# =============================================================================
def login(code: str, proxies: Optional[Dict[str, str]] = None,
          server: str = "") -> Tuple[Optional[str], Optional[str], str]:
    """用微信 code 完成小程序登录，返回 (sessionId, aesSessionId, openid)"""
    sign, ts, nonce = generate_sign({"loginType": "wechat", "code": code})
    headers = {
        "Content-Type": "application/json",
        "User-Agent": UA,
        "Referer": "https://servicewechat.com/wx9c825da1a7ba062e/592/page-frame.html",
        "source_type": "503",
        "s_channel": "program_wx",
        "s_version": "80457",
    }
    try:
        resp = request_with_proxy(
            "POST",
            PRE_AUTH_URL,
            json={
                "loginType": "wechat",
                "code": code,
                "appKey": APP_KEY,
                "timestamp": ts,
                "nonce": nonce,
                "sign": sign,
            },
            headers=headers,
            proxies=proxies,
            server=server,
        )
        data = resp.json()
        if not data.get("success"):
            log(f"❌ pre-auth 失败: {data}")
            return None, None, ""

        inner = data["data"]
        openid_ret = inner["openId"]
        encrypted_session = inner.get("encryptedSession")
        if not encrypted_session:
            log("❌ pre-auth 返回无 encryptedSession")
            return None, None, ""

        log(f"✅ pre-auth 成功, openid: {openid_ret}")
    except Exception as e:
        log(f"❌ pre-auth 异常: {e}")
        return None, None, ""

    headers2 = {
        "NEWOPPOSID": encrypted_session,
        "openid": openid_ret,
        "source_type": "503",
        "s_channel": "program_wx",
        "s_version": "80457",
        "User-Agent": UA,
        "Referer": "https://servicewechat.com/wx9c825da1a7ba062e/592/page-frame.html",
    }
    try:
        resp2 = request_with_proxy(
            "GET",
            MEMBER_INFO_URL,
            headers=headers2,
            proxies=proxies,
            server=server,
        )
        info = resp2.json()
        if info.get("code") != 200:
            log(f"❌ 换取 session 失败: {info}")
            return None, None, openid_ret

        inner2 = info["data"]
        session_id = inner2.get("sessionId")
        aes_key = inner2.get("aesSessionId")
        if not session_id or not aes_key:
            log("❌ member/info 返回缺少 sessionId/aesSessionId")
            return None, None, openid_ret

        log("✅ 获取最终凭证成功")
        return session_id, aes_key, openid_ret
    except Exception as e:
        log(f"❌ member/info 请求异常: {e}")
        return None, None, openid_ret


# =============================================================================
# 缓存管理
# =============================================================================
def load_cache() -> Dict[str, Any]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"accounts": {}, "activity_ids": {}}


def save_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log(f"⚠️ 缓存保存失败: {e}")


def get_cached_activity_ids(cache: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    info = cache.get("activity_ids", {})
    if info.get("month") == time.strftime("%Y-%m"):
        return info.get("sign_activity_id"), info.get("task_activity_id")
    return None, None


def update_activity_ids_cache(cache: Dict[str, Any], sign_id: str, task_id: str) -> None:
    cache["activity_ids"] = {
        "month": time.strftime("%Y-%m"),
        "sign_activity_id": sign_id,
        "task_activity_id": task_id,
    }


# =============================================================================
# 活动 ID 自动提取
# =============================================================================
def extract_activity_ids() -> Tuple[Optional[str], Optional[str]]:
    url = LANDING_PAGE + LANDING_PARAMS
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://servicewechat.com/wx9c825da1a7ba062e/592/page-frame.html",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        html = resp.text
        match = re.search(r"window\.__DSL__\s*=\s*(\{.*?\});", html, re.DOTALL)
        if not match:
            log("⚠️ 未找到 window.__DSL__，使用默认活动 ID")
            return FALLBACK_SIGN_ACTIVITY_ID, FALLBACK_TASK_ACTIVITY_ID
        dsl = json.loads(match.group(1))
        sign_id = None
        task_id = None
        by_id = dsl.get("byId", {})
        for comp_id, comp in by_id.items():
            if comp.get("type") == "SignIn":
                sign_id = comp.get("attr", {}).get("activityInfo", {}).get("activityId")
            elif comp.get("type") == "Task":
                task_id = comp.get("attr", {}).get("taskActivityInfo", {}).get("activityId")
        if not sign_id:
            sign_id = FALLBACK_SIGN_ACTIVITY_ID
        if not task_id:
            task_id = FALLBACK_TASK_ACTIVITY_ID
        log(f"📌 签到活动 ID: {sign_id}")
        log(f"📌 任务活动 ID: {task_id}")
        return sign_id, task_id
    except Exception as e:
        log(f"❌ 提取活动 ID 异常: {e}")
        return FALLBACK_SIGN_ACTIVITY_ID, FALLBACK_TASK_ACTIVITY_ID


# =============================================================================
# 业务客户端
# =============================================================================
class OppoMiniClient:
    def __init__(self, session_id: str, aes_key: str, openid: str,
                 sign_activity_id: str, task_activity_id: str,
                 credits_action_id: str = CREDITS_ADD_ACTION_ID,
                 proxies: Optional[Dict[str, str]] = None,
                 server: str = "") -> None:
        self.session_id = session_id
        self.aes_key = aes_key
        self.openid = openid
        self.sign_activity_id = sign_activity_id
        self.task_activity_id = task_activity_id
        self.credits_action_id = credits_action_id
        self.proxies = proxies
        self.server = server
        self.session = requests.Session()
        self.session.headers.update(self._build_headers())

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Cookie": f"NEWOPPOSID={self.session_id}; newopkey={self.aes_key}",
            "openid": self.openid,
            "source_type": "503",
            "s_channel": "program_wx",
            "s_version": "080457",
            "User-Agent": UA,
            "Referer": "https://hd.opposhop.cn/bp/b371ce270f7509f0?us=wode&um=qiandaobanner",
            "Accept": "application/json, text/plain, */*",
        }

    def _get(self, url: str, params: dict = None) -> dict:
        try:
            resp = request_with_proxy(
                "GET",
                url,
                params=params,
                headers=dict(self.session.headers),
                proxies=self.proxies,
                server=self.server,
                timeout=BROWSE_TIMEOUT,
            )
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    def _post(self, url: str, json_body: dict = None) -> dict:
        headers = dict(self.session.headers)
        headers["Content-Type"] = "application/json"
        try:
            resp = request_with_proxy(
                "POST",
                url,
                json=json_body,
                headers=headers,
                proxies=self.proxies,
                server=self.server,
                timeout=BROWSE_TIMEOUT,
            )
            return resp.json()
        except Exception as e:
            return {"_error": str(e)}

    def is_login_valid(self) -> bool:
        data = self._get(API_CREDIT)
        return data.get("code") == 200

    def credit_info(self) -> dict:
        data = self._get(API_CREDIT)
        if data.get("code") == 200:
            return data.get("data", {})
        return {}

    def sign_detail(self) -> dict:
        data = self._get(API_SIGN_DETAIL, params={"activityId": self.sign_activity_id})
        if data.get("code") == 200:
            return data.get("data", {})
        return {}

    def do_sign(self) -> Tuple[bool, str, Optional[int]]:
        detail = self.sign_detail()
        if detail.get("todaySignIn") is True:
            days = detail.get("signInDayNum")
            return True, f"今日已签到，本周累计【{days}】天", 0

        body = {
            "activityId": str(self.sign_activity_id),
            "creditsAddActionId": str(self.credits_action_id),
            "business": 1,
        }
        data = self._post(API_SIGN_IN, json_body=body)
        if data.get("code") != 200:
            msg = str(data.get("message") or data.get("errorMessage") or data)
            if any(k in msg for k in ("已签", "重复", "已经签到", "今日已")):
                return True, f"今日已签到（{msg}）", 0
            return False, f"签到失败: {msg}", None

        info = data.get("data") or {}
        award = None
        if isinstance(info, dict):
            if info.get("receiveStatus") is False:
                fail = info.get("receiveFailMsg") or "领取失败"
                if any(k in str(fail) for k in ("已签", "重复", "今日")):
                    return True, f"今日已签到（{fail}）", 0
                return False, f"签到领取失败: {fail}", None
            try:
                award = int(info.get("awardValue") or 0)
            except:
                award = None

        detail2 = self.sign_detail()
        days = detail2.get("signInDayNum") or detail.get("signInDayNum") or "?"
        if award is not None:
            return True, f"签到成功，获得【{award}】积分，累计【{days}】天", award
        return True, f"签到成功，累计【{days}】天", 0

    def task_list(self) -> List[dict]:
        data = self._get(API_TASK_LIST, params={"activityId": self.task_activity_id, "source": "c"})
        if data.get("code") == 200:
            return list((data.get("data") or {}).get("taskDTOList") or [])
        return []

    def report_browse(self, task: dict) -> bool:
        params = {
            "taskId": str(task.get("taskId") or ""),
            "activityId": str(task.get("activityId") or self.task_activity_id),
            "taskType": str(task.get("taskType") or TASK_TYPE_BROWSE),
        }
        data = self._get(API_TASK_REPORT, params=params)
        return data.get("code") == 200

    def receive_award(self, task: dict) -> Tuple[bool, int, str]:
        params = {
            "taskId": str(task.get("taskId") or ""),
            "activityId": str(task.get("activityId") or self.task_activity_id),
            "creditsAddActionId": str(self.credits_action_id),
            "business": "1",
        }
        data = self._get(API_TASK_RECEIVE, params=params)
        if data.get("code") != 200:
            msg = str(data.get("message") or data.get("errorMessage") or data)
            return False, 0, msg
        info = data.get("data") or {}
        if isinstance(info, dict) and info.get("receiveStatus") is False:
            return False, 0, str(info.get("receiveFailMsg") or "领奖失败")
        points = 0
        if isinstance(info, dict):
            try:
                points = int(info.get("awardValue") or 0)
            except:
                points = 0
        return True, points, f"+{points}" if points else "ok"

    @staticmethod
    def browse_seconds(task: dict) -> int:
        cfg = task.get("attachConfigOne") or {}
        try:
            sec = int(cfg.get("browseTime") or 5)
        except:
            sec = 5
        return max(1, min(sec, 30))

    @staticmethod
    def task_award_points(task: dict) -> str:
        cfg = task.get("awardAttachConfig") or {}
        pts = cfg.get("pointsNum")
        return str(pts) if pts is not None else "?"

    def do_browse_tasks(self, simulate_wait: bool = True) -> dict:
        stats = {"done": 0, "awarded": 0, "skipped": 0, "failed": 0, "points": 0,
                 "details": [], "manual_hint": False}
        tasks = self.task_list()
        if not tasks:
            log("⚠️ 任务列表为空")
            return stats

        browse = [t for t in tasks if int(t.get("taskType") or -1) == TASK_TYPE_BROWSE]
        other = [t for t in tasks if int(t.get("taskType") or -1) != TASK_TYPE_BROWSE]
        log(f"📋 共【{len(tasks)}】个任务，可自动浏览【{len(browse)}】个")

        for t in other:
            name = str(t.get("taskName") or t.get("taskId") or "未知任务")
            log(f"⏭️ [{name}] 已跳过（非浏览任务）")
            stats["skipped"] += 1
            stats["manual_hint"] = True

        for task in browse:
            name = str(task.get("taskName") or task.get("taskId"))
            status = int(task.get("taskStatus") or 0)
            task_id = str(task.get("taskId") or "")
            award_show = self.task_award_points(task)

            if not task_id:
                stats["skipped"] += 1
                continue

            if status == TASK_STATUS_DONE:
                log(f"✅ [{name}] 已完成")
                stats["skipped"] += 1
                continue

            if status == TASK_STATUS_TODO:
                wait_s = self.browse_seconds(task)
                log(f"🔎 发现任务: {name}（待完成，奖励约【{award_show}】）")
                if simulate_wait:
                    log(f"👀 [{name}] 浏览等待 {wait_s} 秒")
                    time.sleep(wait_s + 0.5)
                if not self.report_browse(task):
                    stats["failed"] += 1
                    log(f"❌ [{name}] 提交失败")
                    continue
                stats["done"] += 1
                log(f"📤 [{name}] 提交成功")
                time.sleep(0.8)
                status = TASK_STATUS_CLAIMABLE
            else:
                log(f"🔎 发现任务: {name}（待领奖，奖励约【{award_show}】）")

            ok, pts, msg = self.receive_award(task)
            if ok:
                stats["awarded"] += 1
                stats["points"] += pts
                if pts:
                    stats["details"].append(f"{name} +{pts}")
                    log(f"🎁 [{name}] 奖励领取成功（+{pts}）")
                else:
                    stats["details"].append(f"{name} 领奖成功")
                    log(f"🎁 [{name}] 奖励领取成功")
            else:
                if any(k in msg for k in ("已领", "已完成", "重复", "已经")):
                    stats["skipped"] += 1
                    log(f"⏭️ [{name}] 已跳过（{msg}）")
                else:
                    stats["failed"] += 1
                    stats["details"].append(f"领奖失败: {name} > {msg}")
                    log(f"❌ 奖励领取失败: {name} > {msg}")
            time.sleep(0.6)

        return stats


# =============================================================================
# 通知推送
# =============================================================================
def send_pushplus(title: str, content: str) -> None:
    if not PLUSPLUS_TOKEN:
        log("⚠️ [PushPlus] 未配置 PLUSPLUS_TOKEN，跳过推送")
        return

    try:
        requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": PLUSPLUS_TOKEN,
                "title": title,
                "content": content,
                "template": "txt",
            },
            timeout=10,
        )
        log("✅ [PushPlus] 推送成功")
    except Exception as exc:
        log(f"❌ [PushPlus] 推送失败: {exc}")


# =============================================================================
# 主流程
# =============================================================================
def run_account(server: str, sign_act_id: str, task_act_id: str,
                cache: dict, index: int) -> dict:
    nickname = f"账号{index}"
    log_blank()
    log(f"👤 账号【{index}】> 【{nickname}】来源 {server}")

    result = {"name": nickname, "success": False, "user": nickname, "server": server,
              "proxyStatus": "未使用代理", "proxyIp": "-",
              "points_before": None, "points_after": None,
              "sign_points": 0, "task_points": 0, "sign_already": False}

    proxies, proxy_ip = get_valid_proxy(server)
    result["proxyStatus"] = "使用品赞代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"

    time.sleep(PROXY_FETCH_INTERVAL)
    delay = random.randint(2, 6)
    log(f"⏳ [延迟] 启动延迟 {delay}s")
    time.sleep(delay)

    cached = cache.get("accounts", {}).get(server)
    session_id = aes_key = None
    openid = ""
    if cached:
        session_id = cached.get("sessionId")
        aes_key = cached.get("aesSessionId")
        openid = cached.get("openid") or ""
        if session_id and aes_key:
            client = OppoMiniClient(session_id, aes_key, openid, sign_act_id, task_act_id,
                                    proxies=proxies, server=server)
            if client.is_login_valid():
                log("✅ 缓存凭证有效")
            else:
                log("⚠️ 缓存凭证失效，重新登录")
                session_id = aes_key = None

    if not session_id or not aes_key:
        log_section("🔐 微信登录")
        code = get_code(server)
        if not code:
            log("❌ 获取 code 失败，跳过该账号")
            result["error"] = "获取 code 失败"
            return result
        session_id, aes_key, openid_ret = login(code, proxies=proxies, server=server)
        if not session_id or not aes_key:
            log("❌ 登录失败，跳过该账号")
            result["error"] = "登录失败"
            return result
        openid = openid_ret
        if "accounts" not in cache:
            cache["accounts"] = {}
        cache["accounts"][server] = {
            "sessionId": session_id,
            "aesSessionId": aes_key,
            "openid": openid,
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S")
        }
        save_cache(cache)

    client = OppoMiniClient(session_id, aes_key, openid, sign_act_id, task_act_id,
                            proxies=proxies, server=server)

    credit = client.credit_info()
    points_before = None
    if credit:
        user = credit.get("userNickName") or credit.get("userName") or nickname
        level = credit.get("userLevel")
        amount = credit.get("amount")
        points_before = amount
        result["points_before"] = amount
        result["user"] = user
        log(f"💎 当前积分: 【{points_text(amount)}】 等级【{level}】")
    else:
        log("⚠️ 积分查询失败，继续任务")

    log_section("📝 签到")
    ok, msg, award = client.do_sign()
    result["sign"] = msg
    sign_points = 0
    if ok:
        log(f"✅ {msg}")
        sign_points = award or 0
        result["sign_already"] = ("今日已签" in msg) and sign_points == 0
    else:
        log(f"❌ {msg}")
    result["sign_points"] = sign_points

    log_section("🎯 日常任务")
    stats = client.do_browse_tasks(simulate_wait=SIMULATE_WAIT)
    task_points = stats.get("points", 0)
    result["task_points"] = task_points
    result["tasks"] = (f"提交【{stats['done']}】领奖【{stats['awarded']}】"
                       f"跳过【{stats['skipped']}】失败【{stats['failed']}】")
    if stats.get("manual_hint"):
        log("ℹ️ 说明: 非浏览任务需人工完成")

    credit2 = client.credit_info()
    points_after = None
    if credit2:
        points_after = credit2.get("amount")
        result["points_after"] = points_after
        log(f"💎 执行后积分: 【{points_text(points_after)}】")
    elif points_before is not None:
        points_after = points_before

    delta = 0
    if points_before is not None and points_after is not None:
        try:
            delta = int(points_after) - int(points_before)
        except:
            delta = sign_points + task_points
    else:
        delta = sign_points + task_points
    result["points_delta"] = delta
    log(f"📈 积分变化: {points_text(points_before)} -> {points_text(points_after)} (+{delta})")

    result["success"] = ok or ("今日已签" in msg)
    return result


def should_retry_with_new_ids(result: dict) -> bool:
    msg = result.get("sign", "") + result.get("tasks", "")
    return any(k in msg for k in ("活动已经结束", "活动已过期", "活动不存在"))


def main():
    log("==============================")
    log("🛒 OPPO 商城 · 小程序签到+任务 (YYB-Go 动态多账号版)")
    log("==============================")

    cache = load_cache()
    if not SERVERS:
        log("❌ 没有配置服务地址，退出")
        return

    sign_act_id, task_act_id = get_cached_activity_ids(cache)
    if not sign_act_id or not task_act_id:
        log_section("🔍 提取活动 ID（无缓存）")
        sign_act_id, task_act_id = extract_activity_ids()
        update_activity_ids_cache(cache, sign_act_id, task_act_id)
        save_cache(cache)

    results = []
    for idx, server in enumerate(SERVERS, 1):
        try:
            res = run_account(server, sign_act_id, task_act_id, cache, idx)
            if not res["success"] and should_retry_with_new_ids(res):
                log("⚠️ 疑似活动ID过期，尝试重新提取并重试本账号")
                new_sign, new_task = extract_activity_ids()
                if new_sign != sign_act_id or new_task != task_act_id:
                    update_activity_ids_cache(cache, new_sign, new_task)
                    save_cache(cache)
                    log(f"🔄 使用新活动ID重试: 签到={new_sign}, 任务={new_task}")
                    res = run_account(server, new_sign, new_task, cache, idx)
                else:
                    log("ℹ️ 活动ID未变化，不重试")
            results.append(res)
        except Exception as e:
            log(f"❌ 账号异常: {e}")
            results.append({"name": f"账号{idx}", "server": server,
                            "success": False, "error": str(e)})
        if idx < len(SERVERS):
            time.sleep(1.5)

    ok_count = sum(1 for r in results if r.get("success"))
    fail_count = len(results) - ok_count
    log_blank()
    log("==============================")
    log(f"🏁 全部完成: 成功【{ok_count}】失败【{fail_count}】")
    log("==============================")

    lines = []
    for r in results:
        flag = "✅" if r.get("success") else "❌"
        nick = r.get("user") or r.get("name", "未知")
        lines.append(f"{flag} {nick}")
        lines.append(f"🌍 来源: {r.get('server', '-')}")
        lines.append(f"🌐 代理: {r.get('proxyStatus', '-')} {r.get('proxyIp', '')}".rstrip())
        if not r.get("success") and r.get("error"):
            lines.append(f"⚠️ {r['error']}")
            lines.append("")
            continue
        sign_pts = r.get("sign_points", 0)
        sign_already = r.get("sign_already", False)
        if sign_already and sign_pts == 0:
            lines.append("📝 签到: +0（今日已签）")
        else:
            lines.append(f"📝 签到: +{sign_pts}")
        lines.append(f"🎯 任务: +{r.get('task_points', 0)}")
        pts_after = r.get("points_after")
        if pts_after is not None:
            lines.append(f"💎 总积分: {points_text(pts_after)}")
        else:
            lines.append(f"💎 总积分: 未知")
        lines.append("")
    content = "\n".join(lines).strip()
    send_pushplus("📱 OPPO 小程序签到", content)


if __name__ == "__main__":
    main()
