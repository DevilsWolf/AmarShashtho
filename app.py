import os
import json
import uuid
import re
from datetime import datetime, timedelta
import base64
from urllib.parse import urljoin

from flask import (Flask, render_template, request, redirect, url_for, flash,
                   session, g, jsonify, abort)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, String,
                        Text, DateTime, Float, Boolean, ForeignKey, event, or_)
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.engine import Engine
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import fitz
import requests
from dotenv import load_dotenv

load_dotenv()

# --- App Configuration & Database Setup (unchanged) ---
app = Flask(__name__)
app.config.from_mapping(SECRET_KEY=os.getenv('SECRET_KEY'), UPLOAD_FOLDER=os.getenv('UPLOAD_FOLDER'), MAX_CONTENT_LENGTH=int(os.getenv('MAX_CONTENT_LENGTH', 16)) * 1024 * 1024, SQLALCHEMY_DATABASE_URI=os.getenv('DATABASE_URL'), ADMIN_SIGNUP_SECRET=os.getenv('ADMIN_SIGNUP_SECRET'), LMSTUDIO_HOST=os.getenv('LMSTUDIO_HOST'), LMSTUDIO_API_KEY=os.getenv('LMSTUDIO_API_KEY'))
Base = declarative_base(); engine = create_engine(app.config['SQLALCHEMY_DATABASE_URI']); Session = sessionmaker(bind=engine); db_session = Session()
@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record): cursor = dbapi_connection.cursor(); cursor.execute("PRAGMA foreign_keys=ON"); cursor.close()
def load_synonyms(path='specialty_synonyms.json'):
    try:
        with open(path, 'r', encoding='utf-8') as f: synonyms_data = json.load(f)
        reverse_map = {}
        for canonical, synonym_list in synonyms_data.items():
            for synonym in synonym_list: reverse_map[synonym.strip().title()] = canonical
        return synonyms_data, reverse_map
    except FileNotFoundError: return {}, {}
SYNONYMS, REVERSE_SYNONYMS = load_synonyms()
VALID_SPECIALTIES = list(SYNONYMS.keys())

# --- Models (unchanged) ---
class User(Base, UserMixin):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True); username = Column(String(80), unique=True, nullable=False); email = Column(String(120), unique=True, nullable=True); password_hash = Column(String(128), nullable=False); role = Column(String(10), nullable=False, default='user'); is_pro = Column(Boolean, nullable=False, default=False); upload_quota = Column(Integer, nullable=False, default=10); quota_reset_at = Column(DateTime); created_at = Column(DateTime, default=datetime.utcnow)
class Doctor(Base):
    __tablename__ = 'doctors'
    id = Column(Integer, primary_key=True); name = Column(String, nullable=False); primary_specialty = Column(String, nullable=False); specialties = Column(Text); location_text = Column(String); clinic_address = Column(Text); profile_image = Column(String); notes = Column(Text)
class Query(Base):
    __tablename__ = 'queries'
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey('users.id'), nullable=False); input_type = Column(String); file_path = Column(String); user_text = Column(Text); medgemma_response = Column(Text); matched_doctor_ids = Column(Text); created_at = Column(DateTime, default=datetime.utcnow)
class Payment(Base):
    __tablename__ = 'payments'
    id = Column(Integer, primary_key=True); user_id = Column(Integer, ForeignKey('users.id'), nullable=False); amount = Column(Integer, default=0); status = Column(String, default='success (mock)'); txn_id = Column(String, unique=True, default=lambda: str(uuid.uuid4())); created_at = Column(DateTime, default=datetime.utcnow)
Base.metadata.create_all(engine)

# --- Flask-Login & Helpers (unchanged) ---
login_manager = LoginManager(); login_manager.init_app(app); login_manager.login_view = 'login'
@login_manager.user_loader
def load_user(user_id): return db_session.query(User).get(int(user_id))
def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in {'png', 'jpg', 'jpeg', 'pdf'}
@app.before_request
def before_request():
    g.user = current_user
    if g.user.is_authenticated and not g.user.is_pro:
        if not g.user.quota_reset_at or datetime.utcnow() > g.user.quota_reset_at:
            g.user.upload_quota = 10; g.user.quota_reset_at = datetime.utcnow() + timedelta(days=30); db_session.commit(); flash('Your monthly upload quota has been reset!', 'success')
def decrement_quota(user):
    if not user.is_pro and user.upload_quota > 0: user.upload_quota -= 1; db_session.commit()

