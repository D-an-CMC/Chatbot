# app.py
# Giao dien Streamlit cho Chatbot Tra Cuu Diem (Khoa hoc tu nhien / Vat ly)

import logging
import streamlit as st
from pathlib import Path
from typing import Optional

from config import (
    APP_TITLE, APP_ICON, GRADES_DIR, USE_SUPABASE, USE_AUTH, USE_ROLE_SELECT,
    LOG_FORMAT, LOG_LEVEL,
)
from src.engine.chatbot import ChatbotEngine
from src.llm.response_builder import format_for_display, strip_think_tags

logging.basicConfig(level=LOG_LEVEL, format=LOG_FORMAT)
logger = logging.getLogger(__name__)

_ROLE_LABELS = {
    "Admin": "Quản trị viên",
    "GiaoVien": "Giáo viên",
    "HocSinh-PhuHuynh": "Học sinh / Phụ huynh",
}
# Nhan hien thi -> role_name trong DB, dung cho bo chon vai tro demo
_ROLE_PICKER = {
    "Quản trị viên": "Admin",
    "Giáo viên": "GiaoVien",
    "Học sinh": "HocSinh-PhuHuynh",
}

# ---------------------------------------------------------------------------
# Page config & Custom CSS
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title=APP_TITLE,
    page_icon=APP_ICON,
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

.stApp { font-family: 'Inter', sans-serif; }

