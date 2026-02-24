import os
import glob
import zipfile
import datetime
from azure.storage.blob import BlobServiceClient

# --- 設定 ---
LOG_DIR = "/home/umatani/csi/Service/logs"
CONTAINER_NAME = "csi-logs"
# ★Azureポータルで取得した「接続文字列」をここに貼り付けます
AZURE_CONNECTION_STRING = "[Your connection string]"

def main():
    print(f"[{datetime.datetime.now()}] Upload job started.")
    
    # 現在の時刻から「今書き込み中のファイル名」を推測（例: csi_20240222_13.txt）
    current_hour_str = datetime.datetime.now().strftime('csi_%Y%m%d_%H.txt')
    
    try:
        blob_service_client = BlobServiceClient.from_connection_string(AZURE_CONNECTION_STRING)
        container_client = blob_service_client.get_container_client(CONTAINER_NAME)
    except Exception as e:
        print(f"Azure Connection Error: {e}")
        return

    # logsフォルダ内のすべてのtxtファイルを検索
    txt_files = glob.glob(os.path.join(LOG_DIR, "csi_*.txt"))
    
    # ★ここを追加して、プログラムが認識しているファイル数を確認
    print(f"Found {len(txt_files)} files in {LOG_DIR}")
    
    for txt_path in txt_files:
        filename = os.path.basename(txt_path)
        
        # 今まさに書き込んでいる最中のファイルはスキップ
        if filename == current_hour_str:
            continue
            
        print(f"Processing: {filename}...")
        zip_path = txt_path.replace('.txt', '.zip')
        zip_filename = os.path.basename(zip_path)
        
        try:
            # 1. ZIP圧縮 (ZIP_DEFLATEDでサイズを劇的に小さくする)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                zipf.write(txt_path, arcname=filename)
            
            # 2. Azure Blobへアップロード
            blob_client = container_client.get_blob_client(zip_filename)
            with open(zip_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)
            print(f"  -> Uploaded to Azure: {zip_filename}")
            
            # 3. アップロード成功後、ローカルのtxtとzipを削除（SDカードの容量を空ける）
            os.remove(txt_path)
            os.remove(zip_path)
            print(f"  -> Local files deleted.")
            
        except Exception as e:
            print(f"  -> Error processing {filename}: {e}")
            # エラーが起きた場合はファイルを消さずに残す（次回リトライ）

    print("Upload job finished.\n")

if __name__ == "__main__":
    main()
