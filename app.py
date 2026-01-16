import streamlit as st
import pandas as pd
import random
import os
import json
import re
import difflib
import asyncio
import edge_tts
from io import BytesIO
import speech_recognition as sr
from streamlit_mic_recorder import mic_recorder

# --- Configuration ---
DATA_FILENAME = 'phrases.xlsx'
MISTAKE_FILENAME = 'mistakes.json'
TEMP_AUDIO_FILE = "temp_voice.mp3" # Temporary file for iOS compatibility

# --- 1. Basic Functions ---

@st.cache_data
def load_data():
    if not os.path.exists(DATA_FILENAME): return [], {}, []
    try:
        df = pd.read_excel(DATA_FILENAME).fillna("")
        data_list = df.to_dict('records')
        valid_data = []
        synonym_map = {} 
        all_meanings = [] 

        for row in data_list:
            p = str(row.get('phrase', '')).strip()
            s = str(row.get('sentence', '')).strip()
            a = str(row.get('Answer', '')).strip()
            m = str(row.get('meaning', '')).strip()
            
            if p and s:
                if not a: a = p
                valid_data.append({"phrase": p, "meaning": m, "sentence": s, "answer": a})
                if m not in all_meanings: all_meanings.append(m)
                
                if m not in synonym_map: synonym_map[m] = []
                if p.lower() not in synonym_map[m]: synonym_map[m].append(p.lower())
                if a.lower() not in synonym_map[m]: synonym_map[m].append(a.lower())

        return valid_data, synonym_map, all_meanings
    except: return [], {}, []

def load_mistakes():
    if not os.path.exists(MISTAKE_FILENAME): return []
    try:
        with open(MISTAKE_FILENAME, 'r', encoding='utf-8') as f: return json.load(f)
    except: return []

def save_mistakes(mistake_list):
    try:
        with open(MISTAKE_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(mistake_list, f, ensure_ascii=False, indent=4)
    except: pass

# --- Edge-TTS File Saving Mode (Fix for iOS) ---
async def _edge_tts_save(text, voice="en-US-AriaNeural"):
    try:
        clean_text = text.replace("_", " ")
        communicate = edge_tts.Communicate(clean_text, voice)
        await communicate.save(TEMP_AUDIO_FILE)
        return True
    except Exception as e:
        print(f"EdgeTTS Error: {e}")
        return False

def get_audio_bytes(text):
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        success = loop.run_until_complete(_edge_tts_save(text))
        
        if success and os.path.exists(TEMP_AUDIO_FILE):
            with open(TEMP_AUDIO_FILE, "rb") as f:
                audio_bytes = f.read()
            return audio_bytes
        else:
            return None
    except Exception as e:
        st.error(f"Audio generation failed: {e}")
        return None

def generate_diff(user_text, target_text):
    s = difflib.SequenceMatcher(None, user_text.lower(), target_text.lower())
    html = []
    for opcode, a0, a1, b0, b1 in s.get_opcodes():
        if opcode == 'equal':
            html.append(f"<span style='color:green; font-weight:bold'>{target_text[b0:b1]}</span>")
        elif opcode == 'insert':
            html.append(f"<span style='color:red; text-decoration:underline; background-color:#ffe6e6'>[{target_text[b0:b1]}]</span>")
        elif opcode == 'delete':
             html.append(f"<span style='color:gray; text-decoration:line-through'>{user_text[a0:a1]}</span>")
        elif opcode == 'replace':
            html.append(f"<span style='color:gray; text-decoration:line-through'>{user_text[a0:a1]}</span>")
            html.append(f"<span style='color:red; background-color:#ffe6e6'>[{target_text[b0:b1]}]</span>")
    return "".join(html)

def transcribe_audio_bytes(audio_bytes):
    r = sr.Recognizer()
    try:
        with sr.AudioFile(BytesIO(audio_bytes)) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data, language='en-US')
            return text
    except sr.UnknownValueError:
        return "Not Recognized"
    except sr.RequestError:
        return "API Error"
    except Exception as e:
        return str(e)

