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


def _click(ctrl, x, y, delay=1.0):
    ctrl.post_click(int(x), int(y)).wait()
    time.sleep(delay)


def _shot(ctrl):
    """模拟器截图（偶发失败返回空图，自动重试 3 次）"""
    for _ in range(3):
        try:
            img = ctrl.post_screencap().get(wait=True)
        except Exception as e:
            _log(f"截图异常: {e}")
            time.sleep(1.0)
            continue
        if img is not None and getattr(img, "size", 0) > 0:
            return img
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
                pass  # 待用户确认出场技按钮位置后补

            time.sleep(3)
        else:
            _log("剧情战斗超时（超过 6 分钟），异常结束")
            return False

        _log("剧情战斗结束")
        return True


# ---------- 竞技场（青铜战场） ----------

ARENA_BATTLE_ROUNDS = 130  # 单把战斗内层轮次上限（10s/轮 ≈ 21 分钟），超出按异常处理
AUTO_BUTTON = (445, 45)    # 战斗内「自动」按钮（点两次切 2 倍速）
ARENA_MATCH_BTN = (1178, 710)
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

            # 点「匹配」
            img = shot()
            match_btn = _hit(context, img, "竞技场_匹配按钮")
            if match_btn is None or not match_btn.hit:
                t = _hit(context, img, "竞技场_界面标题")
                t_text = getattr(t, "text", "") if t else "<识别未启动>"
                t_box = getattr(t, "box", None) if t else None
                _log(f"竞技场界面未找到匹配按钮（标题识别: {t_text!r} box={t_box}）")
                click(*BACK_POS, delay=2.0)
                return True
            click(*ARENA_MATCH_BTN, delay=2.5)

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
                    click(1213, 33, delay=3.0)

            # 主界面 → 竞技场建筑
            img = shot()
            if _hit(context, img, "日常_主界面确认") is None:
                _log("导航：仍未到主界面，重试")
                continue
            _log("导航：已在主界面，点竞技场入口")
            try:
                context.run_action("日常_竞技场入口")
                time.sleep(2)
            except Exception as e:
                _log(f"竞技场导航异常: {e}")
                continue

            img = shot()
            if _hit(context, img, "竞技场_界面标题") is not None and _hit(context, img, "竞技场_界面标题").hit:
                _log("导航：已到达竞技场界面")
                return True
            _log("导航：点击竞技场入口后未识别到竞技场界面")
        return False


# ---------- 清日常调度（BAAH 式任务顺序配置） ----------

# 子流程显示名 → pipeline 入口节点名
DAILY_STEPS = {
    "签到": "日常_签到",
    "补给站": "日常_补给站入口",
    "邮件": "日常_邮件入口",
    "联络": "日常_联络入口",
    "社团": "日常_社团入口",
    "次元入侵": "日常_入侵入口",
    "追猎计划": "日常_追猎入口",
    "特训矩阵": "日常_特训入口",
    "竞技场": "日常_竞技场入口",
    "世界之眼": "世界之眼_入口",
    "魔女日程": "日常_魔女日程入口",
}

DEFAULT_DAILY_ORDER = [
    "签到", "补给站", "邮件", "联络", "社团", "次元入侵",
    "追猎计划", "特训矩阵", "竞技场", "世界之眼", "魔女日程",
]


def load_daily_order():
    """读取日常执行顺序，非法名剔除；配置缺失时用默认顺序"""
    try:
        with open(_DAILY_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        order = [s for s in data.get("order", []) if s in DAILY_STEPS]
        if order:
            return order
    except Exception:
        pass
    return list(DEFAULT_DAILY_ORDER)


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
            try:
                context.run_task(entry)
                time.sleep(2.0)
            except Exception as e:
                _log(f"子流程 {step} 异常: {e}")

        _log("全部日常子流程执行完毕")
        return True

    def _ensure_main(self, context, ctrl):
        """确保回到主界面（最多 3 轮：游戏启动 + 返回位 + 家园兜底）"""

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
