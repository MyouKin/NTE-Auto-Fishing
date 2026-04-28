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
import threading
import random 

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except AttributeError:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

pydirectinput.PAUSE = 0  
pydirectinput.FAILSAFE = False  

# ======================= 配置区 =======================
PROCESS_NAME = "HTGame.exe"
BACKGROUND_MODE = True  

SLIDER_ROI = (608, 65, 713, 20)

GREEN_LOWER = np.array([70, 190, 0])
GREEN_UPPER = np.array([90, 255, 255])
YELLOW_LOWER = np.array([0, 0, 215])
YELLOW_UPPER = np.array([60, 160, 255])

KEY_LEFT = 'a'
KEY_RIGHT = 'd'
CENTER_TOLERANCE = 5
PREDICT_TIME = 0.08      
MORPH_KERNEL_SIZE = 21

GREEN_MIN_AREA = 1400

STATE_TIMEOUT = 20.0   # 钓鱼状态下的防卡死超时时长
IDLE_TIMEOUT = 10.0    # 闲置等待状态下的定期重置循环时长
IS_RUNNING = False

CLICK_RANDOM_OFFSET = 100 
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
    if not target_pid: return None

    def callback(hwnd, hwnds):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if pid == target_pid:
                rect = win32gui.GetClientRect(hwnd)
                if rect[2] > 0 and rect[3] > 0: hwnds.append(hwnd)
        return True

    hwnds =[]
    win32gui.EnumWindows(callback, hwnds)
    if hwnds:
        _cached_hwnd = hwnds[0]
        return _cached_hwnd
    return None

def get_window_bbox(process_name):
    hwnd = get_hwnd_by_process_name(process_name)
    if not hwnd: return None
    try:
        rect = win32gui.GetClientRect(hwnd)
        point = win32gui.ClientToScreen(hwnd, (0, 0))
        return {"left": point[0], "top": point[1], "width": rect[2], "height": rect[3]}
    except:
        global _cached_hwnd
        _cached_hwnd = None
        return None

def find_yellow_center_from_mask(mask):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        c = max(contours, key=cv2.contourArea)
        if cv2.contourArea(c) > 5:
            M = cv2.moments(c)
            if M["m00"] != 0: return int(M["m10"] / M["m00"])
    return None

def find_green_bounds_from_mask(mask):
    x_coords = np.where(mask > 0)[1]
    if len(x_coords) > 0: 
        return int(np.min(x_coords)), int(np.max(x_coords))
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

def force_release_all_keys():
    # 释放 AD 方向键
    simulate_keyup(KEY_LEFT)
    simulate_keyup(KEY_RIGHT)
    
    # 释放 F键 与 鼠标左键
    if BACKGROUND_MODE:
        hwnd = get_hwnd_by_process_name(PROCESS_NAME)
        if hwnd:
            try:
                win32api.PostMessage(hwnd, win32con.WM_KEYUP, VK_CODE['f'], 0)
                win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, 0)
            except:
                pass
    else:
        try:
            pydirectinput.keyUp('f')
            pydirectinput.mouseUp(button='left')
        except:
            pass