# --- AI & Doctor Matching (unchanged) ---
def get_medgemma_response(text=None, file_path=None, mode='standard', history=None):
    api_url = urljoin(app.config['LMSTUDIO_HOST'], "v1/chat/completions")
    headers = {"Authorization": f"Bearer {app.config['LMSTUDIO_API_KEY']}"}
    if mode == 'standard':
        system_prompt = f'You are a professional medical AI assistant. Your response MUST be ONLY a single, valid JSON object with keys "SUMMARY", "FINDINGS", "SUGGESTED_SPECIALTIES", "CONFIDENCE", and "NEXT_STEPS". The value for "FINDINGS" must be a single string with each finding separated by a newline (\\n). The value for "SUGGESTED_SPECIALTIES" MUST be a single string of one or more specialties separated by a comma, chosen ONLY from this exact list: {json.dumps(VALID_SPECIALTIES)}. All text values must be in clear, beginner-friendly English.'
        messages = [{"role": "system", "content": system_prompt}]
        content_parts = [{"type": "text", "text": text or "Analyze this medical document."}]
        if file_path:
            try:
                ext = file_path.rsplit('.', 1)[1].lower()
                if ext == 'pdf': doc = fitz.open(file_path); page = doc.load_page(0); pix = page.get_pixmap(dpi=150); img_bytes = pix.tobytes("png"); base64_image = base64.b64encode(img_bytes).decode('utf-8'); doc.close()
                else:
                    with open(file_path, "rb") as f: base64_image = base64.b64encode(f.read()).decode('utf-8')
                content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}})
            except Exception as e: return json.dumps({"error": f"Failed to process file: {e}"})
        messages.append({"role": "user", "content": content_parts})
    else:
        system_prompt = "You are a compassionate AI assistant. Respond directly to the user's last message in a supportive, conversational tone. Do not output JSON."
        messages = [{"role": "system", "content": system_prompt}] + history
    payload = {"model": "medgemma-4b-it", "messages": messages, "temperature": 0.7, "max_tokens": 1500, "stream": False}
    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=180); response.raise_for_status()
        ai_content_str = response.json()['choices'][0]['message']['content']
        if mode == 'standard':
            json_start = ai_content_str.find('{'); json_end = ai_content_str.rfind('}') + 1
            return ai_content_str[json_start:json_end] if json_start != -1 else json.dumps({"error": "No JSON found"})
        else:
            return ai_content_str
    except Exception as e:
        if mode == 'therapeutic': return "I'm sorry, I'm having a connection issue."
        else: return json.dumps({"error": f"AI server connection failed: {e}"})
def find_matching_doctors(specialties_list):
    if not specialties_list: return []
    search_terms = {REVERSE_SYNONYMS.get(s.strip().title(), s.strip().title()) for s in specialties_list}
    return db_session.query(Doctor).filter(Doctor.primary_specialty.in_(search_terms)).limit(6).all()

# --- Routes (unchanged up to symptom_checker) ---
@app.route('/')
def index(): return render_template('index.html')
@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']; password = request.form['password']
        if db_session.query(User).filter_by(username=username).first(): flash('Username already exists.', 'danger'); return redirect(url_for('signup'))
        role = 'admin' if request.form.get('admin_secret') == app.config['ADMIN_SIGNUP_SECRET'] else 'user'
        new_user = User(username=username, password_hash=generate_password_hash(password), role=role, quota_reset_at=datetime.utcnow() + timedelta(days=30))
        db_session.add(new_user); db_session.commit(); flash(f'Account created for {username}!', 'success'); return redirect(url_for('login'))
    return render_template('signup.html')
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user = db_session.query(User).filter_by(username=request.form['username']).first()
        if user and check_password_hash(user.password_hash, request.form['password']):
            login_user(user, remember=True)
            return redirect(url_for('admin_dashboard') if user.role == 'admin' else url_for('dashboard'))
        else: flash('Login unsuccessful.', 'danger')
    return render_template('login.html')
@app.route('/logout')
@login_required
def logout(): logout_user(); return redirect(url_for('index'))
@app.route('/dashboard')
@login_required
def dashboard():
    if current_user.role == 'admin': return redirect(url_for('admin_dashboard'))
    history = db_session.query(Query).filter_by(user_id=current_user.id).order_by(Query.created_at.desc()).limit(5).all()
    return render_template('dashboard.html', history=history)
@app.route('/admin/dashboard')
@login_required
def admin_dashboard():
    if current_user.role != 'admin': flash('Access denied.', 'danger'); return redirect(url_for('dashboard'))
    return render_template('admin_dashboard.html', users=db_session.query(User).all(), doctors=db_session.query(Doctor).all())
