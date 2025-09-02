import re
import io
import time
import json
import base64
import boto3
import requests
import datetime
from pathlib import Path
import streamlit as st
import streamlit.components.v1 as components
from typing import Generator, Dict, List, Optional, Tuple

# =========================
# Helper to get secrets
# =========================
def get_secret(section: str, key: str, default=None):
    try:
        return st.secrets[section][key]
    except Exception:
        return default

# =========================
# Azure OpenAI (Chat + Streaming)
# =========================
def stream_azure_chat(system: str, user: str, temperature: float = 0.6) -> Generator[str, None, None]:
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
        "max_tokens": 3500,
    }

    with requests.post(url, headers=headers, json=payload, stream=True, timeout=900) as r:
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

def azure_chat_once(system: str, user: str, temperature: float = 0.3, max_tokens: int = 800) -> str:
    s = st.secrets["azure"]
    api_key = s["AZURE_API_KEY"]
    endpoint = s["AZURE_ENDPOINT"].rstrip("/")
    deployment = s["AZURE_DEPLOYMENT"]
    version = s.get("AZURE_API_VERSION", "2025-01-01-preview")

    url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={version}"
    headers = {"api-key": api_key, "Content-Type": "application/json"}
    payload = {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

# =========================
# Azure DALL·E
# =========================
def dalle_generate(prompt: str, size: str = "1024x1024") -> bytes:
    s = st.secrets["dalle"]
    endpoint = s["DALE_ENDPOINT"]
    key = s["DALE_KEY"]
    headers = {"api-key": key, "Content-Type": "application/json"}
    payload = {"prompt": prompt, "size": size, "n": 1}
    r = requests.post(endpoint, headers=headers, json=payload, timeout=300)
    r.raise_for_status()
    data = r.json()
    b64 = data["data"][0].get("b64_json")
    if not b64:
        raise RuntimeError("No image data in DALL·E response.")
    return base64.b64decode(b64)

# =========================
# AWS S3
# =========================
def s3_client():
    return boto3.client(
        "s3",
        region_name=st.secrets["aws"]["AWS_REGION"],
        aws_access_key_id=st.secrets["aws"]["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=st.secrets["aws"]["AWS_SECRET_ACCESS_KEY"],
    )

def s3_upload_bytes(data: bytes, key: str, content_type: str) -> str:
    bucket = st.secrets["aws"]["AWS_BUCKET"]
    region = st.secrets["aws"]["AWS_REGION"]
    s3 = s3_client()
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, ACL="public-read")
    cdn = get_secret("cdn", "CDN_PREFIX_MEDIA", "")
    if cdn:
        return cdn.rstrip("/") + "/" + key
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

# =========================
# HTML Wrapping
# =========================
HTML_TAG_RE = re.compile(r"<\s*html\b", re.I)
END_HTML_RE = re.compile(r"</\s*html\s*>", re.I)

PHASER_SCAFFOLD = """<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8"/>
    <title>{title}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <script src="https://cdn.jsdelivr.net/npm/phaser@3/dist/phaser.js"></script>
    <style>
      html, body {{ margin:0; padding:0; background:{bg}; }}
    </style>
  </head>
  <body>
    <div id="wrap"></div>
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
        this.add.text(20,20,'Scaffold active',{{fontFamily:'monospace',fontSize:'16px',color:'#fff'}});
      }}
      function update() {{}}
      new Phaser.Game(config);
    </script>
    <script>
{snippet}
    </script>
  </body>
</html>
"""

def looks_like_full_html(text: str) -> bool:
    return bool(HTML_TAG_RE.search(text) and END_HTML_RE.search(text))

def autowrap_if_needed(generated: str, *, title="Phaser Preview", width=720, height=1280, bg="#0e1a20") -> str:
    if looks_like_full_html(generated):
        return generated
    stripped = generated.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[^\n]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    return PHASER_SCAFFOLD.format(title=title, width=width, height=height, bg=bg, snippet=stripped)

# =========================
# Asset Handling
# =========================
PLACEHOLDER_RE = re.compile(r"ASSET_URL_([A-Za-z0-9_]+)")

def extract_asset_keys(code: str) -> List[str]:
    return sorted(list(set(PLACEHOLDER_RE.findall(code))))

def inject_asset_urls(code: str, mapping: Dict[str, str]) -> str:
    for key, url in mapping.items():
        code = code.replace(f"ASSET_URL_{key}", url)
    return code

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Advanced Phaser Game Builder", page_icon="🎮", layout="wide")
st.title("🎮 Advanced Phaser 2D Game Builder")

# Sidebar
with st.sidebar:
    st.subheader("Status")
    st.write("Azure GPT-5: " + ("✅" if get_secret("azure","AZURE_API_KEY") else "❌"))
    st.write("Azure DALL·E 3: " + ("✅" if get_secret("dalle","DALE_KEY") else "❌"))
    st.write("AWS S3: " + ("✅" if get_secret("aws","AWS_ACCESS_KEY_ID") else "❌"))
    st.caption("Set credentials in `.streamlit/secrets.toml`")

creativity = st.slider("Creativity", 0.0, 1.2, 0.6, 0.1)
canvas_w = st.number_input("Canvas width", 480, 1920, 720, 10)
canvas_h = st.number_input("Canvas height", 480, 1920, 1280, 10)
bg_color = st.color_picker("Background color", "#0e1a20")

SYSTEM_INSTRUCT = (
    "You are a Phaser 3 game code generator. "
    "Always use ASSET_URL_<key> placeholders for all image loaders."
)

prompt = st.text_area(
    "Describe the game you want",
    placeholder=(
        "Example: Create a two-stage endless runner with bg, hero, obstacle, and coin assets. "
        "Use ASSET_URL_bg, ASSET_URL_hero, etc."
    ),
    height=120,
)

# Session state
if "buffer" not in st.session_state:
    st.session_state.buffer = ""
if "latest_html" not in st.session_state:
    st.session_state.latest_html = ""
if "asset_urls" not in st.session_state:
    st.session_state.asset_urls = {}

code_placeholder = st.empty()
preview_placeholder = st.empty()

def render_preview():
    if not st.session_state.latest_html:
        return
    with preview_placeholder:
        components.html(
            st.session_state.latest_html,
            height=min(max(canvas_h + 60, 600), 1600),
            scrolling=False
        )

def update_preview_from_buffer():
    buf = st.session_state.buffer
    if not buf.strip():
        return
    if looks_like_full_html(buf):
        st.session_state.latest_html = buf
    else:
        st.session_state.latest_html = autowrap_if_needed(
            buf, title="Phaser Preview", width=canvas_w, height=canvas_h, bg=bg_color
        )
    render_preview()

# Generate game code
if st.button("Generate Phaser Game", type="primary", disabled=not prompt.strip()):
    st.session_state.buffer = ""
    st.session_state.asset_urls = {}
    code_placeholder.code("// Streaming Phaser code...", language="html")
    try:
        for chunk in stream_azure_chat(SYSTEM_INSTRUCT, prompt, temperature=creativity):
            st.session_state.buffer += chunk
            code_placeholder.code(st.session_state.buffer, language="html")
            update_preview_from_buffer()
            time.sleep(0.03)
    except Exception as e:
        st.error(f"Error: {e}")

# Show preview if any
if st.session_state.latest_html:
    render_preview()

st.markdown("---")

# Assets section
st.subheader("Assets")
detected_keys = extract_asset_keys(st.session_state.buffer)
if detected_keys:
    st.write("Detected asset placeholders:", ", ".join(f"`{k}`" for k in detected_keys))
else:
    st.info("No assets detected. Ask GPT-5 to use ASSET_URL_<key> placeholders.")
    detected_keys = []

if detected_keys:
    uploaded = {}
    for key in detected_keys:
        img_prompt = st.text_input(f"Prompt for {key}", value=f"{key} asset, cartoon style")
        if st.button(f"Generate {key} image & upload to S3", key=f"gen_{key}"):
            try:
                img_bytes = dalle_generate(img_prompt)
                s3_key = f"{get_secret('aws','S3_PREFIX','media')}/games/assets/{int(time.time())}_{key}.png"
                url = s3_upload_bytes(img_bytes, s3_key, content_type="image/png")
                st.image(img_bytes, caption=f"{key} → {url}", use_column_width=True)
                uploaded[key] = url
            except Exception as e:
                st.error(f"Failed for {key}: {e}")
    if uploaded:
        st.session_state.asset_urls.update(uploaded)
        st.success("Images uploaded!")

# Inject URLs into code
if st.session_state.asset_urls and st.session_state.buffer:
    if st.button("Inject asset URLs and refresh preview"):
        st.session_state.buffer = inject_asset_urls(st.session_state.buffer, st.session_state.asset_urls)
        st.session_state.latest_html = autowrap_if_needed(
            st.session_state.buffer, width=canvas_w, height=canvas_h, bg=bg_color
        )
        code_placeholder.code(st.session_state.buffer, language="html")
        render_preview()
        st.success("Updated game with CDN URLs!")

# Export game
st.markdown("---")
st.subheader("Export Game")

if st.session_state.latest_html:
    html_bytes = st.session_state.latest_html.encode("utf-8")
    default_name = f"game_{datetime.datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"

    # Download button
    st.download_button(
        label="⬇️ Download index.html",
        data=html_bytes,
        file_name="index.html",
        mime="text/html; charset=utf-8"
    )

    # Optional upload to S3
    if st.button("Upload HTML to S3 & get link"):
        try:
            s3_key = f"{get_secret('aws','S3_PREFIX','media')}/games/builds/{default_name}"
            url = s3_upload_bytes(html_bytes, s3_key, content_type="text/html; charset=utf-8")
            st.success("Uploaded HTML")
            st.code(url)
            st.markdown(f"[Open Game Page]({url})")
        except Exception as e:
            st.error(f"Upload failed: {e}")
else:
    st.info("Generate game first to enable export.")
