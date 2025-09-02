# app.py
# Advanced Streamlit Phaser builder:
# - ChatGPT-style streaming code generator (Azure GPT-5)
# - Enforces asset placeholders (ASSET_URL_<key>)
# - Asset planning (GPT-5) -> Azure DALL·E image generation -> S3 upload
# - Injects CDN URLs into code -> Live preview
# - Export index.html (optional upload to S3)

import re
import io
import time
import json
import base64
import requests
import boto3
import streamlit as st
import streamlit.components.v1 as components
from typing import Generator, Dict, List, Optional, Tuple

# =========================
# Secrets helpers
# =========================
def get_secret(section: str, key: str, default=None):
    try:
        return st.secrets[section][key]
    except Exception:
        return default

# Azure OpenAI (chat)
def have_azure() -> bool:
    try:
        s = st.secrets["azure"]
        return bool(s.get("AZURE_API_KEY") and s.get("AZURE_ENDPOINT") and s.get("AZURE_DEPLOYMENT"))
    except Exception:
        return False

# Azure OpenAI (DALL·E images)
def have_dalle() -> bool:
    try:
        s = st.secrets["dalle"]
        return bool(s.get("DALE_ENDPOINT") and s.get("DALE_KEY"))
    except Exception:
        return False

# AWS S3
def have_s3() -> bool:
    try:
        s = st.secrets["aws"]
        return bool(s.get("AWS_ACCESS_KEY_ID") and s.get("AWS_SECRET_ACCESS_KEY") and s.get("AWS_BUCKET"))
    except Exception:
        return False

# CDN base for media
def cdn_media_base() -> str:
    return get_secret("cdn", "CDN_PREFIX_MEDIA", "")

# =========================
# Azure GPT-5 STREAMING chat
# =========================
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
    """Non-streaming call (for asset-plan JSON)."""
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
    """
    Calls Azure OpenAI DALL·E 3 endpoint.
    Returns image bytes (PNG) decoded from b64_json.
    """
    s = st.secrets["dalle"]
    endpoint = s["DALE_ENDPOINT"]  # includes api-version
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
    """
    Uploads bytes to s3://AWS_BUCKET/key (public-read) and returns a URL
    (prefers CDN_PREFIX_MEDIA if configured).
    """
    bucket = st.secrets["aws"]["AWS_BUCKET"]
    region = st.secrets["aws"]["AWS_REGION"]
    s3 = s3_client()
    s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type, ACL="public-read")
    cdn = cdn_media_base()
    if cdn:
        return cdn.rstrip("/") + "/" + key
    return f"https://{bucket}.s3.{region}.amazonaws.com/{key}"

# =========================
# HTML wrapping helpers
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
        this.add.text(16,16,'Scaffold active',{{
          fontFamily:'monospace', fontSize:'14px', color:'#9ad4f5'
        }});
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

def autowrap_if_needed(generated: str, *, title="Phaser Preview", width=720, height=1280, bg="#0e1a20") -> str:
    """
    If the model output is a full HTML page, return as-is.
    If it's just JS (or <script>...</script>), wrap in a safe Phaser scaffold.
    """
    if looks_like_full_html(generated):
        return generated

    stripped = generated.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[^\n]*\n", "", stripped)
        stripped = re.sub(r"\n```$", "", stripped)
    return PHASER_SCAFFOLD.format(title=title, width=width, height=height, bg=bg, snippet=stripped)

# =========================
# Asset placeholder extraction & replacement
# =========================
PLACEHOLDER_RE = re.compile(r"ASSET_URL_([A-Za-z0-9_]+)")

def extract_asset_keys(code: str) -> List[str]:
    return sorted(list(set(PLACEHOLDER_RE.findall(code))))

def inject_asset_urls(code: str, mapping: Dict[str, str]) -> str:
    """
    Replace ASSET_URL_<key> with the actual URL (string-literal-safe).
    """
    for key, url in mapping.items():
        code = code.replace(f"ASSET_URL_{key}", url)
    return code

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Advanced Phaser Builder", page_icon="🎮", layout="wide")
st.title("🎮 Advanced Phaser 2D Game Builder")

with st.sidebar:
    st.subheader("Status")
    st.write(("✅ " if have_azure() else "❌ ") + "Azure GPT-5")
    st.write(("✅ " if have_dalle() else "❌ ") + "Azure DALL·E 3")
    st.write(("✅ " if have_s3() else "❌ ") + "AWS S3")
    st.caption("Add credentials in `.streamlit/secrets.toml`.")