# --- 2. State Initialization ---

if 'initialized' not in st.session_state:
    data, syn_map, meanings = load_data()
    st.session_state.all_phrases = data
    st.session_state.synonym_map = syn_map
    st.session_state.all_meanings = meanings
    st.session_state.mistakes = load_mistakes()
    
    st.session_state.current_q = None
    st.session_state.mode = None
    st.session_state.feedback = None
    st.session_state.audio_data = None
    st.session_state.q_audio_data = None
    st.session_state.options = [] 
    st.session_state.show_hint = False
    st.session_state.user_answer_key = "" 
    st.session_state.initialized = True

# --- 3. Core Logic ---

def pick_new_question():
    mistakes = st.session_state.mistakes
    all_phrases = st.session_state.all_phrases
    
    if not all_phrases: return

    target_item = None
    is_review = False
    
    if mistakes and random.random() < 0.7:
        review_phrase = random.choice(mistakes)
        target_item = next((item for item in all_phrases if item['phrase'] == review_phrase), None)
        if target_item: is_review = True
        else:
            mistakes.remove(review_phrase)
            save_mistakes(mistakes)
            st.session_state.mistakes = mistakes

    if not target_item:
        target_item = random.choice(all_phrases)

    mode = random.choice(['phrase', 'sentence', 'listening', 'choice', 'speaking'])
    
    st.session_state.current_q = target_item
    st.session_state.mode = mode
    st.session_state.is_review = is_review
    st.session_state.feedback = None
    st.session_state.audio_data = None
    st.session_state.q_audio_data = None
    st.session_state.show_hint = False 
    
    full_s = re.sub(r'_+', target_item['answer'], target_item['sentence'])
    
    # Generate question audio
    if mode == 'listening' or mode == 'speaking':
        st.session_state.q_audio_data = get_audio_bytes(full_s)
    elif mode == 'choice':
        st.session_state.q_audio_data = get_audio_bytes(target_item['phrase'])
        correct = target_item['meaning']
        distractors = random.sample([m for m in st.session_state.all_meanings if m != correct], 3)
        opts = distractors + [correct]
        random.shuffle(opts)
        st.session_state.options = opts

def check_answer(user_input):
    item = st.session_state.current_q
    mode = st.session_state.mode
    
    if not item: return

    # Clean input
    user_clean = user_input.strip()

    if mode == 'phrase' or mode == 'choice':
        target_ans = item['phrase'] 
        if mode == 'choice': 
            is_correct = (user_clean == item['meaning'])
            target_ans = item['meaning']
            # Choice mode is simple, return here
            if is_correct:
                handle_correct(item, re.sub(r'_+', item['answer'], item['sentence']))
            else:
                handle_wrong(item, target_ans, re.sub(r'_+', item['answer'], item['sentence']))
            return # Exit function

    elif mode == 'speaking':
        target_ans = re.sub(r'_+', item['answer'], item['sentence'])
    else:
        target_ans = item['answer']

    # Logic for non-choice modes
    def clean(t): return re.sub(r'[^\w\s]', '', t.lower()).strip()
    is_correct = clean(user_clean) == clean(target_ans)
    
    if not is_correct:
        # 1. Tense/Form Check (User typed phrase base form instead of answer)
        if mode in ['sentence', 'listening', 'speaking']:
             phrase_base = item['phrase']
             if clean(user_clean) == clean(phrase_base) and clean(phrase_base) != clean(target_ans):
                full_s = re.sub(r'_+', item['answer'], item['sentence'])
                msg = f"""
                ⚠️ **Correct word, wrong form/tense!** <br>
                You typed: `{user_clean}` (Base form)<br>
                Should be: **{target_ans}**
                """
                st.session_state.feedback = {"type": "warning", "msg": msg}
                st.session_state.audio_data = get_audio_bytes(full_s)
                return

        # 2. Synonym Check (Skip speaking)
        if mode != 'speaking':
            syn_map = st.session_state.synonym_map
            current_meaning = item['meaning']
            if current_meaning in syn_map and user_clean.lower() in syn_map[current_meaning]:
                full_s = re.sub(r'_+', item['answer'], item['sentence'])
                msg = f"⚠️ **Correct meaning!** (You typed `{user_clean}`) But the answer here is **{target_ans}**"
                st.session_state.feedback = {"type": "warning", "msg": msg}
                st.session_state.audio_data = get_audio_bytes(full_s)
                return

    full_sentence_str = re.sub(r'_+', item['answer'], item['sentence'])
    
    if is_correct:
        handle_correct(item, full_sentence_str)
    else:
        handle_wrong(item, target_ans, full_sentence_str, user_clean)

