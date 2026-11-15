#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
央视 + 卫视 直播源爬虫
======================
流程：从 sources.txt 里的多个上游源抓取 -> 按 channels.txt 模板匹配央视/卫视频道
     -> 逐条校验并测速 -> 去重排序 -> 生成 TVBox 可用的 result.txt / result.m3u。

仅使用 Python 标准库，无需安装任何依赖。
本地运行：python3 crawler.py
跳过测速（只解析生成，不校验）：SKIP_VALIDATE=1 python3 crawler.py
"""
import json
import os
import re
import sys
import time
import ipaddress
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "output")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
CST = timezone(timedelta(hours=8))

DEFAULT_CONFIG = {
    "keep_per_channel": 6,            # 每个频道保留几条线路（供 TVBox 换源）
    "max_candidates_per_channel": 20, # 每个频道最多校验多少条候选
    "workers": 24,                    # 测速并发数
    "channel_workers": 10,            # 同时处理的频道数
    "speed_test_seconds": 3,          # 每条源最多测速几秒
    "speed_test_bytes": 1572864,      # 每条源最多下载多少字节(1.5MB)
    "min_speed_mbps": 0.05,           # 最低速率 MB/s，低于则丢弃
    "lenient": True,                  # 宽松模式：播放列表有效但测速失败时仍保留（排末尾）
    "include_ipv6": False,            # 是否保留 IPv6 源（家里宽带支持才开）
    "request_timeout": 10,            # 普通请求超时(秒)
    "logo_base": "https://raw.githubusercontent.com/fanmingming/live/main/tv",
}


def log(msg):
    print(msg, flush=True)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    p = os.path.join(BASE, "config.json")
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log(f"[警告] config.json 解析失败，使用默认配置: {e}")
    return cfg


# ---------------- 频道模板与匹配 ----------------

def normalize(name):
    s = name.lower().strip()
    s = re.sub(r"[\s\-_·．.\u3000]+", "", s)
    for _ in range(3):
        s2 = re.sub(r"(高清|超清|蓝光|标清|hd|fhd|uhd|50fps|60fps|hevc|h265|h264|频道)$", "", s)
        if s2 == s:
            break
        s = s2
    return s


def load_channels(path):
    """返回 (频道顺序列表[(分类, 显示名)], 匹配函数)"""
    channels = []          # [(category, display)]
    alias_map = {}         # 归一化别名 -> 显示名
    cctv_keys = {}         # cctv1 / cctv5+ / cctv4k -> 显示名
    category = "未分类"
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.endswith(",#genre#"):
                category = line.split(",")[0].strip()
                continue
            parts = [p.strip() for p in line.split("|") if p.strip()]
            display = parts[0]
            channels.append((category, display))
            for a in parts:
                alias_map[normalize(a)] = display
            up = display.upper()
            if up == "CCTV-4K":
                cctv_keys["cctv4k"] = display
            elif up == "CCTV-5+":
                cctv_keys["cctv5+"] = display
            else:
                m = re.match(r"^CCTV-(\d+)$", display, re.I)
                if m:
                    cctv_keys["cctv" + m.group(1)] = display

    def match(name):
        if not name:
            return None
        if any(x in name for x in ("欧洲", "美洲")):
            return None
        n = normalize(name)
        m = re.match(r"^cctv(\d+)(\+|k)?", n)
        if m:
            key = "cctv" + m.group(1) + (m.group(2) or "")
            return cctv_keys.get(key)
        m = re.search(r"中央(\d+)套", n)
        if m:
            return cctv_keys.get("cctv" + m.group(1))
        return alias_map.get(n)

    return channels, match


# ---------------- 抓取 ----------------

def fetch(url, timeout, max_bytes=None):
    """抓取 URL；raw.githubusercontent.com 失败时自动走 GitHub API 备用通道。"""
    headers = {"User-Agent": UA, "Accept": "*/*"}
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read(max_bytes) if max_bytes else r.read()
    except Exception as e:
        m = re.match(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", url)
        if m:
            owner, repo, branch, path = m.groups()
            api = (f"https://api.github.com/repos/{owner}/{repo}/contents/{path}"
                   f"?ref={branch}")
            req = urllib.request.Request(api, headers={
                "User-Agent": UA, "Accept": "application/vnd.github.raw"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(max_bytes) if max_bytes else r.read()
        raise


def parse_source(text):
    """自动识别 m3u / txt，返回 [(频道名, url)]"""
    entries = []
    if text.lstrip().startswith("#EXTM3U"):
        name = None
        tvg = None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("#EXTINF"):
                name = line.rsplit(",", 1)[-1].strip()
                m = re.search(r'tvg-name="([^"]+)"', line)
                tvg = m.group(1) if m else None
            elif line and not line.startswith("#"):
                if name:
                    entries.append((name, tvg, line))
                name, tvg = None, None
    else:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ",#genre#" in line:
                continue
            if "," in line:
                n, u = line.split(",", 1)
                entries.append((n.strip(), None, u.strip()))
    return entries


def host_is_private(url):
    try:
        host = urlparse(url).hostname
        if not host:
            return True
        if host in ("localhost",):
            return True
        try:
            ip = ipaddress.ip_address(host)
            return ip.is_private or ip.is_loopback or ip.is_link_local
        except ValueError:
            return False  # 域名
    except Exception:
        return True


def is_ipv6(url):
    try:
        host = urlparse(url).hostname or ""
        return ":" in host
    except Exception:
        return False


# ---------------- 校验与测速 ----------------

def read_stream(url, timeout, seconds, max_bytes):
    """下载最多 max_bytes / seconds，返回 (字节数, 耗时, 内容头部)"""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    start = time.time()
    got = 0
    head = b""
    with urllib.request.urlopen(req, timeout=timeout) as r:
        while True:
            if time.time() - start > seconds or got >= max_bytes:
                break
            chunk = r.read(65536)
            if not chunk:
                break
            if got < 2048:
                head += chunk[:2048 - got]
            got += len(chunk)
    return got, max(time.time() - start, 0.001), head


def validate(url, cfg):
    """返回速率 MB/s；失败返回 None"""
    timeout = cfg["request_timeout"]
    secs = cfg["speed_test_seconds"]
    nbytes = cfg["speed_test_bytes"]
    try:
        body = fetch(url, timeout, max_bytes=524288)
        text = body.decode("utf-8", "ignore")
        if "#EXTM3U" not in text[:2048]:
            # 不是 m3u8：可能是直连流(flv/ts/php)，也可能是网页
            head = body[:1024].lstrip()
            if head.startswith(b"<!DOCTYPE") or head.startswith(b"<html") or b"<html" in head[:512].lower():
                return None
            try:
                got, dur, _ = read_stream(url, timeout, secs, nbytes)
                if got >= 32768:
                    return got / dur / 1048576
            except Exception:
                pass
            return 0.05 if cfg.get("lenient", True) else None
        # master 播放列表：选码率最高的子流
        if "#EXT-X-STREAM-INF" in text:
            best, best_bw = None, -1
            lines = text.splitlines()
            for i, l in enumerate(lines):
                if l.startswith("#EXT-X-STREAM-INF"):
                    m = re.search(r"BANDWIDTH=(\d+)", l)
                    bw = int(m.group(1)) if m else 0
                    if i + 1 < len(lines) and not lines[i + 1].startswith("#"):
                        if bw > best_bw:
                            best_bw, best = bw, lines[i + 1].strip()
            if not best:
                return None
            url = urljoin(url, best)
            body = fetch(url, timeout, max_bytes=524288)
            text = body.decode("utf-8", "ignore")
        # 广告/点播占位循环：带 ENDLIST 且分片极少
        segs = [l.strip() for l in text.splitlines()
                if l.strip() and not l.startswith("#")]
        if "#EXT-X-ENDLIST" in text and len(segs) <= 6:
            return None
        if not segs:
            return None
        seg_url = urljoin(url, segs[0])
        try:
            got, dur, _ = read_stream(seg_url, timeout, secs, nbytes)
            if got >= 32768:
                return got / dur / 1048576
        except Exception:
            pass
        # 播放列表有效但分片测速失败（常见于 GitHub 海外节点访问国内源）：
        # 宽松模式下保留该源并排在末尾，由 TVBox 自动换源兜底
        return 0.05 if cfg.get("lenient", True) else None
    except Exception:
        return None


# ---------------- 主流程 ----------------

def main():
    cfg = load_config()
    skip_validate = os.environ.get("SKIP_VALIDATE") == "1"
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    now = datetime.now(CST).strftime("%Y-%m-%d %H:%M")

    channels, match = load_channels(os.path.join(BASE, "channels.txt"))
    log(f"频道模板：{len(channels)} 个频道")

    # 1. 抓取上游源
    candidates = {disp: [] for _, disp in channels}
    seen_urls = set()
    seed_status = []
    with open(os.path.join(BASE, "sources.txt"), encoding="utf-8") as f:
        seeds = [l.strip() for l in f
                 if l.strip() and not l.strip().startswith("#")]
    for url in seeds:
        try:
            text = fetch(url, 30).decode("utf-8", "ignore")
            entries = parse_source(text)
            hit = 0
            for name, tvg, u in entries:
                if not u.startswith(("http://", "https://")):
                    continue
                if not cfg["include_ipv6"] and is_ipv6(u):
                    continue
                if host_is_private(u):
                    continue
                disp = match(name) or match(tvg)
                if not disp or disp not in candidates:
                    continue
                if u in seen_urls:
                    continue
                seen_urls.add(u)
                candidates[disp].append(u)
                hit += 1
            seed_status.append(f"[OK] {url}  条目{len(entries)} 命中{hit}")
            log(seed_status[-1])
        except Exception as e:
            seed_status.append(f"[失败] {url}  {e}")
            log(seed_status[-1])

    # 2. 去重(同主机同路径只留一条) + 主机多样化 + 截断
    for disp, urls in candidates.items():
        by_key = {}
        for u in urls:
            p = urlparse(u)
            key = (p.hostname, p.path)
            by_key.setdefault(key, u)
        uniq = list(by_key.values())
        by_host = {}
        for u in uniq:
            by_host.setdefault(urlparse(u).hostname, []).append(u)
        mixed, i = [], 0
        while True:
            added = False
            for host in by_host:
                if i < len(by_host[host]):
                    mixed.append(by_host[host][i])
                    added = True
            if not added:
                break
            i += 1
        candidates[disp] = mixed[:cfg["max_candidates_per_channel"]]
    total = sum(len(v) for v in candidates.values())
    log(f"候选接口共 {total} 条，开始校验测速（skip_validate={skip_validate}）")

    # 3. 分校验（每频道分波测试，凑够即停）
    results = {}
    url_executor = ThreadPoolExecutor(max_workers=cfg["workers"])

    def test_channel(item):
        disp, urls = item
        if skip_validate:
            return disp, [(u, 0.0) for u in urls[:cfg["keep_per_channel"]]]
        good = []
        wave = 8
        for i in range(0, len(urls), wave):
            batch = urls[i:i + wave]
            futs = {url_executor.submit(validate, u, cfg): u for u in batch}
            for fut in futs:
                try:
                    speed = fut.result()
                except Exception:
                    speed = None
                if speed is not None and speed >= cfg["min_speed_mbps"]:
                    good.append((futs[fut], speed))
            if len(good) >= cfg["keep_per_channel"]:
                break
        good.sort(key=lambda x: -x[1])
        return disp, good[:cfg["keep_per_channel"]]

    with ThreadPoolExecutor(max_workers=cfg["channel_workers"]) as ch_ex:
        for disp, good in ch_ex.map(test_channel, candidates.items()):
            results[disp] = good
            log(f"  {disp}: {len(good)} 条可用")

    kept = sum(len(v) for v in results.values())
    if kept == 0:
        log("[错误] 没有任何可用接口，保留上次结果，不覆盖输出。")
        with open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8") as f:
            f.write(f"{now} 更新失败：无可用接口\n" + "\n".join(seed_status))
        sys.exit(1)

    # 4. 生成结果
    txt_lines, m3u_lines = [], ["#EXTM3U"]
    logo_map = {}
    for _, disp in channels:
        if disp.startswith("CCTV-"):
            logo_map[disp] = disp.replace("-", "") + ".png"
        else:
            logo_map[disp] = disp + ".png"
    current_cat = None
    for cat, disp in channels:
        good = results.get(disp) or []
        if not good:
            continue
        if cat != current_cat:
            txt_lines.append(f"{cat},#genre#")
            current_cat = cat
        for u, speed in good:
            txt_lines.append(f"{disp},{u}")
            logo = f"{cfg['logo_base']}/{logo_map[disp]}"
            m3u_lines.append(
                f'#EXTINF:-1 tvg-name="{disp}" tvg-logo="{logo}" group-title="{cat}",{disp}')
            m3u_lines.append(u)
    txt_lines.append("更新时间,#genre#")
    txt_lines.append(f"{now} 更新,https://github.com")

    with open(os.path.join(OUT, "result.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(txt_lines) + "\n")
    with open(os.path.join(OUT, "result.m3u"), "w", encoding="utf-8") as f:
        f.write("\n".join(m3u_lines) + "\n")
    empty = [d for _, d in channels if not results.get(d)]
    summary = (f"{now} 更新成功：{kept} 条可用接口，"
               f"{sum(1 for v in results.values() if v)}/{len(channels)} 个频道有源，"
               f"耗时 {time.time() - t0:.0f} 秒")
    log(summary)
    with open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8") as f:
        f.write(summary + "\n\n" + "\n".join(seed_status) +
                "\n\n无源频道：" + (",".join(empty) or "无") + "\n")


if __name__ == "__main__":
    main()
