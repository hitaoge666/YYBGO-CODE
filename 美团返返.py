#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# name: 美团返返
# cron: 10 9,15 * * *
# desc: 美团返返外卖返现动态 (YYB-Go 动态适配版)

# ==========================================================
# 功能说明：code 换 token（含缓存与自动刷新）
# 机制：本地 code 服务获取微信 code → 换取美团返返 token → 缓存到本地 JSON；
#       下次运行先读取缓存 token，并调用用户信息接口验证是否仍有效；
#       有效则直接复用（无需再获取 code）；失效或过期则重新获取 code 自动刷新。
# ==========================================================

import json
import os
import sys
import random
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# 增加顶部随机延时，降低并发风控特征 (对齐其他脚本架构)
sleep_time = random.randint(10, 600)
print(f"随机等待 {sleep_time} 秒，防黑号风控...")
time.sleep(sleep_time)

# ========== 企业微信推送配置（可选） ==========
QYWX_TOKEN = os.getenv("QYWX_TOKEN", "")

APP_NAME = "美团返返外卖返现小程序"
APPID = os.getenv("MTFF_APPID", "wxfdba1f3193621ebf")
MT_APP_NAME = os.getenv("MTFF_APP_NAME", "foodtaster")

# ---- 自动读取多账号配置（支持 YYB_GO、CODE_SERVER 逗号/换行符分割） ----
SERVERS = []
env_servers = os.getenv("YYB_GO") or os.getenv("CODE_SERVER") or os.getenv("MTFF_SERVERS")
if env_servers:
    for line in env_servers.replace(",", "\n").splitlines():
        line = line.strip()
        if line:
            SERVERS.append(line)
if not SERVERS:
    # 默认兜底
    SERVERS = ["127.0.0.1:8088"]

PLUSPLUS_TOKEN = os.getenv("PLUSPLUS_TOKEN", "")
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

OPEN_BASE_URL = "https://open.meituan.com"
WECHAT_LOGIN_URL = f"{OPEN_BASE_URL}/user/v1/weapplogin"
USER_INFO_URL = f"{OPEN_BASE_URL}/user/v1/info"

ACT_BASE_URL = "https://offsiteact.meituan.com"
ACT_QUERY = (
    "yodaReady=wx&csecappid=wxfdba1f3193621ebf&csecplatform=3"
    "&csecversionname=0.2.233&csecversion=1.4.0"
)

CHECK_IN_URL = f"{ACT_BASE_URL}/wmpact/mission/check/in?{ACT_QUERY}"
ACTIVITY_LIST_URL = f"{ACT_BASE_URL}/wmpact/xcx/activity/list?{ACT_QUERY}"
DYNAMIC_RULE_URL = f"{ACT_BASE_URL}/wmpact/dynamic/rule?{ACT_QUERY}"
WALLET_BALANCE_URL = f"{ACT_BASE_URL}/wmpact/wallet/balance?{ACT_QUERY}"

