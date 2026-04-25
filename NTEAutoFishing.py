import cv2
import numpy as np
import mss
import pydirectinput
import keyboard
import time
import win32gui
import win32con
import win32process
import psutil
import ctypes

# ================== 开启系统 DPI 感知 ==================
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except AttributeError:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

pydirectinput.PAUSE = 0  # 极其关键，关闭原生指令等待时间以适应百赫兹级下发

# ======================= 配置区 =======================
PROCESS_NAME = "HTGame.exe"

SLIDER_ROI = (592, 58, 742, 29)

GREEN_LOWER = np.array([35, 130, 0])
GREEN_UPPER = np.array([89, 255, 255])

YELLOW_LOWER = np.array([0, 0, 70])
YELLOW_UPPER = np.array([60, 255, 255])

KEY_LEFT = 'a'
KEY_RIGHT = 'd'

CENTER_TOLERANCE = 8     # 核心中点静态靠拢死区（微调缩小死区可以跟得更紧凑）

# ===== 高级运动控制（前馈追踪）核心调参参数 =====
# “预测时间（秒）”：打多少时间后的提前量？决定跟运动趋势纠正的侵略性。
# 如设置为 0.08，系统计算认为滑块正向左滑时，就预先按左边0.08s时即将达到的地方作为中心！可以自行加减(0.02~0.25区间均有效)。
PREDICT_TIME = 0.08
# ======================================================

_cached_hwnd = None

def get_hwnd_by_process_name(process_name):
    global _cached_hwnd
    if _cached_hwnd and win32gui.IsWindow(_cached_hwnd):
        return _cached_hwnd

    target_pid = None
    for proc in psutil.process_iter(['name', 'pid']):
        if proc.info['name'] and proc.info['name'].lower() == process_name.lower():
            target_pid = proc.info['pid']
            break
            
    if not target_pid:
        return None

    def callback(hwnd, hwnds):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == target_pid:
                rect = win32gui.GetClientRect(hwnd)
                if rect[2] > 0 and rect[3] > 0:
                    hwnds.append(hwnd)
        return True

    hwnds =[]
    win32gui.EnumWindows(callback, hwnds)
    if hwnds:
        _cached_hwnd = hwnds[0]
        return _cached_hwnd
    return None

def get_window_bbox(process_name):
    hwnd = get_hwnd_by_process_name(process_name)
    if not hwnd:
        return None
    try:
        rect = win32gui.GetClientRect(hwnd)
        point = win32gui.ClientToScreen(hwnd, (0, 0))
        return {"left": point[0], "top": point[1], "width": rect[2], "height": rect[3]}
    except:
        global _cached_hwnd
        _cached_hwnd = None
        return None

def find_yellow_center_x(hsv_img):
    mask = cv2.inRange(hsv_img, YELLOW_LOWER, YELLOW_UPPER)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) > 5:
            M = cv2.moments(c)
            if M["m00"] != 0:
                return int(M["m10"] / M["m00"])
    return None

def find_green_bounds_x(hsv_img):
    mask = cv2.inRange(hsv_img, GREEN_LOWER, GREEN_UPPER)
    x_coords = np.where(mask > 0)[1]
    if len(x_coords) > 20: 
        return np.min(x_coords), np.max(x_coords)
    return None, None

