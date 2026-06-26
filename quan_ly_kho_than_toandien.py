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
from contextlib import contextmanager
import threading
from streamlit_option_menu import option_menu
import streamlit.components.v1 as components

# ==========================================
# CẤU HÌNH TỌA ĐỘ ĐỊA LÝ BẢN ĐỒ MIỀN BẮC
# ==========================================
MAP_COORDS = {
    "Hà Nội": {"lat": 21.0285, "lon": 105.8542},
    "Thái Nguyên": {"lat": 21.5942, "lon": 105.8482},
    "Bắc Ninh": {"lat": 21.5928, "lon": 106.0598},
    "Bắc Giang": {"lat": 21.2731, "lon": 106.1946},
    "Hưng Yên": {"lat": 20.8532, "lon": 106.0583},
    "Hải Dương": {"lat": 20.9370, "lon": 106.3146},
    "Hải Phòng": {"lat": 20.8449, "lon": 106.6881},
    "Quảng Ninh": {"lat": 20.8561, "lon": 107.1361},
    "Khác": {"lat": 21.0, "lon": 105.8}
}

# ==========================================
# CÁC HÀM XỬ LÝ ĐỊNH DẠNG & BẢO MẬT
# ==========================================
def to_int(val):
    try: return int(float(val))
    except: return 0

def to_float(val):
    try: return float(val) if pd.notna(val) and str(val).strip() != "" else 0.0
    except: return 0.0

def fmt_vn(val):
    """Hàm định dạng số kiểu Việt Nam dùng dấu chấm (Ví dụ: 15000 -> 15.000)"""
    try: return f"{int(float(val)):,}".replace(",", ".")
    except: return "0"

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# --- HỆ THỐNG PHÁT HIỆN & GHI LOG HỆ THỐNG ---
if 'sys_log' not in st.session_state: st.session_state.sys_log = []

def write_log(action, status, detail=""):
    time_str = datetime.now(timezone.utc).strftime('%H:%M:%S')
    icon = "✅" if status == "SUCCESS" else "❌"
    log_msg = f"[{time_str}] {icon} {action} | {detail}"
    st.session_state.sys_log.insert(0, log_msg)
    if len(st.session_state.sys_log) > 50: st.session_state.sys_log.pop()

# ==========================================
# CÁC CALLBACKS XỬ LÝ DỮ LIỆU BẰNG ROWID ẨN
# ==========================================
def cb_xoa_than(db_rowid):
    with get_connection() as c: c.execute("DELETE FROM loai_than WHERE rowid=?", (db_rowid,)); c.commit()
    write_log("Xóa loại than", "SUCCESS", f"RowID: {db_rowid}")

def cb_xoa_khach(db_rowid):
    with get_connection() as c: c.execute("DELETE FROM khach_hang WHERE rowid=?", (db_rowid,)); c.commit()
    write_log("Xóa khách hàng", "SUCCESS", f"RowID: {db_rowid}")

def cb_xoa_taixe(db_rowid):
    with get_connection() as c: c.execute("DELETE FROM nhan_vien WHERE rowid=?", (db_rowid,)); c.commit()
    write_log("Xóa tài xế", "SUCCESS", f"RowID: {db_rowid}")

def cb_xoa_user(db_rowid):
    with get_connection() as c: c.execute("DELETE FROM users WHERE rowid=?", (db_rowid,)); c.commit()
    write_log("Xóa tài khoản", "SUCCESS", f"Đã xóa vĩnh viễn user có RowID: {db_rowid}")

def cb_huy_don(db_rowid):
    try:
        with get_connection() as c:
            res = c.execute("SELECT id FROM don_hang WHERE rowid=?", (db_rowid,)).fetchone()
            if res:
                don_id = to_int(res[0])
                chi_tiet = pd.read_sql_query(f"SELECT loai_than_id, so_luong FROM chi_tiet_don_hang WHERE don_hang_id={don_id}", c.connection)
                for _, row in chi_tiet.iterrows():
                    c.execute("UPDATE loai_than SET ton_kho = ton_kho + ? WHERE id = ?", (to_float(row['so_luong']), to_int(row['loai_than_id'])))
                c.execute("DELETE FROM chi_tiet_don_hang WHERE don_hang_id=?", (don_id,))
                c.execute("DELETE FROM lich_su_thanh_toan WHERE don_hang_id=?", (don_id,))
            c.execute("DELETE FROM don_hang WHERE rowid=?", (db_rowid,))
            c.commit()
        write_log("Hủy đơn hàng", "SUCCESS", f"Hủy triệt để đơn mục RowID: {db_rowid}")
    except Exception as e: write_log("Hủy đơn hàng", "ERROR", str(e))

