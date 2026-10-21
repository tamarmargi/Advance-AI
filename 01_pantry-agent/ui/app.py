"""Optional Streamlit UI for the pantry agent (Hebrew, RTL). Run: streamlit run ui/app.py

Uses the existing agents unchanged: API/agent.py and SDK/agent.py.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from shared.runner import SCENARIO  # noqa: E402
from shared.tools import reset_state  # noqa: E402
from ui.compare import render_compare  # noqa: E402
from ui.state_view import render_state  # noqa: E402
from ui.worker import AgentWorker  # noqa: E402

RTL_CSS = """<style>
.stApp, [data-testid="stSidebar"], [data-testid="stChatMessage"], .stMarkdown, .stTable, .stAlert {
  direction: rtl; text-align: right; }
[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"] { direction: ltr; }
table th, table td { text-align: right !important; }
</style>"""


def settings() -> tuple[str, str]:
    cfg = json.loads(SCENARIO.read_text(encoding="utf-8"))
    return cfg["model"], cfg["effort"]


def api_key() -> str | None:
    """ANTHROPIC_API_KEY from the environment, else from the git-ignored .env file.

    The key is handed straight to the API client and never written to os.environ, so the SDK's
    Claude Code subprocess (which inherits the environment) keeps using the Claude Code login.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == "ANTHROPIC_API_KEY":
                return value.strip().strip("\"'") or None
    return None


def make_worker(version: str, resume: bool) -> AgentWorker:
    model, effort = settings()
    if version == "API":
        import anthropic
        from API.agent import Agent
        key = api_key()
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY לא נמצא בסביבה ולא בקובץ .env")
        return AgentWorker(lambda: Agent(model, effort, client=anthropic.Anthropic(api_key=key)))
    from SDK.agent import Agent
    prev = st.session_state.get("last_sdk") if resume else None
    return AgentWorker(lambda: Agent(model, effort, resume=prev and prev["session_id"],
                                     cost_offset=prev["cumulative_cost"] if prev else 0.0))


def start_session(version: str, resume: bool = False) -> None:
    """Close the current agent and start a NEW one (memory is loaded from disk)."""
    old = st.session_state.get("worker")
    if old is not None:
        if st.session_state.get("worker_version") == "SDK":
            sid, cost = old.call(lambda a: (a.session_id, a.cumulative_cost))
            if sid:
                st.session_state["last_sdk"] = {"session_id": sid, "cumulative_cost": cost}
        old.close()
    st.session_state.update(worker=None, worker_version=version, messages=[], agent_error=None)
    try:
        st.session_state["worker"] = make_worker(version, resume)
    except Exception as e:  # e.g. missing API key
        st.session_state["agent_error"] = f"{type(e).__name__}: {e}"


def turn_details(info: dict) -> None:
    with st.expander("פרטי התור"):
        u = info.get("usage") or {}
        cost = u.get("cost_usd")
        st.markdown(
            f"**קריאות למודל:** {info.get('model_calls', 0)} · **זמן:** {info.get('seconds', 0):.1f} ש׳ · "
            f"**עלות:** {'לא ידוע' if cost is None else f'${cost:.4f}'}  \n"
            f"**טוקנים:** קלט {u.get('input_tokens', 0):,} · קריאה מהמטמון {u.get('cache_read_input_tokens', 0):,} · "
            f"כתיבה למטמון {u.get('cache_creation_input_tokens', 0):,} · פלט {u.get('output_tokens', 0):,}")
        calls = info.get("tool_calls", [])
        if not calls:
            st.caption("לא נקראו כלים.")
        for c in calls:
            line = f"`{c['name']}` {json.dumps(c['input'], ensure_ascii=False)} → {c.get('result') or ''}"
            (st.error if c.get("is_error") else st.markdown)(line)


def sidebar() -> None:
    with st.sidebar:
        st.header("סוכן המזווה")
        version = st.radio("גרסה", ["API", "SDK"], horizontal=True, key="version")
        if version != st.session_state.get("worker_version"):
            start_session(version)

        if version == "API" and not api_key():
            st.warning("ANTHROPIC_API_KEY לא נמצא בסביבה ולא בקובץ .env. גרסת ה-API לא תוכל לפנות למודל.")
        if version == "API" and api_key() and not os.environ.get("ANTHROPIC_API_KEY"):
            st.caption("המפתח נטען מהקובץ .env, רק בשביל גרסת ה-API.")
        if version == "SDK" and os.environ.get("ANTHROPIC_API_KEY"):
            st.warning("ANTHROPIC_API_KEY מוגדר בסביבה. גרסת ה-SDK אמורה לרוץ בלי מפתח, על החשבון של Claude Code.")

        resume = False
        if version == "SDK":
            resume = st.checkbox("המשך את הסשן הקודם (resume)", disabled="last_sdk" not in st.session_state)
        if st.button("סשן חדש", width="stretch"):
            with st.spinner("פותח סשן חדש..."):
                start_session(version, resume)

        st.divider()
        sure = st.checkbox("אני בטוח/ה: למחוק את כל המצב השמור")
        if st.button("איפוס מצב", disabled=not sure, width="stretch"):
            reset_state()
            st.success("המצב אופס. כדאי לפתוח סשן חדש כדי שהזיכרון ייטען מחדש.")

        model, effort = settings()
        st.caption(f"מודל: {model} · effort: {effort}")


def chat() -> None:
    if st.session_state.get("agent_error"):
        st.error("לא ניתן ליצור סוכן: " + st.session_state["agent_error"])
    for m in st.session_state["messages"]:
        with st.chat_message(m["role"]):
            st.markdown(m["text"])
            if m.get("info"):
                turn_details(m["info"])

    prompt = st.chat_input("כתבו הודעה לסוכן...", disabled=st.session_state.get("worker") is None)
    if not prompt:
        return
    st.session_state["messages"].append({"role": "user", "text": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        try:
            with st.spinner("הסוכן עובד..."):
                t0 = time.perf_counter()
                info = st.session_state["worker"].call(lambda a: a.send(prompt))
                info["seconds"] = time.perf_counter() - t0
        except Exception as e:
            st.error(f"שגיאה בתור: {type(e).__name__}: {e}")
            st.session_state["messages"].append({"role": "assistant", "text": f"⚠️ שגיאה: {e}"})
            return
        text = info.get("text") or "(אין תשובה)"
        st.markdown(text)
        turn_details(info)
    st.session_state["messages"].append({"role": "assistant", "text": text, "info": info})


def main() -> None:
    st.set_page_config(page_title="סוכן המזווה", page_icon="🥘", layout="wide")
    st.markdown(RTL_CSS, unsafe_allow_html=True)
    st.session_state.setdefault("messages", [])
    sidebar()

    tab_chat, tab_state, tab_compare = st.tabs(["צ'אט", "מצב שמור", "השוואה"])
    with tab_chat:
        col_state, col_chat = st.columns([2, 3])  # RTL: first column is on the right
        with col_chat:
            chat()  # runs the turn before the state panel renders, so the panel is fresh
        with col_state:
            render_state("side")
    with tab_state:
        render_state("tab")
    with tab_compare:
        render_compare()


main()
