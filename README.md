# Wi-Fi-CSI-Radar-Service

Wi-Fiセンシング・分析システム 構築マニュアル 

1. ハードウェア構成
   ● 送信機 (Tx): ESP32 (1台)
     ○ 役割: Wi-Fiパケット（Ping）をひたすら送信する
     ○ 接続: モバイルバッテリーやUSB充電器（PC不要）
   
     ○ FW: csi_send
   ● 受信機 (Rx): ESP32 (1台)
   　○ 役割: パケットを受信し、CSI（波形データ）をシリアル通信で送る
   　○ 接続: Raspberry Pi 5のUSBポート（例: /dev/ttyUSB0）
   　○ FW: csi_recv
   ● 解析サーバー: Raspberry Pi 5
   　○ 役割: データの可視化、有人判定、ログ保存、Web公開

3. ソフトウェア環境の準備
   Raspberry Pi OS (Bookworm) に必要なライブラリをインストールします。
   　> sudo apt update sudo apt install python3-numpy python3-matplotlib python3-serial python3-flask python3-gevent
   # Webサーバー用（必要な場合）
     > sudo pip install flask-socketio --break-system-packages
   # リモートアクセス用
     > curl -fsSL https://tailscale.com/install.sh | sh