@app.route('/ai/query', methods=['GET', 'POST'])
@login_required
def ai_query():
    if request.method == 'POST':
        text, file = request.form.get('query_text', ''), request.files.get('file')
        fpath, itype = None, 'text'
        if file and file.filename:
            if not allowed_file(file.filename): flash('Invalid file type.', 'danger'); return redirect(request.url)
            if not current_user.is_pro and current_user.upload_quota <= 0: flash('Upload quota exceeded.', 'warning'); return redirect(url_for('upgrade'))
            fname = secure_filename(f"{uuid.uuid4()}_{file.filename}"); fpath = os.path.join(app.config['UPLOAD_FOLDER'], fname); file.save(fpath); decrement_quota(current_user)
            itype = 'pdf' if fname.endswith('.pdf') else 'image'
        elif not text: flash('Please provide text or a file for analysis.', 'danger'); return redirect(request.url)
        response_str = get_medgemma_response(text=text, file_path=fpath, mode='standard')
        try:
            if json.loads(response_str).get('error'): flash(f"AI Error: {json.loads(response_str)['error']}", 'danger'); return redirect(url_for('ai_query'))
        except (json.JSONDecodeError, TypeError): flash("Invalid AI response.", "danger"); return redirect(url_for('ai_query'))
        session['last_query_result'] = response_str; new_query = Query(user_id=current_user.id, input_type=itype, file_path=fpath, user_text=text, medgemma_response=response_str)
        db_session.add(new_query); db_session.commit(); return redirect(url_for('query_result'))
    return render_template('ai_query.html')
@app.route('/query/result')
@login_required
def query_result():
    result_json = session.pop('last_query_result', None);
    if not result_json: return redirect(url_for('dashboard'))
    result_data = json.loads(result_json)
    if isinstance(result_data.get('FINDINGS'), str): result_data['FINDINGS'] = [l.strip().lstrip('*-• ') for l in result_data['FINDINGS'].split('\n') if l.strip()]
    if isinstance(result_data.get('SUGGESTED_SPECIALTIES'), str): result_data['SUGGESTED_SPECIALTIES'] = [s.strip() for s in result_data['SUGGESTED_SPECIALTIES'].split(',') if s.strip()]
    doctors = find_matching_doctors(result_data.get('SUGGESTED_SPECIALTIES', []))
    last_query = db_session.query(Query).filter_by(user_id=current_user.id).order_by(Query.created_at.desc()).first()
    if last_query: last_query.matched_doctor_ids = json.dumps([d.id for d in doctors]); db_session.commit()
    return render_template('query_result.html', result=result_data, doctors=doctors)
@app.route('/query/history/<int:query_id>')
@login_required
def query_history_detail(query_id):
    query = db_session.query(Query).filter_by(id=query_id, user_id=current_user.id).first_or_404()
    result_data = json.loads(query.medgemma_response)
    if isinstance(result_data.get('FINDINGS'), str): result_data['FINDINGS'] = [l.strip().lstrip('*-• ') for l in result_data['FINDINGS'].split('\n') if l.strip()]
    if isinstance(result_data.get('SUGGESTED_SPECIALTIES'), str): result_data['SUGGESTED_SPECIALTIES'] = [s.strip() for s in result_data['SUGGESTED_SPECIALTIES'].split(',') if s.strip()]
    doctors = []
    if query.matched_doctor_ids:
        doctor_ids = json.loads(query.matched_doctor_ids)
        if doctor_ids: doctors = db_session.query(Doctor).filter(Doctor.id.in_(doctor_ids)).all()
    return render_template('history_detail.html', result=result_data, doctors=doctors, query=query)
@app.route('/therapeutic_chat', methods=['GET', 'POST'])
@login_required
def therapeutic_chat():
    if 'chat_history' not in session: session['chat_history'] = [{"role": "assistant", "content": "Hello! I'm here to listen. How are you feeling today?"}]
    if request.method == 'POST':
        user_msg = request.form.get('message')
        if user_msg:
            session['chat_history'].append({"role": "user", "content": user_msg})
            history_for_api = session['chat_history'][1:]
            ai_response_text = get_medgemma_response(mode='therapeutic', history=history_for_api)
            ai_message = ai_response_text.strip()
            session['chat_history'].append({"role": "assistant", "content": ai_message}); session.modified = True
        return redirect(url_for('therapeutic_chat'))
    return render_template('therapeutic_chat.html', chat_history=session['chat_history'])
@app.route('/clear_chat')
@login_required
def clear_chat(): session.pop('chat_history', None); return redirect(url_for('therapeutic_chat'))
@app.route('/upgrade', methods=['GET', 'POST'])
@login_required
def upgrade():
    if request.method == 'POST':
        current_user.is_pro = True; current_user.upload_quota = -1; db_session.add(Payment(user_id=current_user.id)); db_session.commit()
        flash('Congratulations! You have been upgraded to AmarShashtho Pro.', 'success'); return redirect(url_for('dashboard'))
    return render_template('upgrade.html')
