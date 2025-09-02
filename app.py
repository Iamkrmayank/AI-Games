import json
import re
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any

import streamlit as st
import requests
import streamlit.components.v1 as components

# =========================
# Optional Azure OpenAI (Chat)
# =========================
def have_azure() -> bool:
    try:
        s = st.secrets["azure"]
        return bool(s.get("AZURE_API_KEY") and s.get("AZURE_ENDPOINT") and s.get("AZURE_DEPLOYMENT"))
    except Exception:
        return False

def azure_chat(prompt: str, system: str, temperature: float = 0.6, max_tokens: int = 800) -> str:
    """
    Calls Azure OpenAI Chat Completions.
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
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]

# =========================
# Game Config + HTML builder
# =========================
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
    theme_color_bg: str = "#102026"
    theme_color_track: str = "#CDAA6E"
    theme_color_card: str = "#1e2a30"
    theme_color_card_text: str = "#e6f1f5"
    theme_color_train: str = "#6EC1E4"
    theme_color_cta: str = "#ffd166"
    hint_text: str = "Tap the highlighted card to lay tracks!"
    cta_text: str = "PLAY FULL GAME"
    cta_url: str = "https://play.google.com/store/apps/details?id=com.brightpointstudios.apps.castle_royal"

def _seq_to_list(seq: str):
    return [c.strip() for c in seq.split(",") if c.strip()]

def build_phaser_html(cfg: GameConfig) -> str:
    s1 = _seq_to_list(cfg.stage1_sequence)
    s2 = _seq_to_list(cfg.stage2_sequence)
    # Phaser app is all inline, asset-free; Phaser from CDN.
    html = f"""<!DOCTYPE html>
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
      let gameComplete = false;
      let flow = 1; // 1->stage1, 2->stage2
      let idx1 = 0, idx2 = 0;
      let cards = [];
      let tracks = [];

      const config = {{
        type: Phaser.AUTO,
        width: W, height: H,
        backgroundColor: "{cfg.theme_color_bg}",
        parent: "wrap",
        scale: {{ mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH }},
        scene: {{ preload, create, update }}
      }};
      new Phaser.Game(config);

      function drawCard(scene, x, y, key, angle=0) {{
        const cw = 94, ch = 134;
        const rect = scene.add.rectangle(x, y, cw, ch, CARD_COLOR).setStrokeStyle(2, 0xffffff, 0.2);
        rect.setAngle(angle);
        rect.setInteractive({{ cursor: 'pointer' }});
        const label = scene.add.text(x, y, key, {{ fontFamily: 'monospace', fontSize: '32px', color: CARD_TEXT }}).setOrigin(0.5);
        label.setAngle(angle);
        rect.on('pointerdown', () => onCardTap(scene, key, rect, label));
        return {{ key, rect, label }};
      }}

      function addTrackPiece(scene, x, y, h=120) {{
        const track = scene.add.rectangle(x, y, 14, h, TRACK_COLOR);
        track.alpha = 0;
        scene.tweens.add({{
          targets: track, alpha: 1, duration: 280, ease: 'Quad.easeIn'
        }});
        tracks.push(track);
      }}

      function wrongShake(scene, rect) {{
        const ox = rect.x;
        scene.tweens.add({{
          targets: rect,
          x: ox + 8, yoyo: true, repeat: 2, duration: 60,
          onComplete: () => rect.x = ox
        }});
      }}

      function onCardTap(scene, key, rect, label) {{
        const expect = (flow === 1) ? STAGE1[idx1] : STAGE2[idx2];
        if (key !== expect || gameComplete) {{ wrongShake(scene, rect); return; }}

        lastTappedKey = key;
        rect.disableInteractive();
        const tx = W*0.72, ty = H*0.83;
        scene.tweens.add({{
          targets: [rect, label],
          x: tx, y: ty, angle: 0,
          duration: 340, ease: 'Quad.easeInOut',
          onComplete: () => {{
            if (flow === 1 && idx1 >= 3) {{
              const ty2 = H*0.72 - (idx1-3)*130;
              addTrackPiece(scene, W*0.12, ty2, 110);
            }}
            if (flow === 2) {{
              const ty2 = H*0.72 - (idx2)*130;
              addTrackPiece(scene, W*0.12, ty2, 110);
            }}
            advance(scene);
          }}
        }});
      }}

      function advance(scene) {{
        if (flow === 1) {{
          idx1++;
          if (idx1 >= STAGE1.length) {{
            trainMove(scene, () => layoutStage2(scene));
          }}
        }} else {{
          idx2++;
          if (idx2 >= STAGE2.length) {{
            gameComplete = true;
            finalSequence(scene);
          }}
        }}
      }}

      function trainMove(scene, after) {{
        const train = scene.add.rectangle(W*0.2, H*0.9, 80, 40, TRAIN_COLOR);
        scene.tweens.add({{
          targets: train, y: H*0.12, duration: 1200, ease: 'Cubic.easeInOut',
          onComplete: () => {{ train.destroy(); after && after(); }}
        }});
      }}

      function clearCards() {{
        cards.forEach(c => {{ c.rect.destroy(); c.label.destroy(); }});
        cards = [];
      }}

      function clearTracks() {{
        tracks.forEach(t => t.destroy());
        tracks = [];
      }}

      function layoutStage1(scene) {{
        clearCards(); clearTracks();
        flow = 1; idx1 = 0; idx2 = 0; gameComplete = false;

        const left = W*0.40, right = W*0.65;
        const yTop = H*0.45, yBot = H*0.65;

        // K, Q top
        cards.push(drawCard(scene, left,  H*0.35, 'K', 0));
        cards.push(drawCard(scene, right, H*0.35, 'Q', 0));

        // 4..8 bottom cluster
        const bottom = ['4','5','6','7','8'];
        const pos = [
          [W*0.55, H*0.8], // 4 center
          [left, yTop],    // 5
          [right, yTop],   // 6
          [left, yBot],    // 7
          [right, yBot]    // 8
        ];
        bottom.forEach((k, i) => {{
          const [x, y] = pos[i];
          const ang = (i==1? -15 : (i==2? 15 : (i==3? 15 : (i==4? -15:0))));
          cards.push(drawCard(scene, x, y, k, ang));
        }});

        lastHandTime = scene.time.now;
      }}

      function layoutStage2(scene) {{
        clearCards();
        flow = 2; idx2 = 0;

        const xmid = W*0.55, y0 = H*0.5;
        cards.push(drawCard(scene, xmid,      y0,   '6', 0));
        cards.push(drawCard(scene, xmid-120,  y0,   '7', -10));
        cards.push(drawCard(scene, xmid+120,  y0,   '8', 10));
        cards.push(drawCard(scene, xmid,      y0+160, '5', 0)); // distractor

        lastHandTime = scene.time.now;
      }}

      function finalSequence(scene) {{
        const cta = scene.add.rectangle(W*0.5, H*0.75, 280, 64, CTA_COLOR).setInteractive({{cursor:'pointer'}});
        const txt = scene.add.text(W*0.5, H*0.75, CFG.cta_text, {{
          fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
          fontSize: '20px', color: '#111'
        }}).setOrigin(0.5);
        scene.tweens.add({{
          targets: cta, scaleX: 1.06, scaleY: 1.06, yoyo: true, repeat: -1, duration: 1200, ease: 'Sine.easeInOut'
        }});
        cta.on('pointerdown', () => window.open(CFG.cta_url, '_blank'));
      }}

      function getNextCardKey() {{
        if (gameComplete) return null;
        return (flow === 1) ? STAGE1[idx1] || null : STAGE2[idx2] || null;
      }}

      function showTutorialHand(scene) {{
        const targetKey = getNextCardKey();
        if (!targetKey || targetKey === lastTappedKey) return;
        const c = cards.find(cc => cc.key === targetKey);
        if (!c) return;

        const g = scene.add.graphics();
        g.fillStyle(0xffffff, 0.9);
        g.fillTriangle(c.rect.x + CFG.hand_offset_x, c.rect.y + CFG.hand_offset_y,
                       c.rect.x + CFG.hand_offset_x - 18, c.rect.y + CFG.hand_offset_y + 36,
                       c.rect.x + CFG.hand_offset_x + 18, c.rect.y + CFG.hand_offset_y + 36);
        handSprite = g;
        scene.tweens.add({{
          targets: g, alpha: 0, duration: 1200, ease: 'Sine.easeOut',
          onComplete: () => {{ if (handSprite) {{ handSprite.destroy(); handSprite = null; }} }}
        }});
      }}

      function preload(){{}}
      function create() {{
        layoutStage1(this);
        this.time.addEvent({{
          delay: 250, loop: true,
          callback: () => {{
            if (!gameComplete && (this.time.now - lastHandTime) > CFG.tutorial_delay_ms && !handSprite) {{
              showTutorialHand(this);
              lastHandTime = this.time.now;
            }}
          }}
        }});
      }}
      function update(){{}}
    </script>
  </body>
