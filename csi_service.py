import sys
import os
import glob
import re
import json
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
CONFIG_FILE = "/home/umatani/csi/config.json"

# 自動キャリブレーション時間
AUTO_CALIB_HOUR = 3 

os.makedirs(LOG_DIR, exist_ok=True)

# --- HTML ---
HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>Wi-Fi CSI Radar (Pro)</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.0.1/socket.io.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body { font-family: 'Segoe UI', sans-serif; background: #000000; color: #eee; margin: 0; padding: 10px; }
        .container { max-width: 1000px; margin: 0 auto; }
        
        .control-panel { background: #111; padding: 15px; border-radius: 8px; margin-bottom: 15px; border: 1px solid #333; }
        .panel-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; margin-bottom: 10px; }
        
        select, button { padding: 8px 12px; border-radius: 4px; border: none; cursor: pointer; font-weight: bold; }
        button.active { background: #00d2ff; color: #000; }
        button.inactive { background: #333; color: #888; }
        
        button.calib-btn { background: #d200ff; color: white; border: 1px solid #a000cc; }
        button.calib-active { background: #d200ff; color: white; animation: pulse 1s infinite; }
        
        button.record-on { background: #ff3333; color: white; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.7; } 100% { opacity: 1; } }
        
        /* スライダーエリア */
        .slider-container { width: 100%; display: flex; align-items: center; gap: 10px; margin-top: 5px; }
        .slider-label { width: 120px; font-size: 0.9rem; color: #aaa; text-align: right; }
        input[type=range] { flex-grow: 1; accent-color: #00d2ff; }
        .slider-val { width: 50px; text-align: right; font-family: monospace; color: #00d2ff; }

        .status-bar { display: flex; justify-content: space-between; margin-bottom: 10px; font-size: 0.9rem; color: #888; }
        .status-detected { color: #ff3333; font-weight: bold; font-size: 1.5rem; text-shadow: 0 0 10px #ff0000; }
        .status-sitting { color: #00ffff; font-weight: bold; font-size: 1.5rem; text-shadow: 0 0 10px #00ffff; }
        .status-safe { color: #444; font-weight: bold; font-size: 1.5rem; }
        .status-calib { color: #d200ff; font-weight: bold; font-size: 1.5rem; }

        .chart-container { background: #000; border: 1px solid #222; border-radius: 4px; margin-bottom: 10px; position: relative; }
        canvas { display: block; width: 100%; }
        #diffCanvas { height: 250px; background: #000; }
        
        .info-text { font-family: monospace; font-size: 1.1rem; color: #aaa; }
    </style>
</head>
<body>
    <div class="container">
        <div class="control-panel">
            <div class="panel-row">
                <button id="btnLive" onclick="setMode('live')" class="active">LIVE</button>
                <button id="btnHistory" onclick="setMode('history')" class="inactive">HISTORY</button>
                <button id="btnRecord" onclick="toggleRecord()">REC: ON</button>
                <button id="btnCalib" class="calib-btn" onclick="startCalibration()">AUTO CALIB (Empty)</button>
            </div>
            
            <div class="panel-row" style="border-top: 1px solid #333; padding-top: 10px;">
                <div class="slider-container">
                    <span class="slider-label">SITTING Threshold:</span>
                    <input type="range" id="sittingRange" min="5" max="50" step="0.5" value="25" oninput="updateThresholds()">
                    <span id="sittingVal" class="slider-val">25.0</span>
                </div>
            </div>
            <div class="panel-row">
                 <div class="slider-container">
                    <span class="slider-label">EMPTY Margin:</span>
                    <input type="range" id="marginRange" min="1" max="10" step="0.1" value="4.5" oninput="updateThresholds()">
                    <span id="marginVal" class="slider-val">4.5</span>
                </div>
            </div>

            <div id="historyControls" style="display:none; margin-top:10px;">
                <select id="fileSelect" style="background:#222; color:#fff; border:1px solid #444;"></select>
                <button onclick="loadSelectedFile()">PLAY</button>
            </div>
        </div>

        <div class="status-bar">
            <span id="timestamp" style="font-family:monospace;">--:--:--</span>
            <span class="info-text">
                Score: <span id="scoreVal" style="color:#fff;">0.0</span> | 
                Base: <span id="baseVal">0.0</span> | 
                Thresh: <span id="threshVal" style="color:#d200ff;">--</span>
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
                    label: 'Amplitude', data: new Array(192).fill(0),
                    borderColor: '#00d2ff', borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.1
                }]
            },
            options: {
                responsive: true, maintainAspectRatio: false, animation: false,
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
            
            for (var i = 0; i < width; i++) {
                var val = diffArray[Math.floor((i / width) * diffArray.length)];
                var t = (Math.min(val * 10, 255) / 255) ** 2;
                var r=0, g=0, b=0;

                if (t < 0.05) { r=0; g=0; b=0; } 
                else if (t < 0.4) { r = ((t-0.05)/0.35)*100; g=0; b=50+(((t-0.05)/0.35)*150); }
                else if (t < 0.7) { r = 100+(((t-0.4)/0.3)*155); g=((t-0.4)/0.3)*100; b=200*(1-((t-0.4)/0.3)); }
                else { r=255; g=100+(((t-0.7)/0.3)*155); b=((t-0.7)/0.3)*255; }
                
                var px = i * 4;
                rowImage.data[px] = r; rowImage.data[px+1] = g; rowImage.data[px+2] = b; rowImage.data[px+3] = 255;
            }
            diffCtx.putImageData(rowImage, 0, 0);
        }

        // --- Socket Events ---
        socket.on('connect', function() {
            // 接続時に現在の設定値をリクエスト
            socket.emit('get_config');
        });

        socket.on('config_update', function(cfg) {
            // サーバーから設定値を受信してスライダーに反映
            document.getElementById('sittingRange').value = cfg.sitting_thresh;
            document.getElementById('sittingVal').innerText = cfg.sitting_thresh;
            document.getElementById('marginRange').value = cfg.margin;
            document.getElementById('marginVal').innerText = cfg.margin;
        });

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
            
            statusEl.className = "";
            if (msg.status === "CALIBRATING...") {
                statusEl.classList.add("status-calib");
                csiChart.data.datasets[0].borderColor = '#d200ff';
                document.getElementById('btnCalib').innerText = "Running...";
                document.getElementById('btnCalib').className = "calib-btn calib-active";
            } else {
                document.getElementById('btnCalib').innerText = "AUTO CALIB (Empty)";
                document.getElementById('btnCalib').className = "calib-btn";
                
                if (msg.status === "WALKING") {
                    statusEl.classList.add("status-detected");
                    csiChart.data.datasets[0].borderColor = '#ff3333';
                } else if (msg.status === "SITTING") {
                    statusEl.classList.add("status-sitting");
                    csiChart.data.datasets[0].borderColor = '#00ffff';
                } else {
                    statusEl.classList.add("status-safe");
                    csiChart.data.datasets[0].borderColor = '#444444';
                }
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
        function updateThresholds() {
            var sitting = parseFloat(document.getElementById('sittingRange').value);
            var margin = parseFloat(document.getElementById('marginRange').value);
            
            document.getElementById('sittingVal').innerText = sitting;
            document.getElementById('marginVal').innerText = margin;
            
            socket.emit('update_config', {sitting_thresh: sitting, margin: margin});
        }

        function setMode(mode) {
            socket.emit('change_mode', {mode: mode});
            document.getElementById('btnLive').className = (mode === 'live') ? 'active' : 'inactive';
            document.getElementById('btnHistory').className = (mode === 'history') ? 'active' : 'inactive';
            document.getElementById('liveControls').style.display = (mode === 'live') ? 'flex' : 'none'; // RECボタンなどは常に表示に変更してもよい
            document.getElementById('historyControls').style.display = (mode === 'history') ? 'block' : 'none';
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
        
        # --- キャリブレーション & 設定 ---
        self.base_noise_level = 2.0  # ノイズフロア
        
        # 設定のロード (なければデフォルト)
        self.config = {
            'margin': 4.5,          # Empty判定ライン = Base + Margin
            'sitting_thresh': 25.0  # Walking判定ライン
        }
        self.load_config()
        
        self.is_calibrating = False
        self.calibration_buffer = []
        self.calibrated_today = False
        
        signal.signal(signal.SIGINT, self.stop_handler)
        signal.signal(signal.SIGTERM, self.stop_handler)

        self.app.add_url_rule('/', 'index', self.index)
        self.socketio.on_event('change_mode', self.handle_mode_change)
        self.socketio.on_event('toggle_logging', self.handle_logging_toggle)
        self.socketio.on_event('load_file', self.handle_file_load)
        self.socketio.on_event('start_calibration', self.handle_manual_calib)
        self.socketio.on_event('update_config', self.handle_config_update)
        self.socketio.on_event('get_config', self.send_config)

    def load_config(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r') as f:
                    saved_conf = json.load(f)
                    self.config.update(saved_conf)
                print(f"Config loaded: {self.config}")
            except Exception as e:
                print(f"Config load error: {e}")

    def save_config(self):
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(self.config, f)
        except Exception as e:
            print(f"Config save error: {e}")

    def stop_handler(self, signum, frame):
        print("Stopping Service...")
        self.save_config() # 終了時に保存
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

    def handle_config_update(self, data):
        # スライダーから受け取った値を反映
        self.config['margin'] = float(data['margin'])
        self.config['sitting_thresh'] = float(data['sitting_thresh'])
        # 即時保存
        self.save_config()

    def send_config(self):
        emit('config_update', self.config)

    def process_data(self, timestamp, amplitude):
        diff = np.zeros(SUB_CARRIERS)
        if self.prev_amp is not None:
            diff = np.abs(amplitude - self.prev_amp)
        self.prev_amp = amplitude

        self.amp_history.append(amplitude)
        status = "BUFFERING"
        score = 0.0
        
        # 現在の閾値を計算
        current_empty_thresh = self.base_noise_level + self.config['margin']
        current_sitting_thresh = self.config['sitting_thresh']
        
        if len(self.amp_history) == 50:
            history_np = np.array(self.amp_history)
            q75, q25 = np.percentile(history_np, [75, 25], axis=0)
            score = np.mean(q75 - q25)
            
            if self.is_calibrating:
                status = "CALIBRATING..."
                self.calibration_buffer.append(score)
                if len(self.calibration_buffer) > 200:
                    new_base = np.percentile(self.calibration_buffer, 95)
                    self.base_noise_level = new_base
                    self.is_calibrating = False
                    self.calibration_buffer = []
            else:
                # ★動的に設定された閾値を使用
                if score < current_empty_thresh:
                    status = "EMPTY"
                elif score < current_sitting_thresh:
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
            'threshold': current_empty_thresh
        })

    def worker(self):
        ser = None
        try:
            ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
        except Exception as e:
            print(f"Serial Error: {e}")

        while self.is_running:
            now = datetime.datetime.now()
            if now.hour == AUTO_CALIB_HOUR:
                if not self.calibrated_today:
                    self.is_calibrating = True
                    self.calibration_buffer = []
                    self.calibrated_today = True
            else:
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
