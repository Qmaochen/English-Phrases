import streamlit as st
import pandas as pd
import random
import os
import json
import re
import difflib
from gtts import gTTS
from io import BytesIO
import speech_recognition as sr

# --- 設定區 ---
DATA_FILENAME = 'phrases.xlsx'
MISTAKE_FILENAME = 'mistakes.json'

# --- 1. 基礎函式 ---

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

def get_audio_bytes(text):
    try:
        clean_text = text.replace("_", " ")
        tts = gTTS(text=clean_text, lang='en')
        fp = BytesIO()
        tts.write_to_fp(fp)
        return fp
    except: return None

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

def transcribe_audio(audio_bytes):
    r = sr.Recognizer()
    try:
        with sr.AudioFile(audio_bytes) as source:
            audio_data = r.record(source)
            text = r.recognize_google(audio_data, language='en-US')
            return text
    except sr.UnknownValueError:
        return "Not Recognized"
    except sr.RequestError:
        return "API Error"
    except Exception as e:
        return str(e)

# --- 2. 狀態初始化 ---

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

# --- 3. 核心邏輯 ---

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
    
    if mode == 'listening' or mode == 'speaking':
        st.session_state.q_audio_data = get_audio_bytes(full_s)
    elif mode == 'choice':
        st.session_state.q_audio_data = get_audio_bytes(target_item['phrase'])
        correct = target_item['meaning']
        distractors = random.sample([m for m in st.session_state.all_meanings if m != correct], 3)
        opts = distractors + [correct]
        random.shuffle(opts)
        st.session_state.options = opts

def submit_answer():
    user_input = st.session_state.user_answer_key
    check_answer(user_input)
    st.session_state.user_answer_key = ""

def check_answer(user_input):
    item = st.session_state.current_q
    mode = st.session_state.mode
    
    if not item: return

    if mode == 'phrase' or mode == 'choice':
        target_ans = item['phrase'] 
        if mode == 'choice': 
            is_correct = (user_input == item['meaning'])
            target_ans = item['meaning']
    elif mode == 'speaking':
        target_ans = re.sub(r'_+', item['answer'], item['sentence'])
    else:
        target_ans = item['answer']

    if mode != 'choice':
        def clean(t): return re.sub(r'[^\w\s]', '', t.lower())
        is_correct = clean(user_input) == clean(target_ans)
        
        if not is_correct and mode != 'speaking':
            syn_map = st.session_state.synonym_map
            current_meaning = item['meaning']
            if current_meaning in syn_map and user_input.lower().strip() in syn_map[current_meaning]:
                full_s = re.sub(r'_+', item['answer'], item['sentence'])
                msg = f"⚠️ **意思正確！** (你答 `{user_input}`) 但這題指定答案是 **{target_ans}**"
                st.session_state.feedback = {"type": "warning", "msg": msg}
                st.session_state.audio_data = get_audio_bytes(full_s)
                return

    full_sentence_str = re.sub(r'_+', item['answer'], item['sentence'])
    
    if is_correct:
        handle_correct(item, full_sentence_str)
    else:
        handle_wrong(item, target_ans, full_sentence_str, user_input if mode != 'choice' else "")

def handle_correct(item, full_s):
    msg = "✅ Correct! 答對了！"
    if item['phrase'] in st.session_state.mistakes:
        st.session_state.mistakes.remove(item['phrase'])
        save_mistakes(st.session_state.mistakes)
        msg += " (已移除錯題 🎉)"
    
    st.session_state.feedback = {"type": "success", "msg": msg}
    st.session_state.audio_data = get_audio_bytes(full_s)

def handle_wrong(item, target_text, full_s, user_input=""):
    diff_html = ""
    if user_input:
        diff_html = generate_diff(user_input, target_text)
        diff_display = f"<br>差異比對: {diff_html}"
    else:
        diff_display = ""

    msg = f"❌ 答錯了！<br>正確答案: **{target_text}**{diff_display}<br>完整例句: *{full_s}*"
    
    if item['phrase'] not in st.session_state.mistakes:
        st.session_state.mistakes.append(item['phrase'])
        save_mistakes(st.session_state.mistakes)
    
    st.session_state.feedback = {"type": "error", "msg": msg}
    st.session_state.audio_data = get_audio_bytes(full_s)

def toggle_hint():
    st.session_state.show_hint = True

# --- 4. 介面佈局 ---

st.set_page_config(page_title="究極英文特訓", page_icon="🧠")