creativity = st.slider("Creativity (temperature)", 0.0, 1.2, 0.6, 0.1)
canvas_w = st.number_input("Canvas width", 480, 1920, 720, 10)
canvas_h = st.number_input("Canvas height", 480, 1920, 1280, 10)
bg_color = st.color_picker("Preview background", "#0e1a20")

SYSTEM_INSTRUCT = (
    "You are a professional Phaser 3 game code generator. "
    "Output either a FULL single-file HTML page, or a single <script> block / plain JS with a minimal working scene. "
    "IMPORTANT: When loading or referencing images, ALWAYS use placeholder URLs of the form ASSET_URL_<key>, e.g.: "
    "this.load.image('bg', 'ASSET_URL_bg'); this.add.image(400,300,'bg'); "
    "Keys must be alphanumeric/underscore only. "
    "Prefer shapes/text if assets are optional. Keep code minimal and working."
)

prompt = st.text_area(
    "Describe the game you want",
    placeholder=(
        "Example: Side-scrolling runner with a sky background (key: bg), a hero (key: hero), "
        "and obstacles (key: rock). Use the keys bg, hero, rock for assets. "
        "Two stages, tutorial hint after 3s, and a CTA button that opens a URL."
    ),
    height=120,
)

# Session state
if "buffer" not in st.session_state:
    st.session_state.buffer = ""
if "latest_html" not in st.session_state:
    st.session_state.latest_html = ""
if "is_generating" not in st.session_state:
    st.session_state.is_generating = False
if "asset_plan" not in st.session_state:
    st.session_state.asset_plan = []  # list of dicts: {key, prompt, size}
if "asset_urls" not in st.session_state:
    st.session_state.asset_urls = {}  # key -> URL

colA, colB = st.columns([1,1])
start = colA.button("Generate code (stream)", type="primary", disabled=st.session_state.is_generating or not prompt.strip())
clear = colB.button("Clear", disabled=st.session_state.is_generating)

if clear:
    st.session_state.buffer = ""
    st.session_state.latest_html = ""
    st.session_state.asset_plan = []
    st.session_state.asset_urls = {}
    st.rerun()

code_placeholder = st.empty()
preview_placeholder = st.empty()
download_placeholder = st.empty()

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

# Step 1: Stream code
if start:
    if not have_azure():
        st.error("Azure GPT-5 is not configured.")
    else:
        st.session_state.is_generating = True
        st.session_state.buffer = ""
        st.session_state.asset_plan = []
        st.session_state.asset_urls = {}
        code_placeholder.code("// streaming…\n// Remember: use placeholders like ASSET_URL_bg in your asset loaders.", language="html")
        try:
            for chunk in stream_azure_chat(SYSTEM_INSTRUCT, prompt, temperature=creativity):
                st.session_state.buffer += chunk
                code_placeholder.code(st.session_state.buffer, language="html")
                update_preview_from_buffer()
                time.sleep(0.03)
        except Exception as e:
            st.error(f"Error while streaming: {e}")
        finally:
            st.session_state.is_generating = False

# Show latest preview if any
if st.session_state.latest_html:
    render_preview()

st.markdown("---")

# Step 2: Asset planning
st.subheader("Assets")
detected_keys = extract_asset_keys(st.session_state.buffer)

if detected_keys:
    st.write("Detected asset placeholders:", ", ".join(f"`{k}`" for k in detected_keys))
else:
    st.info("No ASSET_URL_<key> placeholders found in the current code. Ask GPT-5 to use them when loading images.")
    detected_keys = []

colP1, colP2 = st.columns([1,1])

with colP1:
    st.markdown("**Generate a plan from GPT-5** (prompts & sizes for each key)")
    plan_note = st.text_area(
        "Describe art direction for images",
        value="Clean, cohesive art; readable silhouettes; bright, mobile-friendly colors.",
        height=80
    )
    if st.button("Propose prompts (JSON)"):
        if not have_azure():
            st.error("Azure GPT-5 not configured.")
        elif not detected_keys:
            st.error("No asset keys detected.")
        else:
            schema = {
                "images": [{"key": "bg", "prompt": "a sky gradient background", "size": "1024x1024"}]
            }
            SYSTEM_PLAN = (
                "Return STRICT JSON ONLY (no prose, no code fences) describing DALL·E prompts for each key; "
                "schema: " + json.dumps(schema) + " . "
                "Keys must exactly match the provided list."
            )
            USER_PLAN = "Keys: " + ", ".join(detected_keys) + ". Art direction: " + plan_note
            try:
                raw = azure_chat_once(SYSTEM_PLAN, USER_PLAN, temperature=0.4, max_tokens=600)
                # Try parse JSON
                match = re.search(r"\{.*\}", raw, flags=re.S)
                data = json.loads(match.group(0)) if match else json.loads(raw)
                st.session_state.asset_plan = data.get("images", [])
                st.success("Plan created. You can edit below before generating.")
            except Exception as e:
                st.error(f"Failed to parse plan: {e}")

