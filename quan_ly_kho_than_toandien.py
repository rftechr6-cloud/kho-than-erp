import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
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

# ==========================================
# 1. TỰ ĐỘNG NHẬN DIỆN THIẾT BỊ & TỐI ƯU GIAO DIỆN
# ==========================================
st.set_page_config(
    page_title="ERP Quản Lý Kho Than V4.1 - Secure Cloud", 
    page_icon="🪨", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
        html, body, [data-testid="stAppViewContainer"] { background-color: #f8fafc; font-family: "Inter", -apple-system, sans-serif; }
        .kpi-card { background: #ffffff; padding: 20px; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); border-top: 4px solid #3b82f6; margin-bottom: 20px; transition: transform 0.2s ease, box-shadow 0.2s ease; }
        .kpi-card:hover { transform: translateY(-3px); box-shadow: 0 8px 15px rgba(0,0,0,0.1); }
        .kpi-label { font-size: 13px; color: #64748b; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 5px; }
        .kpi-value { font-size: 28px; color: #0f172a; font-weight: 800; }
        .border-green { border-top-color: #10b981; }
        .border-red { border-top-color: #ef4444; }
        .border-purple { border-top-color: #8b5cf6; }
        .text-green { color: #10b981; }
        .text-red { color: #ef4444; }
        .text-purple { color: #8b5cf6; }
        .main-header { background: linear-gradient(135deg, #0f172a 0%, #1e293b 100%); padding: 24px; border-radius: 12px; color: white; margin-bottom: 25px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1); }
        .delay-alert { background-color: #fef2f2; border: 1px solid #fecaca; padding: 16px; border-radius: 8px; border-left: 6px solid #ef4444; color: #991b1b; font-size: 14px; margin-bottom: 15px; font-weight: 500; }
        .ai-card { padding:12px; background:#f0fdf4; border-left:4px solid #16a34a; margin-bottom:10px; border-radius:4px; }
        .ai-warn { background:#fffbeb; border-left:4px solid #f59e0b; }
        .ai-danger { background:#fef2f2; border-left:4px solid #dc2626; }
        .map-btn { display: inline-block; padding: 6px 12px; background-color: #e0f2fe; color: #0284c7; text-decoration: none; border-radius: 6px; font-weight: 600; font-size: 13px; margin-top: 8px; }
        .map-btn:hover { background-color: #bae6fd; }
        .invoice-box { background: #ffffff; padding: 30px; border: 1px solid #e2e8f0; border-radius: 8px; margin: 10px auto; color: #1e293b; }
        .invoice-table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        .invoice-table th, .invoice-table td { padding: 12px; border-bottom: 1px solid #e2e8f0; }
        .invoice-table th { background-color: #f1f5f9; text-align: left; font-weight: bold; }
        .size-a4 { max-width: 800px; }
        .size-a5 { max-width: 600px; font-size: 13px; }
        .size-80mm { max-width: 320px; font-size: 11px; padding: 10px; }
    </style>
""", unsafe_allow_html=True)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# 2. BẢO MẬT KẾT NỐI GOOGLE SHEETS & SQLITE
# ==========================================
try:
    SHEET_URL = st.secrets["sheet_url"]
except KeyError:
    st.error("Chưa cấu hình Két sắt bảo mật (Secrets) cho hệ thống.")
    st.stop()

@st.cache_resource
def get_gspread_client():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    try:
        creds_dict = json.loads(st.secrets["google_key"])
        
        # --- DÒNG CODE VÀNG: Ép đổi text thành dấu xuống dòng chuẩn ---
        creds_dict["private_key"] = creds_dict["private_key"].replace("\\n", "\n")
        
        creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
        return gspread.authorize(creds)
    except Exception as e:
        st.error(f"Lỗi đọc chìa khóa bảo mật: {e}")
        st.stop()
@st.cache_resource
def init_local_db():
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    client = get_gspread_client()
    sheet = client.open_by_url(SHEET_URL)
    
    for ws in sheet.worksheets():
        data = ws.get_all_records()
        if data:
            df = pd.DataFrame(data)
            df.to_sql(ws.title, conn, if_exists='replace', index=False)
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
            for col in df.select_dtypes(include=['datetime64', 'datetimetz']).columns:
                df[col] = df[col].astype(str)
                
            try:
                ws = sheet.worksheet(table_name)
            except gspread.WorksheetNotFound:
                ws = sheet.add_worksheet(title=table_name, rows=100, cols=20)
                
            ws.clear()
            if not df.empty:
                ws.update(values=[df.columns.values.tolist()] + df.fillna("").astype(str).values.tolist(), range_name="A1")
    except Exception:
        pass 
    finally:
        bg_conn.close()

@contextmanager
def get_connection():
    conn = sqlite3.connect("kho_than.db", check_same_thread=False)
    
    class CursorWrapper:
        def __init__(self, cursor):
            self.cursor = cursor
        def execute(self, query, params=None):
            query = query.replace('%s', '?')
            if params: return self.cursor.execute(query, params)
            return self.cursor.execute(query)
        def fetchone(self): return self.cursor.fetchone()
        def fetchall(self): return self.cursor.fetchall()
        def __getattr__(self, name): return getattr(self.cursor, name)

    class ConnectionWrapper:
        def __init__(self, connection):
            self.connection = connection
            
        def commit(self):
            self.connection.commit()
            sync_thread = threading.Thread(target=background_sync_task)
            sync_thread.daemon = True
            sync_thread.start()
                
        def cursor(self):
            return CursorWrapper(self.connection.cursor())
            
        def close(self):
            self.connection.close()
            
        def __getattr__(self, name):
            return getattr(self.connection, name)
    
    try:
        yield ConnectionWrapper(conn)
    finally:
        conn.close()

def init_database():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username VARCHAR(255) UNIQUE NOT NULL, password VARCHAR(255) NOT NULL, role VARCHAR(50) DEFAULT 'user', status VARCHAR(50) DEFAULT 'Chờ duyệt')''')
        
        cursor.execute("SELECT * FROM users WHERE username='admin'")
        if not cursor.fetchone(): 
            cursor.execute("INSERT INTO users (username, password, role, status) VALUES (?, ?, 'admin', 'Đã duyệt')", 
                           ('admin', hash_password(st.secrets["admin_pass"])))

        cursor.execute('''CREATE TABLE IF NOT EXISTS loai_than (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ten_than VARCHAR(255) UNIQUE NOT NULL, gia_nhap_mac_dinh DOUBLE PRECISION DEFAULT 0, gia_mac_dinh DOUBLE PRECISION DEFAULT 0, ton_kho DOUBLE PRECISION DEFAULT 0, nguoi_tao VARCHAR(255) DEFAULT 'Hệ thống')''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS khach_hang (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ma_khach_hang VARCHAR(50) UNIQUE, ten_khach VARCHAR(255) UNIQUE NOT NULL, sdt VARCHAR(50), dia_chi TEXT, khu_vuc VARCHAR(255) DEFAULT 'Chưa rõ', link_google_maps TEXT, nguoi_tao VARCHAR(255) DEFAULT 'Hệ thống')''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS nhan_vien (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ten_nhan_vien VARCHAR(255) UNIQUE NOT NULL, sdt VARCHAR(50), chuc_vu VARCHAR(100) DEFAULT 'Giao hàng')''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS gia_rieng (
            khach_hang_id INTEGER, loai_than_id INTEGER, gia_uu_dai DOUBLE PRECISION NOT NULL, PRIMARY KEY (khach_hang_id, loai_than_id), FOREIGN KEY (khach_hang_id) REFERENCES khach_hang(id) ON DELETE CASCADE, FOREIGN KEY (loai_than_id) REFERENCES loai_than(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_gia (
            id INTEGER PRIMARY KEY AUTOINCREMENT, khach_hang_id INTEGER, loai_than_id INTEGER, gia_cu DOUBLE PRECISION, gia_moi DOUBLE PRECISION, ngay_thay_doi TIMESTAMP, FOREIGN KEY (khach_hang_id) REFERENCES khach_hang(id) ON DELETE CASCADE, FOREIGN KEY (loai_than_id) REFERENCES loai_than(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS don_hang (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ma_don_hien_thi VARCHAR(50) UNIQUE, khach_hang_id INTEGER, nhan_vien_id INTEGER, ngay_ban DATE NOT NULL, thoi_gian_tao TIMESTAMP NOT NULL, da_thanh_toan INTEGER DEFAULT 0, trang_thai_giao VARCHAR(100) DEFAULT 'Chờ giao hàng', hinh_thuc_thanh_toan VARCHAR(100) DEFAULT 'Chưa thanh toán', ghi_chu TEXT, giao_gap INTEGER DEFAULT 0, tong_tien DOUBLE PRECISION DEFAULT 0, tien_da_tra DOUBLE PRECISION DEFAULT 0, tien_con_no DOUBLE PRECISION DEFAULT 0, nguoi_tao VARCHAR(255) DEFAULT 'Hệ thống', FOREIGN KEY (khach_hang_id) REFERENCES khach_hang(id) ON DELETE CASCADE, FOREIGN KEY (nhan_vien_id) REFERENCES nhan_vien(id) ON DELETE SET NULL)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chi_tiet_don_hang (
            id INTEGER PRIMARY KEY AUTOINCREMENT, don_hang_id INTEGER, loai_than_id INTEGER, so_luong DOUBLE PRECISION NOT NULL, don_gia DOUBLE PRECISION NOT NULL, FOREIGN KEY (don_hang_id) REFERENCES don_hang(id) ON DELETE CASCADE, FOREIGN KEY (loai_than_id) REFERENCES loai_than(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS nhap_hang (
            id INTEGER PRIMARY KEY AUTOINCREMENT, loai_than_id INTEGER, ngay_nhap DATE NOT NULL, so_luong DOUBLE PRECISION NOT NULL, don_gia_nhap DOUBLE PRECISION NOT NULL, nguoi_tao VARCHAR(255) DEFAULT 'Hệ thống', FOREIGN KEY (loai_than_id) REFERENCES loai_than(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_thanh_toan (
            id INTEGER PRIMARY KEY AUTOINCREMENT, don_hang_id INTEGER, so_tien_tra DOUBLE PRECISION NOT NULL, hinh_thuc VARCHAR(100), ngay_tra TIMESTAMP, ghi_chu TEXT, nguoi_tao VARCHAR(255) DEFAULT 'Hệ thống', FOREIGN KEY (don_hang_id) REFERENCES don_hang(id) ON DELETE CASCADE)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS cau_hinh_in (
            id INTEGER PRIMARY KEY CHECK (id = 1), ten_cua_hang VARCHAR(255) DEFAULT 'TỔNG KHO THAN', so_dien_thoai VARCHAR(50) DEFAULT '0988.888.888', thong_tin_ngan_hang TEXT DEFAULT 'Chưa cài đặt', kho_giay_mac_dinh VARCHAR(100) DEFAULT 'A4 (Tiêu chuẩn văn phòng)')''')
        
        cursor.execute("INSERT OR IGNORE INTO cau_hinh_in (id, thong_tin_ngan_hang) VALUES (1, 'Chưa cài đặt')")
        cursor.execute("SELECT id FROM khach_hang WHERE ma_khach_hang IS NULL")
        old_custs = cursor.fetchall()
        for c in old_custs: cursor.execute("UPDATE khach_hang SET ma_khach_hang = ? WHERE id = ?", (f"KH{c[0]:04d}", c[0]))
        conn.commit()

init_database()

# ==========================================
# 3. HỆ THỐNG ĐĂNG NHẬP & SESSION STATE
# ==========================================
if 'logged_in' not in st.session_state: st.session_state.logged_in = False
if 'current_user' not in st.session_state: st.session_state.current_user = None
if 'user_role' not in st.session_state: st.session_state.user_role = None
if 'cart' not in st.session_state: st.session_state.cart = []
if 'last_order_id' not in st.session_state: st.session_state.last_order_id = None

# ĐỒNG BỘ MÚI GIỜ CHUẨN VIỆT NAM (UTC + 7) ĐỂ TRÁNH LỖI NGÀY HÔM NAY TRÊN MÁY CHỦ CLOUD
now_dt = datetime.utcnow() + timedelta(hours=7)
today_str = now_dt.strftime('%Y-%m-%d')

if not st.session_state.logged_in:
    st.markdown("<div class='main-header'><h1 style='text-align:center;'>HỆ THỐNG QUẢN TRỊ KHO THAN CLOUD</h1></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        tab_login, tab_reg = st.tabs(["🔐 Đăng Nhập", "📝 Đăng Ký Tài Khoản"])
        with tab_login:
            with st.form("login_form"):
                user = st.text_input("Tài khoản:")
                pwd = st.text_input("Mật khẩu:", type="password")
                if st.form_submit_button("Đăng Nhập ", type="primary"):
                    with get_connection() as conn:
                        res = conn.cursor()
                        res.execute("SELECT role, status FROM users WHERE username=%s AND password=%s", (user, hash_password(pwd)))
                        data = res.fetchone()
                        if data:
                            role, status = data
                            if status == "Đã duyệt" or role == "admin":
                                st.session_state.logged_in = True
                                st.session_state.current_user = user
                                st.session_state.user_role = role
                                st.rerun()
                            else: st.error("Tài khoản của bạn đang chờ Admin phê duyệt. Vui lòng liên hệ quản lý!")
                        else: st.error("Sai tài khoản hoặc mật khẩu!")
        
        with tab_reg:
            with st.form("reg_form"):
                n_user = st.text_input("Tài khoản muốn tạo:")
                n_pwd = st.text_input("Mật khẩu:", type="password")
                n_pwd2 = st.text_input("Nhập lại mật khẩu:", type="password")
                if st.form_submit_button("Gửi Yêu Cầu Đăng Ký"):
                    if n_pwd != n_pwd2: st.error("Mật khẩu không khớp!")
                    elif len(n_user) < 3: st.error("Tài khoản phải từ 3 ký tự!")
                    else:
                        try:
                            with get_connection() as conn:
                                conn.cursor().execute("INSERT INTO users (username, password, role, status) VALUES (%s, %s, 'user', 'Chờ duyệt')", (n_user, hash_password(n_pwd)))
                                conn.commit()
                            st.success("Đã gửi yêu cầu! Vui lòng chờ Quản trị viên phê duyệt.")
                        except: st.error("Tài khoản này đã tồn tại!")
    st.stop()

def sinh_ma_don_hang_theo_ngay(ngay_str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM don_hang WHERE ngay_ban = %s", (ngay_str,))
        sttt = cursor.fetchone()[0] + 1
        chuoi_ngay = datetime.strptime(ngay_str, '%Y-%m-%d').strftime('%d%m%y')
        return f"DH{chuoi_ngay}-{sttt:03d}"

MAP_COORDS = {
    "Thái Nguyên": {"lat": 21.5942, "lon": 105.8443}, "Hà Nội": {"lat": 21.0285, "lon": 105.8542},
    "Bắc Giang": {"lat": 21.2731, "lon": 106.1946}, "Vĩnh Phúc": {"lat": 21.3089, "lon": 105.6049},
    "Sông Công": {"lat": 21.4883, "lon": 105.8465}, "Phổ Yên": {"lat": 21.4178, "lon": 105.8661},
    "Điềm Thụy": {"lat": 21.4833, "lon": 105.9500}, "Quế Võ": {"lat": 21.1352, "lon": 106.1558},
    "Yên Phong": {"lat": 21.2069, "lon": 105.9781}, "Khác": {"lat": 21.0, "lon": 105.5}
}

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2850/2850785.png", width=80)
    st.markdown("<h2 style='color: #0f172a; margin-top:0;'>Kho Than ERP</h2>", unsafe_allow_html=True)
    st.markdown(f"👤 Chào, **{st.session_state.current_user}** ({st.session_state.user_role})")
    if st.button("🚪 Đăng Xuất"):
        st.session_state.clear(); st.rerun()
    st.markdown("---")
    
    menu = option_menu(
        menu_title="CHỨC NĂNG CỐT LÕI", 
        options=[
            "Thống Kê (HQ)", 
            "Lập Đơn & In Phiếu", 
            "Giao Hàng & Vận Tải", 
            "Sổ Quản Lý Nợ",
            "Lịch Sử Đơn Hàng",
            "Cài Đặt Hệ Thống"
        ],
        icons=['bar-chart-fill', 'receipt-cutoff', 'truck', 'wallet-fill', 'clock-history', 'gear-fill'],
        menu_icon="boxes",
        default_index=0,
        styles={
            "container": {"padding": "0!important", "background-color": "transparent"},
            "icon": {"color": "#3b82f6", "font-size": "18px"},
            "nav-link": {"font-size": "15px", "text-align": "left", "margin": "4px 0px", "border-radius": "8px", "--hover-color": "#e2e8f0", "font-weight": "500"},
            "nav-link-selected": {"background-color": "#1e293b", "color": "white", "font-weight": "bold"},
        }
    )

# ==========================================
# PHÂN HỆ 1: TRUNG TÂM THỐNG KÊ (HQ DASHBOARD)
# ==========================================
if menu == "Thống Kê (HQ)":
    st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📊 PHÂN HỆ GIÁM SÁT KINH DOANH TỔNG THỂ</h1></div>", unsafe_allow_html=True)
    time_filter = st.radio("⏳ Mốc thời gian phân tích:", ["Hôm nay", "Tuần này", "Tháng này", "Tất cả thời gian"], horizontal=True)
    st.markdown("<br>", unsafe_allow_html=True)

    with get_connection() as conn:
        q_flat = '''SELECT dh.id as don_id, dh.thoi_gian_tao, dh.da_thanh_toan, dh.trang_thai_giao, dh.ngay_ban, kh.ten_khach, kh.khu_vuc, lt.ten_than, lt.gia_nhap_mac_dinh, ctdh.so_luong, ctdh.don_gia, (ctdh.so_luong * ctdh.don_gia) as thanh_tien FROM don_hang dh JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id JOIN khach_hang kh ON dh.khach_hang_id = kh.id JOIN loai_than lt ON ctdh.loai_than_id = lt.id'''
        df_flat = pd.read_sql_query(q_flat, conn)
        
        q_group = '''SELECT dh.id as don_id, dh.ma_don_hien_thi, dh.thoi_gian_tao, dh.trang_thai_giao, dh.giao_gap, dh.tong_tien, dh.tien_con_no, dh.nguoi_tao, kh.ma_khach_hang, kh.ten_khach, nv.ten_nhan_vien, GROUP_CONCAT(lt.ten_than || ' (' || CAST(ctdh.so_luong AS INT) || 'kg)', ', ') as chi_tiet_hang FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id JOIN loai_than lt ON ctdh.loai_than_id = lt.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id GROUP BY dh.id, kh.ma_khach_hang, kh.ten_khach, nv.ten_nhan_vien ORDER BY dh.id DESC'''
        df_group = pd.read_sql_query(q_group, conn)
        df_kho_status = pd.read_sql_query("SELECT ten_than, ton_kho FROM loai_than", conn)

    if not df_flat.empty:
        df_flat['Date'] = pd.to_datetime(df_flat['thoi_gian_tao'])
        df_flat['loi_nhuan'] = (df_flat['don_gia'] - df_flat['gia_nhap_mac_dinh']) * df_flat['so_luong']
        if time_filter == "Hôm nay": df_flat = df_flat[df_flat['Date'].dt.date == now_dt.date()]
        elif time_filter == "Tuần này": df_flat = df_flat[df_flat['Date'].dt.date >= (now_dt - timedelta(days=now_dt.weekday())).date()]
        elif time_filter == "Tháng này": df_flat = df_flat[(df_flat['Date'].dt.month == now_dt.month) & (df_flat['Date'].dt.year == now_dt.year)]
        
    if not df_group.empty:
        df_group['Date'] = pd.to_datetime(df_group['thoi_gian_tao'])
        if time_filter == "Hôm nay": df_group = df_group[df_group['Date'].dt.date == now_dt.date()]
        elif time_filter == "Tuần này": df_group = df_group[df_group['Date'].dt.date >= (now_dt - timedelta(days=now_dt.weekday())).date()]
        elif time_filter == "Tháng này": df_group = df_group[(df_group['Date'].dt.month == now_dt.month) & (df_group['Date'].dt.year == now_dt.year)]

    # CƠ CHẾ HIỂN THỊ KPI AN TOÀN TUYỆT ĐỐI KHÔNG BỊ TRẮNG MÀN HÌNH
    if df_group.empty:
        total_rev = 0
        debt_rev = 0
        pending_count = 0
        total_orders = 0
    else:
        total_rev = df_group['tong_tien'].sum()
        debt_df = df_group[df_group['trang_thai_giao'] == 'Đã hoàn thành']
        debt_rev = debt_df['tien_con_no'].sum() if not debt_df.empty else 0
        pending_df = df_group[df_group['trang_thai_giao'] != 'Đã hoàn thành']
        pending_count = pending_df['don_id'].nunique() if not pending_df.empty else 0
        total_orders = df_group['don_id'].nunique()

    if df_flat.empty:
        total_profit = 0
    else:
        total_profit = df_flat['loi_nhuan'].sum()
    
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown(f"<div class='kpi-card'><div class='kpi-label'>📦 Tổng Đơn Cần Giao</div><div class='kpi-value'>{total_orders} <span style='font-size:14px;color:#64748b;'>({pending_count} chờ)</span></div></div>", unsafe_allow_html=True)
    with c2: st.markdown(f"<div class='kpi-card border-green'><div class='kpi-label'>💵 Doanh Thu Tạm Tính</div><div class='kpi-value text-green'>{total_rev:,.0f} đ</div></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='kpi-card border-purple'><div class='kpi-label'>📈 Lợi Nhuận Gộp</div><div class='kpi-value text-purple'>{total_profit:,.0f} đ</div></div>", unsafe_allow_html=True)
    with c4: st.markdown(f"<div class='kpi-card border-red'><div class='kpi-label'>🛑 Nợ Thực Tế (Đã Giao)</div><div class='kpi-value text-red'>{debt_rev:,.0f} đ</div></div>", unsafe_allow_html=True)

    st.markdown("---")

    if not df_kho_status.empty:
        for _, r in df_kho_status[df_kho_status['ton_kho'] < 500].iterrows():
            st.markdown(f"<div class='delay-alert' style='border-left-color:#f59e0b; background-color:#fffbeb; color:#b45309;'>⚠️ <b>SẮP HẾT HÀNG</b>: Mã <b>{r['ten_than']}</b> chỉ còn <b>{r['ton_kho']:,.0f} kg</b>. Cần nhập bãi!</div>", unsafe_allow_html=True)

    if df_group.empty or df_flat.empty:
        st.info("📌 Hệ thống chưa ghi nhận phát sinh đơn hàng nào trong mốc thời gian này. Hãy thử chọn mốc thời gian khác (VD: Tuần này hoặc Tất cả thời gian) để xem thống kê chi tiết.")
    else:
        for _, r in df_group[df_group['trang_thai_giao'] != 'Đã hoàn thành'].iterrows():
            hours_elapsed = (now_dt - pd.to_datetime(r['thoi_gian_tao'])).total_seconds() / 3600
            if hours_elapsed > 4:
                tx_name = r['ten_nhan_vien'] if r['ten_nhan_vien'] else "Chưa phân xe"
                st.markdown(f"<div class='delay-alert'>🚨 <b>ĐƠN TRỄ QUÁ 4 GIỜ (Mã {r['ma_don_hien_thi']})</b><br>• Tài xế: {tx_name} | Đối tác: {r['ten_khach']} | Người lên đơn: <b>{r['nguoi_tao']}</b> | Chờ: {hours_elapsed:.1f} giờ</div>", unsafe_allow_html=True)
                
        st.markdown("### 🤖 Trợ Lý AI: Phân Tích Hành Vi & Gợi Ý Chiến Lược")
        with get_connection() as conn:
            df_ai = pd.read_sql_query("SELECT dh.id, dh.ngay_ban, kh.ten_khach, lt.ten_than, ctdh.so_luong, (ctdh.so_luong * ctdh.don_gia) as thanh_tien FROM don_hang dh JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id JOIN khach_hang kh ON dh.khach_hang_id = kh.id JOIN loai_than lt ON ctdh.loai_than_id = lt.id", conn)
        
        if not df_ai.empty:
            df_ai['ngay_ban_dt'] = pd.to_datetime(df_ai['ngay_ban'])
            top_10 = df_ai.groupby('ten_khach').agg({'so_luong':'sum', 'thanh_tien':'sum'}).reset_index().sort_values(by='so_luong', ascending=False).head(10)
            
            df_ai_sort = df_ai.sort_values(by=['ten_khach', 'ngay_ban_dt'])
            df_ai_sort['ngay_ban_truoc'] = df_ai_sort.groupby('ten_khach')['ngay_ban_dt'].shift(1)
            df_ai_sort['khoang_cach_ngay'] = (df_ai_sort['ngay_ban_dt'] - df_ai_sort['ngay_ban_truoc']).dt.days
            
            ai_khach = df_ai_sort.groupby('ten_khach').agg(ngay_mua_cuoi=('ngay_ban_dt', 'max'), chu_ky_mua=('khoang_cach_ngay', 'mean')).reset_index()
            ai_khach['ngay_chua_mua'] = (now_dt - ai_khach['ngay_mua_cuoi']).dt.days
            khach_can_cham_soc = ai_khach[(ai_khach['chu_ky_mua'] > 0) & (ai_khach['ngay_chua_mua'] > ai_khach['chu_ky_mua'] + 5)].copy()
            
            date_30 = now_dt - timedelta(days=30)
            date_60 = now_dt - timedelta(days=60)
            than_30d = df_ai[df_ai['ngay_ban_dt'] >= date_30].groupby('ten_than')['so_luong'].sum().reset_index().rename(columns={'so_luong':'sl_30d'})
            than_60d = df_ai[(df_ai['ngay_ban_dt'] >= date_60) & (df_ai['ngay_ban_dt'] < date_30)].groupby('ten_than')['so_luong'].sum().reset_index().rename(columns={'so_luong':'sl_60d'})
            trend_than = pd.merge(than_30d, than_60d, on='ten_than', how='outer').fillna(0)
            trend_than['tang_truong'] = trend_than['sl_30d'] - trend_than['sl_60d']
            
            t_ai1, t_ai2, t_ai3 = st.tabs(["🏆 1. Top 10 Đối Tác VIP", "⚠️ 2. Cảnh Báo Khách Rời Bỏ", "📊 3. Dự Báo Nhập Kho"])
            with t_ai1: st.dataframe(top_10.rename(columns={'ten_khach':'Tên Đối Tác', 'so_luong':'Sản Lượng (kg)', 'thanh_tien':'Doanh Thu (đ)'}).style.format({'Sản Lượng (kg)':'{:,.0f}', 'Doanh Thu (đ)':'{:,.0f}'}), use_container_width=True, hide_index=True)
            with t_ai2:
                if khach_can_cham_soc.empty: st.success("✅ Toàn bộ đối tác đang quay vòng đặt hàng ổn định.")
                else:
                    for _, r in khach_can_cham_soc.iterrows():
                        st.markdown(f"<div class='ai-card ai-warn'>⚠️ Khách hàng <b>{r['ten_khach']}</b> thường <b>{r['chu_ky_mua']:.0f} ngày đặt 1 lần</b>, hiện đã quá hạn <b>{r['ngay_chua_mua']} ngày</b>. Cần liên hệ chăm sóc!</div>", unsafe_allow_html=True)
            with t_ai3:
                for _, r in trend_than.iterrows():
                    if r['tang_truong'] > 0: st.markdown(f"<div class='ai-card'>📈 Chủng loại <b>{r['ten_than']}</b> sức mua TĂNG MẠNH <b>+{r['tang_truong']:,.0f} kg</b> so với tháng trước. Đề xuất chuẩn bị nhập thêm.</div>", unsafe_allow_html=True)
                    elif r['tang_truong'] < 0: st.markdown(f"<div class='ai-card ai-danger'>📉 Chủng loại <b>{r['ten_than']}</b> sức mua GIẢM <b>{r['tang_truong']:,.0f} kg</b>. Xem xét hạn chế nhập tàu.</div>", unsafe_allow_html=True)

        st.markdown("---")
        st.markdown("### 🗺️ Bản Đồ Phân Bổ Mở Rộng Thị Trường")
        map_data = df_flat.groupby('khu_vuc')['so_luong'].sum().reset_index()
        map_data['lat'] = map_data['khu_vuc'].apply(lambda x: MAP_COORDS.get(x, MAP_COORDS["Khác"])['lat'])
        map_data['lon'] = map_data['khu_vuc'].apply(lambda x: MAP_COORDS.get(x, MAP_COORDS["Khác"])['lon'])
        
        if not map_data.empty:
            fig_map = px.scatter_mapbox(
                map_data, lat="lat", lon="lon", size="so_luong", color="khu_vuc",
                hover_name="khu_vuc", hover_data={"lat":False, "lon":False, "so_luong":True},
                color_discrete_sequence=px.colors.qualitative.G10, zoom=7, height=400,
                title="Sản lượng tiêu thụ theo vị trí địa lý (kg)"
            )
            fig_map.update_layout(mapbox_style="carto-positron", margin={"r":0,"t":40,"l":0,"b":0})
            st.plotly_chart(fig_map, use_container_width=True)

        st.markdown("### 📊 Chi Tiết Các Mảng Thống Kê Phân Bổ")
        ch1, ch2 = st.columns(2) 
        with ch1:
            fig2 = px.pie(df_flat.groupby('ten_than')['so_luong'].sum().reset_index(), values='so_luong', names='ten_than', hole=0.4, title="Tỷ trọng than xuất kho")
            st.plotly_chart(fig2, use_container_width=True)
        with ch2:
            fig3 = px.pie(df_flat.groupby('ten_khach')['loi_nhuan'].sum().reset_index(), values='loi_nhuan', names='ten_khach', hole=0.4, title="Lợi nhuận theo khách hàng")
            st.plotly_chart(fig3, use_container_width=True)

# ==========================================
# PHÂN HỆ 2: LẬP ĐƠN & IN PHIẾU
# ==========================================
elif menu == "Lập Đơn & In Phiếu":
    st.markdown("### 📋 Lập Lệnh Xuất Kho / Bán Hàng")
    with get_connection() as conn:
        print_config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn).iloc[0]
        
    if st.session_state.last_order_id:
        with get_connection() as conn:
            master = pd.read_sql_query(f"SELECT * FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.id = {st.session_state.last_order_id}", conn).iloc[0]
            details = pd.read_sql_query(f"SELECT ctdh.*, lt.ten_than FROM chi_tiet_don_hang ctdh JOIN loai_than lt ON ctdh.loai_than_id = lt.id WHERE ctdh.don_hang_id = {st.session_state.last_order_id}", conn)
            
        css_class = "max-width: 800px;"
        if print_config['kho_giay_mac_dinh'] == "A5 (Khổ ngang bằng một nửa A4)": css_class = "max-width: 600px; font-size: 13px;"
        elif print_config['kho_giay_mac_dinh'] == "Khổ K80mm (Máy in bill siêu thị nhiệt)": css_class = "max-width: 320px; font-size: 11px;"
        
        html_rows = ""
        total_val = 0
        for _, r in details.iterrows():
            thanh_tien = r['so_luong'] * r['don_gia']
            total_val += thanh_tien
            html_rows += f"<tr><td style='padding: 8px; border-bottom: 1px solid #e2e8f0;'>{r['ten_than']}</td><td style='padding: 8px; border-bottom: 1px solid #e2e8f0; text-align:center;'>{r['so_luong']:,.0f}</td><td style='padding: 8px; border-bottom: 1px solid #e2e8f0; text-align:right;'>{r['don_gia']:,.0f}</td><td style='padding: 8px; border-bottom: 1px solid #e2e8f0; text-align:right; font-weight:bold;'>{thanh_tien:,.0f}</td></tr>"
            
        full_html_print = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <title>Phiếu Xuất Kho - {master["ma_don_hien_thi"]}</title>
            <style>
                body {{ font-family: sans-serif; color: #1e293b; margin: 20px; }}
                .invoice-box {{ {css_class}; margin: 0 auto; padding: 20px; border: 1px solid #ccc; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
                th {{ background: #f1f5f9; text-align: left; padding: 10px; border-bottom: 2px solid #cbd5e1; }}
                @media print {{ .invoice-box {{ border: none; padding: 0; }} }}
            </style>
        </head>
        <body onload="window.print()">
            <div class="invoice-box">
                <h2 style="text-align:center; margin-bottom: 5px; color:#0f172a; font-weight:900;">{print_config["ten_cua_hang"]}</h2>
                <p style="text-align:center; margin-top:0; font-size:13px; color:#475569;">SĐT: <b>{print_config["so_dien_thoai"]}</b></p>
                <hr style="border:0; border-top:1px dashed #cbd5e1; margin: 15px 0;">
                <h3 style="text-align:center; margin-top:10px; margin-bottom:5px;">PHIẾU XUẤT KHO KIÊM GIAO HÀNG</h3>
                <p style="text-align:center; color:#64748b; margin-top:0; font-size:12px;">Mã Đơn: <b>{master["ma_don_hien_thi"]}</b> | Ngày lập: {master["thoi_gian_tao"]}</p>
                <p style="margin-bottom:5px; margin-top:15px;"><b>Khách Hàng:</b> {master["ten_khach"]} ({master["sdt"]})</p>
                <p style="margin-top:0; margin-bottom:15px;"><b>Địa chỉ nhận:</b> {master["dia_chi"]} <br><b>Ghi chú:</b> {master["ghi_chu"]}</p>
                <table>
                    <thead><tr><th>Chủng Loại</th><th style="text-align:center;">SL (kg)</th><th style="text-align:right;">Đơn Giá</th><th style="text-align:right;">Thành Tiền</th></tr></thead>
                    <tbody>{html_rows}
                    <tr><td colspan="3" style="text-align:right; font-weight:bold; padding-top:15px; border:none;">TỔNG CỘNG:</td>
                    <td style="text-align:right; font-weight:bold; padding-top:15px; font-size:16px; border:none;">{total_val:,.0f} đ</td></tr>
                    </tbody>
                </table>
                <p style="margin-top:20px; font-size:13px; padding:12px; border:1px dashed #ccc;"><b>THÔNG TIN THANH TOÁN:</b><br>{print_config["thong_tin_ngan_hang"]}</p>
                <table style="width:100%; margin-top:30px; text-align:center; font-weight:bold; font-size:13px; border:none;">
                    <tr><td style="border:none;">Người Lập Phiếu<br><br><br><b>{master["nguoi_tao"]}</b></td><td style="border:none;">Người Nhận Hàng<br><br><br><br></td></tr>
                </table>
            </div>
        </body>
        </html>
        """
        
        b64 = base64.b64encode(full_html_print.encode('utf-8')).decode()
        print_href = f'<a href="data:text/html;base64,{b64}" target="_blank" style="display: block; text-align: center; background-color: #3b82f6; color: white; padding: 12px 24px; border-radius: 8px; font-weight: bold; text-decoration: none; margin-top: 15px; font-size: 16px;">🖨️ BẤM VÀO ĐÂY ĐỂ IN PHIẾU XUẤT NÀY</a>'
        
        st.success("Tạo đơn hàng thành công!")
        st.markdown(print_href, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        
        if st.button("🔄 LẬP ĐƠN MỚI TẾP THEO"):
            st.session_state.last_order_id = None; st.rerun()
    else:
        with get_connection() as conn:
            df_khach = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach FROM khach_hang", conn)
            df_than = pd.read_sql_query("SELECT id, ten_than, gia_mac_dinh, ton_kho FROM loai_than", conn)

        if df_khach.empty or df_than.empty: st.warning("Vui lòng cấu hình Khách hàng và Loại than ở Tab Cài Đặt trước.")
        else:
            khach_id = st.selectbox("👤 Chọn Khách Hàng:", options=df_khach['id'].tolist(), format_func=lambda x: f"[{df_khach[df_khach['id']==x]['ma_khach_hang'].values[0]}] {df_khach[df_khach['id']==x]['ten_khach'].values[0]}")
            st.markdown("---")
            
            with get_connection() as conn: df_pb = pd.read_sql_query(f"SELECT loai_than_id FROM gia_rieng WHERE khach_hang_id = {khach_id}", conn)
            than_options = df_than[df_than['id'].isin(df_pb['loai_than_id'].tolist())] if not df_pb.empty else df_than
            if than_options.empty: than_options = df_than
            
            t_id = st.selectbox("🪨 Chọn loại than:", options=than_options['id'].tolist(), format_func=lambda x: than_options[than_options['id']==x]['ten_than'].values[0])
            with get_connection() as conn: 
                cur = conn.cursor()
                cur.execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=%s AND loai_than_id=%s", (khach_id, t_id))
                gr_res = cur.fetchone()
            
            gia_goi_y = gr_res[0] if gr_res else df_than[df_than['id']==t_id]['gia_mac_dinh'].values[0]
            st.caption(f"Trữ lượng bãi thực tế: **{df_than[df_than['id']==t_id]['ton_kho'].values[0]:,.0f} kg**")
            
            col_sl, col_dg = st.columns(2)
            with col_sl: sl = st.number_input("Khối lượng (kg):", min_value=1.0, value=1000.0, step=500.0)
            with col_dg: dg = st.number_input("Đơn giá bán (đ/kg):", value=float(gia_goi_y), step=10.0)
            
            if st.button("➕ Thêm vào phiếu"):
                if any(i['loai_than_id'] == t_id for i in st.session_state.cart): st.error("Mã này đã có trong giỏ!")
                else: st.session_state.cart.append({'loai_than_id': t_id, 'ten_than': df_than[df_than['id']==t_id]['ten_than'].values[0], 'so_luong': sl, 'don_gia': dg, 'thanh_tien': sl * dg}); st.rerun()

            if st.session_state.cart:
                df_c = pd.DataFrame(st.session_state.cart)
                st.dataframe(df_c[['ten_than', 'so_luong', 'don_gia', 'thanh_tien']].style.format({'so_luong': '{:,.0f}', 'don_gia': '{:,.0f}', 'thanh_tien': '{:,.0f}'}), use_container_width=True, hide_index=True)
                total_val = df_c['thanh_tien'].sum()
                st.markdown(f"### 💰 Tổng Hóa Đơn: <span style='color:#dc2626'>{total_val:,.0f} đ</span>", unsafe_allow_html=True)
                
                if st.button("🗑️ Xóa giỏ hàng"): st.session_state.cart = []; st.rerun()
                st.markdown("---")
                giao_gap = st.checkbox("🔥 ĐƠN HÀNG GIAO GẤP")
                g_chu = st.text_input("Ghi chú biển số xe/tài xế (nếu có):")
                
                if st.button("🚀 CHỐT LỆNH XUẤT", type="primary"):
                    stock_ok = True
                    for i in st.session_state.cart:
                        if i['so_luong'] > df_than[df_than['id']==i['loai_than_id']]['ton_kho'].values[0]: stock_ok = False; st.error(f"❌ Mã {i['ten_than']} vượt quá tồn kho!")
                    if stock_ok:
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        ma_don_final = sinh_ma_don_hang_theo_ngay(today_str)
                        is_gap = 1 if giao_gap else 0
                        with get_connection() as conn:
                            cur = conn.cursor()
                            cur.execute('INSERT INTO don_hang (ma_don_hien_thi, khach_hang_id, ngay_ban, thoi_gian_tao, trang_thai_giao, ghi_chu, giao_gap, tong_tien, tien_con_no, nguoi_tao) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)', 
                                        (ma_don_final, khach_id, today_str, ts, 'Chờ giao hàng', g_chu, is_gap, total_val, total_val, st.session_state.current_user))
                            cur.execute('SELECT last_insert_rowid()')
                            new_id = cur.fetchone()[0]
                            
                            for i in st.session_state.cart:
                                cur.execute('INSERT INTO chi_tiet_don_hang (don_hang_id, loai_than_id, so_luong, don_gia) VALUES (%s, %s, %s, %s)', (new_id, i['loai_than_id'], i['so_luong'], i['don_gia']))
                                cur.execute("UPDATE loai_than SET ton_kho = ton_kho - %s WHERE id = %s", (i['so_luong'], i['loai_than_id']))
                            conn.commit()
                        st.session_state.cart = []; st.session_state.last_order_id = new_id; st.rerun()

# ==========================================
# PHÂN HỆ 3: GIAO HÀNG & THU TIỀN TÀI XẾ
# ==========================================
elif menu == "Giao Hàng & Vận Tải":
    st.markdown("### 🚚 Bàn Giao Lộ Trình & Nghiệm Thu")
    with get_connection() as conn: df_staff = pd.read_sql_query("SELECT id, ten_nhan_vien FROM nhan_vien", conn)
    
    if df_staff.empty: st.warning("Vui lòng cấu hình danh sách tài xế trước.")
    else:
        tab1, tab2 = st.tabs(["📦 1. Xe Chờ Đi Giao", "🏁 2. Nghiệm Thu Giao Xong"])
        with tab1:
            with get_connection() as conn: df_cho = pd.read_sql_query("SELECT dh.id, dh.ma_don_hien_thi, dh.thoi_gian_tao, dh.giao_gap, kh.ten_khach, kh.dia_chi, kh.link_google_maps FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.trang_thai_giao = 'Chờ giao hàng'", conn)
            if df_cho.empty: st.success("Không có đơn chờ đi giao.")
            else:
                for _, r in df_cho.iterrows():
                    with get_connection() as conn: tong_kg = pd.read_sql_query(f"SELECT SUM(so_luong) FROM chi_tiet_don_hang WHERE don_hang_id = {r['id']}", conn).iloc[0,0]
                    map_html = f"<a href='{r['link_google_maps']}' target='_blank' class='map-btn'>📍 Mở Bản Đồ Đường Đi</a>" if r['link_google_maps'] else ""
                    
                    st.markdown(f"""
                        <div class='kpi-card' style='border-top-color:#f59e0b; padding:15px;'>
                            <b style='font-size:16px;'>📦 Mã Lệnh: {r['ma_don_hien_thi']}</b><br>
                            Khách: <b>{r['ten_khach']}</b> | Tải trọng: {tong_kg:,.0f} kg<br>
                            Địa chỉ: {r['dia_chi']}<br>
                            {map_html}
                        </div>
                    """, unsafe_allow_html=True)
                    
                    with st.form(f"giao_xe_{r['id']}"):
                        tx_id = st.selectbox("Chọn tài xế vận chuyển:", options=df_staff['id'].tolist(), format_func=lambda x: df_staff[df_staff['id']==x]['ten_nhan_vien'].values[0])
                        if st.form_submit_button("Lệnh Cho Xe Chạy"):
                            with get_connection() as conn: 
                                conn.cursor().execute("UPDATE don_hang SET trang_thai_giao='Đang giao', nhan_vien_id=%s WHERE id=%s", (tx_id, r['id']))
                                conn.commit()
                            st.rerun()

        with tab2:
            with get_connection() as conn: df_dang = pd.read_sql_query("SELECT dh.id, dh.ma_don_hien_thi, dh.giao_gap, dh.tong_tien, kh.ten_khach, nv.ten_nhan_vien FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id WHERE dh.trang_thai_giao = 'Đang giao'", conn)
            if df_dang.empty: st.info("Chưa có xe nào đang chạy.")
            else:
                for _, r in df_dang.iterrows():
                    with st.form(f"form_done_{r['id']}"):
                        st.markdown(f"<b>🚚 Mã #{r['ma_don_hien_thi']}</b> - Khách: {r['ten_khach']} | Tài xế: {r['ten_nhan_vien']} | Tổng đơn: <b style='color:red'>{r['tong_tien']:,.0f} đ</b>", unsafe_allow_html=True)
                        tien_tra_ngay = st.number_input("Tiền khách trả ngay (đ):", min_value=0.0, max_value=float(r['tong_tien']), value=float(r['tong_tien']), step=10000.0)
                        pt_tt = st.selectbox("Hình thức thanh toán:", ["Chuyển khoản", "Tiền mặt"])
                        
                        if st.form_submit_button("🏁 Xác Nhận Giao Hạ Tải Thành Công"):
                            tien_con_no_lai = r['tong_tien'] - tien_tra_ngay
                            is_paid = 1 if tien_con_no_lai <= 0 else 0
                            hinh_thuc_luu = pt_tt if tien_con_no_lai <= 0 else f"Trả trước 1 phần ({pt_tt}) - Nợ gối"
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            with get_connection() as conn:
                                cur = conn.cursor()
                                cur.execute("UPDATE don_hang SET trang_thai_giao='Đã hoàn thành', da_thanh_toan=%s, hinh_thuc_thanh_toan=%s, tien_da_tra=%s, tien_con_no=%s WHERE id=%s", (is_paid, hinh_thuc_luu, tien_tra_ngay, tien_con_no_lai, r['id']))
                                if tien_tra_ngay > 0: 
                                    cur.execute("INSERT INTO lich_su_thanh_toan (don_hang_id, so_tien_tra, hinh_thuc, ngay_tra, ghi_chu, nguoi_tao) VALUES (%s,%s,%s,%s,%s,%s)", (r['id'], tien_tra_ngay, pt_tt, ts, "Thu đợt 1 tại bãi", st.session_state.current_user))
                                conn.commit()
                            st.success("Đã nghiệm thu giao hàng!"); st.rerun()

# ==========================================
# PHÂN HỆ QUẢN LÝ THU TIỀN & NỢ SỔ
# ==========================================
elif menu == "Sổ Quản Lý Nợ":
    st.markdown("### 💰 Quản Lý Dòng Tiền & Công Nợ")
    with get_connection() as conn:
        df_no = pd.read_sql_query('''
            SELECT dh.id, dh.ma_don_hien_thi as "Mã Đơn", dh.ngay_ban as "Ngày Mua", kh.ten_khach as "Khách Hàng",
                   dh.tong_tien as "Tổng Tiền", dh.tien_da_tra as "Đã Trả", dh.tien_con_no as "CÒN NỢ"
            FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.tien_con_no > 0 AND dh.trang_thai_giao = 'Đã hoàn thành'
        ''', conn)
        
    if df_no.empty: st.success("🎉 Cực kỳ tuyệt vời! Công ty không còn dư nợ tồn đọng.")
    else:
        st.dataframe(df_no.drop(columns=['id']).style.format({'Tổng Tiền':'{:,.0f}', 'Đã Trả':'{:,.0f}', 'CÒN NỢ':'{:,.0f}'}), use_container_width=True, hide_index=True)
        st.markdown(f"<div style='background:#fef2f2; padding:15px; border-radius:8px; border-left:5px solid #ef4444;'><h4 style='color:#b91c1c; margin:0;'>TỔNG DƯ NỢ ĐANG KẸT: {df_no['CÒN NỢ'].sum():,.0f} VNĐ</h4></div>", unsafe_allow_html=True)
        
        st.markdown("---")
        st.markdown("#### 💸 Gạch Nợ Khách Trả Thêm")
        with st.form("f_thu_no"):
            id_don_no = st.selectbox("Chọn hóa đơn cần gạch nợ:", options=df_no['id'].tolist(), format_func=lambda x: f"{df_no[df_no['id']==x]['Mã Đơn'].values[0]} - {df_no[df_no['id']==x]['Khách Hàng'].values[0]} (Nợ: {df_no[df_no['id']==x]['CÒN NỢ'].values[0]:,.0f}đ)")
            info_no = df_no[df_no['id'] == id_don_no].iloc[0]
            
            tien_thu_them = st.number_input("Số tiền thu được đợt này (đ):", min_value=1.0, max_value=float(info_no['CÒN NỢ']), value=float(info_no['CÒN NỢ']), step=10000.0)
            ht_thu = st.selectbox("Hình thức:", ["Chuyển khoản", "Tiền mặt"])
            
            if st.form_submit_button("Xác Nhận Khấu Trừ Nợ"):
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                no_moi = info_no['CÒN NỢ'] - tien_thu_them
                tra_moi = info_no['Đã Trả'] + tien_thu_them
                is_full = 1 if no_moi <= 0 else 0
                ht_luu = ht_thu if is_full == 1 else "Trả góp"
                
                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("UPDATE don_hang SET tien_con_no=%s, tien_da_tra=%s, da_thanh_toan=%s, hinh_thuc_thanh_toan=%s WHERE id=%s", (no_moi, tra_moi, is_full, ht_luu, id_don_no))
                    cur.execute("INSERT INTO lich_su_thanh_toan (don_hang_id, so_tien_tra, hinh_thuc, ngay_tra, ghi_chu, nguoi_tao) VALUES (%s,%s,%s,%s,%s,%s)", (id_don_no, tien_thu_them, ht_thu, ts, "Thu tiền nợ", st.session_state.current_user))
                    conn.commit()
                st.success("✔️ Đã gạch sổ nợ thành công!"); st.rerun()

# ==========================================
# PHÂN HỆ SỔ LỊCH SỬ
# ==========================================
elif menu == "Lịch Sử Đơn Hàng":
    st.markdown("### 🗂️ Tra Cứu Lịch Sử Đơn Hàng Đã Giao")
    with get_connection() as conn:
        df_his = pd.read_sql_query('''
            SELECT dh.id, dh.ma_don_hien_thi as "Mã Đơn", dh.thoi_gian_tao as "Thời Gian", kh.ten_khach as "Khách Hàng", nv.ten_nhan_vien as "Tài Xế", 
                   dh.tong_tien as "Tổng Tiền (đ)", dh.tien_con_no as "Nợ Lại (đ)", dh.nguoi_tao as "Người Lên Đơn"
            FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id
            WHERE dh.trang_thai_giao = 'Đã hoàn thành' ORDER BY dh.id DESC
        ''', conn)
    if not df_his.empty:
        st.dataframe(df_his.style.format({'Tổng Tiền (đ)': '{:,.0f}', 'Nợ Lại (đ)': '{:,.0f}'}), use_container_width=True, hide_index=True)

# ==========================================
# PHÂN HỆ 5: QUẢN LÝ CẤU HÌNH HỆ THỐNG GỘP
# ==========================================
elif menu == "Cài Đặt Hệ Thống":
    st.markdown("### ⚙️ Cài Đặt Danh Mục Cơ Sở Dữ Liệu")
    
    tabs_list = ["1. Danh Mục Loại Than", "2. Quản Lý Khách Hàng", "3. Quản Lý Tài Xế", "4. Phân Quyền Giá Riêng", "5. Cấu Hình In Bill"]
    if st.session_state.user_role == 'admin':
        tabs_list.append("6. Quản Lý Tài Khoản Người Dùng (Admin)")
        
    tab_sys = st.selectbox("Chọn danh mục cần cấu hình:", tabs_list)
    
    if tab_sys == "1. Danh Mục Loại Than":
        with get_connection() as conn: df_t = pd.read_sql_query("SELECT id, ten_than, gia_nhap_mac_dinh, gia_mac_dinh, ton_kho, nguoi_tao as \"Người Nhập\" FROM loai_than", conn)
        t_sub1, t_sub2, t_sub4 = st.tabs(["➕ Thêm Mã Mới", "🔧 Sửa Mã", "🚢 Nhập Hàng Mới"])
        
        with t_sub1:
            with st.form("f_c_add"):
                n = st.text_input("Tên loại than:"); pn = st.number_input("Giá nhập gốc (đ/kg):", value=1500); p = st.number_input("Giá bán lẻ (đ/kg):", value=3000); s = st.number_input("Tồn kho (kg):", value=0.0)
                if st.form_submit_button("Thêm Loại Than"):
                    with get_connection() as conn: 
                        conn.cursor().execute("INSERT INTO loai_than(ten_than,gia_nhap_mac_dinh,gia_mac_dinh,ton_kho,nguoi_tao) VALUES(%s,%s,%s,%s,%s)",(n.strip(),pn,p,s,st.session_state.current_user))
                        conn.commit()
                    st.rerun()
        with t_sub2:
            if not df_t.empty:
                id_e = st.selectbox("Chọn mã than:", options=df_t['id'].tolist(), format_func=lambda x: df_t[df_t['id']==x]['ten_than'].values[0])
                info = df_t[df_t['id']==id_e].iloc[0]
                with st.form("f_c_edit"):
                    en = st.text_input("Tên mới:", value=info['ten_than']); epn = st.number_input("Giá nhập gốc (đ/kg):", value=float(info['gia_nhap_mac_dinh'])); ep = st.number_input("Giá bán mới (đ/kg):", value=float(info['gia_mac_dinh'])); es = st.number_input("Hiệu chỉnh Tồn Kho (kg):", value=float(info['ton_kho']))
                    if st.form_submit_button("Cập Nhật"):
                        with get_connection() as conn: 
                            conn.cursor().execute("UPDATE loai_than SET ten_than=%s, gia_nhap_mac_dinh=%s, gia_mac_dinh=%s, ton_kho=%s WHERE id=%s",(en.strip(),epn,ep,es,id_e))
                            conn.commit()
                        st.rerun()
        with t_sub4:
            if not df_t.empty:
                id_n = st.selectbox("Chọn than cần nhập kho:", options=df_t['id'].tolist(), format_func=lambda x: df_t[df_t['id']==x]['ten_than'].values[0])
                with st.form("f_c_in"):
                    w_in = st.number_input("Số kg nhập kho:", min_value=1.0, value=5000.0); p_in = st.number_input("Giá nhập chuyến này (đ/kg):", value=1500)
                    if st.form_submit_button("Cộng Kho"):
                        with get_connection() as conn: 
                            conn.cursor().execute("UPDATE loai_than SET ton_kho=ton_kho+%s WHERE id=%s",(w_in,id_n))
                            conn.cursor().execute("INSERT INTO nhap_hang(loai_than_id, ngay_nhap, so_luong, don_gia_nhap, nguoi_tao) VALUES(%s,%s,%s,%s,%s)", (id_n, today_str, w_in, p_in, st.session_state.current_user))
                            conn.commit()
                        st.rerun()
        st.dataframe(df_t.drop(columns=['id']), use_container_width=True, hide_index=True)

    elif tab_sys == "2. Quản Lý Khách Hàng":
        with get_connection() as conn: df_k = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps, nguoi_tao as \"Người Tạo\" FROM khach_hang", conn)
        k_sub1, k_sub2 = st.tabs(["➕ Thêm Khách Hàng", "🔧 Sửa Hồ Sơ"])
        with k_sub1:
            with st.form("f_k_add"):
                kn = st.text_input("Tên đối tác:"); kp = st.text_input("SĐT:"); kd = st.text_input("Địa chỉ:"); kkv = st.text_input("Khu vực (VD: Thái Nguyên, Bắc Giang...):"); kmap = st.text_input("Link Google Maps (Copy từ web):")
                if st.form_submit_button("Lưu Đăng Ký"):
                    with get_connection() as conn:
                        cur = conn.cursor()
                        cur.execute("INSERT INTO khach_hang (ten_khach,sdt,dia_chi,khu_vuc,link_google_maps,nguoi_tao) VALUES(%s,%s,%s,%s,%s,%s)",(kn.strip(),kp,kd,kkv.strip(),kmap,st.session_state.current_user))
                        cur.execute('SELECT last_insert_rowid()')
                        nid = cur.fetchone()[0]
                        cur.execute("UPDATE khach_hang SET ma_khach_hang = %s WHERE id = %s", (f"KH{nid:04d}", nid))
                        conn.commit()
                    st.rerun()
        with k_sub2:
            if not df_k.empty:
                id_ke = st.selectbox("Chọn hồ sơ khách:", options=df_k['id'].tolist(), format_func=lambda x: df_k[df_k['id']==x]['ten_khach'].values[0])
                k_info = df_k[df_k['id'] == id_ke].iloc[0]
                with st.form("f_k_edit"):
                    ekn = st.text_input("Tên:", value=k_info['ten_khach']); ekp = st.text_input("SĐT:", value=k_info['sdt']); ekd = st.text_input("Địa chỉ:", value=k_info['dia_chi']); ekk = st.text_input("Khu vực:", value=k_info['khu_vuc']); emap = st.text_input("Link Google Maps:", value=k_info['link_google_maps'] if k_info['link_google_maps'] else "")
                    if st.form_submit_button("Cập Nhật"):
                        with get_connection() as conn: 
                            conn.cursor().execute("UPDATE khach_hang SET ten_khach=%s, sdt=%s, dia_chi=%s, khu_vuc=%s, link_google_maps=%s WHERE id=%s",(ekn.strip(),ekp,ekd,ekk.strip(),emap,id_ke))
                            conn.commit()
                        st.rerun()
        st.dataframe(df_k.drop(columns=['id']), use_container_width=True, hide_index=True)
        
    elif tab_sys == "3. Quản Lý Tài Xế":
        with get_connection() as conn: df_nv = pd.read_sql_query("SELECT id, ten_nhan_vien, sdt FROM nhan_vien", conn)
        v_sub1, v_sub2, v_sub3 = st.tabs(["➕ Thêm Tài Xế Mới", "🔧 Sửa Thông Tin", "❌ Xóa Bỏ Tài Xế"])
        
        with v_sub1:
            with st.form("f_v_add"):
                nv_n = st.text_input("Họ và tên tài xế:")
                nv_p = st.text_input("Số điện thoại:")
                if st.form_submit_button("Lưu Đăng Ký"):
                    if nv_n.strip():
                        try:
                            with get_connection() as conn: 
                                conn.cursor().execute("INSERT INTO nhan_vien(ten_nhan_vien,sdt) VALUES(%s,%s)",(nv_n.strip(),nv_p))
                                conn.commit()
                            st.success("Đăng ký tài xế thành công!"); st.rerun()
                        except: st.error("Họ tên tài xế này đã tồn tại trong hệ thống!")
        with v_sub2:
            if not df_nv.empty:
                id_ve = st.selectbox("Chọn tài xế cần sửa thông tin:", options=df_nv['id'].tolist(), format_func=lambda x: df_nv[df_nv['id']==x]['ten_nhan_vien'].values[0])
                v_info = df_nv[df_nv['id'] == id_ve].iloc[0]
                with st.form("f_v_edit"):
                    evn = st.text_input("Họ và tên mới:", value=v_info['ten_nhan_vien'])
                    evp = st.text_input("Số điện thoại mới:", value=v_info['sdt'])
                    if st.form_submit_button("Cập Nhật Thông Tin"):
                        with get_connection() as conn: 
                            conn.cursor().execute("UPDATE nhan_vien SET ten_nhan_vien=%s, sdt=%s WHERE id=%s",(evn.strip(), evp, id_ve))
                            conn.commit()
                        st.success("Đã cập nhật thông tin tài xế thành công!"); st.rerun()
        with v_sub3:
            if not df_nv.empty:
                id_vd = st.selectbox("Chọn tài xế muốn gỡ bỏ:", options=df_nv['id'].tolist(), format_func=lambda x: df_nv[df_nv['id']==x]['ten_nhan_vien'].values[0])
                if st.button("❌ Xác Nhận Xóa Tài Xế Này", type="primary"):
                    with get_connection() as conn: 
                        conn.cursor().execute("DELETE FROM nhan_vien WHERE id=%s",(id_vd,))
                        conn.commit()
                    st.success("Đã gỡ bỏ tài xế khỏi danh sách điều phối!"); st.rerun()
                    
        st.markdown("---")
        st.markdown("#### 📋 Danh Sách Tài Xế Giao Hàng Nội Bộ")
        st.dataframe(df_nv.drop(columns=['id']).rename(columns={'ten_nhan_vien':'Họ Tên Tài Xế', 'sdt':'Số Điện Thoại Liên Hệ'}), use_container_width=True, hide_index=True)

    elif tab_sys == "4. Phân Quyền Giá Riêng":
        with get_connection() as conn:
            df_k = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach FROM khach_hang", conn)
            df_t = pd.read_sql_query("SELECT id, ten_than FROM loai_than", conn)
            
        t_pr1, t_price2 = st.tabs(["⚙️ Cài Đặt Giá Ưu Đãi Độc Quyền", "📜 Sổ Lịch Sử Đổi Giá"])
        with t_pr1:
            if not df_k.empty and not df_t.empty:
                with st.form("form_set_gr"):
                    id_k = st.selectbox("Đối tác khách hàng:", options=df_k['id'].tolist(), format_func=lambda x: f"[{df_k[df_k['id']==x]['ma_khach_hang'].values[0]}] {df_k[df_k['id']==x]['ten_khach'].values[0]}")
                    id_t = st.selectbox("Chủng loại than:", options=df_t['id'].tolist(), format_func=lambda x: df_t[df_t['id']==x]['ten_than'].values[0])
                    
                    with get_connection() as cnn: 
                        cur = cnn.cursor()
                        cur.execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=%s AND loai_than_id=%s", (id_k, id_t))
                        old_p_res = cur.fetchone()
                    old_p = old_p_res[0] if old_p_res else 0
                    
                    if old_p > 0: st.info(f"💡 Giá đang áp dụng cho đối tác này: **{old_p:,.0f} đ/kg**")
                    g_new = st.number_input("Thiết lập đơn giá MỚI (đ/kg):", value=float(old_p) if old_p > 0 else 2500.0, step=10.0)
                    
                    if st.form_submit_button("Lưu Chính Sách Giá"):
                        ts_change = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        with get_connection() as conn:
                            cur = conn.cursor()
                            if old_p != g_new: cur.execute("INSERT INTO lich_su_gia (khach_hang_id, loai_than_id, gia_cu, gia_moi, ngay_thay_doi) VALUES (%s,%s,%s,%s,%s)", (id_k, id_t, old_p, g_new, ts_change))
                            cur.execute("INSERT INTO gia_rieng (khach_hang_id, loai_than_id, gia_uu_dai) VALUES (%s,%s,%s) ON CONFLICT (khach_hang_id, loai_than_id) DO UPDATE SET gia_uu_dai = EXCLUDED.gia_uu_dai", (id_k, id_t, g_new))
                            conn.commit()
                        st.success("✔️ Đã đồng bộ cấu hình giá riêng!"); st.rerun()
            with get_connection() as conn: df_pq = pd.read_sql_query('SELECT kh.ten_khach as "Khách Hàng", lt.ten_than as "Chủng Loại Than", gr.gia_uu_dai as "Mức Giá Đang Áp Dụng (đ/kg)" FROM gia_rieng gr JOIN khach_hang kh ON gr.khach_hang_id = kh.id JOIN loai_than lt ON gr.loai_than_id = lt.id', conn)
            st.dataframe(df_pq.style.format({'Mức Giá Đang Áp Dụng (đ/kg)': '{:,.0f}'}), use_container_width=True, hide_index=True)
            
        with t_price2:
            with get_connection() as conn: df_ls_gia = pd.read_sql_query("SELECT ls.ngay_thay_doi as \"Thời Điểm\", kh.ten_khach as \"Khách Hàng\", lt.ten_than as \"Loại Than\", ls.gia_cu as \"Giá Cũ (đ)\", ls.gia_moi as \"Giá Mới (đ)\" FROM lich_su_gia ls JOIN khach_hang kh ON ls.khach_hang_id = kh.id JOIN loai_than lt ON ls.loai_than_id = lt.id ORDER BY ls.id DESC", conn)
            if df_ls_gia.empty: st.caption("Hệ thống chưa ghi nhận lịch sử đổi giá nào.")
            else: st.dataframe(df_ls_gia.style.format({'Giá Cũ (đ)': '{:,.0f}', 'Giá Mới (đ)': '{:,.0f}'}), use_container_width=True, hide_index=True)

    elif tab_sys == "5. Cấu Hình In Bill":
        with get_connection() as conn: config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn).iloc[0]
        with st.form("form_print_setting"):
            st.markdown("**📝 Thiết Lập Cấu Hình Thương Hiệu In Ấn**")
            ten_ch = st.text_input("Tên Cửa Hàng / Công Ty (Hiện to trên Bill):", value=config['ten_cua_hang'])
            sdt_ch = st.text_input("Số hotline liên hệ:", value=config['so_dien_thoai'])
            stk_ch = st.text_input("Thông tin số tài khoản kế toán:", value=config['thong_tin_ngan_hang'])
            kho_giay = st.selectbox("Khổ máy in mặc định:", ["A4 (Tiêu chuẩn văn phòng)", "A5 (Khổ ngang bằng một nửa A4)", "Khổ K80mm (Máy in bill siêu thị nhiệt)"], index=["A4 (Tiêu chuẩn văn phòng)", "A5 (Khổ ngang bằng một nửa A4)", "Khổ K80mm (Máy in bill siêu thị nhiệt)"].index(config['kho_giay_mac_dinh']))
            
            if st.form_submit_button("Lưu Cấu Hình Hóa Đơn"):
                with get_connection() as conn: 
                    conn.cursor().execute("UPDATE cau_hinh_in SET ten_cua_hang=%s, so_dien_thoai=%s, thong_tin_ngan_hang=%s, kho_giay_mac_dinh=%s WHERE id=1", (ten_ch, sdt_ch, stk_ch, kho_giay))
                    conn.commit()
                st.success("Đã đồng bộ thông số thương hiệu in ấn!"); st.rerun()

    elif tab_sys == "6. Quản Lý Tài Khoản Người Dùng (Admin)":
        st.info("Khu vực Quản Trị Viên (Admin) - Phê duyệt yêu cầu tài khoản mới")
        with get_connection() as conn:
            df_users = pd.read_sql_query("SELECT id, username, role, status FROM users WHERE username != 'admin'", conn)
            
        if not df_users.empty:
            pending = df_users[df_users['status'] == 'Chờ duyệt']
            approved = df_users[df_users['status'] == 'Đã duyệt']
            
            t_u1, t_u2 = st.tabs(["🟡 Chờ Phê Duyệt", "🟢 Tài Khoản Đang Hoạt Động"])
            with t_u1:
                if pending.empty: st.success("Không có yêu cầu đăng ký mới nào.")
                else:
                    for _, r in pending.iterrows():
                        col1, col2, col3 = st.columns([3, 1, 1])
                        col1.write(f"Tài khoản: **{r['username']}** - Trạng thái: {r['status']}")
                        if col2.button("✅ Duyệt", key=f"app_{r['id']}"):
                            with get_connection() as conn: 
                                conn.cursor().execute("UPDATE users SET status='Đã duyệt' WHERE id=%s", (r['id'],))
                                conn.commit()
                            st.rerun()
                        if col3.button("❌ Hủy", key=f"rej_{r['id']}"):
                            with get_connection() as conn: 
                                conn.cursor().execute("DELETE FROM users WHERE id=%s", (r['id'],))
                                conn.commit()
                            st.rerun()
            with t_u2:
                if approved.empty: st.caption("Chưa có user nào được cấp quyền.")
                else:
                    for _, r in approved.iterrows():
                        col1, col2 = st.columns([4, 1])
                        col1.write(f"Tài khoản: **{r['username']}** - Quyền: {r['role']}")
                        if col2.button("🗑️ Xóa", key=f"del_u_{r['id']}"):
                            with get_connection() as conn: 
                                conn.cursor().execute("DELETE FROM users WHERE id=%s", (r['id'],))
                                conn.commit()
                            st.rerun()
        else: st.info("Hệ thống chỉ có 1 tài khoản Admin duy nhất.")
