import numpy as np
import os
import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import classification_report, accuracy_score

# --- 設定 ---
FILES = {
    0: 'training_data/csi_empty.txt',
    1: 'training_data/csi_sitting.txt',
    2: 'training_data/csi_walking.txt'
}

HP_FILTER_ALPHA = 0.05
WINDOW_SIZE = 50

def extract_features(filename, label):
    features = []
    labels = []
    
    if not os.path.exists(filename):
        print(f"Warning: File not found -> {filename}")
        return features, labels

    avg_amp = None
    buffer = []
    all_frames = []
    
    print(f"Processing {filename} (Label {label})...")
    with open(filename, 'r') as f:
        for line in f:
            start_idx = line.find('[')
            end_idx = line.find(']')
            if start_idx == -1 or end_idx == -1: continue
            
            data_str = line[start_idx+1 : end_idx]
            try:
                vals = [int(x) for x in data_str.split(',') if x.strip()]
            except ValueError:
                continue
            
            if len(vals) != 384: continue
            
            c = np.array(vals)
            amp = np.sqrt(c[0::2]**2 + c[1::2]**2)
            
            if avg_amp is None:
                avg_amp = amp
            else:
                avg_amp = (avg_amp * (1.0 - HP_FILTER_ALPHA)) + (amp * HP_FILTER_ALPHA)
            
            clean_amp = np.abs(amp - avg_amp)
            all_frames.append(clean_amp)

    # 最初と最後の100フレーム(約5〜10秒)を捨てて、純粋な状態だけを抽出
    if len(all_frames) > 200:
        all_frames = all_frames[100:-100]

    # 特徴量の抽出 (ウィンドウをスライドさせながら計算)
    for i in range(0, len(all_frames) - WINDOW_SIZE, 10): # 10フレームずつずらす（データ量増加）
        buf_np = np.array(all_frames[i : i + WINDOW_SIZE]) # shape: (50, 192)
        
        # 1. 時間方向の振幅の平均変動 (全体のエネルギー)
        frame_means = np.mean(buf_np, axis=1)
        f_mean = np.mean(frame_means) * 10.0
        f_std = np.std(frame_means) * 10.0
        f_max = np.max(frame_means) * 10.0
        f_min = np.min(frame_means) * 10.0
        f_range = f_max - f_min
        
        # 2. サブキャリアごとの分散 (空間的な揺らぎ)
        # 人が動くと特定のサブキャリアだけが大きく乱れる性質を利用
        sub_vars = np.var(buf_np, axis=0)
        f_sub_var_mean = np.mean(sub_vars) * 10.0
        f_sub_var_max = np.max(sub_vars) * 10.0
        
        # 7つの特徴量セット
        features.append([f_mean, f_std, f_max, f_min, f_range, f_sub_var_mean, f_sub_var_max])
        labels.append(label)
                
    return features, labels

def main():
    X = []
    y = []
    
    for label, filepath in FILES.items():
        feat, lab = extract_features(filepath, label)
        X.extend(feat)
        y.extend(lab)
        
    X = np.array(X)
    y = np.array(y)
    
    print(f"\nTotal samples extracted: {len(X)}")
    
    if len(X) == 0:
        print("エラー: 抽出できるデータがありませんでした。")
        return

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
    
    print("Training Random Forest AI Model (V2)...")
    # AIの決定木を100本から200本に増やし、より複雑なパターンを学習させる
    model = RandomForestClassifier(n_estimators=200, max_depth=15, random_state=42, class_weight='balanced')
    model.fit(X_train, y_train)
    
    y_pred = model.predict(X_test)
    accuracy = accuracy_score(y_test, y_pred)
    
    print(f"\n=== AI Model Evaluation ===")
    print(f"Accuracy (正答率): {accuracy * 100:.2f}%\n")
    
    target_names = ['EMPTY (0)', 'SITTING (1)', 'WALKING (2)']
    print(classification_report(y_test, y_pred, target_names=target_names))
    
    model_filename = 'csi_model.pkl'
    joblib.dump(model, model_filename)
    print(f"Model saved successfully as '{model_filename}'!")

if __name__ == "__main__":
    main()
