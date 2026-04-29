import cv2
import numpy as np
import mss
import keyboard
import time
import win32gui
import win32con
import win32api
import win32process
import psutil
import ctypes
import threading

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except AttributeError:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except:
        pass

# ======================= 配置区 =======================
PROCESS_NAME = "HTGame.exe"

SHOW_DEBUG_VISION = False

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

STATE_TIMEOUT = 20.0   
IDLE_TIMEOUT = 10.0    
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
    if hwnd:
        win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, VK_CODE.get(key, 0), 0)

def simulate_keyup(key):
    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if hwnd:
        win32api.PostMessage(hwnd, win32con.WM_KEYUP, VK_CODE.get(key, 0), 0)

def force_release_all_keys():
    simulate_keyup(KEY_LEFT)
    simulate_keyup(KEY_RIGHT)
    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if hwnd:
        try:
            win32api.PostMessage(hwnd, win32con.WM_KEYUP, VK_CODE['f'], 0)
            win32api.PostMessage(hwnd, win32con.WM_LBUTTONUP, 0, 0)
        except Exception:
            pass

def refresh_window_focus(hwnd):
    """发送底层消息模拟窗口失去焦点与重新获取焦点，唤醒输入挂起"""
    if hwnd:
        try:
            # 模拟失去焦点
            win32api.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_INACTIVE, 0)
            win32api.PostMessage(hwnd, win32con.WM_KILLFOCUS, 0, 0)
            time.sleep(0.1)
            # 模拟获得焦点
            win32api.PostMessage(hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
            win32api.PostMessage(hwnd, win32con.WM_SETFOCUS, 0, 0)
        except Exception:
            pass

def auto_fishing():
    global _cached_hwnd
    _cached_hwnd = None 
    
    while get_window_bbox(PROCESS_NAME) is None:
        time.sleep(1)
        if keyboard.is_pressed('q'): return "QUIT"

    hwnd = get_hwnd_by_process_name(PROCESS_NAME)
    if hwnd:
        refresh_window_focus(hwnd)
        
    thread_running = [True]
    
    def _click_spammer_thread():
        while thread_running[0]:
            try:
                hwnd_t = get_hwnd_by_process_name(PROCESS_NAME)
                if not hwnd_t:
                    time.sleep(0.5)
                    continue

                rect = win32gui.GetClientRect(hwnd_t)
                if rect[2] <= 0 or rect[3] <= 0:
                    time.sleep(0.4)
                    continue

                center_lparam = win32api.MAKELONG(rect[2] // 2, rect[3] // 2)

                try:
                    win32api.PostMessage(hwnd_t, win32con.WM_KEYDOWN, VK_CODE['f'], 0)
                finally:
                    time.sleep(0.1)  
                    win32api.PostMessage(hwnd_t, win32con.WM_KEYUP, VK_CODE['f'], 0)
                
                time.sleep(0.1) 
                
                try:
                    win32api.PostMessage(hwnd_t, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, center_lparam)
                finally:
                    time.sleep(0.1) 
                    win32api.PostMessage(hwnd_t, win32con.WM_LBUTTONUP, 0, center_lparam)
                    
            except Exception:
                pass
            time.sleep(0.3)
            
    threading.Thread(target=_click_spammer_thread, daemon=True).start()

    state = "IDLE"
    state_start_time = time.time() 
    current_held_key = None
    last_valid_time = time.time()
    last_green_center = None

    if SHOW_DEBUG_VISION:
        cv2.namedWindow("Debug Vision", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Debug Vision", SLIDER_ROI[2] * 2, SLIDER_ROI[3] * 6)

    def switch_key(new_key):
        nonlocal current_held_key
        if current_held_key != new_key:
            if current_held_key is not None: simulate_keyup(current_held_key)
            if new_key is not None: simulate_keydown(new_key)
            current_held_key = new_key

    try:
        with mss.mss() as sct:
            while True:
                if SHOW_DEBUG_VISION:
                    key_in = cv2.waitKey(1) & 0xFF
                    quit_pressed = keyboard.is_pressed('q') or key_in == ord('q')
                else:
                    quit_pressed = keyboard.is_pressed('q')
                    
                if quit_pressed:
                    return "QUIT"

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
                
                if SHOW_DEBUG_VISION:
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
                
                if state == "REELING" and (curr_time - state_start_time > STATE_TIMEOUT):
                    return "RESTART"
                elif state == "IDLE" and (curr_time - state_start_time > IDLE_TIMEOUT):
                    return "RESTART"
                
                if state == "IDLE":
                    if green_min_x is not None:
                        smooth_green_vel = 0.0
                        last_green_center = (green_min_x + green_max_x) // 2
                        last_valid_time = curr_time
                        
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
                        switch_key(None)
                        return "RESTART"

    finally:
        thread_running[0] = False 
        force_release_all_keys()
        if SHOW_DEBUG_VISION:
            cv2.destroyAllWindows()

if __name__ == "__main__":
    print("开始运行...按Q退出")
    while True:
        result = auto_fishing()
        if result == "QUIT":
            break
        elif result == "RESTART":
            time.sleep(0.5)