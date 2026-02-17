import sys
import os
import glob
import re
import serial
import time
import datetime
import threading
import signal
import numpy as np
from collections import deque
from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit

# --- 設定 ---
SERIAL_PORT = '/dev/ttyUSB0'
BAUD_RATE = 921600
SUB_CARRIERS = 192
LOG_DIR = "/home/umatani/csi/Service/logs"

# 自動キャリブレーションを行う時間 (24時間表記)
# この時間(3:00〜3:59)の間に一度だけ、誰もいない前提で基準値を更新します
AUTO_CALIB_HOUR = 3 

os.makedirs(LOG_DIR, exist_ok=True)

# --- HTML ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Wi-Fi CSI Radar (Auto-Calib)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #000000; color: #eee; margin: 0; padding: 10px; }
        .container { max-width: 1000px; margin: 0 auto; }
        
        .control-panel { background: #111; padding: 15px; border-radius: 8px; margin-bottom: 15px; display: flex; flex-wrap: wrap; gap: 10px; align-items: center; border: 1px solid #333; }
        select, button { padding: 8px 12px; border-radius: 4px; border: none; cursor: pointer; font-weight: bold; }
        
        button.active { background: #00d2ff; color: #000; }
        button.inactive { background: #333; color: #888; }
        
        /* キャリブレーションボタン */
        button.calib-btn { background: #d200ff; color: white; border: 1px solid #a000cc; }
        button.calib-active { background: #d200ff; color: white; animation: pulse 1s infinite; }
        
        button.record-on { background: #ff3333; color: white; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
        
        .status-bar { display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 0.9rem; color: #888; }
        .status-detected { color: #ff3333; font-weight: bold; font-size: 1.5rem; text-shadow: 0 0 10px #ff0000; }
        .status-sitting { color: #00ffff; font-weight: bold; font-size: 1.5rem; text-shadow: 0 0 10px #00ffff; }
        .status-safe { color: #444; font-weight: bold; font-size: 1.5rem; }
        .status-calib { color: #d200ff; font-weight: bold; font-size: 1.5rem; }

        .chart-container { background: #000; border: 1px solid #222; border-radius: 4px; margin-bottom: 10px; position: relative; }
        canvas { display: block; width: 100%; }
        #diffCanvas { height: 250px; background: #000; }
        
        /* 情報表示 */
        .info-text { font-family: monospace; font-size: 1.1rem; color: #aaa; }
        .thresh-val { color: #d200ff; font-weight: bold; }
    </style>
</head>
<body>
    <div class="container">
        <div class="control-panel">
            <button id="btnLive" onclick="setMode('live')" class="active">LIVE</button>
            <button id="btnHistory" onclick="setMode('history')" class="inactive">HISTORY</button>
            
            <div id="liveControls" style="display:flex; gap:10px; align-items:center;">
                <button id="btnRecord" onclick="toggleRecord()">REC: ON</button>
                <button id="btnCalib" class="calib-btn" onclick="startCalibration()">CALIBRATE (10s)</button>
            </div>

            <div id="historyControls" style="display:none; gap:10px;">
                <select id="fileSelect" style="background:#222; color:#fff; border:1px solid #444;"></select>
                <button onclick="loadSelectedFile()">PLAY</button>
            </div>
        </div>

        <div class="status-bar">
            <span id="timestamp" style="font-family:monospace;">--:--:--</span>
            <span class="info-text">
                Score: <span id="scoreVal">0.0</span> / 
                Base: <span id="baseVal">0.0</span> / 
                Thresh: <span id="threshVal" class="thresh-val">--</span>
            </span>
            <span id="statusText" class="status-safe">EMPTY</span>
        </div>

        <div class="chart-container">
            <canvas id="csiChart"></canvas>
        </div>

        <div class="chart-container" style="border-top: 2px solid #333;">
            <canvas id="diffCanvas"></canvas>
        </div>
    </div>

    <script>
        var socket = io();
        var isRecording = true;
        var diffCtx = document.getElementById('diffCanvas').getContext('2d');
        
        // --- Chart.js ---
        var csiChart = new Chart(document.getElementById('csiChart').getContext('2d'), {
            type: 'line',
            data: {
                labels: Array.from({length: 192}, (_, i) => i),
                datasets: [{
                    label: 'Amplitude',
                    data: new Array(192).fill(0),
                    borderColor: '#00d2ff',
                    borderWidth: 1.5,
                    pointRadius: 0,
                    fill: false,
                    tension: 0.1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                scales: { y: { beginAtZero: true, max: 60, grid: { color: '#222' } }, x: { display: false } },
                plugins: { legend: { display: false } }
            }
        });

        // --- Spectrogram ---
        function drawSpectrogram(diffArray) {
            var width = diffCanvas.width;
            var height = diffCanvas.height;
            var imageData = diffCtx.getImageData(0, 0, width, height - 1);
            diffCtx.putImageData(imageData, 0, 1);
            
            var rowImage = diffCtx.createImageData(width, 1);
            var subcarriers = diffArray.length;
            
            for (var i = 0; i < width; i++) {
                var dataIndex = Math.floor((i / width) * subcarriers);
                var val = diffArray[dataIndex];
                
                var r=0, g=0, b=0;
                var intensity = Math.min(val * 10, 255); 
                var t = (intensity / 255) ** 2; // Gamma correction

                if (t < 0.05) { r=0; g=0; b=0; } 
                else if (t < 0.4) {
                    var subT = (t - 0.05) / 0.35;
                    r = subT * 100; g = 0; b = 50 + (subT * 150);
                } else if (t < 0.7) {
                    var subT = (t - 0.4) / 0.3;
                    r = 100 + (155 * subT); g = subT * 100; b = 200 * (1.0 - subT);
                } else {
                    var subT = (t - 0.7) / 0.3;
                    r = 255; g = 100 + (155 * subT); b = subT * 255;
                }
                
                var px = i * 4;
                rowImage.data[px] = parseInt(r);
                rowImage.data[px+1] = parseInt(g);
                rowImage.data[px+2] = parseInt(b);
                rowImage.data[px+3] = 255;
            }
            diffCtx.putImageData(rowImage, 0, 0);
        }

        // --- Socket Events ---
        socket.on('update_data', function(msg) {
            csiChart.data.datasets[0].data = msg.amplitude;
            csiChart.update();
            if (msg.diff) drawSpectrogram(msg.diff);

            document.getElementById('timestamp').innerText = msg.timestamp;
            document.getElementById('scoreVal').innerText = msg.score.toFixed(1);
            document.getElementById('baseVal').innerText = msg.base_noise.toFixed(1);
            document.getElementById('threshVal').innerText = msg.threshold.toFixed(1);
            
            var statusEl = document.getElementById('statusText');
            statusEl.innerText = msg.status;
            
            // ステータス色分け
            statusEl.className = ""; // Reset
            if (msg.status === "CALIBRATING...") {
                statusEl.classList.add("status-calib");
                csiChart.data.datasets[0].borderColor = '#d200ff';
            } else if (msg.status === "WALKING") {
                statusEl.classList.add("status-detected");
                csiChart.data.datasets[0].borderColor = '#ff3333';
            } else if (msg.status === "SITTING") {
                statusEl.classList.add("status-sitting");
                csiChart.data.datasets[0].borderColor = '#00ffff';
            } else {
                statusEl.classList.add("status-safe");
                csiChart.data.datasets[0].borderColor = '#444444';
            }
            
            // ボタン状態同期
            var calibBtn = document.getElementById('btnCalib');
            if (msg.status === "CALIBRATING...") {
                calibBtn.innerText = "Running...";
                calibBtn.className = "calib-btn calib-active";
            } else {
                calibBtn.innerText = "CALIBRATE (10s)";
                calibBtn.className = "calib-btn";
            }
        });

        socket.on('file_list', function(files) {
            var select = document.getElementById('fileSelect');
            select.innerHTML = "";
            files.forEach(function(f) {
                var option = document.createElement("option"); option.text = f; option.value = f; select.add(option);
            });
        });

        // --- Functions ---
        function setMode(mode) {
            socket.emit('change_mode', {mode: mode});
            document.getElementById('btnLive').className = (mode === 'live') ? 'active' : 'inactive';
            document.getElementById('btnHistory').className = (mode === 'history') ? 'active' : 'inactive';
            document.getElementById('liveControls').style.display = (mode === 'live') ? 'flex' : 'none';
            document.getElementById('historyControls').style.display = (mode === 'history') ? 'flex' : 'none';
        }

        function toggleRecord() {
            isRecording = !isRecording;
            socket.emit('toggle_logging', {record: isRecording});
            var btn = document.getElementById('btnRecord');
            if (isRecording) { btn.innerText = "REC: ON"; btn.className = "record-on"; }
            else { btn.innerText = "REC: OFF"; btn.className = "inactive"; }
        }
        
        function startCalibration() {
            if(confirm("誰もいない状態で実行してください。開始しますか？")) {
                socket.emit('start_calibration');
            }
        }

        function loadSelectedFile() {
            var file = document.getElementById('fileSelect').value;
            if (file) socket.emit('load_file', {filename: file});
        }
        
        window.onload = function() {
            var c = document.getElementById('diffCanvas');
            c.width = c.parentElement.clientWidth;
            c.height = 250;
        }
    </script>
</body>
</html>
"""

class CSIUltimateService:
    def __init__(self):
        self.app = Flask(__name__)
        self.socketio = SocketIO(self.app, cors_allowed_origins="*", async_mode='threading')
        self.mode = 'live'
        self.is_running = True
        self.is_logging = True
        self.history_file = None
        self.playback_active = False
        
        self.prev_amp = None
        self.amp_history = deque(maxlen=50) 
        
        # --- キャリブレーション関連変数 ---
        self.base_noise_level = 2.0  # デフォルトのノイズレベル
        self.current_threshold = 6.5 # デフォルトの閾値 (Base + Margin)
        self.margin = 4.5            # マージン (ノイズ + 4.5 = 不在判定ライン)
        
        self.is_calibrating = False
        self.calibration_buffer = [] # キャリブレーション中のデータを溜める
        self.calibrated_today = False # 今日の自動補正が終わったかフラグ
        
        signal.signal(signal.SIGINT, self.stop_handler)
        signal.signal(signal.SIGTERM, self.stop_handler)

        self.app.add_url_rule('/', 'index', self.index)
        self.socketio.on_event('change_mode', self.handle_mode_change)
        self.socketio.on_event('toggle_logging', self.handle_logging_toggle)
        self.socketio.on_event('load_file', self.handle_file_load)
        self.socketio.on_event('start_calibration', self.handle_manual_calib)

    def stop_handler(self, signum, frame):
        print("Stopping Service...")
        self.is_running = False
        sys.exit(0)

    def index(self):
        return render_template_string(HTML_PAGE)

    def handle_mode_change(self, data):
        self.mode = data['mode']
        if self.mode == 'history':
            files = sorted(glob.glob(os.path.join(LOG_DIR, "csi_*.txt")), reverse=True)
            display_files = [os.path.basename(f) for f in files]
            emit('file_list', display_files)

    def handle_logging_toggle(self, data):
        self.is_logging = data['record']

    def handle_file_load(self, data):
        self.history_file = os.path.join(LOG_DIR, data['filename'])
        self.playback_active = True

    def handle_manual_calib(self):
        print("Manual Calibration Started")
        self.is_calibrating = True
        self.calibration_buffer = []

    def process_data(self, timestamp, amplitude):
        diff = np.zeros(SUB_CARRIERS)
        if self.prev_amp is not None:
            diff = np.abs(amplitude - self.prev_amp)
        self.prev_amp = amplitude

        self.amp_history.append(amplitude)
        status = "BUFFERING"
        score = 0.0
        
        if len(self.amp_history) == 50:
            history_np = np.array(self.amp_history)
            q75, q25 = np.percentile(history_np, [75, 25], axis=0)
            score = np.mean(q75 - q25) # IQR Score
            
            # === キャリブレーションモード ===
            if self.is_calibrating:
                status = "CALIBRATING..."
                self.calibration_buffer.append(score)
                
                # 約10秒分溜まったら完了 (1秒20フレーム換算で200個)
                if len(self.calibration_buffer) > 200:
                    # 溜まったデータから基準値を決定 (最大値を採用して安全側に倒す)
                    # 平均ではなくMax付近を採用することで、たまに入るノイズもカバーする
                    new_base = np.percentile(self.calibration_buffer, 95)
                    self.base_noise_level = new_base
                    self.current_threshold = self.base_noise_level + self.margin
                    
                    # 安全装置 (極端な値防止)
                    self.current_threshold = max(3.0, min(self.current_threshold, 15.0))
                    
                    print(f"Calibration Done. New Base: {new_base:.2f}, Thresh: {self.current_threshold:.2f}")
                    self.is_calibrating = False
                    self.calibration_buffer = []
            
            # === 通常判定モード ===
            else:
                if score < self.current_threshold:
                    status = "EMPTY"
                elif score < 25.0: # 動作判定ライン(固定でOK)
                    status = "SITTING"
                else:                
                    status = "WALKING"

        self.socketio.emit('update_data', {
            'timestamp': timestamp,
            'amplitude': amplitude.tolist(),
            'diff': diff.tolist(),
            'status': status,
            'score': score,
            'base_noise': self.base_noise_level,
            'threshold': self.current_threshold
        })

    def worker(self):
        ser = None
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        except Exception as e:
            print(f"Serial Error: {e}")

        while self.is_running:
            # === 自動キャリブレーションの時間チェック ===
            now = datetime.datetime.now()
            if now.hour == AUTO_CALIB_HOUR:
                if not self.calibrated_today:
                    print(f"Auto Calibration Started at {now}")
                    self.is_calibrating = True
                    self.calibration_buffer = []
                    self.calibrated_today = True # 今日はもうやらない
            else:
                # 時間が過ぎたらフラグをリセット (翌日のために)
                self.calibrated_today = False

            if self.mode == 'live':
                if ser is None: 
                    time.sleep(1)
                    continue
                try:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if "CSI_DATA" in line:
                        ts_str = now.strftime('%H:%M:%S.%f')[:-3]
                        
                        if self.is_logging:
                            fname = f"csi_{now.strftime('%Y%m%d_%H')}.txt"
                            with open(os.path.join(LOG_DIR, fname), "a") as f:
                                f.write(f"{now.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]},{line}\n")

                        match = re.search(r'\"\[(.*?)\]\"', line)
                        if match:
                            vals = [int(x) for x in match.group(1).split(',') if x.strip()]
                            if len(vals) == SUB_CARRIERS * 2:
                                c = np.array(vals)
                                amp = np.sqrt(c[0::2]**2 + c[1::2]**2)
                                self.process_data(ts_str, amp)
                except: pass

            elif self.mode == 'history':
                # (履歴再生処理は変更なし)
                if self.history_file and self.playback_active:
                    try:
                        with open(self.history_file, 'r') as f:
                            for line in f:
                                if self.mode != 'history' or not self.playback_active: break
                                parts = line.split(',', 1)
                                if len(parts) < 2: continue
                                ts = parts[0].split(' ')[1]
                                match = re.search(r'\"\[(.*?)\]\"', parts[1])
                                if match:
                                    vals = [int(x) for x in match.group(1).split(',') if x.strip()]
                                    if len(vals) == SUB_CARRIERS * 2:
                                        c = np.array(vals)
                                        amp = np.sqrt(c[0::2]**2 + c[1::2]**2)
                                        self.process_data(ts, amp)
                                        time.sleep(0.04)
                        self.playback_active = False
                    except: self.playback_active = False
                else:
                    time.sleep(0.5)

    def start(self):
        t = threading.Thread(target=self.worker, daemon=True)
        t.start()
        self.socketio.run(self.app, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)

if __name__ == "__main__":
    service = CSIUltimateService()
    service.start()
