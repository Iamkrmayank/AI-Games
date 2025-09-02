# app.py
# Streamlit + Azure GPT-5 streaming code generator with live Phaser preview.
# - Streams model output token-by-token
# - Shows growing code
# - Re-renders preview whenever there's a valid page (or auto-wraps JS)
# - Lets you download the latest HTML as index.html

import re
import time
import json
import requests
import streamlit as st
import streamlit.components.v1 as components
from typing import Generator

# =========================
# Azure GPT-5 (Chat Completions) STREAMING
# =========================
def have_azure() -> bool:
    try:
        s = st.secrets["azure"]
        return bool(s.get("AZURE_API_KEY") and s.get("AZURE_ENDPOINT") and s.get("AZURE_DEPLOYMENT"))
    except Exception:
        return False

def stream_azure_chat(system: str, user: str, temperature: float = 0.6) -> Generator[str, None, None]:
    """
    Streams content tokens from Azure Chat Completions (SSE-like).
    Yields text chunks (delta content).
    """
    s = st.secrets["azure"]
    api_key = s["AZURE_API_KEY"]
    endpoint = s["AZURE_ENDPOINT"].rstrip("/")
    deployment = s["AZURE_DEPLOYMENT"]
    version = s.get("AZURE_API_VERSION", "2025-01-01-preview")

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={version}"
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "stream": True,
        "max_tokens": 2000,
    }

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=600) as r:
        r.raise_for_status()
        for line in r.iter_lines(decode_unicode=True):
            if not line:
                continue
            if line.startswith("data: "):
                data_str = line[len("data: "):]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except Exception:
                    continue

# =========================
# Helpers: detect HTML / wrap JS
# =========================
HTML_TAG_RE = re.compile(r"<\s*html\b", re.I)
END_HTML_RE = re.compile(r"</\s*html\s*>", re.I)
SCRIPT_TAG_RE = re.compile(r"<\s*script\b", re.I)

PHASER_SCAFFOLD = """<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>{title}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <script src="https://cdn.jsdelivr.net/npm/phaser@3/dist/phaser.js"></script>
    <style>
      html, body {{ margin:0; padding:0; background:{bg}; }}
      #wrap {{ width:100%; height:100vh; display:flex; align-items:center; justify-content:center; }}
      .note {{ position: fixed; left: 12px; top: 8px; color:#cde4ef; font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; opacity:.85; }}
    </style>
  </head>
  <body>
    <div id="wrap"></div>
    <div class="note">Phaser preview — generated live</div>
    <script>
      const config = {{
        type: Phaser.AUTO,
        width: {width},
        height: {height},
        parent: 'wrap',
        scale: {{ mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH }},
        backgroundColor: '{bg}',
        scene: {{ preload, create, update }}
      }};
      function preload() {{}}
      function create() {{
        this.add.rectangle({width}//2, {height}//2, {width}-20, {height}-20, 0x1A2A32).setStrokeStyle(2, 0xffffff, 0.15);
      }}
      function update() {{}}
      new Phaser.Game(config);
    </script>

    <!-- === BEGIN MODEL SCRIPT === -->
    <script>
{snippet}
    </script>
    <!-- === END MODEL SCRIPT === -->
  </body>
</html>
"""

def looks_like_full_html(text: str) -> bool:
    return bool(HTML_TAG_RE.search(text) and END_HTML_RE.search(text))

def autowrap_if_needed(generated: str, *, title="Phaser Live Preview", width=720, height=1280, bg="#0e1a20") -> str:
    """If output is a full HTML page, return as-is. Else, wrap as a script in a safe Phaser scaffold."""
    if looks_like_full_html(generated):
        return generated

    stripped = generated.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[^\n]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)

    snippet = stripped  # works for <script> or plain JS
    return PHASER_SCAFFOLD.format(title=title, width=width, height=height, bg=bg, snippet=snippet)

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Streaming Phaser Generator", page_icon="🎮", layout="wide")
st.title("🎮 ChatGPT-style Streaming → Live Phaser 2D Preview")

st.markdown(
    "Type a prompt describing the **Phaser 3** mini-game you want. "
    "We stream code as it’s generated and update the **preview** as soon as possible."
)

with st.sidebar:
    st.subheader("Model & Options")
    if have_azure():
        st.success("Azure GPT-5: configured")
    else:
        st.warning("Azure GPT-5 not configured in `.streamlit/secrets.toml`")
    creativity = st.slider("Creativity (temperature)", 0.0, 1.2, 0.6, 0.1)
    width = st.number_input("Canvas width", 480, 1920, 720, 10)
    height = st.number_input("Canvas height", 480, 1920, 1280, 10)
    bg = st.color_picker("Background", "#0e1a20")

SYSTEM = (
    "You are a code generator that outputs either a FULL HTML page containing Phaser 3 game code, "
    "or a self-contained <script> with Phaser scene code. "
    "Do NOT include explanations. Prefer minimal, working code. "
    "Use asset-free shapes/text so it runs anywhere (no external images required)."
)

# Session state
if "buffer" not in st.session_state:
    st.session_state.buffer = ""
if "latest_html" not in st.session_state:
    st.session_state.latest_html = ""
if "is_generating" not in st.session_state:
    st.session_state.is_generating = False

prompt = st.text_area(
    "Prompt",
    placeholder="Example: Two-stage card tap mini-game (K→Q→4→5→6→7→8 then 6→7→8) with a tutorial hand and a pulsing CTA.",
    height=110
)

colA, colB = st.columns([1, 1])
start = colA.button("Generate (stream)", type="primary", disabled=st.session_state.is_generating or not prompt.strip())
clear = colB.button("Clear output", disabled=st.session_state.is_generating)

if clear:
    st.session_state.buffer = ""
    st.session_state.latest_html = ""
    st.rerun()

code_placeholder = st.empty()
preview_placeholder = st.empty()
download_placeholder = st.empty()

def _render_preview_from_buffer():
    """Try to build a valid page from the current buffer and render in the placeholder."""
    buf = st.session_state.buffer
    if not buf.strip():
        return
    if looks_like_full_html(buf):
        st.session_state.latest_html = buf
    else:
        st.session_state.latest_html = autowrap_if_needed(buf, width=width, height=height, bg=bg)

    # IMPORTANT: render inside the placeholder context (fixes AttributeError)
    with preview_placeholder:
        components.html(
            st.session_state.latest_html,
            height=min(max(height + 60, 600), 1400),
            scrolling=False
        )

if start:
    if not have_azure():
        st.error("Azure GPT-5 is not configured. Add your keys to `.streamlit/secrets.toml` and try again.")
    else:
        st.session_state.is_generating = True
        st.session_state.buffer = ""
        code_placeholder.code("// streaming…", language="html")

        try:
            for chunk in stream_azure_chat(SYSTEM, prompt, temperature=creativity):
                st.session_state.buffer += chunk
                code_placeholder.code(st.session_state.buffer, language="html")
                _render_preview_from_buffer()
                time.sleep(0.03)
        except Exception as e:
            st.error(f"Error while streaming: {e}")
        finally:
            st.session_state.is_generating = False

# If something was generated earlier, show it again (e.g., after rerun)
if st.session_state.latest_html:
    with preview_placeholder:
        components.html(
            st.session_state.latest_html,
            height=min(max(height + 60, 600), 1400),
            scrolling=False
        )

# Download button
if st.session_state.latest_html:
    if download_placeholder.button("Download index.html"):
        out_path = "/mnt/data/index.html"
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(st.session_state.latest_html)
        st.success("Exported index.html")
        st.markdown(f"[Download the HTML](sandbox:{out_path})")
