#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# name: YYB-Go 双变量同步助手
# cron: 0 */6 * * *
# desc: 自动从 YYB-Go 获取账号，并通过青龙 OpenAPI 自动同步到 YYB_GO 与 XC_SERVERS
#
# =====================================================================
# 💡 【青龙面板「环境变量」配置指南】
# 请在青龙面板的「环境变量」页面中添加以下变量：
# 1. YYB_HOST       -> 你的 YYB-Go 地址和端口 (例: 192.168.250.250:8000)
# 2. YYB_TOKEN      -> 你的 YYB-Go 管理员网页登录 Cookie中yyb_session部分
# 3. QL_HOST        -> 你的青龙面板地址 (例: http://192.168.250.250:5700,必须为http或https开头)
# 4. QL_CLIENT_ID   -> 你的青龙应用 Client ID
# 5. QL_CLIENT_SECRET -> 你的青龙应用 Client Secret
# =====================================================================

import os
import json
import requests

# 1. 基础配置（全部直接从青龙环境变量读取）
YYB_HOST = os.getenv("YYB_HOST", "").replace("http://", "").replace("https://", "").rstrip("/")
YYB_TOKEN = os.getenv("YYB_TOKEN", "")

QL_HOST = os.getenv("QL_HOST", "").rstrip("/")
QL_CLIENT_ID = os.getenv("QL_CLIENT_ID", "")
QL_CLIENT_SECRET = os.getenv("QL_CLIENT_SECRET", "")

def get_ql_token() -> str:
    """获取青龙 OpenAPI 访问 Token"""
    if not QL_HOST or not QL_CLIENT_ID or not QL_CLIENT_SECRET:
        print("❌ [青龙认证] 环境变量不完整！请检查 QL_HOST、QL_CLIENT_ID、QL_CLIENT_SECRET 是否已在青龙面板中配置。")
        return ""

    print("🔑 [青龙认证] 正在向青龙面板请求授权 Token...")
    try:
        url = f"{QL_HOST}/open/auth/token?client_id={QL_CLIENT_ID}&client_secret={QL_CLIENT_SECRET}"
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("code") == 200:
                print("✅ [青龙认证] Token 获取成功！")
                return data.get("data", {}).get("token")
        print(f"❌ [青龙认证] 失败，响应内容: {resp.text}")
    except Exception as e:
        print(f"❌ [青龙认证] 异常: {e}")
    return ""

def fetch_yyb_accounts() -> list:
    """从 YYB-Go 获取所有账号列表"""
    if not YYB_HOST or not YYB_TOKEN:
        print("❌ [YYB同步] 未配置 YYB_HOST 或 YYB_TOKEN 环境变量！")
        return []

    url = f"http://{YYB_HOST}/accounts"
    cookies = {"yyb_session": YYB_TOKEN}
    headers = {"token": YYB_TOKEN}
    print(f"🌐 [YYB同步] 正在请求 YYB-Go 接口: {url}")
    try:
        resp = requests.get(url, headers=headers, cookies=cookies, timeout=10)
        print(f"📥 [YYB同步] 响应状态码: {resp.status_code}")
        if resp.status_code == 200:
            res_data = resp.json()
            acc_list = res_data.get("data", [])
            if isinstance(acc_list, list):
                print(f"✅ [YYB同步] 成功从 YYB-Go 获取到 {len(acc_list)} 个账号数据")
                return acc_list
        print(f"⚠️ [YYB同步] 获取账号异常，返回内容: {resp.text}")
    except Exception as e:
        print(f"❌ [YYB同步] 请求异常: {e}")
    return []

def update_ql_env(ql_token: str, env_name: str, env_value: str, env_remark: str) -> None:
    """精准更新或创建指定的青龙环境变量并打印详细日志"""
    headers = {"Authorization": f"Bearer {ql_token}", "Content-Type": "application/json"}
    print(f"\n----------------------------------------")
    print(f"📌 [青龙同步] 正在处理环境变量: 【{env_name}】")
    
    try:
        search_url = f"{QL_HOST}/open/envs?searchValue={env_name}"
        resp = requests.get(search_url, headers=headers, timeout=10)
        envs = resp.json().get("data", [])
        
        target_env = None
        for env in envs:
            if env.get("name") == env_name:
                target_env = env
                break

        if target_env:
            env_id = target_env.get("id") or target_env.get("_id")
            print(f"🔍 [青龙查询] 发现已存在同名变量 (ID: {env_id})，准备执行【更新】...")
            update_url = f"{QL_HOST}/open/envs"
            payload = {
                "id": env_id,
                "name": env_name,
                "value": env_value,
                "remarks": env_remark
            }
            put_resp = requests.put(update_url, headers=headers, json=payload, timeout=10)
            print(f"🔄 [青龙结果] 更新状态码: {put_resp.status_code}")
        else:
            print(f"🔍 [青龙查询] 发现未创建同名变量，准备执行【新建】...")
            create_url = f"{QL_HOST}/open/envs"
            payload = [{
                "name": env_name,
                "value": env_value,
                "remarks": env_remark
            }]
            post_resp = requests.post(create_url, headers=headers, json=payload, timeout=10)
            print(f"✨ [青龙结果] 新建状态码: {post_resp.status_code}")
            
    except Exception as e:
        print(f"❌ [青龙写入] 写入环境变量 {env_name} 异常: {e}")

def main():
    print("========================================")
    print("🚀 开始执行 YYB_GO & XC_SERVERS 同步任务")
    print("========================================")
    
    accounts = fetch_yyb_accounts()
    if not accounts:
        print("❌ [终止] 未能获取到任何 YYB-Go 账号！")
        return

    items_openid = []
    names = []
    
    print("\n📋 [账号解析详情]:")
    for idx, acc in enumerate(accounts, 1):
        openid = acc.get("openid") or acc.get("wxid")
        alias = acc.get("alias") or acc.get("nickname") or f"账号{idx}"
        print(f"  - 账号 [{idx}] 昵称: {alias} | OpenID: {openid if openid else '未获取到'}")
        if openid:
            items_openid.append(f"{YYB_HOST}@{openid}")
            names.append(alias)

    if not items_openid:
        print("❌ [终止] 未能在账号数据中解析到有效的 OpenID！")
        return

    value_yyb_go = "\n".join(items_openid)
    value_xc_servers = ",".join(items_openid)
    remark_str = "自动同步(OpenID): " + ", ".join(names)

    ql_token = get_ql_token()
    if not ql_token:
        print("❌ [终止] 无法连接青龙 OpenAPI，同步中断。")
        return

    update_ql_env(ql_token, "YYB_GO", value_yyb_go, remark_str)
    update_ql_env(ql_token, "XC_SERVERS", value_xc_servers, remark_str)
    
    print("\n========================================")
    print("🎉 所有环境变量同步操作已全部完成！")
    print("========================================")

if __name__ == "__main__":
    main()