</html>"""
    return html

# =========================
# JSON Design via GPT-5
# =========================
SCHEMA_EXAMPLE = {
    "title": "Train Tracks — Card Run",
    "stage1_sequence": "K,Q,4,5,6,7,8",
    "stage2_sequence": "6,7,8",
    "tutorial_delay_ms": 3000,
    "hand_offset_x": 60,
    "hand_offset_y": 90,
    "theme_color_bg": "#102026",
    "theme_color_track": "#CDAA6E",
    "theme_color_card": "#1e2a30",
    "theme_color_card_text": "#e6f1f5",
    "theme_color_train": "#6EC1E4",
    "theme_color_cta": "#ffd166",
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
      "tutorial_delay_ms is 0..8000. Use a short, punchy title and CTA."
)

def try_parse_json(text: str) -> Optional[Dict[str, Any]]:
    fenced = re.search(r"\{.*\}", text, flags=re.S)
    if fenced:
        try:
            return json.loads(fenced.group(0))
        except Exception:
            pass
    try:
        return json.loads(text)
    except Exception:
        return None

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

# =========================
# Streamlit UI
# =========================
st.set_page_config(page_title="Chat + Phaser Mini-Game Builder", page_icon="🎮", layout="wide")
st.title("🎮 Chat + Two-Stage Phaser Game Builder")
st.caption("Describe your idea → get a JSON design (GPT-5 optional) → live preview → export standalone HTML.")

# Keep state
if "cfg" not in st.session_state:
    st.session_state.cfg = GameConfig()
if "design_json" not in st.session_state:
    st.session_state.design_json = None
if "chat" not in st.session_state:
    st.session_state.chat = []

left, right = st.columns([1, 1])

with left:
    st.subheader("Designer Chatbot")
    st.write("Describe theme, mood, languages, sequences, timing, colors, CTA, etc.")
    sys_prompt = st.text_area("System prompt (for GPT-5)", SYSTEM_JSON, height=120)
    user_prompt = st.text_area(
        "Your brief",
        "Train theme • Stage 1: K→Q→4→5→6→7→8 • Stage 2: 6→7→8 • "
        "Hindi+English hints • warm colors • energetic CTA • tutorial hand ~3s.",
        height=120
    )
    temp = st.slider("Creativity (temperature)", 0.0, 1.2, 0.6, 0.1)

    if st.button("Generate design with GPT-5", type="primary"):
        st.session_state.chat.append({"role": "user", "content": user_prompt})
        if have_azure():
            try:
                raw = azure_chat(user_prompt, system=sys_prompt, temperature=temp, max_tokens=800)
                parsed = try_parse_json(raw)
                if not parsed:
                    st.error("Model did not return valid JSON. Raw output:")
                    st.code(raw)
                else:
                    st.session_state.design_json = parsed
                    st.session_state.cfg = apply_design_to_config(parsed, st.session_state.cfg)
                    st.success("Applied design JSON to game config.")
                    st.code(json.dumps(parsed, indent=2))
                    st.session_state.chat.append({"role": "assistant", "content": json.dumps(parsed)})
            except Exception as e:
                st.error(f"Azure error: {e}")
        else:
            # Local fallback if Azure not configured
            fallback = SCHEMA_EXAMPLE
            st.session_state.design_json = fallback
            st.session_state.cfg = apply_design_to_config(fallback, st.session_state.cfg)
            st.info("Azure not configured; applied a built-in example design.")
            st.code(json.dumps(fallback, indent=2))

    st.markdown("**Recent messages**")
    for m in st.session_state.chat[-6:]:
        st.markdown(("**You:** " if m["role"] == "user" else "**Assistant:** ") + m["content"])

with right:
    st.subheader("Manual Tweaks (optional)")
    cfg = st.session_state.cfg

    cfg.title = st.text_input("Title", cfg.title)
    cfg.stage1_sequence = st.text_input("Stage 1 sequence (comma-separated)", cfg.stage1_sequence)
    cfg.stage2_sequence = st.text_input("Stage 2 sequence (comma-separated)", cfg.stage2_sequence)
    cfg.tutorial_delay_ms = st.number_input("Tutorial hand delay (ms)", 0, 8000, cfg.tutorial_delay_ms, 100)
    st.markdown("**Tutorial hand offsets**")
    colh1, colh2 = st.columns(2)
    with colh1:
        cfg.hand_offset_x = st.number_input("Hand offset X", -200, 200, cfg.hand_offset_x, 2)
    with colh2:
        cfg.hand_offset_y = st.number_input("Hand offset Y", -200, 200, cfg.hand_offset_y, 2)

    st.markdown("**Theme colors**")
    cfg.theme_color_bg = st.color_picker("Background", cfg.theme_color_bg)
    cfg.theme_color_track = st.color_picker("Track", cfg.theme_color_track)
    cfg.theme_color_card = st.color_picker("Card fill", cfg.theme_color_card)
    cfg.theme_color_card_text = st.color_picker("Card text", cfg.theme_color_card_text)
    cfg.theme_color_train = st.color_picker("Train", cfg.theme_color_train)
    cfg.theme_color_cta = st.color_picker("CTA", cfg.theme_color_cta)

    st.markdown("**Copy & CTA**")
    cfg.hint_text = st.text_input("Hint text", cfg.hint_text)
    cfg.cta_text = st.text_input("CTA text", cfg.cta_text)
    cfg.cta_url = st.text_input("CTA URL", cfg.cta_url)

st.markdown("---")
st.subheader("Live Preview")

preview_html = build_phaser_html(st.session_state.cfg)
components.html(preview_html, height=min(max(st.session_state.cfg.canvas_h + 60, 720), 1400), scrolling=False)

st.markdown("### Export")

html_bytes = preview_html.encode("utf-8")
json_bytes = json.dumps(asdict(st.session_state.cfg), indent=2).encode("utf-8")

colx, coly = st.columns([1, 1])
with colx:
    st.download_button(
        "Download index.html",
        data=html_bytes,
        file_name="index.html",
        mime="text/html",
        type="primary",
    )
with coly:
    st.download_button(
        "Download design.json",
        data=json_bytes,
        file_name="design.json",
        mime="application/json",
    )