# 抓包固定参数
CAPTURED_UUID = "1a036668070c8-f6fa2cff930c-0-0-1a036668070c8"
CAPTURED_DEVICEID = "3ec6febf-fff0-4b9c-a962-b1b590e46a7c"
CAPTURED_WXP = "1a112a20b52140e0a93f77b593ab275d"
CAPTURED_PAGE_PVID = "b5ee1cea-6c99-4174-aec3-accff077e5a4"
CAPTURED_MISSION_SECRET_ID = "100000020241016"
CAPTURED_LAT = 22.78120994567871
CAPTURED_LON = 108.27330780029297
CAPTURED_APPLETS_FINGERPRINT = (
    "WX__ver1.2.0_CCCC_6OcnVcWU1tfBNaRIa/DMjk8Ats0smFN9DAE7ivnjYv2f50CD282EGOdtYGVg29NY7VQew1bqa9JrbXJZyMu0S6KIUA61B6ONoPWHNNyXERhqwVCBJBMIJxyesViplAUatV47lmxTYP/P2CFYTjOyBSjjAMP5/T1fCftMf/d3FI8WCTLlY6aB1ubunDsh0o92W+gpbOS0fanCxRobcHqXLmrGrxjZb/SPkM7KK+YeS8EL9f0vW82nOIuOnk4jRTJWdNctyrQK76G1TC9mP6i98sKTVdokpy3Vlr/JVvCqzukDwG+2aNpMqcEGBa2cEYsVEQHm4/hlhIcmpGEuGiwqZMqBGpI8qZb+s75RZtcvj9gugFZetYTo7NxlUIUoZcfeVog0mS05QmLb9XinwqjUqoCchNn8APlMAtzsFc3r4HxJ5RSJ0Y2/sbdHHqZ5CpPZ+Hd/cMU2jDltyWm/VQ96ms8J93Tk7SLpe1hU+hqZ2JN++hCc2K8s7UkCAELLgAAniCG1SWWt10UTUF9TLkshqvbJB0Nuw0AmEAUpxBq9mAqbqgqQAEKpIFAt1/rQR0ez4VBH1fj1EbzbuilCvASGUHJeTjRp4mCO/EjlLfdKbT2dMfBytx8Kb7cNz520pJcC2LSGTKvW6yvPto0NAfGznmeZcbB+XQLhqlignpkojgPglGIYzBWfm41ZCIAi3WpgvwOsNcubHGmX56YBZz7wirC/B6dlAGdPYWQm7pVeowROnGqo0ZamCfejpZJrcL4VuoQV8Lorrqc7RPVhAFV3VrZyy4UR8MSk3daMP5xDmnalRVv/AkARySKRSP0bTARZsi3Oy2tcmlh0PocMbNJQPPrkLn6hvfpzV1gje+Sq3cy15SIxBLK+Due1r2TMwiTIQacoEXLzeyL1nbuqLf6WHUD8/2vAlTLlZv7Nt1AQRbR0llXqUZS3A6FlHKwBH2D3zGLYTmnSLGjSgt4V0NVnLqwOBnN/V+zTnPd00y5KGRQXwg3qXSe7QNR5maoCeQw7izhNHCrokOt4Y1Jy4NiJPkYOenGwhvKgl/h2WTJd8tSU6/cG60YPfrl/jOTdU6s2T2jSKA+4gTxRKvPCpJVKbb7wjnJqCOL4EqdgA4CfquIsAYGb3ZYFwfzyUPitDDjeI6GA0WfePcmqmG69Q1aRHkaej8Gy1J3VO8pX/frOeMKFHgDkdnbRLuJW7b8gopolzJQouHKZTS0kYKXECizj7VmnSvDNC+Mta/cmWhDcPClv/DueaTyEggumMXifqMN9KijHDhMrvyISGPJwbz3naorlncl40pMKMnInlpoK1xGq4FGuMyUCuiiRn9HAQvewCFzUqJ58ZWgPsSXEBIgJdDPbuQLwpQylks37vV01CDuvOzfFXx2qIWYN/lbXVauZWSUQ+SUlTcTfNr6Ub4fbV8USxfCa9uesBDvtxLc8Xc3wUQ1mMo0fjJNr8TlUClCx1eyWla0edoHZQenXPazaZ+sKQYdfAwnviSmsiakBaIw5TWJprdIrD1Eezx/E6BoDRHdX9uuqr/7KdWBA12BIEPiFoHuuzNZD1yVZ7tF1LnH0Ycm75hOp6f9muZC0lDhjTFICAsPFUuN4OHqLLotCb6J2UPkysx7/hbXE7DoEBvsHU1i2WpWOG3+jxOAaWvoka/vbrJpGCPw5yfInRG4DIYhGj3feVJFg40pW7VsdZkun0eVCLjmS7mWIFnGdzUiBYhvKhW5a0DaP86Rmjy2l5+VDo+U5kF6OURpV3MV8rSErXMvSvW3KfRaKcoV0YhdRxQJgw3U5m9mbVg91Ukj8GHOdeh11tuUlg5ip6yc8QY6RNtDvZGiknuJ6SDMKFLMLi5/rfS7EM8KErFd424t02Lkw8gyMXmMDt4gE4RoAbXhTHJPT+2S57Pu0PctR1qi4azVB2nlNOdXpZPbRrxp0vVIVhJ/xlJDup19muHJYYXw="
)

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mtffcookie.json")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541c37) XWEB/25364"
)

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sleep(seconds: float) -> None:
    time.sleep(seconds)

def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"

def json_preview(data: Any, limit: int = 800) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]

def to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def safe_data(resp: Dict[str, Any]) -> Dict[str, Any]:
    return resp.get("data") or {}

