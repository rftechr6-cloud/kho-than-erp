import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, timezone
import plotly.express as px
import base64
import hashlib
import sqlite3
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import json
import requests
from contextlib import contextmanager
import threading
from streamlit_option_menu import option_menu
import streamlit.components.v1 as components
import time

# ==========================================
# CẤU HÌNH TỌA ĐỘ ĐỊA LÝ BẢN ĐỒ
# ==========================================
MAP_COORDS = {
    "Hà Nội": {"lat": 21.0285, "lon": 105.8542}, "Thái Nguyên": {"lat": 21.5942, "lon": 105.8482},
    "Bắc Ninh": {"lat": 21.5928, "lon": 106.0598}, "Bắc Giang": {"lat": 21.2731, "lon": 106.1946},
    "Hưng Yên": {"lat": 20.8532, "lon": 106.0583}, "Hải Dương": {"lat": 20.9370, "lon": 106.3146},
    "Hải Phòng": {"lat": 20.8449, "lon": 106.6881}, "Quảng Ninh": {"lat": 20.8561, "lon": 107.1361},
    "Khác": {"lat": 21.0, "lon": 105.8}
}

# ==========================================
# CÁC HÀM XỬ LÝ ĐỊNH DẠNG & BẢO MẬT
# ==========================================
def to_float(val):
    try:
        if pd.isna(val) or str(val).strip() == "": return 0.0
        if isinstance(val, bytes): val = val.decode('utf-8', 'ignore')
        clean_val = str(val).replace(",", "").replace("đ", "").replace("VNĐ", "").replace(" ", "")
        return float(clean_val)
    except: return 0.0

def to_int(val):
    try: return int(to_float(val))
    except: return 0

def fmt_vn(val):
    try: return f"{int(to_float(val)):,}".replace(",", ".")
    except: return "0"

def hash_password(password): return hashlib.sha256(password.encode()).hexdigest()

def parse_coords(coord_str):
    try:
        parts = str(coord_str).replace(" ", "").split(",")
        return float(parts[0]), float(parts[1])
    except: return 0.0, 0.0

# --- HỆ THỐNG GHI LOG ---
if 'sys_log' not in st.session_state: st.session_state.sys_log = []

def write_log(action, status, detail=""):
    time_str = datetime.now(timezone.utc).strftime('%H:%M:%S')
    icon = "✅" if status == "SUCCESS" else "❌"
    log_msg = f"[{time_str}] {icon} {action} | {detail}"
    st.session_state.sys_log.insert(0, log_msg)
    if len(st.session_state.sys_log) > 50: st.session_state.sys_log.pop()

# --- TRẠNG THÁI CHỈNH SỬA ---
if 'edit_t_id' not in st.session_state: st.session_state.edit_t_id = None
if 'edit_kh_id' not in st.session_state: st.session_state.edit_kh_id = None
if 'edit_tx_id' not in st.session_state: st.session_state.edit_tx_id = None
if 'edit_u_id' not in st.session_state: st.session_state.edit_u_id = None

# ==========================================
# GIAO TIẾP BOT ZALO OA API
# ==========================================
def send_zalo_notify(msg):
    """Hàm gửi tin nhắn Zalo tự động qua Zalo OA API"""
    try:
        with get_connection() as conn:
            conf = conn.cursor().execute("SELECT zalo_token, zalo_id, zalo_active FROM cau_hinh_in WHERE id=1").fetchone()
            if conf and to_int(conf[2]) == 1 and conf[0] and conf[1]:
                url = "https://openapi.zalo.me/v3.0/oa/message/cs"
                headers = {"access_token": conf[0], "Content-Type": "application/json"}
                payload = {"recipient": {"user_id": conf[1]}, "message": {"text": msg}}
                requests.post(url, headers=headers, json=payload, timeout=5)
    except Exception as e:
        write_log("Zalo Bot", "ERROR", str(e))

# ==========================================
# CÁC CALLBACKS XỬ LÝ DỮ LIỆU
# ==========================================
def cb_xoa_than(item_id):
    with get_connection() as c: c.execute("DELETE FROM loai_than WHERE id=?", (item_id,)); c.commit()
    write_log("Xóa loại than", "SUCCESS", f"ID: {item_id}")

def cb_xoa_khach(item_id):
    with get_connection() as c: c.execute("DELETE FROM khach_hang WHERE id=?", (item_id,)); c.commit()
    write_log("Xóa khách hàng", "SUCCESS", f"ID: {item_id}")

def cb_xoa_taixe(item_id):
    with get_connection() as c: c.execute("DELETE FROM nhan_vien WHERE id=?", (item_id,)); c.commit()
    write_log("Xóa tài xế", "SUCCESS", f"ID: {item_id}")

def cb_xoa_user(item_id):
    with get_connection() as c: c.execute("DELETE FROM users WHERE id=?", (item_id,)); c.commit()
    write_log("Xóa tài khoản", "SUCCESS", f"Đã xóa vĩnh viễn user có ID: {item_id}")

def cb_huy_don(don_id):
    try:
        with get_connection() as c:
            res = c.execute("SELECT id FROM don_hang WHERE id=?", (don_id,)).fetchone()
            if res:
                don_id = to_int(res[0])
                chi_tiet = pd.read_sql_query(f"SELECT loai_than_id, so_luong FROM chi_tiet_don_hang WHERE don_hang_id={don_id}", c.connection)
                for _, row in chi_tiet.iterrows():
                    c.execute("UPDATE loai_than SET ton_kho = ton_kho + ? WHERE id = ?", (to_float(row['so_luong']), to_int(row['loai_than_id'])))
                c.execute("DELETE FROM chi_tiet_don_hang WHERE don_hang_id=?", (don_id,))
                c.execute("DELETE FROM lich_su_thanh_toan WHERE don_hang_id=?", (don_id,))
            c.execute("DELETE FROM don_hang WHERE id=?", (don_id,))
            c.commit()
        write_log("Hủy đơn hàng", "SUCCESS", f"Hủy triệt để đơn mục ID: {don_id}")
    except Exception as e: write_log("Hủy đơn hàng", "ERROR", str(e))

