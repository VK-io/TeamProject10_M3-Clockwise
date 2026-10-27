import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from src.watcher import run_watcher
from src.chat_agent import process_handoff, handle_user_chat
from src.state import consume_handoffs, get_memory, set_memory, DB_PATH

st.set_page_config(page_title="Clockwise — Canvas AI Agent", page_icon="⏰", layout="centered")

st.title("⏰ Clockwise — Canvas Deadline Agent")
st.caption("Multi-Agent System: Backend Watcher Agent → Frontend ReAct Advisor Agent")

if "messages" not in st.session_state:
    st.session_state.messages = []

# Sidebar controls for Demo & Memory Layer
with st.sidebar:
    st.header("Agent Controls")
    mode = st.radio("Data Source", ["Offline Fixtures", "Live Canvas API"])
    is_offline = (mode == "Offline Fixtures")

    if st.button("🔄 Poll Canvas Now (Run Watcher)", use_container_width=True):
        with st.spinner("Watcher Agent polling Canvas & computing SQLite diff..."):
            try:
                res = run_watcher(offline=is_offline, db_path=DB_PATH)
                if res["status"] == "baseline_saved":
                    st.info(f"Baseline saved ({res['assignment_count']} assignments). Modify a fixture or Canvas date and poll again!")
                elif res["status"] == "no_material_changes":
                    st.success("Watcher Agent: No material changes detected (Silence preserved).")
                elif res["status"] == "handoff_created":
                    st.toast("🔔 New Handoff received from Watcher Agent!", icon="⏰")
            except Exception as e:
                st.error(f"Watcher error: {e}")

    st.divider()
    st.subheader("🧠 Memory Layer (SQLite)")
    current_pref = get_memory("triage_preference", default="concise", db_path=DB_PATH)
    new_pref = st.selectbox(
        "Notification Threshold",
        options=["concise", "all", "urgent_only"],
        index=["concise", "all", "urgent_only"].index(current_pref) if current_pref in ["concise", "all", "urgent_only"] else 0
    )
    if new_pref != current_pref:
        set_memory("triage_preference", new_pref, db_path=DB_PATH)
        st.success(f"Preference updated to: {new_pref}")

    with st.expander("📋 Current 14-Day Schedule"):
        st.text(get_memory("latest_report", default="Run the Watcher Agent first.", db_path=DB_PATH))

# Consume any pending handoffs from Watcher Agent -> Frontend Agent
pending_handoffs = consume_handoffs(db_path=DB_PATH)
for handoff in pending_handoffs:
    with st.spinner("Frontend Agent reasoning over Watcher handoff..."):
        try:
            res = process_handoff(handoff, db_path=DB_PATH)
            alert_msg = f"🔔 **New Deadline Alert**\n\n{res['digest']}\n\n```\n{handoff['report']}\n```"
            st.session_state.messages.append({
                "role": "assistant",
                "content": alert_msg,
                "thought": res["thought"]
            })
        except Exception as e:
            st.error(f"Handoff processing failed: {e}")

# Render Chat History
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg.get("thought"):
            with st.expander("💭 ReAct Reasoning Trace"):
                st.write(msg["thought"])
        st.markdown(msg["content"])

# Interactive Chat Input
if user_input := st.chat_input("Ask about your Canvas deadlines or change your notification settings..."):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                reply = handle_user_chat(user_input, db_path=DB_PATH)
                with st.expander("💭 ReAct Reasoning Trace"):
                    st.write(reply["thought"])
                st.markdown(reply["answer"])
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": reply["answer"],
                    "thought": reply["thought"]
                })
            except Exception as e:
                st.error(f"LLM Error: {e}")