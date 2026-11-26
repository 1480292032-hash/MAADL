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
_ARENA_CONFIG_PATH = os.path.join(_PROJECT_DIR, "config", "arena_config.json")
_DAILY_CONFIG_PATH = os.path.join(_PROJECT_DIR, "config", "daily_config.json")

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


# ---------- 竞技场配置 ----------

ARENA_DEFAULT = {
    "battles_per_day": 5,   # 每日竞技场战斗场数（0 = 不打）
    "done_today": 0,        # 今日已打场数（agent 写入，闪退重启后续打）
    "date": "",             # 记录日期，跨天自动重置 done_today
}


def load_arena_config() -> dict:
    cfg = dict(ARENA_DEFAULT)
    try:
        import datetime

        with open(_ARENA_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in ARENA_DEFAULT:
            if key in data:
                cfg[key] = data[key]
    except Exception:
        pass
    cfg["battles_per_day"] = max(0, min(99, int(cfg["battles_per_day"])))
    cfg["done_today"] = max(0, int(cfg["done_today"]))
    cfg["date"] = str(cfg.get("date", ""))
    today = datetime.date.today().isoformat()
    if cfg["date"] != today:  # 跨天重置今日已打场数
        cfg["done_today"] = 0
        cfg["date"] = today
        save_arena_config(cfg)
    return cfg


def save_arena_config(cfg: dict):
    try:
        os.makedirs(os.path.dirname(_ARENA_CONFIG_PATH), exist_ok=True)
        with open(_ARENA_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=4)
    except Exception as e:
        _log(f"保存竞技场配置失败: {e}")


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


_LOG_FILE = os.path.join(_PROJECT_DIR, "debug", "agent_log.txt")


def _log(msg: str):
    line = f"[park] {msg}"
    print(line, flush=True)
    try:
        with open(_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _hit(context, image, entry):
    """复用 pipeline 识别节点，返回 RecognitionDetail（.hit/.box/.text）或 None"""
    try:
        return context.run_recognition(entry, image)
    except Exception as e:
        _log(f"识别 {entry} 异常: {e}")
        return None


def _dtext(detail):
    """取识别文本：RecognitionDetail 本身没有 text 属性，文本在
    best_result.text（OCRResult）。11-26 实测发现多处误用 getattr(detail,"text")
    永远拿空串，导致模式切换/钥匙数读取一直失败。"""
    if detail is None:
        return ""
    br = getattr(detail, "best_result", None)
    if br is not None:
        return getattr(br, "text", "") or ""
    return ""


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
    text = _dtext(detail)
    m = re.search(r"\d+", text)
    return int(m.group()) if m else -1


def _keys(context, image):
    """剩余翻牌次数：优先读右侧「剩余次数 N/1」，备选顶部钥匙图标数字"""
    detail = _hit(context, image, "乐园_剩余次数")
    if detail is not None and getattr(detail, "hit", False):
        text = _dtext(detail)
        m = re.search(r"(\d+)\s*/", text)
        if m:
            return int(m.group(1))
    return _ocr_int(context, image, "乐园_钥匙数")


def _click(ctrl, x, y, delay=1.0):
    ctrl.post_click(int(x), int(y)).wait()
    time.sleep(delay)


def _shot(ctrl):
    """模拟器截图：轮询等待完成（15 秒/次），重试 3 次。
    连续失败后重连控制器——MaaFW 截图 job 队列在模拟器卡顿时会堵塞，
    旧 job 滞留会让后续每次截图都超时（重连重建连接清空队列）。"""
    for attempt in range(3):
        try:
            job = ctrl.post_screencap()
            for _w in range(15):
                if job.done:
                    img = job.get()
                    if img is not None and getattr(img, "size", 0) > 0:
                        return img
                    break
                time.sleep(1.0)
            _log(f"截图超时/失败（第 {attempt + 1} 次）")
            if attempt >= 1:
                try:
                    ctrl.post_connection().wait()
                    _log("控制器已重连（清截图队列）")
                except Exception as e2:
                    _log(f"重连异常: {e2}")
        except Exception as e:
            _log(f"截图异常: {e}")
        time.sleep(1.0)
    return None


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
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        idle_rounds = 0

        for _round in range(3000):  # 安全上限，正常远达不到
            img = shot()
            if img is None:
                _log("连续截图失败，跳过本轮")
                time.sleep(2.0)
                continue

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
                if _hit(context, img, "乐园_选择奖励弹窗") is not None and _hit(context, img, "乐园_选择奖励弹窗").hit:
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
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

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

            # 回主界面：先确认不在主界面再点返回位（主界面左上角是头像，点了会开个人信息页）
            for _ in range(4):
                img = shot()
                main_ui = _hit(context, img, "日常_主界面确认")
                if main_ui is not None and main_ui.hit:
                    break
                click(*BACK_POS)
                time.sleep(1.5)

            main_ui = _hit(context, img, "日常_主界面确认")
            if main_ui is None or not main_ui.hit:
                # 还没到主界面，用家园图标兜底
                home = _hit(context, img, "乐园_家园图标")
                if home is not None and home.hit:
                    center = _box_center(home)
                    click(*center, delay=3.0)
                else:
                    # 3.7.4 主界面右上角 (1213,33) 改成了房间入口——不再盲点，
                    # 未识别到家园图标时交给返回位循环继续（防误入房间系统）
                    pass

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


# ---------- 剧情推图战斗循环（觉醒/出场技任务分支） ----------

STORY_BATTLE_ROUNDS = 70   # 5s/轮 × 70 ≈ 6 分钟（300 秒限时 + 余量）
STORY_AWAKEN_BTN = (1050, 367)  # 觉醒技能按钮（全角色同位置，游戏坐标）


@AgentServer.custom_action("剧情战斗循环")
class StoryFight(CustomAction):
    """剧情推图战斗内循环（10 秒检测 + 按挑战目标分支技能操作）。

    param.type:
      awaken - 挑战目标含「觉醒技能」：识别金色发光觉醒按钮，每约 5 秒点击一次
      switch - 挑战目标含「出场技」：右上角色头像检测 + 出场技点击 + 角色切换（待补细节）
      none   - 无特殊技能目标：纯等待战斗结束
    """

    def run(self, context, argv) -> bool:
        param = {}
        try:
            param = json.loads(argv.custom_action_param or "{}")
        except Exception:
            pass
        task_type = param.get("type", "none")
        _log(f"剧情战斗循环启动（type={task_type}）")
        ctrl = context.tasker.controller
        if ctrl is None:
            return False

        battle_rounds = 0
        while battle_rounds < STORY_BATTLE_ROUNDS:
            battle_rounds += 1
            img = _shot(ctrl)
            if img is None:
                time.sleep(2.0)
                continue

            # 玩家信息弹窗（误点助战头像）→ 取消
            pi = _hit(context, img, "剧情_玩家信息取消")
            if pi is not None and pi.hit:
                _log("误触玩家信息弹窗，点取消")
                _click(ctrl, 858, 538, 2.0)
                continue

            # 结算页 → 推进
            end = _hit(context, img, "剧情_结算继续")
            if end is not None and end.hit:
                _log("战斗结束，推进结算")
                _click(ctrl, 1150, 710, 4.0)
                _click(ctrl, 1150, 710, 4.0)
                break

            # 战斗前对话 → 跳过
            skip = _hit(context, img, "剧情_战斗前对话跳过")
            if skip is not None and skip.hit:
                c = _box_center(skip)
                _click(ctrl, *c, 1.5)
                continue

            # 技能操作（按任务类型）
            if task_type == "awaken":
                aw = _hit(context, img, "战斗_觉醒技能")
                if aw is not None and aw.hit:
                    c = _box_center(aw)
                    _log("点觉醒技能")
                    _click(ctrl, *c, 5.0)  # 点完等 5 秒再识别
                    continue
            elif task_type == "switch":
                # 出场技 = 切换角色时自动触发：交替点右上两个角色头像
                # 切角色 10 秒冷却单独计算，battle 循环 10s/轮刚好匹配冷却节奏
                head = (1165, 140) if battle_rounds % 2 == 0 else (1165, 255)
                _log("切换出场角色触发出场技")
                _click(ctrl, *head, 1.0)

            time.sleep(3)
        else:
            _log("剧情战斗超时（超过 6 分钟），异常结束")
            return False

        _log("剧情战斗结束")
        return True


# ---------- 竞技场（青铜战场） ----------

ARENA_BATTLE_ROUNDS = 130  # 单把战斗内层轮次上限（10s/轮 ≈ 21 分钟），超出按异常处理
AUTO_BUTTON = (445, 45)    # 战斗内「自动」按钮（点两次切 2 倍速）
ARENA_MATCH_BTN = (1200, 665)   # 右下「匹配」按钮中心（2026-11-23 实测，原 1178,710 点在按钮外）
ARENA_REWARD_BTN = (200, 690)
ARENA_POPUP_CLOSE = (55, 35)      # 关提示/关弹窗通用返回位
ARENA_REWARD_CLOSE_X = (1022, 155)  # 作战奖励弹窗 X


@AgentServer.custom_action("竞技场战斗循环")
class ArenaFight(CustomAction):
    """竞技场全流程 agent：匹配 → 倍速X2 → 等待战斗 → 结算 → 领作战奖励 → 计数循环。

    断点续打：今日已打场数记录在 config/arena_config.json（跨天自动重置），
    闪退重启后从记录的次数继续，绝不超出网页配置的每日场数（0 = 不打）。
    活动未开放（「关卡未开启」提示）时安全返回。
    """

    def run(self, context, argv) -> bool:
        cfg = load_arena_config()
        if cfg["done_today"] >= cfg["battles_per_day"]:
            _log(f"今日竞技场已完成 ({cfg['done_today']}/{cfg['battles_per_day']})，跳过")
            return True
        _log(f"竞技场目标 {cfg['battles_per_day']} 把，已打 {cfg['done_today']} 把")
        ctrl = context.tasker.controller
        if ctrl is None:
            return False

        def click(x, y, delay=1.2):
            ctrl.post_click(int(x), int(y)).wait()
            time.sleep(delay)

        def shot():
            return _shot(ctrl)

        while cfg["done_today"] < cfg["battles_per_day"]:
            # 确保在竞技场界面（含闪退恢复）
            if not self._goto_arena(context, ctrl):
                _log("无法回到竞技场界面，结束")
                return False

            # 点「匹配」（用 OCR box 中心，常量坐标曾点在按钮边缘外）
            img = shot()
            match_btn = _hit(context, img, "竞技场_匹配按钮")
            if match_btn is None or not match_btn.hit:
                t = _hit(context, img, "竞技场_界面标题")
                t_text = _dtext(t) if t else "<识别未启动>"
                t_box = getattr(t, "box", None) if t else None
                _log(f"竞技场界面未找到匹配按钮（标题识别: {t_text!r} box={t_box}）")
                click(*BACK_POS, delay=2.0)
                return True
            mc = _box_center(match_btn)
            click(*mc, delay=2.5)

            # 匹配是否生效（竞技场有开放时段，非开放时段点击无任何反应，
            # 曾在凌晨实测按钮点击无效）→ 3 次尝试无变化则安全跳过
            entered = False
            for _ in range(3):
                img = shot()
                ms = _hit(context, img, "竞技场_匹配成功")
                if ms is not None and ms.hit:
                    entered = True
                    break
                t = _hit(context, img, "竞技场_界面标题")
                if t is None or not t.hit:
                    # 画面已离开竞技场 → 匹配生效（进入匹配/战斗流程）
                    entered = True
                    break
                click(*mc, delay=4.0)   # 无变化，再试一次
            if not entered:
                _log("匹配按钮无响应（竞技场可能未到开放时段），跳过")
                return True

            img = shot()
            if _hit(context, img, "竞技场_未开启提示") is not None and _hit(context, img, "竞技场_未开启提示").hit:
                not_open = _hit(context, img, "竞技场_未开启提示")
                if not_open is not None and not_open.hit:
                    _log("竞技场未开放（关卡未开启提示），跳过")
                    click(*ARENA_POPUP_CLOSE, delay=2.0)
                    return True

            # 匹配成功弹窗 → 等待进入战斗
            for _ in range(8):
                img = shot()
                if _hit(context, img, "竞技场_匹配成功") is not None and _hit(context, img, "竞技场_匹配成功").hit:
                    _log("匹配成功，等待进入战斗")
                    time.sleep(6)
                    break
                time.sleep(2)

            # 战斗内循环：倍速 X2 → 每 10 秒检测一次 → 等待战斗结束
            speed_clicked = False
            battle_rounds = 0
            while battle_rounds < ARENA_BATTLE_ROUNDS:
                battle_rounds += 1
                img = shot()
                if img is None:
                    time.sleep(2.0)
                    continue

                # 玩家信息弹窗（误点助战头像弹出）→ 点「取消」不添加好友
                player_info = _hit(context, img, "竞技场_玩家信息弹窗")
                if player_info is not None and player_info.hit:
                    _log("误触玩家信息弹窗，点取消")
                    click(858, 538, delay=2.0)
                    continue

                # 结算页：「战斗结束」艺术字 or 右下「点击屏幕继续」→ 点右下文字区推进（避开助战头像）
                end_page = _hit(context, img, "竞技场_战斗结束")
                cont_btn = _hit(context, img, "竞技场_继续按钮")
                if (end_page is not None and end_page.hit) or (cont_btn is not None and cont_btn.hit):
                    _log("战斗结束，点屏幕推进结算")
                    click(1150, 710, delay=4.0)
                    # 第二下前确认是否已回竞技场：匹配按钮与结算点击位重叠
                    # (匹配 box 1106-1202,664-714 含 1150,710)，盲点第二下会误开下一场
                    img2 = shot()
                    if img2 is None or _hit(context, img2, "竞技场_界面标题") is None                             or not _hit(context, img2, "竞技场_界面标题").hit:
                        click(1150, 710, delay=4.0)
                    break

                # 战斗进行中：首轮点两次「自动」切 2 倍速
                battle_ui = _hit(context, img, "竞技场_战斗待机")
                if battle_ui is not None and battle_ui.hit:
                    if not speed_clicked:
                        _log("战斗中：点两次自动按钮切 2 倍速")
                        click(*AUTO_BUTTON, delay=1.2)
                        click(*AUTO_BUTTON, delay=1.2)
                        speed_clicked = True
                    time.sleep(10)
                    continue

                # 战斗前剧情对话（偶发）→ 跳过
                if _hit(context, img, "剧情_战斗前对话跳过") is not None:
                    skip = _hit(context, img, "剧情_战斗前对话跳过")
                    if skip is not None and skip.hit:
                        center = _box_center(skip)
                        click(*center, delay=1.5)
                    continue

                time.sleep(10)
            else:
                _log("单把战斗超时（超过 20 分钟），异常结束")
                return False

            # 回到竞技场界面 → 领取左下「奖励」→ 计数
            done = self._claim_and_count(context, ctrl)
            if done is None:
                return False
            cfg["done_today"] = done
            save_arena_config(cfg)
            _log(f"竞技场进度: {done}/{cfg['battles_per_day']}")

        _log("今日竞技场次数全部完成")
        return True

    # ---- 竞技场界面 → 领作战奖励 → 返回竞技场界面，返回今日已打数 ----

    def _claim_and_count(self, context, ctrl):
        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        def wait_flop(entry, rounds=8):
            """等某个识别命中（弹窗/界面切换），返回最终识别结果"""
            for _ in range(rounds):
                img = shot()
                d = _hit(context, img, entry)
                if d is not None and d.hit:
                    return d
                time.sleep(1.5)
            return None

        # 确认回到竞技场界面
        back_to_arena = False
        for _ in range(6):
            img = shot()
            if _hit(context, img, "竞技场_界面标题") is not None and _hit(context, img, "竞技场_界面标题").hit:
                back_to_arena = True
                break
            click(500, 300, delay=2.0)  # 结算页残余点击
        if not back_to_arena:
            _log("战斗后未回到竞技场界面")
            return None

        # 点左下「奖励」打开作战奖励弹窗
        click(*ARENA_REWARD_BTN, delay=2.5)
        reward_popup = wait_flop("竞技场_作战奖励弹窗")
        if reward_popup is None:
            _log("作战奖励弹窗未出现（可能已全部领取）")
            click(*BACK_POS, delay=2.0)  # 保险：点返回位回竞技场界面
            return self._done_value(context, ctrl)

        # 逐个点「领取」（最多 6 个）：领取后列表会重排，每轮重新 OCR 定位
        # 领取后轮询等待「获得物品」弹窗出现再关闭（固定 sleep 会错过时机导致漏领）
        claimed = 0
        for _ in range(6):
            img = shot()
            if img is None:
                time.sleep(2.0)
                continue
            claim = _hit(context, img, "竞技场_领取按钮")
            if claim is None or not claim.hit:
                break
            center = _box_center(claim)
            click(*center, delay=1.0)
            claimed += 1
            # 轮询等待「获得物品」弹窗（最多 8 秒），出现后关掉
            for _w in range(6):
                time.sleep(1.3)
                wimg = shot()
                if wimg is None:
                    continue
                got = _hit(context, wimg, "乐园_获得物品弹窗")
                if got is not None and got.hit:
                    break
            click(*ARENA_POPUP_CLOSE, delay=2.0)
        _log(f"竞技场作战奖励领取 {claimed} 项")

        # 关闭作战奖励弹窗（X）
        click(*ARENA_REWARD_CLOSE_X, delay=2.0)
        return self._done_value(context, ctrl)

    def _done_value(self, context, ctrl):
        cfg = load_arena_config()
        # 竞技场界面下计数 +1（每把打完在此计数）
        cfg["done_today"] = int(cfg.get("done_today", 0)) + 1
        save_arena_config(cfg)
        return cfg["done_today"]

    # ---- 导航：确保回到竞技场界面 ----

    def _goto_arena(self, context, ctrl):
        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        for attempt in range(3):
            img = shot()
            if _hit(context, img, "竞技场_界面标题") is not None and _hit(context, img, "竞技场_界面标题").hit:
                return True

            # 闪退恢复：跑「游戏启动」拉起游戏
            try:
                context.run_action("游戏启动")
                time.sleep(3)
            except Exception as e:
                _log(f"游戏启动节点异常: {e}")

            # 公告面板关闭
            notice = _hit(context, img, "启动_公告面板关闭")
            if notice is not None and notice.hit:
                click(1128, 92, delay=2.0)
                continue

            # 回主界面：先确认不在主界面再点返回位（防点头像）
            for _ in range(4):
                img = shot()
                main_ui = _hit(context, img, "日常_主界面确认")
                if main_ui is not None and main_ui.hit:
                    break
                click(*BACK_POS)
                time.sleep(1.5)
            if _hit(context, shot(), "日常_主界面确认") is None:
                home = _hit(context, shot(), "乐园_家园图标")
                if home is not None and home.hit:
                    c = _box_center(home)
                    click(*c, delay=3.0)
                else:
                    # 3.7.4 主界面右上角 (1213,33) 改成了房间入口——不再盲点，
                    # 未识别到家园图标时交给返回位循环继续（防误入房间系统）
                    pass

            # 主界面 → 作战中心·活动页签 → 竞技场卡片（2026-11-23 迁移）
            img = shot()
            if _hit(context, img, "日常_主界面确认") is None:
                _log("导航：仍未到主界面，重试")
                continue
            _log("导航：已在主界面，走活动页签进竞技场")
            if not _navigate_activity(context, ctrl, "活动_竞技场卡片", "竞技场"):
                _log("导航：活动页签未进入竞技场，重试")
                continue
            time.sleep(2)

            img = shot()
            if _hit(context, img, "竞技场_界面标题") is not None and _hit(context, img, "竞技场_界面标题").hit:
                _log("导航：已到达竞技场界面")
                return True
            _log("导航：点击竞技场卡片后未识别到竞技场界面")
        return False


# ---------- 清日常调度（BAAH 式任务顺序配置） ----------

# 子流程显示名 → pipeline 入口节点名
DAILY_STEPS = {
    # 值为「子流程组合节点」（JumpBack 平铺该子流程全部节点，
    # 入口节点 next 为空不能直接用——run_task 只会点一下入口就结束）
    "签到": "子流程_签到",
    "补给站": "子流程_补给站",
    "邮件": "子流程_邮件",
    "联络": "子流程_联络",
    "社团": "子流程_社团",
    "研发中心": "子流程_研发中心",
    "次元入侵": "子流程_次元入侵",
    "追猎计划": "子流程_追猎计划",
    "特训矩阵": "子流程_特训矩阵",
    "竞技场": "子流程_竞技场",
    "精灵试炼": "子流程_精灵试炼",
    "世界之眼": "子流程_世界之眼",
    "魔女日程": "子流程_魔女日程",
    "每日副本": "子流程_每日副本",
    "剧情推图": "剧情推图循环",
}

DEFAULT_DAILY_ORDER = [
    "签到", "补给站", "邮件", "联络", "社团", "次元入侵",
    "追猎计划", "特训矩阵", "竞技场", "世界之眼", "魔女日程",
]


def load_daily_config():
    """读取清日常配置：order（执行顺序）+ 各子流程参数"""
    cfg = {
        "order": list(DEFAULT_DAILY_ORDER),
        "park_mode": "hard",
        "park_max_stages": 3,
        "story_volume": 6,
        "stage_name": "经验大作战",
        "level": "深渊",
        "times": 1,
    }
    try:
        with open(_DAILY_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        order = [s for s in data.get("order", []) if s in DAILY_STEPS]
        if order:
            cfg["order"] = order
        cfg["park_mode"] = data.get("park_mode", "hard")
        cfg["park_max_stages"] = max(1, min(30, int(data.get("park_max_stages", 3))))
        cfg["story_volume"] = max(1, min(22, int(data.get("story_volume", 6))))
        cfg["stage_name"] = str(data.get("stage_name", cfg["stage_name"]))
        cfg["level"] = str(data.get("level", cfg["level"]))
        cfg["times"] = max(1, min(9, int(data.get("times", 1))))
    except Exception:
        pass
    return cfg


def load_daily_order():
    """读取日常执行顺序（兼容旧接口）"""
    return load_daily_config()["order"]


@AgentServer.custom_action("日常调度循环")
class DailyScheduler(CustomAction):
    """按网页配置的顺序依次执行各日常子流程。

    每个子流程是独立的 pipeline 任务（入口节点扫完无匹配即结束），
    子流程之间确保回到主界面；游戏闪退时自动重启并恢复导航。
    """

    def run(self, context, argv) -> bool:
        order = load_daily_order()
        _log(f"今日日常顺序: {' → '.join(order)}")
        ctrl = context.tasker.controller
        if ctrl is None:
            return False

        for step in order:
            entry = DAILY_STEPS.get(step)
            if entry is None:
                continue

            # 每步前确保回到主界面（含闪退恢复）
            if not self._ensure_main(context, ctrl):
                _log("无法回到主界面，调度中止")
                return False

            _log(f"执行日常：{step}")

            # 剧情推图子流程：从 daily_config 读模式/关数作为参数传入
            override = None
            if step == "剧情推图":
                dcfg = load_daily_config()
                override = {
                    "剧情推图循环": {
                        "custom_action_param": {
                            "mode": dcfg["park_mode"],
                            "max_stages": dcfg["park_max_stages"],
                            "volume": dcfg["story_volume"],
                        }
                    }
                }

            try:
                context.run_task(entry, pipeline_override=override)
                time.sleep(2.0)
            except Exception as e:
                _log(f"子流程 {step} 异常: {e}")

        _log("全部日常子流程执行完毕")
        return True

    def _ensure_main(self, context, ctrl):
        """确保回到主界面（委托模块级实现）"""
        return _ensure_main_screen(context, ctrl)

    def _ensure_main_old(self, context, ctrl):

        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        for attempt in range(3):
            img = shot()
            main_ui = _hit(context, img, "日常_主界面确认")
            if main_ui is not None and main_ui.hit:
                return True

            # 游戏可能闪退：跑「游戏启动」拉起
            try:
                context.run_action("游戏启动")
                time.sleep(3)
            except Exception as e:
                _log(f"游戏启动异常: {e}")

            # 公告面板关闭
            img = shot()
            notice = _hit(context, img, "启动_公告面板关闭")
            if notice is not None and notice.hit:
                click(1128, 92, delay=2.0)
                continue

            # 连点返回位回主界面（先确认不在主界面防点头像）
            for _ in range(4):
                img = shot()
                main_ui = _hit(context, img, "日常_主界面确认")
                if main_ui is not None and main_ui.hit:
                    return True
                click(*BACK_POS)

            # 家园图标兜底
            home = _hit(context, shot(), "乐园_家园图标")
            if home is not None and home.hit:
                c = _box_center(home)
                click(*c, delay=3.0)
            else:
                click(1213, 33, delay=3.0)

        return _hit(context, shot(), "日常_主界面确认") is not None


def _ensure_main_screen(context, ctrl):
    """模块级主界面保障（各 agent 共用）：游戏启动 + 返回位 + 家园兜底"""
    for attempt in range(3):
        img = _shot(ctrl)
        main_ui = _hit(context, img, "日常_主界面确认")
        if main_ui is not None and main_ui.hit:
            return True

        try:
            context.run_action("游戏启动")
            time.sleep(3)
        except Exception as e:
            _log(f"游戏启动异常: {e}")

        img = _shot(ctrl)
        notice = _hit(context, img, "启动_公告面板关闭")
        if notice is not None and notice.hit:
            _click(ctrl, 1128, 92, 2.0)
            continue

        for _ in range(4):
            img = _shot(ctrl)
            main_ui = _hit(context, img, "日常_主界面确认")
            if main_ui is not None and main_ui.hit:
                return True
            _click(ctrl, *BACK_POS, 1.5)

        home = _hit(context, _shot(ctrl), "乐园_家园图标")
        if home is not None and home.hit:
            c = _box_center(home)
            _click(ctrl, *c, 3.0)
        # 未识别到家园图标时不盲点右上角（3.7.4 该位置是房间入口，防误入）

    return _hit(context, _shot(ctrl), "日常_主界面确认") is not None


# ---------- 剧情推图循环（困难/剧情模式推图 agent） ----------

STORY_FARM_ROUNDS = 40      # 地图页轮次上限（防意外死循环）
BATTLE_ROUNDS_LIMIT = 70    # 单关战斗内轮次上限（5s/轮 ≈ 6 分钟，300 秒限时+余量）
MODE_BUTTON_ROI = [1060, 95, 220, 60]


def _navigate_activity(context, ctrl, card_entry, card_name, partial_names=None):
    """主界面之后：作战中心 → 活动页签 → 定位玩法卡片并进入。

    卡片交互规律（2026-11-23 实测）：点非居中卡片 = 列表滚动把它移到居中；
    点居中卡片 = 进入该玩法页；列表记住上次浏览位置，大距离滑动会回弹到端点。
    partial_names: 目标卡片被屏幕边缘裁切时只露出的部分名（如「精灵试炼」在左
    边缘只露「精灵」二字）——命中部分名即点击使其居中，居中后用 card_entry
    精确验证全名再进入，防止部分名误中其他玩法（如「精灵特训」）。
    由调用方保证当前在主界面。返回 True = 已点击居中卡片（进入动作已发出）。
    """
    def shot():
        return _shot(ctrl)

    try:
        context.run_action("剧情_从主界面进入")
        time.sleep(2)
    except Exception as e:
        _log(f"进作战中心异常: {e}")
        return False

    for attempt in range(3):
        _click(ctrl, 60, 345, 3.0)   # 左栏「活动」页签（高亮区实测 y 294-396）
        img = shot()
        t = _hit(context, img, "活动_页签选中")
        if t is not None and t.hit:
            _log("已进入作战·活动页")
            break
    else:
        _log("3 次尝试未进入活动页签")
        return False

    for attempt in range(8):
        img = shot()
        card = context.run_recognition(card_entry, img)
        target = None
        if card is not None and card.hit:
            target = _box_center(card)
        elif partial_names:
            # 精确名未命中 → 扫卡片名 OCR 结果找部分名（边缘裁切态）
            broad = context.run_recognition("活动_卡片名称", img)
            if broad is not None:
                for r in (broad.all_results or []):
                    txt = getattr(r, "text", "") or ""
                    b = getattr(r, "box", None)
                    if b and any(p in txt for p in partial_names):
                        cx = b[0] + b[2] / 2
                        if not (540 <= cx <= 850):
                            target = (cx, b[1] + b[3] / 2)
                        break
        if target is not None:
            if 540 <= target[0] <= 850:
                # 已居中 → 精确验证全名后进入（防部分名误中其他玩法）
                v = context.run_recognition(card_entry, img)
                if v is not None and v.hit:
                    _log(f"点击居中卡片进入 {card_name}")
                    _click(ctrl, target[0], target[1], 3.5)
                    return True
                _log(f"居中卡片非 {card_name}（部分名误中），滑动跳过")
            else:
                _log(f"{card_name} 部分可见（x={target[0]}），点击使其居中")
                _click(ctrl, target[0], target[1], 2.8)
                time.sleep(2.5)
                continue
        _log(f"{card_name} 不在可见区（attempt {attempt}），滑动")
        if attempt % 2 == 0:
            ctrl.post_swipe(1000, 360, 300, 360, 800).wait()   # 左滑看右侧
        else:
            ctrl.post_swipe(300, 360, 1000, 360, 800).wait()   # 右滑看左侧
        time.sleep(2.0)
    return False


def _stars_gold(context, img, box):
    """判断地图关卡节点是否为金色三星（已通关）：采样关卡号下方星星区金色占比"""
    try:
        x, y, w, h = int(box[0]), int(box[1]), int(box[2]), int(box[3])
        y0 = y + h - 5
        x0 = max(0, x - 15)
        x1 = min(img.shape[1], x + w + 15)
        y1 = min(img.shape[0], y0 + 55)
        region = img[y0:y1, x0:x1]
        if region.size == 0:
            return False
        mask = (region[:, :, 0] > 190) & (region[:, :, 1] > 140) & (region[:, :, 2] < 140)
        return float(mask.mean()) > 0.08
    except Exception:
        return False


@AgentServer.custom_action("剧情推图循环")
class StoryFarmAgent(CustomAction):
    """剧情推图全流程 agent：
    主界面 → 作战中心 → 剧情 → 章节卡片（当前进度卷）→ 确认/切换难度模式
    → 地图推图循环：OCR 关卡号定位节点 → 星色判断（金三星已通跳过、黑星未通开打）
      → 详情扫荡判断双保险 → 作战准备 → 编队 → 出击 → 战斗（结算/弹窗/倍速）→ 回地图
    → 全部通关/锁定/体力不足 → 左下宝箱领取 → 返回主界面。
    模式与关数由 param（mode/max_stages）控制，清日常任务经 interface option 传入。
    """

    def run(self, context, argv) -> bool:
        param = {}
        try:
            param = json.loads(argv.custom_action_param or "{}")
        except Exception:
            pass
        mode = param.get("mode", "hard")          # story / hard / hell
        max_stages = int(param.get("max_stages", 3))
        volume = param.get("volume")
        if volume is None:
            volume = load_daily_config()["story_volume"]
        volume = max(1, min(22, int(volume)))
        _log(f"剧情推图：目标第 {volume} 卷 · {mode} 模式 · 最多 {max_stages} 关")
        ctrl = context.tasker.controller
        if ctrl is None:
            return False

        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        # 1. 主界面保障 → 作战中心 → 剧情（章节列表）
        if not _ensure_main_screen(context, ctrl):
            return False
        try:
            context.run_action("剧情_从主界面进入")
            time.sleep(2)
        except Exception as e:
            _log(f"进作战中心异常: {e}")
            return False

        # 2. 作战中心切到「剧情」页签（作战中心会记住上次停留页签）
        #    确认方式：剧情页签选中态模板（标题 OCR 对艺术字不可靠）
        at_story = False
        for _ in range(3):
            img = shot()
            if img is not None:
                d = _hit(context, img, "剧情_页签选中")
                t = _hit(context, img, "作战中心标题")
                # 双条件：页签选中态 + 顶部「作战」标题
                # （3.7.4 房间/其它界面的粉红元素会单独误命中页签模板）
                if d is not None and d.hit and t is not None and t.hit:
                    at_story = True
                    break
            click(60, 125, 2.5)   # 左栏「剧情」页签
        if not at_story:
            _log("未进入剧情页签")
            return False
        _log("已在作战·剧情页")

        # 3. 按卷号定位（位置计数法）→ 点击居中卡片进入地图
        if not self._goto_volume(context, ctrl, volume):
            return False
        click(690, 360, 5.0)
        in_map = False
        for _ in range(3):
            img = shot()
            if img is not None and _hit(context, img, "推图_地图标题") is not None                     and _hit(context, img, "推图_地图标题").hit:
                in_map = True
                break
            time.sleep(1.5)
        if not in_map:
            _log("点击章节卡片后未进入地图页")
            return False
        _log("已进入章节地图")

        # 3. 难度模式确认/切换
        if not self._switch_mode(context, ctrl, mode):
            _log(f"切换到 {mode} 模式失败")
            return False

        # 4. 推图循环
        stages_done = 0
        map_rounds = 0
        while stages_done < max_stages and map_rounds < STORY_FARM_ROUNDS:
            map_rounds += 1
            img = shot()
            if img is None:
                time.sleep(2.0)
                continue

            # 通用瞬态处理（结算/弹窗/跳过/战斗中）
            handled = self._handle_transient(context, ctrl, img)
            if handled:
                continue

            # 非地图页（未知界面）→ 逃跑恢复
            map_page = _hit(context, img, "推图_地图标题")
            if map_page is None or not map_page.hit:
                _log("未知界面，尝试回主界面重导航")
                if not _ensure_main_screen(context, ctrl):
                    return False
                try:
                    context.run_action("剧情_从主界面进入")
                    time.sleep(2)
                    context.run_action("剧情_选章节卡片")
                    time.sleep(2)
                    if not self._switch_mode(context, ctrl, mode):
                        return False
                except Exception as e:
                    _log(f"重导航异常: {e}")
                continue

            # 地图页：黑星模板定位未通关节点（黑星三连=未通关标记；
            # 金三星=已通关颜色不同不会匹配；filtered 按 Horizontal 从左到右）
            target = None
            star = _hit(context, img, "推图_黑星")
            if star is not None and star.hit:
                fr = getattr(star, "filtered_results", None) or []
                if fr:
                    b = list(fr[0].box)
                    target = {"box": b, "text": f"黑星@{b[0]},{b[1]}"}

            if target is None:
                _log("可见区无黑星节点，尝试滚动地图")
                ctrl.post_swipe(1000, 400, 400, 400, 500).wait()
                time.sleep(2.0)
                img2 = _shot(ctrl)
                star2 = _hit(context, img2, "推图_黑星") if img2 is not None else None
                fr2 = getattr(star2, "filtered_results", None) or [] if (star2 is not None and star2.hit) else []
                if not fr2:
                    _log("本卷推图完成（无未通关节点），领取宝箱后返回")
                    return self._finish(context, ctrl)
                b = list(fr2[0].box)
                target = {"box": b, "text": f"黑星@{b[0]},{b[1]}"}

            # 点击黑星上方约 80px 的关卡节点图标
            bx = target["box"]
            node_x, node_y = int(bx[0] + bx[2] / 2), int(bx[1] - 80)
            _log(f"点击关卡节点 {target['text']} @ ({node_x},{node_y})")
            click(node_x, node_y, 2.0)

            # 详情弹窗（作战准备出现 = 可打；未出现 = 锁定节点）
            img = shot()
            prep = _hit(context, img, "剧情_作战准备")
            if prep is None or not prep.hit:
                _log("节点无详情（锁定或不可打），尝试宝箱后返回")
                return self._finish(context, ctrl)

            # 扫荡按钮颜色双保险：灰 = 未通（可打）；亮粉 = 已通（跳过）
            sweep_bright = self._sweep_bright(img)
            if sweep_bright:
                _log("详情显示已通关（扫荡可亮），跳过该节点")
                click(1022, 155, 2.0)  # X 关详情
                continue

            # 作战准备 → 编队
            click(975, 558, 4.0)
            # 编队页：等出击按钮并点击（此前缺失此步——一直卡在编队页超时）
            atk = wait_hit(context, ctrl, "剧情_出击", 10)
            if atk is None:
                _log("编队页未出现出击按钮（队伍不足/弹窗阻挡？）")
                click(*BACK_POS, 2.0)
                continue
            click(*_box_center(atk), 8.0)
            # 战斗前对话跳过（偶发）
            skip = _hit(context, shot(), "剧情_战斗前对话跳过")
            if skip is not None and skip.hit:
                click(*_box_center(skip), 1.5)
            battle_ok = self._battle_until_map(context, ctrl, max_rounds=BATTLE_ROUNDS_LIMIT)
            if battle_ok is None:
                return False  # 致命异常
            stages_done += 1
            _log(f"第 {stages_done} 关流程完成")

            # 体力不足提前结束
            img = shot()
            stam = _ocr_int(context, img, "推图_体力")
            if 0 <= stam < 12:
                _log(f"体力不足（{stam}/132 < 12），停止推图")
                return self._finish(context, ctrl)

        _log(f"推图关数达到上限（{max_stages}），收尾返回")
        return self._finish(context, ctrl)

    # ---- 章节卡片遍历：OCR 居中卡片星数，找目标模式未满星章节并点击进入 ----

    def _goto_volume(self, context, ctrl, target):
        """按卷号定位章节（位置计数法，实测校准 11-26）：
        列表有序卷1→卷22，点击左/tap 右邻卡片（440/940,360）= 精确滚 1 卷。
        先连点左邻到列表头（点无效=到头），再向右点 target-1 次。
        滚动检测：卡片区 48x48 灰度均值图前后绝对差 >5 = 滚动了
        （实测滚动≈48、静止≤1.4，区分度极高，不依赖标题/星数 OCR 内容）。
        """

        def shot():
            return _shot(ctrl)

        def sig(img):
            import numpy as _np
            a = img[160:620, 340:1040, :3].astype("float32").mean(axis=2)
            h, w = a.shape
            ys = _np.linspace(0, h - 1, 48).astype(int)
            xs = _np.linspace(0, w - 1, 48).astype(int)
            return a[_np.ix_(ys, xs)]

        def moved(img_a, img_b):
            import numpy as _np
            return float(_np.abs(sig(img_b) - sig(img_a)).mean()) > 5.0

        # 阶段1：连点左邻 → 列表头（卷1）
        stale = 0
        for _ in range(28):
            img = shot()
            if img is None:
                time.sleep(2.0)
                continue
            _click(ctrl, 440, 360, 1.8)
            img2 = shot()
            if img2 is None:
                continue
            if moved(img, img2):
                stale = 0
            else:
                stale += 1
                if stale >= 2:
                    break
        _log("章节列表已到最左端（卷1）")

        # 阶段2：向右移动 target-1 卷
        reached = 0
        for _ in range(max(0, target - 1)):
            img = shot()
            if img is None:
                time.sleep(2.0)
                continue
            _click(ctrl, 940, 360, 1.8)
            img2 = shot()
            if img2 is None:
                continue
            if moved(img, img2):
                reached += 1
            else:
                _log(f"向右第 {reached + 1} 步到头（目标第 {target} 卷超出已开放范围，"
                     f"最多到第 {reached + 1} 卷）")
                return False
        _log(f"已定位到第 {target} 卷（共向右移动 {reached} 步）")
        return True

    def _title_text(self, context, img):
        """读居中的章节卡片标题（「卷X·名称」），用于检测列表滚动是否生效"""
        d = _hit(context, img, "推图_章节标题")
        if d is not None and d.hit:
            return _dtext(d)
        return ""

    def _parse_modes_boxes(self, all_results):
        """按 OCR box 的 y 坐标把「剧情/困难/地狱」行名与「N/M」数字就近配对。
        实测 OCR 输出示例：{text:"★刷情",box:[631,426,88,32]}（replace 后为剧情）、
        {text:"困难"}, {text:"地狱"}, {text:"21/21"}, {text:"30/30"}, {text:"18/18"}。
        返回 {剧情: True(未满), 困难: ..., 地狱: ...}（数字行缺失的键不出现）"""
        labels = {}
        values = []
        for r in (all_results or []):
            t = getattr(r, "text", "") or ""
            b = getattr(r, "box", None)
            if not b:
                continue
            cy = b[1] + b[3] / 2.0
            for key in ("剧情", "困难", "地狱"):
                if key in t:
                    labels[key] = cy
            m = re.search(r"(\d+)\s*/\s*(\d+)", t)
            if m:
                values.append((cy, int(m.group(1)), int(m.group(2))))
        result = {}
        for key, ly in labels.items():
            if not values:
                continue
            near = min(values, key=lambda v: abs(v[0] - ly))
            if abs(near[0] - ly) < 60:   # 同一行的数字应在行名下方/同高附近
                result[key] = near[1] < near[2]
        return result

    def _parse_card_modes(self, text):
        """解析章节卡片文本「剧情 34/99 困难 0/75 地狱 0/45」→ {剧情: True(未满), ...}"""
        result = {}
        if not text:
            return result
        for key in ("剧情", "困难", "地狱"):
            m = re.search(key + r"\s*(\d+)\s*/\s*(\d+)", text)
            if m:
                result[key] = int(m.group(1)) < int(m.group(2))
        return result

    def _switch_mode(self, context, ctrl, mode):
        """确认副本页右上角当前模式，不符则经下拉菜单切换"""
        target_text = {"story": "剧情模式", "hard": "困难模式", "hell": "地狱模式"}.get(mode, "困难模式")

        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        # 进图瞬间标题区未渲染，先等模式按钮文字出现（最多 12 秒）
        cur_text = ""
        for _wait in range(8):
            img = shot()
            cur = _hit(context, img, "推图_模式按钮")
            cur_text = _dtext(cur) if cur is not None else ""
            if cur_text:
                break
            time.sleep(1.5)
        if target_text in cur_text:
            _log(f"已在 {mode} 模式")
            return True

        for attempt in range(4):
            click(1145, 115, 2.0)   # 点右上模式按钮弹下拉菜单
            menu_item = _hit(context, shot(), f"推图_{target_text}菜单")
            if menu_item is not None and menu_item.hit:
                c = _box_center(menu_item)
                click(*c, 3.0)
                # 切换后确认（切换动画期间重试）
                for _confirm in range(4):
                    img = shot()
                    cur = _hit(context, img, "推图_模式按钮")
                    cur_text = _dtext(cur) if cur is not None else ""
                    if target_text in cur_text:
                        _log(f"已切换到 {mode} 模式")
                        return True
                    time.sleep(1.5)
            time.sleep(1.0)
        _log(f"模式切换失败（目标 {target_text}，当前 {cur_text!r}）")
        return False

    # ---- 瞬态界面处理（结算/玩家信息/跳过/战斗中） ----

    def _handle_transient(self, context, ctrl, img):
        """处理战斗/结算瞬态，返回 True 表示已处理（外层 continue）"""

        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        # 结算页
        end = _hit(context, img, "剧情_结算继续")
        if end is not None and end.hit:
            _log("战斗结束，推进结算")
            click(1150, 710, 4.0)
            click(1150, 710, 4.0)
            return True

        # 玩家信息弹窗
        pi = _hit(context, img, "剧情_玩家信息取消")
        if pi is not None and pi.hit:
            click(858, 538, 2.0)
            return True

        # 战斗前对话跳过
        skip = _hit(context, img, "剧情_战斗前对话跳过")
        if skip is not None and skip.hit:
            c = _box_center(skip)
            click(*c, 1.5)
            return True

        # 战斗中（自动按钮可见）→ 等待
        battle = _hit(context, img, "剧情_战斗中待机")
        if battle is not None and battle.hit:
            time.sleep(5.0)
            return True

        return False

    # ---- 扫荡按钮颜色（粉=已通可扫，灰=未通） ----

    def _sweep_bright(self, img):
        try:
            region = img[545:600, 855:1015]
            if region.size == 0:
                return False
            mask = (region[:, :, 0] > 200) & (region[:, :, 1] < 150) & (region[:, :, 2] < 180)
            return float(mask.mean()) > 0.10
        except Exception:
            return False

    # ---- 战斗循环：等待战斗结束回到地图页 ----

    def _battle_until_map(self, context, ctrl, max_rounds=70):
        """出击后的战斗等待循环。返回：True=已回地图页；False=超时异常；None=致命"""

        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        battle_rounds = 0
        while battle_rounds < max_rounds:
            battle_rounds += 1
            img = shot()
            if img is None:
                time.sleep(2.0)
                continue

            # 回到地图页 → 完成
            if _hit(context, img, "推图_地图标题") is not None and _hit(context, img, "推图_地图标题").hit:
                _log("已回到地图页")
                return True

            # 通用瞬态
            if self._handle_transient(context, ctrl, img):
                continue

            time.sleep(5.0)

        _log("战斗等待超时")
        return None

    # ---- 收尾：左下宝箱 + 返回主界面 ----

    def _finish(self, context, ctrl):
        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        # 确保在地图页
        if _hit(context, shot(), "推图_地图标题") is None:
            if not _ensure_main_screen(context, ctrl):
                return False

        # 左下宝箱（星数达标领取，无奖励时点击无反应）
        click(60, 690, 3.0)
        # 获得物品/确认弹窗关闭
        for _ in range(3):
            img = shot()
            got = _hit(context, img, "乐园_获得物品弹窗")
            if got is not None and got.hit:
                click(55, 35, 2.0)
            else:
                break
            time.sleep(1.0)
        # 回主界面
        if not _ensure_main_screen(context, ctrl):
            return False
        _log("剧情推图收尾完成")
        return True


# ---------- 每日副本（作战中心·日常页签） ----------

DAILY_DUNGEON_ROUNDS = 60   # 单次战斗内轮次上限（10s/轮 ≈ 10 分钟，300 秒限时+余量）


@AgentServer.custom_action("每日副本循环")
class DailyDungeon(CustomAction):
    """每日副本全流程 agent：
    主界面 → 作战中心 → 日常页签 → 副本横向列表定位（OCR 副本名+滑动）
    → 点副本卡片 → 级别节点图 → OCR 级别名点节点 → 详情 → 作战准备
    → 编队（助战可开关）→ 出击 → 战斗 → 结算 → 回节点图 → 按次数循环
    游戏每日挑战次数/体力不足时自动停止。副本名/级别/次数在网页配置中心设置。
    """

    def run(self, context, argv) -> bool:
        cfg = load_daily_config()
        stage_name = cfg["stage_name"]
        level = cfg["level"]
        times = cfg["times"]
        _log(f"每日副本：{stage_name} · {level} 级别 × {times}")
        ctrl = context.tasker.controller
        if ctrl is None:
            return False

        # 1. 主界面 → 作战中心 → 日常页签（带确认重试）
        if not _ensure_main_screen(context, ctrl):
            return False
        try:
            context.run_action("剧情_从主界面进入")
            time.sleep(2)
        except Exception as e:
            _log(f"进作战中心异常: {e}")
            return False
        for attempt in range(3):
            _click(ctrl, 60, 220, 3.0)   # 点左栏「日常」页签（实测 60,220）
            img = _shot(ctrl)
            t = _hit(context, img, "副本_日常页签选中")
            if t is not None and t.hit:
                _log("已进入作战·日常页")
                break
        else:
            _log("3 次尝试未进入日常页签，结束")
            return True

        # 2. 副本横向列表定位（OCR 副本名 + 滑动）
        if not self._locate_stage(context, ctrl, stage_name):
            _log(f"未找到副本「{stage_name}」，结束")
            return True

        # 3. 点副本卡片 → 级别节点图（_locate_stage 内已确认，此处仅防御性复查）
        img = _shot(ctrl)
        t = _hit(context, img, "副本_级别图标题")
        if t is None or not t.hit:
            _log("未进入级别节点图，结束")
            return True

        # 4. 级别定位 + 战斗循环
        done = 0
        node_rounds = 0
        fail_streak = 0   # 连续进攻失败次数（次数用完/异常），连 3 次停止防死循环
        while done < times and node_rounds < 40:
            node_rounds += 1
            img = _shot(ctrl)
            if img is None:
                continue

            # 界面守卫：不在级别节点图（如上次战斗失败返回后已回列表页）→ 重新定位
            t = _hit(context, img, "副本_级别图标题")
            if t is None or not t.hit:
                _log("不在级别节点图，重新定位副本")
                if not self._locate_stage(context, ctrl, stage_name):
                    _log("重新定位副本失败，停止")
                    break
                continue

            # 游戏剩余次数 0 → 停止
            remain = self._remain_times(context, img)
            if remain == 0:
                _log("游戏挑战次数已用完，停止")
                break

            # 级别节点定位（OCR 级别名，override 精确匹配）
            lv = context.run_recognition("副本_级别节点", img, pipeline_override={
                "副本_级别节点": {"expected": [level], "roi": [150, 280, 1050, 440]}
            })
            if lv is not None and lv.hit:
                c = _box_center(lv)
                # 标签文字在节点本体下方约 105px，点标签无效，要点上方的六边形节点
                _log(f"点击级别节点 {level} @ ({c[0]},{c[1] - 105})")
                _click(ctrl, c[0], c[1] - 105, 3.0)
                # 详情 → 作战准备 → 编队 → 出击 → 战斗 → 回节点图
                ok = self._run_one_battle(context, ctrl)
                if ok is None:
                    return False
                if ok:
                    done += 1
                    fail_streak = 0
                else:
                    fail_streak += 1
                    if fail_streak >= 3:
                        _log("连续 3 次进攻失败（次数用完/异常），停止")
                        break
                continue

            # 级别节点不在当前可见区 → 滚动节点图
            _log("级别节点不在可见区，滚动")
            ctrl.post_swipe(1000, 450, 400, 450, 500).wait()
            time.sleep(2.0)
            if node_rounds > 12:
                _log("未找到目标级别节点，停止")
                break

        # 5. 返回主界面
        _ensure_main_screen(context, ctrl)
        _log(f"每日副本完成（{done}/{times}）")
        return True

    def _remain_times(self, context, img):
        """游戏每日挑战次数（右上「剩余次数 N」，数字为艺术字，在 all_results 里）"""
        d = _hit(context, img, "副本_剩余次数")
        if d is not None and d.hit:
            texts = [getattr(r, "text", "") or "" for r in (getattr(d, "all_results", None) or [])]
            m = re.search(r"(\d+)", "".join(texts))
            if m:
                return int(m.group(1))
        return -1

    def _locate_stage(self, context, ctrl, stage_name):
        """作战中心副本横向列表：OCR 副本名定位并点击卡片（含滑动遍历）。
        交互规律与活动页签一致：点非居中卡片只滚动居中，点居中卡片才进入。
        """

        def click(x, y, delay=2.0):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        for attempt in range(8):
            img = shot()
            if img is None:
                time.sleep(2.0)
                continue
            card = context.run_recognition("副本_卡片名称", img, pipeline_override={
                "副本_卡片名称": {"expected": [stage_name], "roi": [120, 100, 1160, 400]}
            })
            if card is not None and card.hit:
                c = _box_center(card)
                if 540 <= c[0] <= 850:
                    # 居中 → 点击进入
                    _log(f"找到居中副本卡片 {stage_name} @ ({c[0]},{c[1]})，点击进入")
                    click(*c, 2.5)
                    for _ in range(3):
                        img = shot()
                        t = _hit(context, img, "副本_级别图标题")
                        if t is not None and t.hit:
                            _log("已进入级别节点图")
                            return True
                        time.sleep(1.5)
                    return False
                # 侧边可见 → 点击使其居中后重扫
                _log(f"副本卡片 {stage_name} 不在居中（x={c[0]}），点击使其居中")
                click(*c, 2.8)
                time.sleep(2.5)
                continue
            # 未命中 → 滑动副本列表（双向交替，800ms 拖拽才能稳定滚动）
            _log(f"副本 {stage_name} 未在可见区（attempt {attempt}），滑动列表")
            if attempt % 2 == 0:
                ctrl.post_swipe(1000, 360, 300, 360, 800).wait()
            else:
                ctrl.post_swipe(300, 360, 1000, 360, 800).wait()
            time.sleep(2.0)
        return False

    def _run_one_battle(self, context, ctrl):
        """从节点图点击后到战斗结束回节点图。返回：True=打完一场；False=异常；None=致命"""
        def click(x, y, delay=1.5):
            _click(ctrl, x, y, delay)

        def shot():
            return _shot(ctrl)

        # 详情 → 作战准备
        prep = wait_hit(context, ctrl, "剧情_作战准备", 8)
        if prep is None:
            _log("详情未出现作战准备按钮")
            click(55, 35, 2.0)
            return False
        click(975, 558, 4.0)

        # 编队页 → 出击
        atk = wait_hit(context, ctrl, "剧情_出击", 10)
        if atk is None:
            _log("编队页未出现出击按钮（队伍不足？）")
            click(55, 35, 2.0)
            return False
        click(1190, 678, 8.0)

        # 战斗内循环（复用乐园同款瞬态处理 + 10s 检测）
        battle_rounds = 0
        while battle_rounds < DAILY_DUNGEON_ROUNDS:
            battle_rounds += 1
            img = _shot(ctrl)
            if img is None:
                time.sleep(2.0)
                continue

            # 回到级别节点图 → 完成
            t = _hit(context, img, "副本_级别图标题")
            if t is not None and t.hit:
                _log("已回到级别节点图")
                return True

            # 结算页
            end = _hit(context, img, "剧情_结算继续")
            if end is not None and end.hit:
                _log("战斗结束，推进结算")
                click(1150, 710, 4.0)
                click(1150, 710, 4.0)
                continue

            # 玩家信息弹窗
            pi = _hit(context, img, "剧情_玩家信息取消")
            if pi is not None and pi.hit:
                click(858, 538, 2.0)
                continue

            # 战斗前对话跳过
            skip = _hit(context, img, "剧情_战斗前对话跳过")
            if skip is not None and skip.hit:
                c = _box_center(skip)
                click(*c, 1.5)
                continue

            time.sleep(10.0)

        _log("单次副本战斗超时")
        return None


@AgentServer.custom_action("精灵试炼循环")
class SpiritTrial(CustomAction):
    """精灵试炼：每天挑战 3 个随机对手（难度递增 普通→困难→噩梦），
    胜利后领挑战奖励。全流程 2026-11-23 实机校准：
    进入准备(1227,685) → [精灵选择弹窗→点默认] → 作战准备页出击(1210,680)
    → 对手介绍页点屏幕跳过 → 自动战斗（实测速胜）→ 结算(1150,710)
    → 自动回试炼页并切下一个对手；3 胜后左下礼盒发光 → 点 (95,605) 领奖
    （实测奖励：钻石 600 + 金币 20000 + 凭证 4，消耗 10 体力/场）。
    """

    def run(self, context, argv) -> bool:
        ctrl = context.tasker.controller
        if ctrl is None:
            return False
        _log("精灵试炼：开始")

        if not _ensure_main_screen(context, ctrl):
            return False
        if not _navigate_activity(context, ctrl, "活动_精灵试炼卡片", "精灵试炼", partial_names=["精灵"]):
            _log("未进入精灵试炼页，结束")
            _ensure_main_screen(context, ctrl)
            return True
        time.sleep(2)

        wins = 0
        rounds = 0
        while rounds < 15:
            rounds += 1
            img = _shot(ctrl)
            if img is None:
                time.sleep(2.0)
                continue

            # 瞬态界面（结算/弹窗/介绍页）优先处理
            if self._handle_transient(context, ctrl, img):
                continue

            # 界面守卫：不在精灵试炼页 → 重新导航
            t = _hit(context, img, "精灵试炼_页标题")
            if t is None or not t.hit:
                _log("不在精灵试炼页，重新导航")
                if not _ensure_main_screen(context, ctrl):
                    return False
                if not _navigate_activity(context, ctrl, "活动_精灵试炼卡片", "精灵试炼", partial_names=["精灵"]):
                    _log("重新导航失败，结束")
                    break
                continue

            # 优先战斗：进入准备 → 出击。
            # （礼盒在已领取后仍发光，旧逻辑先查礼盒会误判"3胜完成"跳过战斗）
            prep = _hit(context, img, "精灵试炼_进入准备")
            if prep is not None and prep.hit:
                c = _box_center(prep)
                _log(f"点击进入准备 @ ({c[0]},{c[1]})")
                _click(ctrl, *c, 3.5)
                # 等作战准备页出击（途中可能弹「精灵选择」编队弹窗 → 点默认）
                at_prep = False
                for _ in range(8):
                    img = _shot(ctrl)
                    if img is None:
                        time.sleep(1.5)
                        continue
                    d = _hit(context, img, "精灵试炼_编队默认")
                    if d is not None and d.hit:
                        dc = _box_center(d)
                        _log("精灵选择弹窗，点默认")
                        _click(ctrl, *dc, 2.5)
                        continue
                    a = _hit(context, img, "剧情_出击")
                    if a is not None and a.hit:
                        at_prep = True
                        break
                    time.sleep(1.5)
                if not at_prep:
                    _log("进入准备未到作战准备页（今日对手已打完），战斗结束")
                    _click(ctrl, *BACK_POS, 2.0)
                    break
                ok = self._battle_one(context, ctrl)
                if ok is None:
                    _ensure_main_screen(context, ctrl)
                    return False
                if ok:
                    wins += 1
                    _log(f"精灵试炼进度 {wins}/3")
                continue

            time.sleep(3.0)

        # 领奖阶段（战斗全部结束后）：确保在精灵试炼页 → 礼盒发光则领取
        # （战斗循环 break 前可能已点返回位退出页面，需重新导航进入）
        img = _shot(ctrl)
        in_page = False
        if img is not None:
            t = _hit(context, img, "精灵试炼_页标题")
            in_page = t is not None and t.hit
        if not in_page:
            _log("领奖前重新进入精灵试炼页")
            if _navigate_activity(context, ctrl, "活动_精灵试炼卡片", "精灵试炼", partial_names=["精灵"]):
                time.sleep(4)   # 进页后立绘/礼盒加载需要时间
                in_page = True
        if in_page:
            # 礼盒有呼吸发光动画，重试几次等到可匹配的帧
            gift = None
            for _ in range(5):
                img = _shot(ctrl)
                if img is not None:
                    gift = _hit(context, img, "精灵试炼_礼盒")
                    if gift is not None and gift.hit:
                        break
                time.sleep(1.5)
            if gift is not None and gift.hit:
                gc = _box_center(gift)
                _log(f"点击礼盒领挑战奖励 @ ({gc[0]},{gc[1]})")
                _click(ctrl, *gc, 4.0)
                for _ in range(5):
                    time.sleep(2.0)
                    img = _shot(ctrl)
                    g = _hit(context, img, "世界之眼_获得物品关闭")
                    if g is not None and g.hit:
                        _log("挑战奖励已领取")
                        _click(ctrl, 55, 35, 2.0)
                        break

        _ensure_main_screen(context, ctrl)
        _log(f"精灵试炼结束（胜 {wins} 场）")
        return True

    def _handle_transient(self, context, ctrl, img):
        """战斗结算/玩家信息弹窗等瞬态界面处理，处理了返回 True"""
        end = _hit(context, img, "剧情_结算继续")
        if end is not None and end.hit:
            _click(ctrl, 1150, 710, 4.0)
            _click(ctrl, 1150, 710, 4.0)
            return True
        pi = _hit(context, img, "剧情_玩家信息取消")
        if pi is not None and pi.hit:
            _click(ctrl, 858, 538, 2.0)
            return True
        return False

    def _battle_one(self, context, ctrl):
        """从作战·准备页出击 → 战斗 → 回精灵试炼页。
        True=打完一场；False=本场异常；None=致命"""
        def shot():
            return _shot(ctrl)

        img = shot()
        a = _hit(context, img, "剧情_出击")
        if a is None or not a.hit:
            a = wait_hit(context, ctrl, "剧情_出击", 6)
            if a is None:
                _log("出击按钮未出现")
                _click(ctrl, 55, 35, 2.0)
                return False
        ac = _box_center(a)
        _click(ctrl, *ac, 6.0)

        # 对手介绍页（四糸乃/崇宫澪等大图）→ 点屏幕跳过
        time.sleep(3)
        _click(ctrl, 640, 400, 4.0)

        battle_rounds = 0
        while battle_rounds < DAILY_DUNGEON_ROUNDS:
            battle_rounds += 1
            img = _shot(ctrl)
            if img is None:
                time.sleep(2.0)
                continue

            # 回到精灵试炼页 → 完成
            t = _hit(context, img, "精灵试炼_页标题")
            if t is not None and t.hit:
                return True

            end = _hit(context, img, "剧情_结算继续")
            if end is not None and end.hit:
                _click(ctrl, 1150, 710, 4.0)
                _click(ctrl, 1150, 710, 4.0)
                continue

            pi = _hit(context, img, "剧情_玩家信息取消")
            if pi is not None and pi.hit:
                _click(ctrl, 858, 538, 2.0)
                continue

            time.sleep(8.0)

        _log("精灵试炼单场战斗超时")
        return None


def wait_hit(context, ctrl, entry, seconds=8):
    """轮询等待某识别节点命中（每 1.5s 截图一次）"""
    for _ in range(int(seconds / 1.5)):
        img = _shot(ctrl)
        if img is None:
            time.sleep(1.0)
            continue
        d = _hit(context, img, entry)
        if d is not None and d.hit:
            return d
        time.sleep(1.5)
    return None
