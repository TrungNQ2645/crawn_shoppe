import requests
import random
import pandas as pd
from datetime import datetime
import os
import time
import re
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv

# --- CẤU HÌNH SCRIPT ---
load_dotenv() # Tải các biến môi trường từ file .env

# CHỈ CẦN DÁN URL SẢN PHẨM MUỐN THEO DÕI VÀO ĐÂY
URLS_TO_TRACK = [
    "https://tiki.vn/dien-thoai-poco-c75-8gb-256gb-hang-chinh-hang-dk-p278098703.html?spid=278098705",
    "https://tiki.vn/dien-thoai-samsung-galaxy-a36-5g-hang-chinh-hang-p277466537.html?pid=277466543"
]

# Đọc thông tin proxy an toàn từ biến môi trường
PROXY_INFO = os.getenv("MY_PROXY_INFO")
if not PROXY_INFO:
    print("❌ LỖI: Không tìm thấy thông tin proxy. Hãy chắc chắn bạn đã tạo file .env và điền đúng biến MY_PROXY_INFO.")
    exit() # Thoát chương trình nếu không có proxy

def extract_ids_from_url(url):
    """Bóc tách product_id và spid tự động từ URL Tiki."""
    match = re.search(r'-p(\d+)\.html\?spid=(\d+)', url)
    if match:
        return {'product_id': match.group(1), 'spid': match.group(2)}
    print(f"⚠️ Không thể bóc tách ID từ URL: {url}")
    return None

def get_guest_token(session):
    """Lấy x-guest-token động bằng cách truy cập trang chủ Tiki."""
    try:
        print("ℹ️ Đang lấy guest token...")
        # Sử dụng một User-Agent đơn giản cho yêu cầu này
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        # Truy cập một trang bất kỳ để được cấp token trong cookie
        response = session.get("https://tiki.vn/", headers=headers, timeout=15)
        response.raise_for_status()
        token = session.cookies.get("tiki_guest_token")
        if token:
            print("✅ Đã lấy token thành công!")
            return token
        print("⚠️ Không tìm thấy tiki_guest_token trong cookie.")
        return None
    except requests.RequestException as e:
        print(f"❌ Lỗi khi lấy token: {e}")
        return None


def get_product_data_api(session, product_id, spid, guest_token):
    """Gửi yêu cầu đến API của Tiki với token động để lấy dữ liệu."""
    # URL API của Tiki, được cập nhật với tham số version
    api_url = f"https://tiki.vn/api/v2/products/{product_id}?platform=web&spid={spid}&version=3"

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
        'x-guest-token': guest_token, # Sử dụng token động đã lấy được
        'Referer': f"https://tiki.vn/-p{product_id}.html", # Thêm Referer để giả lập hành vi người dùng: truy cập từ trang sản phẩm
    }
    proxies = {"http": f"http://{PROXY_INFO}", "https": f"http://{PROXY_INFO}"}

    print(f"🔄 [API] Đang lấy dữ liệu cho sản phẩm ID: {product_id}")
    try:
        response = session.get(api_url, headers=headers, proxies=proxies, timeout=20)
        response.raise_for_status() # Dừng lại nếu có lỗi HTTP (4xx, 5xx)
        data = response.json()

        # Trích xuất dữ liệu dựa trên cấu trúc JSON thực tế
        return {
            'Timestamp': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            'SKU': data.get('sku'),
            'Name': data.get('name'),
            'Brand': data.get('brand', {}).get('name'),
            'Price': data.get('price'),
            'ListPrice': data.get('list_price'),
            'DiscountRate': data.get('discount_rate'),
            'Seller': data.get('current_seller', {}).get('name'),
            'StockStatus': data.get('inventory_status'),
            'StockQuantity': data.get('stock_item', {}).get('qty'),
            'Rating': data.get('rating_average'),
            'ReviewCount': data.get('review_count'),
            'SoldQuantityText': data.get('quantity_sold', {}).get('text'),
            'SoldQuantityValue': data.get('quantity_sold', {}).get('value'),
            'URL': data.get('short_url')
        }
    except requests.exceptions.HTTPError as e:
        print(f"❌ Lỗi HTTP {e.response.status_code}. Có thể sai token, sai URL API hoặc đã bị chặn.")
        return None
    except Exception as e:
        print(f"❌ Lỗi không xác định khi xử lý dữ liệu: {e}")
        return None
    
def save_to_csv(data, filename="price_history_api.csv"):
    """Lưu một dictionary dữ liệu vào file CSV."""
    if not data or data.get('Price') is None:
        print("➡️ Dữ liệu không hợp lệ, bỏ qua việc lưu.")
        return
    
    # Chuyển đổi dictionary thành một DataFrame của Pandas
    df = pd.DataFrame([data])
    
    # Kiểm tra xem file đã tồn tại chưa để quyết định có ghi header không
    file_exists = os.path.isfile(filename)
    
    # Dùng mode 'a' (append) để ghi nối vào cuối file, không ghi đè
    df.to_csv(filename, mode='a', header=not file_exists, index=False, encoding='utf-8-sig')
    print(f"✅ Đã lưu: {data['Name']} - Giá: {data['Price']}đ")

def price_tracking_job():
    """Hàm điều phối toàn bộ quy trình làm việc."""
    print(f"\n--- BẮT ĐẦU PHIÊN LÀM VIỆC LÚC: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    
    # Sử dụng 'with' để quản lý session, đảm bảo nó tự động xử lý cookie và đóng kết nối đúng cách
    with requests.Session() as session:
        # Lấy guest token một lần cho cả phiên làm việc
        guest_token = get_guest_token(session)
        if not guest_token:
            print("❌ Không thể tiếp tục nếu không có guest token. Dừng phiên làm việc.")
            return

        # Lặp qua từng URL trong danh sách để theo dõi
        for url in URLS_TO_TRACK:
            ids = extract_ids_from_url(url)
            if ids:
                product_data = get_product_data_api(session, ids['product_id'], ids['spid'], guest_token)
                if product_data:
                    save_to_csv(product_data)
            
            # Tạm dừng ngẫu nhiên giữa các lần gọi để tránh bị chặn
            sleep_time = random.uniform(5, 10)
            print(f"--- Tạm nghỉ {sleep_time:.2f} giây... ---")
            time.sleep(sleep_time)
            
    print(f"--- PHIÊN LÀM VIỆC KẾT THÚC. ---\n")

# --- ĐIỂM KHỞI ĐỘNG CHƯƠNG TRÌNH ---
if __name__ == "__main__":
    scheduler = BlockingScheduler(timezone="Asia/Ho_Chi_Minh")
    
    # Lập lịch để chạy hàm `price_tracking_job` vào 9 giờ sáng mỗi ngày
    scheduler.add_job(price_tracking_job, 'cron', hour=9, minute=0)
    
    print("🚀 HỆ THỐNG THEO DÕI GIÁ (API-FIRST) ĐÃ KHỞI ĐỘNG.")
    print("Hãy để cửa sổ terminal này chạy nền. Nhấn Ctrl+C để thoát.")
    
    # Chạy tác vụ lần đầu tiên ngay khi khởi động
    price_tracking_job() 
    
    # Bắt đầu bộ lập lịch
    scheduler.start()
