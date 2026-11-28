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
ARENA_CONFIG_PATH = os.path.join(ROOT, "config", "arena_config.json")
DAILY_CONFIG_PATH = os.path.join(ROOT, "config", "daily_config.json")
LOG_PATH = os.path.join(ROOT, "debug", "maafw.log")
INDEX_PATH = os.path.join(ROOT, "webui", "index.html")

DEFAULT_CONFIG = {
    "normal_slot": 1,
    "boss_slot": 1,
}

ARENA_DEFAULT = {
    "battles_per_day": 5,
    "done_today": 0,
    "date": "",
}

DEFAULT_DAILY_ORDER = [
    "签到", "补给站", "邮件", "联络", "社团", "研发中心", "次元入侵",
    "追猎计划", "特训矩阵", "竞技场", "精灵试炼", "世界之眼", "魔女日程",
    "每日副本", "剧情推图", "夏拉姆符石挑战", "剧场番外", "约会料理",
]

# 约会料理菜单（与 agent/park.py DateCooking.FOODS 保持一致）
COOK_MENUS = {
    "面包店": ["干面包", "牛奶面包", "奶酪蛋挞", "黄油面包", "布丁蛋糕",
               "黄豆粉面包", "炸薯球", "抹茶蛋糕", "糯米团子", "草莓大福", "红豆年糕汤"],
    "饮品店": ["清甜果汁", "蔬菜汁", "糯米酒", "水果布丁", "布丁奶昔",
               "牛奶可可", "薄荷抹茶", "绿色妖姬", "薄荷绿茶"],
    "豪华餐厅": ["水果寿司", "蔬菜蛋卷", "水果披萨", "果蔬拼盘", "多层汉堡",
                 "百味拉面", "意式热狗", "糯米甜豆腐", "果木烤肉排"],
}