def log_title() -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ ♻️ 美团返返外卖返现动态 code 版 (YYB-Go)         ║")
    print(f"║ 🕒 启动时间: {now_text():<32}║")
    print(f"║ 🔢 账号数量: {len(SERVERS):<34}║")
    print("╚" + "═" * 50 + "╝")

def log_account_header(index: int, total: int, server: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<37}│")
    print(f"│ 🌍 来源 {server:<40}│")
    print("└" + "─" * 50 + "┘")

def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session

def parse_proxy_response(text: Any) -> Dict[str, Any] | None:
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

def build_proxy_dict(proxy_info: Dict[str, Any] | None) -> Dict[str, str] | None:
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
    print(f"🛠️ [代理] 生成 {scheme.upper()} 代理 {host}:{port}")
    return {
        "http": proxy_url,
        "https": proxy_url,
    }

def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies:
        return False, ""
    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            try:
                ip = response.json().get("origin", "未知")
            except Exception:
                ip = "未知"
            print(f"✅ [代理] 验证通过，出口 IP: {ip}")
            return True, ip
    except Exception as exc:
        print(f"⚠️ [代理] 验证失败: {exc}")
    return False, ""

def get_valid_proxy(account_name: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        print(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""
    print(f"🌐 [代理] {account_name} 正在获取品赞代理...")
    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)
            if not proxy_info:
                print(f"⚠️ [代理] 第 {index} 次代理解析失败")
                continue
            print(f"✅ [代理] 提取到 {proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)
            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip
            print(f"⚠️ [代理] 第 {index} 次代理不可用")
        except Exception as exc:
            print(f"⚠️ [代理] 第 {index} 次获取代理异常: {exc}")
        if index < PROXY_RETRY_TIMES:
            sleep(2)
    print("⚠️ [代理] 获取失败，使用直连")
    return None, ""

def request_with_proxy(
    method: str,
    url: str,
    *,
    proxies: Dict[str, str] | None = None,
    server: str = "",
    **kwargs,
) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"⚠️ [代理] {server} 代理请求失败: {exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            print("🔁 [兜底] 切换直连重试")
    session = direct_session()
    return session.request(method, url, **kwargs)


def send_qywx(title, content):
    """企业微信机器人推送（Webhook）。未配置 QYWX_TOKEN 时自动跳过。"""
    if not QYWX_TOKEN:
        return False
    key = QYWX_TOKEN.split("key=")[-1].strip()
    import json as _qywx_json, urllib.request as _qywx_urllib
    try:
        text = "%s\n%s" % (title, content)
        if len(text.encode("utf-8")) > 2000:
            text = text.encode("utf-8")[:2000].decode("utf-8", "ignore")
        payload = _qywx_json.dumps({"msgtype": "text", "text": {"content": text}}).encode("utf-8")
        req = _qywx_urllib.Request("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=" + key,
                                   data=payload, headers={"Content-Type": "application/json"})
        res = _qywx_json.loads(_qywx_urllib.urlopen(req, timeout=10).read().decode("utf-8"))
        ok = res.get("errcode") == 0
        print("[企业微信] 推送%s errcode=%s errmsg=%s" % ("成功 ✓" if ok else "失败 ✗", res.get("errcode"), res.get("errmsg", "")))
        return ok
    except Exception as _exc:
        print("[企业微信] 推送异常:", _exc)
        return False

def send_pushplus(title: str, content: str) -> None:
    send_qywx(title, content)
    if not PLUSPLUS_TOKEN:
        print("⚠️ [PushPlus] 未配置 PLUSPLUS_TOKEN，跳过推送")
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
        print("✅ [PushPlus] 推送成功")
    except Exception as exc:
        print(f"❌ [PushPlus] 推送失败: {exc}")


def get_code(server_entry: str) -> str | None:
    """从 YYB-Go 或本地服务获取微信 code (适配动态多账号)"""
    server_entry = str(server_entry or "").strip()
    if not server_entry:
        return None

    # 兼容处理：如果包含了 @ 符号（来自 YYB-Go 动态同步的 OpenID 格式）
    if "@" in server_entry:
        parsed_server, ref = server_entry.split("@", 1)
        parsed_server = parsed_server.strip().removeprefix("http://").removeprefix("https://").rstrip("/")
        ref = ref.strip()
        url = f"http://{parsed_server}/wxapp/getCode"
        print(f"🔐 [授权] 请求 YYB-Go 动态获取 code (标识: {mask(ref)}): {url}")
        try:
            response = direct_session().post(url, json={"ref": ref, "app_id": APPID}, timeout=20)
            data = response.json()
            code = None
            if isinstance(data.get("data"), dict):
                code = (data["data"].get("result") or {}).get("code") or data["data"].get("code")
            elif data.get("code") == 0 and data.get("data"):
                code = data.get("data")
            
            if not code:
                print(f"❌ [授权] code 获取失败: {json_preview(data)}")
                return None
            print("✅ [授权] code 获取成功")
            return code
        except Exception as exc:
            print(f"❌ [授权] code 获取异常: {exc}")
            return None
    else:
        # 原版旧逻辑
        clean_server = server_entry.removeprefix("http://").removeprefix("https://").rstrip("/")
        url = f"http://{clean_server}/login"
        print(f"🔐 [授权] 请求本地 code 服务: {url}")
        try:
            response = direct_session().get(
                url,
                params={"appId": APPID},
                timeout=20,
            )
            data = response.json()
            if data.get("err") != 0 or not data.get("code"):
                print(f"❌ [授权] code 获取失败: {json_preview(data)}")
                return None
            print("✅ [授权] code 获取成功")
            return data["code"]
        except Exception as exc:
            print(f"❌ [授权] code 获取异常: {exc}")
            return None


def common_headers(token: str | None = None, acct: Dict[str, Any] | None = None) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "xweb_xhr": "1",
        "Referer": f"https://servicewechat.com/{APPID}/111/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if token:
        headers["token"] = token
        headers["mt-c-token"] = token
    if acct:
        if acct.get("openId"):
            headers["openId"] = acct["openId"]
        if acct.get("openIdCipher"):
            headers["openIdCipher"] = acct["openIdCipher"]
        if acct.get("userId"):
            headers["csecuserid"] = str(acct["userId"])
    headers["csecuuid"] = CAPTURED_UUID
    headers["deviceid"] = CAPTURED_DEVICEID
    return headers


def extract_token(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None

    candidates = [
        data.get("token"),
        data.get("accessToken"),
        data.get("access_token"),
        data.get("jwt"),
    ]

    inner = data.get("data")
    if isinstance(inner, dict):
        candidates.extend([
            inner.get("token"),
            inner.get("accessToken"),
            inner.get("access_token"),
            inner.get("jwt"),
        ])

    for item in candidates:
        if item and item != "null":
            return str(item)

    return None


def login_by_code(
    server: str, code: str, proxies: Dict[str, str] | None
) -> Tuple[str | None, Dict[str, Any] | None]:
    try:
        print("🔐 [登录] 使用 code 换 token")
        response = request_with_proxy(
            "POST",
            WECHAT_LOGIN_URL,
            headers={
                "User-Agent": USER_AGENT,
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "*/*",
            },
            data={
                "code": code,
                "appName": MT_APP_NAME,
            },
            proxies=proxies,
            server=server,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        token = extract_token(data)
        if token:
            inner = data.get("data") or {}
            acct = {
                "token": token,
                "openId": data.get("openId") or "",
                "openIdCipher": data.get("openIdCipher") or "",
                "userId": inner.get("userId") or "",
            }
            print(f"✅ [登录] token 获取成功: {mask(token)}")
            return token, acct

        print(f"❌ [登录] 未识别 token 字段: {json_preview(data)}")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None


def api_get(server: str, url: str, token: str, proxies: Dict[str, str] | None, acct: Dict[str, Any] | None = None) -> Dict[str, Any]:
    response = request_with_proxy(
        "GET",
        url,
        headers=common_headers(token, acct),
        proxies=proxies,
        server=server,
    )
    try:
        return response.json()
    except Exception:
        return {
            "code": -1,
            "msg": f"JSON解析失败: {response.text[:300]}",
        }


def api_post(server: str, url: str, token: str, proxies: Dict[str, str] | None, payload: Dict[str, Any], acct: Dict[str, Any] | None = None) -> Dict[str, Any]:
    response = request_with_proxy(
        "POST",
        url,
        headers=common_headers(token, acct),
        json=payload,
        proxies=proxies,
        server=server,
    )
    try:
        return response.json()
    except Exception:
        return {
            "code": -1,
            "msg": f"JSON解析失败: {response.text[:300]}",
        }


# ====================== Token缓存管理 ======================
def load_token_cache() -> Dict[str, Any]:
    try:
        if os.path.exists(COOKIE_FILE):
            with open(COOKIE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as exc:
        print(f"⚠️ [缓存] 读取失败: {exc}")
    return {}


def save_token_cache(cache: Dict[str, Any]) -> None:
    try:
        with open(COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        print("✅ [缓存] Token保存成功")
    except Exception as exc:
        print(f"❌ [缓存] 保存失败: {exc}")


def get_cached_account(server: str) -> Dict[str, Any] | None:
    cache = load_token_cache()
    data = cache.get(server)
    if data and data.get("token"):
        return data
    return None


def set_cached_account(server: str, acct: Dict[str, Any]) -> None:
    cache = load_token_cache()
    cache[server] = {
        "token": acct["token"],
        "openId": acct.get("openId", ""),
        "openIdCipher": acct.get("openIdCipher", ""),
        "userId": acct.get("userId", ""),
        "updateTime": datetime.now().isoformat(),
    }
    save_token_cache(cache)


def login_with_cache(server: str, proxies: Dict[str, str] | None) -> Tuple[str | None, Dict[str, Any] | None]:
    """优先使用缓存 token（用户信息接口验证），失效自动 code 刷新"""
    cached = get_cached_account(server)
    if cached:
        print("🔍 [缓存] 验证 token")
        try:
            user_resp = api_get(server, USER_INFO_URL, cached["token"], proxies, cached)
            if isinstance(user_resp, dict) and user_resp.get("user"):
                print("✅ [缓存] token 有效")
                return cached["token"], cached
        except Exception as exc:
            print(f"⚠️ [缓存] 验证异常: {exc}")
        print("⚠️ [缓存] token 已失效，重新登录")

    code = get_code(server)
    if not code:
        return None, None

    token, acct = login_by_code(server, code, proxies)
    if not token or not acct:
        return None, acct if acct else None

    set_cached_account(server, acct)
    return token, acct


# ====================== 业务接口 ======================
def build_risk_params(acct: Dict[str, Any], with_fingerprint: bool = False) -> Dict[str, Any]:
    risk = {
        "version": "0.2.233",
        "openId": acct.get("openId", ""),
        "expoId": acct.get("openIdCipher", ""),
    }
    if with_fingerprint:
        risk["appletsFingerprint"] = CAPTURED_APPLETS_FINGERPRINT
    return risk


def check_in(server: str, token: str, proxies: Dict[str, str] | None, acct: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "day": 1,
        "missionSecretId": CAPTURED_MISSION_SECRET_ID,
        "xcxVersion": "0.2.233",
        "channelType": "SELF",
        "appContainer": "WEI_XIN_APP",
        "os": "UNKNOW",
        "lat": CAPTURED_LAT,
        "lon": CAPTURED_LON,
        "pagePvId": CAPTURED_PAGE_PVID,
        "wxp": CAPTURED_WXP,
        "riskParams": build_risk_params(acct, with_fingerprint=True),
    }
    return api_post(server, CHECK_IN_URL, token, proxies, payload, acct)


def activity_list(server: str, token: str, proxies: Dict[str, str] | None, acct: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "queryModel": ["INVITE", "FREE_MEAL_HISTORY"],
        "xcxVersion": "0.2.233",
        "channelType": "SELF",
        "appContainer": "WEI_XIN_APP",
        "os": "UNKNOW",
        "lat": 0,
        "lon": 0,
        "pagePvId": CAPTURED_PAGE_PVID,
        "wxp": CAPTURED_WXP,
        "riskParams": build_risk_params(acct),
    }
    return api_post(server, ACTIVITY_LIST_URL, token, proxies, payload, acct)


def wallet_balance(server: str, token: str, proxies: Dict[str, str] | None, acct: Dict[str, Any]) -> Dict[str, Any]:
    payload = {
        "xcxVersion": "0.2.233",
        "channelType": "SELF",
        "appContainer": "WEI_XIN_APP",
        "os": "UNKNOW",
        "lat": CAPTURED_LAT,
        "lon": CAPTURED_LON,
        "pagePvId": CAPTURED_PAGE_PVID,
        "wxp": CAPTURED_WXP,
        "riskParams": build_risk_params(acct, with_fingerprint=True),
    }
    return api_post(server, WALLET_BALANCE_URL, token, proxies, payload, acct)


def format_sign_msg(resp: Dict[str, Any]) -> str:
    ret = resp.get("ret")
    if ret == 0:
        data = safe_data(resp)
        award_type = data.get("awardType") or ""
        award_value = data.get("awardValue")
        if award_type and award_value is not None:
            return f"签到成功，奖励 {award_type} {award_value}"
        return "签到成功"
    if ret == 217:
        return "今日已签到"
    return resp.get("msg") or resp.get("message") or "签到失败"


def run_account(index: int, total: int, server: str) -> Dict[str, Any]:
    result = {
        "server": server,
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "token": "-",
        "signMsg": "-",
        "activityMsg": "-",
        "balance": "-",
        "error": "",
    }

    log_account_header(index, total, server)

    proxies, proxy_ip = get_valid_proxy(server)
    result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"

    sleep(PROXY_FETCH_INTERVAL)

    delay = random.randint(2, 6)
    print(f"⏳ [延迟] 启动延迟 {delay}s")
    sleep(delay)

    token, acct = login_with_cache(server, proxies)
    if not token or not acct:
        result["error"] = f"登录失败: {json_preview(acct)}"
        return result

    result["token"] = mask(token)

    try:
        sign_resp = check_in(server, token, proxies, acct)
        result["signMsg"] = format_sign_msg(sign_resp)
        print(f"✅ [签到] {result['signMsg']}")

        act_resp = activity_list(server, token, proxies, acct)
        if act_resp.get("ret") == 0:
            act_data = safe_data(act_resp)
            invite = act_data.get("invite") or {}
            details = invite.get("prizeDetails") or []
            if details:
                parts = [
                    f"{d.get('title', '')}: {d.get('awardType', '')} {d.get('awardValue', '')}"
                    for d in details
                ]
                result["activityMsg"] = "；".join(parts)
                print(f"🎁 [活动] {result['activityMsg']}")
            else:
                result["activityMsg"] = "暂无活动详情"
                print(f"🎁 [活动] 暂无活动详情")
        else:
            result["activityMsg"] = act_resp.get("msg") or act_resp.get("message") or "活动查询失败"
            print(f"⚠️ [活动] {result['activityMsg']}")

        balance_resp = wallet_balance(server, token, proxies, acct)
        if balance_resp.get("ret") == 0:
            balance_raw = safe_data(balance_resp).get("balance", 0)
            balance = to_float(balance_raw) / 100
            result["balance"] = f"{balance:.2f} 元"
            print(f"💰 [余额] 当前余额: {balance:.2f} 元")
        else:
            result["balance"] = balance_resp.get("msg") or balance_resp.get("message") or "余额查询失败"
            print(f"⚠️ [余额] {result['balance']}")

        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    content = f"""♻️ 美团返返外卖返现任务结果

━━━━━━━━━━━━━━━━━━━━
🏁 总结：{success_count} 成功 / {fail_count} 失败
🕒 时间：{now_text()}
━━━━━━━━━━━━━━━━━━━━
"""

    for idx, res in enumerate(results, 1):
        icon = "✅" if res["success"] else "❌"

        content += f"""
🧩 账号 {idx}
🌍 来源：{res["server"]}
🌐 代理：{res["proxyStatus"]}
📡 出口IP：{res["proxyIp"]}
🔐 Token：{res["token"]}
📝 签到：{res["signMsg"]}
🎁 活动：{res["activityMsg"]}
💰 余额：{res["balance"]}
{icon} 结果：{"成功" if res["success"] else "失败"}
"""

        if not res["success"]:
            content += f"❌ 原因：{res['error']}\n"

        content += "━━━━━━━━━━━━━━━━━━━━\n"

    return content


def main() -> None:
    log_title()

    results: List[Dict[str, Any]] = []

    for index, server in enumerate(SERVERS, 1):
        try:
            result = run_account(index, len(SERVERS), server)
            results.append(result)
        except Exception as exc:
            print(f"❌ [主程序] {server} 执行异常: {exc}")
            results.append({
                "server": server,
                "success": False,
                "proxyStatus": "-",
                "proxyIp": "-",
                "token": "-",
                "signMsg": "-",
                "activityMsg": "-",
                "balance": "-",
                "error": traceback.format_exc().strip(),
            })

        if index < len(SERVERS):
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 美团返返任务执行完成                      ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    send_pushplus("♻️ 美团返返外卖返现任务完成", build_notify(results))


if __name__ == "__main__":
    main()
