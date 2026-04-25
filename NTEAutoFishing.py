import cv2
import numpy as np
import mss
import pydirectinput
import keyboard
import time
import win32gui
import win32con
import win32api
import win32process
import psutil
import ctypes

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except AttributeError:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

pydirectinput.PAUSE = 0  

# ======================= 配置区 =======================
PROCESS_NAME = "HTGame.exe"

# ----- 按键输入发送模式控制 -----
# 设为 True 尝试实现无干扰后台发送。若进游戏无效则说明被物理防封，需设回 False 运行挂机。
BACKGROUND_MODE = True  

SLIDER_ROI = (592, 58, 742, 29)

GREEN_LOWER = np.array([35, 130, 0])
GREEN_UPPER = np.array([89, 255, 255])
YELLOW_LOWER = np.array([0, 0, 70])
YELLOW_UPPER = np.array([60, 255, 255])

KEY_LEFT = 'a'
KEY_RIGHT = 'd'

# 减小中心盲区（让其时刻修正，不再等到偏差变大才动手），默认从8降到了3。
CENTER_TOLERANCE = 3
PREDICT_TIME = 0.08      
# ======================================================

VK_CODE = {
    'a': 0x41, 'd': 0x44, 'f': 0x46
}

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

def simulate_keydown(key):
    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if BACKGROUND_MODE and hwnd:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, VK_CODE.get(key, 0), 0)
    else:
        pydirectinput.keyDown(key)

def simulate_keyup(key):
    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if BACKGROUND_MODE and hwnd:
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, VK_CODE.get(key, 0), 0)
    else:
        pydirectinput.keyUp(key)

def simulate_press(key):
    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if BACKGROUND_MODE and hwnd:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, VK_CODE.get(key, 0), 0)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, VK_CODE.get(key, 0), 0)
    else:
        pydirectinput.press(key)

def simulate_left_click():
    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if BACKGROUND_MODE and hwnd:
        # 打包一条安全中心区标定的坐标
        rect = win32gui.GetClientRect(hwnd)
        center_lparam = win32api.MAKELONG(rect[2] // 2, rect[3] // 2)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, center_lparam)
        time.sleep(0.05)
        win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, center_lparam)
    else:
        pydirectinput.click()

def force_release_all_keys():
    simulate_keyup(KEY_LEFT)
    simulate_keyup(KEY_RIGHT)

def auto_fishing():
    print(f"等待获取 [{PROCESS_NAME}]...")
    while get_window_bbox(PROCESS_NAME) is None:
        time.sleep(1)
        if keyboard.is_pressed('q'):
            return

    print("准备完毕。按 Q 结束运行。")
    state = "IDLE"
    current_held_key = None
    
    miss_frames = 0
    smooth_green_vel = 0.0
    last_green_center = None
    last_valid_time = 0.0

    def switch_key(new_key):
        nonlocal current_held_key
        if current_held_key != new_key:
            if current_held_key is not None:
                simulate_keyup(current_held_key)
            if new_key is not None:
                simulate_keydown(new_key)
            current_held_key = new_key

    def init_fishing_control(first_green_center):
        nonlocal miss_frames, smooth_green_vel, last_green_center, last_valid_time, current_held_key
        miss_frames = 0
        smooth_green_vel = 0.0
        last_green_center = first_green_center
        last_valid_time = time.time()
        
        force_release_all_keys()
        current_held_key = None

    with mss.mss() as sct:
        while True:
            if keyboard.is_pressed('q'):
                force_release_all_keys()
                print("手动结束。")
                break

            bbox = get_window_bbox(PROCESS_NAME)
            if not bbox:
                time.sleep(1)
                continue

            img = np.array(sct.grab(bbox))
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            img_1080p = cv2.resize(img, (1920, 1080))

            x, y, w, h = SLIDER_ROI
            slider_img = img_1080p[y:y+h, x:x+w]
            hsv_slider = cv2.cvtColor(slider_img, cv2.COLOR_BGR2HSV)

            green_min_x, green_max_x = find_green_bounds_x(hsv_slider)
            yellow_x = find_yellow_center_x(hsv_slider)
            curr_time = time.time()

            if state == "IDLE":
                if green_min_x is not None:
                    green_center_x = (green_min_x + green_max_x) // 2
                    init_fishing_control(green_center_x)
                    state = "REELING"
                else:
                    simulate_press('f')
                    simulate_left_click()
                    time.sleep(0.2)

            elif state == "REELING":
                if green_min_x is not None and yellow_x is not None:
                    miss_frames = 0 
                    green_center_x = (green_min_x + green_max_x) // 2
                    
                    if last_green_center is not None and green_center_x != last_green_center:
                        dt = curr_time - last_valid_time
                        if 0 < dt < 0.2:
                            instant_vel = (green_center_x - last_green_center) / dt
                            # 【核心灵敏度修改项】：75%的比重取于即时的当前滑块新瞬时切动量速度，极大幅加强紧咬跟随
                            smooth_green_vel = 0.25 * smooth_green_vel + 0.75 * instant_vel
                        else:
                            smooth_green_vel = 0.0
                        
                        last_green_center = green_center_x
                        last_valid_time = curr_time

                    predict_offset = smooth_green_vel * PREDICT_TIME 
                    aiming_target_x = green_center_x + predict_offset
                    
                    max_boundary_pull_out = 10 
                    min_valid = green_min_x + max_boundary_pull_out
                    max_valid = green_max_x - max_boundary_pull_out
                    aiming_target_x = max(min_valid, min(aiming_target_x, max_valid)) 

                    if yellow_x < aiming_target_x - CENTER_TOLERANCE:
                        switch_key(KEY_RIGHT)
                    elif yellow_x > aiming_target_x + CENTER_TOLERANCE:
                        switch_key(KEY_LEFT)
                    else:
                        switch_key(None)
                else:
                    miss_frames += 1
                    switch_key(None)

                    if miss_frames > 15: 
                        switch_key(None)
                        force_release_all_keys() 
                        time.sleep(1) 
                        state = "IDLE"

            time.sleep(0.002)

if __name__ == "__main__":
    auto_fishing()