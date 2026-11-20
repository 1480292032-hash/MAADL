# -*- coding: utf-8 -*-
"""冬雪乐园·奇妙车（惊喜翻牌）Agent

由 pipeline 节点「乐园翻牌循环」（Custom Action）触发，接管翻牌全循环：
选择奖励 → 开始翻牌 → 点牌 → 漩涡进下一层 → 钥匙耗尽后返回（交给 JSON 的邮箱补给链）。

奖励档位配置从 config/park_config.json 读取（本地网页「启动网页配置.bat」可修改）。
"""

import json
import os
import random
import re
import time

from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.custom_recognition import CustomRecognition

# ---------- 配置 ----------

_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PROJECT_DIR, "config", "park_config.json")

DEFAULT_CONFIG = {
    "normal_slot": 1,  # 每层（非10整数层）奖励：选第几个槽位（1~5）
    "boss_slot": 1,    # 每 10 层大奖：选第几个槽位（1~5）
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in DEFAULT_CONFIG:
            if key in data:
                cfg[key] = int(data[key])
    except Exception:
        pass
    cfg["normal_slot"] = max(1, min(5, cfg["normal_slot"]))
    cfg["boss_slot"] = max(1, min(5, cfg["boss_slot"]))
    return cfg


# ---------- 实测坐标 ----------

FLOP_GRID = [  # 20 张牌中心坐标（5 列 × 4 行）
    (100, 215), (270, 215), (440, 215), (610, 215), (780, 215),
    (100, 345), (270, 345), (440, 345), (610, 345), (780, 345),
    (100, 475), (270, 475), (440, 475), (610, 475), (780, 475),
    (100, 605), (270, 605), (440, 605), (610, 605), (780, 605),
]
REWARD_SLOTS = [(363, 325), (513, 325), (663, 325), (813, 325), (963, 325)]  # 选择奖励弹窗 5 个槽位
NORMAL_TAB = (265, 325)   # 弹窗左列「每1~9层」页签
BOSS_TAB = (265, 495)     # 弹窗左列「每10层」页签
CONFIRM_NEXT = (455, 497)     # 「前往下一层」确认弹窗的确认按钮
LEAVE_CONFIRM = (893, 518)    # 乐园退出确认弹窗的确认按钮
BACK_POS = (55, 35)           # 返回按钮（关弹窗/退界面两用）

_RECO_TIMEOUT_NOTE = "识别异常"


def _log(msg: str):
    print(f"[park] {msg}", flush=True)


def _hit(context, image, entry):
    """复用 pipeline 识别节点，返回 RecognitionDetail（.hit/.box/.text）或 None"""
    try:
        return context.run_recognition(entry, image)
    except Exception as e:
        _log(f"识别 {entry} 异常: {e}")
        return None


def _box_center(detail):
    if detail is None or not getattr(detail, "hit", False):
        return None
    box = getattr(detail, "box", None)
    if not box:
        return None
    x, y, w, h = box[0], box[1], box[2], box[3]
    return (int(x + w / 2), int(y + h / 2))


def _ocr_int(context, image, entry):
    detail = _hit(context, image, entry)
    if detail is None or not getattr(detail, "hit", False):
        return -1
    text = getattr(detail, "text", "") or ""
    m = re.search(r"\d+", text)
    return int(m.group()) if m else -1


def _keys(context, image):
    """剩余翻牌次数：优先读右侧「剩余次数 N/1」，备选顶部钥匙图标数字"""
    detail = _hit(context, image, "乐园_剩余次数")
    if detail is not None and getattr(detail, "hit", False):
        text = getattr(detail, "text", "") or ""
        m = re.search(r"(\d+)\s*/", text)
        if m:
            return int(m.group(1))
    return _ocr_int(context, image, "乐园_钥匙数")


@AgentServer.custom_action("park_farm_loop")
class ParkFarm(CustomAction):
    def run(self, context, argv) -> bool:
        cfg = load_config()
        _log(f"乐园配置: {cfg}")
        ctrl = context.tasker.controller
        if ctrl is None:
            _log("controller 不可用")
            return False

        def click(x, y, delay=1.0):
            ctrl.post_click(int(x), int(y)).wait()
            time.sleep(delay)

        def shot():
            return ctrl.post_screencap().get(wait=True)

        idle_rounds = 0

        for _round in range(3000):  # 安全上限，正常远达不到
            img = shot()

            # 不在翻牌界面 → 尝试导航回去
            title = _hit(context, img, "乐园_界面标题")
            if title is None or not title.hit:
                idle_rounds += 1
                _log(f"不在翻牌界面（idle={idle_rounds}），尝试导航")
                if not self._goto_flop_screen(context, ctrl):
                    _log("无法回到翻牌界面，结束翻牌循环")
                    return False
                continue

            idle_rounds = 0

            # 剩余次数 0：最高优先级，不进行任何点击，直接结束翻牌循环
            keys = _keys(context, img)
            if keys <= 0:
                _log("剩余次数 0，交还控制权给邮箱补给流程")
                return True

            # 1) 选择奖励弹窗：按层数选档位 + 槽位
            sel = _hit(context, img, "乐园_选择奖励弹窗")
            if sel is not None and sel.hit:
                layer = _ocr_int(context, img, "乐园_弹窗层号")
                is_boss = layer > 0 and layer % 10 == 0
                slot = cfg["boss_slot"] if is_boss else cfg["normal_slot"]
                _log(f"层 {layer}（{'大奖' if is_boss else '普通'}档）选第 {slot} 个奖励")
                if is_boss:
                    click(*BOSS_TAB, delay=1.5)
                else:
                    click(*NORMAL_TAB, delay=1.5)
                img = shot()
                if _hit(context, img, "乐园_选择奖励弹窗") is not None:
                    click(*REWARD_SLOTS[slot - 1], delay=1.5)
                continue

            # 2) 设置图标是「+」：本层奖励未设置 → 点它打开选择奖励弹窗
            plus = _hit(context, img, "乐园_设置加号")
            if plus is not None and plus.hit:
                _log("本层奖励未设置，点加号打开选择奖励弹窗")
                center = _box_center(plus)
                click(*center, delay=2.5)
                continue

            # 3) 紫色漩涡：层完成 → 确认前往下一层
            vortex = _hit(context, img, "乐园_漩涡")
            if vortex is not None and vortex.hit:
                _log("层完成，点漩涡前往下一层")
                center = _box_center(vortex)
                click(*center, delay=2.0)
                click(*CONFIRM_NEXT, delay=3.0)
                continue

            # 3) 「开始翻牌」粉色按钮（新层已设置奖励）
            start = _hit(context, img, "乐园_开始翻牌")
            if start is not None and start.hit:
                keys = _ocr_int(context, img, "乐园_钥匙数")
                if keys <= 0:
                    _log("钥匙耗尽，交还控制权给邮箱补给流程")
                    return True
                _log("点开始翻牌")
                center = _box_center(start)
                click(*center, delay=3.0)
                continue

            # 4) 未翻牌：随机点一张
            unflipped = _hit(context, img, "乐园_未翻牌")
            if unflipped is not None and unflipped.hit:
                keys = _ocr_int(context, img, "乐园_钥匙数")
                if keys <= 0:
                    _log("钥匙耗尽，交还控制权给邮箱补给流程")
                    return True
                center = _box_center(unflipped)
                _log(f"翻牌（剩余钥匙 {keys}）")
                click(*center, delay=1.8)
                continue

            # 5) 没有任何已知状态
            keys = _ocr_int(context, img, "乐园_钥匙数")
            if keys <= 0:
                _log("钥匙耗尽，交还控制权给邮箱补给流程")
                return True

            idle_rounds += 1
            _log(f"无已知可执行状态（idle={idle_rounds}）")
            if idle_rounds >= 5:
                _log("连续无状态，尝试点返回位恢复")
                click(*BACK_POS, delay=2.0)
                if idle_rounds >= 10:
                    _log("恢复失败，结束翻牌循环")
                    return False
            time.sleep(1.5)

        _log("达到安全循环上限，结束")
        return False

    # ---- 导航：确保回到惊喜翻牌界面 ----

    def _goto_flop_screen(self, context, ctrl):
        def click(x, y, delay=1.5):
            ctrl.post_click(int(x), int(y)).wait()
            time.sleep(delay)

        def shot():
            return ctrl.post_screencap().get(wait=True)

        for attempt in range(3):
            img = shot()
            title = _hit(context, img, "乐园_界面标题")
            if title is not None and title.hit:
                return True

            # 游戏可能闪退到桌面：先跑「游戏启动」拉起（已在前台则无副作用）
            try:
                context.run_action("游戏启动")
                time.sleep(3)
            except Exception as e:
                _log(f"游戏启动节点异常: {e}")

            # 登录前公告面板 → 点右上角 X 关闭（重启后常驻此界面）
            notice = _hit(context, img, "启动_公告面板关闭")
            if notice is not None and notice.hit:
                _log("关闭登录前公告面板")
                click(1128, 92, delay=2.0)
                continue

            # 回主界面：连点返回位（关弹窗/退界面）
            for _ in range(3):
                click(*BACK_POS)
                img = shot()
                main_ui = _hit(context, img, "日常_主界面确认")
                if main_ui is not None and main_ui.hit:
                    break

            main_ui = _hit(context, img, "日常_主界面确认")
            if main_ui is None or not main_ui.hit:
                # 还没到主界面，用家园图标兜底
                home = _hit(context, img, "乐园_家园图标")
                if home is not None and home.hit:
                    center = _box_center(home)
                    click(*center, delay=3.0)
                else:
                    click(1213, 33, delay=3.0)

            # 主界面 → 乐园导航链（复用 pipeline 节点）
            main_ui = _hit(context, shot(), "日常_主界面确认")
            if main_ui is None or not main_ui.hit:
                continue
            try:
                context.run_action("乐园_主界面入口")
                time.sleep(2)
                context.run_action("乐园_手册")
                time.sleep(2)
                context.run_action("乐园_车图标")
                time.sleep(2)
                context.run_action("乐园_进入")
                time.sleep(2)
            except Exception as e:
                _log(f"乐园导航异常: {e}")
                continue

            title = _hit(context, shot(), "乐园_界面标题")
            if title is not None and title.hit:
                return True
        return False


@AgentServer.custom_recognition("乐园状态诊断")
class ParkStateReco(CustomRecognition):
    """调试用识别：输出层号/钥匙/奖池摘要（可在 pipeline 中挂为节点观察）"""

    def analyze(self, context, argv):
        layer = _ocr_int(context, argv.image, "乐园_层号")
        keys = _ocr_int(context, argv.image, "乐园_钥匙数")
        unflipped = _hit(context, argv.image, "乐园_未翻牌")
        unflipped_count = -1
        if unflipped is not None:
            all_results = getattr(unflipped, "all_results", None) or []
            unflipped_count = len(all_results) if all_results else (1 if unflipped.hit else 0)
        _log(f"层={layer} 钥匙={keys} 未翻={unflipped_count}")
        if layer < 0 and keys < 0:
            return None
        box = (783, 200, 60, 60)
        return CustomRecognition.AnalyzeResult(box=box, detail={"layer": layer, "keys": keys, "unflipped": unflipped_count})
