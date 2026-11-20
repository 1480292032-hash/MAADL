# -*- coding: utf-8 -*-
"""MAADL 本地网页配置服务（零依赖，Python 标准库实现）

启动：双击项目根目录「启动网页配置.bat」或 `python webui/server.py`
浏览器访问：http://127.0.0.1:8512

功能：
- 读写 config/park_config.json（冬雪乐园翻牌的奖励档位配置）
- 查看 debug/maafw.log 运行日志
"""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8512
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "park_config.json")
LOG_PATH = os.path.join(ROOT, "debug", "maafw.log")
INDEX_PATH = os.path.join(ROOT, "webui", "index.html")

DEFAULT_CONFIG = {
    "normal_slot": 1,
    "boss_slot": 1,
}

SLOT_OPTIONS = [
    (1, "第 1 个（最左）"),
    (2, "第 2 个"),
    (3, "第 3 个（中间）"),
    (4, "第 4 个"),
    (5, "第 5 个（最右）"),
]


def read_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in DEFAULT_CONFIG:
            if key in data:
                cfg[key] = int(data[key])
    except Exception:
        pass
    for key in DEFAULT_CONFIG:
        cfg[key] = max(1, min(5, int(cfg[key])))
    return cfg


def write_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    payload = dict(cfg)
    payload["updated_at"] = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=4)


def tail_log(lines=120):
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            return "".join(f.readlines()[-lines:])
    except Exception as e:
        return f"(暂无日志: {e})"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, content, ctype="application/json; charset=utf-8"):
        data = content.encode("utf-8") if isinstance(content, str) else content
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(500, "index.html 缺失")
        elif path == "/api/config":
            cfg = read_config()
            cfg["_slot_options"] = [name for _, name in SLOT_OPTIONS]
            self._send(200, json.dumps(cfg, ensure_ascii=False))
        elif path == "/api/log":
            self._send(200, json.dumps({"log": tail_log()}, ensure_ascii=False))
        else:
            self._send(404, "{}")

    def do_POST(self):
        if self.path != "/api/config":
            self._send(404, "{}")
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            cfg = json.loads(self.rfile.read(length).decode("utf-8"))
            write_config(cfg)
            self._send(200, json.dumps({"ok": True, "config": read_config()}, ensure_ascii=False))
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


if __name__ == "__main__":
    print(f"MAADL 网页配置服务已启动: http://127.0.0.1:{PORT}")
    print("配置文件:", CONFIG_PATH)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