# ==========================================
# 1. THIẾT KẾ ĐỒ HỌA & STYLE DOANH NGHIỆP
# ==========================================
st.set_page_config(page_title="Hệ thống quản lý kho than", page_icon="🪨", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
        html, body, [data-testid="stAppViewContainer"] { background-color: #f8fafc; font-family: "Inter", -apple-system, sans-serif; }
        .kpi-card { background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border-top: 4px solid #3b82f6; margin-bottom: 20px; }
        .kpi-label { font-size: 13px; color: #64748b; font-weight: 600; text-transform: uppercase; margin-bottom: 5px; }
        .kpi-value { font-size: 26px; color: #0f172a; font-weight: 800; }
        .border-green { border-top-color: #10b981; }
        .border-red { border-top-color: #ef4444; }
        .border-purple { border-top-color: #8b5cf6; }
        .text-green { color: #10b981; }
        .text-red { color: #ef4444; }
        .text-purple { color: #8b5cf6; }
        .main-header { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 25px; }
        .list-header { font-weight: bold; color: #475569; padding-bottom: 10px; border-bottom: 2px solid #e2e8f0; margin-bottom: 10px; font-size: 14px;}
        .list-row { padding: 8px 0; border-bottom: 1px solid #f1f5f9; font-size: 14px; align-items: center; display: flex;}
        div[data-testid="stButton"] button { padding: 4px 12px; font-size: 13px; border-radius: 6px; }
        .log-box { background: #1e293b; color: #10b981; padding: 15px; border-radius: 8px; font-family: monospace; font-size: 12px; height: 300px; overflow-y: scroll; }
        .danger-zone { background-color: #fff1f2; border: 1px solid #fecdd3; padding: 20px; border-radius: 8px; border-left: 6px solid #e11d48; margin-top: 15px;}
        .ai-card { background: #f8fafc; padding: 12px; border-radius: 6px; border-left: 4px solid #3b82f6; margin-bottom: 8px; font-size: 14px;}
        .ai-warn { border-left-color: #f59e0b; background: #fffbeb;}
        .ai-danger { border-left-color: #ef4444; background: #fef2f2;}
        .panel-summary { background: #ffffff; padding: 24px; border-radius: 12px; border: 1px solid #e2e8f0; box-shadow: 0 4px 12px rgba(0,0,0,0.02); }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. ĐỒNG BỘ ĐỘC LẬP GOOGLE SHEETS
# ==========================================
try: SHEET_URL = st.secrets["sheet_url"]
except KeyError: st.error("Chưa cấu hình Két sắt bảo mật."); st.stop()

@st.cache_resource
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        creds_dict = json.loads(st.secrets["google_key"])
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        return gspread.authorize(ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope))
    except Exception as e: st.error(f"Lỗi đọc chìa khóa bảo mật: {e}"); st.stop()

@st.cache_resource
def init_local_db():
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    client = get_gspread_client()
    sheet = client.open_by_url(SHEET_URL)
    for ws in sheet.worksheets():
        data = ws.get_all_records()
        if data:
            df = pd.DataFrame(data)
            if 'id' in df.columns: df['id'] = pd.to_numeric(df['id'], errors='coerce')
            df.to_sql(ws.title, conn, if_exists='replace', index=False)
    
    # Auto-Clean dữ liệu rác
    conn.execute("DELETE FROM loai_than WHERE id IS NULL OR id = '' OR ten_than IS NULL OR TRIM(ten_than) = ''")
    conn.execute("DELETE FROM khach_hang WHERE id IS NULL OR id = '' OR ten_khach IS NULL OR TRIM(ten_khach) = ''")
    conn.execute("DELETE FROM nhan_vien WHERE id IS NULL OR id = '' OR ten_nhan_vien IS NULL OR TRIM(ten_nhan_vien) = ''")
    conn.commit()
    return conn

init_local_db()

def background_sync_task():
    try:
        bg_conn = sqlite3.connect("kho_than.db", check_same_thread=False)
        client = get_gspread_client()
        sheet = client.open_by_url(SHEET_URL)
        tables = pd.read_sql_query("SELECT name FROM sqlite_master WHERE type='table'", bg_conn)
        for table_name in tables['name']:
            if table_name == "sqlite_sequence": continue
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", bg_conn)
            for col in df.select_dtypes(include=['datetime64', 'datetimetz']).columns: df[col] = df[col].astype(str)
            try: ws = sheet.worksheet(table_name)
            except gspread.WorksheetNotFound: ws = sheet.add_worksheet(title=table_name, rows=100, cols=20)
            ws.clear()
            if not df.empty: ws.update(values=[df.columns.values.tolist()] + df.fillna("").astype(str).values.tolist(), range_name="A1")
    except: pass 
    finally: bg_conn.close()

@contextmanager
def get_connection():
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    class ConnectionWrapper:
        def __init__(self, c): self.c = c
        def commit(self):
            self.c.commit()
            threading.Thread(target=background_sync_task, daemon=True).start()
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

def init_database():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username VARCHAR(255) UNIQUE, password VARCHAR(255), role VARCHAR(50), status VARCHAR(50))''')
        cursor.execute("SELECT * FROM users WHERE username='admin'")
        if not cursor.fetchone(): 
            uid = get_next_id('users', cursor)
            cursor.execute("INSERT INTO users (id, username, password, role, status) VALUES (?, ?, ?, 'admin', 'Đã duyệt')", (uid, 'admin', hash_password(st.secrets["admin_pass"])))
        cursor.execute('''CREATE TABLE IF NOT EXISTS loai_than (id INTEGER PRIMARY KEY, ten_than VARCHAR(255) UNIQUE, gia_nhap_mac_dinh DOUBLE, gia_mac_dinh DOUBLE, ton_kho DOUBLE, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS khach_hang (id INTEGER PRIMARY KEY, ma_khach_hang VARCHAR(50) UNIQUE, ten_khach VARCHAR(255) UNIQUE, sdt VARCHAR(50), dia_chi TEXT, khu_vuc VARCHAR(255), link_google_maps TEXT, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS nhan_vien (id INTEGER PRIMARY KEY, ten_nhan_vien VARCHAR(255) UNIQUE, sdt VARCHAR(50), chuc_vu VARCHAR(100))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS gia_rieng (khach_hang_id INTEGER, loai_than_id INTEGER, gia_uu_dai DOUBLE, PRIMARY KEY (khach_hang_id, loai_than_id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_gia (id INTEGER PRIMARY KEY, khach_hang_id INTEGER, loai_than_id INTEGER, gia_cu DOUBLE, gia_moi DOUBLE, ngay_thay_doi TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS don_hang (id INTEGER PRIMARY KEY, ma_don_hien_thi VARCHAR(50) UNIQUE, khach_hang_id INTEGER, nhan_vien_id INTEGER, ngay_ban DATE, thoi_gian_tao TIMESTAMP, da_thanh_toan INTEGER, trang_thai_giao VARCHAR(100), hinh_thuc_thanh_toan VARCHAR(100), ghi_chu TEXT, giao_gap INTEGER, tong_tien DOUBLE, tien_da_tra DOUBLE, tien_con_no DOUBLE, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chi_tiet_don_hang (id INTEGER PRIMARY KEY, don_hang_id INTEGER, loai_than_id INTEGER, so_luong DOUBLE, don_gia DOUBLE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS nhap_hang (id INTEGER PRIMARY KEY, loai_than_id INTEGER, ngay_nhap DATE, so_luong DOUBLE, don_gia_nhap DOUBLE, nguoi_tao VARCHAR(255), xuong_nhap VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_thanh_toan (id INTEGER PRIMARY KEY, don_hang_id INTEGER, so_tien_tra DOUBLE, hinh_thuc VARCHAR(100), ngay_tra TIMESTAMP, ghi_chu TEXT, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS cau_hinh_in (id INTEGER PRIMARY KEY, ten_cua_hang VARCHAR(255), so_dien_thoai VARCHAR(50), thong_tin_ngan_hang TEXT, kho_giay_mac_dinh VARCHAR(100))''')
        
        cursor.execute("PRAGMA table_info(nhap_hang)")
        if 'xuong_nhap' not in [col[1] for col in cursor.fetchall()]: cursor.execute("ALTER TABLE nhap_hang ADD COLUMN xuong_nhap VARCHAR(255)")
        cursor.execute("INSERT OR IGNORE INTO cau_hinh_in (id, thong_tin_ngan_hang) VALUES (1, 'Chưa cài đặt')")
        conn.commit()

init_database()

# ==========================================
# 3. KIỂM TRÁT CƠ CHẾ ĐĂNG NHẬP
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
                                uid = get_next_id('users', conn.cursor())
                                # Mặc định đăng ký mới là quyền lái xe chờ duyệt
                                conn.cursor().execute("INSERT INTO users (id, username, password, role, status) VALUES (?, ?, ?, 'laixe', 'Chờ duyệt')", (uid, n_user, hash_password(n_pwd)))
                                conn.commit()
                            st.success("Đăng ký thành công! Vui lòng báo Admin duyệt."); st.rerun()
                        except: st.error("Tài khoản này đã tồn tại trên hệ thống!")
    st.stop()

# ==========================================
# ĐIỀU HƯỚNG MENU THEO PHÂN QUYỀN (RBAC)
# ==========================================
ROLE_MENUS = {
    "admin": ["Thống Kê (HQ)", "Lập Đơn & In Phiếu", "Giao Hàng & Vận Tải", "Sổ Quản Lý Nợ", "Lịch Sử Đơn Hàng", "Cài Đặt Hệ Thống"],
    "ketoan": ["Thống Kê (HQ)", "Lập Đơn & In Phiếu", "Sổ Quản Lý Nợ", "Lịch Sử Đơn Hàng"],
    "laixe": ["Giao Hàng & Vận Tải", "Lịch Sử Đơn Hàng"]
}
ROLE_ICONS = {
    "admin": ['bar-chart-fill', 'receipt-cutoff', 'truck', 'wallet-fill', 'clock-history', 'gear-fill'],
    "ketoan": ['bar-chart-fill', 'receipt-cutoff', 'wallet-fill', 'clock-history'],
    "laixe": ['truck', 'clock-history']
}

current_role = st.session_state.user_role
if current_role not in ROLE_MENUS: current_role = "laixe" # Fallback an toàn

with st.sidebar:
    st.markdown(f"### 🪨 TRẠM VẬN HÀNH\n• Người dùng: **{st.session_state.current_user}**\n• Quyền hạn: **{current_role.upper()}**")
    if st.button("🚪 Đăng Xuất"): st.session_state.clear(); st.rerun()
    st.markdown("---")
    menu = option_menu("CHỨC NĂNG CHÍNH", ROLE_MENUS[current_role], icons=ROLE_ICONS[current_role], menu_icon="boxes", default_index=0)

# ==========================================
# PHÂN HỆ 1: THỐNG KÊ (HQ DASHBOARD TÍCH HỢP)
# ==========================================
if menu == "Thống Kê (HQ)":
    st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📊 PHÂN HỆ GIÁM SÁT KINH DOANH TỔNG THỂ</h1></div>", unsafe_allow_html=True)
    time_filter = st.radio("⏳ Mốc thời gian:", ["Hôm nay", "Tuần này", "Tháng này", "Tất cả thời gian"], horizontal=True)

    with get_connection() as conn:
        df_flat = pd.read_sql_query('''SELECT dh.id as don_id, dh.thoi_gian_tao, dh.da_thanh_toan, dh.trang_thai_giao, dh.ngay_ban, kh.ten_khach, kh.khu_vuc, lt.ten_than, lt.gia_nhap_mac_dinh, ctdh.so_luong, ctdh.don_gia, (ctdh.so_luong * ctdh.don_gia) as thanh_tien FROM don_hang dh JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id JOIN khach_hang kh ON dh.khach_hang_id = kh.id JOIN loai_than lt ON ctdh.loai_than_id = lt.id''', conn.connection)
        df_group = pd.read_sql_query('''SELECT dh.id as don_id, dh.ma_don_hien_thi, dh.thoi_gian_tao, dh.trang_thai_giao, dh.giao_gap, dh.tong_tien, dh.tien_con_no, dh.nguoi_tao, kh.ma_khach_hang, kh.ten_khach, nv.ten_nhan_vien FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id ORDER BY dh.id DESC''', conn.connection)
        df_kho_status = pd.read_sql_query("SELECT ten_than, ton_kho FROM loai_than", conn.connection)

    if not df_flat.empty:
        df_flat['don_gia'] = pd.to_numeric(df_flat['don_gia'], errors='coerce').fillna(0)
        df_flat['gia_nhap_mac_dinh'] = pd.to_numeric(df_flat['gia_nhap_mac_dinh'], errors='coerce').fillna(0)
        df_flat['so_luong'] = pd.to_numeric(df_flat['so_luong'], errors='coerce').fillna(0)
        df_flat['thanh_tien'] = pd.to_numeric(df_flat['thanh_tien'], errors='coerce').fillna(0)
        df_flat['Date'] = pd.to_datetime(df_flat['thoi_gian_tao'])
        df_flat['loi_nhuan'] = (df_flat['don_gia'] - df_flat['gia_nhap_mac_dinh']) * df_flat['so_luong']
        
        if time_filter == "Hôm nay": df_flat = df_flat[df_flat['Date'].dt.date == now_dt.date()]
        elif time_filter == "Tuần này": df_flat = df_flat[df_flat['Date'].dt.date >= (now_dt - timedelta(days=now_dt.weekday())).date()]
        elif time_filter == "Tháng này": df_flat = df_flat[(df_flat['Date'].dt.month == now_dt.month) & (df_flat['Date'].dt.year == now_dt.year)]
        
    if not df_group.empty:
        df_group['tong_tien'] = pd.to_numeric(df_group['tong_tien'], errors='coerce').fillna(0)
        df_group['tien_con_no'] = pd.to_numeric(df_group['tien_con_no'], errors='coerce').fillna(0)
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
    
    # HIỂN THỊ 4 THÈ KPI CHUẨN ĐỊNH DẠNG DẤU CHẤM VN
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown(f"<div class='kpi-card'><div class='kpi-label'>📦 Tổng Đơn Cần Giao</div><div class='kpi-value'>{total_orders} đơn <span style='font-size:13px;color:#64748b;'>({pending_count} chờ)</span></div></div>", unsafe_allow_html=True)
    with c2: st.markdown(f"<div class='kpi-card border-green'><div class='kpi-label'>💵 Doanh Thu Tạm Tính</div><div class='kpi-value text-green'>{fmt_vn(total_rev)} đ</div></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='kpi-card border-purple'><div class='kpi-label'>📈 Lợi Nhuận Gộp bãi</div><div class='kpi-value text-purple'>{fmt_vn(total_profit)} đ</div></div>", unsafe_allow_html=True)
    with c4: st.markdown(f"<div class='kpi-card border-red'><div class='kpi-label'>🛑 Tổng Công Nợ</div><div class='kpi-value text-red'>{fmt_vn(debt_rev)} đ</div></div>", unsafe_allow_html=True)

    # ------------------ CẢNH BÁO TIẾN ĐỘ GIAO HÀNG CHUẨN 2 GIỜ ------------------
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
                    is_late = wait_minutes > 120 # Ngưỡng bảo vệ 2 giờ
                    
                    color = "#ef4444" if is_late else "#22c55e"
                    icon = "🚨" if is_late else "✅"
                    wait_str = f"{int(wait_minutes // 60)}h {int(wait_minutes % 60)}p" if wait_minutes >= 60 else f"{int(wait_minutes)} phút"
                    status_text = f"TRỄ HẸN QUÁ HẠN (Đã chờ {wait_str})" if is_late else f"TRONG TIẾN ĐỘ AN TOÀN (Đang chờ {wait_str})"
                    tx_name = r['ten_nhan_vien'] if r['ten_nhan_vien'] else "Chưa phân xe tài xế"
                    
                    st.markdown(f"""
                        <div style='border-left: 6px solid {color}; background-color: #ffffff; padding: 14px; border-radius: 8px; margin-bottom: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.05);'>
                            <b style='color: {color}'>{icon} Lệnh {r['ma_don_hien_thi']} - {status_text}</b><br>
                            <small>• Giờ đặt lệnh: <b style='color:#000'>{row_time.strftime('%H:%M %d/%m')}</b> | Giờ kiểm tra thực tế: <b style='color:#000'>{current_time.strftime('%H:%M')}</b></small><br>
                            <small>• Tài xế: {tx_name} | Đối tác: {r['ten_khach']} | Nhân viên trực ca: {r['nguoi_tao']}</small>
                        </div>
                    """, unsafe_allow_html=True)
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

    # ------------------ THỊ TRƯỜNG ĐỊA LÝ VÀ BIỂU ĐỒ ------------------
    st.markdown("---")
    st.markdown("### 🗺️ Bản Đồ Phân Bổ Mở Rộng Thị Trường")
    if not df_flat.empty:
        map_data = df_flat.groupby('khu_vuc')['so_luong'].sum().reset_index()
        map_data['lat'] = map_data['khu_vuc'].apply(lambda x: MAP_COORDS.get(x, MAP_COORDS["Khác"])['lat'])
        map_data['lon'] = map_data['khu_vuc'].apply(lambda x: MAP_COORDS.get(x, MAP_COORDS["Khác"])['lon'])
        fig_map = px.scatter_mapbox(map_data, lat="lat", lon="lon", size="so_luong", color="khu_vuc", zoom=7, height=400)
        fig_map.update_layout(mapbox_style="carto-positron", margin={"r":0,"t":0,"l":0,"b":0})
        st.plotly_chart(fig_map, use_container_width=True)

    st.markdown("### 📊 Chi Tiết Các Mảng Thống Kê Phân Bổ")
    if not df_flat.empty:
        ch1, ch2 = st.columns(2) 
        with ch1: st.plotly_chart(px.pie(df_flat.groupby('ten_than')['so_luong'].sum().reset_index(), values='so_luong', names='ten_than', hole=0.4, title="Tỷ trọng than xuất kho"), use_container_width=True)
        with ch2: st.plotly_chart(px.pie(df_flat.groupby('ten_khach')['loi_nhuan'].sum().reset_index(), values='loi_nhuan', names='ten_khach', hole=0.4, title="Lợi nhuận theo khách hàng"), use_container_width=True)

# ==========================================
# PHÂN HỆ 2: LẬP ĐƠN & IN PHIẾU (CHIA 2 CỘT)
# ==========================================
elif menu == "Lập Đơn & In Phiếu":
    st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📋 HỆ THỐNG LẬP LỆNH XUẤT KHO CHUYÊN NGHIỆP</h1></div>", unsafe_allow_html=True)
    with get_connection() as conn: print_config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn.connection).iloc[0]
        
    if st.session_state.last_order_id:
        with get_connection() as conn:
            df_master = pd.read_sql_query(f"SELECT * FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.id = {to_int(st.session_state.last_order_id)}", conn.connection)
            details = pd.read_sql_query(f"SELECT ctdh.*, lt.ten_than FROM chi_tiet_don_hang ctdh JOIN loai_than lt ON ctdh.loai_than_id = lt.id WHERE ctdh.don_hang_id = {to_int(st.session_state.last_order_id)}", conn.connection)
            
        if df_master.empty:
            st.error("Lỗi đồng bộ mã đơn. Vui lòng lập lại đơn mới.")
            if st.button("Quay lại"): st.session_state.last_order_id = None; st.rerun()
        else:
            master = df_master.iloc[0]
            html_rows = ""; txt_rows = ""; total_val = 0
            for idx, r in enumerate(details.iterrows(), 1):
                _, row = r; thanh_tien = row['so_luong'] * row['don_gia']; total_val += thanh_tien
                html_rows += f"<tr><td style='text-align:center;'>{idx}</td><td>{row['ten_than']}</td><td style='text-align:center;'>{fmt_vn(row['so_luong'])}</td><td style='text-align:right;'>{fmt_vn(row['don_gia'])}</td><td style='text-align:right; font-weight:bold;'>{fmt_vn(thanh_tien)}</td></tr>"
                txt_rows += f"- {row['ten_than']}: {fmt_vn(row['so_luong'])} kg x {fmt_vn(row['don_gia'])} đ = {fmt_vn(thanh_tien)} đ\n"
                
            full_html_print = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><style>body {{ font-family: 'Arial', sans-serif; color: #333; margin: 0; padding: 20px; }} .invoice-container {{ background: #fff; max-width: 800px; margin: 0 auto; padding: 30px; border: 1px solid #cbd5e1; border-radius: 8px; }} .header {{ display: flex; justify-content: space-between; border-bottom: 2px solid #2563eb; padding-bottom: 15px; }} .company-info h2 {{ margin: 0; color: #1e3a8a; }} table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }} th {{ background-color: #2563eb; color: white; padding: 10px; }} td {{ padding: 10px; border-bottom: 1px solid #e2e8f0; }} .total-row td {{ font-weight: bold; font-size: 18px; color: #dc2626; }} .footer {{ display: flex; justify-content: space-between; margin-top: 40px; text-align: center; }} .signature {{ width: 45%; }} .print-btn {{ display: block; width: 100%; padding: 12px; background-color: #10b981; color: white; border: none; border-radius: 5px; font-size: 16px; font-weight: bold; cursor: pointer; margin-bottom:15px;}} @media print {{ .print-btn {{ display: none; }} }}</style></head><body><div class="invoice-container"><button class="print-btn" onclick="window.print()">🖨️ KÍCH HOẠT LỆNH IN FILE HÓA ĐƠN PDF</button><div class="header"><div class="company-info"><h2>{print_config["ten_cua_hang"]}</h2><p>SĐT bãi xe: {print_config["so_dien_thoai"]}</p></div><div style="text-align:right;"><h2>PHIẾU XUẤT</h2><p>Mã: <b>{master["ma_don_hien_thi"]}</b></p><p>Ngày: {master["thoi_gian_tao"]}</p></div></div><div style="margin: 20px 0; background:#f8fafc; padding:12px; border-radius:6px;">Link KH: <b>{master["ten_khach"]}</b><br>Địa chỉ: {master["dia_chi"]}<br>Ghi chú lái xe: {master["ghi_chu"]}</div><table><thead><tr><th>STT</th><th>Mặt Hàng Than</th><th>Khối Lượng (kg)</th><th>Đơn Giá (đ)</th><th>Thành Tiền (đ)</th></tr></thead><tbody>{html_rows}<tr class="total-row"><td colspan="4" style="text-align:right;">TỔNG CỘNG THANH TOÁN:</td><td style="text-align:right;">{fmt_vn(total_val)} đ</td></tr></tbody></table><p style="margin-top:20px; font-size:13px;"><b>Tài khoản thụ hưởng:</b> {print_config["thong_tin_ngan_hang"]}</p><div class="footer"><div class="signature"><p><b>Đại Diện Khách Hàng</b></p><p><i>(Ký tên)</i></p></div><div class="signature"><p><b>Thủ Kho Bảo Vệ</b></p><p><i>(Ký tên)</i></p></div></div></div></body></html>"""
            text_bill = f"HÓA ĐƠN GIAO HÀNG - {print_config['ten_cua_hang']}\nMã: {master['ma_don_hien_thi']} | Ngày: {master['thoi_gian_tao']}\nKhách: {master['ten_khach']}\nĐịa chỉ: {master['dia_chi']}\n-------------------------\n{txt_rows}-------------------------\nTỔNG CỘNG: {fmt_vn(total_val)} VNĐ\nCK/TT: {print_config['thong_tin_ngan_hang']}\nCảm ơn quý khách!"

            st.success("🎉 Ghi nhận đơn hàng thành công trên máy chủ!")
            components.html(full_html_print, height=520, scrolling=True)
            
            c_btn1, c_btn2 = st.columns(2)
            with c_btn1: st.download_button(label="📥 TẢI FILE HÓA ĐƠN ĐIỆN TỬ (.HTML)", data=full_html_print.encode('utf-8'), file_name=f"HoaDon_{master['ma_don_hien_thi']}.html", mime="text/html", use_container_width=True)
            with c_btn2: 
                if st.button("🔄 TIẾP TỤC LẬP PHIẾU MỚI", type="primary", use_container_width=True):
                    st.session_state.last_order_id = None; st.rerun()
            st.markdown("---")
            st.code(text_bill, language="text")
    else:
        with get_connection() as conn:
            df_khach = pd.read_sql_query("SELECT rowid as db_rowid, id, ma_khach_hang, ten_khach FROM khach_hang", conn.connection)
            df_than = pd.read_sql_query("SELECT rowid as db_rowid, id, ten_than, gia_mac_dinh, ton_kho FROM loai_than", conn.connection)

        if df_khach.empty or df_than.empty: st.warning("Vui lòng khởi tạo Danh mục đối tác và Chủng loại than trước.")
        else:
            panel_input, panel_cart = st.columns([2, 3])
            
            with panel_input:
                st.markdown("#### 👤 Lập hóa đơn")
                khach_dict = dict(zip(df_khach['db_rowid'], "[" + df_khach['ma_khach_hang'].astype(str) + "] " + df_khach['ten_khach'].astype(str)))
                khach_db_id = st.selectbox("Chọn Khách Hàng:", options=list(khach_dict.keys()), format_func=lambda x: khach_dict.get(x))
                khach_id = df_khach[df_khach['db_rowid']==khach_db_id]['id'].values[0]
                
                with get_connection() as conn: 
                    df_pb = pd.read_sql_query(f"SELECT loai_than_id FROM gia_rieng WHERE khach_hang_id = {to_int(khach_id)}", conn.connection)
                
                than_options = df_than[df_than['id'].isin(df_pb['loai_than_id'].tolist())] if not df_pb.empty else df_than
                if than_options.empty: than_options = df_than
                
                than_dict = dict(zip(than_options['db_rowid'], than_options['ten_than'].astype(str)))
                t_db_id = st.selectbox("Chọn Loại Than Xuất Bãi:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x))
                t_id = than_options[than_options['db_rowid']==t_db_id]['id'].values[0]
                
                with get_connection() as conn: 
                    gr_res = conn.cursor().execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=? AND loai_than_id=?", (to_int(khach_id), to_int(t_id))).fetchone()
                
                df_tk_filter = df_than[df_than['db_rowid']==t_db_id]
                gia_goi_y = gr_res[0] if gr_res else (df_tk_filter['gia_mac_dinh'].values[0] if not df_tk_filter.empty else 0)
                ton_kho_hien_tai = to_float(df_tk_filter['ton_kho'].values[0]) if not df_tk_filter.empty else 0.0
                
                st.markdown(f"Trữ lượng bãi thực tế: <b style='color:#2563eb;'>{fmt_vn(ton_kho_hien_tai)} kg</b>", unsafe_allow_html=True)
                st.markdown("---")
                st.markdown("#### ⚙️ Khối Lượng & Định Giá")
                
                col_sl, col_dg = st.columns(2)
                with col_sl: sl = st.number_input("Khối lượng xuất (kg):", min_value=1, value=1000, step=100, format="%d")
                with col_dg: dg = st.number_input("Đơn giá chốt bán (đ/kg):", min_value=1, value=int(float(gia_goi_y)), step=500, format="%d")
                
                if st.button("➕ NẠP VÀO PHIẾU XUẤT", use_container_width=True):
                    if any(i['loai_than_id'] == to_int(t_id) for i in st.session_state.cart): st.error("Mặt hàng này đã nằm trong danh sách tạm tính!")
                    else:
                        st.session_state.cart.append({'loai_than_id': to_int(t_id), 'ten_than': than_dict.get(t_db_id), 'so_luong': sl, 'don_gia': dg, 'thanh_tien': sl * dg})
                        st.rerun()
            
            with panel_cart:
                st.markdown("<div class='panel-summary'>", unsafe_allow_html=True)
                st.markdown("### 📊 BẢNG TỔNG HỢP TẠM TÍNH PHIẾU XUẤT")
                if st.session_state.cart:
                    df_c = pd.DataFrame(st.session_state.cart)
                    st.dataframe(df_c[['ten_than', 'so_luong', 'don_gia', 'thanh_tien']].rename(columns={
                        'ten_than':'Chủng Loại Than', 'so_luong':'Khối Lượng (kg)', 'don_gia':'Đơn Giá (đ)', 'thanh_tien':'Thành Tiền (đ)'
                    }).style.format({
                        'Khối Lượng (kg)': lambda x: fmt_vn(x), 'Đơn Giá (đ)': lambda x: fmt_vn(x), 'Thành Tiền (đ)': lambda x: fmt_vn(x)
                    }), hide_index=True, use_container_width=True)
                    
                    total_val = df_c['thanh_tien'].sum()
                    st.markdown(f"""
                        <div style='background: #fef2f2; padding: 15px; border-radius: 8px; text-align: center; margin: 15px 0; border: 1px solid #fca5a5;'>
                            <span style='color:#991b1b; font-weight:600; font-size:14px; text-transform:uppercase;'>Tổng Tiền Phiếu Dự Kiến</span><br>
                            <span style='color:#dc2626; font-size:28px; font-weight:800;'>{fmt_vn(total_val)} đ</span>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    giao_gap = st.checkbox("🔥 PHÁT LỆNH GIAO GẤP KHẨN CẤP")
                    g_chu = st.text_input("Biển số xe điều vận / Tên lái xe:")
                    
                    bx1, bx2 = st.columns(2)
                    with bx1:
                        if st.button("🗑️ HỦY PHIẾU TẠM", type="secondary", use_container_width=True):
                            st.session_state.cart = []; st.rerun()
                    with bx2:
                        if st.button("🚀 XUẤT PHIẾU VÀ ĐẨY LỆNH", type="primary", use_container_width=True):
                            stock_ok = True
                            for i in st.session_state.cart:
                                ton_check = df_than[df_than['id'] == to_int(i['loai_than_id'])]
                                ton_val = to_float(ton_check['ton_kho'].values[0]) if not ton_check.empty else 0.0
                                if to_float(i['so_luong']) > ton_val: stock_ok = False; st.error(f"Cảnh báo: Mã {i['ten_than']} vượt trữ lượng bãi xe!")
                            if stock_ok:
                                try:
                                    ts = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'); ma_don_final = sinh_ma_don_hang_theo_ngay(today_str); is_gap = 1 if giao_gap else 0
                                    with get_connection() as conn:
                                        cur = conn.cursor()
                                        new_id = get_next_id('don_hang', cur)
                                        cur.execute('INSERT INTO don_hang (id, ma_don_hien_thi, khach_hang_id, ngay_ban, thoi_gian_tao, trang_thai_giao, ghi_chu, giao_gap, tong_tien, tien_con_no, nguoi_tao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (new_id, ma_don_final, to_int(khach_id), today_str, ts, 'Chờ giao hàng', g_chu, is_gap, total_val, total_val, st.session_state.current_user))
                                        for i in st.session_state.cart:
                                            ct_id = get_next_id('chi_tiet_don_hang', cur)
                                            cur.execute('INSERT INTO chi_tiet_don_hang (id, don_hang_id, loai_than_id, so_luong, don_gia) VALUES (?, ?, ?, ?, ?)', (ct_id, new_id, to_int(i['loai_than_id']), i['so_luong'], i['don_gia']))
                                            cur.execute("UPDATE loai_than SET ton_kho = ton_kho - ? WHERE id = ?", (i['so_luong'], to_int(i['loai_than_id'])))
                                        conn.commit()
                                    write_log("Lập đơn hàng", "SUCCESS", f"Mã phiếu xuất: {ma_don_final}")
                                    st.session_state.cart = []; st.session_state.last_order_id = new_id; st.rerun()
                                except Exception as e: write_log("Lập đơn hàng", "ERROR", str(e))
                else: st.info("Giỏ hàng rỗng. Hãy chọn hàng hóa bên cột trái để tạo bảng hợp lệ.")
                st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# PHÂN HỆ 3: GIAO HÀNG & ĐIỀU VẬN TÀI XẾ
# ==========================================
elif menu == "Giao Hàng & Vận Tải":
    st.markdown("### 🚚 Bàn Giao Lộ Trình & Nghiệm Thu Trạm Xe")
    with get_connection() as conn: df_staff = pd.read_sql_query("SELECT rowid as db_rowid, id, ten_nhan_vien FROM nhan_vien", conn.connection)
    
    tab1, tab2 = st.tabs(["📦 Xe Chờ Đi Giao", "🏁 Nghiệm Thu Giao Xong"])
    with tab1:
        with get_connection() as conn: df_cho = pd.read_sql_query("SELECT rowid as db_rowid, id, ma_don_hien_thi, khach_hang_id, trang_thai_giao FROM don_hang WHERE trang_thai_giao = 'Chờ giao hàng'", conn.connection)
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
                        with st.form(key=f"giao_xe_{idx}_{r['db_rowid']}"):
                            if link_map: st.markdown(f"[📍 Chỉ đường Google Maps bãi giao]({link_map})")
                            if df_staff.empty: st.warning("Chưa cấu hình hồ sơ tài xế.")
                            else:
                                tx_dict = dict(zip(df_staff['id'], df_staff['ten_nhan_vien'].astype(str)))
                                tx_id = st.selectbox("Giao xe cho tài xế:", options=list(tx_dict.keys()), format_func=lambda x: tx_dict.get(x))
                                if st.form_submit_button("Lệnh Xuất Phát", type="primary"):
                                    with get_connection() as c_update: 
                                        c_update.execute("UPDATE don_hang SET trang_thai_giao='Đang giao', nhan_vien_id=? WHERE rowid=?", (to_int(tx_id), to_int(r['db_rowid'])))
                                        c_update.commit()
                                    st.success("Xe đã lăn bánh!"); st.rerun()
                    with c2: st.button("🗑️ Hủy Đơn", key=f"huy_don_cho_{idx}_{r['db_rowid']}", on_click=cb_huy_don, args=(to_int(r['db_rowid']),))

    with tab2:
        with get_connection() as conn: df_dang = pd.read_sql_query("SELECT rowid as db_rowid, id, ma_don_hien_thi, khach_hang_id, tong_tien FROM don_hang WHERE trang_thai_giao = 'Đang giao'", conn.connection)
        if df_dang.empty: st.info("Không có xe nào đang di chuyển ngoài đường.")
        else:
            for idx, r in df_dang.iterrows():
                with get_connection() as conn:
                    khach_info = pd.read_sql_query(f"SELECT ten_khach FROM khach_hang WHERE id={to_int(r['khach_hang_id'])}", conn.connection)
                    ten_kh = khach_info.iloc[0]['ten_khach'] if not khach_info.empty else "Ẩn danh"
                    
                with st.expander(f"🚚 Xe Đang Chạy {r['ma_don_hien_thi']} - Khách: {ten_kh}", expanded=True):
                    c1, c2 = st.columns([4, 1])
                    with c1:
                        with st.form(key=f"form_done_gh_{idx}_{r['db_rowid']}"):
                            # 1. Ép kiểu an toàn (Bỏ qua lỗi rỗng/chữ)
                            tong_tien_an_toan = int(to_float(r['tong_tien']))
                            
                            st.write(f"Giá trị đơn hàng: <b>{fmt_vn(tong_tien_an_toan)} đ</b>", unsafe_allow_html=True)
                            
                            # 2. Đưa biến an toàn vào number_input
                            tien_tra_ngay = st.number_input(
                                "Cầm tiền mặt / CK thu ngay (đ):", 
                                min_value=0, 
                                max_value=tong_tien_an_toan if tong_tien_an_toan > 0 else 1000000000, 
                                value=tong_tien_an_toan if tong_tien_an_toan > 0 else 0, 
                                step=10000, 
                                format="%d"
                            )
                            
                            pt_tt = st.selectbox("Cơ chế nhận tiền:", ["Chuyển khoản", "Tiền mặt"])
                            if st.form_submit_button("Xác Nhận Nghiệm Thu Hạ Hàng", type="primary"):
# ==========================================
# PHÂN HỆ 4: SỔ QUẢN LÝ NỢ
# ==========================================
elif menu == "Sổ Quản Lý Nợ":
    st.markdown("### 💰 Quản Lý Dòng Tiền & Kế Toán Công Nợ")
    with get_connection() as conn: df_no = pd.read_sql_query('''SELECT dh.id, dh.ma_don_hien_thi as "Mã Đơn", dh.ngay_ban as "Ngày", kh.ten_khach as "Khách Hàng", dh.tong_tien as "Tổng Tiền", dh.tien_da_tra as "Đã Trả", dh.tien_con_no as "CÒN NỢ" FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.tien_con_no > 0 AND dh.trang_thai_giao = 'Đã hoàn thành' ''', conn.connection)
    if df_no.empty: st.success("Hệ thống sạch bóng nợ xấu. Không có dư nợ tồn đọng.")
    else:
        for col in ['Tổng Tiền', 'Đã Trả', 'CÒN NỢ']:
            df_no[col] = pd.to_numeric(df_no[col], errors='coerce').fillna(0)
            
        st.dataframe(df_no.drop(columns=['id']).style.format({
            'Tổng Tiền': lambda x: fmt_vn(x), 'Đã Trả': lambda x: fmt_vn(x), 'CÒN NỢ': lambda x: fmt_vn(x)
        }), hide_index=True, use_container_width=True)
        
        st.markdown(f"<h4 style='color:#b91c1c;'>TỔNG DƯ NỢ HIỆN TẠI: {fmt_vn(df_no['CÒN NỢ'].sum())} VNĐ</h4>", unsafe_allow_html=True)
        with st.form("f_thu_no"):
            no_dict = dict(zip(df_no['id'], df_no['Mã Đơn'].astype(str) + " - " + df_no['Khách Hàng'].astype(str)))
            id_don_no = st.selectbox("Gạch nợ đơn:", options=list(no_dict.keys()), format_func=lambda x: no_dict.get(x))
            info_no = df_no[df_no['id'] == id_don_no].iloc[0] if not df_no[df_no['id'] == id_don_no].empty else None
            if info_no is not None:
                max_no = int(float(info_no['CÒN NỢ']))
                tien_thu = st.number_input("Số tiền thu gạch nợ (đ):", min_value=1, max_value=max_no, value=max_no, step=10000, format="%d")
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
# PHÂN HỆ 5: LỊCH SỬ ĐƠN HÀNG
# ==========================================
elif menu == "Lịch Sử Đơn Hàng":
    st.markdown("### 🗂️ Tra Cứu Lịch Sử Giao Hàng")
    with get_connection() as conn: df_his = pd.read_sql_query('''SELECT dh.ma_don_hien_thi as "Mã Đơn", dh.thoi_gian_tao as "Ngày Giờ", kh.ten_khach as "Khách Hàng", nv.ten_nhan_vien as "Tài Xế", dh.tong_tien as "Tổng Tiền", dh.tien_con_no as "Nợ Lại", dh.nguoi_tao as "Người Lập" FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id WHERE dh.trang_thai_giao = 'Đã hoàn thành' ORDER BY dh.id DESC''', conn.connection)
    if not df_his.empty:
        for col in ['Tổng Tiền', 'Nợ Lại']: df_his[col] = pd.to_numeric(df_his[col], errors='coerce').fillna(0)
        st.dataframe(df_his.style.format({'Tổng Tiền': lambda x: fmt_vn(x), 'Nợ Lại': lambda x: fmt_vn(x)}), hide_index=True, use_container_width=True)
        st.download_button("📥 XUẤT BÁO CÁO EXCEL TRẠM", data=df_his.to_csv(index=False, encoding='utf-8-sig'), file_name=f"BaoCao_BaiThan_{today_str}.csv", mime="text/csv")

# ==========================================
# PHÂN HỆ 6: CÀI ĐẶT HỆ THỐNG (KHÔI PHỤC NÚT XÓA SỬA INLINE & QUYỀN NHÂN SỰ)
# ==========================================
elif menu == "Cài Đặt Hệ Thống":
    st.markdown("### ⚙️ Cấu Hình Danh Mục Cơ Sở Dữ Liệu")
    tabs_list = ["1. Danh Mục Loại Than", "2. Quản Lý Khách Hàng", "3. Quản Lý Tài Xế", "4. Phân Quyền Giá Riêng", "5. Cấu Hình In Bill"]
    if st.session_state.user_role == 'admin': tabs_list.extend(["6. Quản Lý Tài Khoản (Admin)", "7. System Log (Theo dõi lỗi)"])
    tab_sys = st.selectbox("Chọn hạng mục:", tabs_list)
    
    # ------------------ 1. LOẠI THAN ------------------
    if tab_sys == "1. Danh Mục Loại Than":
        with get_connection() as conn: 
            df_t_show = pd.read_sql_query("SELECT rowid as db_rowid, id, ten_than as 'Tên Loại Than', gia_nhap_mac_dinh as 'Giá Nhập (đ)', gia_mac_dinh as 'Giá Bán (đ)', ton_kho as 'Tồn Kho (kg)' FROM loai_than", conn.connection)
            df_nhap = pd.read_sql_query('''SELECT nh.ngay_nhap as "Ngày", lt.ten_than as "Loại Than", nh.xuong_nhap as "Xưởng", nh.so_luong as "SL (kg)", nh.don_gia_nhap as "Giá Nhập" FROM nhap_hang nh JOIN loai_than lt ON nh.loai_than_id = lt.id ORDER BY nh.id DESC''', conn.connection)
            
        t_sub1, t_sub2 = st.tabs(["📋 Danh Mục Hàng Hóa", "🚢 Nhập Hàng & Lịch Sử"])
        with t_sub1:
            st.markdown("#### Thêm Chủng Loại Mới")
            with st.form("f_c_add"):
                n = st.text_input("Tên chủng loại than mới:"); pn = st.number_input("Giá mua gốc nhập kho (đ):", value=1500, step=500, format="%d"); p = st.number_input("Giá bán niêm yết (đ):", value=3000, step=500, format="%d")
                if st.form_submit_button("Lưu Thêm"):
                    with get_connection() as conn: conn.execute("INSERT INTO loai_than(id, ten_than, gia_nhap_mac_dinh, gia_mac_dinh, ton_kho, nguoi_tao) VALUES(?,?,?,?,?,?)", (get_next_id('loai_than', conn.cursor()), n.strip(), pn, p, 0, st.session_state.current_user)); conn.commit()
                    st.success("Đã thêm chủng loại!"); st.rerun()
            
            st.markdown("---")
            st.markdown("#### 📋 Bảng Quản Lý Chủng Loại Hàng Hóa (Xóa / Sửa)")
            if not df_t_show.empty:
                c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
                c1.markdown("<b>Tên Loại Than</b>", unsafe_allow_html=True)
                c2.markdown("<b>Giá Nhập</b>", unsafe_allow_html=True)
                c3.markdown("<b>Giá Bán</b>", unsafe_allow_html=True)
                c4.markdown("<b>Tồn Kho</b>", unsafe_allow_html=True)
                c5.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
                
                for idx, r in df_t_show.iterrows():
                    with st.container():
                        cc1, cc2, cc3, cc4, cc5, cc6 = st.columns([3, 2, 2, 2, 1, 1])
                        cc1.markdown(f"<div class='list-row'>{r['Tên Loại Than']}</div>", unsafe_allow_html=True)
                        cc2.markdown(f"<div class='list-row'>{fmt_vn(r['Giá Nhập (đ)'])} đ</div>", unsafe_allow_html=True)
                        cc3.markdown(f"<div class='list-row'>{fmt_vn(r['Giá Bán (đ)'])} đ</div>", unsafe_allow_html=True)
                        cc4.markdown(f"<div class='list-row'>{fmt_vn(r['Tồn Kho (kg)'])} kg</div>", unsafe_allow_html=True)
                        with cc5: edit_exp = st.expander("✏️ Sửa")
                        with cc6: 
                            if st.button("❌", key=f"del_t_{r['db_rowid']}"): cb_xoa_than(r['db_rowid']); st.rerun()
                        
                        with edit_exp:
                            with st.form(f"f_edit_t_{r['db_rowid']}"):
                                en = st.text_input("Tên mới:", value=r['Tên Loại Than'])
                                ep = st.number_input("Đơn giá bán mới (đ):", value=int(float(r['Giá Bán (đ)'])), step=500, format="%d")
                                if st.form_submit_button("Lưu"):
                                    with get_connection() as conn:
                                        conn.execute("UPDATE loai_than SET ten_than=?, gia_mac_dinh=? WHERE rowid=?", (en.strip(), ep, r['db_rowid']))
                                        conn.commit()
                                    st.rerun()

        with t_sub2:
            st.subheader("📦 Ghi nhận tàu nhập bãi hàng vào")
            with st.form("f_c_in"):
                if not df_t_show.empty:
                    than_dict = dict(zip(df_t_show['db_rowid'], df_t_show['Tên Loại Than'].astype(str)))
                    id_n = st.selectbox("Chủng loại:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x))
                    xuong = st.text_input("Nguồn nhập / Tên mỏ Đông Triều:")
                    w_in = st.number_input("Khối lượng nhập bãi (kg):", min_value=1, value=1000, step=100, format="%d")
                    p_in = st.number_input("Giá cả nhập (đ/kg):", value=1500, step=500, format="%d")
                    if st.form_submit_button("Xác nhận lệnh nhập bãi"):
                        with get_connection() as conn: 
                            nid = get_next_id('nhap_hang', conn.cursor()); real_id = df_t_show[df_t_show['db_rowid']==id_n]['id'].values[0]
                            conn.execute('''INSERT INTO nhap_hang(id, loai_than_id, ngay_nhap, xuong_nhap, so_luong, don_gia_nhap, nguoi_tao) VALUES(?,?,?,?,?,?,?)''', (nid, to_int(real_id), today_str, xuong, w_in, p_in, st.session_state.current_user))
                            conn.execute("UPDATE loai_than SET ton_kho=ton_kho+? WHERE rowid=?", (w_in, to_int(id_n))); conn.commit()
                        st.success("Tăng trữ lượng kho bãi thành công!"); st.rerun()
            st.markdown("#### 📜 NHẬT KÝ NHẬP KHO GẦN ĐÂY")
            st.dataframe(df_nhap, hide_index=True)

    # ------------------ 2. KHÁCH HÀNG ------------------
    elif tab_sys == "2. Quản Lý Khách Hàng":
        with get_connection() as conn: df_k = pd.read_sql_query("SELECT rowid as db_rowid, id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps FROM khach_hang", conn.connection)
        
        st.markdown("#### Thêm Đối Tác Mới")
        with st.form("f_k_add"):
            c1, c2 = st.columns(2)
            with c1: kn = st.text_input("Tên KH:"); kp = st.text_input("SĐT:"); kkv = st.selectbox("Khu vực địa lý:", list(MAP_COORDS.keys()))
            with c2: kd = st.text_input("Địa chỉ bãi nhận:"); kmap = st.text_input("Đường link Google Maps:")
            if st.form_submit_button("Lưu Hồ Sơ"):
                with get_connection() as conn:
                    nid = get_next_id('khach_hang', conn.cursor())
                    conn.execute("INSERT INTO khach_hang (id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps, nguoi_tao) VALUES(?,?,?,?,?,?,?,?)", (nid, f"KH{nid:04d}", kn.strip(), kp, kd, kkv, kmap, st.session_state.current_user))
                    conn.commit()
                st.success("Đã lưu hồ sơ đối tác VIP!"); st.rerun()
        
        st.markdown("---")
        st.markdown("#### 📋 Quản Lý Hồ Sơ Đối Tác (Xóa / Sửa)")
        if not df_k.empty: 
            c1, c2, c3, c4, c5 = st.columns([1.5, 3, 2, 4, 1.5])
            c1.markdown("<b>Mã KH</b>", unsafe_allow_html=True); c2.markdown("<b>Tên Khách</b>", unsafe_allow_html=True)
            c3.markdown("<b>SĐT</b>", unsafe_allow_html=True); c4.markdown("<b>Khu Vực</b>", unsafe_allow_html=True); c5.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
            for idx, r in df_k.iterrows():
                with st.container():
                    cc1, cc2, cc3, cc4, cc5, cc6 = st.columns([1.5, 3, 2, 4, 0.75, 0.75])
                    cc1.markdown(f"<div class='list-row'>{r['ma_khach_hang']}</div>", unsafe_allow_html=True)
                    cc2.markdown(f"<div class='list-row'>{r['ten_khach']}</div>", unsafe_allow_html=True)
                    cc3.markdown(f"<div class='list-row'>{r['sdt']}</div>", unsafe_allow_html=True)
                    cc4.markdown(f"<div class='list-row'>{r['khu_vuc']}</div>", unsafe_allow_html=True)
                    with cc5: edit_exp = st.expander("✏️")
                    with cc6: 
                        if st.button("❌", key=f"del_kh_{r['db_rowid']}"): cb_xoa_khach(r['db_rowid']); st.rerun()
                    
                    with edit_exp:
                        with st.form(f"f_edit_kh_{r['db_rowid']}"):
                            ekn = st.text_input("Tên đối tác:", value=r['ten_khach'])
                            ekp = st.text_input("Liên hệ SĐT:", value=r['sdt'])
                            ekk = st.selectbox("Khu vực:", list(MAP_COORDS.keys()), index=list(MAP_COORDS.keys()).index(r['khu_vuc']) if r['khu_vuc'] in MAP_COORDS else 0)
                            if st.form_submit_button("Lưu thay đổi"):
                                with get_connection() as conn: 
                                    conn.execute("UPDATE khach_hang SET ten_khach=?, sdt=?, khu_vuc=? WHERE rowid=?", (ekn.strip(), ekp, ekk, r['db_rowid'])); conn.commit()
                                st.rerun()

    # ------------------ 3. TÀI XẾ ------------------
    elif tab_sys == "3. Quản Lý Tài Xế":
        with get_connection() as conn: df_nv = pd.read_sql_query("SELECT rowid as db_rowid, id, ten_nhan_vien, sdt FROM nhan_vien", conn.connection)
        with st.form("f_v_add"):
            nv_n = st.text_input("Họ tên tài xế vận tải mới:"); nv_p = st.text_input("Số máy điện thoại liên hệ:")
            if st.form_submit_button("Lưu Tài Xế"):
                with get_connection() as conn: conn.execute("INSERT INTO nhan_vien(id, ten_nhan_vien, sdt) VALUES(?,?,?)", (get_next_id('nhan_vien', conn.cursor()), nv_n.strip(), nv_p)); conn.commit()
                st.success("Ghi nhận xe bãi thành công!"); st.rerun()
        st.markdown("---")
        if not df_nv.empty:
            c1, c2, c3 = st.columns([4, 4, 2])
            c1.markdown("<b>Họ Tên Lái Xe</b>", unsafe_allow_html=True); c2.markdown("<b>SĐT</b>", unsafe_allow_html=True); c3.markdown("<b>Thao tác</b>", unsafe_allow_html=True)
            for idx, r in df_nv.iterrows():
                with st.container():
                    cc1, cc2, cc3, cc4 = st.columns([4, 4, 1, 1])
                    cc1.markdown(f"<div class='list-row'>{r['ten_nhan_vien']}</div>", unsafe_allow_html=True)
                    cc2.markdown(f"<div class='list-row'>{r['sdt']}</div>", unsafe_allow_html=True)
                    with cc3: edit_exp = st.expander("✏️")
                    with cc4: 
                        if st.button("❌", key=f"del_tx_{r['db_rowid']}"): cb_xoa_taixe(r['db_rowid']); st.rerun()
                    
                    with edit_exp:
                        with st.form(f"f_edit_tx_{r['db_rowid']}"):
                            en = st.text_input("Tên lái xe:", value=r['ten_nhan_vien'])
                            ep = st.text_input("SĐT:", value=r['sdt'])
                            if st.form_submit_button("Lưu thay đổi"):
                                with get_connection() as conn: 
                                    conn.execute("UPDATE nhan_vien SET ten_nhan_vien=?, sdt=? WHERE rowid=?", (en.strip(), ep, r['db_rowid'])); conn.commit()
                                st.rerun()

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

    # ------------------ 5. CẤU HÌNH IN BILL ------------------
    elif tab_sys == "5. Cấu Hình In Bill":
        with get_connection() as conn: config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn.connection).iloc[0]
        with st.form("form_print_setting"):
            ten_ch = st.text_input("Tên Doanh Nghiệp Thương Hiệu In:", value=config['ten_cua_hang'])
            sdt_ch = st.text_input("Số máy tổng đài in phiếu:", value=config['so_dien_thoai'])
            stk_ch = st.text_area("Thông tin tài khoản thụ hưởng nhận tiền:", value=config['thong_tin_ngan_hang'])
            kho_giay = st.selectbox("Khổ mẫu hóa đơn định dạng giấy in:", ["A4 (Tiêu chuẩn văn phòng)", "A5 (Khổ ngang bằng một nửa A4)", "Khổ K80mm (Máy in bill siêu thị nhiệt)"], index=["A4 (Tiêu chuẩn văn phòng)", "A5 (Khổ ngang bằng một nửa A4)", "Khổ K80mm (Máy in bill siêu thị nhiệt)"].index(config['kho_giay_mac_dinh']) if config['kho_giay_mac_dinh'] else 0)
            if st.form_submit_button("Cập Nhật Tham Số In"):
                with get_connection() as conn: conn.execute("UPDATE cau_hinh_in SET ten_cua_hang=?, so_dien_thoai=?, thong_tin_ngan_hang=?, kho_giay_mac_dinh=? WHERE id=1", (ten_ch, sdt_ch, stk_ch, kho_giay)); conn.commit()
                st.success("Đồng bộ tham số in ấn thành công!"); st.rerun()

    # ------------------ 6. ADMIN (PHÂN QUYỀN VÀ XÓA TÀI KHOẢN) ------------------
    elif tab_sys == "6. Quản Lý Tài Khoản (Admin)":
        with get_connection() as conn: df_users = pd.read_sql_query("SELECT rowid as db_rowid, id, username, role, status FROM users WHERE username != 'admin'", conn.connection)
        
        t_u1, t_u2 = st.tabs(["🟡 Phê Duyệt Tài Khoản Mới", "🟢 Cấp Quyền & Xóa Tài Khoản Cũ"])
        with t_u1:
            if not df_users.empty:
                for idx, r in df_users[df_users['status'] == 'Chờ duyệt'].iterrows():
                    with st.form(f"f_approve_{r['db_rowid']}"):
                        st.write(f"Tài khoản đăng ký: **{r['username']}**")
                        role_assign = st.selectbox("Gắn chức vụ:", options=["ketoan", "laixe"], format_func=lambda x: "Kế toán / Sale" if x == "ketoan" else "Tài xế lái xe")
                        c1, c2 = st.columns(2)
                        with c1: 
                            if st.form_submit_button("✅ Cấp quyền"):
                                with get_connection() as c: c.execute("UPDATE users SET status='Đã duyệt', role=? WHERE rowid=?", (role_assign, r['db_rowid'])); c.commit()
                                st.rerun()
                        with c2:
                            if st.form_submit_button("❌ Từ chối & Xóa"):
                                cb_xoa_user(r['db_rowid']); st.rerun()
        with t_u2:
            st.info("💡 Bạn có thể thay đổi chức vụ hoặc Xóa vĩnh viễn tài khoản của nhân viên đã nghỉ việc tại đây.")
            if not df_users.empty:
                for idx, r in df_users[df_users['status'] == 'Đã duyệt'].iterrows():
                    with st.expander(f"👤 {r['username']} (Quyền: {'Kế toán' if r['role'] == 'ketoan' else 'Lái xe'})"):
                        with st.form(f"f_edit_u_{r['db_rowid']}"):
                            new_role = st.selectbox("Đổi chức vụ:", options=["ketoan", "laixe"], index=0 if r['role'] == 'ketoan' else 1, format_func=lambda x: "Kế toán / Sale" if x == "ketoan" else "Tài xế lái xe")
                            c1, c2 = st.columns(2)
                            with c1:
                                if st.form_submit_button("💾 Lưu Quyền Mới"):
                                    with get_connection() as c: c.execute("UPDATE users SET role=? WHERE rowid=?", (new_role, r['db_rowid'])); c.commit()
                                    st.success("Đã đổi quyền thành công!"); st.rerun()
                            with c2:
                                if st.form_submit_button("🗑️ Xóa vĩnh viễn (Nghỉ việc)"):
                                    cb_xoa_user(r['db_rowid']); st.rerun()

    # ------------------ 7. SYSTEM LOG ------------------
    elif tab_sys == "7. System Log (Theo dõi lỗi)":
        st.markdown("### 🛠️ NHẬT KÝ HỆ THỐNG & PHÂN HỆ KHẨN CẤP AN TOÀN")
        st.markdown("<div class='danger-zone'><h4>⚠️ KHU VỰC KHẨN CẤP GIỚI HẠN QUẢN TRỊ VIÊN</h4><p>Tính năng này yêu cầu mật khẩu Admin tối cao để giải phóng, xóa trắng toàn bộ dữ liệu bãi xe SQLite và dọn dẹp các trang tính Google Sheets về rỗng ban đầu.</p></div><br>", unsafe_allow_html=True)
        col_log1, col_log2 = st.columns([1, 1])
        with col_log1:
            if st.button("🗑️ Xóa bảng Log màn hình"): st.session_state.sys_log = []; st.rerun()
        with col_log2:
            if st.session_state.user_role == 'admin':
                pass_confirm = st.text_input("🔑 XÁC MINH CHÌA KHÓA BẢO MẬT ADMIN ĐỂ RESET KHẨN CẤP:", type="password", key="field_secure_factory_reset")
                if st.button("🚨 KÍCH HOẠT QUY TRÌNH FACTORY RESET", type="primary"):
                    if hash_password(pass_confirm) == hash_password(st.secrets["admin_pass"]):
                        try:
                            with get_connection() as conn:
                                for t in ['loai_than', 'khach_hang', 'nhan_vien', 'gia_rieng', 'lich_su_gia', 'don_hang', 'chi_tiet_don_hang', 'nhap_hang', 'lich_su_thanh_toan']: conn.execute(f"DELETE FROM {t}")
                                conn.commit()
                            try:
                                sheet = get_gspread_client().open_by_url(SHEET_URL)
                                for ws in sheet.worksheets():
                                    if ws.title not in ['users', 'cau_hinh_in']: ws.clear()
                            except: pass
                            st.session_state.sys_log = []
                            write_log("FACTORY RESET", "SUCCESS", "Hệ thống dọn rác khẩn cấp thành công.")
                            st.success("💥 Quy trình xóa dữ liệu hoàn tất! Toàn bộ cơ sở dữ liệu đã quay về trạng thái rỗng. Hãy bấm F5 để làm mới trình duyệt.")
                        except Exception as e: write_log("FACTORY RESET", "ERROR", str(e))
                    else: st.error("❌ XÁC MINH SAI MẬT KHẨU! Lệnh xóa hệ thống đã bị chặn đứng tự động.")
            else: st.error("🔒 Tài khoản của bạn không có quyền thực hiện lệnh hủy diệt hệ thống.")
        log_content = "\n".join(st.session_state.sys_log) if st.session_state.sys_log else "Hệ thống đang hoạt động an toàn ổn định."
        st.markdown(f"<div class='log-box'>{log_content.replace(chr(10), '<br>')}</div>", unsafe_allow_html=True)