@app.route('/doctors', methods=['GET'])
@login_required
def doctor_search():
    search_name = request.args.get('name', '').strip(); search_specialty = request.args.get('specialty', '').strip(); search_location = request.args.get('location', '').strip()
    query = db_session.query(Doctor)
    if search_name: query = query.filter(Doctor.name.ilike(f'%{search_name}%'))
    if search_specialty: query = query.filter(Doctor.primary_specialty == search_specialty)
    if search_location: query = query.filter(Doctor.location_text.ilike(f'%{search_location}%'))
    doctors = query.order_by(Doctor.name).all()
    return render_template('doctor_search.html', doctors=doctors, specialties=VALID_SPECIALTIES, search_values={'name': search_name, 'specialty': search_specialty, 'location': search_location})
@app.route('/doctor/<int:doctor_id>')
@login_required
def doctor_profile(doctor_id):
    doctor = db_session.query(Doctor).filter_by(id=doctor_id).first_or_404()
    return render_template('doctor_profile.html', doctor=doctor)

# --- Symptom Checker Routes ---
@app.route('/symptom_checker')
@login_required
def symptom_checker():
    session['symptom_chat_history'] = [{"role": "assistant", "content": "Welcome to the Interactive Symptom Checker. To begin, please describe your main symptom (e.g., 'I have a headache')."}]
    return render_template('symptom_checker.html', chat_history=session['symptom_chat_history'])

# --- FINAL FIX: THIS IS THE CORRECTED AND ROBUST SYMPTOM CHECKER LOGIC ---
@app.route('/symptom_checker/send', methods=['POST'])
@login_required
def symptom_checker_send():
    user_message = request.json.get('message')
    if not user_message: return jsonify({"error": "No message provided."}), 400

    if 'symptom_chat_history' not in session: session['symptom_chat_history'] = []
    session['symptom_chat_history'].append({"role": "user", "content": user_message})
    
    user_message_count = sum(1 for msg in session['symptom_chat_history'] if msg['role'] == 'user')
    is_final_turn = user_message_count >= 3

    history_for_api = session['symptom_chat_history'][1:]

    if is_final_turn:
        system_prompt = f'You are a symptom analysis AI. Based on the conversation, provide a final analysis. Your response MUST be ONLY a single, valid JSON object with keys "POSSIBLE_CAUSES", "SUGGESTED_SPECIALTIES", and "NEXT_STEPS". The value for "SUGGESTED_SPECIALTIES" MUST be a single string of specialties separated by a comma, chosen ONLY from this list: {json.dumps(VALID_SPECIALTIES)}. All text values must be in clear, beginner-friendly English.'
    else:
        system_prompt = 'You are a symptom checker AI. Ask only ONE clarifying question. Your response MUST be ONLY a single, valid JSON object with two keys: "question" (your follow-up question) and "is_final" (which must be the boolean value false).'

    api_url = urljoin(app.config['LMSTUDIO_HOST'], "v1/chat/completions")
    headers = {"Authorization": f"Bearer {app.config['LMSTUDIO_API_KEY']}"}
    messages = [{"role": "system", "content": system_prompt}] + history_for_api
    payload = {"model": "medgemma-4b-it", "messages": messages, "temperature": 0.7, "max_tokens": 1500, "stream": False}

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=180); response.raise_for_status()
        ai_content_str = response.json()['choices'][0]['message']['content']
        
        json_start = ai_content_str.find('{'); json_end = ai_content_str.rfind('}') + 1
        ai_response_json_str = ai_content_str[json_start:json_end]
        response_data = json.loads(ai_response_json_str)

        if not is_final_turn:
            session['symptom_chat_history'].append({"role": "assistant", "content": response_data.get("question")})
        else:
            # Add the 'is_final' flag for the frontend to know it's the last step
            response_data['is_final'] = True
        
        session.modified = True
        return jsonify(response_data)

    except Exception as e:
        print(f"Symptom Checker Error: {e}")
        return jsonify({"error": "Sorry, a server error occurred. Please try again."}), 500

@app.route('/symptom_checker/clear')
@login_required
def symptom_checker_clear():
    session.pop('symptom_chat_history', None)
    return redirect(url_for('symptom_checker'))


if __name__ == '__main__':
    if not os.path.exists(app.config['UPLOAD_FOLDER']): os.makedirs(app.config['UPLOAD_FOLDER'])
    app.run(debug=os.getenv('FLASK_DEBUG', 'False').lower() in ['true', '1', 't'])