def handle_correct(item, full_s):
    msg = "✅ Correct! Great job!"
    if item['phrase'] in st.session_state.mistakes:
        st.session_state.mistakes.remove(item['phrase'])
        save_mistakes(st.session_state.mistakes)
        msg += " (Removed from mistakes 🎉)"
    
    st.session_state.feedback = {"type": "success", "msg": msg}
    st.session_state.audio_data = get_audio_bytes(full_s)

def handle_wrong(item, target_text, full_s, user_input=""):
    diff_html = ""
    if user_input:
        diff_html = generate_diff(user_input, target_text)
        diff_display = f"<br>Diff: {diff_html}"
    else:
        diff_display = ""

    msg = f"❌ Incorrect!<br>Answer: **{target_text}**{diff_display}<br>Sentence: *{full_s}*"
    
    if item['phrase'] not in st.session_state.mistakes:
        st.session_state.mistakes.append(item['phrase'])
        save_mistakes(st.session_state.mistakes)
    
    st.session_state.feedback = {"type": "error", "msg": msg}
    st.session_state.audio_data = get_audio_bytes(full_s)

def toggle_hint():
    st.session_state.show_hint = True

# --- 4. Interface Layout ---

st.set_page_config(page_title="Ultimate English Training", page_icon="🧠")

with st.sidebar:
    st.header("📊 Dashboard")
    st.metric("💀 Mistakes", f"{len(st.session_state.mistakes)} words")
    with st.expander("🗑️ Manage Mistakes"):
        if st.session_state.mistakes:
            to_remove = st.multiselect("Remove mastered:", st.session_state.mistakes)
            if st.button("Confirm Delete"):
                for w in to_remove:
                    if w in st.session_state.mistakes: st.session_state.mistakes.remove(w)
                save_mistakes(st.session_state.mistakes)
                st.rerun()
        else: st.write("No mistakes yet! Keep it up!")
    st.divider()
    if st.button("🔄 Reload DB"):
        st.cache_data.clear()
        st.session_state.initialized = False
        st.rerun()

st.title("🧠 Ultimate English Training (Edge-TTS Ver.)")

if st.session_state.current_q is None:
    pick_new_question()

q = st.session_state.current_q
mode = st.session_state.mode

if st.session_state.is_review: st.warning("💀 Reviewing Mistakes...")

col1, col2 = st.columns([1, 4])
with col1:
    if mode == 'phrase': st.info("📝 Phrase")
    elif mode == 'sentence': st.success("🗣️ Sentence")
    elif mode == 'listening': st.warning("👂 Listening")
    elif mode == 'choice': st.error("⚡ Choice")
    elif mode == 'speaking': st.error("🎙️ Speaking")