.agent-header {
    background: linear-gradient(135deg, #0f3460 0%, #16213e 50%, #1a1a2e 100%);
    padding: 1.5rem 2rem;
    border-radius: 16px;
    margin-bottom: 1.5rem;
    color: white;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
.agent-header h1 { font-size: 1.8rem; font-weight: 700; margin: 0; color: white; }
.agent-header p { color: #94a3b8; margin: 0.3rem 0 0 0; font-size: 0.95rem; }

.status-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    margin-right: 6px; animation: pulse 2s ease-in-out infinite;
}
.status-dot.green { background: #22c55e; }
.status-dot.red { background: #ef4444; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }

[data-testid="stSidebar"] { background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%); }
[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
[data-testid="stSidebar"] .stMetric label { color: #94a3b8 !important; }
</style>
""", unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Load engine / auth
# ---------------------------------------------------------------------------

@st.cache_resource(show_spinner="🔄 Đang nạp dữ liệu điểm...")
def get_engine() -> ChatbotEngine:
    engine = ChatbotEngine()
    engine.initialize()
    return engine


def get_session_user():
    return st.session_state.get("session_user")


# ---------------------------------------------------------------------------
# Login (chi khi USE_AUTH bat — co du SUPABASE_ANON_KEY)
# ---------------------------------------------------------------------------

def render_login(engine: ChatbotEngine) -> None:
    st.markdown(f"""
    <div class="agent-header">
        <h1>{APP_ICON} {APP_TITLE}</h1>
        <p>Vui lòng đăng nhập để tiếp tục.</p>
    </div>
    """, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.2, 1])
    with col:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Mật khẩu", type="password")
            submitted = st.form_submit_button("Đăng nhập", use_container_width=True, type="primary")

        if submitted:
            try:
                user = engine.auth.sign_in(email, password)
                st.session_state["session_user"] = user
                st.session_state["messages"] = []
                st.rerun()
            except ValueError as e:
                st.error(str(e))
            except Exception as e:
                st.error(f"Lỗi không xác định khi đăng nhập: {e}")


# ---------------------------------------------------------------------------
# Bo chon vai tro (demo — thay cho dang nhap, AUTH_MODE=select)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def _student_picker_options(_engine: ChatbotEngine):
    """(label hien thi, student_code) cho selectbox chon hoc sinh."""
    opts = _engine.list_students_for_picker()
    return [(f"{s['full_name']} ({s['student_code']})", s["student_code"]) for s in opts]


def render_role_selector(engine: ChatbotEngine) -> None:
    st.divider()
    st.markdown("##### 🧪 Chế độ demo — chọn vai trò")
    st.caption("Chưa ghép đăng nhập; chọn vai trò để thử phân quyền.")

    role_label = st.radio(
        "Vai trò", list(_ROLE_PICKER.keys()), key="role_pick", label_visibility="collapsed",
    )
    role_name = _ROLE_PICKER[role_label]

    student_code = None
    if role_name == "HocSinh-PhuHuynh":
        options = _student_picker_options(engine)
        if options:
            labels = [o[0] for o in options]
            chosen = st.selectbox("Chọn học sinh", labels, key="student_pick")
            student_code = dict(options).get(chosen)
        else:
            st.warning("Không có danh sách học sinh để chọn.")

    # Tao/cap nhat SessionUser khi lua chon thay doi
    signature = (role_name, student_code)
    if st.session_state.get("_role_signature") != signature:
        st.session_state["session_user"] = engine.make_demo_user(role_name, student_code)
        st.session_state["_role_signature"] = signature
        st.session_state["messages"] = []


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar(engine: ChatbotEngine, session_user) -> None:
    with st.sidebar:
        st.markdown(f"### {APP_ICON} {APP_TITLE}")
        st.caption("Điểm số • Xếp loại • Thống kê lớp • Giáo viên • Thông tin học sinh • Danh sách lớp • Thời khóa biểu • Điểm danh • Lịch thi • Thông báo • Hoạt động")

        if USE_ROLE_SELECT:
            render_role_selector(engine)
        elif session_user is not None:
            st.divider()
            role_label = _ROLE_LABELS.get(session_user.role_name, session_user.role_name)
            st.markdown(f"##### 👤 {session_user.full_name}")
            st.caption(f"Vai trò: {role_label}")
            if st.button("🚪 Đăng xuất", use_container_width=True):
                del st.session_state["session_user"]
                st.session_state["messages"] = []
                st.rerun()

        st.divider()

        st.markdown("##### ⚙️ Trạng thái dữ liệu")
        source_label = "Supabase (database)" if USE_SUPABASE else "File Excel"
        st.caption(f"Nguồn dữ liệu: {source_label}")
        if engine.is_ready():
            stats = engine.get_index_stats()
            st.markdown('<span class="status-dot green"></span> **Sẵn sàng**', unsafe_allow_html=True)
            col1, col2, col3 = st.columns(3)
            col1.metric("Học sinh", f"{stats.get('num_students', 0):,}")
            col2.metric("Môn học", f"{stats.get('num_subjects', 0):,}")
            col3.metric("Bản ghi", f"{stats.get('total_records', 0):,}")
            st.caption("Năm học: " + ", ".join(stats.get("school_years", [])))
        else:
            st.markdown('<span class="status-dot red"></span> **Chưa có dữ liệu**', unsafe_allow_html=True)
            if USE_SUPABASE:
                st.info("Không lấy được dữ liệu từ Supabase. Kiểm tra lại SUPABASE_URL/SUPABASE_KEY trong .env.")
            else:
                st.info(f"Đặt file .xlsx sổ điểm vào thư mục `{GRADES_DIR.name}/` rồi bấm 'Tải lại dữ liệu'.")

        st.divider()

        if not USE_SUPABASE:
            st.markdown("##### 📄 Quản lý sổ điểm")
            uploaded_files = st.file_uploader(
                "Thêm file Excel sổ điểm (.xlsx)", type=["xlsx"],
                accept_multiple_files=True,
                help="File sẽ được lưu vào thư mục dữ liệu rồi nạp lại.",
            )
            if uploaded_files:
                GRADES_DIR.mkdir(parents=True, exist_ok=True)
                saved = []
                for uf in uploaded_files:
                    (GRADES_DIR / uf.name).write_bytes(uf.read())
                    saved.append(uf.name)
                st.success(f"✅ Đã lưu: {', '.join(saved)}. Bấm 'Tải lại dữ liệu' để áp dụng.")

        if st.button("🔄 Tải lại dữ liệu", use_container_width=True):
            get_engine.clear()
            st.rerun()

        st.divider()

        if st.button("🆕 Cuộc hội thoại mới", use_container_width=True, type="primary"):
            engine.clear_session(session_user)
            st.session_state["messages"] = []
            st.session_state["is_generating"] = False
            st.rerun()


# ---------------------------------------------------------------------------
# Setup guide (no data)
# ---------------------------------------------------------------------------

def render_setup_guide() -> None:
    st.markdown("""
    <div class="agent-header">
        <h1>⚠️ Chưa có dữ liệu điểm</h1>
        <p>Chatbot cần có nguồn dữ liệu điểm để hoạt động.</p>
    </div>
    """, unsafe_allow_html=True)

    if USE_SUPABASE:
        st.markdown("""
```
Đang cấu hình dùng Supabase (USE_SUPABASE=true) nhưng không lấy được dữ liệu.
Kiểm tra lại trong .env:
  - SUPABASE_URL đúng project chưa
  - SUPABASE_KEY phải là service_role key (không phải anon/publishable key)
  - Bảng subject_results/grade_items có dữ liệu cho SUPABASE_SUBJECT_NAME chưa
```
        """)
    else:
        st.markdown(f"""
```
# Bước 1: Đặt file .xlsx sổ điểm vào thư mục:
{GRADES_DIR}

# Bước 2: Bấm nút "Tải lại dữ liệu" ở thanh bên,
# hoặc tải file lên trực tiếp qua mục "Quản lý sổ điểm".
```
        """)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

def render_header():
    st.markdown(f"""
    <div class="agent-header">
        <h1>{APP_ICON} Tra Cứu Điểm Học Sinh</h1>
        <p>Hỏi điểm tất cả các môn hoặc từng môn cụ thể, danh sách lớp, thời khóa biểu, thông báo...</p>
    </div>
    """, unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Suggested questions
# ---------------------------------------------------------------------------

SUGGESTED_QUESTIONS = [
    "Xếp loại học lực học kỳ I của Nguyễn Nông Bảo An năm 2025-2026?",
    "Điểm trung bình lớp 6A môn Toán học kỳ I năm 2025-2026?",
    "Giáo viên chủ nhiệm lớp 6A năm học 2025-2026 là ai?",
    "Thông tin học sinh Nguyễn Nông Bảo An?",
]

STUDENT_SUGGESTED_QUESTIONS = [
    "Kết quả học tập cả năm của tôi thế nào?",
    "Thời khóa biểu của tôi?",
    "Lịch thi của tôi trong kì này?",
    "Tôi đã nghỉ những buổi học nào?",
]


def render_suggestions(session_user) -> Optional[str]:
    if st.session_state.get("messages"):
        return None

    questions = STUDENT_SUGGESTED_QUESTIONS if (session_user is not None and session_user.is_student) else SUGGESTED_QUESTIONS

    st.markdown("#### 💡 Câu hỏi gợi ý")
    cols = st.columns(len(questions))
    for i, (col, q) in enumerate(zip(cols, questions)):
        with col:
            if st.button(q, key=f"suggest_{i}", use_container_width=True):
                return q
    return None


# ---------------------------------------------------------------------------
# Chat area
# ---------------------------------------------------------------------------

def render_chat_area(engine: ChatbotEngine, session_user) -> None:
    if "messages" not in st.session_state:
        st.session_state["messages"] = []
    if "is_generating" not in st.session_state:
        st.session_state["is_generating"] = False
    if "stop_requested" not in st.session_state:
        st.session_state["stop_requested"] = False

    for msg in st.session_state["messages"]:
        with st.chat_message(msg["role"], avatar="🤖" if msg["role"] == "assistant" else "👤"):
            st.markdown(msg["content"])
            if msg.get("warnings"):
                for w in msg["warnings"]:
                    st.info(w, icon="ℹ️")

    if st.session_state["is_generating"]:
        if st.button("⏹️ Dừng sinh câu trả lời", type="primary", use_container_width=True):
            st.session_state["stop_requested"] = True
            st.session_state["is_generating"] = False

    prompt = st.chat_input(
        "Hỏi về điểm học sinh (tên, lớp, học kỳ, năm học)..."
        if not st.session_state["is_generating"] else "⏳ Đang trả lời...",
        disabled=st.session_state["is_generating"],
    )

    if prompt:
        st.session_state["is_generating"] = True
        st.session_state["stop_requested"] = False

        with st.chat_message("user", avatar="👤"):
            st.markdown(prompt)
        st.session_state["messages"].append({"role": "user", "content": prompt, "warnings": []})

        with st.chat_message("assistant", avatar="🤖"):
            thinking = st.empty()
            thinking.markdown("""
            <div style="display:flex;align-items:center;gap:0.5rem;padding:0.75rem 1rem;
                        background:linear-gradient(135deg,#1e293b,#334155);border-radius:12px;
                        color:#94a3b8;font-size:0.85rem;">
                🔍 Đang tra cứu sổ điểm...
            </div>
            """, unsafe_allow_html=True)

            placeholder = st.empty()
            full_text = ""

            try:
                stream_gen, lookup = engine.chat_streaming(prompt, session_user=session_user)
                thinking.empty()

                for token in stream_gen:
                    if st.session_state.get("stop_requested"):
                        break
                    full_text += token
                    placeholder.markdown(strip_think_tags(full_text) + " ▌")
                full_text = strip_think_tags(full_text)
                placeholder.markdown(full_text)

                response = engine.finalize_streaming_response(
                    full_text=full_text, lookup=lookup, question=prompt, session_user=session_user,
                )

                extra = format_for_display(response)[len(full_text):]
                if extra.strip():
                    st.markdown(extra)

                for warning in response.warnings:
                    st.info(warning, icon="ℹ️")

                st.session_state["messages"].append({
                    "role": "assistant",
                    "content": format_for_display(response),
                    "warnings": response.warnings,
                })

            except RuntimeError as e:
                thinking.empty()
                err_msg = f"❌ Lỗi: {e}"
                placeholder.error(err_msg)
                st.session_state["messages"].append({"role": "assistant", "content": err_msg, "warnings": []})

        st.session_state["is_generating"] = False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    engine = get_engine()

    if USE_AUTH and get_session_user() is None:
        render_login(engine)
        return

    # O che do chon vai tro, render_sidebar (chua role selector) se set
    # session_user vao state; doc lai NGAY SAU do de dung cho phan chinh.
    render_sidebar(engine, get_session_user())
    session_user = get_session_user()

    if not engine.is_ready():
        render_setup_guide()
        return

    render_header()

    suggested = render_suggestions(session_user)
    if suggested:
        st.session_state.setdefault("messages", [])
        try:
            response = engine.chat(suggested, session_user=session_user)
            st.session_state["messages"].append({"role": "user", "content": suggested})
            st.session_state["messages"].append({
                "role": "assistant",
                "content": format_for_display(response),
                "warnings": response.warnings,
            })
        except Exception as e:
            st.session_state["messages"] = [
                {"role": "user", "content": suggested},
                {"role": "assistant", "content": f"Lỗi: {e}", "warnings": []},
            ]
        st.rerun()

    render_chat_area(engine, session_user)


if __name__ == "__main__":
    main()
