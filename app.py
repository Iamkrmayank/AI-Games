import json
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

import streamlit as st
import requests
import streamlit.components.v1 as components

# -----------------------------
# Page & Global Style
# -----------------------------
st.set_page_config(
    page_title="Phaser Mini-Game Builder",
    page_icon="🎮",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Subtle, modern CSS (glass cards, phone frame, nicer spacing)
st.markdown("""
<style>
:root{
  --bg: #0b1116;
  --muted: #9fb6c2;
  --text: #e6f1f5;
  --card: #121a21cc;
  --stroke: #21313d;
  --accent: linear-gradient(135deg, #3ee 0%, #a7f37f 100%);
}
html, body .block-container { padding-top: 1.25rem; }
section.main > div { padding-top: 0 !important; }
h1, h2, h3, h4, h5, h6 { letter-spacing: 0.2px; }
.small-muted { color: var(--muted); font-size: 0.9rem; }

.card {
  background: var(--card);
  border: 1px solid var(--stroke);
  border-radius: 16px;
  padding: 18px 18px 14px 18px;
  box-shadow: 0 4px 24px rgba(0,0,0,.25);
}

.header-hero {
  background: radial-gradient(1200px 600px at 10% -20%, rgba(0, 94, 130, 0.35), transparent 40%),
              radial-gradient(1200px 600px at 90% -30%, rgba(18, 162, 141, 0.35), transparent 40%);
  border: 1px solid var(--stroke);
  border-radius: 16px;
  padding: 18px;
  margin-bottom: 12px;
}

.badge {
  display: inline-flex;
  gap: .5rem;
  align-items: center;
  padding: 6px 10px;
  background: rgba(255,255,255,.06);
  border: 1px solid var(--stroke);
  border-radius: 999px;
  font-size: .9rem;
  color: var(--text);
}

.phone-frame {
  width: 420px;
  max-width: 100%;
  margin: 8px auto 0;
  border-radius: 26px;
  padding: 12px 12px 18px 12px;
  background: radial-gradient(1200px 600px at 50% -20%, rgba(255,255,255,0.06), transparent 50%),
              rgba(255,255,255,0.04);
  border: 1px solid var(--stroke);
  box-shadow: 0 30px 80px rgba(0,0,0,.35);
}

.phone-notch {
  width: 120px; height: 20px; margin: 6px auto 10px;
  border-radius: 10px; background: rgba(0,0,0,.5);
}

hr.soft { border: none; height: 1px; background: var(--stroke); margin: 12px 0; }

.stDownloadButton button[kind="primary"] {
  background-image: var(--accent);
  color: #0b1116 !important;
  border: none !important;
}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Azure (optional)
# -----------------------------
def have_azure() -> bool:
    try:
        s = st.secrets["azure"]
        return bool(s.get("AZURE_API_KEY") and s.get("AZURE_ENDPOINT") and s.get("AZURE_DEPLOYMENT"))
    except Exception:
        return False

def azure_chat(prompt: str, system: str, temperature: float = 0.6, max_tokens: int = 800) -> str:
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
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

# -----------------------------
# Game Model & Builder
# -----------------------------
@dataclass
class GameConfig:
    title: str = "Two-Stage Card-Track Mini-Game"
    canvas_w: int = 720
    canvas_h: int = 1280
    tutorial_delay_ms: int = 3000
    stage1_sequence: str = "K,Q,4,5,6,7,8"
    stage2_sequence: str = "6,7,8"
    hand_offset_x: int = 60
    hand_offset_y: int = 90
    theme_color_bg: str = "#0b1116"
    theme_color_track: str = "#CDAA6E"
    theme_color_card: str = "#121a21"
    theme_color_card_text: str = "#e6f1f5"
    theme_color_train: str = "#57c7ff"
    theme_color_cta: str = "#a7f37f"
    hint_text: str = "Tap the highlighted card to lay tracks!"
    cta_text: str = "PLAY FULL GAME"
    cta_url: str = "https://play.google.com/store/apps/details?id=com.brightpointstudios.apps.castle_royal"

def _seq_to_list(seq: str):
    return [c.strip() for c in seq.split(",") if c.strip()]

def build_phaser_html(cfg: GameConfig) -> str:
    s1 = _seq_to_list(cfg.stage1_sequence)
    s2 = _seq_to_list(cfg.stage2_sequence)
    return f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>{cfg.title}</title>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <script src="https://cdn.jsdelivr.net/npm/phaser@3/dist/phaser.js"></script>
    <style>
      html, body {{ margin:0; padding:0; background:{cfg.theme_color_bg}; }}
      #wrap {{ width:100%; display:flex; justify-content:center; }}
      .hint {{
        font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
        color:{cfg.theme_color_card_text}; text-align:center; margin-top:8px; opacity:.85
      }}
    </style>
  </head>
  <body>
    <div id="wrap"></div>
    <div class="hint">{cfg.hint_text}</div>
    <script>
      const CFG = {json.dumps(asdict(cfg))};
      const STAGE1 = {json.dumps(s1)};
      const STAGE2 = {json.dumps(s2)};
      const W = CFG.canvas_w, H = CFG.canvas_h;
      const TRACK_COLOR = 0x{cfg.theme_color_track.strip('#')};
      const CARD_COLOR = 0x{cfg.theme_color_card.strip('#')};
      const CARD_TEXT = "{cfg.theme_color_card_text}";
      const TRAIN_COLOR = 0x{cfg.theme_color_train.strip('#')};
      const CTA_COLOR = 0x{cfg.theme_color_cta.strip('#')};

      let handSprite = null, lastHandTime = 0, lastTappedKey = null;
      let gameComplete = false, flow = 1; let idx1 = 0, idx2 = 0;
      let cards = [], tracks = [];

      const config = {{
        type: Phaser.AUTO, width: W, height: H,
        backgroundColor: "{cfg.theme_color_bg}",
        parent: "wrap",
        scale: {{ mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH }},
        scene: {{ preload, create, update }}
      }};
      new Phaser.Game(config);

      function drawCard(scene, x, y, key, angle=0) {{
        const cw = 94, ch = 134;
        const rect = scene.add.rectangle(x, y, cw, ch, CARD_COLOR).setStrokeStyle(2, 0xffffff, 0.13);
        rect.setAngle(angle).setInteractive({{ cursor:'pointer' }});
        const label = scene.add.text(x, y, key, {{ fontFamily:'monospace', fontSize:'32px', color:CARD_TEXT }}).setOrigin(0.5);
        label.setAngle(angle);
        rect.on('pointerdown', () => onCardTap(scene, key, rect, label));
        return {{ key, rect, label }};
      }}

      function addTrackPiece(scene, x, y, h=120) {{
        const track = scene.add.rectangle(x, y, 14, h, TRACK_COLOR);
        track.alpha = 0;
        scene.tweens.add({{ targets: track, alpha:1, duration:280, ease:'Quad.easeIn' }});
        tracks.push(track);
      }}

      function wrongShake(scene, rect) {{
        const ox = rect.x;
        scene.tweens.add({{ targets: rect, x: ox+8, yoyo:true, repeat:2, duration:60, onComplete:() => rect.x = ox }});
      }}

      function onCardTap(scene, key, rect, label) {{
        const expect = (flow===1) ? STAGE1[idx1] : STAGE2[idx2];
        if (key !== expect || gameComplete) {{ wrongShake(scene, rect); return; }}
        lastTappedKey = key; rect.disableInteractive();
        const tx = W*0.72, ty = H*0.83;
        scene.tweens.add({{
          targets:[rect,label], x:tx, y:ty, angle:0, duration:340, ease:'Quad.easeInOut',
          onComplete:() => {{
            if (flow===1 && idx1>=3) addTrackPiece(scene, W*0.12, H*0.72-(idx1-3)*130, 110);
            if (flow===2) addTrackPiece(scene, W*0.12, H*0.72-(idx2)*130, 110);
            advance(scene);
          }}
        }});
      }}

      function advance(scene) {{
        if (flow===1) {{
          idx1++; if (idx1>=STAGE1.length) trainMove(scene, () => layoutStage2(scene));
        }} else {{
          idx2++; if (idx2>=STAGE2.length) {{ gameComplete=true; finalSequence(scene); }}
        }}
      }}

      function trainMove(scene, after) {{
        const train = scene.add.rectangle(W*0.2, H*0.9, 80, 40, TRAIN_COLOR);
        scene.tweens.add({{ targets:train, y:H*0.12, duration:1200, ease:'Cubic.easeInOut',
          onComplete:()=>{{ train.destroy(); after && after(); }} }});
      }}

      function clearCards() {{ cards.forEach(c=>{{c.rect.destroy(); c.label.destroy();}}); cards=[]; }}
      function clearTracks() {{ tracks.forEach(t=>t.destroy()); tracks=[]; }}

      function layoutStage1(scene) {{
        clearCards(); clearTracks(); flow=1; idx1=0; idx2=0; gameComplete=false;
        const left=W*0.40, right=W*0.65, yTop=H*0.45, yBot=H*0.65;
        cards.push(drawCard(scene, left,  H*0.35, 'K', 0));
        cards.push(drawCard(scene, right, H*0.35, 'Q', 0));
        const bottom=['4','5','6','7','8'];
        const pos=[[W*0.55,H*0.8],[left,yTop],[right,yTop],[left,yBot],[right,yBot]];
        bottom.forEach((k,i)=>{{ const [x,y]=pos[i]; const ang=(i==1?-15:(i==2?15:(i==3?15:(i==4?-15:0))));
          cards.push(drawCard(scene,x,y,k,ang)); }});
        lastHandTime = scene.time.now;
      }}

      function layoutStage2(scene) {{
        clearCards(); flow=2; idx2=0;
        const xmid=W*0.55, y0=H*0.5;
        cards.push(drawCard(scene, xmid,     y0,    '6', 0));
        cards.push(drawCard(scene, xmid-120, y0,    '7', -10));
        cards.push(drawCard(scene, xmid+120, y0,    '8', 10));
        cards.push(drawCard(scene, xmid,     y0+160,'5', 0));
        lastHandTime = scene.time.now;
      }}

      function finalSequence(scene) {{
        const cta = scene.add.rectangle(W*0.5, H*0.75, 280, 64, CTA_COLOR).setInteractive({{cursor:'pointer'}});
        scene.add.text(W*0.5, H*0.75, CFG.cta_text, {{
          fontFamily:'system-ui, -apple-system, Segoe UI, Roboto, sans-serif', fontSize:'20px', color:'#111'
        }}).setOrigin(0.5);
        scene.tweens.add({{ targets:cta, scaleX:1.06, scaleY:1.06, yoyo:true, repeat:-1, duration:1200, ease:'Sine.easeInOut' }});
        cta.on('pointerdown', ()=> window.open(CFG.cta_url, '_blank'));
      }}

      function getNextCardKey() {{ if (gameComplete) return null; return (flow===1) ? STAGE1[idx1]||null : STAGE2[idx2]||null; }}

      function showTutorialHand(scene) {{
        const target = getNextCardKey(); if (!target || target===lastTappedKey) return;
        const c = cards.find(cc=>cc.key===target); if (!c) return;
        const g = scene.add.graphics();
        g.fillStyle(0xffffff, 0.9);
        g.fillTriangle(c.rect.x+CFG.hand_offset_x, c.rect.y+CFG.hand_offset_y,
                       c.rect.x+CFG.hand_offset_x-18, c.rect.y+CFG.hand_offset_y+36,
                       c.rect.x+CFG.hand_offset_x+18, c.rect.y+CFG.hand_offset_y+36);
        handSprite = g;
        scene.tweens.add({{ targets:g, alpha:0, duration:1200, ease:'Sine.easeOut',
          onComplete:()=>{{ if(handSprite){{handSprite.destroy(); handSprite=null;}} }} }});
      }}

      function preload(){{}}
      function create() {{
        layoutStage1(this);
        this.time.addEvent({{
          delay: 250, loop: true, callback: ()=>{{
            if (!gameComplete && (this.time.now - lastHandTime) > CFG.tutorial_delay_ms && !handSprite) {{
              showTutorialHand(this); lastHandTime = this.time.now;
            }}
          }}
        }});
      }}
      function update(){{}}
    </script>
  </body>
</html>"""

# -----------------------------
# GPT-5 JSON Design Prompting
# -----------------------------
SCHEMA_EXAMPLE = {
    "title": "Train Tracks — Card Run",
    "stage1_sequence": "K,Q,4,5,6,7,8",
    "stage2_sequence": "6,7,8",
    "tutorial_delay_ms": 3000,
    "hand_offset_x": 60,
    "hand_offset_y": 90,
    "theme_color_bg": "#0b1116",
    "theme_color_track": "#CDAA6E",
    "theme_color_card": "#121a21",
    "theme_color_card_text": "#e6f1f5",
    "theme_color_train": "#57c7ff",
    "theme_color_cta": "#a7f37f",
    "hint_text": "Tap the highlighted card to lay tracks!",
    "cta_text": "PLAY FULL GAME",
    "cta_url": "https://play.google.com/store/apps/details?id=com.brightpointstudios.apps.castle_royal"
}

SYSTEM_JSON = (
    "You are a senior game designer. "
    "Return a STRICT JSON object ONLY (no prose, no code fences) with keys exactly like this schema: "
    + json.dumps(SCHEMA_EXAMPLE)
    + ". Values must be valid for a two-stage, guided card mini-game in Phaser. "
      "Colors must be #RRGGBB. Sequences are comma-separated like 'K,Q,4,5,6,7,8'. "
      "tutorial_delay_ms is 0..8000. Keep copy short and punchy."
)

def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    fenced = re.search(r"\{.*\}", text, flags=re.S)
    if fenced:
        try: return json.loads(fenced.group(0))
        except Exception: pass
    try: return json.loads(text)
    except Exception: return None

def apply_design_to_config(data: Dict[str, Any], cfg: GameConfig) -> GameConfig:
    def get(k, default):
        v = data.get(k, default)
        return v if v not in (None, "") else default
    cfg.title = get("title", cfg.title)
    cfg.stage1_sequence = get("stage1_sequence", cfg.stage1_sequence)
    cfg.stage2_sequence = get("stage2_sequence", cfg.stage2_sequence)
    cfg.tutorial_delay_ms = int(get("tutorial_delay_ms", cfg.tutorial_delay_ms))
    cfg.hand_offset_x = int(get("hand_offset_x", cfg.hand_offset_x))
    cfg.hand_offset_y = int(get("hand_offset_y", cfg.hand_offset_y))
    cfg.theme_color_bg = get("theme_color_bg", cfg.theme_color_bg)
    cfg.theme_color_track = get("theme_color_track", cfg.theme_color_track)
    cfg.theme_color_card = get("theme_color_card", cfg.theme_color_card)
    cfg.theme_color_card_text = get("theme_color_card_text", cfg.theme_color_card_text)
    cfg.theme_color_train = get("theme_color_train", cfg.theme_color_train)
    cfg.theme_color_cta = get("theme_color_cta", cfg.theme_color_cta)
    cfg.hint_text = get("hint_text", cfg.hint_text)
    cfg.cta_text = get("cta_text", cfg.cta_text)
    cfg.cta_url = get("cta_url", cfg.cta_url)
    return cfg

# -----------------------------
# State
# -----------------------------
if "cfg" not in st.session_state:
    st.session_state.cfg = GameConfig()
if "design_json" not in st.session_state:
    st.session_state.design_json = None
if "chat_msgs" not in st.session_state:
    st.session_state.chat_msgs = [{"role":"assistant","content":"Describe your vibe, sequences, colors, and CTA. I’ll propose a design JSON."}]

# -----------------------------
# Header
# -----------------------------
st.markdown("""
<div class="header-hero">
  <div class="badge">🎮 Phaser 3 Builder <span class="small-muted">two-stage, guided mini-game</span></div>
  <div style="display:flex; gap:12px; align-items:center; margin-top:8px;">
    <h1 style="margin:0;">Mini-Game Designer</h1>
    <span class="small-muted">Chat → JSON design → live preview → export</span>
  </div>
</div>
""", unsafe_allow_html=True)

# -----------------------------
# Sidebar: Presets & Status
# -----------------------------
with st.sidebar:
    st.markdown("### 🎨 Theme Presets")
    presets = {
        "Midnight Rail": dict(bg="#0b1116", track="#CDAA6E", card="#121a21", text="#e6f1f5", train="#57c7ff", cta="#a7f37f"),
        "Sunset Ember": dict(bg="#1a0f10", track="#E6A15E", card="#2a1517", text="#ffe9de", train="#ff8b6e", cta="#ffd166"),
        "Neon Mint":    dict(bg="#0c1112", track="#3bd1a6", card="#131b1f", text="#cff7e9", train="#7fffd4", cta="#afff8b"),
        "Royal Slate":  dict(bg="#0f1420", track="#b0b6ff", card="#151a28", text="#e8ecff", train="#8fa2ff", cta="#b0ffea"),
    }
    chosen = st.selectbox("Pick a palette", list(presets.keys()), index=0)
    if st.button("Apply palette", use_container_width=True):
        p = presets[chosen]
        cfg = st.session_state.cfg
        cfg.theme_color_bg = p["bg"]
        cfg.theme_color_track = p["track"]
        cfg.theme_color_card = p["card"]
        cfg.theme_color_card_text = p["text"]
        cfg.theme_color_train = p["train"]
        cfg.theme_color_cta = p["cta"]
        st.success(f"Applied {chosen}")

    st.markdown("---")
    azure_ok = have_azure()
    st.markdown(f"**Azure GPT-5:** {'✅ Ready' if azure_ok else '❌ Not configured'}")
    st.caption("Add `[azure]` in `.streamlit/secrets.toml` to enable guided JSON generation.")

# -----------------------------
# Layout
# -----------------------------
col_left, col_right = st.columns([0.56, 0.44])

# Chat / JSON Designer
with col_left:
    st.markdown("#### ✍️ Designer Copilot", help="Chat and get a strict JSON design your game uses instantly.")

    # Chat history
    for msg in st.session_state.chat_msgs:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])

    user_text = st.chat_input("Describe theme, mood, sequences (Stage1 & Stage2), timing, colors, CTA…")
    if user_text:
        st.session_state.chat_msgs.append({"role":"user","content": user_text})
        with st.chat_message("user"):
            st.write(user_text)

        if azure_ok:
            try:
                raw = azure_chat(user_text, system=SYSTEM_JSON, temperature=0.6, max_tokens=800)
                parsed = try_parse_json(raw)
                if not parsed:
                    reply = "I tried to generate JSON but it wasn't valid. Here's the raw output:\n\n" + raw
                    st.session_state.chat_msgs.append({"role":"assistant","content": reply})
                    with st.chat_message("assistant"):
                        st.error("Model returned invalid JSON.")
                        st.code(raw)
                else:
                    st.session_state.design_json = parsed
                    st.session_state.cfg = apply_design_to_config(parsed, st.session_state.cfg)
                    pretty = json.dumps(parsed, indent=2)
                    st.session_state.chat_msgs.append({"role":"assistant","content": "Applied this design:\n```json\n"+pretty+"\n```"})
                    with st.chat_message("assistant"):
                        st.success("Applied design JSON to game config.")
                        st.code(pretty, language="json")
            except Exception as e:
                st.session_state.chat_msgs.append({"role":"assistant","content": f"Azure error: {e}"})
                with st.chat_message("assistant"):
                    st.error(f"Azure error: {e}")
        else:
            # Local fallback: use schema example
            st.session_state.design_json = SCHEMA_EXAMPLE
            st.session_state.cfg = apply_design_to_config(SCHEMA_EXAMPLE, st.session_state.cfg)
            with st.chat_message("assistant"):
                st.info("Azure not configured; using built-in example JSON.")
                st.code(json.dumps(SCHEMA_EXAMPLE, indent=2), language="json")

    st.markdown("#### 🧩 Design Inspector", help="Tweak important bits quickly.")
    cfg = st.session_state.cfg
    c1, c2, c3 = st.columns(3)
    with c1:
        cfg.title = st.text_input("Title", cfg.title)
        cfg.stage1_sequence = st.text_input("Stage 1 (comma)", cfg.stage1_sequence)
    with c2:
        cfg.stage2_sequence = st.text_input("Stage 2 (comma)", cfg.stage2_sequence)
        cfg.tutorial_delay_ms = st.number_input("Tutorial delay (ms)", 0, 8000, cfg.tutorial_delay_ms, 100)
    with c3:
        cfg.hint_text = st.text_input("Hint text", cfg.hint_text)
        cfg.cta_text = st.text_input("CTA text", cfg.cta_text)

    st.markdown(
        "<div class='card'>"
        "<b>Colors</b><hr class='soft'/>",
        unsafe_allow_html=True
    )
    c4, c5, c6 = st.columns(3)
    with c4:
        cfg.theme_color_bg = st.color_picker("Background", cfg.theme_color_bg)
        cfg.theme_color_card = st.color_picker("Card", cfg.theme_color_card)
    with c5:
        cfg.theme_color_track = st.color_picker("Track", cfg.theme_color_track)
        cfg.theme_color_train = st.color_picker("Train", cfg.theme_color_train)
    with c6:
        cfg.theme_color_card_text = st.color_picker("Text", cfg.theme_color_card_text)
        cfg.theme_color_cta = st.color_picker("CTA", cfg.theme_color_cta)
    st.markdown("</div>", unsafe_allow_html=True)

# Live Preview & Exports
with col_right:
    st.markdown("#### 📱 Live Preview")
    st.markdown("<div class='phone-frame'><div class='phone-notch'></div>", unsafe_allow_html=True)
    preview_html = build_phaser_html(st.session_state.cfg)
    components.html(preview_html, height=min(max(st.session_state.cfg.canvas_h * 0.52, 520), 900), scrolling=False)
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown("#### ⬇️ Export")
    html_bytes = preview_html.encode("utf-8")
    json_bytes = json.dumps(asdict(st.session_state.cfg), indent=2).encode("utf-8")
    d1, d2 = st.columns(2)
    with d1:
        st.download_button("Download index.html", data=html_bytes, file_name="index.html", mime="text/html", type="primary")
    with d2:
        st.download_button("Download design.json", data=json_bytes, file_name="design.json", mime="application/json")