# --- Question Display Area ---
with col2:
    if mode == 'choice':
        st.subheader("Listen and choose the meaning:")
        if st.session_state.q_audio_data:
            st.audio(st.session_state.q_audio_data, format='audio/mpeg')
        else:
            st.warning("⚠️ Audio failed")
            
    elif mode == 'listening':
        st.subheader("Listen and fill in the blank:")
        if st.session_state.q_audio_data:
            st.audio(st.session_state.q_audio_data, format='audio/mpeg')
        else:
            st.warning("⚠️ Audio failed")
        clean_s = re.sub(r'_+', ' ______ ', q['sentence'])
        st.markdown(f"**{clean_s}**")
        
    elif mode == 'speaking':
        full_display = re.sub(r'_+', q['answer'], q['sentence'])
        st.subheader("Read aloud:")
        st.markdown(f"### 🗣️ {full_display}")
        st.info("Use the record button below.")
        
    else: # Phrase / Sentence Mode
        st.subheader(f"Meaning: {q['meaning']}")
        if mode == 'sentence':
            clean_s = re.sub(r'_+', ' ______ ', q['sentence'])
            st.markdown(f"#### {clean_s}")

# --- Hint Area ---
if mode not in ['choice', 'speaking'] and not st.session_state.feedback:
    target = q['phrase'] if mode == 'phrase' else q['answer']
    hint_text = f"First letter: **{target[0]}...** (Length: {len(target)})"
    if st.session_state.show_hint: st.info(f"💡 Hint: {hint_text}")
    else: st.button("💡 Hint (Scaffolding)", on_click=toggle_hint)

st.divider()

# --- Answer Area ---
has_answered = st.session_state.feedback is not None

if mode == 'choice':
    st.write("Choose one:")
    cols = st.columns(2)
    for i, opt in enumerate(st.session_state.options):
        cols[i%2].button(
            opt, 
            use_container_width=True, 
            on_click=check_answer, 
            args=(opt,),
            disabled=has_answered 
        )

elif mode == 'speaking':
    if not has_answered:
        col_rec, col_msg = st.columns([1, 3])
        with col_rec:
            # Frontend recorder
            audio_blob = mic_recorder(
                start_prompt="🎙️ Start", 
                stop_prompt="⏹️ Stop & Send", 
                key='my_recorder',
                format="wav"
            )
        
        with col_msg:
            if audio_blob:
                st.write("🔄 Recognizing...")
                audio_bytes = audio_blob['bytes']
                text_result = transcribe_audio_bytes(audio_bytes)
                
                if text_result == "Not Recognized":
                    st.warning("😓 Not recognized")
                elif text_result == "API Error":
                    st.error("⚠️ API Error")
                else:
                    st.success(f"👂 Heard: **{text_result}**")
                    check_answer(text_result)
                    st.rerun()

        # Skip button
        st.markdown("")
        if st.button("😶 Skip this question"):
            pick_new_question() 
            st.rerun()          
    else:
        st.info("🎤 Recording finished.")

else:
    # --- [Correction] Use st.form for stable submission on iPad/Mobile ---
    with st.form(key='answer_form', clear_on_submit=True):
        user_input_val = st.text_input(
            "Enter answer:", 
            key="user_input_form",
            disabled=has_answered 
        )
        submitted = st.form_submit_button(
            "Submit Answer", 
            disabled=has_answered
        )

    if submitted:
        check_answer(user_input_val)

# --- Feedback Area ---
if st.session_state.feedback:
    fb = st.session_state.feedback
    
    if fb['type'] == 'success': st.success(fb['msg'])
    elif fb['type'] == 'warning': st.warning(fb['msg'], icon="⚠️")
    else: 
        st.markdown(fb['msg'], unsafe_allow_html=True)
        st.error("Keep trying!")
    
    if st.session_state.audio_data:
        st.write("🔊 Audio (Edge-TTS):")
        # iPad supported format, no autoplay
        st.audio(st.session_state.audio_data, format='audio/mpeg', start_time=0)

    st.markdown("---")
    st.button("👉 Next Question", on_click=pick_new_question, type="primary", key="btn_next")
