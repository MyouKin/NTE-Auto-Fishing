import cv2
import numpy as np
import mss
import win32gui
import win32con
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

PROCESS_NAME = "HTGame.exe"

def nothing(x):
    pass

def get_hwnd_by_process_name(process_name):
    target_pid = None
    for proc in psutil.process_iter(['name', 'pid']):
        if proc.info['name'] and proc.info['name'].lower() == process_name.lower():
            target_pid = proc.info['pid']
            break
            
    if not target_pid:
        raise Exception(f"未运行进程: {process_name}")

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
        return hwnds[0]
    else:
        raise Exception(f"没有可视化的游戏窗口: {process_name}")

def get_window_bbox(process_name):
    hwnd = get_hwnd_by_process_name(process_name)
    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    
    rect = win32gui.GetClientRect(hwnd)
    point = win32gui.ClientToScreen(hwnd, (0, 0))
    return {"left": point[0], "top": point[1], "width": rect[2], "height": rect[3]}

def debug_screen_and_color():
    try:
        bbox = get_window_bbox(PROCESS_NAME)
        print(f"坐标: {bbox}")
    except Exception as e:
        print(e)
        return

    with mss.mss() as sct:
        img = np.array(sct.grab(bbox))
        img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        img_resized = cv2.resize(img, (1920, 1080))

        print("用鼠标拖动框选区域后按空格，或按C重新框选")
        cv2.namedWindow("Select ROI", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Select ROI", 1280, 720) 
        
        roi = cv2.selectROI("Select ROI", img_resized, showCrosshair=True, fromCenter=False)
        cv2.destroyWindow("Select ROI")
        print(f"ROI 坐标: {roi}")

        x, y, w, h = roi
        if w == 0 or h == 0:
            print("退出调试")
            return
            
        roi_img = img_resized[y:y+h, x:x+w]

        cv2.namedWindow("Color Debugger", cv2.WINDOW_NORMAL)
        cv2.createTrackbar("H Min", "Color Debugger", 0, 179, nothing)
        cv2.createTrackbar("H Max", "Color Debugger", 179, 179, nothing)
        cv2.createTrackbar("S Min", "Color Debugger", 0, 255, nothing)
        cv2.createTrackbar("S Max", "Color Debugger", 255, 255, nothing)
        cv2.createTrackbar("V Min", "Color Debugger", 0, 255, nothing)
        cv2.createTrackbar("V Max", "Color Debugger", 255, 255, nothing)
        
        # [新增] 闭操作内核大小调整控制杆
        cv2.createTrackbar("Close Size", "Color Debugger", 21, 100, nothing)

        while True:
            h_min = cv2.getTrackbarPos("H Min", "Color Debugger")
            h_max = cv2.getTrackbarPos("H Max", "Color Debugger")
            s_min = cv2.getTrackbarPos("S Min", "Color Debugger")
            s_max = cv2.getTrackbarPos("S Max", "Color Debugger")
            v_min = cv2.getTrackbarPos("V Min", "Color Debugger")
            v_max = cv2.getTrackbarPos("V Max", "Color Debugger")
            c_size = cv2.getTrackbarPos("Close Size", "Color Debugger")

            lower = np.array([h_min, s_min, v_min])
            upper = np.array([h_max, s_max, v_max])

            hsv = cv2.cvtColor(roi_img, cv2.COLOR_BGR2HSV)
            mask = cv2.inRange(hsv, lower, upper)
            
            #[核心执行区] 进行形态学闭操作修复连通断点
            c_size = max(1, c_size) # 防止大小为0抛异常
            kernel = np.ones((c_size, c_size), np.uint8)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            result = cv2.bitwise_and(roi_img, roi_img, mask=mask)

            mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            stacked = np.hstack((roi_img, mask_bgr, result))
            
            cv2.imshow("Color Debugger", stacked)

            if cv2.waitKey(1) & 0xFF == 27:
                print(f"\n===== 将以下参数复制到 NTEAutoFishing.py 配置区 =====")
                print(f"ROI = {roi}")
                print(f"LOWER = np.array([{h_min}, {s_min}, {v_min}])")
                print(f"UPPER = np.array([{h_max}, {s_max}, {v_max}])")
                print(f"MORPH_KERNEL_SIZE = {c_size}")
                print(f"=====================================================")
                break

        cv2.destroyAllWindows()

if __name__ == "__main__":
    debug_screen_and_color()