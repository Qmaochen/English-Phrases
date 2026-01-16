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

# --- 設定區 ---
DATA_FILENAME = 'phrases.xlsx'
MISTAKE_FILENAME = 'mistakes.json'
TEMP_AUDIO_FILE = "temp_voice.mp3" # 暫存檔名

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

# --- Edge-TTS 存檔模式 ---
async def _edge_tts_save(text, voice="en-US-GuyNeural"):
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
        st.error(f"語音生成失敗: {e}")
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
    st.session_state.user_audio_bytes = None # [新功能] 存使用者的錄音
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
    st.session_state.user_audio_bytes = None # [重置] 清空上一題的錄音
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
            if is_correct:
                handle_correct(item, re.sub(r'_+', item['answer'], item['sentence']))
            else:
                handle_wrong(item, target_ans, re.sub(r'_+', item['answer'], item['sentence']))
            return 

    elif mode == 'speaking':
        target_ans = re.sub(r'_+', item['answer'], item['sentence'])
    else:
        target_ans = item['answer']

    # Logic for non-choice modes
    def clean(t): return re.sub(r'[^\w\s]', '', t.lower()).strip()
    is_correct = clean(user_clean) == clean(target_ans)
    
    if not is_correct:
        # 1. Tense/Form Check
        if mode in ['sentence', 'listening', 'speaking']:
             phrase_base = item['phrase']
             if clean(user_clean) == clean(phrase_base) and clean(phrase_base) != clean(target_ans):
                full_s = re.sub(r'_+', item['answer'], item['sentence'])
                msg = f"""
                ⚠️ **用詞正確，但型態/時態不對喔！** <br>
                你輸入: `{user_clean}` (原形)<br>
                正確應為: **{target_ans}**
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
                msg = f"⚠️ **意思正確！** (你答 `{user_clean}`) 但這題指定答案是 **{target_ans}**"
                st.session_state.feedback = {"type": "warning", "msg": msg}
                st.session_state.audio_data = get_audio_bytes(full_s)
                return

    full_sentence_str = re.sub(r'_+', item['answer'], item['sentence'])
    
    if is_correct:
        handle_correct(item, full_sentence_str)
    else:
        handle_wrong(item, target_ans, full_sentence_str, user_clean)

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

st.title("🧠 究極英文特訓 (Edge-TTS Ver.)")

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

# --- 題目顯示區 ---
with col2:
    if mode == 'choice':
        st.subheader("請聽發音，選出正確意思：")
        if st.session_state.q_audio_data:
            st.audio(st.session_state.q_audio_data, format='audio/mpeg')
        else:
            st.warning("⚠️ 音檔生成失敗")
            
    elif mode == 'listening':
        st.subheader("請聽完整句子，填入空格：")
        if st.session_state.q_audio_data:
            st.audio(st.session_state.q_audio_data, format='audio/mpeg')
        else:
            st.warning("⚠️ 音檔生成失敗")
        clean_s = re.sub(r'_+', ' ______ ', q['sentence'])
        st.markdown(f"**{clean_s}**")
        
    elif mode == 'speaking':
        full_display = re.sub(r'_+', q['answer'], q['sentence'])
        st.subheader("請大聲唸出以下句子：")
        st.markdown(f"### 🗣️ {full_display}")
        st.info("請使用下方錄音按鈕進行作答。")
        
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
has_answered = st.session_state.feedback is not None

if mode == 'choice':
    st.write("請選擇:")
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
            audio_blob = mic_recorder(
                start_prompt="🎙️ 開始錄音", 
                stop_prompt="⏹️ 停止並送出", 
                key='my_recorder',
                format="wav"
            )
        
        with col_msg:
            if audio_blob:
                # [新功能] 1. 儲存使用者錄音供稍後回放
                st.session_state.user_audio_bytes = audio_blob['bytes']

                st.write("🔄 正在辨識...")
                audio_bytes = audio_blob['bytes']
                text_result = transcribe_audio_bytes(audio_bytes)
                
                if text_result == "Not Recognized":
                    st.warning("😓 聽不太清楚")
                elif text_result == "API Error":
                    st.error("⚠️ 語音服務連線錯誤")
                else:
                    st.success(f"👂 系統聽到： **{text_result}**")
                    check_answer(text_result)
                    st.rerun()

        st.markdown("")
        if st.button("😶 現在不方便說，跳過這題"):
            pick_new_question() 
            st.rerun()          
    else:
        st.info("🎤 錄音結束，請查看下方回饋並按下一題。")

else:
    with st.form(key='answer_form', clear_on_submit=True):
        user_input_val = st.text_input(
            "請輸入答案 (按 Enter 送出):", 
            key="user_input_form",
            disabled=has_answered 
        )
        submitted = st.form_submit_button(
            "送出答案", 
            disabled=has_answered
        )

    if submitted:
        check_answer(user_input_val)

# --- 回饋區 ---
if st.session_state.feedback:
    fb = st.session_state.feedback
    
    if fb['type'] == 'success': st.success(fb['msg'])
    elif fb['type'] == 'warning': st.warning(fb['msg'], icon="⚠️")
    else: 
        st.markdown(fb['msg'], unsafe_allow_html=True)
        st.error("加油！再試一次！")
    
    # 顯示標準發音 (Edge-TTS)
    if st.session_state.audio_data:
        st.write("🔊 標準發音 (Edge-TTS)：")
        st.audio(st.session_state.audio_data, format='audio/mpeg', start_time=0)

    # [新功能] 顯示使用者剛剛的錄音 (如果有)
    if st.session_state.user_audio_bytes:
        st.write("🎤 你的錄音回放：")
        st.audio(st.session_state.user_audio_bytes, format='audio/wav')

    st.markdown("---")
    st.button("👉 下一題 (Next)", on_click=pick_new_question, type="primary", key="btn_next")