with colP2:
    st.markdown("**Manual plan** (edit or create)")
    if not st.session_state.asset_plan and detected_keys:
        st.session_state.asset_plan = [{"key": k, "prompt": f"{k} concept art, cohesive style", "size": "1024x1024"} for k in detected_keys]

    new_plan = []
    for i, item in enumerate(st.session_state.asset_plan):
        st.write(f"**{item.get('key','(key)')}**")
        key = st.text_input(f"Key {i}", value=item.get("key",""), key=f"key_{i}")
        prompt_in = st.text_area(f"Prompt {i}", value=item.get("prompt",""), height=60, key=f"prompt_{i}")
        size = st.selectbox(f"Size {i}", ["1024x1024", "1792x1024", "1024x1792"], index=0, key=f"size_{i}")
        new_plan.append({"key": key, "prompt": prompt_in, "size": size})
        st.markdown("---")
    st.session_state.asset_plan = new_plan

# Step 3: Generate images + upload to S3
if st.session_state.asset_plan:
    if st.button("Generate images (DALL·E) & upload to S3", type="primary"):
        if not have_dalle() or not have_s3():
            st.error("DALL·E and/or S3 not configured.")
        else:
            uploaded = {}
            s3_prefix = get_secret("aws","S3_PREFIX","media").rstrip("/")
            for item in st.session_state.asset_plan:
                keyname = item["key"]
                prompt_img = item["prompt"]
                size = item.get("size","1024x1024")
                try:
                    img_bytes = dalle_generate(prompt_img, size=size)
                    s3_key = f"{s3_prefix}/games/assets/{int(time.time())}_{keyname}.png"
                    url = s3_upload_bytes(img_bytes, s3_key, content_type="image/png")
                    uploaded[keyname] = url
                    st.image(img_bytes, caption=f"{keyname} → {url}", use_column_width=True)
                except Exception as e:
                    st.error(f"Failed for {keyname}: {e}")
            if uploaded:
                st.session_state.asset_urls = uploaded
                st.success("Uploaded images and captured CDN URLs.")
                st.code(json.dumps(uploaded, indent=2), language="json")

# Step 4: Inject URLs into code & refresh preview
if st.session_state.asset_urls and st.session_state.buffer:
    if st.button("Inject asset URLs into code & refresh"):
        updated = inject_asset_urls(st.session_state.buffer, st.session_state.asset_urls)
        st.session_state.buffer = updated
        # Rebuild preview (wrap if needed)
        if looks_like_full_html(updated):
            st.session_state.latest_html = updated
        else:
            st.session_state.latest_html = autowrap_if_needed(updated, width=canvas_w, height=canvas_h, bg=bg_color)
        code_placeholder.code(st.session_state.buffer, language="html")
        render_preview()
        st.success("Injected and refreshed!")

# Step 5: Export index.html (and optional upload to S3)
st.markdown("### Export")
colX, colY = st.columns([1,1])
with colX:
    if st.session_state.latest_html:
        if st.button("Download index.html"):
            out_path = "/mnt/data/index.html"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(st.session_state.latest_html)
            st.success("Exported index.html")
            st.markdown(f"[Download the HTML](sandbox:{out_path})")
    else:
        st.info("Generate code first to enable export.")

with colY:
    if st.session_state.latest_html and have_s3():
        if st.button("Upload index.html to S3 & get link"):
            try:
                html_bytes = st.session_state.latest_html.encode("utf-8")
                s3_prefix = get_secret("aws","S3_PREFIX","media").rstrip("/")
                # Put the HTML alongside assets
                s3_key = f"{s3_prefix}/games/builds/{int(time.time())}_index.html"
                url = s3_upload_bytes(html_bytes, s3_key, content_type="text/html; charset=utf-8")
                st.success("Uploaded game HTML")
                st.code(url)
                st.markdown(f"[Open game page]({url})")
            except Exception as e:
                st.error(f"Upload failed: {e}")

st.markdown("---")
st.caption("Tip: In your prompt, specify asset keys you want (e.g., bg, hero, rock). The model must use ASSET_URL_<key> placeholders in loaders.")
