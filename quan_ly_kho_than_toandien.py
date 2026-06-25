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
import io

# ==========================================
# 1. TỰ ĐỘNG NHẬN DIỆN THIẾT BỊ & TỐI ƯU GIAO DIỆN
# ==========================================
st.set_page_config(
    page_title="ERP Quản Lý Kho Than V6.3 - Hoàn Hảo", 
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
        .invoice-box { background: #ffffff; padding: 30px; border: 1px solid #e2e8f0; border-radius: 8px; margin: 10px auto; color: #1e293b; }
        .size-a4 { max-width: 800px; }
        .size-a5 { max-width: 600px; font-size: 13px; }
        .size-80mm { max-width: 320px; font-size: 11px; padding: 10px; }
    </style>
""", unsafe_allow_html=True)

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

# ==========================================
# 2. BẢO MẬT & ĐỒNG BỘ GOOGLE SHEETS
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
            # Khắc phục lỗi ID rỗng từ GSheets
            if 'id' in df.columns: df['id'] = pd.to_numeric(df['id'], errors='coerce')
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
    class CursorWrapper:
        def __init__(self, cursor): self.cursor = cursor
        def execute(self, query, params=None):
            query = query.replace('%s', '?')
            if params: return self.cursor.execute(query, params)
            return self.cursor.execute(query)
        def fetchone(self): return self.cursor.fetchone()
        def fetchall(self): return self.cursor.fetchall()
        def __getattr__(self, name): return getattr(self.cursor, name)
    class ConnectionWrapper:
        def __init__(self, connection): self.connection = connection
        def commit(self):
            self.connection.commit()
            sync_thread = threading.Thread(target=background_sync_task)
            sync_thread.daemon = True; sync_thread.start()
        def cursor(self): return CursorWrapper(self.connection.cursor())
        def close(self): self.connection.close()
        def __getattr__(self, name): return getattr(self.connection, name)
    try: yield ConnectionWrapper(conn)
    finally: conn.close()

# CƠ CHẾ ĐẶC TRỊ ÉP ID THỦ CÔNG - CHỐNG LỖI MAT INVOICE & DUPLICATE
def get_next_id(table_name, cursor):
    try:
        cursor.execute(f"SELECT MAX(id) FROM {table_name}")
        val = cursor.fetchone()[0]
        return 1 if pd.isna(val) or val is None else int(val) + 1
    except: return 1

def init_database():
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, username VARCHAR(255) UNIQUE, password VARCHAR(255), role VARCHAR(50), status VARCHAR(50))''')
        cursor.execute("SELECT * FROM users WHERE username='admin'")
        if not cursor.fetchone(): 
            uid = get_next_id('users', cursor)
            cursor.execute("INSERT INTO users (id, username, password, role, status) VALUES (?, ?, ?, 'admin', 'Đã duyệt')", (uid, 'admin', hash_password(st.secrets["admin_pass"])))
        cursor.execute('''CREATE TABLE IF NOT EXISTS loai_than (id INTEGER PRIMARY KEY, ten_than VARCHAR(255) UNIQUE, gia_nhap_mac_dinh DOUBLE PRECISION, gia_mac_dinh DOUBLE PRECISION, ton_kho DOUBLE PRECISION, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS khach_hang (id INTEGER PRIMARY KEY, ma_khach_hang VARCHAR(50) UNIQUE, ten_khach VARCHAR(255) UNIQUE, sdt VARCHAR(50), dia_chi TEXT, khu_vuc VARCHAR(255), link_google_maps TEXT, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS nhan_vien (id INTEGER PRIMARY KEY, ten_nhan_vien VARCHAR(255) UNIQUE, sdt VARCHAR(50), chuc_vu VARCHAR(100))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS gia_rieng (khach_hang_id INTEGER, loai_than_id INTEGER, gia_uu_dai DOUBLE PRECISION, PRIMARY KEY (khach_hang_id, loai_than_id))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_gia (id INTEGER PRIMARY KEY, khach_hang_id INTEGER, loai_than_id INTEGER, gia_cu DOUBLE PRECISION, gia_moi DOUBLE PRECISION, ngay_thay_doi TIMESTAMP)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS don_hang (id INTEGER PRIMARY KEY, ma_don_hien_thi VARCHAR(50) UNIQUE, khach_hang_id INTEGER, nhan_vien_id INTEGER, ngay_ban DATE, thoi_gian_tao TIMESTAMP, da_thanh_toan INTEGER, trang_thai_giao VARCHAR(100), hinh_thuc_thanh_toan VARCHAR(100), ghi_chu TEXT, giao_gap INTEGER, tong_tien DOUBLE PRECISION, tien_da_tra DOUBLE PRECISION, tien_con_no DOUBLE PRECISION, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS chi_tiet_don_hang (id INTEGER PRIMARY KEY, don_hang_id INTEGER, loai_than_id INTEGER, so_luong DOUBLE PRECISION, don_gia DOUBLE PRECISION)''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS nhap_hang (id INTEGER PRIMARY KEY, loai_than_id INTEGER, ngay_nhap DATE, so_luong DOUBLE PRECISION, don_gia_nhap DOUBLE PRECISION, nguoi_tao VARCHAR(255), xuong_nhap VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS lich_su_thanh_toan (id INTEGER PRIMARY KEY, don_hang_id INTEGER, so_tien_tra DOUBLE PRECISION, hinh_thuc VARCHAR(100), ngay_tra TIMESTAMP, ghi_chu TEXT, nguoi_tao VARCHAR(255))''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS cau_hinh_in (id INTEGER PRIMARY KEY, ten_cua_hang VARCHAR(255), so_dien_thoai VARCHAR(50), thong_tin_ngan_hang TEXT, kho_giay_mac_dinh VARCHAR(100))''')
        
        cursor.execute("PRAGMA table_info(nhap_hang)")
        if 'xuong_nhap' not in [col[1] for col in cursor.fetchall()]: cursor.execute("ALTER TABLE nhap_hang ADD COLUMN xuong_nhap VARCHAR(255)")
            
        cursor.execute("INSERT OR IGNORE INTO cau_hinh_in (id, thong_tin_ngan_hang) VALUES (1, 'Chưa cài đặt')")
        cursor.execute("SELECT id FROM khach_hang WHERE ma_khach_hang IS NULL")
        for c in cursor.fetchall(): cursor.execute("UPDATE khach_hang SET ma_khach_hang = ? WHERE id = ?", (f"KH{int(c[0]):04d}", int(c[0])))
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

now_dt = datetime.utcnow() + timedelta(hours=7)
today_str = now_dt.strftime('%Y-%m-%d')

if not st.session_state.logged_in:
    st.markdown("<div class='main-header'><h1 style='text-align:center;'>HỆ THỐNG QUẢN TRỊ KHO THAN CLOUD</h1></div>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1,2,1])
    with c2:
        tab_login, tab_reg = st.tabs(["🔐 Đăng Nhập", "📝 Đăng Ký Tài Khoản"])
        with tab_login:
            with st.form("login_form"):
                user = st.text_input("Tài khoản:"); pwd = st.text_input("Mật khẩu:", type="password")
                if st.form_submit_button("Đăng Nhập Nhận Ca", type="primary"):
                    with get_connection() as conn:
                        res = conn.cursor(); res.execute("SELECT role, status FROM users WHERE username=? AND password=?", (user, hash_password(pwd)))
                        data = res.fetchone()
                        if data:
                            if data[1] == "Đã duyệt" or data[0] == "admin":
                                st.session_state.logged_in = True; st.session_state.current_user = user; st.session_state.user_role = data[0]; st.rerun()
                            else: st.error("Tài khoản đang chờ Admin duyệt.")
                        else: st.error("Sai tài khoản hoặc mật khẩu!")
        with tab_reg:
            with st.form("reg_form"):
                n_user = st.text_input("Tài khoản muốn tạo:"); n_pwd = st.text_input("Mật khẩu:", type="password"); n_pwd2 = st.text_input("Nhập lại mật khẩu:", type="password")
                if st.form_submit_button("Gửi Yêu Cầu Đăng Ký"):
                    if n_pwd != n_pwd2: st.error("Mật khẩu không khớp!")
                    elif len(n_user) < 3: st.error("Tài khoản phải từ 3 ký tự!")
                    else:
                        try:
                            with get_connection() as conn:
                                uid = get_next_id('users', conn.cursor())
                                conn.cursor().execute("INSERT INTO users (id, username, password, role, status) VALUES (?, ?, ?, 'user', 'Chờ duyệt')", (uid, n_user, hash_password(n_pwd)))
                                conn.commit()
                            st.success("Đã gửi yêu cầu đăng ký!")
                        except: st.error("Tài khoản này đã tồn tại!")
    st.stop()

def sinh_ma_don_hang_theo_ngay(ngay_str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM don_hang WHERE ngay_ban = ?", (ngay_str,))
        return f"DH{datetime.strptime(ngay_str, '%Y-%m-%d').strftime('%d%m%y')}-{(cursor.fetchone()[0] + 1):03d}"

with st.sidebar:
    st.image("https://cdn-icons-png.flaticon.com/512/2850/2850785.png", width=80)
    st.markdown("<h2 style='color: #0f172a; margin-top:0;'>Kho Than ERP</h2>", unsafe_allow_html=True)
    st.markdown(f"👤 Chào, **{st.session_state.current_user}**")
    if st.button("🚪 Đăng Xuất"): st.session_state.clear(); st.rerun()
    st.markdown("---")
    menu = option_menu("CHỨC NĂNG CỐT LÕI", ["Thống Kê (HQ)", "Lập Đơn & In Phiếu", "Giao Hàng & Vận Tải", "Sổ Quản Lý Nợ", "Lịch Sử Đơn Hàng", "Cài Đặt Hệ Thống"], icons=['bar-chart-fill', 'receipt-cutoff', 'truck', 'wallet-fill', 'clock-history', 'gear-fill'], menu_icon="boxes", default_index=0)

# ==========================================
# PHÂN HỆ 1: THỐNG KÊ (HQ DASHBOARD)
# ==========================================
if menu == "Thống Kê (HQ)":
    st.markdown("<div class='main-header'><h1 style='margin:0; font-size:24px; text-align:center;'>📊 PHÂN HỆ GIÁM SÁT KINH DOANH TỔNG THỂ</h1></div>", unsafe_allow_html=True)
    time_filter = st.radio("⏳ Mốc thời gian:", ["Hôm nay", "Tuần này", "Tháng này", "Tất cả thời gian"], horizontal=True)

    with get_connection() as conn:
        df_flat = pd.read_sql_query('''SELECT dh.id as don_id, dh.thoi_gian_tao, dh.da_thanh_toan, dh.trang_thai_giao, dh.ngay_ban, kh.ten_khach, kh.khu_vuc, lt.ten_than, lt.gia_nhap_mac_dinh, ctdh.so_luong, ctdh.don_gia, (ctdh.so_luong * ctdh.don_gia) as thanh_tien FROM don_hang dh JOIN chi_tiet_don_hang ctdh ON dh.id = ctdh.don_hang_id JOIN khach_hang kh ON dh.khach_hang_id = kh.id JOIN loai_than lt ON ctdh.loai_than_id = lt.id''', conn.connection)
        df_group = pd.read_sql_query('''SELECT dh.id as don_id, dh.ma_don_hien_thi, dh.thoi_gian_tao, dh.trang_thai_giao, dh.giao_gap, dh.tong_tien, dh.tien_con_no, dh.nguoi_tao, kh.ma_khach_hang, kh.ten_khach, nv.ten_nhan_vien FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id ORDER BY dh.id DESC''', conn.connection)
        df_kho_status = pd.read_sql_query("SELECT ten_than, ton_kho FROM loai_than", conn.connection)

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

    total_rev = df_group['tong_tien'].sum() if not df_group.empty else 0
    debt_df = df_group[df_group['trang_thai_giao'] == 'Đã hoàn thành'] if not df_group.empty else pd.DataFrame()
    debt_rev = debt_df['tien_con_no'].sum() if not debt_df.empty else 0
    pending_df = df_group[df_group['trang_thai_giao'] != 'Đã hoàn thành'] if not df_group.empty else pd.DataFrame()
    pending_count = pending_df['don_id'].nunique() if not pending_df.empty else 0
    total_orders = df_group['don_id'].nunique() if not df_group.empty else 0
    total_profit = df_flat['loi_nhuan'].sum() if not df_flat.empty else 0
    
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.markdown(f"<div class='kpi-card'><div class='kpi-label'>📦 Tổng Đơn Cần Giao</div><div class='kpi-value'>{total_orders} <span style='font-size:14px;color:#64748b;'>({pending_count} chờ)</span></div></div>", unsafe_allow_html=True)
    with c2: st.markdown(f"<div class='kpi-card border-green'><div class='kpi-label'>💵 Doanh Thu Tạm Tính</div><div class='kpi-value text-green'>{total_rev:,.0f} đ</div></div>", unsafe_allow_html=True)
    with c3: st.markdown(f"<div class='kpi-card border-purple'><div class='kpi-label'>📈 Lợi Nhuận Gộp</div><div class='kpi-value text-purple'>{total_profit:,.0f} đ</div></div>", unsafe_allow_html=True)
    with c4: st.markdown(f"<div class='kpi-card border-red'><div class='kpi-label'>🛑 Nợ Thực Tế</div><div class='kpi-value text-red'>{debt_rev:,.0f} đ</div></div>", unsafe_allow_html=True)

    if not df_kho_status.empty:
        for _, r in df_kho_status[df_kho_status['ton_kho'] < 500].iterrows():
            st.markdown(f"<div class='delay-alert' style='border-left-color:#f59e0b; background-color:#fffbeb; color:#b45309;'>⚠️ <b>SẮP HẾT HÀNG</b>: Mã <b>{r['ten_than']}</b> chỉ còn <b>{r['ton_kho']:,.0f} kg</b>. Cần nhập bãi!</div>", unsafe_allow_html=True)

    if not df_group.empty:
        for _, r in df_group[df_group['trang_thai_giao'] != 'Đã hoàn thành'].iterrows():
            hours_elapsed = (now_dt - pd.to_datetime(r['thoi_gian_tao'])).total_seconds() / 3600
            if hours_elapsed > 4: st.markdown(f"<div class='delay-alert'>🚨 <b>ĐƠN TRỄ QUÁ 4 GIỜ (Mã {r['ma_don_hien_thi']})</b><br>• Tài xế: {r['ten_nhan_vien'] or 'Chưa phân xe'} | Đối tác: {r['ten_khach']} | Người lên đơn: <b>{r['nguoi_tao']}</b> | Chờ: {hours_elapsed:.1f} giờ</div>", unsafe_allow_html=True)

# ==========================================
# PHÂN HỆ 2: LẬP ĐƠN & IN PHIẾU
# ==========================================
elif menu == "Lập Đơn & In Phiếu":
    st.markdown("### 📋 Lập Lệnh Xuất Kho / Bán Hàng")
    with get_connection() as conn: print_config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn.connection).iloc[0]
        
    if st.session_state.last_order_id:
        with get_connection() as conn:
            df_master = pd.read_sql_query(f"SELECT * FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.id = {int(st.session_state.last_order_id)}", conn.connection)
            details = pd.read_sql_query(f"SELECT ctdh.*, lt.ten_than FROM chi_tiet_don_hang ctdh JOIN loai_than lt ON ctdh.loai_than_id = lt.id WHERE ctdh.don_hang_id = {int(st.session_state.last_order_id)}", conn.connection)
            
        if df_master.empty:
            st.error("Không tìm thấy dữ liệu hóa đơn. Vui lòng lập lại đơn mới.")
            if st.button("Quay lại"): st.session_state.last_order_id = None; st.rerun()
        else:
            master = df_master.iloc[0]
            css_class = "max-width: 800px;" if print_config['kho_giay_mac_dinh'] == "A4 (Tiêu chuẩn văn phòng)" else "max-width: 600px; font-size: 13px;" if print_config['kho_giay_mac_dinh'] == "A5 (Khổ ngang bằng một nửa A4)" else "max-width: 320px; font-size: 11px;"
            html_rows = ""; txt_rows = ""; total_val = 0
            for _, r in details.iterrows():
                thanh_tien = r['so_luong'] * r['don_gia']; total_val += thanh_tien
                html_rows += f"<tr><td style='padding: 8px; border-bottom: 1px solid #e2e8f0;'>{r['ten_than']}</td><td style='padding: 8px; border-bottom: 1px solid #e2e8f0; text-align:center;'>{r['so_luong']:,.0f}</td><td style='padding: 8px; border-bottom: 1px solid #e2e8f0; text-align:right;'>{r['don_gia']:,.0f}</td><td style='padding: 8px; border-bottom: 1px solid #e2e8f0; text-align:right; font-weight:bold;'>{thanh_tien:,.0f}</td></tr>"
                txt_rows += f"- {r['ten_than']}: {r['so_luong']:,.0f} kg x {r['don_gia']:,.0f} đ = {thanh_tien:,.0f} đ\n"
                
            full_html_print = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>Phiếu Xuất - {master["ma_don_hien_thi"]}</title><style>body {{ font-family: sans-serif; color: #1e293b; margin: 20px; }} .invoice-box {{ {css_class}; margin: 0 auto; padding: 20px; border: 1px solid #ccc; }} table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }} th {{ background: #f1f5f9; text-align: left; padding: 10px; border-bottom: 2px solid #cbd5e1; }} @media print {{ .invoice-box {{ border: none; padding: 0; }} }}</style></head><body onload="window.print()"><div class="invoice-box"><h2 style="text-align:center; margin-bottom: 5px; color:#0f172a;">{print_config["ten_cua_hang"]}</h2><p style="text-align:center; margin-top:0; font-size:13px;">SĐT: <b>{print_config["so_dien_thoai"]}</b></p><hr style="border:0; border-top:1px dashed #cbd5e1; margin: 15px 0;"><h3 style="text-align:center; margin-top:10px; margin-bottom:5px;">PHIẾU XUẤT KHO</h3><p style="text-align:center; color:#64748b; margin-top:0; font-size:12px;">Mã Đơn: <b>{master["ma_don_hien_thi"]}</b> | Ngày: {master["thoi_gian_tao"]}</p><p style="margin-bottom:5px; margin-top:15px;"><b>Khách Hàng:</b> {master["ten_khach"]} ({master["sdt"]})</p><p style="margin-top:0; margin-bottom:15px;"><b>Địa chỉ:</b> {master["dia_chi"]} <br><b>Ghi chú:</b> {master["ghi_chu"]}</p><table><thead><tr><th>Chủng Loại</th><th style="text-align:center;">SL (kg)</th><th style="text-align:right;">Đơn Giá</th><th style="text-align:right;">Thành Tiền</th></tr></thead><tbody>{html_rows}<tr><td colspan="3" style="text-align:right; font-weight:bold; padding-top:15px; border:none;">TỔNG CỘNG:</td><td style="text-align:right; font-weight:bold; padding-top:15px; font-size:16px; border:none;">{total_val:,.0f} đ</td></tr></tbody></table><p style="margin-top:20px; font-size:13px; padding:12px; border:1px dashed #ccc;"><b>THANH TOÁN:</b><br>{print_config["thong_tin_ngan_hang"]}</p><table style="width:100%; margin-top:30px; text-align:center; font-weight:bold; font-size:13px; border:none;"><tr><td style="border:none;">Người Lập Phiếu<br><br><br><b>{master["nguoi_tao"]}</b></td><td style="border:none;">Người Nhận<br><br><br><br></td></tr></table></div></body></html>"""
            text_bill = f"HÓA ĐƠN GIAO HÀNG - {print_config['ten_cua_hang']}\nMã: {master['ma_don_hien_thi']} | Ngày: {master['thoi_gian_tao']}\nKhách: {master['ten_khach']}\nĐịa chỉ: {master['dia_chi']}\n-------------------------\n{txt_rows}-------------------------\nTỔNG CỘNG: {total_val:,.0f} VNĐ\nCK/TT: {print_config['thong_tin_ngan_hang']}\nCảm ơn quý khách!"

            st.success("Tạo đơn hàng thành công!")
            b64 = base64.b64encode(full_html_print.encode('utf-8')).decode()
            st.markdown(f'<a href="data:text/html;base64,{b64}" target="_blank" style="display: block; text-align: center; background-color: #3b82f6; color: white; padding: 12px 24px; border-radius: 8px; font-weight: bold; text-decoration: none; margin-top: 15px; font-size: 16px;">🖨️ XEM & IN HÓA ĐƠN (Để xuất PDF: Bấm In -> Chọn "Lưu thành PDF")</a>', unsafe_allow_html=True)
            st.markdown("<br>**Hoặc bôi đen nội dung dưới đây để COPY gửi qua Zalo:**", unsafe_allow_html=True)
            st.code(text_bill, language="text")
            if st.button("🔄 LẬP ĐƠN MỚI TIẾP THEO"): st.session_state.last_order_id = None; st.rerun()
    else:
        with get_connection() as conn:
            df_khach = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach FROM khach_hang", conn.connection)
            df_than = pd.read_sql_query("SELECT id, ten_than, gia_mac_dinh, ton_kho FROM loai_than", conn.connection)

        if df_khach.empty or df_than.empty: st.warning("Vui lòng cấu hình Khách hàng và Loại than trước.")
        else:
            khach_dict = dict(zip(df_khach['id'], "[" + df_khach['ma_khach_hang'] + "] " + df_khach['ten_khach']))
            khach_id = st.selectbox("👤 Chọn Khách Hàng:", options=list(khach_dict.keys()), format_func=lambda x: khach_dict.get(x, "Lỗi"))
            st.markdown("---")
            
            with get_connection() as conn: df_pb = pd.read_sql_query(f"SELECT loai_than_id FROM gia_rieng WHERE khach_hang_id = {int(khach_id)}", conn.connection)
            than_options = df_than[df_than['id'].isin(df_pb['loai_than_id'].tolist())] if not df_pb.empty else df_than
            if than_options.empty: than_options = df_than
            
            than_dict = dict(zip(than_options['id'], than_options['ten_than']))
            t_id = st.selectbox("🪨 Chọn loại than:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x, "Lỗi"))
            
            with get_connection() as conn: 
                cur = conn.cursor()
                cur.execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=? AND loai_than_id=?", (int(khach_id), int(t_id)))
                gr_res = cur.fetchone()
            
            default_price = df_than[df_than['id']==t_id]['gia_mac_dinh'].values
            gia_goi_y = gr_res[0] if gr_res else (default_price[0] if len(default_price) > 0 else 0)
            ton_kho_hien_tai = df_than[df_than['id']==t_id]['ton_kho'].values[0] if len(default_price) > 0 else 0
            st.caption(f"Trữ lượng bãi thực tế: **{ton_kho_hien_tai:,.0f} kg**")
            
            col_sl, col_dg = st.columns(2)
            with col_sl: sl = st.number_input("Khối lượng (kg):", min_value=1.0, value=1000.0, step=500.0)
            with col_dg: dg = st.number_input("Đơn giá bán (đ/kg):", value=float(gia_goi_y), step=10.0)
            
            if st.button("➕ Thêm vào phiếu"):
                if any(i['loai_than_id'] == t_id for i in st.session_state.cart): st.error("Mã này đã có trong giỏ!")
                else: st.session_state.cart.append({'loai_than_id': int(t_id), 'ten_than': than_dict.get(t_id), 'so_luong': sl, 'don_gia': dg, 'thanh_tien': sl * dg}); st.rerun()

            if st.session_state.cart:
                df_c = pd.DataFrame(st.session_state.cart)
                st.dataframe(df_c[['ten_than', 'so_luong', 'don_gia', 'thanh_tien']].style.format({'so_luong': '{:,.0f}', 'don_gia': '{:,.0f}', 'thanh_tien': '{:,.0f}'}), use_container_width=True, hide_index=True)
                total_val = df_c['thanh_tien'].sum()
                st.markdown(f"### 💰 Tổng Hóa Đơn: <span style='color:#dc2626'>{total_val:,.0f} đ</span>", unsafe_allow_html=True)
                
                if st.button("🗑️ Xóa giỏ hàng"): st.session_state.cart = []; st.rerun()
                st.markdown("---")
                giao_gap = st.checkbox("🔥 ĐƠN HÀNG GIAO GẤP")
                g_chu = st.text_input("Ghi chú biển số xe/tài xế:")
                
                if st.button("🚀 CHỐT LỆNH XUẤT", type="primary"):
                    stock_ok = True
                    for i in st.session_state.cart:
                        if i['so_luong'] > df_than[df_than['id']==i['loai_than_id']]['ton_kho'].values[0]: stock_ok = False; st.error(f"❌ Mã {i['ten_than']} vượt tồn kho!")
                    if stock_ok:
                        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S'); ma_don_final = sinh_ma_don_hang_theo_ngay(today_str); is_gap = 1 if giao_gap else 0
                        with get_connection() as conn:
                            cur = conn.cursor()
                            new_id = get_next_id('don_hang', cur)
                            cur.execute('INSERT INTO don_hang (id, ma_don_hien_thi, khach_hang_id, ngay_ban, thoi_gian_tao, trang_thai_giao, ghi_chu, giao_gap, tong_tien, tien_con_no, nguoi_tao) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (new_id, ma_don_final, int(khach_id), today_str, ts, 'Chờ giao hàng', g_chu, is_gap, total_val, total_val, st.session_state.current_user))
                            for i in st.session_state.cart:
                                ct_id = get_next_id('chi_tiet_don_hang', cur)
                                cur.execute('INSERT INTO chi_tiet_don_hang (id, don_hang_id, loai_than_id, so_luong, don_gia) VALUES (?, ?, ?, ?, ?)', (ct_id, new_id, int(i['loai_than_id']), i['so_luong'], i['don_gia']))
                                cur.execute("UPDATE loai_than SET ton_kho = ton_kho - ? WHERE id = ?", (i['so_luong'], int(i['loai_than_id'])))
                            conn.commit()
                        st.session_state.cart = []; st.session_state.last_order_id = new_id; st.rerun()

# ==========================================
# PHÂN HỆ 3: GIAO HÀNG & SỔ NỢ (ĐÃ VÁ LỖI DUPLICATE FORM)
# ==========================================
elif menu == "Giao Hàng & Vận Tải":
    st.markdown("### 🚚 Bàn Giao Lộ Trình & Nghiệm Thu")
    with get_connection() as conn: df_staff = pd.read_sql_query("SELECT id, ten_nhan_vien FROM nhan_vien", conn.connection)
    if df_staff.empty: st.warning("Vui lòng cấu hình danh sách tài xế trước.")
    else:
        tab1, tab2 = st.tabs(["📦 Xe Chờ Đi Giao", "🏁 Nghiệm Thu Giao Xong"])
        with tab1:
            with get_connection() as conn: df_cho = pd.read_sql_query("SELECT dh.id, dh.ma_don_hien_thi, kh.ten_khach, kh.dia_chi, kh.link_google_maps FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.trang_thai_giao = 'Chờ giao hàng'", conn.connection)
            if df_cho.empty: st.success("Không có đơn chờ đi giao.")
            else:
                for idx, r in df_cho.iterrows():
                    with st.form(f"giao_xe_form_{idx}_{r['id']}"):
                        st.write(f"📦 Đơn **{r['ma_don_hien_thi']}** - Khách: **{r['ten_khach']}**")
                        if r['link_google_maps']: st.markdown(f"[📍 Mở Bản Đồ Đường Đi]({r['link_google_maps']})")
                        
                        tx_dict = dict(zip(df_staff['id'], df_staff['ten_nhan_vien']))
                        tx_id = st.selectbox("Tài xế:", options=list(tx_dict.keys()), format_func=lambda x: tx_dict.get(x))
                        
                        if st.form_submit_button("Lệnh Cho Xe Chạy"):
                            with get_connection() as c_update: 
                                c_update.cursor().execute("UPDATE don_hang SET trang_thai_giao='Đang giao', nhan_vien_id=? WHERE id=?", (int(tx_id), int(r['id'])))
                                c_update.commit()
                            st.success("Đã phân xe!")
                            st.rerun()
        with tab2:
            with get_connection() as conn: df_dang = pd.read_sql_query("SELECT dh.id, dh.ma_don_hien_thi, dh.tong_tien, kh.ten_khach, nv.ten_nhan_vien FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id WHERE dh.trang_thai_giao = 'Đang giao'", conn.connection)
            if df_dang.empty: st.info("Chưa có xe nào đang chạy.")
            else:
                for idx, r in df_dang.iterrows():
                    with st.form(f"form_done_gh_{idx}_{r['id']}"):
                        st.write(f"🚚 Đơn **{r['ma_don_hien_thi']}** - Khách: {r['ten_khach']} | Tổng: **{r['tong_tien']:,.0f} đ**")
                        tien_tra_ngay = st.number_input("Khách trả ngay (đ):", min_value=0.0, max_value=float(r['tong_tien']), value=float(r['tong_tien']), step=10000.0)
                        pt_tt = st.selectbox("Hình thức thanh toán:", ["Chuyển khoản", "Tiền mặt"])
                        if st.form_submit_button("Xác Nhận Giao Thành Công"):
                            tien_con_no_lai = r['tong_tien'] - tien_tra_ngay; is_paid = 1 if tien_con_no_lai <= 0 else 0
                            ht_luu = pt_tt if is_paid else f"Trả trước 1 phần ({pt_tt}) - Nợ gối"
                            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            with get_connection() as c_update:
                                cur = c_update.cursor()
                                cur.execute("UPDATE don_hang SET trang_thai_giao='Đã hoàn thành', da_thanh_toan=?, hinh_thuc_thanh_toan=?, tien_da_tra=?, tien_con_no=? WHERE id=?", (is_paid, ht_luu, tien_tra_ngay, tien_con_no_lai, int(r['id'])))
                                if tien_tra_ngay > 0: 
                                    lsid = get_next_id('lich_su_thanh_toan', cur)
                                    cur.execute("INSERT INTO lich_su_thanh_toan (id, don_hang_id, so_tien_tra, hinh_thuc, ngay_tra, ghi_chu, nguoi_tao) VALUES (?,?,?,?,?,?,?)", (lsid, int(r['id']), tien_tra_ngay, pt_tt, ts, "Thu tại bãi", st.session_state.current_user))
                                c_update.commit()
                            st.success("Hoàn thành!")
                            st.rerun()

elif menu == "Sổ Quản Lý Nợ":
    st.markdown("### 💰 Quản Lý Dòng Tiền & Công Nợ")
    with get_connection() as conn: df_no = pd.read_sql_query('''SELECT dh.id, dh.ma_don_hien_thi as "Mã Đơn", dh.ngay_ban as "Ngày", kh.ten_khach as "Khách Hàng", dh.tong_tien as "Tổng Tiền", dh.tien_da_tra as "Đã Trả", dh.tien_con_no as "CÒN NỢ" FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id WHERE dh.tien_con_no > 0 AND dh.trang_thai_giao = 'Đã hoàn thành' ''', conn.connection)
    if df_no.empty: st.success("Công ty không còn dư nợ tồn đọng.")
    else:
        st.dataframe(df_no.drop(columns=['id']).style.format({'Tổng Tiền':'{:,.0f}', 'Đã Trả':'{:,.0f}', 'CÒN NỢ':'{:,.0f}'}), use_container_width=True, hide_index=True)
        st.markdown(f"<h4 style='color:#b91c1c;'>TỔNG DƯ NỢ: {df_no['CÒN NỢ'].sum():,.0f} VNĐ</h4>", unsafe_allow_html=True)
        with st.form("f_thu_no"):
            no_dict = dict(zip(df_no['id'], df_no['Mã Đơn'] + " - " + df_no['Khách Hàng']))
            id_don_no = st.selectbox("Gạch nợ đơn:", options=list(no_dict.keys()), format_func=lambda x: no_dict.get(x))
            info_no = df_no[df_no['id'] == id_don_no].iloc[0]
            tien_thu = st.number_input("Số tiền thu (đ):", min_value=1.0, max_value=float(info_no['CÒN NỢ']), value=float(info_no['CÒN NỢ']))
            ht_thu = st.selectbox("Hình thức:", ["Chuyển khoản", "Tiền mặt"])
            if st.form_submit_button("Xác Nhận Khấu Trừ Nợ"):
                ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                with get_connection() as c_update:
                    cur = c_update.cursor()
                    cur.execute("UPDATE don_hang SET tien_con_no=tien_con_no-?, tien_da_tra=tien_da_tra+?, da_thanh_toan=CASE WHEN tien_con_no-? <= 0 THEN 1 ELSE 0 END WHERE id=?", (tien_thu, tien_thu, tien_thu, int(id_don_no)))
                    lsid = get_next_id('lich_su_thanh_toan', cur)
                    cur.execute("INSERT INTO lich_su_thanh_toan (id, don_hang_id, so_tien_tra, hinh_thuc, ngay_tra, ghi_chu, nguoi_tao) VALUES (?,?,?,?,?,?,?)", (lsid, int(id_don_no), tien_thu, ht_thu, ts, "Thu nợ", st.session_state.current_user))
                    c_update.commit()
                st.success("Đã gạch nợ!"); st.rerun()

elif menu == "Lịch Sử Đơn Hàng":
    st.markdown("### 🗂️ Tra Cứu Lịch Sử Giao Hàng")
    with get_connection() as conn: df_his = pd.read_sql_query('''SELECT dh.ma_don_hien_thi as "Mã Đơn", dh.thoi_gian_tao as "Ngày Giờ", kh.ten_khach as "Khách Hàng", nv.ten_nhan_vien as "Tài Xế", dh.tong_tien as "Tổng Tiền", dh.tien_con_no as "Nợ Lại", dh.nguoi_tao as "Người Lập" FROM don_hang dh JOIN khach_hang kh ON dh.khach_hang_id = kh.id LEFT JOIN nhan_vien nv ON dh.nhan_vien_id = nv.id WHERE dh.trang_thai_giao = 'Đã hoàn thành' ORDER BY dh.id DESC''', conn.connection)
    if not df_his.empty:
        st.dataframe(df_his.style.format({'Tổng Tiền': '{:,.0f}', 'Nợ Lại': '{:,.0f}'}), use_container_width=True, hide_index=True)
        st.download_button("📥 XUẤT BÁO CÁO EXCEL", data=df_his.to_csv(index=False, encoding='utf-8-sig'), file_name=f"Lich_Su_Giao_{today_str}.csv", mime="text/csv")

# ==========================================
# PHÂN HỆ 5: QUẢN LÝ CẤU HÌNH
# ==========================================
elif menu == "Cài Đặt Hệ Thống":
    st.markdown("### ⚙️ Cài Đặt Danh Mục Cơ Sở Dữ Liệu")
    tabs_list = ["1. Danh Mục Loại Than", "2. Quản Lý Khách Hàng", "3. Quản Lý Tài Xế", "4. Phân Quyền Giá Riêng", "5. Cấu Hình In Bill"]
    if st.session_state.user_role == 'admin': tabs_list.append("6. Quản Lý Tài Khoản (Admin)")
    tab_sys = st.selectbox("Chọn danh mục cần cấu hình:", tabs_list)
    
    if tab_sys == "1. Danh Mục Loại Than":
        with get_connection() as conn: 
            df_t = pd.read_sql_query("SELECT id, ten_than FROM loai_than", conn.connection)
            df_nhap = pd.read_sql_query('''SELECT nh.ngay_nhap as "Ngày", lt.ten_than as "Loại Than", nh.xuong_nhap as "Xưởng", nh.so_luong as "SL (kg)", nh.don_gia_nhap as "Giá Nhập", nh.nguoi_tao as "Người Nhập" FROM nhap_hang nh JOIN loai_than lt ON nh.loai_than_id = lt.id ORDER BY nh.id DESC''', conn.connection)
            
        t_sub1, t_sub2, t_sub3 = st.tabs(["➕ Thêm Loại Than", "🔧 Sửa Tên/Giá", "🚢 Nhập Hàng & Lịch Sử"])
        with t_sub1:
            with st.form("f_c_add"):
                n = st.text_input("Tên than mới:"); pn = st.number_input("Giá nhập gốc:", value=1500); p = st.number_input("Giá bán:", value=3000)
                if st.form_submit_button("Thêm"):
                    try:
                        with get_connection() as conn: 
                            tid = get_next_id('loai_than', conn.cursor())
                            conn.cursor().execute("INSERT INTO loai_than(id, ten_than, gia_nhap_mac_dinh, gia_mac_dinh, ton_kho, nguoi_tao) VALUES(?,?,?,?,?,?)", (tid, n.strip(), pn, p, 0.0, st.session_state.current_user)); conn.commit()
                        st.success("Thêm thành công!"); st.rerun()
                    except: st.error("Tên loại than này đã tồn tại!")
        with t_sub2:
            if not df_t.empty:
                than_dict = dict(zip(df_t['id'], df_t['ten_than']))
                id_e = st.selectbox("Chọn mã than:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x))
                with get_connection() as conn: info = pd.read_sql_query(f"SELECT * FROM loai_than WHERE id={int(id_e)}", conn.connection).iloc[0]
                with st.form("f_c_edit"):
                    en = st.text_input("Tên mới:", value=info['ten_than']); ep = st.number_input("Giá bán mới:", value=float(info['gia_mac_dinh']))
                    if st.form_submit_button("Cập Nhật"):
                        try:
                            with get_connection() as conn: 
                                conn.cursor().execute("UPDATE loai_than SET ten_than=?, gia_mac_dinh=? WHERE id=?", (en.strip(), ep, int(id_e))); conn.commit()
                            st.success("Cập nhật thành công!"); st.rerun()
                        except: st.error("Tên loại than này đã tồn tại!")
        with t_sub3:
            st.subheader("📦 Nhập hàng vào kho")
            with st.form("f_c_in"):
                if not df_t.empty:
                    id_n = st.selectbox("Chọn loại than:", options=list(than_dict.keys()), format_func=lambda x: than_dict.get(x))
                    xuong = st.text_input("Nguồn nhập / Xưởng:")
                    w_in = st.number_input("SL Nhập (kg):", min_value=1.0, value=1000.0)
                    p_in = st.number_input("Giá Nhập (đ):", value=1500)
                    if st.form_submit_button("Xác nhận nhập kho"):
                        with get_connection() as conn: 
                            nid = get_next_id('nhap_hang', conn.cursor())
                            conn.cursor().execute('''INSERT INTO nhap_hang(id, loai_than_id, ngay_nhap, xuong_nhap, so_luong, don_gia_nhap, nguoi_tao) VALUES(?,?,?,?,?,?,?)''', (nid, int(id_n), today_str, xuong, w_in, p_in, st.session_state.current_user))
                            conn.cursor().execute("UPDATE loai_than SET ton_kho=ton_kho+? WHERE id=?", (w_in, int(id_n))); conn.commit()
                        st.success("Nhập kho thành công!"); st.rerun()
            st.dataframe(df_nhap, use_container_width=True, hide_index=True)

    elif tab_sys == "2. Quản Lý Khách Hàng":
        with get_connection() as conn: df_k = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps FROM khach_hang", conn.connection)
        k_sub1, k_sub2 = st.tabs(["➕ Thêm", "🔧 Sửa"])
        with k_sub1:
            with st.form("f_k_add"):
                kn = st.text_input("Tên KH:"); kp = st.text_input("SĐT:"); kd = st.text_input("Địa chỉ:"); kkv = st.text_input("Khu vực:"); kmap = st.text_input("Maps:")
                if st.form_submit_button("Thêm KH"):
                    try:
                        with get_connection() as conn:
                            cur = conn.cursor()
                            nid = get_next_id('khach_hang', cur)
                            cur.execute("INSERT INTO khach_hang (id, ma_khach_hang, ten_khach, sdt, dia_chi, khu_vuc, link_google_maps, nguoi_tao) VALUES(?,?,?,?,?,?,?,?)", (nid, f"KH{nid:04d}", kn.strip(), kp, kd, kkv.strip(), kmap, st.session_state.current_user))
                            conn.commit()
                        st.success("Thêm thành công!"); st.rerun()
                    except sqlite3.IntegrityError: st.error("Tên khách hàng này đã tồn tại!")
        with k_sub2:
            if not df_k.empty:
                kd_dict = dict(zip(df_k['id'], df_k['ten_khach']))
                id_ke = st.selectbox("Hồ sơ KH:", options=list(kd_dict.keys()), format_func=lambda x: kd_dict.get(x))
                k_info = df_k[df_k['id'] == id_ke].iloc[0]
                with st.form("f_k_edit"):
                    ekn = st.text_input("Tên:", value=k_info['ten_khach']); ekp = st.text_input("SĐT:", value=k_info['sdt']); ekd = st.text_input("Địa chỉ:", value=k_info['dia_chi']); ekk = st.text_input("Khu vực:", value=k_info['khu_vuc']); emap = st.text_input("Maps:", value=k_info['link_google_maps'] or "")
                    if st.form_submit_button("Cập Nhật"):
                        try:
                            with get_connection() as conn: 
                                conn.cursor().execute("UPDATE khach_hang SET ten_khach=?, sdt=?, dia_chi=?, khu_vuc=?, link_google_maps=? WHERE id=?",(ekn.strip(),ekp,ekd,ekk.strip(),emap,int(id_ke))); conn.commit()
                            st.success("Cập nhật thành công!"); st.rerun()
                        except sqlite3.IntegrityError: st.error("Tên khách hàng này đã tồn tại!")

    elif tab_sys == "3. Quản Lý Tài Xế":
        with get_connection() as conn: df_nv = pd.read_sql_query("SELECT id, ten_nhan_vien, sdt FROM nhan_vien", conn.connection)
        v_sub1, v_sub2 = st.tabs(["➕ Thêm", "🔧 Sửa / Xóa"])
        with v_sub1:
            with st.form("f_v_add"):
                nv_n = st.text_input("Tên TX:"); nv_p = st.text_input("SĐT:")
                if st.form_submit_button("Thêm TX"):
                    try:
                        with get_connection() as conn: 
                            tid = get_next_id('nhan_vien', conn.cursor())
                            conn.cursor().execute("INSERT INTO nhan_vien(id, ten_nhan_vien, sdt) VALUES(?,?,?)", (tid, nv_n.strip(), nv_p)); conn.commit()
                        st.success("Thêm thành công!"); st.rerun()
                    except sqlite3.IntegrityError: st.error("Tên tài xế này đã tồn tại!")
        with v_sub2:
            if not df_nv.empty:
                nv_dict = dict(zip(df_nv['id'], df_nv['ten_nhan_vien']))
                id_ve = st.selectbox("Tài xế:", options=list(nv_dict.keys()), format_func=lambda x: nv_dict.get(x))
                if st.button("❌ Xóa tài xế này"):
                    with get_connection() as conn: 
                        conn.cursor().execute("DELETE FROM nhan_vien WHERE id=?",(int(id_ve),)); conn.commit()
                    st.rerun()

    elif tab_sys == "4. Phân Quyền Giá Riêng":
        with get_connection() as conn:
            df_k = pd.read_sql_query("SELECT id, ma_khach_hang, ten_khach FROM khach_hang", conn.connection)
            df_t = pd.read_sql_query("SELECT id, ten_than FROM loai_than", conn.connection)
            
        t_pr1, t_price2 = st.tabs(["⚙️ Cài Đặt Giá", "📜 Lịch Sử Đổi Giá"])
        with t_pr1:
            if not df_k.empty and not df_t.empty:
                with st.form("form_set_gr"):
                    k_dict = dict(zip(df_k['id'], df_k['ma_khach_hang'] + " - " + df_k['ten_khach']))
                    t_dict = dict(zip(df_t['id'], df_t['ten_than']))
                    
                    id_k = st.selectbox("Khách hàng:", options=list(k_dict.keys()), format_func=lambda x: k_dict.get(x, "Lỗi"))
                    id_t = st.selectbox("Chủng loại than:", options=list(t_dict.keys()), format_func=lambda x: t_dict.get(x, "Lỗi"))
                    
                    with get_connection() as cnn: 
                        cur = cnn.cursor()
                        cur.execute("SELECT gia_uu_dai FROM gia_rieng WHERE khach_hang_id=? AND loai_than_id=?", (int(id_k), int(id_t)))
                        old_p_res = cur.fetchone()
                    old_p = old_p_res[0] if old_p_res else 0
                    
                    st.write(f"Giá đang áp dụng: **{old_p:,.0f} đ/kg**" if old_p > 0 else "Chưa cài giá riêng (Đang dùng giá mặc định)")
                    g_new = st.number_input("Giá MỚI (đ/kg):", value=float(old_p) if old_p > 0 else 2500.0, step=10.0)
                    
                    if st.form_submit_button("Lưu Cài Đặt"):
                        ts_change = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        with get_connection() as conn:
                            cur = conn.cursor()
                            if old_p != g_new: 
                                lsgid = get_next_id('lich_su_gia', cur)
                                cur.execute("INSERT INTO lich_su_gia (id, khach_hang_id, loai_than_id, gia_cu, gia_moi, ngay_thay_doi) VALUES (?,?,?,?,?,?)", (lsgid, int(id_k), int(id_t), old_p, g_new, ts_change))
                            cur.execute("INSERT INTO gia_rieng (khach_hang_id, loai_than_id, gia_uu_dai) VALUES (?,?,?) ON CONFLICT (khach_hang_id, loai_than_id) DO UPDATE SET gia_uu_dai = EXCLUDED.gia_uu_dai", (int(id_k), int(id_t), g_new))
                            conn.commit()
                        st.success("Đã lưu!"); st.rerun()

    elif tab_sys == "5. Cấu Hình In Bill":
        with get_connection() as conn: config = pd.read_sql_query("SELECT * FROM cau_hinh_in WHERE id = 1", conn.connection).iloc[0]
        with st.form("form_print_setting"):
            ten_ch = st.text_input("Tên Cửa Hàng (In to):", value=config['ten_cua_hang'])
            sdt_ch = st.text_input("Hotline:", value=config['so_dien_thoai'])
            stk_ch = st.text_input("TK Ngân hàng:", value=config['thong_tin_ngan_hang'])
            kho_giay = st.selectbox("Khổ máy in:", ["A4 (Tiêu chuẩn văn phòng)", "A5 (Khổ ngang bằng một nửa A4)", "Khổ K80mm (Máy in bill siêu thị nhiệt)"], index=["A4 (Tiêu chuẩn văn phòng)", "A5 (Khổ ngang bằng một nửa A4)", "Khổ K80mm (Máy in bill siêu thị nhiệt)"].index(config['kho_giay_mac_dinh']))
            if st.form_submit_button("Lưu Cấu Hình"):
                with get_connection() as conn: 
                    conn.cursor().execute("UPDATE cau_hinh_in SET ten_cua_hang=?, so_dien_thoai=?, thong_tin_ngan_hang=?, kho_giay_mac_dinh=? WHERE id=1", (ten_ch, sdt_ch, stk_ch, kho_giay)); conn.commit()
                st.success("Đã cập nhật!"); st.rerun()

    elif tab_sys == "6. Quản Lý Tài Khoản (Admin)":
        with get_connection() as conn: df_users = pd.read_sql_query("SELECT id, username, role, status FROM users WHERE username != 'admin'", conn.connection)
        if not df_users.empty:
            pending = df_users[df_users['status'] == 'Chờ duyệt']
            t_u1, t_u2 = st.tabs(["🟡 Chờ Duyệt", "🟢 Đã Duyệt"])
            with t_u1:
                for _, r in pending.iterrows():
                    c1, c2, c3 = st.columns([3, 1, 1])
                    c1.write(f"Tài khoản: **{r['username']}**")
                    if c2.button("✅ Duyệt", key=f"app_{r['id']}"):
                        with get_connection() as conn: conn.cursor().execute("UPDATE users SET status='Đã duyệt' WHERE id=?", (int(r['id']),)); conn.commit()
                        st.rerun()
                    if c3.button("❌ Xóa", key=f"rej_{r['id']}"):
                        with get_connection() as conn: conn.cursor().execute("DELETE FROM users WHERE id=?", (int(r['id']),)); conn.commit()
                        st.rerun()
            with t_u2:
                for _, r in df_users[df_users['status'] == 'Đã duyệt'].iterrows():
                    c1, c2 = st.columns([4, 1])
                    c1.write(f"Tài khoản: **{r['username']}**")
                    if c2.button("🗑️ Xóa", key=f"del_u_{r['id']}"):
                        with get_connection() as conn: conn.cursor().execute("DELETE FROM users WHERE id=?", (int(r['id']),)); conn.commit()
                        st.rerun()