def auto_fishing():
    print(f"等待检测到游戏进程[{PROCESS_NAME}] 的窗口...")
    while get_window_bbox(PROCESS_NAME) is None:
        time.sleep(1)
        if keyboard.is_pressed('q'):
            return

    print("自动钓鱼脚本已启动！按 'q' 键退出。")
    state = "IDLE"
    miss_frames = 0
    current_held_key = None
    
    # 【运动检测相关状态】
    last_green_center = None
    last_loop_time = time.time()
    smooth_green_vel = 0.0  # 平滑过的绿条移动速度（像素/秒）

    def switch_key(new_key):
        nonlocal current_held_key
        if current_held_key != new_key:
            if current_held_key is not None:
                pydirectinput.keyUp(current_held_key)
            if new_key is not None:
                pydirectinput.keyDown(new_key)
            current_held_key = new_key

    with mss.mss() as sct:
        while True:
            curr_time = time.time()
            dt = curr_time - last_loop_time  # 当前迭代使用时间
            last_loop_time = curr_time

            if keyboard.is_pressed('q'):
                switch_key(None)
                print("退出脚本。")
                break

            bbox = get_window_bbox(PROCESS_NAME)
            if not bbox:
                time.sleep(1)
                continue

            # 使用全像素区域截图提升转换响应
            img = np.array(sct.grab(bbox))
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            img_1080p = cv2.resize(img, (1920, 1080))

            x, y, w, h = SLIDER_ROI
            slider_img = img_1080p[y:y+h, x:x+w]
            hsv_slider = cv2.cvtColor(slider_img, cv2.COLOR_BGR2HSV)

            green_min_x, green_max_x = find_green_bounds_x(hsv_slider)
            yellow_x = find_yellow_center_x(hsv_slider)

            if state == "IDLE":
                if green_min_x is not None:
                    print("[状态] 检测到目标，进入【预测追踪模式】...")
                    state = "REELING"
                    miss_frames = 0
                    smooth_green_vel = 0.0  # 切入控鱼瞬间复位惯性
                    last_green_center = (green_min_x + green_max_x) // 2
                else:
                    pydirectinput.press('f')
                    pydirectinput.click()
                    time.sleep(0.2)

            elif state == "REELING":
                if green_min_x is not None and yellow_x is not None:
                    miss_frames = 0 
                    green_center_x = (green_min_x + green_max_x) // 2
                    
                    # 1. ==== 计算猎物绿条实时速度并降噪 ====
                    if last_green_center is not None and dt > 0.0001:
                        instant_vel = (green_center_x - last_green_center) / dt
                        # 进行指数级滑动平均(EMA)抑制微抖造成的测速失准,只取大趋势方向(加权融合：60%上一帧大势，40%最新抓取切变)
                        smooth_green_vel = 0.6 * smooth_green_vel + 0.4 * instant_vel
                    else:
                        smooth_green_vel = 0.0

                    last_green_center = green_center_x

                    # 2. ==== 核心前馈追踪偏移介入 ====
                    # 并非直瞄真正的绿条当前中线了，目标打在 “速度*前视预期系数” 上！
                    predict_offset = smooth_green_vel * PREDICT_TIME 
                    aiming_target_x = green_center_x + predict_offset
                    
                    # 给定物理边界封印保护机制 (再怎么被速度甩也至少强制向框死在这个内围区间瞄准避免越野导致滑钩)：
                    max_boundary_pull_out = 10 # 防止将指针指导跑到绿边之外
                    min_valid = green_min_x + max_boundary_pull_out
                    max_valid = green_max_x - max_boundary_pull_out
                    
                    # 这也是精简写法的钳制安全区间边界
                    aiming_target_x = max(min_valid, min(aiming_target_x, max_valid)) 

                    # 3. ==== 下行击发：执行运动纠集操作 ====
                    # 这时由于aiming_target被极大向前拽引（发生急变方向回正左侧偏移后），当前小鱼即便还在偏左也极大可能相对目标靶心是“过度偏右状态”。触发 A
                    if yellow_x < aiming_target_x - CENTER_TOLERANCE:
                        switch_key(KEY_RIGHT)
                    elif yellow_x > aiming_target_x + CENTER_TOLERANCE:
                        switch_key(KEY_LEFT)
                    else:
                        # 指挥跟车咬到最内部目标位置(已经达到未来的轨迹核心)，关发停放状态。
                        switch_key(None)

                else:
                    miss_frames += 1
                    switch_key(None)
                    if miss_frames > 15: # 加宽退出条件帧(防止刷新时机微频闪)
                        print("[状态] 绿段丢失保护收线触发，等待返回休眠。")
                        time.sleep(1)
                        state = "IDLE"
            time.sleep(0.002)

if __name__ == "__main__":
    auto_fishing()