def read_park_config():
    cfg = {"mode": "hard", "max_stages": 3}
    try:
        with open(DAILY_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        cfg["mode"] = data.get("park_mode", "hard")
        cfg["max_stages"] = max(1, min(30, int(data.get("park_max_stages", 3))))
        cfg["volume"] = max(1, min(22, int(data.get("story_volume", 6))))
        grades = data.get("kabbalah_grades", ["D"])
        valid = [g for g in ("SSS", "SS", "S", "A", "B", "C", "D") if g in grades]             if isinstance(grades, list) else []
        cfg["kabbalah_grades"] = valid or ["D"]
        cfg["kabbalah_times"] = max(1, min(9, int(data.get("kabbalah_times", 3))))
        cfg["kabbalah_exchange"] = bool(data.get("kabbalah_exchange", False))
        cfg["theater_times"] = max(1, min(5, int(data.get("theater_times", 1))))
        cfg["theater_exchange"] = bool(data.get("theater_exchange", True))
        shop = data.get("cook_shop", "面包店")
        cfg["cook_shop"] = shop if shop in COOK_MENUS else "面包店"
        cfg["cook_food"] = str(data.get("cook_food", "干面包"))
        cfg["cook_times"] = int(data.get("cook_times", 1))
    except Exception:
        pass
    cfg["cook_times"] = cfg["cook_times"] if cfg["cook_times"] in (1, 5, 10) else 1
    return cfg


def write_park_config(mode, max_stages, volume=6, grades=None, times=3, exchange=False,
                      theater_times=None, theater_exchange=None,
                      cook_shop=None, cook_food=None, cook_times=None):
    cfg = {"park_mode": mode if mode in ("story", "hard", "hell") else "hard",
           "park_max_stages": max(1, min(30, int(max_stages))),
           "story_volume": max(1, min(22, int(volume)))}
    os.makedirs(os.path.dirname(DAILY_CONFIG_PATH), exist_ok=True)
    existing = {}
    try:
        with open(DAILY_CONFIG_PATH, "r", encoding="utf-8") as f:
            existing = json.load(f)
    except Exception:
        pass
    existing.update(cfg)
    if isinstance(grades, list):
        valid = [g for g in ("SSS", "SS", "S", "A", "B", "C", "D") if g in grades]
        if valid:
            existing["kabbalah_grades"] = valid
    if times is not None:
        existing["kabbalah_times"] = max(1, min(9, int(times)))
    if exchange is not None:
        existing["kabbalah_exchange"] = bool(exchange)
    if theater_times is not None:
        existing["theater_times"] = max(1, min(5, int(theater_times)))
    if theater_exchange is not None:
        existing["theater_exchange"] = bool(theater_exchange)
    if cook_shop is not None and cook_shop in COOK_MENUS:
        existing["cook_shop"] = cook_shop
        # 换餐厅时若当前食物不属于新菜单则重置为第一种
        menu = COOK_MENUS[cook_shop]
        cur_food = existing.get("cook_food", menu[0])
        existing["cook_food"] = cur_food if cur_food in menu else menu[0]
    if cook_food is not None:
        shop_now = existing.get("cook_shop", "面包店")
        if cook_food in COOK_MENUS.get(shop_now, []):
            existing["cook_food"] = cook_food
    if cook_times is not None and int(cook_times) in (1, 5, 10):
        existing["cook_times"] = int(cook_times)
    with open(DAILY_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=4)
    return {"mode": cfg["park_mode"], "max_stages": cfg["park_max_stages"],
            "volume": cfg["story_volume"],
            "kabbalah_grades": existing.get("kabbalah_grades", ["D"]),
            "kabbalah_times": existing.get("kabbalah_times", 3),
            "kabbalah_exchange": existing.get("kabbalah_exchange", False),
            "theater_times": existing.get("theater_times", 1),
            "theater_exchange": existing.get("theater_exchange", True),
            "cook_shop": existing.get("cook_shop", "面包店"),
            "cook_food": existing.get("cook_food", "干面包"),
            "cook_times": existing.get("cook_times", 1),
            "cook_menus": COOK_MENUS}


def read_daily_order():
    try:
        with open(DAILY_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        order = [s for s in data.get("order", []) if s in DEFAULT_DAILY_ORDER]
        if order:
            return order
    except Exception:
        pass
    return list(DEFAULT_DAILY_ORDER)


def write_daily_order(order):
    os.makedirs(os.path.dirname(DAILY_CONFIG_PATH), exist_ok=True)
    with open(DAILY_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump({"order": order}, f, ensure_ascii=False, indent=4)

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


def read_arena_config():
    cfg = dict(ARENA_DEFAULT)
    try:
        with open(ARENA_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ARENA_DEFAULT:
            if key in data:
                cfg[key] = data[key]
    except Exception:
        pass
    cfg["battles_per_day"] = max(0, min(99, int(cfg["battles_per_day"])))
    cfg["done_today"] = max(0, int(cfg["done_today"]))
    return cfg


def write_arena_battles(battles_per_day):
    cfg = read_arena_config()
    cfg["battles_per_day"] = max(0, min(99, int(battles_per_day)))
    os.makedirs(os.path.dirname(ARENA_CONFIG_PATH), exist_ok=True)
    with open(ARENA_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=4)
    return cfg


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
            cfg["arena"] = read_arena_config()
            cfg["park"] = read_park_config()
            cfg["park"]["cook_menus"] = COOK_MENUS
            cfg["daily_order"] = read_daily_order()
            self._send(200, json.dumps(cfg, ensure_ascii=False))
        elif path == "/api/log":
            self._send(200, json.dumps({"log": tail_log()}, ensure_ascii=False))
        else:
            self._send(404, "{}")

    def do_POST(self):
        path = self.path.split("?")[0]
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if path == "/api/config":
                write_config(body)
                cfg = read_config()
                cfg["arena"] = read_arena_config()
                cfg["daily_order"] = read_daily_order()
                self._send(200, json.dumps({"ok": True, "config": cfg}, ensure_ascii=False))
            elif path == "/api/arena":
                cfg = write_arena_battles(body.get("battles_per_day", 5))
                self._send(200, json.dumps({"ok": True, "arena": cfg}, ensure_ascii=False))
            elif path == "/api/park":
                cfg = write_park_config(body.get("mode", "hard"), body.get("max_stages", 3),
                                        body.get("volume", 6), body.get("grades"),
                                        body.get("kabbalah_times"), body.get("kabbalah_exchange"),
                                        body.get("theater_times"), body.get("theater_exchange"),
                                        body.get("cook_shop"), body.get("cook_food"),
                                        body.get("cook_times"))
                self._send(200, json.dumps({"ok": True, "park": cfg}, ensure_ascii=False))
            elif path == "/api/daily":
                order = [s for s in body.get("order", []) if s in DEFAULT_DAILY_ORDER]
                write_daily_order(order)
                self._send(200, json.dumps({"ok": True, "order": read_daily_order()}, ensure_ascii=False))
            else:
                self._send(404, "{}")
        except Exception as e:
            self._send(400, json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))

    def log_message(self, fmt, *args):
        pass  # 静默访问日志


if __name__ == "__main__":
    print(f"MAADL 网页配置服务已启动: http://127.0.0.1:{PORT}")
    print("配置文件:", CONFIG_PATH)
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