def auto_fishing():
    global IS_RUNNING
    
    print(f"等待获取[{PROCESS_NAME}] 窗口...")
    while get_window_bbox(PROCESS_NAME) is None:
        time.sleep(1)
        if keyboard.is_pressed('q'): return

    print("开始执行，可随时按 Q 键停止。")
    IS_RUNNING = True
    
    state = "IDLE"
    state_start_time = time.time() 
    current_held_key = None
    
    miss_frames = 0
    smooth_green_vel = 0.0
    last_green_center = None
    last_valid_time = 0.0
    spammer_thread_id = 0

    cv2.namedWindow("Debug Vision", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Debug Vision", SLIDER_ROI[2] * 2, SLIDER_ROI[3] * 6)

    def switch_key(new_key):
        nonlocal current_held_key
        if current_held_key != new_key:
            if current_held_key is not None: simulate_keyup(current_held_key)
            if new_key is not None: simulate_keydown(new_key)
            current_held_key = new_key

    def _click_spammer_thread(thread_id):
        # 加入 thread_id 判断，当外部发起重置导致 ID 更新时，旧线程自动销毁
        while IS_RUNNING and spammer_thread_id == thread_id:
            hwnd = get_hwnd_by_process_name(PROCESS_NAME)
            if hwnd:
                try:
                    rect = win32gui.GetClientRect(hwnd)
                    if rect[2] <= 0 or rect[3] <= 0:
                        time.sleep(0.4)
                        continue

                    if BACKGROUND_MODE:
                        rand_x = (rect[2] // 2) + random.randint(-CLICK_RANDOM_OFFSET, CLICK_RANDOM_OFFSET)
                        rand_y = (rect[3] // 2) + random.randint(-CLICK_RANDOM_OFFSET, CLICK_RANDOM_OFFSET)
                        center_lparam = win32api.MAKELONG(rand_x, rand_y)
                    else:
                        point = win32gui.ClientToScreen(hwnd, (0, 0))
                        abs_x = point[0] + (rect[2] // 2) + random.randint(-CLICK_RANDOM_OFFSET, CLICK_RANDOM_OFFSET)
                        abs_y = point[1] + (rect[3] // 2) + random.randint(-CLICK_RANDOM_OFFSET, CLICK_RANDOM_OFFSET)

                    if BACKGROUND_MODE:
                        try:
                            win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, VK_CODE['f'], 0)
                        finally:
                            time.sleep(0.1)  
                            win32api.PostMessage(hwnd, win32con.WM_KEYUP, VK_CODE['f'], 0)
                        
                        time.sleep(0.15) 
                        
                        try:
                            win32api.PostMessage(hwnd, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, center_lparam)
                        finally:
                            time.sleep(0.1) 
                            win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, center_lparam)
                            
                    else:
                        try:
                            pydirectinput.keyDown('f')
                        finally:
                            time.sleep(0.1)
                            pydirectinput.keyUp('f')
                            
                        time.sleep(0.15)
                        
                        pydirectinput.moveTo(abs_x, abs_y)
                        
                        try:
                            pydirectinput.mouseDown(button='left')
                        finally:
                            time.sleep(0.1)
                            pydirectinput.mouseUp(button='left')
                            
                except Exception:
                    # 发生异常时由 force_release_all_keys 统一处理兜底
                    pass
            time.sleep(0.3)

    def full_system_reset(reset_type="CYCLE"):
        nonlocal miss_frames, smooth_green_vel, last_green_center, last_valid_time, current_held_key, state_start_time
        nonlocal spammer_thread_id, state
        
        # 1. 废弃旧线程
        spammer_thread_id += 1
        current_id = spammer_thread_id
        
        # 2. 清理所有物理按键残留状态
        force_release_all_keys()
        switch_key(None)
        
        # 3. 重置全部程序追踪状态参数
        state = "IDLE"
        miss_frames = 0
        smooth_green_vel = 0.0
        last_green_center = None
        last_valid_time = time.time()
        state_start_time = time.time()
        current_held_key = None
        
        # 4. 创建全新的干净线程接管
        threading.Thread(target=_click_spammer_thread, args=(current_id,), daemon=True).start()
        
        if reset_type == "TIMEOUT":
            print(f"[日志] 触发超时机制，所有驱动进程与状态已重置")
        elif reset_type == "CYCLE":
            pass  # 常规流程重置不额外输出日志
            # print(f"[日志] 完成本轮流程，执行线程驱动与按键层级刷新")

    # 首次启动线程与系统初始化
    full_system_reset("START")

    with mss.mss() as sct:
        while True:
            key_in = cv2.waitKey(1) & 0xFF
            if keyboard.is_pressed('q') or key_in == ord('q'):
                IS_RUNNING = False
                force_release_all_keys()
                cv2.destroyAllWindows()
                break

            bbox = get_window_bbox(PROCESS_NAME)
            if not bbox:
                continue

            img = np.array(sct.grab(bbox))
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
            img_1080p = cv2.resize(img, (1920, 1080))

            x, y, w, h = SLIDER_ROI
            slider_img = img_1080p[y:y+h, x:x+w]
            hsv_slider = cv2.cvtColor(slider_img, cv2.COLOR_BGR2HSV)

            mask_green = cv2.inRange(hsv_slider, GREEN_LOWER, GREEN_UPPER)
            
            if MORPH_KERNEL_SIZE > 0:
                kernel = np.ones((MORPH_KERNEL_SIZE, MORPH_KERNEL_SIZE), np.uint8)
                mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_CLOSE, kernel)
            
            contours_g, _ = cv2.findContours(mask_green, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            clean_mask_green = np.zeros_like(mask_green)
            for cg in contours_g:
                if cv2.contourArea(cg) > GREEN_MIN_AREA:
                    cv2.drawContours(clean_mask_green, [cg], -1, 255, -1)
            mask_green = clean_mask_green
            
            mask_yellow = cv2.inRange(hsv_slider, YELLOW_LOWER, YELLOW_UPPER)

            green_min_x, green_max_x = find_green_bounds_from_mask(mask_green)
            yellow_x = find_yellow_center_from_mask(mask_yellow)
            
            debug_view = slider_img.copy()
            if green_min_x is not None:
                cv2.rectangle(debug_view, (green_min_x, 0), (green_max_x, h-1), (0, 255, 0), 1)
            if yellow_x is not None:
                cv2.line(debug_view, (yellow_x, 0), (yellow_x, h-1), (0, 255, 255), 1)

            vis_stack = np.vstack((
                debug_view, 
                cv2.cvtColor(mask_green, cv2.COLOR_GRAY2BGR),
                cv2.cvtColor(mask_yellow, cv2.COLOR_GRAY2BGR)
            ))
            cv2.imshow("Debug Vision", vis_stack)

            curr_time = time.time()
            
            # 定时兜底：若长期保持状态不流转，进行全盘初始化
            if state == "REELING" and (curr_time - state_start_time > STATE_TIMEOUT):
                full_system_reset("TIMEOUT")
                continue 
            elif state == "IDLE" and (curr_time - state_start_time > IDLE_TIMEOUT):
                full_system_reset("TIMEOUT")
                continue
            
            if state == "IDLE":
                if green_min_x is not None:
                    # 进入控鱼状态，仅初始化运算参数而不重启线程
                    miss_frames = 0
                    smooth_green_vel = 0.0
                    last_green_center = (green_min_x + green_max_x) // 2
                    last_valid_time = time.time()
                    
                    state = "REELING"
                    state_start_time = curr_time

            elif state == "REELING":
                if green_min_x is not None and yellow_x is not None:
                    miss_frames = 0 
                    green_center_x = (green_min_x + green_max_x) // 2
                    
                    if last_green_center is not None and green_center_x != last_green_center:
                        dt = curr_time - last_valid_time
                        if 0 < dt < 0.2:
                            instant_vel = (green_center_x - last_green_center) / dt
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

                    # 当检测不到绿色目标帧数累积上限，判断本轮控鱼已结束
                    if miss_frames > 10: 
                        full_system_reset("CYCLE")

if __name__ == "__main__":
    auto_fishing()