# ==========================================
# THIẾT KẾ ĐỒ HỌA
# ==========================================
st.set_page_config(page_title=" Quản Lý Bãi Than", page_icon="🪨", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
    <style>
        html, body, [data-testid="stAppViewContainer"] { background-color: #f8fafc; font-family: "Inter", -apple-system, sans-serif; }
        .kpi-card { background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border-top: 4px solid #3b82f6; margin-bottom: 20px; }
        .kpi-label { font-size: 13px; color: #64748b; font-weight: 600; text-transform: uppercase; margin-bottom: 5px; }
        .kpi-value { font-size: 26px; color: #0f172a; font-weight: 800; }
        .border-green { border-top-color: #10b981; } .border-red { border-top-color: #ef4444; } .border-purple { border-top-color: #8b5cf6; }
        .text-green { color: #10b981; } .text-red { color: #ef4444; } .text-purple { color: #8b5cf6; }
        .main-header { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 25px; }
        .list-row { padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; align-items: center; display: flex;}
        div[data-testid="stButton"] button { padding: 4px 12px; font-size: 13px; border-radius: 6px; }
        .edit-box { background-color: #fffbeb; padding: 20px; border-radius: 8px; border: 1px solid #fde68a; border-left: 5px solid #f59e0b; margin-bottom: 20px;}
        .log-box { background: #1e293b; color: #10b981; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 12px; height: 300px; overflow-y: scroll; }
        .danger-zone { background-color: #fff1f2; border: 1px solid #fecdd3; padding: 20px; border-radius: 8px; border-left: 6px solid #e11d48; margin-top: 15px;}
    </style>
""", unsafe_allow_html=True)

# ==========================================
# KHỞI TẠO DỮ LIỆU & ĐỒNG BỘ
# ==========================================
try: SHEET_URL = st.secrets["sheet_url"]
except KeyError: st.error("Chưa cấu hình Két sắt bảo mật."); st.stop()

@st.cache_resource
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(st.secrets["google_key"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))

def check_and_add_column(cursor, table, col_name, col_def):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [col[1] for col in cursor.fetchall()]
    if col_name not in cols: cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

@st.cache_resource
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(st.secrets["google_key"])
    creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
    return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))

def check_and_add_column(cursor, table, col_name, col_def):
    cursor.execute(f"PRAGMA table_info({table})")
    cols = [col[1] for col in cursor.fetchall()]
    if col_name not in cols: cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}")

# BỎ HOÀN TOÀN @st.cache_resource ĐỂ CHỐNG KẸT DỮ LIỆU KHI REBOOT
def init_local_db(force_pull=False):
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    
    # Tối ưu: Nếu SQLite đã có dữ liệu, không cần kéo lại từ GSheets (trừ khi bạn bấm nút Ép buộc)
    if not force_pull:
        try:
            check = pd.read_sql_query("SELECT COUNT(*) as cnt FROM users", conn)
            if check.iloc[0]['cnt'] > 0: return conn
        except: pass
        
    # Kéo dữ liệu tươi từ Google Sheets
    try:
        client = get_gspread_client()
        sheet = client.open_by_url(SHEET_URL)
        for ws in sheet.worksheets():
            data = ws.get_all_records()
            if data:
                df = pd.DataFrame(data)
                df = df.replace('', pd.NA).dropna(how='all')
                if 'id' in df.columns: 
                    df['id'] = pd.to_numeric(df['id'], errors='coerce')
                    df = df.dropna(subset=['id'])
                    df['id'] = df['id'].astype(int) # FIX LỖI 1.0 THÀNH SỐ NGUYÊN 1
                if not df.empty:
                    df.to_sql(ws.title.strip(), conn, if_exists='replace', index=False)
    except: pass
    return conn

init_local_db()

# --- BỘ ĐẾM GIỜ CHỐNG QUÁ TẢI GOOGLE (DEBOUNCER) ---
sync_timer = None

def background_sync_task():
    try:
        bg_conn = sqlite3.connect("kho_than.db", check_same_thread=False)
        client = get_gspread_client()
        sheet = client.open_by_url(SHEET_URL)
        tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", bg_conn)
        
        for table_name in tables['name']:
            if table_name == "sqlite_sequence": continue
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", bg_conn)
            
            # BẢO VỆ TỐI THƯỢNG: TUYỆT ĐỐI KHÔNG XÓA GOOGLE SHEETS NẾU DỮ LIỆU TRỐNG
            if df.empty: continue 
            
            for col in df.select_dtypes(include=['datetime64', 'datetimetz']).columns: df[col] = df[col].astype(str)
            try: ws = sheet.worksheet(table_name)
            except gspread.WorksheetNotFound: ws = sheet.add_worksheet(title=table_name, rows=100, cols=20)
            
            # CHÈN ĐÈ DỮ LIỆU THAY VÌ DÙNG LỆNH CLEAR GÂY MẤT DỮ LIỆU
            values = [df.columns.values.tolist()] + df.fillna("").astype(str).values.tolist()
            empty_row = [""] * len(df.columns)
            values.extend([empty_row] * 20) # Chèn 20 dòng trắng để xóa rác tự nhiên
            ws.update(values=values, range_name="A1")
            time.sleep(1) # Chống nghẽn API Google
    except: pass 
    finally: bg_conn.close()

def trigger_sync():
    global sync_timer
    if sync_timer is not None: sync_timer.cancel()
    sync_timer = threading.Timer(3.0, background_sync_task)
    sync_timer.daemon = True
    sync_timer.start()

@contextmanager
def get_connection():
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    class ConnectionWrapper:
        def __init__(self, c): self.c = c
        def commit(self):
            self.c.commit()
            trigger_sync() # Đồng bộ sau 3s khi lưu xong
        def cursor(self): return self.c.cursor()
        def execute(self, q, p=None): return self.c.execute(q, p) if p else self.c.execute(q)
        @property
        def connection(self): return self.c
    try: yield ConnectionWrapper(conn)
    finally: conn.close()

def get_next_id(table, cursor):
    try:
        cursor.execute(f"SELECT MAX(id) FROM {table}")
        val = cursor.fetchone()[0]
        return 1 if pd.isna(val) or val is None or str(val).strip() == '' else to_int(val) + 1
    except: return 1

def sinh_ma_don_hang_theo_ngay(date_str):
    with get_connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM don_hang WHERE ngay_ban=?", (date_str,)).fetchone()[0]
        return f"{date_str.replace('-', '')}-{count + 1:03d}"

def init_database():
    # Dùng kết nối nguyên thủy để khởi tạo DB không kích hoạt đồng bộ (chống xóa dữ liệu oan)
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username VARCHAR(255) UNIQUE, password VARCHAR(255), role VARCHAR(50), status VARCHAR(50))''')
    cursor.execute("SELECT * FROM users WHERE username='admin'")
    if not cursor.fetchone(): 
        uid = get_next_id('users', cursor)
        cursor.execute("INSERT INTO users (id, username, password, role, status) VALUES (?, ?, ?, 'admin', 'Đã duyệt')", (uid, 'admin', hash_password(st.secrets["admin_pass"])))
    conn.commit()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS loai_than (id INTEGER PRIMARY KEY, ten_than VARCHAR(255) UNIQUE, gia_nhap_mac_dinh DOUBLE, gia_mac_dinh DOUBLE, ton_kho DOUBLE, nguoi_tao VARCHAR(255))''')
    try: cursor.execute("ALTER TABLE loai_than ADD COLUMN don_vi_tinh VARCHAR(50) DEFAULT 'kg'")
    except: pass
    try: cursor.execute("ALTER TABLE loai_than ADD COLUMN he_so_kg DOUBLE DEFAULT 1.0")
    except: pass
    conn.commit()
    
    # 3. BẢNG KHÁCH HÀNG
    cursor.execute('''CREATE TABLE IF NOT EXISTS khach_hang (id INTEGER PRIMARY KEY, ma_khach_hang VARCHAR(50) UNIQUE, ten_khach VARCHAR(255) UNIQUE, sdt VARCHAR(50), dia_chi TEXT, khu_vuc VARCHAR(255), link_google_maps TEXT, nguoi_tao VARCHAR(255))''')
    try: cursor.execute("ALTER TABLE khach_hang ADD COLUMN lat DOUBLE DEFAULT 0.0")
    except: pass
    try: cursor.execute("ALTER TABLE khach_hang ADD COLUMN lon DOUBLE DEFAULT 0.0")
    except: pass
    try: cursor.execute("ALTER TABLE khach_hang ADD COLUMN han_muc_no DOUBLE DEFAULT 0.0")
    except: pass
    try: cursor.execute("ALTER TABLE khach_hang ADD COLUMN ghi_chu_kh TEXT")
    except: pass
    conn.commit()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS nhan_vien (id INTEGER PRIMARY KEY, ten_nhan_vien VARCHAR(255) UNIQUE, sdt VARCHAR(50), chuc_vu VARCHAR(100))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gia_rieng (khach_hang_id INTEGER, loai_than_id INTEGER, gia_uu_dai DOUBLE, PRIMARY KEY (khach_hang_id, loai_than_id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_gia (id INTEGER PRIMARY KEY, khach_hang_id INTEGER, loai_than_id INTEGER, gia_cu DOUBLE, gia_moi DOUBLE, ngay_thay_doi TIMESTAMP)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS don_hang (id INTEGER PRIMARY KEY, ma_don_hien_thi VARCHAR(50) UNIQUE, khach_hang_id INTEGER, nhan_vien_id INTEGER, ngay_ban DATE, thoi_gian_tao TIMESTAMP, da_thanh_toan INTEGER, trang_thai_giao VARCHAR(100), hinh_thuc_thanh_toan VARCHAR(100), ghi_chu TEXT, giao_gap INTEGER, tong_tien DOUBLE, tien_da_tra DOUBLE, tien_con_no DOUBLE, nguoi_tao VARCHAR(255))''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS chi_tiet_don_hang (id INTEGER PRIMARY KEY, don_hang_id INTEGER, loai_than_id INTEGER, so_luong DOUBLE, don_gia DOUBLE)''')
    try: cursor.execute("ALTER TABLE chi_tiet_don_hang ADD COLUMN don_gia_von DOUBLE DEFAULT 0.0")
    except: pass
    conn.commit()
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS nhap_hang (id INTEGER PRIMARY KEY, loai_than_id INTEGER, ngay_nhap DATE, so_luong DOUBLE, don_gia_nhap DOUBLE, nguoi_tao VARCHAR(255), xuong_nhap VARCHAR(255))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_thanh_toan (id INTEGER PRIMARY KEY, don_hang_id INTEGER, so_tien_tra DOUBLE, hinh_thuc VARCHAR(100), ngay_tra TIMESTAMP, ghi_chu TEXT, nguoi_tao VARCHAR(255))''')
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS cau_hinh_in (id INTEGER PRIMARY KEY, ten_cua_hang VARCHAR(255), so_dien_thoai VARCHAR(50), thong_tin_ngan_hang TEXT, kho_giay_mac_dinh VARCHAR(100))''')
    try: cursor.execute("ALTER TABLE cau_hinh_in ADD COLUMN zalo_token TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE cau_hinh_in ADD COLUMN zalo_id TEXT")
    except: pass
    try: cursor.execute("ALTER TABLE cau_hinh_in ADD COLUMN zalo_active INTEGER DEFAULT 0")
    except: pass
    cursor.execute("INSERT OR IGNORE INTO cau_hinh_in (id, thong_tin_ngan_hang) VALUES (1, 'Chưa cài đặt')")
    
    cursor.execute('''CREATE TABLE IF NOT EXISTS so_quy (id INTEGER PRIMARY KEY, ngay DATE, thoi_gian TIMESTAMP, loai_phieu VARCHAR(50), so_tien DOUBLE, hang_muc VARCHAR(255), nguoi_tao VARCHAR(255), ghi_chu TEXT)''')
    conn.commit()
    conn.close()

init_database()

# ==========================================
# ĐĂNG NHẬP & PHÂN QUYỀN
# ==========================================
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'current_user' not in st.session_state: st.session_state.current_user = None
if 'user_role' not in st.session_state: st.session_state.user_role = None
if 'cart' not in st.session_state: st.session_state.cart = []
if 'last_order_id' not in st.session_state: st.session_state.last_order_id = None

now_dt = datetime.now(timezone.utc) + timedelta(hours=7)
today_str = now_dt.strftime('%Y-%m-%d')

if not st.session_state.logged_in:
    st.markdown("<div class='main-header'><h1 style='text-align:center;'>HỆ THỐNG QUẢN TRỊ KHO THAN CLOUD</h1></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        t_log, t_reg = st.tabs(["🔐 Đăng Nhập", "📝 Đăng Ký Tài Khoản"])
        with t_log:
            with st.form("login_form"):
                user = st.text_input("Tài khoản:"); pwd = st.text_input("Mật khẩu:", type="password")
                if st.form_submit_button("Đăng Nhập ", type="primary"):
                    with get_connection() as conn:
                        res = conn.cursor().execute("SELECT role, status FROM users WHERE username=? AND password=?", (user, hash_password(pwd))).fetchone()
                        if res:
                            if res[1] == "Đã duyệt" or res[0] == "admin":
                                st.session_state.logged_in = True; st.session_state.current_user = user; st.session_state.user_role = res[0]; st.rerun()
                            else: st.error("Tài khoản đang chờ Admin phê duyệt.")
                        else: st.error("Sai tài khoản hoặc mật khẩu!")
        with t_reg:
            with st.form("reg_form"):
                n_user = st.text_input("Tài khoản muốn tạo:"); n_pwd = st.text_input("Mật khẩu:", type="password"); n_pwd2 = st.text_input("Nhập lại mật khẩu:", type="password")
                if st.form_submit_button("Gửi Yêu Cầu Đăng Ký"):
                    if n_pwd != n_pwd2: st.error("Mật khẩu không khớp!")
                    elif len(n_user) < 3: st.error("Tài khoản tối thiểu phải từ 3 ký tự!")
                    else:
                        try:
                            with get_connection() as conn:
                                conn.cursor().execute("INSERT INTO users (id, username, password, role, status) VALUES (?, ?, ?, 'laixe', 'Chờ duyệt')", (get_next_id('users', conn.cursor()), n_user, hash_password(n_pwd)))
                                conn.commit()
                            st.success("Đăng ký thành công! Vui lòng báo Admin duyệt."); st.rerun()
                        except: st.error("Tài khoản này đã tồn tại trên hệ thống!")
    st.stop()
ROLE_MENUS = {
    "admin": ["Thống Kê (HQ)", "Lập Đơn & In Phiếu", "Giao Hàng & Vận Tải", "Sổ Quản Lý Nợ", "Sổ Quỹ & Lãi Lỗ", "Quản Lý Tồn Kho", "Lịch Sử Đơn Hàng", "Cài Đặt Hệ Thống"],
    "manager": ["Thống Kê (HQ)", "Lập Đơn & In Phiếu", "Sổ Quản Lý Nợ", "Sổ Quỹ & Lãi Lỗ", "Quản Lý Tồn Kho", "Lịch Sử Đơn Hàng", "Cài Đặt Hệ Thống"], 
    "ketoan": ["Thống Kê (HQ)", "Lập Đơn & In Phiếu", "Sổ Quản Lý Nợ", "Sổ Quỹ & Lãi Lỗ", "Quản Lý Tồn Kho", "Lịch Sử Đơn Hàng"],
    "laixe": ["Giao Hàng & Vận Tải", "Lịch Sử Đơn Hàng"]
}
ROLE_ICONS = {
    "admin": ['bar-chart-fill', 'receipt-cutoff', 'truck', 'wallet-fill', 'cash-stack', 'box-seam', 'clock-history', 'gear-fill'],
    "manager": ['bar-chart-fill', 'receipt-cutoff', 'wallet-fill', 'cash-stack', 'box-seam', 'clock-history', 'gear-fill'], 
    "ketoan": ['bar-chart-fill', 'receipt-cutoff', 'wallet-fill', 'cash-stack', 'box-seam', 'clock-history'],
    "laixe": ['truck', 'clock-history']
}

current_role = st.session_state.user_role
if current_role not in ROLE_MENUS: current_role = "laixe" 
is_admin = (current_role == 'admin')
is_manager = (current_role == 'manager')
can_edit = is_admin 

with st.sidebar:
    st.markdown(f"### 🪨 TRẠM VẬN HÀNH\n• Người dùng: **{st.session_state.current_user}**\n• Quyền hạn: **{current_role.upper()}**")
    if st.button("🚪 Đăng Xuất"): st.session_state.clear(); st.rerun()
    st.markdown("---")
    menu = option_menu("CHỨC NĂNG CHÍNH", ROLE_MENUS[current_role], icons=ROLE_ICONS[current_role], menu_icon="boxes", default_index=0)

# ==========================================
# PHÂN HỆ 1: THỐNG KÊ (HQ DASHBOARD)
# ==========================================
if menu == "Thống Kê (HQ)":
    col_head1, col_head2 = st.columns([3, 1])
    with col_head1: st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📊 PHÂN HỆ GIÁM SÁT KINH DOANH TỔNG THỂ</h1></div>", unsafe_allow_html=True)
    with col_head2:
        st.markdown("<br>", unsafe_allow_html=True)
        # BỎ DÒNG NÀY: auto_refresh = st.checkbox("🔄 Tự động làm mới (30s)", value=True)
        auto_refresh = True # Ép cứng biến luôn luôn Bật (chạy ngầm)
        st.markdown(f"<div style='font-size:13px; color:#10b981; font-weight:bold; text-align:right;'>⏳ Cập nhật lúc: {now_dt.strftime('%H:%M:%S | %d/%m/%Y')}</div>", unsafe_allow_html=True)

    time_filter = st.radio("⏳ Mốc thời gian:", ["Hôm nay", "Tuần này", "Tháng này", "Tất cả thời gian"], horizontal=True)

    with get_connection() as conn:
        df_flat = pd.read_sql_query('''SELECT dh.id as don_id, dh.thoi_gian_tao, dh.da_thanh_toan, dh.trang_thai_giao, dh.ngay_ban, kh.ten_khach, kh.khu_vuc, kh.lat, kh.lon, lt.ten_than, lt.gia_nhap_mac_dinh, ctdh.so_luong, ctdh.don_gia, ctdh.don_gia_von, (ctdh.so_luong * ctdh.don_gia) as thanh_tien FROM don_hang dh JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id JOIN khach_hang kh ON dh.khach_hang_id = kh.id JOIN loai_than lt ON ctdh.loai_than_id = lt.id''', conn.connection)
        df_group = pd.read_sql_query('''SELECT dh.id as don_id, dh.ma_don_hien_thi, dh.thoi_gian_tao, dh.trang_thai_giao, dh.giao_gap, dh.tong_tien, dh.tien_con_no, dh.nguoi_tao, kh.ma_khach_hang, kh.ten_khach, nv.ten_nhan_vien FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id ORDER BY dh.id DESC''', conn.connection)

    if not df_flat.empty:
        for c in ['don_gia', 'don_gia_von', 'so_luong', 'thanh_tien']: df_flat[c] = df_flat[c].apply(to_float)
        df_flat['Date'] = pd.to_datetime(df_flat['thoi_gian_tao'])
        df_flat['loi_nhuan'] = (df_flat['don_gia'] - df_flat['don_gia_von']) * df_flat['so_luong']
        if time_filter == "Hôm nay": df_flat = df_flat[df_flat['Date'].dt.date == now_dt.date()]
        elif time_filter == "Tuần này": df_flat = df_flat[df_flat['Date'].dt.date >= (now_dt - timedelta(days=now_dt.weekday())).date()]
        elif time_filter == "Tháng này": df_flat = df_flat[(df_flat['Date'].dt.month == now_dt.month) & (df_flat['Date'].dt.year == now_dt.year)]
        
    if not df_group.empty:
        for c in ['tong_tien', 'tien_con_no']: df_group[c] = df_group[c].apply(to_float)
        df_group['Date'] = pd.to_datetime(df_group['thoi_gian_tao'])
        if time_filter == "Hôm nay": df_group = df_group[df_group['Date'].dt.date == now_dt.date()]
        elif time_filter == "Tuần này": df_group = df_group[df_group['Date'].dt.date >= (now_dt - timedelta(days=now_dt.weekday())).date()]
        elif time_filter == "Tháng này": df_group = df_group[(df_group['Date'].dt.month == now_dt.month) & (df_group['Date'].dt.year == now_dt.year)]

    total_rev = df_group['tong_tien'].sum() if not df_group.empty else 0
    debt_df = df_group[df_group['trang_thai_giao'] == 'Đã hoàn thành'] if not df_group.empty else pd.DataFrame()
    debt_rev = debt_df['tien_con_no'].sum() if not debt_df.empty else 0
    pending_df = df_group[df_group['trang_thai_giao'] != 'Đã hoàn thành'] if not df_group.empty else pd.DataFrame()
    pending_count = pending_df['don_id'].nunique() if not pending_df.empty else 0
    total_orders = df_group['don_id'].nunique() if not df_group.empty else 0
    total_profit = df_flat['loi_nhuan'].sum() if not df_flat.empty else 0
    
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown(f"<div class='kpi-card'><div class='kpi-label'>📦 Tổng Đơn Cần Giao</div><div class='kpi-value'>{total_orders} đơn <span style='font-size:13px;color:#64748b;'>({pending_count} chờ)</span></div></div>", unsafe_allow_html=True)
    with c2: st.markdown(f"<div class='kpi-card border-green'><div class='kpi-label'>💵 Doanh Thu Tạm Tính</div><div class='kpi-value text-green'>{fmt_vn(total_rev)} đ</div></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='kpi-card border-purple'><div class='kpi-label'>📈 Lợi Nhuận Gộp bãi</div><div class='kpi-value text-purple'>{fmt_vn(total_profit)} đ</div></div>", unsafe_allow_html=True)
    with c4: st.markdown(f"<div class='kpi-card border-red'><div class='kpi-label'>🛑 Tổng Công Nợ</div><div class='kpi-value text-red'>{fmt_vn(debt_rev)} đ</div></div>", unsafe_allow_html=True)

    st.markdown("### 🕒 Giám Sát Tiến Độ Giao Hàng")
    if not df_group.empty:
        df_cho = df_group[df_group['trang_thai_giao'] != 'Đã hoàn thành'].copy()
        if df_cho.empty: st.success("✅ Tuyệt vời! Không có lệnh xuất kho nào bị tồn đọng quá thời gian.")
        else:
            current_time = pd.Timestamp.now(tz=timezone.utc) + pd.Timedelta(hours=7)
            for _, r in df_cho.iterrows():
                try:
                    row_time = pd.Timestamp(r['thoi_gian_tao'])
                    if row_time.tz is None: row_time = row_time.tz_localize(timezone.utc) + pd.Timedelta(hours=7)
                    wait_minutes = (current_time - row_time).total_seconds() / 60
                    is_late = wait_minutes > 120 
                    color = "#ef4444" if is_late else "#22c55e"
                    icon = "🚨" if is_late else "✅"
                    wait_str = f"{int(wait_minutes // 60)}h {int(wait_minutes % 60)}p" if wait_minutes >= 60 else f"{int(wait_minutes)} phút"
                    status_text = f"TRỄ HẸN QUÁ HẠN (Đã chờ {wait_str})" if is_late else f"TRONG TIẾN ĐỘ AN TOÀN (Đang chờ {wait_str})"
                    tx_name = r['ten_nhan_vien'] if r['ten_nhan_vien'] else "Chưa phân xe"
                    st.markdown(f"""<div style='border-left: 6px solid {color}; background-color: #ffffff; padding: 14px; border-radius: 8px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);'>
                        <b style='color: {color}'>{icon} Lệnh {r['ma_don_hien_thi']} - {status_text}</b><br>
                        <small>• Giờ đặt: <b style='color:#000'>{row_time.strftime('%H:%M %d/%m')}</b> | Hiện tại: <b style='color:#000'>{current_time.strftime('%H:%M')}</b></small><br>
                        <small>• Tài xế: {tx_name} | Đối tác: {r['ten_khach']} | Nhân viên: {r['nguoi_tao']}</small></div>""", unsafe_allow_html=True)
                except: continue
    # ------------------ PHÂN TÍCH THÔNG MINH AI ------------------
    st.markdown("---")
    st.markdown("### 🤖 Trợ Lý AI: Phân Tích Hành Vi & Gợi Ý Chiến Lược")
    if not df_flat.empty:
        df_flat['ngay_ban_dt'] = pd.to_datetime(df_flat['ngay_ban'])
        top_10 = df_flat.groupby('ten_khach').agg({'so_luong':'sum', 'thanh_tien':'sum'}).reset_index().sort_values(by='so_luong', ascending=False).head(10)
        df_ai_sort = df_flat.sort_values(by=['ten_khach', 'ngay_ban_dt'])
        df_ai_sort['ngay_ban_truoc'] = df_ai_sort.groupby('ten_khach')['ngay_ban_dt'].shift(1)
        df_ai_sort['khoang_cach_ngay'] = (df_ai_sort['ngay_ban_dt'] - df_ai_sort['ngay_ban_truoc']).dt.days
        
        now_dt_flat = now_dt.replace(tzinfo=None)
        ai_khach = df_ai_sort.groupby('ten_khach').agg(ngay_mua_cuoi=('ngay_ban_dt', 'max'), chu_ky_mua=('khoang_cach_ngay', 'mean')).reset_index()
        ai_khach['ngay_chua_mua'] = (now_dt_flat - ai_khach['ngay_mua_cuoi']).dt.days
        khach_can_cham_soc = ai_khach[(ai_khach['chu_ky_mua'] > 0) & (ai_khach['ngay_chua_mua'] > ai_khach['chu_ky_mua'] + 5)].copy()
        
        date_30 = now_dt_flat - timedelta(days=30)
        date_60 = now_dt_flat - timedelta(days=60)
        than_30d = df_flat[df_flat['ngay_ban_dt'] >= date_30].groupby('ten_than')['so_luong'].sum().reset_index().rename(columns={'so_luong':'sl_30d'})
        than_60d = df_flat[(df_flat['ngay_ban_dt'] >= date_60) & (df_flat['ngay_ban_dt'] < date_30)].groupby('ten_than')['so_luong'].sum().reset_index().rename(columns={'so_luong':'sl_60d'})
        trend_than = pd.merge(than_30d, than_60d, on='ten_than', how='outer').fillna(0)
        trend_than['tang_truong'] = trend_than['sl_30d'] - trend_than['sl_60d']
        
        t_ai1, t_ai2, t_ai3 = st.tabs(["🏆 1. Top 10 Đối Tác VIP", "⚠️ 2. Cảnh Báo Khách Rời Bỏ", "📊 3. Dự Báo Nhập Kho"])
        with t_ai1: 
            st.dataframe(top_10.rename(columns={'ten_khach':'Tên Đối Tác', 'so_luong':'Sản Lượng (kg)', 'thanh_tien':'Doanh Thu (đ)'}).style.format({
                'Sản Lượng (kg)': lambda x: fmt_vn(x), 'Doanh Thu (đ)': lambda x: fmt_vn(x)
            }), use_container_width=True, hide_index=True)
        with t_ai2:
            if khach_can_cham_soc.empty: st.success("✅ Toàn bộ đối tác đang quay vòng đặt hàng ổn định.")
            else:
                for _, r in khach_can_cham_soc.iterrows():
                    st.markdown(f"<div class='ai-card ai-warn'>⚠️ Khách hàng <b>{r['ten_khach']}</b> thường <b>{r['chu_ky_mua']:.0f} ngày đặt 1 lần</b>, hiện đã quá hạn bãi xe <b>{r['ngay_chua_mua']} ngày</b>. Cần liên hệ chăm sóc gấp!</div>", unsafe_allow_html=True)
        with t_ai3:
            for _, r in trend_than.iterrows():
                if r['tang_truong'] > 0: st.markdown(f"<div class='ai-card'>📈 Chủng loại <b>{r['ten_than']}</b> sức mua TĂNG MẠNH <b>+{fmt_vn(r['tang_truong'])} kg</b>. Đề xuất chuẩn bị nhập thêm bãi.</div>", unsafe_allow_html=True)
                elif r['tang_truong'] < 0: st.markdown(f"<div class='ai-card ai-danger'>📉 Chủng loại <b>{r['ten_than']}</b> sức mua GIẢM <b>{fmt_vn(r['tang_truong'])} kg</b>. Xem xét hạn chế nhập bến.</div>", unsafe_allow_html=True)
    st.markdown("---")
    st.markdown("### 🗺️ Bản Đồ Phân Bổ Tiêu Thụ")
    if not df_flat.empty and 'lat' in df_flat.columns:
        map_data = df_flat[(df_flat['lat'].notna()) & (df_flat['lat'] != 0.0) & (df_flat['lon'] != 0.0)]
        if not map_data.empty:
            map_data_group = map_data.groupby(['ten_khach', 'khu_vuc', 'lat', 'lon'])['so_luong'].sum().reset_index()
            fig_map = px.scatter_mapbox(map_data_group, lat="lat", lon="lon", size="so_luong", color="ten_khach", hover_name="ten_khach", hover_data=["khu_vuc", "so_luong"], zoom=12, height=500) 
            fig_map.update_layout(mapbox_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
            st.plotly_chart(fig_map, use_container_width=True)
            st.caption("💡 Mẹo: Những vòng tròn càng to thể hiện sản lượng tiêu thụ khu đó càng lớn.")
    st.markdown("### 📊 Chi Tiết Các Mảng Thống Kê Phân Bổ")
    if not df_flat.empty:
        ch1, ch2 = st.columns(2) 
        with ch1: st.plotly_chart(px.pie(df_flat.groupby('ten_than')['so_luong'].sum().reset_index(), values='so_luong', names='ten_than', hole=0.4, title="Tỷ trọng than xuất kho"), use_container_width=True)
        with ch2: st.plotly_chart(px.pie(df_flat.groupby('ten_khach')['loi_nhuan'].sum().reset_index(), values='loi_nhuan', names='ten_khach', hole=0.4, title="Lợi nhuận theo khách hàng"), use_container_width=True)
    
    # ==========================================
    # MODULE THỐNG KÊ: CHỈ SỐ TĂNG TRƯỞNG KHÁCH HÀNG MỚI
    # ==========================================
    st.markdown("### 🌟 CHỈ SỐ TĂNG TRƯỞNG & MỞ RỘNG THỊ TRƯỜNG")
    
    # Lấy tháng hiện tại theo múi giờ Việt Nam
    current_month_str = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime('%Y-%m')
    
    with get_connection() as conn:
        # Thuật toán: Tìm khách có "Ngày mua lần đầu tiên" rơi vào tháng hiện tại
        query_new_cus = '''
            WITH KhachHangDauTien AS (
                SELECT khach_hang_id, MIN(ngay_ban) as ngay_mua_dau
                FROM don_hang
                GROUP BY khach_hang_id
            )
            SELECT 
                dh.khach_hang_id, 
                kh.ten_khach, 
                kh.sdt,
                kh.dia_chi,
                SUM(dh.tong_tien) as doanh_thu_mang_lai,
                khdt.ngay_mua_dau
            FROM don_hang dh
            JOIN KhachHangDauTien khdt ON dh.khach_hang_id = khdt.khach_hang_id
            JOIN khach_hang kh ON dh.khach_hang_id = kh.id
            WHERE strftime('%Y-%m', khdt.ngay_mua_dau) = ?
              AND strftime('%Y-%m', dh.ngay_ban) = ?
            GROUP BY dh.khach_hang_id, kh.ten_khach, kh.sdt, kh.dia_chi, khdt.ngay_mua_dau
        '''
        df_new_cus = pd.read_sql_query(query_new_cus, conn.connection, params=(current_month_str, current_month_str))
        
    # Tính toán chỉ số hiển thị
    so_khach_moi = len(df_new_cus)
    doanh_thu_moi = df_new_cus['doanh_thu_mang_lai'].sum() if not df_new_cus.empty else 0
    
    # Hiển thị giao diện Thẻ báo cáo (Dashboard Card)
    c1, c2 = st.columns(2)
    with c1:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #0ea5e9, #2563eb); padding: 20px; border-radius: 10px; color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
            <h4 style='margin:0; color: #e0f2fe;'>TỔNG KHÁCH HÀNG MỚI (THÁNG NÀY)</h4>
            <h1 style='margin:0; font-size: 36px;'>+{so_khach_moi} <span style='font-size:16px; font-weight:normal;'>đối tác</span></h1>
        </div>
        """, unsafe_allow_html=True)
    with c2:
        st.markdown(f"""
        <div style='background: linear-gradient(135deg, #10b981, #059669); padding: 20px; border-radius: 10px; color: white; box-shadow: 0 4px 6px rgba(0,0,0,0.1);'>
            <h4 style='margin:0; color: #d1fae5;'>DOANH THU TỪ TỆP KHÁCH MỚI</h4>
            <h1 style='margin:0; font-size: 36px;'>{fmt_vn(doanh_thu_moi)} <span style='font-size:16px; font-weight:normal;'>VNĐ</span></h1>
        </div>
        """, unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    
    # Danh sách CSKH
    if not df_new_cus.empty:
        st.markdown("#### 📋 Danh sách Khách hàng mới cần chăm sóc (CSKH)")
        st.info("💡 Đây là những đối tác vừa chốt đơn lần đầu trong tháng này. Hãy lưu ý nhắc nhở bộ phận Sale / CSKH gọi điện hỏi thăm chất lượng than để biến họ thành khách quen.")
        
        df_display = df_new_cus[['ten_khach', 'sdt', 'dia_chi', 'ngay_mua_dau', 'doanh_thu_mang_lai']].copy()
        df_display.columns = ['Tên Khách Hàng', 'Số Điện Thoại', 'Địa Chỉ', 'Ngày Ký Đơn Đầu', 'Doanh Thu Mang Lại (đ)']
        for col in ['Doanh Thu Mang Lại (đ)']: df_display[col] = df_display[col].apply(to_float)
        
        st.dataframe(df_display.style.format({
            'Doanh Thu Mang Lại (đ)': lambda x: fmt_vn(x)
        }), hide_index=True, use_container_width=True)
    else:
        st.warning("⚠️ Báo động đỏ: Tháng này bãi xe chưa khai thác được khách hàng mới nào. Cần thúc đẩy bộ phận Kinh doanh chạy thị trường!")
    if auto_refresh: time.sleep(30); st.rerun()
# ==========================================
# ==========================================
# ==========================================
# ==========================================
# PHÂN HỆ 2: LẬP ĐƠN & IN PHIẾU (TÍCH HỢP QUY CÁCH ĐÓNG GÓI)
# ==========================================
elif menu == "Lập Đơn & In Phiếu":
    st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📋 LẬP LỆNH XUẤT KHO</h1></div>", unsafe_allow_html=True)
    with get_connection() as conn: print_config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn.connection).iloc[0]
        
    if st.session_state.last_order_id:
        with get_connection() as conn:
            df_master = pd.read_sql_query(f"SELECT * FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.id = {to_int(st.session_state.last_order_id)}", conn.connection)
            # Kéo thêm Đơn vị tính và Quy cách từ bảng Loại Than
            details = pd.read_sql_query(f"SELECT ctdh.*, lt.ten_than, lt.don_vi_tinh, lt.he_so_kg FROM chi_tiet_don_hang ctdh JOIN loai_than lt ON ctdh.loai_than_id = lt.id WHERE ctdh.don_hang_id = {to_int(st.session_state.last_order_id)}", conn.connection)
            
        if df_master.empty:
            st.error("Lỗi đồng bộ. Vui lòng lập lại đơn mới.")
            if st.button("Quay lại"): st.session_state.last_order_id = None; st.rerun()
        else:
            master = df_master.iloc[0]
            html_rows = ""; txt_rows = ""; total_val = 0
            for idx, r in enumerate(details.iterrows(), 1):
                _, row = r; thanh_tien = row['so_luong'] * row['don_gia']; total_val += thanh_tien
                
                # HIỂN THỊ HÓA ĐƠN THÔNG MINH DỰA TRÊN QUY CÁCH
                dv = row['don_vi_tinh'] if pd.notna(row.get('don_vi_tinh')) and row.get('don_vi_tinh') != '' else 'kg'
                hs = to_float(row['he_so_kg']) if pd.notna(row.get('he_so_kg')) and to_float(row['he_so_kg']) > 0 else 1.0
                
                if dv.lower() != 'kg' and hs != 1.0:
                    so_luong_thung = row['so_luong'] / hs
                    don_gia_thung = row['don_gia'] * hs
                    sl_hien_thi = f"{fmt_vn(so_luong_thung)} {dv}<br><small style='color:#64748b;'>({fmt_vn(row['so_luong'])} kg)</small>"
                    dg_hien_thi = f"{fmt_vn(don_gia_thung)} đ/{dv}"
                    sl_txt = f"{fmt_vn(so_luong_thung)} {dv} ({fmt_vn(row['so_luong'])} kg)"
                    dg_txt = f"{fmt_vn(don_gia_thung)} đ/{dv}"
                else:
                    sl_hien_thi = f"{fmt_vn(row['so_luong'])} kg"
                    dg_hien_thi = f"{fmt_vn(row['don_gia'])} đ/kg"
                    sl_txt = sl_hien_thi
                    dg_txt = dg_hien_thi

                html_rows += f"<tr><td style='text-align:center;'>{idx}</td><td>{row['ten_than']}</td><td style='text-align:center;'>{sl_hien_thi}</td><td style='text-align:right;'>{dg_hien_thi}</td><td style='text-align:right; font-weight:bold;'>{fmt_vn(thanh_tien)}</td></tr>"
                txt_rows += f"- {row['ten_than']}: {sl_txt} x {dg_txt} = {fmt_vn(thanh_tien)} đ\n"
                
            full_html_print = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>body {{ font-family: 'Arial', sans-serif; color: #333; margin: 0; padding: 20px; }} .invoice-container {{ background: #fff; max-width: 800px; margin: 0 auto; padding: 30px; border: 1px solid #cbd5e1; border-radius: 8px; }} .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #2563eb; padding-bottom: 15px; }} .company-info h2 {{ margin: 0; color: #1e3a8a; }} table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }} th {{ background-color: #2563eb; color: white; padding: 10px; }} td {{ padding: 10px; border-bottom: 1px solid #e2e8f0; vertical-align: middle; }} .total-row td {{ font-weight: bold; font-size: 18px; color: #dc2626; }} .footer {{ display: flex; justify-content: space-between; margin-top: 40px; text-align: center; }} .signature {{ width: 45%; }} .print-btn {{ display: block; width: 100%; padding: 12px; background-color: #10b981; color: white; border: none; border-radius: 5px; font-size: 16px; font-weight: bold; cursor: pointer; margin-bottom:15px;}} @media print {{ .print-btn {{ display: none; }} }}</style></head><body><div class="invoice-container"><button class="print-btn" onclick="window.print()">🖨️ KÍCH HOẠT LỆNH IN FILE HÓA ĐƠN PDF</button><div class="header"><div class="company-info"><h2>{print_config["ten_cua_hang"]}</h2><p>SĐT bãi xe: {print_config["so_dien_thoai"]}</p></div><div style="text-align:right;"><h2>PHIẾU XUẤT</h2><p>Mã: <b>{master["ma_don_hien_thi"]}</b></p><p>Ngày: {master["thoi_gian_tao"]}</p></div></div><div style="margin: 20px 0; background:#f8fafc; padding:12px; border-radius:6px;">Link KH: <b>{master["ten_khach"]}</b><br>Địa chỉ: {master["dia_chi"]}<br>Ghi chú lái xe: {master["ghi_chu"]}</div><table><thead><tr><th>STT</th><th>Mặt Hàng Than</th><th>Khối Lượng / Đóng Gói</th><th>Đơn Giá</th><th>Thành Tiền (đ)</th></tr></thead><tbody>{html_rows}<tr class="total-row"><td colspan="4" style="text-align:right;">TỔNG CỘNG THANH TOÁN:</td><td style="text-align:right;">{fmt_vn(total_val)} đ</td></tr></tbody></table><p style="margin-top:20px; font-size:13px;"><b>Tài khoản thụ hưởng:</b> {print_config["thong_tin_ngan_hang"]}</p><div class="footer"><div class="signature"><p><b>Đại Diện Khách Hàng</b></p><p><i>(Ký tên)</i></p></div><div class="signature"><p><b>Thủ Kho Bảo Vệ</b></p><p><i>(Ký tên)</i></p></div></div></div></body></html>"""
            text_bill = f"HÓA ĐƠN GIAO HÀNG - {print_config['ten_cua_hang']}\nMã: {master['ma_don_hien_thi']} | Ngày: {master['thoi_gian_tao']}\nKhách: {master['ten_khach']}\nĐịa chỉ: {master['dia_chi']}\n-------------------------\n{txt_rows}-------------------------\nTỔNG CỘNG: {fmt_vn(total_val)} VNĐ\nCK/TT: {print_config['thong_tin_ngan_hang']}\nCảm ơn quý khách!"

            st.success("🎉 Ghi nhận đơn hàng thành công trên máy chủ!")
            components.html(full_html_print, height=520, scrolling=True)
            
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1: st.download_button(label="📥 TẢI FILE HÓA ĐƠN HTML", data=full_html_print.encode('utf-8'), file_name=f"HoaDon_{master['ma_don_hien_thi']}.html", mime="text/html", use_container_width=True)
            with c_btn2: 
                if st.button("🔄 TIẾP TỤC LẬP PHIẾU MỚI", type="primary", use_container_width=True):
                    st.session_state.last_order_id = None; st.rerun()
            st.markdown("---")
            st.code(text_bill, language="text")
    else:
        with get_connection() as conn:
            # Kéo thêm cột ghi_chu_kh vào để hiển thị
            df_khach = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach, han_muc_no, ghi_chu_kh FROM khach_hang", conn.connection)
            # Kéo thêm dữ liệu Đơn vị và Quy cách
            df_than = pd.read_sql_query("SELECT id, ten_than, gia_nhap_mac_dinh, gia_mac_dinh, ton_kho, don_vi_tinh, he_so_kg FROM loai_than", conn.connection)

        if df_khach.empty or df_than.empty: st.warning("Vui lòng khởi tạo Danh mục đối tác và Chủng loại than trước.")
        else:
            panel_input, panel_cart = st.columns([2, 3])
            with panel_input:
                st.markdown("#### 👤 Lập hóa đơn")
                khach_dict = dict(zip(df_khach['id'], "[" + df_khach['ma_khach_hang'].astype(str) + "] " + df_khach['ten_khach'].astype(str)))
                khach_id = st.selectbox("Chọn Khách Hàng:", options=list(khach_dict.keys()), format_func=lambda x: khach_dict.get(x))
                
                k_info = df_khach[df_khach['id']==khach_id].iloc[0]
                
                # HIỂN THỊ GHI CHÚ ĐẶC BIỆT CỦA KHÁCH HÀNG
                ghi_chu_khach = k_info.get('ghi_chu_kh', '')
                if pd.notna(ghi_chu_khach) and str(ghi_chu_khach).strip() != "":
                    st.markdown(f"<div style='background-color:#fffbeb; padding:15px; border-radius:6px; border-left:5px solid #f59e0b; margin-bottom:15px;'><b style='color:#b45309;'>⚠️ LƯU Ý YÊU CẦU TỪ KHÁCH HÀNG:</b><br><span style='color:#92400e; font-size:15px;'>{ghi_chu_khach}</span></div>", unsafe_allow_html=True)
                
                han_muc_no = to_float(k_info.get('han_muc_no', 0))
                with get_connection() as conn:
                    no_hien_tai_raw = conn.execute("SELECT SUM(tien_con_no) FROM don_hang WHERE khach_hang_id=? AND trang_thai_giao='Đã hoàn thành'", (to_int(khach_id),)).fetchone()[0]
                no_hien_tai = to_float(no_hien_tai_raw)
                if han_muc_no > 0:
                    st.markdown(f"<div style='font-size:13px; color:#b91c1c;'>⚠️ Nợ hiện tại: <b>{fmt_vn(no_hien_tai)}đ</b> / Hạn mức: <b>{fmt_vn(han_muc_no)}đ</b></div>", unsafe_allow_html=True)
                
                with get_connection() as conn: df_pb = pd.read_sql_query(f"SELECT loai_than_id FROM gia_rieng WHERE khach_hang_id = {to_int(khach_id)}", conn.connection)
                than_options = df_than[df_than['id'].isin(df_pb['loai_than_id'].tolist())] if not df_pb.empty else df_than
                if than_options.empty: than_options = df_than
                
                than_dict = dict(zip(than_options['id'], than_options['ten_than'].astype(str)))
                t_id = st.selectbox("Chọn Loại Than Xuất Bãi:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x))
                
                with get_connection() as conn: gr_res = conn.cursor().execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=? AND loai_than_id=?", (to_int(khach_id), to_int(t_id))).fetchone()
                
                df_tk_filter = df_than[df_than['id']==t_id]
                gia_goi_y = gr_res[0] if gr_res else (df_tk_filter['gia_mac_dinh'].values[0] if not df_tk_filter.empty else 0)
                ton_kho_hien_tai = to_float(df_tk_filter['ton_kho'].values[0]) if not df_tk_filter.empty else 0.0
                gia_von_kho = to_float(df_tk_filter['gia_nhap_mac_dinh'].values[0]) if not df_tk_filter.empty else 0.0
                
                # BẮT ĐẦU XỬ LÝ QUY CÁCH ĐÓNG HÀNG
                dv_tinh = df_tk_filter['don_vi_tinh'].values[0] if 'don_vi_tinh' in df_tk_filter.columns and pd.notna(df_tk_filter['don_vi_tinh'].values[0]) else 'kg'
                hs_kg = to_float(df_tk_filter['he_so_kg'].values[0]) if 'he_so_kg' in df_tk_filter.columns and pd.notna(df_tk_filter['he_so_kg'].values[0]) else 1.0
                if hs_kg <= 0: hs_kg = 1.0
                
                if dv_tinh.lower() == 'kg':
                    st.markdown(f"Trữ lượng bãi thực tế: <b style='color:#2563eb;'>{fmt_vn(ton_kho_hien_tai)} kg</b>", unsafe_allow_html=True)
                    st.markdown("---")
                    col_sl, col_dg = st.columns(2)
                    with col_sl: sl = st.number_input("Khối lượng (kg):", min_value=1.0, value=1000.0, step=100.0)
                    with col_dg: dg = st.number_input("Đơn giá bán (đ/kg):", min_value=1.0, value=float(gia_goi_y), step=500.0)
                    
                    sl_kg = sl
                    dg_kg = dg
                    thanh_tien = sl * dg
                    hien_thi_sl = f"{fmt_vn(sl)} kg"
                    hien_thi_dg = f"{fmt_vn(dg)} đ/kg"
                else:
                    ton_kho_dv = ton_kho_hien_tai / hs_kg
                    st.markdown(f"Trữ lượng bãi thực tế: <b style='color:#2563eb;'>{fmt_vn(ton_kho_dv)} {dv_tinh}</b> <small>(Tương đương {fmt_vn(ton_kho_hien_tai)} kg)</small>", unsafe_allow_html=True)
                    st.markdown("---")
                    col_sl, col_dg = st.columns(2)
                    with col_sl: sl = st.number_input(f"Số lượng ({dv_tinh}):", min_value=1.0, value=10.0, step=1.0, help=f"Hệ thống sẽ tự quy đổi: 1 {dv_tinh} = {hs_kg} kg")
                    # Gợi ý đơn giá tự nhân theo quy cách
                    gia_dv_goi_y = float(gia_goi_y) * hs_kg
                    with col_dg: dg = st.number_input(f"Đơn giá bán (đ/{dv_tinh}):", min_value=1.0, value=gia_dv_goi_y, step=1000.0)
                    
                    # Hệ thống luôn ngầm định tính toán và trừ kho theo hệ KG để báo cáo không bị sai lệch
                    sl_kg = sl * hs_kg
                    dg_kg = dg / hs_kg
                    thanh_tien = sl * dg
                    hien_thi_sl = f"{fmt_vn(sl)} {dv_tinh}"
                    hien_thi_dg = f"{fmt_vn(dg)} đ/{dv_tinh}"

                if st.button("➕ NẠP VÀO PHIẾU XUẤT", use_container_width=True):
                    if any(i['loai_than_id'] == to_int(t_id) for i in st.session_state.cart): st.error("Mặt hàng này đã nằm trong danh sách tạm tính!")
                    else:
                        st.session_state.cart.append({
                            'loai_than_id': to_int(t_id), 'ten_than': than_dict.get(t_id), 
                            'so_luong': sl_kg, 'don_gia': dg_kg, 'thanh_tien': thanh_tien, 
                            'don_gia_von': gia_von_kho,
                            'hien_thi_sl': hien_thi_sl, 'hien_thi_dg': hien_thi_dg
                        })
                        st.rerun()
            
            with panel_cart:
                st.markdown("<div class='panel-summary'>", unsafe_allow_html=True)
                st.markdown("### 📊 BẢNG TỔNG HỢP TẠM TÍNH")
                if st.session_state.cart:
                    df_c = pd.DataFrame(st.session_state.cart)
                    # Hiển thị đẹp mắt trên Cart
                    st.dataframe(df_c[['ten_than', 'hien_thi_sl', 'hien_thi_dg', 'thanh_tien']].rename(columns={
                        'ten_than':'Chủng Loại Than', 'hien_thi_sl':'Số Lượng', 'hien_thi_dg':'Đơn Giá', 'thanh_tien':'Thành Tiền (đ)'
                    }).style.format({
                        'Thành Tiền (đ)': lambda x: fmt_vn(x)
                    }), hide_index=True, use_container_width=True)
                    
                    total_val = df_c['thanh_tien'].sum()
                    st.markdown(f"""<div style='background: #fef2f2; padding: 15px; border-radius: 8px; text-align: center; margin: 15px 0; border: 1px solid #fca5a5;'>
                        <span style='color:#991b1b; font-weight:600; font-size:14px; text-transform:uppercase;'>Tổng Tiền Phiếu Dự Kiến</span><br>
                        <span style='color:#dc2626; font-size:28px; font-weight:800;'>{fmt_vn(total_val)} đ</span></div>""", unsafe_allow_html=True)
                    
                    giao_gap = st.checkbox("🔥 PHÁT LỆNH GIAO GẤP KHẨN CẤP")
                    g_chu = st.text_input("Biển số xe điều vận / Tên lái xe:")
                    
                    bx1, bx2 = st.columns(2)
                    with bx1:
                        if st.button("🗑️ HỦY PHIẾU TẠM", type="secondary", use_container_width=True):
                            st.session_state.cart = []; st.rerun()
                    with bx2:
                        if st.button("🚀 XUẤT PHIẾU VÀ ĐẨY LỆNH", type="primary", use_container_width=True):
                            if han_muc_no > 0 and (no_hien_tai + total_val) > han_muc_no:
                                st.error(f"⛔ Khách hàng này đã vượt hạn mức công nợ! (Nợ cũ: {fmt_vn(no_hien_tai)}đ + Đơn mới: {fmt_vn(total_val)}đ > Hạn mức: {fmt_vn(han_muc_no)}đ)")
                            else:
                                stock_ok = True
                                for i in st.session_state.cart:
                                    ton_check = df_than[df_than['id'] == to_int(i['loai_than_id'])]
                                    ton_val = to_float(ton_check['ton_kho'].values[0]) if not ton_check.empty else 0.0
                                    # Cảnh báo tồn kho bây giờ cũng tính bằng kg ngầm định
                                    if to_float(i['so_luong']) > ton_val: stock_ok = False; st.error(f"Cảnh báo: Mã {i['ten_than']} vượt trữ lượng bãi xe!")
                                
                                if stock_ok:
                                    try:
                                        ts = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime('%Y-%m-%d %H:%M:%S')
                                        ma_don_final = sinh_ma_don_hang_theo_ngay(today_str)
                                        is_gap = 1 if giao_gap else 0
                                        
                                        with get_connection() as conn:
                                            cur = conn.cursor()
                                            new_id = get_next_id('don_hang', cur)
                                            cur.execute('INSERT INTO don_hang (id, ma_don_hien_thi, khach_hang_id, ngay_ban, thoi_gian_tao, trang_thai_giao, ghi_chu, giao_gap, tong_tien, tien_con_no, nguoi_tao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (new_id, ma_don_final, to_int(khach_id), today_str, ts, 'Chờ giao hàng', g_chu, is_gap, float(total_val), float(total_val), st.session_state.current_user))
                                            for i in st.session_state.cart:
                                                ct_id = get_next_id('chi_tiet_don_hang', cur)
                                                cur.execute('INSERT INTO chi_tiet_don_hang (id, don_hang_id, loai_than_id, so_luong, don_gia, don_gia_von) VALUES (?, ?, ?, ?, ?, ?)', (ct_id, new_id, to_int(i['loai_than_id']), i['so_luong'], i['don_gia'], i.get('don_gia_von', 0)))
                                                cur.execute("UPDATE loai_than SET ton_kho = ton_kho - ? WHERE id = ?", (i['so_luong'], to_int(i['loai_than_id'])))
                                            conn.commit()
                                        
                                        try: send_tele_notify(f"📢 [LỆNH XUẤT MỚI]\n- Mã đơn: {ma_don_final}\n- Khách: {k_info['ten_khach']}\n- Tổng tiền: {fmt_vn(total_val)} VNĐ\n- Trực ca: {st.session_state.current_user}")
                                        except: pass 
                                        
                                        write_log("Lập đơn hàng", "SUCCESS", f"Mã phiếu: {ma_don_final}")
                                        st.session_state.cart = []; st.session_state.last_order_id = new_id; st.rerun()
                                    except Exception as e: 
                                        write_log("Lập đơn hàng", "ERROR", str(e)); st.error(f"Lỗi hệ thống bãi xe: {str(e)}")
                else: st.info("Giỏ hàng rỗng.")
                st.markdown("</div>", unsafe_allow_html=True)
# ==========================================
# ==========================================
# PHÂN HỆ 3: GIAO HÀNG & ĐIỀU VẬN TÀI XẾ
# ==========================================
elif menu == "Giao Hàng & Vận Tải":
    st.markdown("### 🚚 Bàn Giao Lộ Trình & Nghiệm Thu")
    with get_connection() as conn: df_staff = pd.read_sql_query("SELECT id, ten_nhan_vien FROM nhan_vien", conn.connection)
    
    tab1, tab2 = st.tabs(["📦 Xe Chờ Đi Giao", "🏁 Nghiệm Thu Giao Xong"])
    with tab1:
        with get_connection() as conn: df_cho = pd.read_sql_query("SELECT id, ma_don_hien_thi, khach_hang_id, trang_thai_giao FROM don_hang WHERE trang_thai_giao = 'Chờ giao hàng'", conn.connection)
        if df_cho.empty: st.success("Hiện tại không có đơn xe nào chờ điều phối bãi.")
        else:
            for idx, r in df_cho.iterrows():
                with get_connection() as conn: 
                    khach_info = pd.read_sql_query(f"SELECT ten_khach, link_google_maps FROM khach_hang WHERE id={to_int(r['khach_hang_id'])}", conn.connection)
                    ten_kh = khach_info.iloc[0]['ten_khach'] if not khach_info.empty else "Ẩn danh"
                    link_map = khach_info.iloc[0]['link_google_maps'] if not khach_info.empty else ""
                    tong_kg_raw = pd.read_sql_query(f"SELECT SUM(so_luong) FROM chi_tiet_don_hang WHERE don_hang_id={to_int(r['id'])}", conn.connection)
                    tong_kg = to_float(tong_kg_raw.iloc[0,0]) if not tong_kg_raw.empty else 0.0
                
                with st.expander(f"📦 Đơn {r['ma_don_hien_thi']} - Khách: {ten_kh} | Khối lượng: {fmt_vn(tong_kg)} kg", expanded=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        with st.form(key=f"giao_xe_{r['id']}"):
                            if link_map: st.markdown(f"[📍 Chỉ đường Google Maps]({link_map})")
                            if df_staff.empty: st.warning("Chưa cấu hình hồ sơ tài xế.")
                            else:
                                tx_dict = dict(zip(df_staff['id'], df_staff['ten_nhan_vien'].astype(str)))
                                tx_id = st.selectbox("Giao xe cho tài xế:", options=list(tx_dict.keys()), format_func=lambda x: tx_dict.get(x))
                                if st.form_submit_button("Lệnh Xuất Phát", type="primary"):
                                    with get_connection() as c_update: 
                                        c_update.execute("UPDATE don_hang SET trang_thai_giao='Đang giao', nhan_vien_id=? WHERE id=?", (to_int(tx_id), to_int(r['id'])))
                                        c_update.commit()
                                    st.success("Xe đã lăn bánh!"); st.rerun()
                    with c2: 
                        if st.button("🗑️ Hủy Đơn", key=f"huy_don_cho_{r['id']}"): cb_huy_don(to_int(r['id'])); st.rerun()

    with tab2:
        with get_connection() as conn: df_dang = pd.read_sql_query("SELECT dh.id, dh.ma_don_hien_thi, dh.khach_hang_id, dh.tong_tien, nv.ten_nhan_vien FROM don_hang dh LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id WHERE dh.trang_thai_giao = 'Đang giao'", conn.connection)
        if df_dang.empty: st.info("Không có xe nào đang di chuyển ngoài đường.")
        else:
            for idx, r in df_dang.iterrows():
                with get_connection() as conn:
                    khach_info = pd.read_sql_query(f"SELECT ten_khach FROM khach_hang WHERE id={to_int(r['khach_hang_id'])}", conn.connection)
                    ten_kh = khach_info.iloc[0]['ten_khach'] if not khach_info.empty else "Ẩn danh"
                    tx_name = r['ten_nhan_vien'] if r['ten_nhan_vien'] else "Chưa rõ"
                    # TRÍCH XUẤT CHI TIẾT ĐƠN ĐỂ HIỂN THỊ CÁC MẶT HÀNG CHO PHÉP TRẢ
                    ct_df = pd.read_sql_query(f"SELECT c.id, c.loai_than_id, c.so_luong, c.don_gia, l.ten_than FROM chi_tiet_don_hang c JOIN loai_than l ON c.loai_than_id = l.id WHERE c.don_hang_id={to_int(r['id'])}", conn.connection)
                    
                with st.expander(f"🚚 Lái xe: {tx_name} | Đơn {r['ma_don_hien_thi']} - Khách: {ten_kh}", expanded=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        with st.form(key=f"form_done_gh_{r['id']}"):
                            tong_tien_an_toan = to_int(r['tong_tien'])
                            st.write(f"Giá trị đơn hàng gốc: **{fmt_vn(tong_tien_an_toan)} đ**", unsafe_allow_html=True)
                            
                            st.markdown("---")
                            st.write("📦 **Ghi nhận hàng trả lại / hao hụt (Nếu khách không nhận đủ):**")
                            return_inputs = []
                            for _, ct in ct_df.iterrows():
                                ret_qty = st.number_input(f"Số kg trả lại - {ct['ten_than']} (Đơn gốc giao {ct['so_luong']} kg):", min_value=0.0, max_value=to_float(ct['so_luong']), value=0.0, step=10.0, key=f"ret_{r['id']}_{ct['id']}")
                                return_inputs.append({'ct_id': ct['id'], 'loai_than_id': ct['loai_than_id'], 'ret_qty': ret_qty, 'don_gia': ct['don_gia'], 'ten_than': ct['ten_than']})
                            
                            st.markdown("---")
                            tien_tra_ngay = st.number_input("Cầm tiền mặt / CK thu ngay (đ):", min_value=0.0, value=0.0, step=10000.0)
                            pt_tt = st.selectbox("Cơ chế nhận tiền:", ["Chuyển khoản", "Tiền mặt"])
                            
                            if st.form_submit_button("Xác Nhận Nghiệm Thu", type="primary"):
                                # TÍNH TOÁN GIẢM TRỪ VÀ TRẢ LẠI KHO
                                tien_giam_tru = sum(item['ret_qty'] * item['don_gia'] for item in return_inputs)
                                tong_tien_moi = tong_tien_an_toan - tien_giam_tru
                                
                                if tien_tra_ngay > tong_tien_moi:
                                    st.error(f"Lỗi: Tiền thu ngay ({fmt_vn(tien_tra_ngay)} đ) không được lớn hơn tổng tiền sau khi trừ hàng hoàn ({fmt_vn(tong_tien_moi)} đ)!")
                                else:
                                    tien_con_no_lai = tong_tien_moi - tien_tra_ngay
                                    is_paid = 1 if tien_con_no_lai <= 0 else 0
                                    ht_luu = pt_tt if is_paid else f"Thanh toán 1 phần ({pt_tt})"
                                    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                                    
                                    with get_connection() as c_update:
                                        cur = c_update.cursor()
                                        
                                        # Cập nhật số lượng hoàn trả vào kho bãi
                                        for item in return_inputs:
                                            if item['ret_qty'] > 0:
                                                cur.execute("UPDATE chi_tiet_don_hang SET so_luong = so_luong - ? WHERE id=?", (item['ret_qty'], item['ct_id']))
                                                cur.execute("UPDATE loai_than SET ton_kho = ton_kho + ? WHERE id=?", (item['ret_qty'], item['loai_than_id']))
                                        
                                        # Cập nhật tiền mới của đơn hàng
                                        cur.execute("UPDATE don_hang SET trang_thai_giao='Đã hoàn thành', tong_tien=?, da_thanh_toan=?, hinh_thuc_thanh_toan=?, tien_da_tra=?, tien_con_no=? WHERE id=?", (tong_tien_moi, is_paid, ht_luu, tien_tra_ngay, tien_con_no_lai, to_int(r['id'])))
                                        
                                        if tien_tra_ngay > 0: 
                                            lsid = get_next_id('lich_su_thanh_toan', cur)
                                            cur.execute("INSERT INTO lich_su_thanh_toan (id, don_hang_id, so_tien_tra, hinh_thuc, ngay_tra, ghi_chu, nguoi_tao) VALUES (?,?,?,?,?,?,?)", (lsid, to_int(r['id']), tien_tra_ngay, pt_tt, ts, "Thu tiền hạ hàng", st.session_state.current_user))
                                        c_update.commit()
                                    
                                    # KÍCH HOẠT BOT ZALO GỬI BÁO CÁO CẬP NHẬT
                                    hoan_msg = f"\n- Khách trả lại hàng: Trừ {fmt_vn(tien_giam_tru)} VNĐ" if tien_giam_tru > 0 else ""
                                    try:
                                        send_zalo_notify(f"✅ [GIAO THÀNH CÔNG]\n- Mã đơn: {r['ma_don_hien_thi']}\n- Lái xe: {tx_name}\n- Tổng tiền (đã trừ hoàn trả): {fmt_vn(tong_tien_moi)} VNĐ{hoan_msg}\n- Đã thu: {fmt_vn(tien_tra_ngay)} VNĐ\n- Còn nợ: {fmt_vn(tien_con_no_lai)} VNĐ")
                                    except:
                                        pass
                                    st.success("Nghiệm thu đơn hoàn tất!"); st.rerun()
                    with c2: 
                        if st.button("🗑️ Hủy Đơn", key=f"huy_don_dang_{r['id']}"): cb_huy_don(to_int(r['id'])); st.rerun()
# ==========================================
# ==========================================
# ==========================================
# PHÂN HỆ MỚI: SỔ QUỸ & LÃI LỖ (P&L)
# ==========================================
elif menu == "Sổ Quỹ & Lãi Lỗ":
    if 'edit_sq_id' not in st.session_state: st.session_state.edit_sq_id = None
    
    st.markdown("### 💵 Kế Toán Tổng Hợp & Báo Cáo Lãi Lỗ (P&L)")
    # Đã thêm Tab báo cáo Xe Tải
    tab_lap, tab_ls, tab_pl, tab_xe = st.tabs(["📝 Lập Phiếu Thu/Chi", "📚 Lịch Sử & Chỉnh Sửa", "📈 Báo Cáo Lãi Lỗ (P&L)", "🚚 Báo Cáo Xe Tải"])
    
    # Bổ sung các hạng mục chuyên nghiệp hơn
    HANG_MUC_LIST = ["Chi tiền tàu/nhập hàng", "Chi điện nước, mặt bằng", "Chi lương/nhân công", "Chi phí xe tải (Dầu, phụ tùng)", "Thu từ chở thuê (Xe tải)", "Thu khác", "Chi khác", "Chi sửa xe/xăng dầu"]

    with tab_lap:
        with st.form("form_so_quy"):
            c1, c2 = st.columns(2)
            with c1:
                loai_phieu = st.radio("Loại phiếu:", ["Chi", "Thu"], horizontal=True)
                hang_muc = st.selectbox("Hạng mục:", HANG_MUC_LIST)
            with c2:
                so_tien = st.number_input("Số tiền (đ):", min_value=1000, value=100000, step=10000)
                ghi_chu_sq = st.text_input("Ghi chú chi tiết (VD: Biển số xe, tên chuyến...):")
            
            if st.form_submit_button("Lưu Phiếu Giao Dịch", type="primary"):
                ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                with get_connection() as conn:
                    cur = conn.cursor()
                    qid = get_next_id('so_quy', cur)
                    cur.execute("INSERT INTO so_quy (id, ngay, thoi_gian, loai_phieu, so_tien, hang_muc, nguoi_tao, ghi_chu) VALUES (?,?,?,?,?,?,?,?)", (qid, today_str, ts, loai_phieu, so_tien, hang_muc, st.session_state.current_user, ghi_chu_sq))
                    conn.commit()
                st.success("✅ Đã ghi sổ thành công! (Vui lòng bấm sang tab 'Lịch Sử & Chỉnh Sửa' để kiểm tra lại giao dịch)")
                st.rerun()
                
    with tab_ls:
        with get_connection() as conn:
            df_sq = pd.read_sql_query("SELECT id, thoi_gian, loai_phieu, hang_muc, so_tien, nguoi_tao, ghi_chu FROM so_quy ORDER BY id DESC", conn.connection)
        
        # === GIAO DIỆN CHỈNH SỬA SỔ QUỸ ===
        if st.session_state.edit_sq_id is not None:
            edit_id = st.session_state.edit_sq_id
            sq_info = df_sq[df_sq['id'] == edit_id].iloc[0]
            st.markdown(f"<div class='edit-box'><h4>✏️ ĐANG SỬA PHIẾU: {sq_info['loai_phieu'].upper()} - {fmt_vn(sq_info['so_tien'])} đ</h4></div>", unsafe_allow_html=True)
            with st.form("f_edit_sq"):
                c1, c2 = st.columns(2)
                with c1:
                    e_lp = st.radio("Loại phiếu:", ["Chi", "Thu"], index=0 if sq_info['loai_phieu']=="Chi" else 1, horizontal=True)
                    current_hm_index = HANG_MUC_LIST.index(sq_info['hang_muc']) if sq_info['hang_muc'] in HANG_MUC_LIST else 0
                    e_hm = st.selectbox("Hạng mục:", HANG_MUC_LIST, index=current_hm_index)
                with c2:
                    e_st = st.number_input("Số tiền (đ):", min_value=1000, value=to_int(sq_info['so_tien']), step=10000)
                    e_gc = st.text_input("Ghi chú chi tiết:", value=str(sq_info['ghi_chu'] if pd.notna(sq_info['ghi_chu']) else ""))
                
                bc1, bc2 = st.columns([1, 10])
                with bc1:
                    if st.form_submit_button("💾 LƯU", type="primary"):
                        with get_connection() as conn:
                            conn.execute("UPDATE so_quy SET loai_phieu=?, hang_muc=?, so_tien=?, ghi_chu=? WHERE id=?", (e_lp, e_hm, e_st, e_gc, edit_id))
                            conn.commit()
                        st.session_state.edit_sq_id = None; st.rerun()
                with bc2:
                    if st.form_submit_button("Hủy bỏ"): st.session_state.edit_sq_id = None; st.rerun()
            st.markdown("---")

        # === DANH SÁCH LỊCH SỬ KÈM NÚT XÓA/SỬA ===
        st.markdown("#### 📋 Lịch Sử Giao Dịch Kho Bãi")
        if not df_sq.empty:
            df_sq_show = df_sq.head(100) # Lấy 100 giao dịch gần nhất để phần mềm chạy mượt mà
            c1, c2, c3, c4, c5, c6 = st.columns([1.5, 1, 2.5, 2, 3, 1.5])
            c1.markdown("<b>Thời Gian</b>", unsafe_allow_html=True)
            c2.markdown("<b>Loại</b>", unsafe_allow_html=True)
            c3.markdown("<b>Hạng Mục</b>", unsafe_allow_html=True)
            c4.markdown("<b>Số Tiền</b>", unsafe_allow_html=True)
            c5.markdown("<b>Ghi Chú</b>", unsafe_allow_html=True)
            c6.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
            
            for _, r in df_sq_show.iterrows():
                with st.container():
                    cc1, cc2, cc3, cc4, cc5, cc6, cc7 = st.columns([1.5, 1, 2.5, 2, 3, 0.75, 0.75])
                    cc1.markdown(f"<div class='list-row'>{r['thoi_gian']}</div>", unsafe_allow_html=True)
                    
                    color = "#16a34a" if r['loai_phieu'] == "Thu" else "#dc2626"
                    cc2.markdown(f"<div class='list-row' style='color:{color}; font-weight:bold;'>{r['loai_phieu']}</div>", unsafe_allow_html=True)
                    
                    cc3.markdown(f"<div class='list-row'>{r['hang_muc']}</div>", unsafe_allow_html=True)
                    cc4.markdown(f"<div class='list-row'>{fmt_vn(r['so_tien'])} đ</div>", unsafe_allow_html=True)
                    
                    note = r['ghi_chu'] if pd.notna(r['ghi_chu']) and r['ghi_chu'] else "-"
                    cc5.markdown(f"<div class='list-row'>{note}</div>", unsafe_allow_html=True)
                    
                    with cc6:
                        if st.button("✏️", key=f"esq_{r['id']}"): st.session_state.edit_sq_id = r['id']; st.rerun()
                    with cc7:
                        if st.button("❌", key=f"dsq_{r['id']}"): 
                            with get_connection() as c: 
                                c.execute("DELETE FROM so_quy WHERE id=?", (r['id'],))
                                c.commit()
                            st.rerun()
        else:
            st.info("Chưa có dữ liệu thu chi nào được ghi nhận.")
            
    with tab_pl:
        st.markdown("#### 📊 Báo Cáo Lãi Lỗ Hoạt Động Doanh Nghiệp (P&L)")
        thang_loc = st.selectbox("Chọn chu kỳ báo cáo:", ["Tháng này", "Tháng trước", "Tất cả thời gian"])
        
        with get_connection() as conn:
            df_pl_dh = pd.read_sql_query("SELECT dh.id as dh_id, dh.thoi_gian_tao, dh.tong_tien, ctdh.so_luong, ctdh.don_gia_von FROM don_hang dh JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id WHERE dh.trang_thai_giao='Đã hoàn thành'", conn.connection)
            df_pl_sq = pd.read_sql_query("SELECT thoi_gian, loai_phieu, so_tien FROM so_quy", conn.connection)
            
        if not df_pl_dh.empty: df_pl_dh['Date'] = pd.to_datetime(df_pl_dh['thoi_gian_tao'])
        if not df_pl_sq.empty: df_pl_sq['Date'] = pd.to_datetime(df_pl_sq['thoi_gian'])
        
        if thang_loc == "Tháng này":
            if not df_pl_dh.empty: df_pl_dh = df_pl_dh[(df_pl_dh['Date'].dt.month == now_dt.month) & (df_pl_dh['Date'].dt.year == now_dt.year)]
            if not df_pl_sq.empty: df_pl_sq = df_pl_sq[(df_pl_sq['Date'].dt.month == now_dt.month) & (df_pl_sq['Date'].dt.year == now_dt.year)]
        elif thang_loc == "Tháng trước":
            prev_month = (now_dt.replace(day=1) - timedelta(days=1))
            if not df_pl_dh.empty: df_pl_dh = df_pl_dh[(df_pl_dh['Date'].dt.month == prev_month.month) & (df_pl_dh['Date'].dt.year == prev_month.year)]
            if not df_pl_sq.empty: df_pl_sq = df_pl_sq[(df_pl_sq['Date'].dt.month == prev_month.month) & (df_pl_sq['Date'].dt.year == prev_month.year)]
            
        doanh_thu = df_pl_dh.drop_duplicates(subset=['dh_id'])['tong_tien'].apply(to_float).sum() if not df_pl_dh.empty else 0
        gia_von = (df_pl_dh['so_luong'].apply(to_float) * df_pl_dh['don_gia_von'].apply(to_float)).sum() if not df_pl_dh.empty else 0
        loi_nhuan_gop = doanh_thu - gia_von
        
        tong_chi = df_pl_sq[df_pl_sq['loai_phieu']=='Chi']['so_tien'].apply(to_float).sum() if not df_pl_sq.empty else 0
        tong_thu_khac = df_pl_sq[df_pl_sq['loai_phieu']=='Thu']['so_tien'].apply(to_float).sum() if not df_pl_sq.empty else 0
        
        loi_nhuan_thuan = loi_nhuan_gop + tong_thu_khac - tong_chi
        
        st.markdown(f"""
        <div style='background:#fff; padding:20px; border-radius:10px; border:1px solid #e2e8f0; margin-top:15px;'>
            <table style='width:100%; font-size:16px;'>
                <tr><td style='padding:8px 0;'><b>1. Doanh thu bán hàng thực tế</b></td><td style='text-align:right; padding:8px 0;'><b>{fmt_vn(doanh_thu)} đ</b></td></tr>
                <tr><td style='padding:8px 0;'>2. Giá vốn hàng bán (Theo kho)</td><td style='text-align:right; color:#dc2626; padding:8px 0;'>- {fmt_vn(gia_von)} đ</td></tr>
                <tr style='background:#f1f5f9;'><td style='padding:12px;'><b>3. LỢI NHUẬN GỘP (1 - 2)</b></td><td style='text-align:right; font-weight:bold; color:#2563eb; padding:12px;'>{fmt_vn(loi_nhuan_gop)} đ</td></tr>
                <tr><td style='padding:8px 0; margin-top:10px;'>4. Tổng chi phí vận hành (Lương, Mặt bằng...)</td><td style='text-align:right; color:#dc2626; padding:8px 0;'>- {fmt_vn(tong_chi)} đ</td></tr>
                <tr><td style='padding:8px 0;'>5. Thu nhập khác ngoài bãi</td><td style='text-align:right; color:#16a34a; padding:8px 0;'>+ {fmt_vn(tong_thu_khac)} đ</td></tr>
                <tr style='background:#dcfce7; border-top:2px solid #22c55e;'><td style='padding:18px; font-size:18px;'><b>6. LỢI NHUẬN RÒNG (NET PROFIT)</b></td><td style='text-align:right; font-size:24px; font-weight:bold; color:#15803d; padding:18px;'>{fmt_vn(loi_nhuan_thuan)} đ</td></tr>
            </table>
        </div>
        """, unsafe_allow_html=True)

    # === TÍNH NĂNG MỚI: BÁO CÁO HIỆU QUẢ NUÔI XE TẢI ===
    with tab_xe:
        st.markdown("#### 🚚 Báo Cáo Hiệu Quả Kinh Doanh Xe Tải Chở Thuê")
        st.info("💡 Hệ thống tự động lọc các khoản **Thu / Chi** thuộc hạng mục xe tải. Nó cũng tự động dò tìm các từ khóa cũ (như 'chở', 'cẩu', 'dầu', 'lốp') trong ghi chú cũ để bóc tách lợi nhuận chính xác nhất cho xe.")
        
        thang_loc_xe = st.selectbox("Chọn tháng xem báo cáo xe tải:", ["Tháng này", "Tháng trước", "Tất cả thời gian"])
        
        with get_connection() as conn:
            df_xe = pd.read_sql_query("SELECT thoi_gian, loai_phieu, hang_muc, so_tien, ghi_chu FROM so_quy ORDER BY thoi_gian DESC", conn.connection)
            
        if not df_xe.empty:
            df_xe['Date'] = pd.to_datetime(df_xe['thoi_gian'])
            
            # Lọc theo tháng
            if thang_loc_xe == "Tháng này":
                df_xe = df_xe[(df_xe['Date'].dt.month == now_dt.month) & (df_xe['Date'].dt.year == now_dt.year)]
            elif thang_loc_xe == "Tháng trước":
                prev_month = (now_dt.replace(day=1) - timedelta(days=1))
                df_xe = df_xe[(df_xe['Date'].dt.month == prev_month.month) & (df_xe['Date'].dt.year == prev_month.year)]
            
            # Hàm thuật toán AI nhỏ để nhận diện giao dịch xe tải (gồm cả list hạng mục mới và dữ liệu lịch sử cũ)
            def check_is_xe(row):
                hm = str(row['hang_muc']).lower()
                gc = str(row['ghi_chu']).lower()
                
                # Bắt theo Hạng mục mới
                if "xe tải" in hm or "sửa xe" in hm: return True
                
                # Bắt tự động các dữ liệu Ghi chú cũ bạn từng nhập
                if row['loai_phieu'] == "Thu" and ("chở" in gc or "cẩu" in gc or "xe" in gc): return True
                if row['loai_phieu'] == "Chi" and ("dầu" in gc or "xe" in gc or "lốp" in gc): return True
                
                return False
                
            df_xe['is_xe'] = df_xe.apply(check_is_xe, axis=1)
            df_xe_filtered = df_xe[df_xe['is_xe'] == True].copy()
            
            if not df_xe_filtered.empty:
                df_xe_filtered['so_tien'] = df_xe_filtered['so_tien'].apply(to_float)
                
                thu_xe = df_xe_filtered[df_xe_filtered['loai_phieu'] == 'Thu']['so_tien'].sum()
                chi_xe = df_xe_filtered[df_xe_filtered['loai_phieu'] == 'Chi']['so_tien'].sum()
                lai_xe = thu_xe - chi_xe
                
                c1, c2, c3 = st.columns(3)
                with c1: st.markdown(f"<div class='kpi-card border-green'><div class='kpi-label'>💵 Tổng Thu Chở Thuê</div><div class='kpi-value text-green'>+{fmt_vn(thu_xe)} đ</div></div>", unsafe_allow_html=True)
                with c2: st.markdown(f"<div class='kpi-card border-red'><div class='kpi-label'>⛽ Chi Phí Dầu/Phụ Tùng</div><div class='kpi-value text-red'>-{fmt_vn(chi_xe)} đ</div></div>", unsafe_allow_html=True)
                with c3: st.markdown(f"<div class='kpi-card border-purple'><div class='kpi-label'>📈 Lãi Ròng Của Xe</div><div class='kpi-value text-purple'>{fmt_vn(lai_xe)} đ</div></div>", unsafe_allow_html=True)
                
                st.markdown("##### 📜 Bảng Kê Chi Tiết Thu Chi Xe Tải")
                df_display_xe = df_xe_filtered[['thoi_gian', 'loai_phieu', 'hang_muc', 'so_tien', 'ghi_chu']].rename(columns={
                    'thoi_gian':'Thời Gian', 'loai_phieu':'Loại', 'hang_muc':'Hạng Mục', 'so_tien':'Số Tiền', 'ghi_chu':'Ghi Chú'
                })
                st.dataframe(df_display_xe.style.format({'Số Tiền': lambda x: fmt_vn(x)}), hide_index=True, use_container_width=True)
            else:
                st.info("Không có phát sinh thu/chi nào liên quan đến xe tải trong kỳ này.")
        else:
            st.info("Sổ quỹ đang trống.")
# PHÂN HỆ 4: SỔ QUẢN LÝ NỢ
# ==========================================
elif menu == "Sổ Quản Lý Nợ":
    st.markdown("### 💰 Quản Lý Dòng Tiền & Kế Toán Công Nợ")
    with get_connection() as conn: df_no = pd.read_sql_query('''SELECT dh.id, dh.ma_don_hien_thi as "Mã Đơn", dh.ngay_ban as "Ngày", kh.ten_khach as "Khách Hàng", dh.tong_tien as "Tổng Tiền", dh.tien_da_tra as "Đã Trả", dh.tien_con_no as "CÒN NỢ" FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.tien_con_no > 0 AND dh.trang_thai_giao = 'Đã hoàn thành' ''', conn.connection)
    if df_no.empty: st.success("Hệ thống sạch bóng nợ xấu. Không có dư nợ tồn đọng.")
    else:
        for col in ['Tổng Tiền', 'Đã Trả', 'CÒN NỢ']: df_no[col] = df_no[col].apply(to_float)
            
        st.dataframe(df_no.drop(columns=['id']).style.format({
            'Tổng Tiền': lambda x: fmt_vn(x), 'Đã Trả': lambda x: fmt_vn(x), 'CÒN NỢ': lambda x: fmt_vn(x)
        }), hide_index=True, use_container_width=True)
        
        st.markdown(f"<h4 style='color:#b91c1c;'>TỔNG DƯ NỢ HIỆN TẠI: {fmt_vn(df_no['CÒN NỢ'].sum())} VNĐ</h4>", unsafe_allow_html=True)
        with st.form("f_thu_no"):
            no_dict = dict(zip(df_no['id'], df_no['Mã Đơn'].astype(str) + " - " + df_no['Khách Hàng'].astype(str)))
            id_don_no = st.selectbox("Gạch nợ đơn:", options=list(no_dict.keys()), format_func=lambda x: no_dict.get(x))
            info_no = df_no[df_no['id'] == id_don_no].iloc[0] if not df_no[df_no['id'] == id_don_no].empty else None
            if info_no is not None:
                max_no = to_int(info_no['CÒN NỢ'])
                tien_thu = st.number_input("Số tiền thu gạch nợ (đ):", min_value=1, max_value=max_no if max_no > 0 else 1000000000, value=max_no if max_no > 0 else 1, step=10000, format="%d")
                ht_thu = st.selectbox("Hình thức:", ["Chuyển khoản", "Tiền mặt"])
                if st.form_submit_button("Xác Nhận Khấu Trừ Nợ", type="primary"):
                    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                    with get_connection() as c_update:
                        cur = c_update.cursor()
                        cur.execute("UPDATE don_hang SET tien_con_no=tien_con_no-?, tien_da_tra=tien_da_tra+?, da_thanh_toan=CASE WHEN tien_con_no-? <= 0 THEN 1 ELSE 0 END WHERE id=?", (tien_thu, tien_thu, tien_thu, to_int(id_don_no)))
                        lsid = get_next_id('lich_su_thanh_toan', cur)
                        cur.execute("INSERT INTO lich_su_thanh_toan (id, don_hang_id, so_tien_tra, hinh_thuc, ngay_tra, ghi_chu, nguoi_tao) VALUES (?,?,?,?,?,?,?)", (lsid, to_int(id_don_no), tien_thu, ht_thu, ts, "Cắt nợ bãi", st.session_state.current_user))
                        c_update.commit()
                    st.success("Đã cấn trừ công nợ!"); st.rerun()

# ==========================================
# PHÂN HỆ QUẢN LÝ TỒN KHO & ĐÁNH GIÁ
# ==========================================
elif menu == "Quản Lý Tồn Kho":
    st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📦 PHÂN TÍCH HÀNG TỒN & CHIẾN LƯỢC ĐẨY KHO</h1></div>", unsafe_allow_html=True)
    
    with get_connection() as conn:
        df_than = pd.read_sql_query("SELECT id, ten_than, ton_kho, gia_nhap_mac_dinh FROM loai_than", conn.connection)
        df_ban = pd.read_sql_query("SELECT ctdh.loai_than_id, ctdh.so_luong, dh.thoi_gian_tao FROM chi_tiet_don_hang ctdh JOIN don_hang dh ON ctdh.don_hang_id = dh.id WHERE dh.trang_thai_giao = 'Đã hoàn thành'", conn.connection)
        df_nhap = pd.read_sql_query("SELECT loai_than_id, MAX(ngay_nhap) as ngay_nhap_cuoi FROM nhap_hang GROUP BY loai_than_id", conn.connection)

    if df_than.empty: st.info("Danh mục kho hàng hiện đang trống.")
    else:
        if not df_ban.empty:
            df_ban['thoi_gian_tao'] = pd.to_datetime(df_ban['thoi_gian_tao'])
            date_30d_ago = now_dt.replace(tzinfo=None) - timedelta(days=30)
            df_ban_30d = df_ban[df_ban['thoi_gian_tao'] >= date_30d_ago]
            df_ban_30d['so_luong'] = df_ban_30d['so_luong'].apply(to_float)
            ban_grouped = df_ban_30d.groupby('loai_than_id')['so_luong'].sum().reset_index().rename(columns={'so_luong': 'Sức Bán 30 Ngày (kg)'})
        else: ban_grouped = pd.DataFrame(columns=['loai_than_id', 'Sức Bán 30 Ngày (kg)'])
        
        df_tonghop = pd.merge(df_than, ban_grouped, left_on='id', right_on='loai_than_id', how='left')
        df_tonghop = pd.merge(df_tonghop, df_nhap, left_on='id', right_on='loai_than_id', how='left')
        
        df_tonghop['Sức Bán 30 Ngày (kg)'] = df_tonghop['Sức Bán 30 Ngày (kg)'].fillna(0)
        df_tonghop['ton_kho'] = df_tonghop['ton_kho'].apply(to_float)
        df_tonghop['gia_nhap_mac_dinh'] = df_tonghop['gia_nhap_mac_dinh'].apply(to_float)
        df_tonghop['Tổng Vốn Tồn (đ)'] = df_tonghop['ton_kho'] * df_tonghop['gia_nhap_mac_dinh']
        
        def calc_days(date_str):
            if pd.isna(date_str) or not date_str: return 999
            try: return (now_dt.date() - pd.to_datetime(date_str).date()).days
            except: return 999
            
        df_tonghop['Số Ngày Lưu Bãi'] = df_tonghop['ngay_nhap_cuoi'].apply(calc_days)
        df_tonghop['Hiển thị ngày'] = df_tonghop['Số Ngày Lưu Bãi'].apply(lambda x: "Chưa rõ TT" if x == 999 else f"{x} ngày")
        
        def xep_loai(row):
            if row['ton_kho'] <= 0: return "⚪ Hết hàng"
            if row['Số Ngày Lưu Bãi'] > 60 and row['ton_kho'] > 0: return "⚠️ Tồn lâu (Cần thanh lý)"
            if row['ton_kho'] > (row['Sức Bán 30 Ngày (kg)'] * 3) and row['ton_kho'] > 500: return "🔥 Tồn nhiều (Cần đẩy)"
            if row['ton_kho'] <= (row['Sức Bán 30 Ngày (kg)'] * 0.3) or row['ton_kho'] < 200: return "📉 Sắp hết (Cần nhập)"
            return "✅ Ổn định"
            
        df_tonghop['Cảnh Báo Nhập/Xuất'] = df_tonghop.apply(xep_loai, axis=1)
        
        tong_von = df_tonghop['Tổng Vốn Tồn (đ)'].sum()
        tong_kg = df_tonghop['ton_kho'].sum()
        
        c1, c2 = st.columns(2)
        with c1: st.markdown(f"<div class='kpi-card border-purple'><div class='kpi-label'>💸 Tổng Vốn Đang Đọng (Tạm Tính)</div><div class='kpi-value text-purple'>{fmt_vn(tong_von)} VNĐ</div></div>", unsafe_allow_html=True)
        with c2: st.markdown(f"<div class='kpi-card border-red'><div class='kpi-label'>⚖️ Tổng Khối Lượng Toàn Bãi</div><div class='kpi-value text-red'>{fmt_vn(tong_kg)} kg</div></div>", unsafe_allow_html=True)
        
        st.markdown("#### Bảng Kê Vòng Quay Hàng Hóa Từng Chủng Loại")
        filter_tt = st.selectbox("Lọc theo trạng thái kho:", ["Tất cả", "⚠️ Tồn lâu (Cần thanh lý)", "🔥 Tồn nhiều (Cần đẩy)", "📉 Sắp hết (Cần nhập)", "✅ Ổn định", "⚪ Hết hàng"])
        
        df_show = df_tonghop.copy()
        if filter_tt != "Tất cả": df_show = df_show[df_show['Cảnh Báo Nhập/Xuất'] == filter_tt]
        
        df_show = df_show[['ten_than', 'ton_kho', 'Tổng Vốn Tồn (đ)', 'Sức Bán 30 Ngày (kg)', 'Hiển thị ngày', 'Cảnh Báo Nhập/Xuất']].rename(columns={'ten_than': 'Chủng Loại', 'ton_kho': 'Trữ Lượng Bãi (kg)', 'Hiển thị ngày': 'Nằm Bãi'})
        
        st.dataframe(df_show.style.format({
            'Trữ Lượng Bãi (kg)': lambda x: fmt_vn(x), 'Tổng Vốn Tồn (đ)': lambda x: fmt_vn(x), 'Sức Bán 30 Ngày (kg)': lambda x: fmt_vn(x)
        }), hide_index=True, use_container_width=True)
        st.download_button("📥 XUẤT BÁO CÁO TỒN KHO (EXCEL)", data=df_tonghop.to_csv(index=False, encoding='utf-8-sig'), file_name=f"BaoCao_TonKho_{today_str}.csv", mime="text/csv", type="primary")

# ==========================================
# ==========================================
# ==========================================
# PHÂN HỆ 5: LỊCH SỬ ĐƠN HÀNG & HOÀN TRẢ
# ==========================================
elif menu == "Lịch Sử Đơn Hàng":
    st.markdown("### 🗂️ Tra Cứu Lịch Sử & Xử Lý Hoàn Trả")
    has_high_clearance = (st.session_state.user_role in ['admin', 'manager'])
    
    if has_high_clearance: 
        tab_tongquan, tab_hoantra, tab_chitiet = st.tabs(["📋 Tổng Quan Đơn Hàng", "🔄 Xử Lý Khách Trả Hàng", "📊 Sổ Cái Bán Hàng Chi Tiết"])
    else: 
        tab_tongquan = st.container()
        
    with tab_tongquan:
        st.markdown("#### Nhật ký các chuyến xe đã giao")
        with get_connection() as conn: 
            # Đã bổ sung thêm cột Trạng Thái báo nợ / thu đủ
            df_his = pd.read_sql_query('''
                SELECT 
                    dh.ma_don_hien_thi as "Mã Đơn", dh.thoi_gian_tao as "Ngày Giờ", kh.ten_khach as "Khách Hàng", 
                    nv.ten_nhan_vien as "Tài Xế", dh.tong_tien as "Tổng Tiền", dh.tien_con_no as "Nợ Lại",
                    CASE WHEN dh.tien_con_no <= 0 THEN '✅ Đã thu đủ' ELSE '⚠️ Còn nợ' END as "Trạng Thái",
                    dh.nguoi_tao as "Người Lập" 
                FROM don_hang dh 
                JOIN khach_hang kh ON dh.khach_hang_id = kh.id 
                LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id 
                WHERE dh.trang_thai_giao = 'Đã hoàn thành' ORDER BY dh.id DESC
            ''', conn.connection)
        
        if not df_his.empty:
            for col in ['Tổng Tiền', 'Nợ Lại']: df_his[col] = df_his[col].apply(to_float)
            st.dataframe(df_his.style.format({'Tổng Tiền': lambda x: fmt_vn(x), 'Nợ Lại': lambda x: fmt_vn(x)}), hide_index=True, use_container_width=True)
            st.download_button("📥 XUẤT BÁO CÁO EXCEL TRẠM", data=df_his.to_csv(index=False, encoding='utf-8-sig'), file_name=f"BaoCao_TrangThai_DonHang_{today_str}.csv", mime="text/csv")
        else: st.info("Chưa có chuyến xe nào hoàn thành.")

    if has_high_clearance:
        # === TAB: XỬ LÝ HOÀN TRẢ SAU KHI GIAO XONG ===
        with tab_hoantra:
            st.markdown("#### 📦 Nhập lại kho hàng hoàn (Đơn đã hoàn thành)")
            st.info("💡 Nếu khách phàn nàn và yêu cầu trả lại hàng sau khi đã giao xong, dùng chức năng này để thu hồi hàng về bãi và tự động trừ giảm doanh thu/công nợ.")
            
            with get_connection() as conn:
                df_done = pd.read_sql_query('''SELECT dh.id, dh.ma_don_hien_thi, kh.ten_khach, dh.tong_tien, dh.tien_da_tra, dh.tien_con_no 
                                               FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id 
                                               WHERE dh.trang_thai_giao = 'Đã hoàn thành' ORDER BY dh.id DESC LIMIT 100''', conn.connection)
            
            if df_done.empty:
                st.write("Chưa có hóa đơn nào hoàn thành để xử lý trả hàng.")
            else:
                don_dict = dict(zip(df_done['id'], df_done['ma_don_hien_thi'] + " - " + df_done['ten_khach']))
                id_don_hoan = st.selectbox("1. Chọn hóa đơn khách muốn trả hàng:", options=list(don_dict.keys()), format_func=lambda x: don_dict.get(x))
                
                if id_don_hoan:
                    with get_connection() as conn:
                        # Đã dùng LEFT JOIN tránh lỗi mất hàng
                        ct_hoan = pd.read_sql_query(f"SELECT c.id, c.loai_than_id, c.so_luong, c.don_gia, COALESCE(l.ten_than, 'Mặt hàng đã bị xóa') as ten_than FROM chi_tiet_don_hang c LEFT JOIN loai_than l ON c.loai_than_id = l.id WHERE c.don_hang_id={to_int(id_don_hoan)}", conn.connection)
                    
                    with st.form("form_hoan_tra_sau"):
                        st.write("2. **Ghi nhận số lượng (kg) khách thực tế trả về bãi:**")
                        return_inputs_ht = []
                        for _, ct in ct_hoan.iterrows():
                            ret_qty = st.number_input(f"Thu hồi - {ct['ten_than']} (Đã mua: {ct['so_luong']} kg):", min_value=0.0, max_value=to_float(ct['so_luong']), value=0.0, step=10.0, key=f"hoan_{id_don_hoan}_{ct['id']}")
                            return_inputs_ht.append({'ct_id': ct['id'], 'loai_than_id': ct['loai_than_id'], 'ret_qty': ret_qty, 'don_gia': ct['don_gia'], 'ten_than': ct['ten_than']})
                        
                        ghi_chu_hoan = st.text_input("3. Lý do trả hàng (ghi chú lại để nhắc nhở bãi):")
                        
                        if st.form_submit_button("XÁC NHẬN NHẬP KHO & TRỪ CÔNG NỢ", type="primary"):
                            tien_giam_tru = sum(item['ret_qty'] * item['don_gia'] for item in return_inputs_ht)
                            if tien_giam_tru <= 0:
                                st.error("Vui lòng nhập số lượng lớn hơn 0 để tiến hành hoàn trả.")
                            else:
                                don_info = df_done[df_done['id'] == id_don_hoan].iloc[0]
                                tong_tien_cu = to_float(don_info['tong_tien'])
                                tien_da_tra_cu = to_float(don_info['tien_da_tra'])
                                
                                tong_tien_moi = tong_tien_cu - tien_giam_tru
                                tien_con_no_moi = tong_tien_moi - tien_da_tra_cu
                                da_thanh_toan_moi = 1 if tien_con_no_moi <= 0 else 0
                                
                                with get_connection() as c_update:
                                    cur = c_update.cursor()
                                    for item in return_inputs_ht:
                                        if item['ret_qty'] > 0:
                                            cur.execute("UPDATE chi_tiet_don_hang SET so_luong = so_luong - ? WHERE id=?", (item['ret_qty'], item['ct_id']))
                                            cur.execute("UPDATE loai_than SET ton_kho = ton_kho + ? WHERE id=?", (item['ret_qty'], item['loai_than_id']))
                                    
                                    cur.execute("UPDATE don_hang SET tong_tien=?, tien_con_no=?, da_thanh_toan=?, ghi_chu = ghi_chu || ? WHERE id=?", 
                                                (tong_tien_moi, tien_con_no_moi, da_thanh_toan_moi, f"\n[Hoàn trả: {ghi_chu_hoan} - Giảm {fmt_vn(tien_giam_tru)}đ]", to_int(id_don_hoan)))
                                    c_update.commit()
                                
                                try:
                                    send_tele_notify(f"⚠️ [CẢNH BÁO TRẢ HÀNG]\n- Mã đơn: {don_info['ma_don_hien_thi']}\n- Khách: {don_info['ten_khach']}\n- Giá trị bị giảm trừ: {fmt_vn(tien_giam_tru)} VNĐ\n- Lý do: {ghi_chu_hoan}\n- Người duyệt: {st.session_state.current_user}")
                                except: pass
                                
                                st.success(f"Thành công! Đã thu hồi hàng về bãi và trừ lùi {fmt_vn(tien_giam_tru)}đ vào hóa đơn.")
                                st.rerun()

        # === TAB CHI TIẾT SỔ CÁI MẶT HÀNG ===
        with tab_chitiet:
            st.markdown("#### Báo cáo chi tiết từng mặt hàng xuất kho")
            st.caption("🔒 Khu vực dữ liệu bảo mật: Tổng hợp số lượng, đơn giá, nợ đọng theo từng chủng loại và người giao xe.")
            
            with get_connection() as conn:
                # Cấu trúc LEFT JOIN khép kín: Dù xóa than hay xóa tài xế, lịch sử sổ cái vẫn không bị biến mất!
                df_detail = pd.read_sql_query('''
                    SELECT 
                        dh.ngay_ban as "Ngày Bán", dh.ma_don_hien_thi as "Mã Hóa Đơn", kh.ten_khach as "Khách Hàng", 
                        COALESCE(nv.ten_nhan_vien, 'Chưa phân xe') as "Người Giao",
                        COALESCE(lt.ten_than, 'Mặt hàng đã xóa') as "Loại Than", 
                        COALESCE(ctdh.so_luong, 0) as "Số Lượng (kg)", 
                        COALESCE(ctdh.don_gia, 0) as "Đơn Giá (đ)",
                        (COALESCE(ctdh.so_luong, 0) * COALESCE(ctdh.don_gia, 0)) as "Thành Tiền (đ)", 
                        dh.tien_con_no as "Nợ Lại (đ)",
                        CASE WHEN dh.tien_con_no <= 0 THEN '✅ Đã thu đủ' ELSE '⚠️ Đang báo nợ' END as "Trạng Thái"
                    FROM don_hang dh 
                    LEFT JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id 
                    JOIN khach_hang kh ON dh.khach_hang_id = kh.id
                    LEFT JOIN loai_than lt ON ctdh.loai_than_id = lt.id 
                    LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id
                    WHERE dh.trang_thai_giao = 'Đã hoàn thành' ORDER BY dh.thoi_gian_tao DESC
                ''', conn.connection)
            
            if not df_detail.empty:
                c1, c2, c3 = st.columns(3)
                with c1: f_khach = st.selectbox("Lọc theo Khách Hàng:", ["Tất cả"] + list(df_detail['Khách Hàng'].unique()))
                with c2: f_than = st.selectbox("Lọc theo Chủng Loại:", ["Tất cả"] + list(df_detail['Loại Than'].unique()))
                with c3: f_tt = st.selectbox("Lọc Trạng Thái Nợ:", ["Tất cả", "✅ Đã thu đủ", "⚠️ Đang báo nợ"])
                    
                df_filtered = df_detail.copy()
                if f_khach != "Tất cả": df_filtered = df_filtered[df_filtered['Khách Hàng'] == f_khach]
                if f_than != "Tất cả": df_filtered = df_filtered[df_filtered['Loại Than'] == f_than]
                if f_tt != "Tất cả": df_filtered = df_filtered[df_filtered['Trạng Thái'] == f_tt]

                for col in ['Số Lượng (kg)', 'Đơn Giá (đ)', 'Thành Tiền (đ)', 'Nợ Lại (đ)']: df_filtered[col] = df_filtered[col].apply(to_float)
                    
                st.dataframe(df_filtered.style.format({
                    'Số Lượng (kg)': lambda x: fmt_vn(x), 'Đơn Giá (đ)': lambda x: fmt_vn(x), 
                    'Thành Tiền (đ)': lambda x: fmt_vn(x), 'Nợ Lại (đ)': lambda x: fmt_vn(x)
                }), hide_index=True, use_container_width=True)
                
                tong_sl = df_filtered['Số Lượng (kg)'].sum()
                tong_tien = df_filtered['Thành Tiền (đ)'].sum()
                tong_no = df_filtered.drop_duplicates(subset=['Mã Hóa Đơn'])['Nợ Lại (đ)'].sum() if not df_filtered.empty else 0
                
                st.markdown(f"""
                    <div style='background-color: #f0fdf4; padding: 15px; border-radius: 8px; border-left: 5px solid #22c55e; margin-top: 10px; margin-bottom: 20px;'>
                        <h4 style='color: #166534; margin: 0;'>TỔNG HỢP THEO BỘ LỌC HIỆN TẠI</h4>
                        <span style='font-size: 15px; color: #15803d;'>
                            📦 Tổng khối lượng: <b>{fmt_vn(tong_sl)} kg</b> &nbsp;&nbsp;|&nbsp;&nbsp; 
                            💵 Tổng tiền hàng: <b>{fmt_vn(tong_tien)} đ</b> &nbsp;&nbsp;|&nbsp;&nbsp; 
                            🚨 Tổng nợ đọng: <b style='color:#dc2626;'>{fmt_vn(tong_no)} đ</b>
                        </span>
                    </div>
                """, unsafe_allow_html=True)
                st.download_button("📥 XUẤT SỔ CÁI BÁN HÀNG CHI TIẾT (EXCEL)", data=df_filtered.to_csv(index=False, encoding='utf-8-sig'), file_name=f"SoCai_BanHang_ChiTiet_{today_str}.csv", mime="text/csv", type="primary")
            else:
                st.info("📭 Bảng dữ liệu đang trống. Khi có đơn hàng được giao hoàn thành, hệ thống sẽ tự động thống kê sổ cái chi tiết tại đây.")
# ==========================================
# PHÂN HỆ 6: CÀI ĐẶT HỆ THỐNG - BẢN MASTER/DETAIL MỚI
# ==========================================
elif menu == "Cài Đặt Hệ Thống":
    st.markdown("### ⚙️ Cấu Hình Danh Mục Cơ Sở Dữ Liệu")
    tabs_list = ["1. Danh Mục Loại Than", "2. Quản Lý Khách Hàng", "3. Quản Lý Tài Xế", "4. Phân Quyền Giá Riêng", "5. Hệ Thống (In Bill & Zalo Bot)"]
    if st.session_state.user_role == 'admin': tabs_list.extend(["6. Quản Lý Tài Khoản", "7. System Log"])
    tab_sys = st.selectbox("Chọn hạng mục:", tabs_list)
    
 # ------------------ 1. LOẠI THAN (TÍNH GIÁ VỐN MAC & QUY CÁCH ĐÓNG HÀNG) ------------------
    if tab_sys == "1. Danh Mục Loại Than":
        if 'edit_nh_id' not in st.session_state: st.session_state.edit_nh_id = None
        with get_connection() as conn: 
            df_t = pd.read_sql_query("SELECT id, ten_than as 'Tên Loại Than', gia_nhap_mac_dinh as 'Giá Vốn (đ)', gia_mac_dinh as 'Giá Bán (đ)', ton_kho as 'Tồn Kho (kg)', don_vi_tinh, he_so_kg FROM loai_than", conn.connection)
            # Thêm id và loai_than_id để làm tính năng sửa xóa
            df_nhap = pd.read_sql_query('''SELECT nh.id, nh.loai_than_id, nh.ngay_nhap as "Ngày", lt.ten_than as "Loại Than", nh.xuong_nhap as "Xưởng", nh.so_luong as "SL (kg)", nh.don_gia_nhap as "Giá Nhập" FROM nhap_hang nh JOIN loai_than lt ON nh.loai_than_id = lt.id ORDER BY nh.id DESC''', conn.connection)
            
        t_sub1, t_sub2 = st.tabs(["📋 Danh Mục Hàng Hóa", "🚢 Nhập Hàng & Giá Vốn"])
        with t_sub1:
            if st.session_state.edit_t_id is not None:
                edit_id = st.session_state.edit_t_id
                t_info = df_t[df_t['id'] == edit_id].iloc[0]
                st.markdown(f"<div class='edit-box'><h4>✏️ ĐANG CHỈNH SỬA: {t_info['Tên Loại Than']}</h4></div>", unsafe_allow_html=True)
                with st.form("f_edit_than_master"):
                    en = st.text_input("Tên chủng loại:", value=t_info['Tên Loại Than'])
                    
                    # ĐÃ BỔ SUNG Ô NHẬP GIÁ VỐN (GIÁ NHẬP)
                    c_g1, c_g2 = st.columns(2)
                    with c_g1: ep_nhap = st.number_input("Giá mua gốc / Giá Vốn bãi (đ/kg):", value=to_int(t_info['Giá Vốn (đ)']), step=500, format="%d")
                    with c_g2: ep = st.number_input("Đơn giá bán niêm yết (đ/kg):", value=to_int(t_info['Giá Bán (đ)']), step=500, format="%d")
                    
                    c_dv1, c_dv2 = st.columns(2)
                    with c_dv1: e_dv = st.selectbox("Đơn vị tính (Quy cách đóng gói):", ["kg", "Thùng", "Bao", "Hộp"], index=["kg", "Thùng", "Bao", "Hộp"].index(t_info['don_vi_tinh']) if t_info['don_vi_tinh'] in ["kg", "Thùng", "Bao", "Hộp"] else 0)
                    with c_dv2: e_hs = st.number_input("Quy cách (Số kg / 1 Đơn vị):", min_value=1.0, value=to_float(t_info['he_so_kg']) if to_float(t_info['he_so_kg']) > 0 else 1.0, help="Ví dụ 1 Thùng nặng 20kg thì nhập 20.")
                    
                    bc1, bc2 = st.columns([1, 10])
                    with bc1:
                        if st.form_submit_button("💾 LƯU", type="primary"):
                            if e_dv.lower() == 'kg': e_hs = 1.0
                            with get_connection() as conn: 
                                # Cập nhật cả gia_nhap_mac_dinh vào database
                                conn.execute("UPDATE loai_than SET ten_than=?, gia_nhap_mac_dinh=?, gia_mac_dinh=?, don_vi_tinh=?, he_so_kg=? WHERE id=?", (en.strip(), ep_nhap, ep, e_dv, e_hs, edit_id))
                                conn.commit()
                            st.session_state.edit_t_id = None; st.rerun()
                    with bc2:
                        if st.form_submit_button("Hủy bỏ"): st.session_state.edit_t_id = None; st.rerun()
                st.markdown("---")
            elif can_edit:
                st.markdown("#### Thêm Chủng Loại Mới")
                with st.form("f_c_add"):
                    c1, c2, c3 = st.columns(3)
                    with c1: n = st.text_input("Tên chủng loại than mới:")
                    with c2: pn = st.number_input("Giá mua gốc nhập kho (đ/kg):", value=1500, step=500, format="%d")
                    with c3: p = st.number_input("Giá bán niêm yết (đ/kg):", value=3000, step=500, format="%d")
                    
                    c4, c5 = st.columns(2)
                    with c4: dv = st.selectbox("Đơn vị tính (Quy cách đóng gói):", ["kg", "Thùng", "Bao", "Hộp"])
                    with c5: hs = st.number_input("Quy cách (Số kg / 1 Đơn vị):", min_value=1.0, value=1.0, help="Ví dụ 1 Thùng nặng 20kg thì nhập 20.")
                    
                    if st.form_submit_button("Lưu Thêm"):
                        if dv.lower() == 'kg': hs = 1.0
                        with get_connection() as conn: 
                            conn.execute("INSERT INTO loai_than(id, ten_than, gia_nhap_mac_dinh, gia_mac_dinh, ton_kho, nguoi_tao, don_vi_tinh, he_so_kg) VALUES(?,?,?,?,?,?,?,?)", 
                                         (get_next_id('loai_than', conn.cursor()), n.strip(), pn, p, 0, st.session_state.current_user, dv, hs))
                            conn.commit()
                        st.success("Đã thêm chủng loại!"); st.rerun()
                st.markdown("---")
            
            st.markdown("#### 📋 Quản Lý Chủng Loại Hàng Hóa")
            if not df_t.empty:
                c1, c2, c3, c4, c5, c6 = st.columns([2.5, 1.5, 1.5, 1.5, 1.5, 1.5])
                c1.markdown("<b>Tên Loại Than</b>", unsafe_allow_html=True); c2.markdown("<b>Quy Cách</b>", unsafe_allow_html=True)
                c3.markdown("<b>Giá Vốn/kg</b>", unsafe_allow_html=True); c4.markdown("<b>Giá Bán/kg</b>", unsafe_allow_html=True)
                c5.markdown("<b>Tồn Kho</b>", unsafe_allow_html=True)
                if can_edit: c6.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
                
                for idx, r in df_t.iterrows():
                    with st.container():
                        if can_edit: cc1, cc2, cc3, cc4, cc5, cc6, cc7 = st.columns([2.5, 1.5, 1.5, 1.5, 1.5, 0.75, 0.75])
                        else: cc1, cc2, cc3, cc4, cc5 = st.columns([2.5, 1.5, 1.5, 1.5, 1.5])
                        
                        cc1.markdown(f"<div class='list-row'>{r['Tên Loại Than']}</div>", unsafe_allow_html=True)
                        qc_str = f"1 {r['don_vi_tinh']} = {fmt_vn(r['he_so_kg'])} kg" if r['don_vi_tinh'].lower() != 'kg' else "kg (rời)"
                        cc2.markdown(f"<div class='list-row'>{qc_str}</div>", unsafe_allow_html=True)
                        cc3.markdown(f"<div class='list-row'>{fmt_vn(r['Giá Vốn (đ)'])} đ</div>", unsafe_allow_html=True)
                        cc4.markdown(f"<div class='list-row'>{fmt_vn(r['Giá Bán (đ)'])} đ</div>", unsafe_allow_html=True)
                        cc5.markdown(f"<div class='list-row'>{fmt_vn(r['Tồn Kho (kg)'])} kg</div>", unsafe_allow_html=True)
                        
                        if can_edit:
                            with cc6: 
                                if st.button("✏️", key=f"edit_t_{r['id']}"): st.session_state.edit_t_id = r['id']; st.rerun()
                            with cc7: 
                                if st.button("❌", key=f"del_t_{r['id']}"): cb_xoa_than(r['id']); st.rerun()

        with t_sub2:
            st.subheader("📦 Ghi nhận tàu nhập bãi & Tính giá vốn")
            st.info("Hệ thống sẽ tự động tính **Giá Vốn Bình Quân Gia Quyền** dựa trên số than đang tồn và giá nhập của chuyến tàu mới này để đưa ra mức Giá Vốn chuẩn xác nhất cho kế toán.")
            with st.form("f_c_in"):
                if not df_t.empty:
                    than_dict = dict(zip(df_t['id'], df_t['Tên Loại Than'].astype(str)))
                    id_n = st.selectbox("Chủng loại:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x))
                    xuong = st.text_input("Nguồn nhập / Tên mỏ:")
                    w_in = st.number_input("Khối lượng nhập bãi (kg):", min_value=1.0, value=1000.0, step=100.0)
                    p_in = st.number_input("Giá cả chuyến nhập (đ/kg):", value=1500, step=500, format="%d")
                    if st.form_submit_button("Xác nhận lệnh nhập & Hòa trộn giá vốn"):
                        ton_kho_cu = to_float(df_t[df_t['id']==to_int(id_n)]['Tồn Kho (kg)'].values[0])
                        gia_von_cu = to_float(df_t[df_t['id']==to_int(id_n)]['Giá Vốn (đ)'].values[0])
                        
                        tong_tien_cu = ton_kho_cu * gia_von_cu
                        tong_tien_moi = w_in * p_in
                        ton_kho_moi = ton_kho_cu + w_in
                        gia_von_moi = (tong_tien_cu + tong_tien_moi) / ton_kho_moi if ton_kho_moi > 0 else 0

                        with get_connection() as conn: 
                            nid = get_next_id('nhap_hang', conn.cursor())
                            conn.execute('''INSERT INTO nhap_hang(id, loai_than_id, ngay_nhap, xuong_nhap, so_luong, don_gia_nhap, nguoi_tao) VALUES(?,?,?,?,?,?,?)''', (nid, to_int(id_n), today_str, xuong, w_in, p_in, st.session_state.current_user))
                            conn.execute("UPDATE loai_than SET ton_kho=?, gia_nhap_mac_dinh=? WHERE id=?", (ton_kho_moi, gia_von_moi, to_int(id_n)))
                            conn.commit()
                        st.success(f"Nhập bãi thành công! Giá vốn kho bãi vừa được điều chỉnh thành {fmt_vn(gia_von_moi)} đ/kg."); st.rerun()
            
            # --- ĐÃ BỔ SUNG GIAO DIỆN SỬA / XÓA LỊCH SỬ NHẬP HÀNG TẠI ĐÂY ---
            st.markdown("#### 📜 Lịch Sử Chuyến Nhập (Hỗ trợ Khấu trừ Kho)")
            if st.session_state.edit_nh_id is not None:
                e_nh_id = st.session_state.edit_nh_id
                nh_info = df_nhap[df_nhap['id'] == e_nh_id].iloc[0]
                st.markdown(f"<div class='edit-box'><h4>✏️ ĐANG SỬA CHUYẾN NHẬP: {nh_info['Loại Than']} - {nh_info['Ngày']}</h4></div>", unsafe_allow_html=True)
                with st.form("f_edit_nhap"):
                    c1, c2, c3 = st.columns(3)
                    with c1: e_nx = st.text_input("Nguồn nhập / Xưởng:", value=str(nh_info['Xưởng']))
                    with c2: e_nsl = st.number_input("Khối lượng (kg):", value=to_float(nh_info['SL (kg)']), step=100.0)
                    with c3: e_ng = st.number_input("Giá Nhập (đ/kg):", value=to_int(nh_info['Giá Nhập']), step=500)
                    
                    b1, b2 = st.columns([1, 10])
                    with b1:
                        if st.form_submit_button("💾 LƯU", type="primary"):
                            sl_cu = to_float(nh_info['SL (kg)'])
                            gia_cu = to_float(nh_info['Giá Nhập'])
                            lt_id = to_int(nh_info['loai_than_id'])
                            with get_connection() as c_up:
                                lt_info = c_up.cursor().execute("SELECT ton_kho, gia_nhap_mac_dinh FROM loai_than WHERE id=?", (lt_id,)).fetchone()
                                if lt_info:
                                    ton_ht = to_float(lt_info[0]); gia_von_ht = to_float(lt_info[1])
                                    # Lùi kho cái cũ, nạp kho cái mới
                                    ton_moi = ton_ht - sl_cu + e_nsl
                                    tong_tien_ht = ton_ht * gia_von_ht
                                    tong_tien_moi = tong_tien_ht - (sl_cu * gia_cu) + (e_nsl * e_ng)
                                    gia_von_moi = tong_tien_moi / ton_moi if ton_moi > 0 else 0
                                    c_up.execute("UPDATE loai_than SET ton_kho=?, gia_nhap_mac_dinh=? WHERE id=?", (ton_moi, gia_von_moi, lt_id))
                                
                                c_up.execute("UPDATE nhap_hang SET xuong_nhap=?, so_luong=?, don_gia_nhap=? WHERE id=?", (e_nx, e_nsl, e_ng, e_nh_id))
                                c_up.commit()
                            st.session_state.edit_nh_id = None; st.rerun()
                    with b2:
                        if st.form_submit_button("Hủy"): st.session_state.edit_nh_id = None; st.rerun()
                st.markdown("---")

            if not df_nhap.empty:
                c1, c2, c3, c4, c5, c6 = st.columns([1.5, 2, 2, 1.5, 1.5, 1.5])
                c1.markdown("<b>Ngày Nhập</b>", unsafe_allow_html=True); c2.markdown("<b>Loại Than</b>", unsafe_allow_html=True)
                c3.markdown("<b>Xưởng</b>", unsafe_allow_html=True); c4.markdown("<b>SL (kg)</b>", unsafe_allow_html=True)
                c5.markdown("<b>Giá Nhập</b>", unsafe_allow_html=True)
                if can_edit: c6.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
                
                for _, r in df_nhap.head(50).iterrows(): # Hiển thị 50 dòng mới nhất
                    with st.container():
                        if can_edit: cc1, cc2, cc3, cc4, cc5, cc6, cc7 = st.columns([1.5, 2, 2, 1.5, 1.5, 0.75, 0.75])
                        else: cc1, cc2, cc3, cc4, cc5 = st.columns([1.5, 2, 2, 1.5, 1.5])
                        
                        cc1.markdown(f"<div class='list-row'>{r['Ngày']}</div>", unsafe_allow_html=True)
                        cc2.markdown(f"<div class='list-row'>{r['Loại Than']}</div>", unsafe_allow_html=True)
                        cc3.markdown(f"<div class='list-row'>{r['Xưởng']}</div>", unsafe_allow_html=True)
                        cc4.markdown(f"<div class='list-row'>{fmt_vn(r['SL (kg)'])}</div>", unsafe_allow_html=True)
                        cc5.markdown(f"<div class='list-row'>{fmt_vn(r['Giá Nhập'])} đ</div>", unsafe_allow_html=True)
                        
                        if can_edit:
                            with cc6:
                                if st.button("✏️", key=f"enh_{r['id']}"): st.session_state.edit_nh_id = r['id']; st.rerun()
                            with cc7:
                                if st.button("❌", key=f"dnh_{r['id']}"):
                                    # Xóa sẽ tự động trừ lùi Tồn Kho và Giá Vốn bãi
                                    lt_id = r['loai_than_id']
                                    sl_cu = to_float(r['SL (kg)'])
                                    gia_cu = to_float(r['Giá Nhập'])
                                    with get_connection() as c_del:
                                        lt_info = c_del.cursor().execute("SELECT ton_kho, gia_nhap_mac_dinh FROM loai_than WHERE id=?", (to_int(lt_id),)).fetchone()
                                        if lt_info:
                                            ton_ht = to_float(lt_info[0]); gia_von_ht = to_float(lt_info[1])
                                            ton_moi = ton_ht - sl_cu
                                            tong_tien_ht = ton_ht * gia_von_ht
                                            tong_tien_moi = tong_tien_ht - (sl_cu * gia_cu)
                                            gia_von_moi = tong_tien_moi / ton_moi if ton_moi > 0 else 0
                                            c_del.execute("UPDATE loai_than SET ton_kho=?, gia_nhap_mac_dinh=? WHERE id=?", (ton_moi, gia_von_moi, to_int(lt_id)))
                                        c_del.execute("DELETE FROM nhap_hang WHERE id=?", (r['id'],))
                                        c_del.commit()
                                    st.rerun()
            else:
                st.info("Chưa có dữ liệu nhập hàng lưu trong hệ thống.")

# ------------------ 2. KHÁCH HÀNG ------------------
    elif tab_sys == "2. Quản Lý Khách Hàng":
        with get_connection() as conn: 
            # Đã bổ sung lấy thêm cột ghi_chu_kh từ DB
            df_k = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps, lat, lon, han_muc_no, ghi_chu_kh FROM khach_hang", conn.connection)
        
        # === KHU VỰC SỬA FULL MÀN HÌNH ===
        if st.session_state.edit_kh_id is not None:
            edit_id = st.session_state.edit_kh_id
            k_info = df_k[df_k['id'] == edit_id].iloc[0]
            st.markdown(f"<div class='edit-box'><h4>✏️ ĐANG CHỈNH SỬA: {k_info['ten_khach']}</h4></div>", unsafe_allow_html=True)
            with st.form("f_edit_kh_master"):
                c1, c2 = st.columns(2)
                with c1:
                    ekn = st.text_input("Tên đối tác:", value=k_info['ten_khach'])
                    ekp = st.text_input("Liên hệ SĐT:", value=k_info['sdt'])
                    ekk = st.text_input("Tuyến đường / Khu vực:", value=k_info['khu_vuc'])
                    ek_diachi = st.text_input("Địa chỉ bãi nhận:", value=k_info['dia_chi'])
                with c2:
                    toado_hien_tai = f"{k_info['lat']}, {k_info['lon']}" if k_info['lat'] != 0.0 else ""
                    ek_toado = st.text_input("Tọa độ bản đồ (Lat, Lon):", value=toado_hien_tai, help="Nhập: Lat, Lon (VD: 21.0285, 105.8542)")
                    ek_hm = st.number_input("Hạn mức công nợ tối đa (đ):", min_value=0, value=to_int(k_info.get('han_muc_no', 0)), step=1000000)
                    ek_note = st.text_area("Ghi chú / Yêu cầu đặc biệt:", value=k_info.get('ghi_chu_kh', ''), height=110)
                
                bc1, bc2 = st.columns([1, 10])
                with bc1:
                    if st.form_submit_button("💾 LƯU", type="primary"):
                        lat, lon = parse_coords(ek_toado)
                        generated_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat != 0.0 else ""
                        with get_connection() as conn: 
                            conn.execute("UPDATE khach_hang SET ten_khach=?, sdt=?, khu_vuc=?, dia_chi=?, lat=?, lon=?, link_google_maps=?, han_muc_no=?, ghi_chu_kh=? WHERE id=?", 
                                         (ekn.strip(), ekp, ekk, ek_diachi, lat, lon, generated_link, ek_hm, ek_note, edit_id))
                            conn.commit()
                        st.session_state.edit_kh_id = None; st.rerun()
                with bc2:
                    if st.form_submit_button("Hủy bỏ"): st.session_state.edit_kh_id = None; st.rerun()
            st.markdown("---")

        # === KHU VỰC THÊM MỚI ===
        elif can_edit:
            st.markdown("#### ➕ Thêm Đối Tác Mới")
            with st.form("f_k_add"):
                c1, c2 = st.columns(2)
                with c1: 
                    kn = st.text_input("Tên KH:")
                    kp = st.text_input("SĐT:")
                    kkv = st.text_input("Tuyến đường / Khu vực:")
                    kd = st.text_input("Địa chỉ bãi nhận:")
                with c2: 
                    k_toado = st.text_input("Tọa độ bản đồ (Lat, Lon):", placeholder="VD: 21.0285, 105.8542")
                    kh_hm = st.number_input("Hạn mức nợ tối đa (đ):", min_value=0, value=0, step=1000000, help="Để 0 = Nợ không giới hạn")
                    kh_note = st.text_area("Ghi chú / Yêu cầu đặc biệt:", height=110)
                
                if st.form_submit_button("Lưu Hồ Sơ", type="primary"):
                    lat, lon = parse_coords(k_toado)
                    generated_link = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}" if lat != 0.0 else ""
                    with get_connection() as conn:
                        nid = get_next_id('khach_hang', conn.cursor())
                        conn.execute("INSERT INTO khach_hang (id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps, nguoi_tao, lat, lon, han_muc_no, ghi_chu_kh) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", 
                                     (nid, f"KH{nid:04d}", kn.strip(), kp, kd, kkv, generated_link, st.session_state.current_user, lat, lon, kh_hm, kh_note))
                        conn.commit()
                    st.success("Đã lưu hồ sơ đối tác!"); st.rerun()
            st.markdown("---")

        # === DANH SÁCH KHÁCH HÀNG KÈM NÚT SỬA/XÓA TRỰC TIẾP ===
        st.markdown("#### 📋 Quản Lý Hồ Sơ Đối Tác")
        if not df_k.empty: 
            # Đã chia lại tỷ lệ 6 cột để nhét vừa cột Ghi chú
            c1, c2, c3, c4, c5, c6 = st.columns([1.2, 2.5, 2.0, 2.0, 3.3, 1.5])
            c1.markdown("<b>Mã KH</b>", unsafe_allow_html=True)
            c2.markdown("<b>Tên Khách</b>", unsafe_allow_html=True)
            c3.markdown("<b>SĐT</b>", unsafe_allow_html=True)
            c4.markdown("<b>Hạn Mức</b>", unsafe_allow_html=True)
            c5.markdown("<b>Ghi chú</b>", unsafe_allow_html=True)
            if can_edit: c6.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
            
            for idx, r in df_k.iterrows():
                with st.container():
                    if can_edit: 
                        # Nút sửa và xóa được tách ra (0.75 + 0.75 = 1.5)
                        cc1, cc2, cc3, cc4, cc5, cc6, cc7 = st.columns([1.2, 2.5, 2.0, 2.0, 3.3, 0.75, 0.75])
                    else: 
                        cc1, cc2, cc3, cc4, cc5 = st.columns([1.2, 2.5, 2.0, 2.0, 3.3])
                        
                    cc1.markdown(f"<div class='list-row'>{r['ma_khach_hang']}</div>", unsafe_allow_html=True)
                    cc2.markdown(f"<div class='list-row'>{r['ten_khach']}</div>", unsafe_allow_html=True)
                    cc3.markdown(f"<div class='list-row'>{r['sdt']}</div>", unsafe_allow_html=True)
                    
                    hm_str = fmt_vn(r['han_muc_no']) + " đ" if r['han_muc_no'] > 0 else "Vô hạn"
                    cc4.markdown(f"<div class='list-row'>{hm_str}</div>", unsafe_allow_html=True)
                    
                    # Xử lý hiển thị ghi chú, nếu trống thì để dấu -
                    note_str = str(r.get('ghi_chu_kh', ''))
                    if note_str == 'None' or note_str.strip() == '': note_str = "-"
                    cc5.markdown(f"<div class='list-row' style='color:#b45309; font-size:13px;'>{note_str}</div>", unsafe_allow_html=True)
                    
                    if can_edit:
                        with cc6: 
                            if st.button("✏️", key=f"edit_kh_{r['id']}"): 
                                st.session_state.edit_kh_id = r['id']
                                st.rerun()
                        with cc7: 
                            if st.button("❌", key=f"del_kh_{r['id']}"): 
                                cb_xoa_khach(r['id'])
                                st.rerun()
    # ------------------ 3. TÀI XẾ ------------------
    elif tab_sys == "3. Quản Lý Tài Xế":
        with get_connection() as conn: df_nv = pd.read_sql_query("SELECT id, ten_nhan_vien, sdt FROM nhan_vien", conn.connection)
        if st.session_state.edit_tx_id is not None:
            edit_id = st.session_state.edit_tx_id
            tx_info = df_nv[df_nv['id'] == edit_id].iloc[0]
            st.markdown(f"<div class='edit-box'><h4>✏️ ĐANG CHỈNH SỬA: {tx_info['ten_nhan_vien']}</h4></div>", unsafe_allow_html=True)
            with st.form("f_edit_tx_master"):
                c1, c2 = st.columns(2)
                with c1: en = st.text_input("Họ tên lái xe:", value=tx_info['ten_nhan_vien'])
                with c2: ep = st.text_input("SĐT:", value=tx_info['sdt'])
                bc1, bc2 = st.columns([1, 10])
                with bc1:
                    if st.form_submit_button("💾 LƯU", type="primary"):
                        with get_connection() as conn: conn.execute("UPDATE nhan_vien SET ten_nhan_vien=?, sdt=? WHERE id=?", (en.strip(), ep, edit_id)); conn.commit()
                        st.session_state.edit_tx_id = None; st.rerun()
                with bc2:
                    if st.form_submit_button("Hủy bỏ"): st.session_state.edit_tx_id = None; st.rerun()
            st.markdown("---")
        elif can_edit:
            st.markdown("#### ➕ Thêm Tài Xế")
            with st.form("f_v_add"):
                c1, c2 = st.columns(2)
                with c1: nv_n = st.text_input("Họ tên tài xế vận tải mới:")
                with c2: nv_p = st.text_input("Số điện thoại liên hệ:")
                if st.form_submit_button("Lưu Tài Xế"):
                    with get_connection() as conn: conn.execute("INSERT INTO nhan_vien(id, ten_nhan_vien, sdt) VALUES(?,?,?)", (get_next_id('nhan_vien', conn.cursor()), nv_n.strip(), nv_p)); conn.commit()
                    st.success("Ghi nhận xe bãi thành công!"); st.rerun()
            st.markdown("---")
            
        st.markdown("#### 📋 Quản Lý Tài Xế")
        if not df_nv.empty:
            c1, c2, c3 = st.columns([4, 4, 2])
            c1.markdown("<b>Họ Tên Lái Xe</b>", unsafe_allow_html=True); c2.markdown("<b>SĐT</b>", unsafe_allow_html=True)
            if can_edit: c3.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
            for idx, r in df_nv.iterrows():
                with st.container():
                    if can_edit: cc1, cc2, cc3, cc4 = st.columns([4, 4, 1, 1])
                    else: cc1, cc2 = st.columns([4, 4])
                    cc1.markdown(f"<div class='list-row'>{r['ten_nhan_vien']}</div>", unsafe_allow_html=True)
                    cc2.markdown(f"<div class='list-row'>{r['sdt']}</div>", unsafe_allow_html=True)
                    if can_edit:
                        with cc3: 
                            if st.button("✏️", key=f"edit_tx_{r['id']}"): st.session_state.edit_tx_id = r['id']; st.rerun()
                        with cc4: 
                            if st.button("❌", key=f"del_tx_{r['id']}"): cb_xoa_taixe(r['id']); st.rerun()

    # ------------------ 4. GIÁ RIÊNG ------------------
    elif tab_sys == "4. Phân Quyền Giá Riêng":
        with get_connection() as conn:
            df_k = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach FROM khach_hang", conn.connection)
            df_t = pd.read_sql_query("SELECT id, ten_than FROM loai_than", conn.connection)
        t_pr1, t_price2 = st.tabs(["⚙️ Cài Đặt Giá Cơ Chế", "📜 Lịch Sử Đổi Giá"])
        with t_pr1:
            if not df_k.empty and not df_t.empty:
                with st.form("form_set_gr"):
                    k_dict = dict(zip(df_k['id'], df_k['ma_khach_hang'].astype(str) + " - " + df_k['ten_khach'].astype(str)))
                    t_dict = dict(zip(df_t['id'], df_t['ten_than']))
                    id_k = st.selectbox("Chọn Đối Tác:", options=list(k_dict.keys()), format_func=lambda x: k_dict.get(x))
                    id_t = st.selectbox("Chủng Loại Than Áp Dụng:", options=list(t_dict.keys()), format_func=lambda x: t_dict.get(x))
                    with get_connection() as cnn: 
                        old_p_res = cnn.cursor().execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=? AND loai_than_id=?", (to_int(id_k), to_int(id_t))).fetchone()
                    old_p = to_float(old_p_res[0]) if old_p_res else 0.0
                    st.write(f"Đơn giá ưu đãi hiện hành: **{fmt_vn(old_p)} đ/kg**" if old_p > 0 else "Đối tác đang chịu đơn giá niêm yết mặc định.")
                    g_new = st.number_input("Thiết lập giá mới (đ/kg):", value=int(old_p) if old_p > 0 else 2500, step=500, format="%d")
                    if st.form_submit_button("Lưu Cơ Chế"):
                        ts_change = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
                        with get_connection() as conn:
                            cur = conn.cursor()
                            if old_p != g_new: cur.execute("INSERT INTO lich_su_gia (id, khach_hang_id, loai_than_id, gia_cu, gia_moi, ngay_thay_doi) VALUES (?,?,?,?,?,?)", (get_next_id('lich_su_gia', cur), to_int(id_k), to_int(id_t), old_p, g_new, ts_change))
                            cur.execute("INSERT INTO gia_rieng (khach_hang_id, loai_than_id, gia_uu_dai) VALUES (?,?,?) ON CONFLICT (khach_hang_id, loai_than_id) DO UPDATE SET gia_uu_dai = EXCLUDED.gia_uu_dai", (to_int(id_k), to_int(id_t), g_new))
                            conn.commit()
                        st.success("Cố định biểu giá thành công!"); st.rerun()
            with get_connection() as conn: df_pq = pd.read_sql_query('SELECT kh.ten_khach as "Khách Hàng", lt.ten_than as "Loại Than", gr.gia_uu_dai as "Giá Riêng (đ/kg)" FROM gia_rieng gr JOIN khach_hang kh ON gr.khach_hang_id = kh.id JOIN loai_than lt ON gr.loai_than_id = lt.id', conn.connection)
            if not df_pq.empty: st.dataframe(df_pq.style.format({'Giá Riêng (đ/kg)': lambda x: fmt_vn(x)}), hide_index=True, use_container_width=True)

    # ------------------ 5. CẤU HÌNH IN BILL & ZALO BOT ------------------
    elif tab_sys == "5. Hệ Thống (In Bill & Zalo Bot)":
        with get_connection() as conn: config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn.connection).iloc[0]
        t_in, t_zl = st.tabs(["🖨️ Thông tin Doanh Nghiệp (In Bill)", "🤖 Cấu hình Zalo Bot Cảnh Báo"])
        with t_in:
            with st.form("form_print_setting"):
                ten_ch = st.text_input("Tên Doanh Nghiệp / Bãi Than:", value=config['ten_cua_hang'])
                sdt_ch = st.text_input("Số tổng đài bãi xe:", value=config['so_dien_thoai'])
                stk_ch = st.text_area("Số Tài Khoản Nhận Tiền:", value=config['thong_tin_ngan_hang'])
                if st.form_submit_button("Cập Nhật Thông Tin", type="primary"):
                    with get_connection() as conn: conn.execute("UPDATE cau_hinh_in SET ten_cua_hang=?, so_dien_thoai=?, thong_tin_ngan_hang=? WHERE id=1", (ten_ch, sdt_ch, stk_ch)); conn.commit()
                    st.success("Đã đồng bộ thông tin in ấn!"); st.rerun()
        with t_zl:
            st.info("Zalo OA Bot sẽ tự động gửi tin nhắn đến điện thoại của bạn mỗi khi Trạm phát lệnh xuất xe hoặc Lái xe thu tiền về báo Cáo.")
            with st.form("form_zalo"):
                zalo_active = st.checkbox("Bật tính năng Zalo Bot tự động Nổ tin nhắn", value=True if to_int(config.get('zalo_active', 0))==1 else False)
                zalo_token = st.text_input("Mã API Access Token (Zalo OA):", value=config.get('zalo_token', ''), type="password", help="Đăng nhập Zalo for Developer để lấy Access Token của OA doanh nghiệp bạn.")
                zalo_id = st.text_input("ID Người nhận (Zalo User ID):", value=config.get('zalo_id', ''), help="User ID của tài khoản Zalo Giám đốc để nhận tin nổ tự động.")
                if st.form_submit_button("Lưu cấu hình API Zalo", type="primary"):
                    is_act = 1 if zalo_active else 0
                    with get_connection() as conn: conn.execute("UPDATE cau_hinh_in SET zalo_token=?, zalo_id=?, zalo_active=? WHERE id=1", (zalo_token, zalo_id, is_act)); conn.commit()
                    st.success("Cấu hình Zalo đã được thiết lập thành công!"); st.rerun()

    # ------------------ 6. QUẢN LÝ TÀI KHOẢN (ADMIN) ------------------
    elif tab_sys == "6. Quản Lý Tài Khoản":
        if not is_admin: st.error("🔒 Chỉ quản trị viên (Admin) mới có quyền truy cập khu vực này.")
        else:
            with get_connection() as conn: df_users = pd.read_sql_query("SELECT id, username, role, status FROM users WHERE username != 'admin'", conn.connection)
            t_u1, t_u2 = st.tabs(["🟡 Phê Duyệt Mới", "🟢 Cấp Quyền & Xóa"])
            with t_u1:
                if not df_users.empty and 'Chờ duyệt' in df_users['status'].values:
                    for idx, r in df_users[df_users['status'] == 'Chờ duyệt'].iterrows():
                        with st.form(f"f_approve_{r['id']}"):
                            st.write(f"Đăng ký: **{r['username']}**")
                            role_assign = st.selectbox("Gắn chức vụ:", options=["ketoan", "laixe", "manager"])
                            c1, c2 = st.columns(2)
                            with c1: 
                                if st.form_submit_button("✅ Cấp quyền"):
                                    with get_connection() as c: c.execute("UPDATE users SET status='Đã duyệt', role=? WHERE id=?", (role_assign, r['id'])); c.commit()
                                    st.rerun()
                            with c2:
                                if st.form_submit_button("❌ Xóa"): cb_xoa_user(r['id']); st.rerun()
            with t_u2:
                if not df_users.empty:
                    for idx, r in df_users[df_users['status'] == 'Đã duyệt'].iterrows():
                        with st.expander(f"👤 {r['username']} (Quyền: {r['role'].upper()})"):
                            with st.form(f"f_edit_u_{r['id']}"):
                                new_role = st.selectbox("Đổi chức vụ:", options=["ketoan", "laixe", "manager"], index=["ketoan", "laixe", "manager"].index(r['role']))
                                c1, c2 = st.columns(2)
                                with c1:
                                    if st.form_submit_button("💾 Lưu Quyền Mới"):
                                        with get_connection() as c: c.execute("UPDATE users SET role=? WHERE id=?", (new_role, r['id'])); c.commit()
                                        st.rerun()
                                with c2:
                                    if st.form_submit_button("🗑️ Xóa vĩnh viễn"): cb_xoa_user(r['id']); st.rerun()

   # ------------------ 7. SYSTEM LOG ------------------
    elif tab_sys == "7. System Log":
        st.markdown("### 🛠️ NHẬT KÝ HỆ THỐNG")
        if is_manager: st.warning("⚠️ Bạn là Quản lý, bạn chỉ có quyền xem nhật ký hệ thống.")
        elif is_admin:
            st.markdown("<div class='danger-zone'><h4>⚠️ KHU VỰC KHẨN CẤP QUẢN TRỊ VIÊN</h4></div><br>", unsafe_allow_html=True)
            col_log1, col_log2 = st.columns([1, 1])
            with col_log1:
                if st.button("🗑️ Xóa bảng Log màn hình", use_container_width=True): st.session_state.sys_log = []; st.rerun()
                # NÚT ĐỒNG BỘ ĐÃ ĐƯỢC CHUYỂN VÀO ĐÂY (CHỈ ADMIN THẤY)
                if st.button("🔄 Ép Đồng Bộ Google Sheets", use_container_width=True, help="Kéo lại toàn bộ dữ liệu từ Google Sheets"):
                    init_local_db(force_pull=True)
                    write_log("ĐỒNG BỘ", "SUCCESS", "Đã ép tải dữ liệu từ Google Sheets.")
                    st.success("✅ Đã kéo dữ liệu mới nhất từ Google Sheets thành công!")
            with col_log2:
                pass_confirm = st.text_input("🔑 XÁC MINH CHÌA KHÓA ADMIN:", type="password")
                if st.button("🚨 KÍCH HOẠT FACTORY RESET", type="primary", use_container_width=True):
                    if hash_password(pass_confirm) == hash_password(st.secrets["admin_pass"]):
                        st.session_state.sys_log = []
                        write_log("FACTORY RESET", "SUCCESS", "Đã xóa Nhật ký (Dữ liệu gốc được bảo vệ an toàn).")
                        st.success("💥 Đã làm mới nhật ký thành công! Dữ liệu gốc vẫn còn nguyên.")
                    else: st.error("❌ Mật khẩu sai!")
        
        log_content = "\n".join(st.session_state.sys_log) if st.session_state.sys_log else "Hệ thống đang hoạt động an toàn."
        st.markdown(f"<div class='log-box'>{log_content.replace(chr(10), '<br>')}</div>", unsafe_allow_html=True)