with st.sidebar:
    st.header("📊 學習控制台")
    st.metric("💀 錯題本", f"{len(st.session_state.mistakes)} 題")
    with st.expander("🗑️ 管理錯題"):
        if st.session_state.mistakes:
            to_remove = st.multiselect("移除已學會:", st.session_state.mistakes)
            if st.button("確認刪除"):
                for w in to_remove:
                    if w in st.session_state.mistakes: st.session_state.mistakes.remove(w)
                save_mistakes(st.session_state.mistakes)
                st.rerun()
        else: st.write("錯題本是空的！")
    st.divider()
    if st.button("🔄 重新載入"):
        st.cache_data.clear()
        st.session_state.initialized = False
        st.rerun()

st.title("🧠 究極英文特訓")

if st.session_state.current_q is None:
    pick_new_question()

q = st.session_state.current_q
mode = st.session_state.mode

if st.session_state.is_review: st.warning("💀 錯題複習中...")

col1, col2 = st.columns([1, 4])
with col1:
    if mode == 'phrase': st.info("📝 考片語")
    elif mode == 'sentence': st.success("🗣️ 考例句")
    elif mode == 'listening': st.warning("👂 聽寫")
    elif mode == 'choice': st.error("⚡ 聽音選義")
    elif mode == 'speaking': st.error("🎙️ 口說特訓")

with col2:
    if mode == 'choice':
        st.subheader("請聽發音，選出正確意思：")
        st.audio(st.session_state.q_audio_data, format='audio/mp3')
    elif mode == 'listening':
        st.subheader("請聽完整句子，填入空格：")
        st.audio(st.session_state.q_audio_data, format='audio/mp3')
        clean_s = re.sub(r'_+', ' ______ ', q['sentence'])
        st.markdown(f"**{clean_s}**")
    elif mode == 'speaking':
        full_display = re.sub(r'_+', q['answer'], q['sentence'])
        st.subheader("請大聲唸出以下句子：")
        st.markdown(f"### 🗣️ {full_display}")
        st.info("點擊下方錄音按鈕，唸完後系統會自動辨識。")
    else:
        st.subheader(f"中文: {q['meaning']}")
        if mode == 'sentence':
            clean_s = re.sub(r'_+', ' ______ ', q['sentence'])
            st.markdown(f"#### {clean_s}")

# --- 提示區 ---
if mode not in ['choice', 'speaking'] and not st.session_state.feedback:
    target = q['phrase'] if mode == 'phrase' else q['answer']
    hint_text = f"首字母: **{target[0]}...** (總長度: {len(target)})"
    if st.session_state.show_hint: st.info(f"💡 提示: {hint_text}")
    else: st.button("💡 給我一點提示 (Scaffolding)", on_click=toggle_hint)

st.divider()

# --- 作答區 ---
# [重要修改] 檢查是否已作答，用來鎖定介面
has_answered = st.session_state.feedback is not None

if mode == 'choice':
    st.write("請選擇:")
    cols = st.columns(2)
    for i, opt in enumerate(st.session_state.options):
        # 答題後鎖定按鈕 (disabled=True)
        cols[i%2].button(
            opt, 
            use_container_width=True, 
            on_click=check_answer, 
            args=(opt,),
            disabled=has_answered 
        )

elif mode == 'speaking':
    # 答題後隱藏錄音按鈕，顯示訊息
    if not has_answered:
        audio_val = st.audio_input("🔴 按下紅色按鈕開始錄音")
        if audio_val:
            st.write("🔄 正在辨識您的發音...")
            text_result = transcribe_audio(audio_val)
            if text_result == "Not Recognized":
                st.warning("😓 聽不太清楚，請再試一次！")
            elif text_result == "API Error":
                st.error("⚠️ 語音服務連線錯誤")
            else:
                st.success(f"👂 系統聽到： **{text_result}**")
                check_answer(text_result)
                st.rerun()
    else:
        st.info("🎤 錄音結束，請查看下方回饋並按下一題。")

else:
    # 文字輸入模式：答題後鎖定輸入框與按鈕
    st.text_input(
        "請輸入答案 (按 Enter 送出):", 
        key="user_answer_key", 
        on_change=submit_answer,
        disabled=has_answered # 關鍵：鎖定
    )
    st.button("送出答案", on_click=submit_answer, disabled=has_answered)

# --- 回饋區 ---
if st.session_state.feedback:
    fb = st.session_state.feedback
    
    if fb['type'] == 'success': st.success(fb['msg'])
    elif fb['type'] == 'warning': st.warning(fb['msg'], icon="⚠️")
    else: 
        st.markdown(fb['msg'], unsafe_allow_html=True)
        st.error("加油！再試一次！")
    
    if st.session_state.audio_data:
        st.write("🔊 聽聽看 Google 小姐的標準發音：")
        st.audio(st.session_state.audio_data, format='audio/mp3', start_time=0)

    st.markdown("---")
    st.button("👉 下一題 (Next)", on_click=pick_new_question, type="primary")
