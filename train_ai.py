import numpy as np
import re
import os
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

# --- 設定 ---
# 取得済みのログファイル名を指定してください
FILES = {
    0: 'logs/csi_empty.txt',   # ラベル0: EMPTY (不在)
    1: 'logs/csi_sitting.txt', # ラベル1: SITTING (着席)
    2: 'logs/csi_walking.txt'  # ラベル2: WALKING (歩行)
}

# フィルタ設定（稼働中の csi_service_ultimate.py と合わせる）
HP_FILTER_ALPHA = 0.05
WINDOW_SIZE = 50

def extract_features(filename, label):
    """ログファイルから波形データを読み込み、特徴量を抽出する"""
    features = []
    labels = []
    
    if not os.path.exists(filename):
        print(f"Warning: File not found -> {filename}")
        return features, labels

    avg_amp = None
    buffer = []
    
    print(f"Processing {filename} (Label {label})...")
    with open(filename, 'r') as f:
        for line in f:
            # '[' と ']' の位置を探して、その中身だけを確実に切り出す
            start_idx = line.find('[')
            end_idx = line.find(']')
            if start_idx == -1 or end_idx == -1:
                continue
            
            # 中身の文字列を取得
            data_str = line[start_idx+1 : end_idx]
            
            # ★ ここに try-except を追加して、エラーを回避する ★
            try:
                vals = [int(x) for x in data_str.split(',') if x.strip()]
            except ValueError:
                # シリアル通信の文字化け等で数値変換できない場合はスキップ
                continue
            
            if len(vals) != 384: 
                continue
            
            # (以下、c = np.array(vals) ... と続く)            
            c = np.array(vals)
            amp = np.sqrt(c[0::2]**2 + c[1::2]**2)
            
            # 1. 稼働中システムと同じ DC除去フィルタ をシミュレート
            if avg_amp is None:
                avg_amp = amp
            else:
                avg_amp = (avg_amp * (1.0 - HP_FILTER_ALPHA)) + (amp * HP_FILTER_ALPHA)
            
            clean_amp = np.abs(amp - avg_amp)
            buffer.append(clean_amp)
            
            # 2. 50フレーム溜まったら「特徴量」を計算
            if len(buffer) == WINDOW_SIZE:
                buf_np = np.array(buffer)
                
                # --- 特徴量エンジニアリング ---
                # 単なる1つのスコアではなく、波形のさまざまな特徴をAIに教える
                f_mean = np.mean(buf_np) * 10.0      # 平均的な揺れの大きさ (現在使っているスコア)
                f_std = np.std(buf_np) * 10.0        # 揺れの激しさ（ばらつき）
                f_max = np.max(buf_np) * 10.0        # 瞬間的な最大値
                q75, q25 = np.percentile(buf_np, [75, 25], axis=0)
                f_iqr = np.mean(q75 - q25) * 10.0    # 安定した変動幅 (IQR)
                
                # 4つの特徴を1セットとして追加
                features.append([f_mean, f_std, f_max, f_iqr])
                labels.append(label)
                
                # データを半分（25フレーム）ずらして次を計算（データ増水・オーバーラップ処理）
                buffer = buffer[25:]
                
    return features, labels

def main():
    X = [] # 特徴量データ
    y = [] # 正解ラベル
    
    # 全ファイルからデータを抽出
    for label, filepath in FILES.items():
        feat, lab = extract_features(filepath, label)
        X.extend(feat)
        y.extend(lab)
        
    X = np.array(X)
    y = np.array(y)
    
    print(f"\nTotal samples extracted: {len(X)}")
    
    # データを学習用(80%)とテスト用(20%)に分割
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    print("\nTraining Random Forest AI Model...")
    # ランダムフォレストの学習
    model = RandomForestClassifier(n_estimators=100, max_depth=10, random_state=42)
    model.fit(X_train, y_train)
    
    # テストデータで予測して精度を確認
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    
    print(f"\n=== AI Model Evaluation ===")
    print(f"Accuracy (正答率): {accuracy * 100:.2f}%\n")
    
    target_names = ['EMPTY (0)', 'SITTING (1)', 'WALKING (2)']
    print(classification_report(y_test, y_pred, target_names=target_names))
    
    # モデルの保存
    model_filename = 'csi_model.pkl'
    joblib.dump(model, model_filename)
    print(f"Model saved successfully as '{model_filename}'!")

if __name__ == "__main__":
    main()
